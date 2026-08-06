"""LPR pipeline (plan §6a): decode frame -> detect plate -> OCR -> threshold
check -> detections/lpr_events rows -> watchlist lookup -> alert/incident per
§7 -> evidence snapshot -> audit log. One DB transaction per processed plate,
committed only at the end — XACK in consumer.py only happens after this
function returns without raising.

detect_plates/run_ocr are thin wrappers around the actual model calls, kept as
separate top-level functions specifically so tests can monkeypatch them
directly instead of mocking deep into ultralytics/paddleocr internals — this
is what lets the pipeline logic below (DB writes, alert/incident rules) be
tested for real without needing a real plate-detection weight file loaded.
"""

import base64
import hashlib
import logging
import json
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

import cv2
import numpy as np
from psycopg.types.json import Jsonb

from shared.events import FrameJob
from worker.common.alert_rules_cache import resolve_rule
from worker.common.metrics import alerts_created_total
from worker.common.realtime_publisher import publish_alert_created, publish_incident_created, publish_lpr_plate_detected
from worker.common.tenant_settings_cache import get_tenant_setting
from worker.db_writer import get_tenant_session
import worker.models.lpr_model as _lm
from worker.models.lpr_model import (
    LPR_MODEL_VERSION,
    get_ocr_engine,
    get_plate_detector,
)
from worker.storage import save_evidence_snapshot

logger = logging.getLogger(__name__)

MODULE_TYPE = "lpr"

# Singapore plate format: up to 3 letters + 1-4 digits + 1 checksum letter
# Covers private (SBA1234C), commercial (GB123A), motorcycle, government, etc.
_SG_PLATE_RE = re.compile(r'^[A-Z]{1,3}\d{1,4}[A-Z]$')


def _normalise_plate(text: str) -> str:
    """Strip spaces/hyphens and uppercase — OCR sometimes inserts separators."""
    return re.sub(r'[\s\-\.]', '', text).upper()


def is_valid_sg_plate(text: str) -> bool:
    """Returns True if text matches Singapore plate format after normalisation."""
    return bool(_SG_PLATE_RE.match(_normalise_plate(text)))


def decode_frame(frame_jpeg_b64: str) -> np.ndarray:
    frame_bytes = base64.b64decode(frame_jpeg_b64)
    frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode frame JPEG")
    return frame


def detect_plates(frame: np.ndarray, conf: float = 0.4) -> list[dict]:
    """Returns [{"bbox": (x1, y1, x2, y2), "confidence": float}, ...].
    Returns an empty list when no model is available (no-op mode).
    When using the OIv7 fallback, PLATE_CLASS_FILTER restricts predictions
    to the 'Vehicle registration plate' class only."""
    detector = get_plate_detector()
    if detector is None:
        return []
    classes_arg = [_lm.PLATE_CLASS_FILTER] if _lm.PLATE_CLASS_FILTER is not None else None
    results = detector.predict(frame, conf=conf, classes=classes_arg, verbose=False)[0]
    boxes = []
    for box in results.boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        boxes.append({"bbox": (x1, y1, x2, y2), "confidence": float(box.conf[0])})
    return boxes


def run_ocr(crop: np.ndarray) -> tuple[str, float]:
    """Returns (plate_text, confidence). PaddleOCR rec-only mode (det=False)
    expects a pre-cropped plate image and returns [[text, confidence]]."""
    engine = get_ocr_engine()
    result = engine.ocr(crop, cls=False)
    if not result or not result[0]:
        return "", 0.0
    text, confidence = result[0][0]
    return text.strip().upper(), float(confidence)


