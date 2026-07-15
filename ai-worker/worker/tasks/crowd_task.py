"""Crowd Density Detection pipeline (plan Phase 3): decode frame -> detect all
persons via shared YOLOv8s person detector -> count persons per crowd_zone ->
only on threshold breach AND cooldown expired: detections/crowd_events row ->
alert (zone severity) -> auto-incident only for high/critical zones -> evidence
-> audit log.

Reuses the BreachTracker cooldown mechanism from intrusion detection so a zone
that stays overcrowded across many frames doesn't spam one alert per frame.
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
from worker.common.metrics import alerts_created_total
from worker.common.realtime_publisher import publish_alert_created, publish_incident_created
from worker.common.tenant_settings_cache import get_tenant_setting
from worker.db_writer import get_tenant_session
from worker.dedup import BreachTracker
from worker.models.crowd_model import CROWD_MODEL_VERSION, get_person_detector
from worker.models.intrusion_model import PERSON_CLASS_ID
from worker.storage import save_evidence_snapshot

MODULE_TYPE = "crowd"
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


def _fetch_active_zones(conn, tenant_id, camera_id) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, polygon, max_capacity, severity
            FROM crowd_zones
            WHERE tenant_id = %s AND camera_id = %s AND is_active = TRUE
            """,
            (str(tenant_id), str(camera_id)),
        )
        rows = cur.fetchall()
    return [
        {
            "id": zone_id,
            "polygon": Polygon([(p["x"], p["y"]) for p in polygon_json]),
            "max_capacity": max_capacity,
            "severity": severity,
        }
        for zone_id, polygon_json, max_capacity, severity in rows
    ]


def _count_persons_in_zone(persons: list[dict], zone_polygon: Polygon, frame_w: int, frame_h: int) -> list[dict]:
    in_zone = []
    for person in persons:
        x1, _y1, x2, y2 = person["bbox"]
        # Use foot-point (bottom-center) normalized to zone's 0..1 space
        cx = (x1 + x2) / 2.0 / frame_w
        fy = y2 / frame_h
        if Point(cx, fy).within(zone_polygon):
            in_zone.append(person)
    return in_zone


def process_frame_job(job: FrameJob) -> None:
    if MODULE_TYPE not in job.ai_modules_enabled:
        return

    frame = decode_frame(job.frame_jpeg_b64)

    detector = get_person_detector()
    results = detector.predict(frame, classes=[PERSON_CLASS_ID], conf=0.45, verbose=False)[0]
    persons = [
        {"bbox": box.xyxy[0].tolist(), "confidence": float(box.conf[0])}
        for box in results.boxes
    ]

    if not persons:
        return

    pending_events: list[tuple] = []

    with get_tenant_session(job.tenant_id) as conn:
        alert_threshold = get_tenant_setting(conn, job.tenant_id, "crowd.alert_threshold_ratio")
        cooldown = get_tenant_setting(conn, job.tenant_id, "crowd.breach_cooldown_seconds")
        zones = _fetch_active_zones(conn, job.tenant_id, job.camera_id)
        if not zones:
            return

        tracker = get_breach_tracker()

        for zone in zones:
            in_zone = _count_persons_in_zone(persons, zone["polygon"], job.frame_width, job.frame_height)
            person_count = len(in_zone)
            max_cap = zone["max_capacity"]
            density_ratio = person_count / max_cap if max_cap > 0 else 1.0

            if density_ratio < alert_threshold:
                continue

            is_new, dwell = tracker.register(
                job.tenant_id, job.camera_id, zone["id"], job.captured_at,
                cooldown_seconds=cooldown,
            )
            if not is_new:
                continue

            # Use the highest-confidence person as the representative bbox
            rep_person = max(in_zone, key=lambda p: p["confidence"])
            x1, y1, x2, y2 = (int(v) for v in rep_person["bbox"])
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
                        MODULE_TYPE, rep_person["confidence"],
                        Jsonb(bbox_json),
                        Jsonb({
                            "model_version": CROWD_MODEL_VERSION,
                            "zone_id": str(zone["id"]),
                            "person_count": person_count,
                            "max_capacity": max_cap,
                            "density_ratio": float(density_ratio),
                            "job_id": str(job.job_id),
                        }),
                        job.captured_at,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO crowd_events (detection_id, detected_at, tenant_id, camera_id,
                                               zone_id, person_count, max_capacity, density_ratio)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        detection_id, job.captured_at,
                        str(job.tenant_id), str(job.camera_id),
                        str(zone["id"]), person_count, max_cap,
                        round(density_ratio, 4),
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

            severity = zone["severity"]
            message_params = {
                "zone_id": str(zone["id"]),
                "person_count": person_count,
                "max_capacity": max_cap,
            }
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
                        MODULE_TYPE, severity, "crowd.threshold_exceeded",
                        Jsonb(message_params),
                        "Crowd capacity threshold exceeded",
                        f"Zone has {person_count}/{max_cap} persons (threshold {alert_threshold:.0%})",
                    ),
                )

            incident_id = None
            if severity in AUTO_INCIDENT_SEVERITIES:
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
                            "Crowd capacity incident", "crowd.threshold_exceeded",
                            Jsonb(message_params), severity,
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
                    VALUES (%s, NULL, 'crowd_threshold_exceeded', 'detection', %s, %s)
                    """,
                    (
                        str(job.tenant_id), str(detection_id),
                        Jsonb({
                            "zone_id": str(zone["id"]),
                            "person_count": person_count,
                            "alert_id": str(alert_id),
                            "incident_id": str(incident_id) if incident_id else None,
                        }),
                    ),
                )

            pending_events.append((alert_id, severity, incident_id, "Crowd capacity threshold exceeded"))
            alerts_created_total.labels(module_type=MODULE_TYPE, tenant_id=str(job.tenant_id)).inc()

        conn.commit()

    for alert_id, severity, incident_id, title in pending_events:
        publish_alert_created(job.tenant_id, alert_id, MODULE_TYPE, severity, job.camera_id, title)
        if incident_id is not None:
            publish_incident_created(job.tenant_id, incident_id, alert_id, severity)
