"""PPE Detection pipeline (plan Phase 3): decode frame -> detect PPE items on
every person crop -> classify items_detected vs items_missing -> only write when
at least one required item is missing -> ppe_events row -> alert (high) -> auto-
incident -> evidence -> audit log.

Alert logic: any PPE violation fires a 'high' severity alert + auto-incident,
since a missing hard hat or vest in an industrial zone is an immediate safety
risk. No watchlist / allowlist concept here — the absence of required PPE is
the signal regardless of who the person is.
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
import worker.models.ppe_model as _pm
from worker.models.ppe_model import (
    ACTIVE_PPE_CLASSES,
    ACTIVE_REQUIRED_PPE,
    PPE_MODEL_VERSION,
    get_ppe_detector,
)
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "ppe"
ALERT_SEVERITY = "high"


def decode_frame(frame_jpeg_b64: str) -> np.ndarray:
    frame_bytes = base64.b64decode(frame_jpeg_b64)
    frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode frame JPEG")
    return frame


def detect_ppe(frame: np.ndarray, conf_threshold: float) -> list[dict]:
    """Returns one entry per person with missing required PPE.
    Returns an empty list when the PPE weight file is absent (no-op mode)."""
    detector = get_ppe_detector()
    if detector is None:
        return []
    classes_arg = _pm.PPE_CLASS_IDS  # None = all classes (fine-tuned); list = OIv7 filter
    results = detector.predict(frame, conf=conf_threshold, classes=classes_arg, verbose=False)[0]

    persons: list[dict] = []
    ppe_items: list[dict] = []

    for box in results.boxes:
        cls_id = int(box.cls[0])
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        conf = float(box.conf[0])
        if _pm.PERSON_CLASS_ID is not None:
            # OIv7 mode: person has an explicit class ID (381)
            if cls_id == _pm.PERSON_CLASS_ID:
                persons.append({"bbox": (x1, y1, x2, y2), "confidence": conf})
            else:
                class_name = ACTIVE_PPE_CLASSES.get(cls_id)
                if class_name:
                    ppe_items.append({"class": class_name, "bbox": (x1, y1, x2, y2), "confidence": conf})
        else:
            # Custom model mode: any class not in PPE_CLASSES is treated as a person
            class_name = ACTIVE_PPE_CLASSES.get(cls_id)
            if class_name is None:
                persons.append({"bbox": (x1, y1, x2, y2), "confidence": conf})
            else:
                ppe_items.append({"class": class_name, "bbox": (x1, y1, x2, y2), "confidence": conf})

    # Associate PPE items with persons by overlap (IoU-style containment)
    for person in persons:
        px1, py1, px2, py2 = person["bbox"]
        detected: set[str] = set()
        for item in ppe_items:
            ix1, iy1, ix2, iy2 = item["bbox"]
            # item center within person bbox = associated
            cx, cy = (ix1 + ix2) / 2, (iy1 + iy2) / 2
            if px1 <= cx <= px2 and py1 <= cy <= py2:
                detected.add(item["class"])
        person["items_detected"] = sorted(detected)
        person["items_missing"] = sorted(ACTIVE_REQUIRED_PPE - detected)

    return [p for p in persons if p["items_missing"]]  # only violations


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    frame = decode_frame(job.frame_jpeg_b64)
    conf_threshold = None  # resolved inside the DB session

    pending_events: list[tuple] = []

    with get_tenant_session(job.tenant_id) as conn:
        conf_threshold = get_tenant_setting(conn, job.tenant_id, "ppe.confidence_threshold")
        violations = detect_ppe(frame, conf_threshold)
        if not violations:
            return

        # Single-outcome module: one trigger key, 'violation'. The default
        # reproduces ALERT_SEVERITY='high' plus the unconditional incident.
        rule = resolve_rule(conn, job.tenant_id, MODULE_TYPE, "violation")
        if rule is None:
            return  # tenant disabled PPE alerting entirely
        severity = rule.severity

        for person in violations:
            x1, y1, x2, y2 = (int(v) for v in person["bbox"])
            bbox_json = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
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
                            "model_version": PPE_MODEL_VERSION,
                            "items_detected": person["items_detected"],
                            "items_missing": person["items_missing"],
                            "job_id": str(job.job_id),
                        }),
                        job.captured_at,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO ppe_events (detection_id, detected_at, tenant_id, camera_id,
                                             person_bbox, items_detected, items_missing, severity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        detection_id, job.captured_at,
                        str(job.tenant_id), str(job.camera_id),
                        Jsonb(bbox_json),
                        Jsonb(person["items_detected"]),
                        Jsonb(person["items_missing"]),
                        severity,
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

            message_params = {"items_missing": person["items_missing"]}
            alert_id = uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts (id, tenant_id, detection_id, camera_id, module_type,
                                         severity, alert_code, message_params, title, message, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
                    """,
                    (
                        alert_id, str(job.tenant_id), str(detection_id), str(job.camera_id),
                        MODULE_TYPE, severity, "ppe.violation",
                        Jsonb(message_params),
                        "PPE violation detected",
                        f"Person missing required PPE: {', '.join(person['items_missing'])}",
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
                            "PPE violation", "ppe.violation",
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
                    VALUES (%s, NULL, 'ppe_violation_detected', 'detection', %s, %s)
                    """,
                    (
                        str(job.tenant_id), str(detection_id),
                        Jsonb({
                            "items_missing": person["items_missing"],
                            "alert_id": str(alert_id),
                            "incident_id": str(incident_id) if incident_id else None,
                        }),
                    ),
                )

            pending_events.append((alert_id, incident_id))
            alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()

        conn.commit()

    for alert_id, incident_id in pending_events:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, severity, job.camera_id, "PPE violation detected")
        # incident_id is None when the rule alerts without escalating; publishing
        # a null incident would push a malformed event to every connected client.
        if incident_id is not None:
            publish_incident_created(job.tenant_id, incident_id, alert_id, severity)
