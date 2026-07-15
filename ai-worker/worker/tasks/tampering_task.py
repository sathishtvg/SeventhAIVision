"""Camera tampering detection pipeline.

Fires a 'high' alert + auto-incident when tampering is detected.
Per-camera reference frames are kept in a module-level dict (5-minute refresh
so a legitimate scene change after setup doesn't keep alerting).
"""

import base64
import os
import time
from collections import defaultdict
from uuid import uuid4

import cv2
import numpy as np
from psycopg.types.json import Jsonb

from shared.events import FrameJob
from worker.common.metrics import alerts_created_total
from worker.common.realtime_publisher import publish_alert_created, publish_incident_created
from worker.common.tenant_settings_cache import get_tenant_setting
from worker.db_writer import get_tenant_session
from worker.models.tampering_model import (
    TAMPERING_MODEL_VERSION,
    detect_tampering,
)
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "tampering"
SEVERITY = "high"
ALERT_CODE = "tampering.camera_tampered"
# Cooldown in seconds between repeated alerts per camera
ALERT_COOLDOWN = int(os.environ.get("TAMPERING_COOLDOWN_SECONDS", "300"))
# Reference frame age before refresh (seconds)
REFERENCE_REFRESH_INTERVAL = int(os.environ.get("TAMPERING_REF_REFRESH_SECONDS", "300"))

# Module-level state (single-process)
_reference_frames: dict[str, tuple[np.ndarray, float]] = {}  # camera_id -> (frame, ts)
_last_alert_ts: dict[str, float] = defaultdict(float)  # camera_id -> ts


def decode_frame(b64: str) -> np.ndarray:
    arr = np.frombuffer(base64.b64decode(b64), np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode frame")
    return frame


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    camera_key = str(job.camera_id)
    frame = decode_frame(job.frame_jpeg_b64)
    now = time.time()

    # Refresh reference frame periodically
    ref_entry = _reference_frames.get(camera_key)
    if ref_entry is None or (now - ref_entry[1]) > REFERENCE_REFRESH_INTERVAL:
        _reference_frames[camera_key] = (frame.copy(), now)
        return  # First frame or refresh — no comparison yet

    ref_frame = ref_entry[0]
    result = detect_tampering(frame, ref_frame)
    if result is None:
        return

    # Cooldown check
    if now - _last_alert_ts[camera_key] < ALERT_COOLDOWN:
        return

    tampering_type = result["tampering_type"]
    score = result["score"]
    reason = result["reason"]

    detection_id = uuid4()
    alert_id = uuid4()
    incident_id = uuid4()

    with get_tenant_session(job.tenant_id) as conn:
        conf_threshold = get_tenant_setting(conn, job.tenant_id, "tampering.score_threshold", default=0.5)
        if score < conf_threshold:
            return

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence,
                                         bounding_box, raw_metadata, detected_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    detection_id, str(job.tenant_id), str(job.camera_id),
                    MODULE_TYPE, score, Jsonb({}),
                    Jsonb({"model_version": TAMPERING_MODEL_VERSION, "tampering_type": tampering_type,
                           "reason": reason, "job_id": str(job.job_id)}),
                    job.captured_at,
                ),
            )
            cur.execute(
                """
                INSERT INTO tampering_events (detection_id, detected_at, tenant_id, camera_id,
                                               tampering_type, score, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (detection_id, job.captured_at, str(job.tenant_id), str(job.camera_id),
                 tampering_type, score, reason),
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

        message_params = {"tampering_type": tampering_type, "score": score}
        title = f"Camera tampering detected: {tampering_type}"

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts (id, tenant_id, detection_id, camera_id, module_type,
                                     severity, alert_code, message_params, title, message, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
                """,
                (
                    alert_id, str(job.tenant_id), str(detection_id), str(job.camera_id),
                    MODULE_TYPE, SEVERITY, ALERT_CODE, Jsonb(message_params),
                    title,
                    f"Camera tampering ({tampering_type}) score={score:.2f}: {reason}",
                ),
            )

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, alert_code,
                                        message_params, severity, status, is_auto_created)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', TRUE)
                """,
                (
                    incident_id, str(job.tenant_id), str(alert_id), str(job.camera_id),
                    f"Camera tampering: {tampering_type}", ALERT_CODE,
                    Jsonb(message_params), SEVERITY,
                ),
            )
            cur.execute(
                "UPDATE evidence SET incident_id = %s WHERE id = %s",
                (str(incident_id), str(evidence_id)),
            )

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail)
                VALUES (%s, NULL, 'tampering_detected', 'detection', %s, %s)
                """,
                (
                    str(job.tenant_id), str(detection_id),
                    Jsonb({"tampering_type": tampering_type, "alert_id": str(alert_id),
                           "incident_id": str(incident_id)}),
                ),
            )

        conn.commit()

    _last_alert_ts[camera_key] = now
    # Reset reference frame after confirmed tampering so it doesn't keep firing
    _reference_frames[camera_key] = (frame.copy(), now)

    alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()
    publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, SEVERITY, job.camera_id, title)
    publish_incident_created(job.tenant_id, incident_id, alert_id, SEVERITY)
