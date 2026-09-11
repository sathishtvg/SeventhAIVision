"""The API's two audiences, and the validation a browser cannot be trusted with.

A duty officer handed a patrol must not acquire the ability to rewrite the
questions they are about to be asked. And a patrol is evidence, so every rule the
UI enforces is enforced again here, because anybody with curl can skip a browser.

Sections:
  A — Configuration needs manage, not execute (3 tests)
  B — A schedule cannot be made nonsensical (3 tests)
  C — Cameras belong to the site they are patrolling (2 tests)
  D — Execution is scoped to the assigned officer (2 tests)
  E — The list endpoints the pages open with (4 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from app.core.security import create_access_token

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

ADMIN_ROLE, GUARD_ROLE = 2, 5
BASE = "/api/v1/virtual-patrol"


async def _sql(stmt: str, params: dict | None = None):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            r = await s.execute(text(stmt), params or {})
            rows = r.all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


async def _world():
    """A tenant with two sites, a camera on each, an admin and a guard."""
    ids = {k: uuid.uuid4() for k in
           ("tenant", "site", "other_site", "cam", "other_cam", "admin", "guard")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'VP API',:s)",
               {"t": ids["tenant"], "s": f"vpapi-{ids['tenant'].hex[:10]}"})
    for key, name in (("site", "Main Site"), ("other_site", "Other Site")):
        await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,:n)",
                   {"i": ids[key], "t": ids["tenant"], "n": name})
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
               "VALUES (:i,:t,:s,'Main Gate')",
               {"i": ids["cam"], "t": ids["tenant"], "s": ids["site"]})
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
               "VALUES (:i,:t,:s,'Elsewhere')",
               {"i": ids["other_cam"], "t": ids["tenant"], "s": ids["other_site"]})
    for key, role in (("admin", ADMIN_ROLE), ("guard", GUARD_ROLE)):
        await _sql(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
            "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
            {"i": ids[key], "t": ids["tenant"], "r": role,
             "e": f"{key}-{ids[key].hex[:8]}@vp.test", "n": key.title()})
    ids["admin_h"] = {
        "Authorization":
            f"Bearer {create_access_token(str(ids['admin']), str(ids['tenant']), ADMIN_ROLE)}"}
    ids["guard_h"] = {
        "Authorization":
            f"Bearer {create_access_token(str(ids['guard']), str(ids['tenant']), GUARD_ROLE)}"}
    return ids


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app), base_url="http://test")


def _schedule_body(site_id, **over):
    body = {"site_id": str(site_id), "name": "Morning Patrol",
            "schedule_type": "DAILY", "start_date": "2026-01-01",
            "patrol_time": "07:00:00"}
    body.update(over)
    return body


# ─── A. Configuration needs manage ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_admin_can_create_a_schedule():
    w = await _world()
    async with _client() as c:
        r = await c.post(f"{BASE}/schedules", json=_schedule_body(w["site"]),
                         headers=w["admin_h"])
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Morning Patrol"


@pytest.mark.asyncio
async def test_a_guard_cannot_create_a_schedule():
    """Section 28. Being handed a patrol must not confer the power to rewrite
    the questions you are about to be asked."""
    w = await _world()
    async with _client() as c:
        r = await c.post(f"{BASE}/schedules", json=_schedule_body(w["site"]),
                         headers=w["guard_h"])
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_a_guard_can_still_see_their_own_patrol_list():
    """Execute is a real permission, not a token one — the officer has to be
    able to find the patrol they are meant to carry out."""
    w = await _world()
    async with _client() as c:
        r = await c.get(f"{BASE}/my-patrols", headers=w["guard_h"])
    assert r.status_code == 200, r.text


# ─── B. A schedule cannot be made nonsensical ────────────────────────────────

@pytest.mark.asyncio
async def test_a_weekly_schedule_without_a_weekday_is_refused_with_a_sentence():
    """The database refuses this too. The API refuses it first so the supervisor
    reads an explanation rather than a constraint name."""
    w = await _world()
    async with _client() as c:
        r = await c.post(f"{BASE}/schedules",
                         json=_schedule_body(w["site"], schedule_type="WEEKLY",
                                             weekdays=[]),
                         headers=w["admin_h"])
    assert r.status_code == 422
    assert "weekday" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_an_unknown_schedule_type_is_refused():
    w = await _world()
    async with _client() as c:
        r = await c.post(f"{BASE}/schedules",
                         json=_schedule_body(w["site"], schedule_type="HOURLY"),
                         headers=w["admin_h"])
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_a_choice_question_without_options_is_refused():
    """Otherwise the officer meets it mid-patrol, at the camera, with nothing
    to choose."""
    w = await _world()
    async with _client() as c:
        sched = (await c.post(f"{BASE}/schedules", json=_schedule_body(w["site"]),
                              headers=w["admin_h"])).json()
        cam = (await c.post(f"{BASE}/schedules/{sched['id']}/cameras",
                            json={"camera_id": str(w["cam"])},
                            headers=w["admin_h"])).json()
        r = await c.post(f"{BASE}/schedule-cameras/{cam['id']}/questions",
                         json={"question_text": "Pick one",
                               "question_type": "SINGLE_CHOICE"},
                         headers=w["admin_h"])
    assert r.status_code == 422
    assert "option" in r.json()["detail"].lower()


# ─── C. Cameras belong to the site ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_camera_from_another_site_cannot_be_added():
    """RLS stops cross-TENANT access. This is the cross-SITE case inside one
    tenant, which RLS cannot see — and an officer sent to inspect a camera at a
    site they are not standing in has been sent on a pointless errand."""
    w = await _world()
    async with _client() as c:
        sched = (await c.post(f"{BASE}/schedules", json=_schedule_body(w["site"]),
                              headers=w["admin_h"])).json()
        r = await c.post(f"{BASE}/schedules/{sched['id']}/cameras",
                         json={"camera_id": str(w["other_cam"])},
                         headers=w["admin_h"])
    assert r.status_code == 422
    assert "different site" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_the_same_camera_cannot_be_added_twice():
    w = await _world()
    async with _client() as c:
        sched = (await c.post(f"{BASE}/schedules", json=_schedule_body(w["site"]),
                              headers=w["admin_h"])).json()
        first = await c.post(f"{BASE}/schedules/{sched['id']}/cameras",
                             json={"camera_id": str(w["cam"])}, headers=w["admin_h"])
        second = await c.post(f"{BASE}/schedules/{sched['id']}/cameras",
                              json={"camera_id": str(w["cam"])}, headers=w["admin_h"])
    assert first.status_code == 201
    assert second.status_code == 409


# ─── D. Execution is scoped to the assigned officer ──────────────────────────

async def _session_for(tenant, site, officer=None):
    sess = uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, patrol_number, schedule_name, scheduled_for, "
        "   officer_user_id) "
        "VALUES (:i,:t,:s,'VP-X','Morning Patrol',:w,CAST(:o AS uuid))",
        {"i": sess, "t": tenant, "s": site,
         "w": datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc),
         "o": str(officer) if officer else None})
    return sess


@pytest.mark.asyncio
async def test_an_officer_cannot_start_a_patrol_assigned_to_someone_else():
    """Holding vpatrol:execute is permission to run YOUR patrol. Without this
    every guard could answer on behalf of every other, and the report would
    carry the wrong name against the evidence."""
    w = await _world()
    other = uuid.uuid4()
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
               "VALUES (:i,:t,CAST(5 AS smallint),:e,'x')",
               {"i": other, "t": w["tenant"], "e": f"other-{other.hex[:8]}@vp.test"})
    sess = await _session_for(w["tenant"], w["site"], officer=other)
    async with _client() as c:
        r = await c.post(f"{BASE}/sessions/{sess}/start", headers=w["guard_h"])
    assert r.status_code == 403
    assert "another officer" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_the_assigned_officer_can_start_their_patrol():
    w = await _world()
    sess = await _session_for(w["tenant"], w["site"], officer=w["guard"])
    async with _client() as c:
        r = await c.post(f"{BASE}/sessions/{sess}/start", headers=w["guard_h"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "IN_PROGRESS"


# ─── E. The list endpoints the pages open with ───────────────────────────────
#
# These were missing, and their absence cost a 500 on the admin page's very
# first request. The POST tests above passed all along: creating a schedule
# worked, listing them did not, and nothing exercised the difference.
#
# The cause was an optional filter used once bare and once cast --
# ":site IS NULL OR site_id = CAST(:site AS uuid)" -- which Postgres cannot
# assign a single type, answering AmbiguousParameterError. It fails whether or
# not the filter is supplied, so the page was broken for everyone.


@pytest.mark.asyncio
async def test_listing_schedules_works_without_a_filter():
    """What the admin page calls the moment it opens."""
    w = await _world()
    async with _client() as c:
        await c.post(f"{BASE}/schedules", json=_schedule_body(w["site"]),
                     headers=w["admin_h"])
        r = await c.get(f"{BASE}/schedules", headers=w["admin_h"])
    assert r.status_code == 200, r.text
    assert any(s["name"] == "Morning Patrol" for s in r.json())


@pytest.mark.asyncio
async def test_listing_schedules_works_with_every_optional_filter():
    """Supplied and omitted are different SQL paths for the same parameter, and
    the type-inference failure hits both."""
    w = await _world()
    async with _client() as c:
        await c.post(f"{BASE}/schedules", json=_schedule_body(w["site"]),
                     headers=w["admin_h"])
        by_site = await c.get(f"{BASE}/schedules", params={"site_id": str(w["site"])},
                              headers=w["admin_h"])
        by_enabled = await c.get(f"{BASE}/schedules", params={"enabled": "true"},
                                 headers=w["admin_h"])
        both = await c.get(f"{BASE}/schedules",
                           params={"site_id": str(w["site"]), "enabled": "true"},
                           headers=w["admin_h"])
    assert by_site.status_code == 200, by_site.text
    assert by_enabled.status_code == 200, by_enabled.text
    assert both.status_code == 200, both.text


@pytest.mark.asyncio
async def test_listing_sessions_works_without_a_filter():
    """What the history tab calls."""
    w = await _world()
    await _session_for(w["tenant"], w["site"], officer=w["guard"])
    async with _client() as c:
        r = await c.get(f"{BASE}/sessions", headers=w["admin_h"])
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_listing_sessions_works_with_a_status_filter():
    w = await _world()
    await _session_for(w["tenant"], w["site"], officer=w["guard"])
    async with _client() as c:
        r = await c.get(f"{BASE}/sessions", params={"status": "SCHEDULED"},
                        headers=w["admin_h"])
    assert r.status_code == 200, r.text
