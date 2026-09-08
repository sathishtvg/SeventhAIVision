"""Man-down detection and escalation (isolated-tenant).

Tests backend/app/routers/mandown.py and backend/app/services/mandown.py.

Key semantics:
  - Ships disabled; the thresholds are tenant settings with shipped defaults
  - A second trigger while one is live is the SAME incident, enforced by a
    partial unique index rather than by the API remembering
  - Only the guard themselves can cancel — a supervisor deciding somebody is
    fine without asking is exactly backwards
  - Escalation goes through the existing SOS service, so a man-down produces
    the occurrence-book entry a licensed agency has to keep
  - The server sweep escalates without the phone, which is the case man-down
    exists for; escalated_by_server records which happened
  - Escalation is idempotent: the phone and the sweep racing cannot raise two
    alerts for one guard

Sections:
  A — Settings (4 tests)
  B — Raising and cancelling (6 tests)
  C — Escalation (5 tests)
  D — Acknowledge and resolve (4 tests)
  E — The server sweep (3 tests)
  F — Permissions and RLS (3 tests)
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

# Module level, not lazy — see test_guardhouse_registers.py for why.
from app.main import app as _fastapi_app  # noqa: E402


def _app():
    return _fastapi_app


async def _exec(statements: list[tuple[str, dict]]):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            for sql, params in statements:
                await s.execute(text(sql), params)
            await s.commit()
    finally:
        await engine.dispose()


async def _seed_tenant(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    slug = f"md-{tenant_id.hex[:10]}"
    await _exec([
        ("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)",
         {"id": tenant_id, "name": f"ManDown Test {slug}", "slug": slug}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:id, :tid, :role, :email, 'hashed', 'MD Tester')",
         {"id": user_id, "tid": tenant_id, "role": role_id,
          "email": f"md-{user_id.hex[:8]}@test.local"}),
    ])
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_user(tenant_id, role_id: int, name: str = "MD Guard"):
    from app.core.security import create_access_token

    user_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:id, :tid, :role, :email, 'hashed', :name)",
         {"id": user_id, "tid": tenant_id, "role": role_id, "name": name,
          "email": f"md-{user_id.hex[:8]}@test.local"}),
    ])
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id, name: str = "MD Site") -> uuid.UUID:
    site_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)",
         {"id": site_id, "tid": tenant_id, "name": name}),
    ])
    return site_id


async def _authed(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _raise(c: AsyncClient, trigger="no_motion", **extra) -> dict:
    r = await c.post("/api/v1/man-down", json={"trigger": trigger, **extra})
    assert r.status_code == 201, f"raise failed: {r.text}"
    return r.json()


async def _tenant_session(tenant_id):
    """An admin session with the tenant GUC set, for driving the sweep directly."""
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    return session, engine


# ─── A. Settings ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ships_disabled_with_shipped_defaults():
    """Continuous sensor monitoring on every guard's phone is the company's
    decision, not an upgrade's."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        body = (await c.get("/api/v1/man-down/settings")).json()
        assert body["enabled"] is False
        assert body["no_motion_seconds"] == 120
        assert body["countdown_seconds"] == 30


@pytest.mark.asyncio
async def test_thresholds_can_be_overridden_per_tenant():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/man-down/settings",
                        json={"enabled": True, "no_motion_seconds": 300})
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        assert r.json()["no_motion_seconds"] == 300
        # And the countdown that was not touched keeps its default.
        assert r.json()["countdown_seconds"] == 30


@pytest.mark.asyncio
async def test_absurd_thresholds_are_refused():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        assert (await c.put("/api/v1/man-down/settings",
                            json={"countdown_seconds": 3})).status_code == 422
        assert (await c.put("/api/v1/man-down/settings",
                            json={"no_motion_seconds": 5})).status_code == 422
        assert (await c.put("/api/v1/man-down/settings",
                            json={"no_motion_secs": 60})).status_code == 422


@pytest.mark.asyncio
async def test_the_countdown_shown_is_the_countdown_stored():
    """The setting can change while an event is live, and the guard was shown a
    specific number of seconds."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        await c.put("/api/v1/man-down/settings", json={"countdown_seconds": 60})
        event = await _raise(c)
        assert event["countdown_seconds"] == 60

        await c.put("/api/v1/man-down/settings", json={"countdown_seconds": 300})
        body = (await c.get(f"/api/v1/man-down/{event['id']}")).json()
        # Still counting to the original deadline, not the new one.
        assert body["seconds_remaining"] <= 60


# ─── B. Raising and cancelling ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_raising_records_where_and_how_much_battery():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        event = await _raise(c, "impact", site_id=str(site_id),
                             latitude=1.2834, longitude=103.8607, battery_level=8,
                             device_info="Pixel 7a")
        body = (await c.get(f"/api/v1/man-down/{event['id']}")).json()
        assert body["trigger"] == "impact"
        assert body["battery_level"] == 8
        assert body["latitude"] == pytest.approx(1.2834)
        assert body["site_name"] == "MD Site"


@pytest.mark.asyncio
async def test_a_second_trigger_is_the_same_incident():
    """Two rows would mean two escalations for one guard on one floor."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        first = await _raise(c, "no_motion")
        r = await c.post("/api/v1/man-down", json={"trigger": "impact"})
        assert r.status_code == 201
        assert r.json()["already_live"] is True
        assert r.json()["id"] == first["id"]


