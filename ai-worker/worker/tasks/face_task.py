"""Face recognition pipeline (plan §6b / Phase 9 pgvector upgrade):
decode frame -> detect+embed faces -> ANN cosine match in SQL via pgvector <=>
operator against face_watchlist_entries.embedding_v (HNSW index) -> detections/
face_events rows -> alert/incident per §7 -> evidence -> audit log.

Unlike LPR's "silent on no-match," an unrecognized face DOES get a low-severity
`info` alert (no incident): the product scope names "Unknown face detection"
as a feature in its own right — an unrecognized person on a monitored site is
itself the signal operators want, unlike an unmatched plate which usually
isn't list-worthy.

Phase 9 replaced the Phase 1 brute-force Python cosine approach (fetch all
watchlist rows, numpy compare) with a single SQL ORDER BY embedding_v <=> query.
The HNSW index makes this sub-millisecond at any watchlist scale. The Python
cosine_similarity function is kept as a utility for tests and offline tools.
"""

import base64
from uuid import uuid4

import cv2
import numpy as np
from psycopg.types.json import Jsonb

from shared.events import FrameJob
from worker.common.alert_rules_cache import resolve_rule
from worker.common.metrics import alerts_created_total
from worker.common.realtime_publisher import publish_alert_created, publish_incident_created
from worker.common.tenant_settings_cache import get_tenant_setting
from worker.db_writer import get_tenant_session
from worker.models.face_model import FACE_MODEL_VERSION, get_face_app
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "face"

# watchlist_match -> (alert severity, alert_code, create incident, incident severity)
# Stable machine keys for i18n — not tenant-configurable. Severity and
# auto-incident come from alert_rules_cache (Module 14).
ALERT_CODES = {
    "block": "face.blocklist_hit",
    "allow": "face.allowlist_hit",
}
UNRECOGNIZED_ALERT_CODE = "face.unrecognized"


def decode_frame(frame_jpeg_b64: str) -> np.ndarray:
    frame_bytes = base64.b64decode(frame_jpeg_b64)
    frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode frame JPEG")
    return frame


def detect_faces(frame: np.ndarray) -> list[dict]:
    """Returns [{"embedding": list[float], "bbox": (x1,y1,x2,y2), "det_score": float}, ...]."""
    app = get_face_app()
    faces = app.get(frame)
    results = []
    for face in faces:
        bbox = face.bbox.tolist()
        results.append({
            "embedding": face.normed_embedding.astype(float).tolist(),
            "bbox": (bbox[0], bbox[1], bbox[2], bbox[3]),
            "det_score": float(face.det_score),
        })
    return results


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    denom = (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)) + 1e-8
    return float(np.dot(a_arr, b_arr) / denom)


def _embedding_to_vec_str(embedding: list[float]) -> str:
    """Format a Python float list as a pgvector literal: [v1,v2,...,v512]."""
    return "[" + ",".join(str(v) for v in embedding) + "]"