def _lookup_watchlist(conn, tenant_id: UUID, plate_number: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT list_type FROM watchlist_entries
            WHERE tenant_id = %s AND plate_number = %s AND is_active = TRUE
              AND (expires_at IS NULL OR expires_at > now())
            ORDER BY (list_type = 'block') DESC
            LIMIT 1
            """,
            (str(tenant_id), plate_number),
        )
        row = cur.fetchone()
    return row[0] if row else None


# alert_code is NOT tenant-configurable — it is the stable machine key that
# message_params is rendered against for i18n, so it identifies *what happened*
# and stays in code. Severity and auto-incident are policy and now come from
# alert_rules_cache (Module 14), defaulting to shared/shared/alert_rules.py.
ALERT_CODES = {
    "block": "lpr.blocklist_hit",
    "allow": "lpr.allowlist_hit",
}


def _apply_alert_incident_rules(conn, tenant_id: UUID, camera_id: UUID, detection_id: UUID, plate_number: str, watchlist_match: str | None) -> tuple[UUID | None, UUID | None, str | None, str | None]:
    """Returns (alert_id, incident_id, title, severity).

    Severity is returned rather than left for the caller to look up again: a
    second resolve_rule() call could land after the 30s cache expired and
    disagree with the severity actually written to the row.
    """
    if watchlist_match not in ALERT_CODES:
        return None, None, None, None  # unmatched: no alert, no incident (plan §7)

    rule = resolve_rule(conn, tenant_id, MODULE_TYPE, watchlist_match)
    if rule is None:
        return None, None, None, None  # tenant disabled this trigger

    severity, create_incident, incident_severity = (
        rule.severity, rule.create_incident, rule.incident_severity,
    )
    alert_code = ALERT_CODES[watchlist_match]
    message_params = {"plate": plate_number}
    title = "Blocklisted plate detected" if watchlist_match == "block" else "Allowlisted plate detected"

    alert_id = uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts (id, tenant_id, detection_id, camera_id, module_type, severity,
                                 alert_code, message_params, title, message, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
            """,
            (alert_id, str(tenant_id), str(detection_id), str(camera_id), MODULE_TYPE, severity,
             alert_code, Jsonb(message_params), title, f"{title}: {plate_number}"),
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
                 f"Blocklist vehicle entry: {plate_number}", alert_code, Jsonb(message_params), incident_severity),
            )

    return alert_id, incident_id, title, severity


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    frame = decode_frame(job.frame_jpeg_b64)
    plates = detect_plates(frame)
    if not plates:
        return

    pending_events: list[tuple] = []         # (alert_id, severity, incident_id)
    pending_lpr_events: list[tuple] = []    # (detection_id, plate_text, direction, confidence)

    with get_tenant_session(job.tenant_id) as conn:
        threshold = get_tenant_setting(conn, job.tenant_id, "lpr.confidence_threshold")

        for plate in plates:
            x1, y1, x2, y2 = (int(v) for v in plate["bbox"])
            crop = frame[max(y1, 0):max(y2, 0), max(x1, 0):max(x2, 0)]
            if crop.size == 0:
                continue

            plate_text, ocr_confidence = run_ocr(crop)
            if not plate_text or ocr_confidence < threshold:
                continue  # below threshold -> no rows written at all (plan §6a)

            # Normalise and validate Singapore plate format (SXX NNNN X).
            # This filters out spurious OCR results and non-SG plates when using
            # the OIv7 fallback model which may catch plates from other regions.
            plate_text = _normalise_plate(plate_text)
            if not is_valid_sg_plate(plate_text):
                continue

            detection_id = uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence,
                                             bounding_box, raw_metadata, detected_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (detection_id, str(job.tenant_id), str(job.camera_id), MODULE_TYPE, ocr_confidence,
                     Jsonb({"x1": x1, "y1": y1, "x2": x2, "y2": y2}),
                     Jsonb({"model_version": LPR_MODEL_VERSION, "job_id": str(job.job_id)}),
                     job.captured_at),
                )
                cur.execute(
                    """
                    INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id,
                                             plate_number, plate_confidence, direction, vehicle_type,
                                             vehicle_color, watchlist_match)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (detection_id, job.captured_at, str(job.tenant_id), str(job.camera_id),
                     plate_text, ocr_confidence, "unknown", "unknown", "unknown", None),
                )

            watchlist_match = _lookup_watchlist(conn, job.tenant_id, plate_text)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE lpr_events SET watchlist_match = %s WHERE detection_id = %s AND detected_at = %s",
                    (watchlist_match, detection_id, job.captured_at),
                )

            # Evidence is saved for every plate that clears the confidence
            # threshold, not just watchlist matches — supports entry/exit
            # logging, which needs visual evidence independent of alerting.
            rel_path, checksum = save_evidence_snapshot(frame, job.tenant_id, detection_id)
            evidence_id = uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO evidence (id, tenant_id, detection_id, incident_id, media_type,
                                           storage_path, checksum_sha256, captured_at, capture_kind)
                    VALUES (%s, %s, %s, %s, 'image', %s, %s, %s, 'frame')
                    """,
                    (evidence_id, str(job.tenant_id), str(detection_id), None, rel_path, checksum, job.captured_at),
                )

            # The plate crop, as proof of what was actually read.
            #
            # The full frame above answers "which vehicle, which lane, when".
            # It does NOT answer "is SGB1234X what the camera saw" — at frame
            # resolution the plate is a smudge. That question is the one asked
            # at the visitor desk, where an operator confirms a vehicle's
            # identity against this read, and where a misread character means
            # the wrong vehicle billed or a blocklisted plate waved through.
            #
            # This crop is the exact pixels run_ocr() read, so it is proof of
            # the actual input rather than a second look at the scene.
            #
            # Best-effort: a failure here must not lose the detection, the
            # frame evidence or the alert. The plate read is the product; the
            # proof image is corroboration.
            try:
                crop_path, crop_checksum = save_evidence_snapshot(
                    crop, job.tenant_id, detection_id, suffix="_plate"
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO evidence (id, tenant_id, detection_id, incident_id, media_type,
                                               storage_path, checksum_sha256, captured_at, capture_kind)
                        VALUES (%s, %s, %s, %s, 'image', %s, %s, %s, 'plate_crop')
                        """,
                        (str(uuid4()), str(job.tenant_id), str(detection_id), None,
                         crop_path, crop_checksum, job.captured_at),
                    )
            except Exception:
                logger.warning(
                    "lpr: plate-crop evidence failed for detection %s; "
                    "frame evidence and plate read are unaffected",
                    detection_id, exc_info=True,
                )

            alert_id, incident_id, alert_title, severity = _apply_alert_incident_rules(
                conn, job.tenant_id, job.camera_id, detection_id, plate_text, watchlist_match
            )
            if incident_id is not None:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE evidence SET incident_id = %s WHERE id = %s", (str(incident_id), evidence_id)
                    )
            if alert_id is not None:
                pending_events.append((alert_id, severity, incident_id, alert_title))
                alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()

            # Queue for post-commit publish so parking automation in the API
            # process can react to every confirmed plate read, not just watchlist hits.
            pending_lpr_events.append((detection_id, plate_text, "unknown", ocr_confidence))

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail)
                    VALUES (%s, NULL, 'lpr_detection_processed', 'detection', %s, %s)
                    """,
                    (str(job.tenant_id), str(detection_id),
                     Jsonb({"plate": plate_text, "watchlist_match": watchlist_match,
                            "alert_id": str(alert_id) if alert_id else None,
                            "incident_id": str(incident_id) if incident_id else None})),
                )

        conn.commit()

    # Published only after commit succeeds, so a client reacting to the push
    # finds the row already durably there if it immediately queries the REST API.
    for alert_id, severity, incident_id, title in pending_events:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, severity, job.camera_id, title)
        if incident_id is not None:
            publish_incident_created(job.tenant_id, incident_id, alert_id, severity)

    for detection_id, plate_text, direction, confidence in pending_lpr_events:
        publish_lpr_plate_detected(job.tenant_id, detection_id, job.camera_id, plate_text, direction, confidence)
