import base64
import os
import uuid
from datetime import datetime, timezone

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL_SYNC",
    "postgresql+psycopg://svc_app:change_me_dev_only@postgres:5432/seventh_ai_vision",
)
os.environ.setdefault("EVIDENCE_ROOT", os.path.join(os.path.dirname(__file__), "_evidence_tmp_face"))

import cv2
import numpy as np
import psycopg
import pytest

from shared.events import FrameJob
from worker.common.tenant_settings_cache import clear_cache
from worker.tasks import face_task

ADMIN_URL = os.environ.get("ADMIN_TEST_DATABASE_URL_SYNC", "postgresql://postgres:change_me_dev_only@postgres:5432/seventh_ai_vision")
SAMPLE_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)

KNOWN_EMBEDDING = [1.0] + [0.0] * 511          # a fixed 512-d unit vector
SIMILAR_EMBEDDING = [0.99] + [0.0] * 511        # nearly identical -> high cosine similarity
DIFFERENT_EMBEDDING = [0.0] * 511 + [1.0]       # orthogonal -> cosine similarity ~0


def _b64(frame) -> str:
    ok, buf = cv2.imencode(".jpg", frame)
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _face(embedding, bbox=(10, 10, 50, 50), det_score=0.95) -> dict:
    return {"embedding": embedding, "bbox": bbox, "det_score": det_score}


@pytest.fixture
def tenant_and_camera():
    clear_cache()
    tenant_id, camera_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, 'T', %s)", (tenant_id, f"t-{tenant_id.hex[:8]}"))
            cur.execute("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) VALUES (%s, %s, 'Cam', '[\"face\"]')", (camera_id, tenant_id))
        conn.commit()
    yield tenant_id, camera_id
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            for table in ("audit_logs", "evidence", "incidents", "alerts", "face_events", "detections", "face_watchlist_entries", "cameras"):
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        conn.commit()


def _vec_str(embedding) -> str:
    return "[" + ",".join(str(v) for v in embedding) + "]"


def _seed_watchlist(tenant_id, person_name, list_type, embedding):
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO face_watchlist_entries (tenant_id, person_name, embedding_v, list_type) "
                "VALUES (%s, %s, %s::vector, %s)",
                (tenant_id, person_name, _vec_str(embedding), list_type),
            )
        conn.commit()


def _make_job(tenant_id, camera_id) -> FrameJob:
    return FrameJob(
        job_id=uuid.uuid4(), tenant_id=tenant_id, camera_id=camera_id,
        frame_jpeg_b64=_b64(SAMPLE_FRAME), frame_width=100, frame_height=100,
        captured_at=datetime.now(timezone.utc), ai_modules_enabled=["face"],
    )


def _fetch_one(query, params):
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def test_cosine_similarity_pure_function():
    assert face_task.cosine_similarity(KNOWN_EMBEDDING, KNOWN_EMBEDDING) == pytest.approx(1.0, abs=1e-4)
    assert face_task.cosine_similarity(KNOWN_EMBEDDING, DIFFERENT_EMBEDDING) == pytest.approx(0.0, abs=1e-4)
    assert face_task.cosine_similarity(KNOWN_EMBEDDING, SIMILAR_EMBEDDING) > 0.9


def test_blocklist_face_high_alert_and_incident(tenant_and_camera, monkeypatch):
    tenant_id, camera_id = tenant_and_camera
    _seed_watchlist(tenant_id, "Known Suspect", "block", KNOWN_EMBEDDING)
    monkeypatch.setattr(face_task, "detect_faces", lambda frame: [_face(SIMILAR_EMBEDDING)])

    face_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one(
        "SELECT a.severity, i.is_auto_created, fe.watchlist_match FROM face_events fe "
        "JOIN alerts a ON a.detection_id = fe.detection_id "
        "LEFT JOIN incidents i ON i.alert_id = a.id "
        "WHERE fe.tenant_id = %s",
        (tenant_id,),
    )
    assert row == ("high", True, "block")


