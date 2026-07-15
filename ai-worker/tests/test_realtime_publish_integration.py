"""Cross-cutting check: the AI worker's publish_alert_created (worker side,
plan §9) produces a message on the real Redis channel in the exact shape
backend's redis_pubsub_listener expects (verified independently in
backend/tests/test_realtime.py) — confirms both halves of the realtime push
feature actually agree on the wire format, not just each half's own test."""

import base64
import json
import os
import uuid
from datetime import datetime, timezone

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL_SYNC",
    "postgresql+psycopg://svc_app:change_me_dev_only@postgres:5432/seventh_ai_vision",
)
os.environ.setdefault("EVIDENCE_ROOT", os.path.join(os.path.dirname(__file__), "_evidence_tmp_realtime"))
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")  # same DB index backend listens on

import cv2
import numpy as np
import psycopg
import redis

from shared.constants import tenant_events_channel
from shared.events import FrameJob
from worker.common.tenant_settings_cache import clear_cache
from worker.tasks import lpr_task

ADMIN_URL = "postgresql://postgres:change_me_dev_only@postgres:5432/seventh_ai_vision"
SAMPLE_FRAME = np.zeros((100, 200, 3), dtype=np.uint8)
SAMPLE_BBOX = {"bbox": (10, 10, 90, 50), "confidence": 0.95}


def _b64(frame) -> str:
    ok, buf = cv2.imencode(".jpg", frame)
    return base64.b64encode(buf.tobytes()).decode("ascii")


def test_lpr_blocklist_hit_publishes_well_formed_event(monkeypatch):
    clear_cache()
    tenant_id, camera_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, 'T', %s)", (tenant_id, f"t-{tenant_id.hex[:8]}"))
            cur.execute("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) VALUES (%s, %s, 'Cam', '[\"lpr\"]')", (camera_id, tenant_id))
            cur.execute("INSERT INTO watchlist_entries (tenant_id, plate_number, list_type) VALUES (%s, 'SGB1234A', 'block')", (tenant_id,))
        conn.commit()

    r = redis.from_url(os.environ["REDIS_URL"])
    pubsub = r.pubsub()
    pubsub.subscribe(tenant_events_channel(str(tenant_id)))
    pubsub.get_message(timeout=1)  # discard the subscribe confirmation message

    try:
        monkeypatch.setattr(lpr_task, "detect_plates", lambda frame, conf=0.4: [SAMPLE_BBOX])
        monkeypatch.setattr(lpr_task, "run_ocr", lambda crop: ("SGB1234A", 0.9))

        job = FrameJob(
            job_id=uuid.uuid4(), tenant_id=tenant_id, camera_id=camera_id,
            frame_jpeg_b64=_b64(SAMPLE_FRAME), frame_width=200, frame_height=100,
            captured_at=datetime.now(timezone.utc), ai_modules_enabled=["lpr"],
        )
        lpr_task.process_frame_job(job)

        message = pubsub.get_message(timeout=3)
        while message is not None and message["type"] != "message":
            message = pubsub.get_message(timeout=3)
        assert message is not None, "expected a published realtime event, got none"

        data = json.loads(message["data"])
        assert data["schema_version"] == 1
        assert data["event_type"] == "alert_created"
        assert data["tenant_id"] == str(tenant_id)
        assert data["payload"]["severity"] == "critical"
        assert data["payload"]["module_type"] == "lpr"
        assert "alert_id" in data["payload"]
        assert "occurred_at" in data

        # A second message should be the incident_created push (block hits auto-create one).
        incident_message = pubsub.get_message(timeout=3)
        while incident_message is not None and incident_message["type"] != "message":
            incident_message = pubsub.get_message(timeout=3)
        assert incident_message is not None
        incident_data = json.loads(incident_message["data"])
        assert incident_data["event_type"] == "incident_created"
    finally:
        pubsub.close()
        r.close()
        with psycopg.connect(ADMIN_URL) as conn:
            with conn.cursor() as cur:
                for table in ("audit_logs", "evidence", "incidents", "alerts", "lpr_events", "detections", "watchlist_entries", "cameras"):
                    cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
                cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
            conn.commit()
