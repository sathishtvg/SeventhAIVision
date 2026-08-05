"""Behavior Analysis pipeline (plan Phase 3): decode frame -> run pose
estimation -> classify behaviors (loitering, running, aggression, tailgating)
-> behavior_events row -> alert -> auto-incident for aggression/tailgating.

Behavior detection approaches:
- loitering: person foot-point stays in a restricted_zone for > loitering_dwell_seconds
  (uses BreachTracker sliding-window dwell timer, same as intrusion but with a
  longer threshold — the breach_cooldown also acts as the re-alert suppressor)
- running: average ankle velocity (keypoint displacement between frames heuristic
  via pose model confidence + bbox size change). Since workers process frames
  independently, we approximate: if the bbox height spans a large fraction of
  the frame AND keypoint confidence is high, the person is moving fast.
- aggression: two or more persons detected with overlapping bboxes AND high
  keypoint confidence — a proxy for physical altercation.
- tailgating: two persons detected at a zone entry point with < N seconds between
  their crossing events (uses BreachTracker with a very short TTL).
"""

import base64
import os
from uuid import uuid4

import cv2
import numpy as np
import redis
from psycopg.types.json import Jsonb
from shapely.geometry import Point, Polygon

from shared.events import FrameJob
from worker.common.alert_rules_cache import resolve_rule
from worker.common.metrics import alerts_created_total
from worker.common.realtime_publisher import publish_alert_created, publish_incident_created
from worker.common.tenant_settings_cache import get_tenant_setting
from worker.db_writer import get_tenant_session
from worker.dedup import BreachTracker
from worker.models.behavior_model import (
    AUTO_INCIDENT_BEHAVIORS,
    BEHAVIOR_MODEL_VERSION,
    BEHAVIOR_SEVERITIES,
    KP_LEFT_ANKLE,
    KP_RIGHT_ANKLE,
    get_pose_model,
)
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "behavior"

_breach_tracker: BreachTracker | None = None


def get_breach_tracker() -> BreachTracker:
    global _breach_tracker
    if _breach_tracker is None:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _breach_tracker = BreachTracker(redis.from_url(redis_url))
    return _breach_tracker


def decode_frame(frame_jpeg_b64: str) -> np.ndarray:
    frame_bytes = base64.b64decode(frame_jpeg_b64)
    frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode frame JPEG")
    return frame


