"""Weapon Detection pipeline (plan Phase 3): decode frame -> detect weapons ->
weapon_events row -> alert (severity by weapon_type) -> auto-incident for
firearm and blade -> evidence -> audit log.

All detections above the confidence threshold write a row — no cooldown dedup
(unlike crowd/intrusion) because every weapon detection in a new frame is a
new safety risk signal worth an operator review.
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
import worker.models.weapon_model as _wm
from worker.models.weapon_model import (
    ACTIVE_WEAPON_CLASSES,
    AUTO_INCIDENT_WEAPON_TYPES,
    WEAPON_MODEL_VERSION,
    WEAPON_SEVERITIES,
    get_weapon_detector,
)
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "weapon"


def decode_frame(frame_jpeg_b64: str) -> np.ndarray:
    frame_bytes = base64.b64decode(frame_jpeg_b64)
    frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode frame JPEG")
    return frame


def detect_weapons(frame: np.ndarray, conf_threshold: float) -> list[dict]:
    """Returns an empty list when the weapon weight file is absent (no-op mode)."""
    detector = get_weapon_detector()
    if detector is None:
        return []
    classes_arg = _wm.WEAPON_CLASS_IDS  # None = all classes (fine-tuned); list = OIv7 filter
    results = detector.predict(frame, conf=conf_threshold, classes=classes_arg, verbose=False)[0]
    detections = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        weapon_type = ACTIVE_WEAPON_CLASSES.get(cls_id)
        if weapon_type is None:
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        detections.append({
            "weapon_type": weapon_type,
            "confidence": float(box.conf[0]),
            "bbox": (x1, y1, x2, y2),
        })
    return detections


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    frame = decode_frame(job.frame_jpeg_b64)
    pending_events: list[tuple] = []

    with get_tenant_session(job.tenant_id) as conn:
        conf_threshold = get_tenant_setting(conn, job.tenant_id, "weapon.confidence_threshold")
        detections = detect_weapons(frame, conf_threshold)
        if not detections:
            return

        for det in detections:
            x1, y1, x2, y2 = (int(v) for v in det["bbox"])
            bbox_json = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            weapon_type = det["weapon_type"]
            # The weapon class is the trigger key; the rule supplies severity
            # and whether an incident opens. Defaults reproduce
            # WEAPON_SEVERITIES + AUTO_INCIDENT_WEAPON_TYPES exactly.
            rule = resolve_rule(conn, job.tenant_id, MODULE_TYPE, weapon_type)
            if rule is None:
                continue  # tenant disabled alerts for this weapon class
            severity = rule.severity
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
                        MODULE_TYPE, det["confidence"],
                        Jsonb(bbox_json),
                        Jsonb({
                            "model_version": WEAPON_MODEL_VERSION,
                            "weapon_type": weapon_type,
                            "job_id": str(job.job_id),
                        }),
                        job.captured_at,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO weapon_events (detection_id, detected_at, tenant_id, camera_id,
                                               weapon_type, confidence, bbox)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        detection_id, job.captured_at,
                        str(job.tenant_id), str(job.camera_id),
                        weapon_type, det["confidence"], Jsonb(bbox_json),
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

            alert_code = f"weapon.{weapon_type}_detected"
            message_params = {"weapon_type": weapon_type, "confidence": det["confidence"]}
            alert_id = uuid4()
            title = f"{weapon_type.capitalize()} detected"
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
                        f"Weapon ({weapon_type}) detected (confidence={det['confidence']:.2f})",
                    ),
                )

            incident_id = None
            if rule.create_incident:
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
                            f"Weapon incident ({weapon_type})", alert_code,
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
                    VALUES (%s, NULL, 'weapon_detected', 'detection', %s, %s)
                    """,
                    (
                        str(job.tenant_id), str(detection_id),
                        Jsonb({
                            "weapon_type": weapon_type,
                            "alert_id": str(alert_id),
                            "incident_id": str(incident_id) if incident_id else None,
                        }),
                    ),
                )

            pending_events.append((alert_id, severity, incident_id, title))
            alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()

        conn.commit()

    for alert_id, severity, incident_id, title in pending_events:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, severity, job.camera_id, title)
        if incident_id is not None:
            publish_incident_created(job.tenant_id, incident_id, alert_id, severity)