def test_allowlist_vip_low_alert_no_incident(tenant_and_camera, monkeypatch):
    tenant_id, camera_id = tenant_and_camera
    _seed_watchlist(tenant_id, "VIP Person", "allow", KNOWN_EMBEDDING)
    monkeypatch.setattr(face_task, "detect_faces", lambda frame: [_face(SIMILAR_EMBEDDING)])

    face_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one(
        "SELECT a.severity, i.id FROM face_events fe "
        "JOIN alerts a ON a.detection_id = fe.detection_id "
        "LEFT JOIN incidents i ON i.alert_id = a.id "
        "WHERE fe.tenant_id = %s",
        (tenant_id,),
    )
    assert row[0] == "low"
    assert row[1] is None


def test_unrecognized_face_info_alert_no_incident(tenant_and_camera, monkeypatch):
    tenant_id, camera_id = tenant_and_camera
    _seed_watchlist(tenant_id, "Someone Else", "block", KNOWN_EMBEDDING)
    monkeypatch.setattr(face_task, "detect_faces", lambda frame: [_face(DIFFERENT_EMBEDDING)])  # doesn't match

    face_task.process_frame_job(_make_job(tenant_id, camera_id))

    row = _fetch_one(
        "SELECT a.severity, i.id, fe.watchlist_match FROM face_events fe "
        "JOIN alerts a ON a.detection_id = fe.detection_id "
        "LEFT JOIN incidents i ON i.alert_id = a.id "
        "WHERE fe.tenant_id = %s",
        (tenant_id,),
    )
    assert row[0] == "info"
    assert row[1] is None
    assert row[2] is None  # deliberate LPR-vs-face asymmetry: still gets an alert despite no match


# ──────────────────────────────────────────────────────────
# Phase 9: pgvector ANN query shape tests (no DB needed)
# ──────────────────────────────────────────────────────────

def test_embedding_to_vec_str_format():
    result = face_task._embedding_to_vec_str([0.1, 0.2, 0.3])
    assert result == "[0.1,0.2,0.3]"
    assert result.startswith("[") and result.endswith("]")


def test_find_closest_watchlist_match_sql_shape():
    """_find_closest_watchlist_match must use pgvector <=> and ORDER BY LIMIT 1."""
    captured = {}

    class FakeCur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
        def fetchone(self): return None

    class FakeConn:
        def cursor(self): return FakeCur()

    face_task._find_closest_watchlist_match(FakeConn(), "00000000-0000-0000-0000-000000000001", [0.0] * 512, 0.6)

    sql = captured["sql"]
    assert "<=>" in sql, "pgvector cosine distance operator must appear in query"
    assert "ORDER BY" in sql, "results must be ordered by distance for ANN"
    assert "LIMIT 1" in sql, "only the closest match is needed"
    assert "embedding_v" in sql, "must query the new vector column, not the FLOAT4[] one"


def test_find_closest_watchlist_match_below_threshold_returns_none():
    """A row whose score < threshold is treated as no match."""
    import uuid as _uuid

    class FakeCur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a): pass
        def fetchone(self):
            return (_uuid.uuid4(), "Someone", "block", 0.3)  # score 0.3 < threshold 0.6

    class FakeConn:
        def cursor(self): return FakeCur()

    matched, score = face_task._find_closest_watchlist_match(FakeConn(), "t", [0.0] * 512, threshold=0.6)
    assert matched is None
    assert score == pytest.approx(0.3)


def test_find_closest_watchlist_match_above_threshold_returns_row():
    import uuid as _uuid

    wl_id = _uuid.uuid4()

    class FakeCur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a): pass
        def fetchone(self):
            return (wl_id, "Known Person", "block", 0.95)  # above threshold

    class FakeConn:
        def cursor(self): return FakeCur()

    matched, score = face_task._find_closest_watchlist_match(FakeConn(), "t", [0.0] * 512, threshold=0.6)
    assert matched is not None
    assert matched["list_type"] == "block"
    assert matched["person_name"] == "Known Person"
    assert score == pytest.approx(0.95)
