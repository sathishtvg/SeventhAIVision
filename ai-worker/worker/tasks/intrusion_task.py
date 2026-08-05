"""Intrusion detection pipeline (plan §6c): decode frame -> detect persons ->
foot-point per person -> point-in-polygon against active restricted_zones ->
BreachTracker de-dup -> only on a *new* breach: detections/intrusion_events
rows -> alert (severity = zone's) -> auto-incident only for high/critical
zones -> evidence -> audit log. Duplicate (ongoing, suppressed) breaches write
nothing at all — not even a detections row — by design.
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
from worker.models.intrusion_model import INTRUSION_MODEL_VERSION, PERSON_CLASS_ID, get_person_detector
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "intrusion"
# Superseded by shared/shared/alert_rules.py (Module 14) — the zone_high /
# zone_critical defaults encode exactly this set. Kept only so existing tests
# that assert the historical policy still have something to reference.
AUTO_INCIDENT_SEVERITIES = {"high", "critical"}

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


def detect_persons(frame: np.ndarray, conf: float = 0.45) -> list[dict]:
    detector = get_person_detector()
    results = detector.predict(frame, classes=[PERSON_CLASS_ID], conf=conf, verbose=False)[0]
    boxes = []
    for box in results.boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        boxes.append({"bbox": (x1, y1, x2, y2), "confidence": float(box.conf[0])})
    return boxes


def foot_point(bbox: tuple[float, float, float, float], frame_w: int, frame_h: int) -> Point:
    x1, _y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    fy = y2  # bottom edge = feet
    return Point(cx / frame_w, fy / frame_h)  # normalized to match zone polygon's 0..1 space


def _fetch_active_zones(conn, tenant_id, camera_id) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, polygon, severity, applies_to_modules FROM restricted_zones "
            "WHERE tenant_id = %s AND camera_id = %s AND is_active = TRUE",
            (str(tenant_id), str(camera_id)),
        )
        rows = cur.fetchall()
    return [
        {"id": zone_id, "polygon": Polygon([(p["x"], p["y"]) for p in polygon_json]), "severity": severity}
        for zone_id, polygon_json, severity, applies_to_modules in rows
        if MODULE_TYPE in (applies_to_modules or ["intrusion"])
    ]


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    frame = decode_frame(job.frame_jpeg_b64)
    persons = detect_persons(frame)
    if not persons:
        return

    pending_events: list[tuple] = []  # (alert_id, severity, incident_id, title)

    with get_tenant_session(job.tenant_id) as conn:
        cooldown = get_tenant_setting(conn, job.tenant_id, "intrusion.breach_cooldown_seconds")
        zones = _fetch_active_zones(conn, job.tenant_id, job.camera_id)
        if not zones:
            return

        tracker = get_breach_tracker()

        for person in persons:
            point = foot_point(person["bbox"], job.frame_width, job.frame_height)

            for zone in zones:
                if not point.within(zone["polygon"]):
                    continue

                is_new, dwell = tracker.register(
                    job.tenant_id, job.camera_id, zone["id"], job.captured_at, cooldown_seconds=cooldown
                )
                if not is_new:
                    continue  # ongoing, already-alerted breach -> suppressed, nothing written

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
                        (detection_id, str(job.tenant_id), str(job.camera_id), MODULE_TYPE, person["confidence"],
                         Jsonb(bbox_json),
                         Jsonb({"model_version": INTRUSION_MODEL_VERSION, "zone_id": str(zone["id"]),
                                "job_id": str(job.job_id),
                                "frame_width": job.frame_width, "frame_height": job.frame_height}),
                         job.captured_at),
                    )
                    cur.execute(
                        """
                        INSERT INTO intrusion_events (detection_id, detected_at, tenant_id, camera_id,
                                                        zone_id, person_bbox, dwell_time_seconds)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (detection_id, job.captured_at, str(job.tenant_id), str(job.camera_id),
                         str(zone["id"]), Jsonb(bbox_json), dwell),
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

                # The breached zone's own severity selects which rule applies;
                # the rule then decides the alert severity and whether an
                # incident opens. The shipped defaults map zone_high -> high
                # + incident, reproducing the previous `severity =
                # zone["severity"]` + AUTO_INCIDENT_SEVERITIES behaviour exactly.
                zone_severity = zone["severity"]
                rule = resolve_rule(conn, job.tenant_id, MODULE_TYPE, f"zone_{zone_severity}")

                alert_id = None
                incident_id = None
                severity = zone_severity
                if rule is not None:
                    severity = rule.severity
                    alert_id = uuid4()
                    message_params = {
                        "zone_id": str(zone["id"]),
                        "severity": severity,
                        "zone_severity": zone_severity,
                    }
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO alerts (id, tenant_id, detection_id, camera_id, module_type, severity,
                                                 alert_code, message_params, title, message, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
                            """,
                            (alert_id, str(job.tenant_id), str(detection_id), str(job.camera_id), MODULE_TYPE, severity,
                             "intrusion.zone_breach", Jsonb(message_params),
                             "Restricted zone breach", f"Person detected in zone (severity={severity})"),
                        )

                    if rule.create_incident:
                        incident_id = uuid4()
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, alert_code,
                                                        message_params, severity, status, is_auto_created)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', TRUE)
                                """,
                                (incident_id, str(job.tenant_id), str(alert_id), str(job.camera_id),
                                 "Restricted zone intrusion", "intrusion.zone_breach", Jsonb(message_params),
                                 rule.incident_severity),
                            )
                            cur.execute("UPDATE evidence SET incident_id = %s WHERE id = %s", (str(incident_id), evidence_id))

                    pending_events.append((alert_id, severity, incident_id, "Restricted zone breach"))
                    alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail)
                        VALUES (%s, NULL, 'intrusion_detected', 'detection', %s, %s)
                        """,
                        # Audited even when the tenant suppressed the alert —
                        # the intrusion still happened, and the record of it is
                        # not theirs to switch off.
                        (str(job.tenant_id), str(detection_id),
                         Jsonb({"zone_id": str(zone["id"]), "severity": severity,
                                "alert_id": str(alert_id) if alert_id else None,
                                "incident_id": str(incident_id) if incident_id else None})),
                    )

        conn.commit()

    for alert_id, severity, incident_id, title in pending_events:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, severity, job.camera_id, title)
        if incident_id is not None:
            publish_incident_created(job.tenant_id, incident_id, alert_id, severity)
