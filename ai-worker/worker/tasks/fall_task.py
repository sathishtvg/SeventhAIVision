"""Slip/fall detection pipeline.

Fires a 'high' alert + auto-incident when a person is detected in a fallen position
using YOLOv8n-pose skeleton estimation.
"""

import base64
from uuid import uuid4

import cv2
import numpy as np
from psycopg.types.json import Jsonb

from shared.events import FrameJob
from worker.common.metrics import alerts_created_total
from worker.common.realtime_publisher import publish_alert_created, publish_incident_created
from worker.common.tenant_settings_cache import get_tenant_setting
from worker.db_writer import get_tenant_session
from worker.models.fall_model import FALL_MODEL_VERSION, detect_falls
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "fall"
SEVERITY = "high"
ALERT_CODE = "fall.person_fallen"


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
    detections = detect_falls(frame)
    if not detections:
        return

    with get_tenant_session(job.tenant_id) as conn:
        conf_threshold = get_tenant_setting(
            conn, job.tenant_id, "fall.confidence_threshold", default=0.45
        )
        pending = []

        for det in detections:
            if det["fall_confidence"] < float(conf_threshold):
                continue

            detection_id = uuid4()
            alert_id = uuid4()
            incident_id = uuid4()

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence,
                                             bounding_box, raw_metadata, detected_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        detection_id, str(job.tenant_id), str(job.camera_id),
                        MODULE_TYPE, det["fall_confidence"],
                        Jsonb(det["person_bbox"] or {}),
                        Jsonb({"model_version": FALL_MODEL_VERSION, "job_id": str(job.job_id)}),
                        job.captured_at,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO fall_events (detection_id, detected_at, tenant_id, camera_id,
                                              fall_confidence, pose_keypoints, person_bbox)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        detection_id, job.captured_at,
                        str(job.tenant_id), str(job.camera_id),
                        det["fall_confidence"],
                        Jsonb(det["pose_keypoints"] or []),
                        Jsonb(det["person_bbox"] or {}),
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

            message_params = {"fall_confidence": det["fall_confidence"]}

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
                        "Person fallen detected",
                        f"Slip/fall event detected (confidence={det['fall_confidence']:.2f})",
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
                        "Person fallen — possible medical emergency", ALERT_CODE,
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
                    VALUES (%s, NULL, 'fall_detected', 'detection', %s, %s)
                    """,
                    (
                        str(job.tenant_id), str(detection_id),
                        Jsonb({"alert_id": str(alert_id), "incident_id": str(incident_id)}),
                    ),
                )

            pending.append((alert_id, incident_id))
            alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()

        conn.commit()

    for alert_id, incident_id in pending:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, SEVERITY, job.camera_id, "Person fallen detected")
        publish_incident_created(job.tenant_id, incident_id, alert_id, SEVERITY)
