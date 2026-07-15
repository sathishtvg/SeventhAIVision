"""Abandoned object detection pipeline.

Fires a 'medium' alert when an object has been stationary for longer than
the configured dwell threshold (tenant setting: abandoned.dwell_seconds, default 30s).
"""

import base64
from uuid import uuid4

import cv2
import numpy as np
from psycopg.types.json import Jsonb

from shared.events import FrameJob
from worker.common.metrics import alerts_created_total
from worker.common.realtime_publisher import publish_alert_created
from worker.common.tenant_settings_cache import get_tenant_setting
from worker.db_writer import get_tenant_session
from worker.models.abandoned_model import (
    ABANDONED_MODEL_VERSION,
    detect_abandoned,
)
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "abandoned"
SEVERITY = "medium"
ALERT_CODE = "abandoned.object_detected"


def decode_frame(b64: str) -> np.ndarray:
    arr = np.frombuffer(base64.b64decode(b64), np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode frame")
    return frame


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    frame = decode_frame(job.frame_jpeg_b64)

    with get_tenant_session(job.tenant_id) as conn:
        dwell_threshold = get_tenant_setting(
            conn, job.tenant_id, "abandoned.dwell_seconds", default=30.0
        )

    camera_key = str(job.camera_id)
    detections = detect_abandoned(frame, camera_key, dwell_threshold_seconds=float(dwell_threshold))

    if not detections:
        return

    with get_tenant_session(job.tenant_id) as conn:
        pending_alert_ids = []

        for det in detections:
            detection_id = uuid4()
            alert_id = uuid4()

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence,
                                             bounding_box, raw_metadata, detected_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        detection_id, str(job.tenant_id), str(job.camera_id),
                        MODULE_TYPE, 0.8,
                        Jsonb(det["bbox"]),
                        Jsonb({"model_version": ABANDONED_MODEL_VERSION,
                               "dwell_seconds": det["dwell_seconds"],
                               "job_id": str(job.job_id)}),
                        job.captured_at,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO abandoned_object_events (detection_id, detected_at, tenant_id,
                                                          camera_id, object_class, dwell_seconds, bbox)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        detection_id, job.captured_at,
                        str(job.tenant_id), str(job.camera_id),
                        det["object_class"], det["dwell_seconds"], Jsonb(det["bbox"]),
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
                    (evidence_id, str(job.tenant_id), str(detection_id),
                     rel_path, checksum, job.captured_at),
                )

            message_params = {
                "dwell_seconds": det["dwell_seconds"],
                "object_class": det["object_class"],
            }

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
                        "Abandoned object detected",
                        f"Unattended object stationary for {det['dwell_seconds']:.0f}s",
                    ),
                )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail)
                    VALUES (%s, NULL, 'abandoned_object_detected', 'detection', %s, %s)
                    """,
                    (str(job.tenant_id), str(detection_id), Jsonb({"alert_id": str(alert_id)})),
                )

            pending_alert_ids.append((alert_id,))
            alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()

        conn.commit()

    for (alert_id,) in pending_alert_ids:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, SEVERITY, job.camera_id, "Abandoned object detected")