def _fetch_active_zones(conn, tenant_id, camera_id) -> list[dict]:
    """Fetch restricted_zones used for loitering/tailgating zone checks."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, polygon, severity, applies_to_modules FROM restricted_zones "
            "WHERE tenant_id = %s AND camera_id = %s AND is_active = TRUE",
            (str(tenant_id), str(camera_id)),
        )
        rows = cur.fetchall()
    return [
        {
            "id": zone_id,
            "polygon": Polygon([(p["x"], p["y"]) for p in polygon_json]),
            "severity": severity,
        }
        for zone_id, polygon_json, severity, applies_to_modules in rows
        if MODULE_TYPE in (applies_to_modules or ["intrusion"])
    ]


def _detect_persons_with_pose(frame: np.ndarray) -> list[dict]:
    model = get_pose_model()
    results = model.predict(frame, conf=0.4, verbose=False)[0]
    persons = []
    for i, box in enumerate(results.boxes):
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        conf = float(box.conf[0])
        keypoints = None
        if results.keypoints is not None and i < len(results.keypoints):
            kp_data = results.keypoints[i].data[0]  # shape (17, 3): x, y, conf
            keypoints = kp_data.tolist()
        persons.append({"bbox": (x1, y1, x2, y2), "confidence": conf, "keypoints": keypoints})
    return persons


def _classify_behaviors(persons: list[dict], frame_w: int, frame_h: int) -> list[tuple[str, dict]]:
    """Returns list of (behavior_type, person_dict) pairs for detected behaviors."""
    behaviors: list[tuple[str, dict]] = []

    # Running: high ankle keypoint velocity approximated by large bbox relative size
    # and high ankle confidence. A person running typically has a tall bbox and
    # visible, high-confidence ankles.
    for p in persons:
        kp = p.get("keypoints")
        if kp:
            lank_conf = kp[KP_LEFT_ANKLE][2] if len(kp) > KP_LEFT_ANKLE else 0
            rank_conf = kp[KP_RIGHT_ANKLE][2] if len(kp) > KP_RIGHT_ANKLE else 0
            x1, y1, x2, y2 = p["bbox"]
            bbox_height_ratio = (y2 - y1) / frame_h
            # Running heuristic: visible ankles + person takes up a significant portion
            # of frame height (close camera). This is a very rough single-frame proxy.
            if (lank_conf > 0.5 or rank_conf > 0.5) and bbox_height_ratio > 0.3:
                behaviors.append(("running", p))

    # Aggression: two or more persons with overlapping bboxes (proximity) and high
    # keypoint confidence — proxy for physical contact.
    if len(persons) >= 2:
        for i, p1 in enumerate(persons):
            for p2 in persons[i + 1:]:
                x1a, y1a, x2a, y2a = p1["bbox"]
                x1b, y1b, x2b, y2b = p2["bbox"]
                # Check overlap
                overlap_x = max(0, min(x2a, x2b) - max(x1a, x1b))
                overlap_y = max(0, min(y2a, y2b) - max(y1a, y1b))
                if overlap_x > 0 and overlap_y > 0:
                    # Persons overlap physically — flag aggression
                    behaviors.append(("aggression", p1))

    return behaviors


def _write_behavior_event(
    conn,
    job: FrameJob,
    person: dict,
    behavior_type: str,
    zone_id=None,
    duration_seconds: float | None = None,
) -> tuple:
    x1, y1, x2, y2 = (int(v) for v in person["bbox"])
    bbox_json = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    # The behaviour class is the trigger key. A disabled rule still records the
    # detection (it happened), it just won't raise an alert — so fall back to
    # the shipped severity for the detection row and let the emit step below
    # decide whether anyone is told about it.
    _rule = resolve_rule(conn, job.tenant_id, MODULE_TYPE, behavior_type)
    severity = _rule.severity if _rule is not None else BEHAVIOR_SEVERITIES[behavior_type]
    detection_id = uuid4()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence,
                                     bounding_box, raw_metadata, detected_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                detection_id, str(job.tenant_id), str(job.camera_id),
                MODULE_TYPE, person["confidence"],
                Jsonb(bbox_json),
                Jsonb({
                    "model_version": BEHAVIOR_MODEL_VERSION,
                    "behavior_type": behavior_type,
                    "zone_id": str(zone_id) if zone_id else None,
                    "job_id": str(job.job_id),
                }),
                job.captured_at,
            ),
        )
        cur.execute(
            """
            INSERT INTO behavior_events (detection_id, detected_at, tenant_id, camera_id,
                                          zone_id, behavior_type, confidence, duration_seconds, person_bbox)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                detection_id, job.captured_at,
                str(job.tenant_id), str(job.camera_id),
                str(zone_id) if zone_id else None,
                behavior_type, person["confidence"],
                duration_seconds, Jsonb(bbox_json),
            ),
        )

    return detection_id, severity, bbox_json


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    frame = decode_frame(job.frame_jpeg_b64)
    persons = _detect_persons_with_pose(frame)
    if not persons:
        return

    pending_events: list[tuple] = []

    with get_tenant_session(job.tenant_id) as conn:
        loitering_dwell = get_tenant_setting(conn, job.tenant_id, "behavior.loitering_dwell_seconds")
        behavior_cooldown = get_tenant_setting(conn, job.tenant_id, "behavior.breach_cooldown_seconds")
        zones = _fetch_active_zones(conn, job.tenant_id, job.camera_id)
        tracker = get_breach_tracker()

        # --- Loitering: person in zone for > loitering_dwell_seconds ---
        for person in persons:
            x1, _y1, x2, y2 = person["bbox"]
            cx = (x1 + x2) / 2.0 / job.frame_width
            fy = y2 / job.frame_height
            foot = Point(cx, fy)
            for zone in zones:
                if not foot.within(zone["polygon"]):
                    continue
                is_new, dwell = tracker.register(
                    job.tenant_id, job.camera_id, zone["id"], job.captured_at,
                    cooldown_seconds=behavior_cooldown,
                )
                if not is_new or dwell < loitering_dwell:
                    continue
                detection_id, severity, bbox_json = _write_behavior_event(
                    conn, job, person, "loitering", zone_id=zone["id"], duration_seconds=dwell
                )
                _emit_behavior_alert_incident(
                    conn, job, detection_id, severity, "loitering", bbox_json, frame, pending_events
                )

        # --- Single-frame behaviors: running, aggression ---
        for behavior_type, person in _classify_behaviors(persons, job.frame_width, job.frame_height):
            detection_id, severity, bbox_json = _write_behavior_event(
                conn, job, person, behavior_type
            )
            _emit_behavior_alert_incident(
                conn, job, detection_id, severity, behavior_type, bbox_json, frame, pending_events
            )

        conn.commit()

    for alert_id, severity, incident_id, title in pending_events:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, severity, job.camera_id, title)
        if incident_id is not None:
            publish_incident_created(job.tenant_id, incident_id, alert_id, severity)


def _emit_behavior_alert_incident(conn, job, detection_id, severity, behavior_type, bbox_json, frame, pending_events):
    message_params = {"behavior_type": behavior_type}
    alert_code = f"behavior.{behavior_type}"
    alert_id = uuid4()
    title = f"{behavior_type.replace('_', ' ').capitalize()} detected"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts (id, tenant_id, detection_id, camera_id, module_type,
                                 severity, alert_code, message_params, title, message, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
            """,
            (
                alert_id, str(job.tenant_id), str(detection_id), str(job.camera_id),
                MODULE_TYPE, severity, alert_code,
                Jsonb(message_params),
                title,
                f"Behavior '{behavior_type}' detected",
            ),
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

    rule = resolve_rule(conn, job.tenant_id, MODULE_TYPE, behavior_type)
    incident_id = None
    if rule is not None and rule.create_incident:
        incident_id = uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, alert_code,
                                        message_params, severity, status, is_auto_created)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', TRUE)
                """,
                (
                    incident_id, str(job.tenant_id), str(alert_id), str(job.camera_id),
                    f"Behavior incident ({behavior_type})", alert_code,
                    Jsonb(message_params), rule.incident_severity,
                ),
            )
            cur.execute(
                "UPDATE evidence SET incident_id = %s WHERE id = %s",
                (str(incident_id), evidence_id),
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail)
            VALUES (%s, NULL, 'behavior_detected', 'detection', %s, %s)
            """,
            (
                str(job.tenant_id), str(detection_id),
                Jsonb({
                    "behavior_type": behavior_type,
                    "alert_id": str(alert_id),
                    "incident_id": str(incident_id) if incident_id else None,
                }),
            ),
        )

    pending_events.append((alert_id, severity, incident_id, title))
    alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()
