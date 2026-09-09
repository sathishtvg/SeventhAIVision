"""Platform health has to be wrong in the safe direction.

A dashboard that shows green because a check failed to run is worse than no
dashboard, because it is believed. That is not hypothetical here: the first
version of the recording probe counted streams straight from an RLS-protected
table on a session scoped to a sentinel tenant, got zero, and reported

    recording: ok — no streams are set to record continuously

on an installation with three cameras recording. A green light produced by a
query that could not see anything.

So `unknown` is a distinct state, it outranks `ok`, and every probe that cannot
reach its subject returns it.

The second failure mode is the opposite one and just as fatal: a check that
cries wolf is a check nobody reads. A rotating stream has no live recording row
for up to a supervisor interval, so the probe measures coverage over a window
rather than the instant.

Sections:
  A — The verdict is the worst of them (4 tests)
  B — Probes report what they measured (5 tests)
  C — The endpoint (3 tests)
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from app.core.security import create_access_token
from app.services import platform_health

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SUPER_ADMIN, ADMIN = 1, 2


async def _token(role_id: int) -> str:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :slug)"),
            {"id": tenant_id, "n": "Health Test", "slug": f"hlth-{tenant_id.hex[:10]}"},
        )
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                 "VALUES (:id, :tid, :role, :email, 'hashed')"),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"hlth-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return create_access_token(str(user_id), str(tenant_id), role_id)


async def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


# ─── A. The verdict ──────────────────────────────────────────────────────────

def test_unknown_is_never_treated_as_healthy():
    """The whole reason `unknown` exists as a separate state. A probe that
    could not run is not evidence that the thing it watches is working."""
    assert platform_health.worst(["ok", "unknown"]) == "unknown"


def test_the_worst_wins():
    assert platform_health.worst(["ok", "degraded"]) == "degraded"
    assert platform_health.worst(["ok", "unknown", "degraded"]) == "degraded"
    assert platform_health.worst(["degraded", "down"]) == "down"
    assert platform_health.worst(["ok", "ok"]) == "ok"


def test_everything_healthy_is_healthy():
    assert platform_health.worst(["ok"]) == "ok"


def test_nothing_measured_is_not_healthy():
    """An empty list means no probe ran at all, which is the same problem as a
    probe that failed."""
    assert platform_health.worst([]) == "unknown"


# ─── B. The probes ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_database_probe_measures_the_database():
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        result = await platform_health.check_database(s)
    await engine.dispose()
    assert result["service"] == "database"
    assert result["status"] in ("ok", "degraded")
    assert result["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_a_database_that_cannot_be_reached_is_down_not_unknown():
    """Asked and refused is different from not asked. `down` means there was an
    answer and it was a bad one."""
    class Refusing:
        async def execute(self, *a, **k):
            raise RuntimeError("connection refused")

    result = await platform_health.check_database(Refusing())
    assert result["status"] == "down"
    assert "refused" in result["detail"]


@pytest.mark.asyncio
async def test_redis_without_a_url_is_unknown_not_down():
    """Nothing was asked, so nothing may be concluded."""
    saved = os.environ.pop("REDIS_URL", None)
    try:
        result = await platform_health.check_redis()
    finally:
        if saved is not None:
            os.environ["REDIS_URL"] = saved
    assert result["status"] == "unknown"


def test_storage_reports_real_usage():
    results = platform_health.check_storage()
    assert {r["service"] for r in results} == {"storage:recordings", "storage:evidence"}
    for r in results:
        if r["status"] != "unknown":
            assert 0 <= r["used_percent"] <= 100
            assert r["total_gb"] > 0


def test_the_disk_thresholds_are_ordered():
    """Degraded has to come before critical, or a full disk is reported as a
    warning and a warning as an outage."""
    assert platform_health.DISK_DEGRADED_PERCENT < platform_health.DISK_CRITICAL_PERCENT


# ─── C. The endpoint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_tenant_admin_cannot_read_platform_health():
    """Which services the vendor runs, how full its disks are and how far
    behind its workers are is not a customer's business."""
    async with await _client(await _token(ADMIN)) as c:
        assert (await c.get("/api/v1/platform/health")).status_code == 403


@pytest.mark.asyncio
async def test_health_answers_the_platform_owner():
    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.get("/api/v1/platform/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded", "down", "unknown")
    assert len(body["services"]) >= 4
    assert {"critical", "degraded", "unknown"} <= set(body)


@pytest.mark.asyncio
async def test_the_recording_probe_sees_past_row_level_security():
    """The bug this file exists because of.

    streams and cameras are RLS'd and the console runs on a sentinel tenant, so
    counting them directly returns zero. Going through the SECURITY DEFINER
    function is what makes the answer true — and "no streams are set to record"
    must only ever mean there are none, never that the query was blind.
    """
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        # Seed a stream that should be recording and is not, then confirm the
        # probe counts it as expected rather than reporting nothing to do.
        tenant_id, camera_id, stream_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Rec', :slug)"),
            {"id": tenant_id, "slug": f"rec-{tenant_id.hex[:10]}"})
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, name, is_active) "
                 "VALUES (:id, :tid, 'Cam', TRUE)"),
            {"id": camera_id, "tid": tenant_id})
        await s.execute(
            text("INSERT INTO streams (id, tenant_id, camera_id, url, "
                 "                     continuous_recording) "
                 "VALUES (:id, :tid, :cid, 'rtsp://203.0.113.9/x', TRUE)"),
            {"id": stream_id, "tid": tenant_id, "cid": camera_id})
        await s.commit()

        result = await platform_health.check_recording(s)
    await engine.dispose()

    assert result["service"] == "recording"
    assert result["expected"] >= 1, "the probe must see streams it is not scoped to"
    assert result["status"] in ("down", "degraded")