@pytest.mark.asyncio
async def test_unknown_trigger_and_unknown_field_are_rejected():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        assert (await c.post("/api/v1/man-down",
                             json={"trigger": "levitating"})).status_code == 422
        assert (await c.post("/api/v1/man-down",
                             json={"trigger": "no_motion", "pulse": 40})).status_code == 422


@pytest.mark.asyncio
async def test_only_the_guard_can_cancel_their_own_man_down():
    """A supervisor deciding a guard is fine without asking is exactly
    backwards."""
    tenant_id, _, admin_token = await _seed_tenant()
    _, guard_token = await _seed_user(tenant_id, 5)
    async with await _authed(guard_token) as guard:
        event = await _raise(guard)
    async with await _authed(admin_token) as admin:
        r = await admin.post(f"/api/v1/man-down/{event['id']}/cancel", json={})
        assert r.status_code == 403
    async with await _authed(guard_token) as guard:
        r = await guard.post(f"/api/v1/man-down/{event['id']}/cancel",
                             json={"notes": "Phone was on the desk"})
        assert r.status_code == 200 and r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancellations_are_kept_not_deleted():
    """A guard cancelling six times a shift means the thresholds are wrong for
    how they work, and that is only visible if the cancellations survive."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        await c.post(f"/api/v1/man-down/{event['id']}/cancel", json={})
        rows = (await c.get("/api/v1/man-down")).json()
        assert [r["status"] for r in rows] == ["cancelled"]


@pytest.mark.asyncio
async def test_cancelling_frees_the_guard_to_trigger_again():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        first = await _raise(c)
        await c.post(f"/api/v1/man-down/{first['id']}/cancel", json={})
        second = await _raise(c)
        assert second["id"] != first["id"]
        assert second["already_live"] is False


# ─── C. Escalation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalation_raises_a_real_panic_alert():
    """A man-down IS a panic alert, just one nobody had a free hand to press —
    so it goes through the same service and leaves the same occurrence-book
    entry a licensed agency has to keep."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        event = await _raise(c, "impact", site_id=str(site_id), battery_level=4)
        r = await c.post(f"/api/v1/man-down/{event['id']}/escalate")
        assert r.status_code == 200
        assert r.json()["escalated"] is True
        assert r.json()["sos"]["entry_id"] is not None
        # The message a supervisor reads has to say enough to act on.
        message = r.json()["sos"]["message"]
        assert "MAN DOWN" in message and "battery 4%" in message


@pytest.mark.asyncio
async def test_escalation_is_idempotent():
    """The phone and the sweep racing each other cannot raise two alerts."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        first = await c.post(f"/api/v1/man-down/{event['id']}/escalate")
        second = await c.post(f"/api/v1/man-down/{event['id']}/escalate")
        assert first.json()["escalated"] is True
        assert second.json()["escalated"] is False


@pytest.mark.asyncio
async def test_the_phone_escalating_is_recorded_as_the_phone():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        await c.post(f"/api/v1/man-down/{event['id']}/escalate")
        body = (await c.get(f"/api/v1/man-down/{event['id']}")).json()
        assert body["status"] == "escalated"
        assert body["escalated_by_server"] is False


@pytest.mark.asyncio
async def test_an_escalated_event_cannot_be_cancelled():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        await c.post(f"/api/v1/man-down/{event['id']}/escalate")
        r = await c.post(f"/api/v1/man-down/{event['id']}/cancel", json={})
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_a_tenant_with_no_cameras_still_gets_the_alert():
    """Guarding-only customers are a large part of who this is sold to, and
    incidents require a camera. The occurrence-book entry does not."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        r = await c.post(f"/api/v1/man-down/{event['id']}/escalate")
        assert r.json()["escalated"] is True
        assert r.json()["sos"]["entry_id"] is not None
        assert r.json()["sos"]["incident_id"] is None


