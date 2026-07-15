import base64
import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL_SYNC",
    "postgresql+psycopg://svc_app:change_me_dev_only@postgres:5432/seventh_ai_vision",
)
os.environ.setdefault("EVIDENCE_ROOT", os.path.join(os.path.dirname(__file__), "_evidence_tmp_intrusion"))
os.environ.setdefault("TEST_REDIS_URL", "redis://redis:6379/1")
os.environ["REDIS_URL"] = os.environ["TEST_REDIS_URL"]

import cv2
import numpy as np
import psycopg
import pytest
from psycopg.types.json import Jsonb

from shapely.geometry import Point, Polygon
from shared.events import FrameJob
from worker.common.tenant_settings_cache import clear_cache
from worker.tasks import intrusion_task

ADMIN_URL = os.environ.get("ADMIN_TEST_DATABASE_URL_SYNC", "postgresql://postgres:change_me_dev_only@postgres:5432/seventh_ai_vision")
SAMPLE_FRAME = np.zeros((100, 200, 3), dtype=np.uint8)
PERSON_BBOX = {"bbox": (80, 20, 120, 90), "confidence": 0.9}  # foot point ~ (100/200, 90/100) = (0.5, 0.9)

# Polygon covering the bottom-right area where PERSON_BBOX's foot point lands.
ZONE_POLYGON = [{"x": 0.3, "y": 0.7}, {"x": 0.7, "y": 0.7}, {"x": 0.7, "y": 1.0}, {"x": 0.3, "y": 1.0}]
OUTSIDE_POLYGON = [{"x": 0.0, "y": 0.0}, {"x": 0.1, "y": 0.0}, {"x": 0.1, "y": 0.1}, {"x": 0.0, "y": 0.1}]


def _b64(frame) -> str:
    ok, buf = cv2.imencode(".jpg", frame)
    return base64.b64encode(buf.tobytes()).decode("ascii")


@pytest.fixture
def tenant_camera_zone():
    clear_cache()
    tenant_id, camera_id, zone_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, 'T', %s)", (tenant_id, f"t-{tenant_id.hex[:8]}"))
            cur.execute("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) VALUES (%s, %s, 'Cam', '[\"intrusion\"]')", (camera_id, tenant_id))
        conn.commit()
    yield tenant_id, camera_id, zone_id
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            for table in ("audit_logs", "evidence", "incidents", "alerts", "intrusion_events", "detections", "restricted_zones", "cameras"):
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        conn.commit()
    import redis

    r = redis.from_url(os.environ["REDIS_URL"])
    r.delete(f"intrusion:active_breach:{tenant_id}:{camera_id}:{zone_id}")


def _seed_zone(tenant_id, camera_id, zone_id, severity, polygon=ZONE_POLYGON):
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon, severity) VALUES (%s, %s, %s, 'Zone', %s, %s)",
                (zone_id, tenant_id, camera_id, Jsonb(polygon), severity),
            )
        conn.commit()


def _make_job(tenant_id, camera_id, captured_at=None) -> FrameJob:
    return FrameJob(
        job_id=uuid.uuid4(), tenant_id=tenant_id, camera_id=camera_id,
        frame_jpeg_b64=_b64(SAMPLE_FRAME), frame_width=200, frame_height=100,
        captured_at=captured_at or datetime.now(timezone.utc), ai_modules_enabled=["intrusion"],
    )


def _fetch_one(query, params):
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def test_point_in_polygon_pure_function():
    poly = Polygon([(p["x"], p["y"]) for p in ZONE_POLYGON])
    assert intrusion_task.foot_point((80, 20, 120, 90), 200, 100).within(poly)  # inside
    assert not intrusion_task.foot_point((5, 5, 15, 15), 200, 100).within(poly)  # outside


def test_breach_high_zone_alert_and_incident(tenant_camera_zone, monkeypatch):
    tenant_id, camera_id, zone_id = tenant_camera_zone
    _seed_zone(tenant_id, camera_id, zone_id, "high")
    monkeypatch.setattr(intrusion_task, "detect_persons", lambda frame, conf=0.45: [PERSON_BBOX])

    intrusion_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one(
        "SELECT a.severity, i.is_auto_created FROM intrusion_events ie "
        "JOIN alerts a ON a.detection_id = ie.detection_id "
        "LEFT JOIN incidents i ON i.alert_id = a.id "
        "WHERE ie.tenant_id = %s AND ie.zone_id = %s",
        (tenant_id, zone_id),
    )
    assert row == ("high", True)


def test_breach_low_zone_alert_no_incident(tenant_camera_zone, monkeypatch):
    tenant_id, camera_id, zone_id = tenant_camera_zone
    _seed_zone(tenant_id, camera_id, zone_id, "low")
    monkeypatch.setattr(intrusion_task, "detect_persons", lambda frame, conf=0.45: [PERSON_BBOX])

    intrusion_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one(
        "SELECT a.severity, i.id FROM intrusion_events ie "
        "JOIN alerts a ON a.detection_id = ie.detection_id "
        "LEFT JOIN incidents i ON i.alert_id = a.id "
        "WHERE ie.tenant_id = %s AND ie.zone_id = %s",
        (tenant_id, zone_id),
    )
    assert row[0] == "low"
    assert row[1] is None


def test_person_outside_any_zone_no_detection(tenant_camera_zone, monkeypatch):
    tenant_id, camera_id, zone_id = tenant_camera_zone
    _seed_zone(tenant_id, camera_id, zone_id, "high", polygon=OUTSIDE_POLYGON)  # zone doesn't cover the person
    monkeypatch.setattr(intrusion_task, "detect_persons", lambda frame, conf=0.45: [PERSON_BBOX])

    intrusion_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one("SELECT count(*) FROM detections WHERE tenant_id = %s", (tenant_id,))
    assert row == (0,)


def test_repeat_breach_within_cooldown_suppressed(tenant_camera_zone, monkeypatch):
    tenant_id, camera_id, zone_id = tenant_camera_zone
    _seed_zone(tenant_id, camera_id, zone_id, "high")
    monkeypatch.setenv("INTRUSION_BREACH_COOLDOWN_SECONDS", "60")
    monkeypatch.setattr(intrusion_task, "detect_persons", lambda frame, conf=0.45: [PERSON_BBOX])

    now = datetime.now(timezone.utc)
    intrusion_task.process_frame_job(_make_job(tenant_id, camera_id, captured_at=now))
    intrusion_task.process_frame_job(_make_job(tenant_id, camera_id, captured_at=now + timedelta(seconds=5)))

    row = _fetch_one("SELECT count(*) FROM detections WHERE tenant_id = %s", (tenant_id,))
    assert row == (1,)  # second call suppressed entirely, not just alert-suppressed
