"""Virtual patrol on the Command Centre board, and who is allowed to see it.

The site-scoping test is the one that matters. A patrol exception names a
camera, a time, and what was wrong with it — precisely the detail site
restrictions exist to contain — so a supervisor limited to two sites must not
read the findings of a third.

Sections:
  A — The panel is gated on the module's own permission (2 tests)
  B — What the board shows (3 tests)
  C — Site scoping (2 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

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

ADMIN_ROLE, VIEWER_ROLE, SUPERVISOR_ROLE = 2, 6, 3
# Roles 1 and 2 are unrestricted by site BY DESIGN
# (dependencies/sites.py::_UNRESTRICTED_ROLES), so an Admin is the wrong
# subject for a scoping test — it would pass or fail for reasons that have
# nothing to do with this endpoint. Supervisor is who actually gets
# restricted in practice, and holds vpatrol:read.
URL = "/api/v1/command-centre/virtual-patrol"


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


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app), base_url="http://test")


async def _world():
    """A tenant with two sites, an admin, and a patrol on each site."""
    ids = {k: uuid.uuid4() for k in
           ("tenant", "site_a", "site_b", "cam_a", "cam_b", "admin", "viewer",
            "supervisor", "sess_a", "sess_b")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'CC Co',:s)",
               {"t": ids["tenant"], "s": f"vpcc-{ids['tenant'].hex[:10]}"})
    for key, name in (("site_a", "Site A"), ("site_b", "Site B")):
        await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,:n)",
                   {"i": ids[key], "t": ids["tenant"], "n": name})
    for cam, site in (("cam_a", "site_a"), ("cam_b", "site_b")):
        await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
                   "VALUES (:i,:t,:s,'Gate')",
                   {"i": ids[cam], "t": ids["tenant"], "s": ids[site]})
    for key, role in (("admin", ADMIN_ROLE), ("viewer", VIEWER_ROLE),
                      ("supervisor", SUPERVISOR_ROLE)):
        await _sql(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
            "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
            {"i": ids[key], "t": ids["tenant"], "r": role,
             "e": f"{key}-{ids[key].hex[:8]}@cc.test", "n": key.title()})

    for sess, site, status in (("sess_a", "site_a", "IN_PROGRESS"),
                               ("sess_b", "site_b", "IN_PROGRESS")):
        await _sql(
            "INSERT INTO virtual_patrol_sessions "
            "  (id, tenant_id, site_id, patrol_number, schedule_name, scheduled_for, "
            "   status, camera_count, completed_camera_count) "
            "VALUES (:i,:t,:s,:n,'Morning Patrol',:w,:st,3,1)",
            {"i": ids[sess], "t": ids["tenant"], "s": ids[site],
             "n": f"VP-{sess}", "w": datetime.now(timezone.utc) - timedelta(minutes=30),
             "st": status})

    ids["admin_h"] = {"Authorization":
                      f"Bearer {create_access_token(str(ids['admin']), str(ids['tenant']), ADMIN_ROLE)}"}
    ids["viewer_h"] = {"Authorization":
                       f"Bearer {create_access_token(str(ids['viewer']), str(ids['tenant']), VIEWER_ROLE)}"}
    ids["supervisor_h"] = {"Authorization":
                           f"Bearer {create_access_token(str(ids['supervisor']), str(ids['tenant']), SUPERVISOR_ROLE)}"}
    return ids


async def _add_exception(ids, site_key, camera_key, session_key, answer="NO"):
    """A completed camera with one failed check on the given site."""
    cam_row, q, a = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_session_cameras "
        "  (id, tenant_id, session_id, camera_id, sequence_no, camera_name, "
        "   snapshot_path, status) "
        "VALUES (:i,:t,:se,:c,1,'Gate','some/path.jpg','COMPLETED')",
        {"i": cam_row, "t": ids["tenant"], "se": ids[session_key], "c": ids[camera_key]})
    await _sql(
        "INSERT INTO virtual_patrol_session_questions "
        "  (id, tenant_id, session_camera_id, question_text, question_type, "
        "   is_required, sequence_no) "
        "VALUES (:i,:t,:c,'Is the gate secure?','YES_NO',TRUE,1)",
        {"i": q, "t": ids["tenant"], "c": cam_row})
    await _sql(
        "INSERT INTO virtual_patrol_session_answers "
        "  (id, tenant_id, session_question_id, answer_text, is_exception, answered_at) "
        "VALUES (:i,:t,:q,:a,TRUE, now())",
        {"i": a, "t": ids["tenant"], "q": q, "a": answer})


# ─── A. Gated on the module's own permission ─────────────────────────────────

@pytest.mark.asyncio
async def test_a_user_with_the_permission_gets_the_panel():
    w = await _world()
    async with _client() as c:
        r = await c.get(URL, headers=w["admin_h"])
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"summary", "in_progress", "exceptions"}


@pytest.mark.asyncio
async def test_a_user_without_it_is_refused_rather_than_shown_nothing():
    """A clean 403 beats an empty panel: a tenant without the module should not
    be left wondering whether their patrols have vanished."""
    w = await _world()
    async with _client() as c:
        r = await c.get(URL, headers=w["viewer_h"])
    assert r.status_code == 403, r.text


# ─── B. What the board shows ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_running_patrols_are_counted_and_listed():
    w = await _world()
    async with _client() as c:
        body = (await c.get(URL, headers=w["admin_h"])).json()
    assert body["summary"]["active"] >= 2
    numbers = [p["patrol_number"] for p in body["in_progress"]]
    assert "VP-sess_a" in numbers and "VP-sess_b" in numbers


@pytest.mark.asyncio
async def test_an_exception_carries_what_an_operator_needs_to_act():
    """Camera, question, answer, whether there is a picture, and whether an
    incident exists — enough to decide without opening the patrol."""
    w = await _world()
    await _add_exception(w, "site_a", "cam_a", "sess_a")
    async with _client() as c:
        body = (await c.get(URL, headers=w["admin_h"])).json()
    mine = [e for e in body["exceptions"] if e["patrol_number"] == "VP-sess_a"]
    assert mine, body["exceptions"]
    e = mine[0]
    assert e["camera_name"] == "Gate"
    assert e["question_text"] == "Is the gate secure?"
    assert e["answer_text"] == "NO"
    assert e["has_snapshot"] is True
    assert "incident_id" in e


@pytest.mark.asyncio
async def test_a_passing_answer_never_reaches_the_board():
    """The panel is for findings. Every answered question would bury the one
    that matters."""
    w = await _world()
    await _sql(
        "INSERT INTO virtual_patrol_session_cameras "
        "  (tenant_id, session_id, camera_id, sequence_no, camera_name, status) "
        "VALUES (:t,:se,:c,2,'Quiet Corner','COMPLETED')",
        {"t": w["tenant"], "se": w["sess_a"], "c": w["cam_a"]})
    async with _client() as c:
        body = (await c.get(URL, headers=w["admin_h"])).json()
    assert all(e["camera_name"] != "Quiet Corner" for e in body["exceptions"])


# ─── C. Site scoping ─────────────────────────────────────────────────────────

async def _restrict_to(tenant_id, user_id, site_id):
    """Give a user an explicit site assignment, which switches on scoping."""
    await _sql("INSERT INTO user_sites (tenant_id, user_id, site_id) "
               "VALUES (:t,:u,:s) ON CONFLICT DO NOTHING",
               {"t": tenant_id, "u": user_id, "s": site_id})


@pytest.mark.asyncio
async def test_a_site_restricted_user_sees_only_their_own_patrols():
    w = await _world()
    await _restrict_to(w["tenant"], w["supervisor"], w["site_a"])
    async with _client() as c:
        body = (await c.get(URL, headers=w["supervisor_h"])).json()
    numbers = [p["patrol_number"] for p in body["in_progress"]]
    assert "VP-sess_a" in numbers
    assert "VP-sess_b" not in numbers, "a restricted user saw another site's patrol"


@pytest.mark.asyncio
async def test_a_site_restricted_user_does_not_see_another_sites_exceptions():
    """The sharper half. An exception names a camera, a time and what was wrong
    with it — exactly what a site restriction is meant to contain."""
    w = await _world()
    await _add_exception(w, "site_b", "cam_b", "sess_b")
    await _restrict_to(w["tenant"], w["supervisor"], w["site_a"])
    async with _client() as c:
        body = (await c.get(URL, headers=w["supervisor_h"])).json()
    assert all(e["patrol_number"] != "VP-sess_b" for e in body["exceptions"]), \
        "a restricted user read another site's finding"