# ─── D. Acknowledge and resolve ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_acknowledging_measures_the_response_time():
    """"Somebody is on their way" and "we know what happened" are different
    facts, and the gap between them is what this feature is judged on."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        await c.post(f"/api/v1/man-down/{event['id']}/escalate")
        r = await c.post(f"/api/v1/man-down/{event['id']}/acknowledge")
        assert r.status_code == 200
        assert r.json()["status"] == "acknowledged"
        assert r.json()["seconds_to_acknowledge"] is not None


@pytest.mark.asyncio
async def test_a_pending_event_cannot_be_acknowledged():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        r = await c.post(f"/api/v1/man-down/{event['id']}/acknowledge")
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_resolving_requires_an_outcome():
    """The false-alarm rate is the number that says whether the thresholds are
    right, and it is unmeasurable if "resolved" carries no outcome."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        await c.post(f"/api/v1/man-down/{event['id']}/escalate")

        assert (await c.post(f"/api/v1/man-down/{event['id']}/resolve",
                             json={})).status_code == 422
        assert (await c.post(f"/api/v1/man-down/{event['id']}/resolve",
                             json={"outcome": "vanished"})).status_code == 422

        r = await c.post(f"/api/v1/man-down/{event['id']}/resolve",
                         json={"outcome": "false_alarm", "notes": "Phone on a shelf"})
        assert r.status_code == 200 and r.json()["outcome"] == "false_alarm"


@pytest.mark.asyncio
async def test_resolving_twice_conflicts():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        await c.post(f"/api/v1/man-down/{event['id']}/escalate")
        await c.post(f"/api/v1/man-down/{event['id']}/resolve",
                     json={"outcome": "guard_ok"})
        r = await c.post(f"/api/v1/man-down/{event['id']}/resolve",
                         json={"outcome": "guard_ok"})
        assert r.status_code == 409


# ─── E. The server sweep ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_server_escalates_when_the_phone_never_calls_back():
    """The whole reason the deadline is stored. A handset that shattered on
    impact is exactly the case man-down exists for."""
    from app.services.mandown import sweep_pending

    tenant_id, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c, "impact")

    await _exec([("UPDATE man_down_events SET escalate_at = now() - interval '1 minute' "
                  "WHERE id = :id", {"id": uuid.UUID(event["id"])})])

    session, engine = await _tenant_session(tenant_id)
    try:
        escalated = await sweep_pending(session, None)
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()
    assert escalated == 1

    async with await _authed(token) as c:
        body = (await c.get(f"/api/v1/man-down/{event['id']}")).json()
        assert body["status"] == "escalated"
        assert body["escalated_by_server"] is True


@pytest.mark.asyncio
async def test_the_sweep_leaves_events_still_counting_down_alone():
    from app.services.mandown import sweep_pending

    tenant_id, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)

    session, engine = await _tenant_session(tenant_id)
    try:
        assert await sweep_pending(session, None) == 0
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    async with await _authed(token) as c:
        assert (await c.get(f"/api/v1/man-down/{event['id']}")).json()["status"] == "pending"


@pytest.mark.asyncio
async def test_the_sweep_does_not_re_escalate_what_the_phone_already_handled():
    from app.services.mandown import sweep_pending

    tenant_id, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        event = await _raise(c)
        await c.post(f"/api/v1/man-down/{event['id']}/escalate")

    await _exec([("UPDATE man_down_events SET escalate_at = now() - interval '1 minute' "
                  "WHERE id = :id", {"id": uuid.UUID(event["id"])})])

    session, engine = await _tenant_session(tenant_id)
    try:
        assert await sweep_pending(session, None) == 0
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    async with await _authed(token) as c:
        body = (await c.get(f"/api/v1/man-down/{event['id']}")).json()
        assert body["escalated_by_server"] is False


# ─── F. Permissions and RLS ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_guard_reports_but_does_not_configure_or_close():
    tenant_id, _, _ = await _seed_tenant()
    _, guard_token = await _seed_user(tenant_id, 5)
    async with await _authed(guard_token) as guard:
        event = await _raise(guard)
        assert (await guard.get("/api/v1/man-down/settings")).status_code == 200
        assert (await guard.put("/api/v1/man-down/settings",
                                json={"enabled": True})).status_code == 403
        await guard.post(f"/api/v1/man-down/{event['id']}/escalate")
        assert (await guard.post(
            f"/api/v1/man-down/{event['id']}/acknowledge")).status_code == 403
        assert (await guard.post(f"/api/v1/man-down/{event['id']}/resolve",
                                 json={"outcome": "guard_ok"})).status_code == 403


@pytest.mark.asyncio
async def test_a_viewer_reads_and_reports_nothing():
    tenant_id, _, _ = await _seed_tenant()
    _, viewer_token = await _seed_user(tenant_id, 6, "Control Room")
    async with await _authed(viewer_token) as viewer:
        assert (await viewer.get("/api/v1/man-down")).status_code == 200
        assert (await viewer.post("/api/v1/man-down",
                                  json={"trigger": "no_motion"})).status_code == 403


@pytest.mark.asyncio
async def test_another_tenant_sees_nothing():
    _, _, token_a = await _seed_tenant()
    _, _, token_b = await _seed_tenant()

    async with await _authed(token_a) as a:
        event = await _raise(a)

    async with await _authed(token_b) as b:
        assert (await b.get(f"/api/v1/man-down/{event['id']}")).status_code == 404
        assert (await b.get("/api/v1/man-down")).json() == []
        # And their own settings are untouched by A's.
        assert (await b.get("/api/v1/man-down/settings")).json()["enabled"] is False
