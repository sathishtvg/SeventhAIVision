import os
import uuid
from datetime import datetime, timezone

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL_SYNC",
    "postgresql+psycopg://svc_app:change_me_dev_only@postgres:5432/seventh_ai_vision",
)
os.environ.setdefault("EVIDENCE_ROOT", os.path.join(os.path.dirname(__file__), "_evidence_tmp"))

import numpy as np
import psycopg
import pytest

from shared.events import FrameJob
from worker.common.tenant_settings_cache import clear_cache
from worker.tasks import lpr_task

ADMIN_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL_SYNC",
    "postgresql://postgres:change_me_dev_only@postgres:5432/seventh_ai_vision",
)

SAMPLE_FRAME = np.zeros((100, 200, 3), dtype=np.uint8)
SAMPLE_BBOX = {"bbox": (10, 10, 90, 50), "confidence": 0.95}


def _b64(frame) -> str:
    import base64

    import cv2

    ok, buf = cv2.imencode(".jpg", frame)
    return base64.b64encode(buf.tobytes()).decode("ascii")


@pytest.fixture
def tenant_and_camera():
    clear_cache()
    tenant_id, camera_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, 'T', %s)", (tenant_id, f"t-{tenant_id.hex[:8]}"))
            cur.execute("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) VALUES (%s, %s, 'Cam', '[\"lpr\"]')", (camera_id, tenant_id))
        conn.commit()
    yield tenant_id, camera_id
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            for table in ("audit_logs", "evidence", "incidents", "alerts", "lpr_events", "detections", "watchlist_entries", "tenant_settings", "users", "cameras"):
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        conn.commit()


def _make_job(tenant_id, camera_id) -> FrameJob:
    return FrameJob(
        job_id=uuid.uuid4(), tenant_id=tenant_id, camera_id=camera_id,
        frame_jpeg_b64=_b64(SAMPLE_FRAME), frame_width=200, frame_height=100,
        captured_at=datetime.now(timezone.utc), ai_modules_enabled=["lpr"],
    )


def _seed_watchlist(tenant_id, plate, list_type):
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO watchlist_entries (tenant_id, plate_number, list_type) VALUES (%s, %s, %s)",
                (tenant_id, plate, list_type),
            )
        conn.commit()


def _fetch_one(query, params):
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def test_blocklist_plate_critical_alert_and_incident(tenant_and_camera, monkeypatch):
    tenant_id, camera_id = tenant_and_camera
    _seed_watchlist(tenant_id, "SGB1234X", "block")
    monkeypatch.setattr(lpr_task, "detect_plates", lambda frame, conf=0.4: [SAMPLE_BBOX])
    monkeypatch.setattr(lpr_task, "run_ocr", lambda crop: ("SGB1234X", 0.9))

    lpr_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one(
        "SELECT a.severity, i.is_auto_created, i.severity, e.id IS NOT NULL, al.action "
        "FROM lpr_events le "
        "JOIN alerts a ON a.detection_id = le.detection_id "
        "LEFT JOIN incidents i ON i.alert_id = a.id "
        "LEFT JOIN evidence e ON e.detection_id::text = le.detection_id::text "
        "LEFT JOIN audit_logs al ON al.resource_id::text = le.detection_id::text "
        "WHERE le.tenant_id = %s AND le.plate_number = 'SGB1234X'",
        (tenant_id,),
    )
    assert row is not None
    severity, is_auto_created, incident_severity, has_evidence, action = row
    assert severity == "critical"
    assert is_auto_created is True
    assert incident_severity == "critical"
    assert has_evidence is True
    assert action == "lpr_detection_processed"


def test_allowlist_plate_low_alert_no_incident(tenant_and_camera, monkeypatch):
    tenant_id, camera_id = tenant_and_camera
    _seed_watchlist(tenant_id, "SGA5678Y", "allow")
    monkeypatch.setattr(lpr_task, "detect_plates", lambda frame, conf=0.4: [SAMPLE_BBOX])
    monkeypatch.setattr(lpr_task, "run_ocr", lambda crop: ("SGA5678Y", 0.9))

    lpr_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one(
        "SELECT a.severity, i.id FROM lpr_events le "
        "JOIN alerts a ON a.detection_id = le.detection_id "
        "LEFT JOIN incidents i ON i.alert_id = a.id "
        "WHERE le.tenant_id = %s AND le.plate_number = 'SGA5678Y'",
        (tenant_id,),
    )
    assert row is not None
    severity, incident_id = row
    assert severity == "low"
    assert incident_id is None


def test_unmatched_plate_no_alert(tenant_and_camera, monkeypatch):
    tenant_id, camera_id = tenant_and_camera
    monkeypatch.setattr(lpr_task, "detect_plates", lambda frame, conf=0.4: [SAMPLE_BBOX])
    monkeypatch.setattr(lpr_task, "run_ocr", lambda crop: ("SGU9999Z", 0.9))

    lpr_task.process_frame_job(_make_job(tenant_id, camera_id))

    lpr_row = _fetch_one(
        "SELECT watchlist_match FROM lpr_events WHERE tenant_id = %s AND plate_number = 'SGU9999Z'", (tenant_id,)
    )
    assert lpr_row == (None,)
    alert_row = _fetch_one(
        "SELECT count(*) FROM alerts WHERE tenant_id = %s AND title LIKE '%%SGU9999Z%%'", (tenant_id,)
    )
    assert alert_row == (0,)


def test_below_threshold_writes_nothing(tenant_and_camera, monkeypatch):
    tenant_id, camera_id = tenant_and_camera
    monkeypatch.setenv("LPR_CONFIDENCE_THRESHOLD", "0.55")
    monkeypatch.setattr(lpr_task, "detect_plates", lambda frame, conf=0.4: [SAMPLE_BBOX])
    monkeypatch.setattr(lpr_task, "run_ocr", lambda crop: ("SGLOW111", 0.10))

    lpr_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one("SELECT count(*) FROM detections WHERE tenant_id = %s", (tenant_id,))
    assert row == (0,)


def test_tenant_setting_overrides_env_default(tenant_and_camera, monkeypatch):
    tenant_id, camera_id = tenant_and_camera
    monkeypatch.setenv("LPR_CONFIDENCE_THRESHOLD", "0.20")  # env default: would pass
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) VALUES (%s, %s, 1, %s, 'x')",
                (uuid.uuid4(), tenant_id, f"{tenant_id}@example.com"),
            )
            cur.execute("SELECT id FROM users WHERE tenant_id = %s", (tenant_id,))
            user_id = cur.fetchone()[0]
            from psycopg.types.json import Jsonb

            cur.execute(
                "INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
                "VALUES (%s, 'lpr.confidence_threshold', %s, %s)",
                (tenant_id, Jsonb(0.99), user_id),  # tenant override: stricter, would fail
            )
        conn.commit()

    monkeypatch.setattr(lpr_task, "detect_plates", lambda frame, conf=0.4: [SAMPLE_BBOX])
    monkeypatch.setattr(lpr_task, "run_ocr", lambda crop: ("SGOVR222", 0.5))

    lpr_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one("SELECT count(*) FROM detections WHERE tenant_id = %s", (tenant_id,))
    assert row == (0,)  # tenant's stricter 0.99 threshold won, not the env's lenient 0.20