def _find_closest_watchlist_match(
    conn, tenant_id, embedding: list[float], threshold: float
) -> tuple[dict | None, float]:
    """Single SQL ANN query using the HNSW cosine index on embedding_v.

    pgvector <=> gives cosine DISTANCE (0 = identical). We compute
    similarity = 1 - distance so the result is in the same [0,1] range as the
    Phase 1 brute-force cosine_similarity, keeping the threshold semantics
    unchanged across the upgrade.
    """
    vec_str = _embedding_to_vec_str(embedding)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, person_name, list_type,
                   1.0 - (embedding_v <=> %s::vector) AS score
            FROM face_watchlist_entries
            WHERE tenant_id = %s AND is_active = TRUE
              AND (expires_at IS NULL OR expires_at > now())
            ORDER BY embedding_v <=> %s::vector
            LIMIT 1
            """,
            (vec_str, str(tenant_id), vec_str),
        )
        row = cur.fetchone()

    if row is None:
        return None, 0.0

    match_id, person_name, list_type, score = row
    score = float(score)
    if score >= threshold:
        return {"id": match_id, "person_name": person_name, "list_type": list_type}, score
    return None, score


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    frame = decode_frame(job.frame_jpeg_b64)
    faces = detect_faces(frame)
    if not faces:
        return

    pending_events: list[tuple] = []  # (alert_id, severity, incident_id)

    with get_tenant_session(job.tenant_id) as conn:
        threshold = get_tenant_setting(conn, job.tenant_id, "face.match_threshold")

        for face in faces:
            matched_row, score = _find_closest_watchlist_match(conn, job.tenant_id, face["embedding"], threshold)
            watchlist_match = matched_row["list_type"] if matched_row else None
            match_confidence = round(score, 4) if matched_row else None

            x1, y1, x2, y2 = (int(v) for v in face["bbox"])
            detection_id = uuid4()
            vec_str = _embedding_to_vec_str(face["embedding"])
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence,
                                             bounding_box, raw_metadata, detected_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (detection_id, str(job.tenant_id), str(job.camera_id), MODULE_TYPE, face["det_score"],
                     Jsonb({"x1": x1, "y1": y1, "x2": x2, "y2": y2}),
                     Jsonb({"model_version": FACE_MODEL_VERSION, "job_id": str(job.job_id)}),
                     job.captured_at),
                )
                cur.execute(
                    """
                    INSERT INTO face_events (detection_id, detected_at, tenant_id, camera_id,
                                              embedding_v, matched_watchlist_id,
                                              match_confidence, watchlist_match)
                    VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s)
                    """,
                    (detection_id, job.captured_at, str(job.tenant_id), str(job.camera_id),
                     vec_str,
                     str(matched_row["id"]) if matched_row else None,
                     match_confidence, watchlist_match),
                )

            rel_path, checksum = save_evidence_snapshot(frame, job.tenant_id, detection_id)
            evidence_id = uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO evidence (id, tenant_id, detection_id, incident_id, media_type,
                                           storage_path, checksum_sha256, captured_at)
                    VALUES (%s, %s, %s, NULL, 'image', %s, %s, %s)
                    """,
                    (evidence_id, str(job.tenant_id), str(detection_id), rel_path, checksum, job.captured_at),
                )

            alert_id, incident_id, severity, alert_title = _apply_alert_incident_rules(
                conn, job.tenant_id, job.camera_id, detection_id, matched_row, watchlist_match
            )
            if incident_id is not None:
                with conn.cursor() as cur:
                    cur.execute("UPDATE evidence SET incident_id = %s WHERE id = %s", (str(incident_id), evidence_id))
            # alert_id is None when the tenant disabled this trigger. The
            # detection and its evidence are still written — suppressing an
            # alert is not the same as not having seen the face.
            if alert_id is not None:
                pending_events.append((alert_id, severity, incident_id, alert_title))
            alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail)
                    VALUES (%s, NULL, 'face_detection_processed', 'detection', %s, %s)
                    """,
                    (str(job.tenant_id), str(detection_id),
                     Jsonb({"watchlist_match": watchlist_match,
                            "alert_id": str(alert_id) if alert_id else None,
                            "incident_id": str(incident_id) if incident_id else None})),
                )

        conn.commit()

    for alert_id, severity, incident_id, title in pending_events:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, severity, job.camera_id, title)
        if incident_id is not None:
            publish_incident_created(job.tenant_id, incident_id, alert_id, severity)


def _apply_alert_incident_rules(conn, tenant_id, camera_id, detection_id, matched_row, watchlist_match) -> tuple[object, object, str | None, str | None]:
    """Returns (alert_id, incident_id, severity, title); alert_id is None when
    this tenant has disabled the trigger, and the caller skips it entirely."""
    if watchlist_match in ALERT_CODES:
        trigger_key = watchlist_match
        alert_code = ALERT_CODES[watchlist_match]
        person_name = matched_row["person_name"]
        title = "Blacklisted person detected" if watchlist_match == "block" else "VIP / known person detected"
        message_params = {"person_name": person_name}
    else:
        # Unrecognized face: deliberate asymmetry vs LPR — still alerts by
        # default, because an unknown person on a monitored site is the signal.
        trigger_key = "unrecognized"
        alert_code = UNRECOGNIZED_ALERT_CODE
        title = "Unrecognized face detected"
        message_params = {}

    rule = resolve_rule(conn, tenant_id, MODULE_TYPE, trigger_key)
    if rule is None:
        return None, None, None, None
    severity, create_incident, incident_severity = (
        rule.severity, rule.create_incident, rule.incident_severity,
    )

    alert_id = uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts (id, tenant_id, detection_id, camera_id, module_type, severity,
                                 alert_code, message_params, title, message, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
            """,
            (alert_id, str(tenant_id), str(detection_id), str(camera_id), MODULE_TYPE, severity,
             alert_code, Jsonb(message_params), title, title),
        )

    incident_id = None
    if create_incident:
        incident_id = uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, alert_code,
                                        message_params, severity, status, is_auto_created)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', TRUE)
                """,
                (incident_id, str(tenant_id), str(alert_id), str(camera_id),
                 "Blacklisted person on premises", alert_code, Jsonb(message_params), incident_severity),
            )

    return alert_id, incident_id, severity, title
