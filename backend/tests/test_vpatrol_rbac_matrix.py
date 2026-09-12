"""Who may do what, checked against the grants migration 0116 actually seeded.

The existing API tests prove an Admin can create a schedule and a Guard cannot.
That is one cell of a six-permission, five-role matrix. This file walks the rest,
because the cells nobody checked are where a role quietly holds more than it was
meant to — and the seeded grants say plainly what each role should have:

    Admin (2), Manager (8)   all six
    Supervisor (3)           read, execute, report
    Operator (4), Guard (5)  read, execute

So a Supervisor must be refused manage, export and email; a Guard must be
refused report as well. None of that was covered.

TWO LAYERS, BECAUSE THEY FAIL DIFFERENTLY. The seeded grants can be right while
an endpoint is guarded by the wrong dependency, and an endpoint can be guarded
correctly while the grant seed drifts. Section A reads the grants from the
database; section B calls the endpoints over HTTP.

Sections:
  A — The seeded grants match the intended matrix (2 tests)
  B — Each endpoint refuses a role that lacks its permission (parametrized)
  C — The snapshot image endpoint (6 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from pathlib import Path
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from app.core.config import settings
from app.core.security import create_access_token

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

ADMIN, SUPERVISOR, OPERATOR, GUARD, VIEWER, MANAGER = 2, 3, 4, 5, 6, 8

#: What migration 0116 grants. Kept here as the statement of intent, so a change
#: to the seed has to be a deliberate change to this table too.
EXPECTED_GRANTS = {
    ADMIN:      {"read", "manage", "execute", "report", "export", "email"},
    MANAGER:    {"read", "manage", "execute", "report", "export", "email"},
    SUPERVISOR: {"read", "execute", "report"},
    OPERATOR:   {"read", "execute"},
    GUARD:      {"read", "execute"},
}


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


async def _world() -> dict:
    """A tenant with a site, a camera, a schedule, and a completed session.

    Enough that every endpoint under test has something real to act on — a 404
    would pass an authorization test for the wrong reason.
    """
    i = {k: uuid.uuid4() for k in
         ("tenant", "site", "camera", "sched", "sched_cam", "session",
          "sess_cam", "recipient")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'RBAC Co',:s)",
               {"t": i["tenant"], "s": f"vprbac-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
               "VALUES (:i,:t,:s,'Main Gate')",
               {"i": i["camera"], "t": i["tenant"], "s": i["site"]})
    await _sql(
        "INSERT INTO virtual_patrol_schedules "
        "  (id, tenant_id, site_id, name, schedule_type, start_date, patrol_time) "
        "VALUES (:i,:t,:s,'Morning Patrol','DAILY',:d,:pt)",
        {"i": i["sched"], "t": i["tenant"], "s": i["site"],
         "d": date(2026, 1, 1), "pt": time(7, 0)})
    await _sql(
        "INSERT INTO virtual_patrol_schedule_cameras "
        "  (id, tenant_id, schedule_id, camera_id, sequence_no) VALUES (:i,:t,:sc,:c,1)",
        {"i": i["sched_cam"], "t": i["tenant"], "sc": i["sched"], "c": i["camera"]})
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
        "   scheduled_for, status) "
        "VALUES (:i,:t,:s,:sc,:n,'Morning Patrol',:w,'COMPLETED')",
        {"i": i["session"], "t": i["tenant"], "s": i["site"], "sc": i["sched"],
         "n": f"VP-RBAC-{i['tenant'].hex[:8]}",
         "w": datetime.now(timezone.utc) - timedelta(hours=1)})
    # A REAL FILE ON DISK, not a path to nothing.
    #
    # Without it every snapshot request returns 404 — the permitted ones because
    # the file is missing and the refused ones because they were refused — and a
    # test asserting 404 for a blocked user would pass even if the block did not
    # exist. The authorized case has to be able to return 200 for the refusal to
    # mean anything.
    rel = f"vpatrol/{i['sess_cam'].hex}.jpg"
    disk = Path(settings.EVIDENCE_ROOT) / rel
    disk.parent.mkdir(parents=True, exist_ok=True)
    # Smallest thing a JPEG reader will accept: SOI + EOI. The endpoint serves
    # bytes, it does not decode them.
    disk.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9")
    i["snapshot_rel"] = rel

    await _sql(
        "INSERT INTO virtual_patrol_session_cameras "
        "  (id, tenant_id, session_id, camera_id, sequence_no, camera_name, "
        "   snapshot_path, status) "
        "VALUES (:i,:t,:se,:c,1,'Main Gate',:p,'COMPLETED')",
        {"i": i["sess_cam"], "t": i["tenant"], "se": i["session"], "c": i["camera"],
         "p": rel})
    await _sql(
        "INSERT INTO virtual_patrol_email_recipients (id, tenant_id, schedule_id, email) "
        "VALUES (:i,:t,:sc,'ops@example.test')",
        {"i": i["recipient"], "t": i["tenant"], "sc": i["sched"]})

    # A FAILED queue row, so the resend route has a legitimate target. Resend
    # accepts only FAILED rows, so a PENDING one would answer 409 for every
    # role and the permission test would pass while proving nothing.
    i["queued"] = uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_email_queue "
        "  (id, tenant_id, schedule_id, session_id, frequency, recipients, "
        "   subject, status, attempts, last_error) "
        "VALUES (:i,:t,:sc,:se,'IMMEDIATE','ops@example.test','Report',"
        "        'FAILED',5,'simulated outage')",
        {"i": i["queued"], "t": i["tenant"], "sc": i["sched"], "se": i["session"]})
    return i


async def _user(tenant_id, role_id: int) -> dict:
    """A user of the given role, and the auth header for them."""
    uid = uuid.uuid4()
    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
        "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
        {"i": uid, "t": tenant_id, "r": role_id,
         "e": f"r{role_id}-{uid.hex[:8]}@rbac.test", "n": f"Role {role_id}"})
    return {"id": uid,
            "headers": {"Authorization":
                        f"Bearer {create_access_token(str(uid), str(tenant_id), role_id)}"}}


# ─── A. The seeded grants match the intended matrix ──────────────────────────

@pytest.mark.asyncio
async def test_each_role_holds_exactly_the_vpatrol_permissions_it_should():
    """Read from the database, not from the migration source.

    A migration that was edited after being applied, or a hand-run grant on a
    live system, both show up here and nowhere else.
    """
    rows = await _sql("""
        SELECT rp.role_id, p.code
          FROM role_permissions rp
          JOIN permissions p ON p.id = rp.permission_id
         WHERE p.code LIKE 'vpatrol:%'
    """)
    actual: dict[int, set[str]] = {}
    for role_id, code in rows:
        actual.setdefault(role_id, set()).add(code.split(":", 1)[1])

    for role_id, expected in EXPECTED_GRANTS.items():
        got = actual.get(role_id, set())
        assert got == expected, (
            f"role {role_id}: holds {sorted(got)}, should hold {sorted(expected)} "
            f"(extra: {sorted(got - expected)}, missing: {sorted(expected - got)})")


@pytest.mark.asyncio
async def test_no_role_outside_the_matrix_was_granted_anything():
    """A grant to a role nobody intended is the quiet kind of privilege creep —
    it never shows up as a failure, only as someone who can do more than the
    matrix says."""
    rows = await _sql("""
        SELECT DISTINCT rp.role_id
          FROM role_permissions rp
          JOIN permissions p ON p.id = rp.permission_id
         WHERE p.code LIKE 'vpatrol:%'
    """)
    granted = {r[0] for r in rows}
    unexpected = granted - set(EXPECTED_GRANTS)
    assert not unexpected, f"roles {sorted(unexpected)} hold vpatrol permissions unintentionally"


# ─── B. Each endpoint refuses a role that lacks its permission ───────────────

#: (permission, method, path template) — one representative endpoint per
#: permission, chosen so that a role WITH the permission gets a non-403 and a
#: role without it gets 403.
ENDPOINTS = [
    ("read",    "GET",    "/api/v1/virtual-patrol/schedules"),
    ("manage",  "POST",   "/api/v1/virtual-patrol/schedules"),
    ("execute", "GET",    "/api/v1/virtual-patrol/my-patrols"),
    ("report",  "GET",    "/api/v1/virtual-patrol/sessions/{session}/report/pdf"),
    ("export",  "GET",    "/api/v1/virtual-patrol/sessions/{session}/report/excel"),
    ("email",   "DELETE", "/api/v1/virtual-patrol/email-recipients/{recipient}"),
    ("email",   "GET",    "/api/v1/virtual-patrol/email-queue"),
    ("email",   "POST",   "/api/v1/virtual-patrol/email-queue/{queued}/resend"),
]

ROLES = [ADMIN, MANAGER, SUPERVISOR, OPERATOR, GUARD]


async def _call(client, method: str, url: str, headers: dict):
    if method == "POST":
        return await client.post(url, headers=headers, json={})
    if method == "DELETE":
        return await client.delete(url, headers=headers)
    return await client.get(url, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("permission,method,path", ENDPOINTS)
@pytest.mark.parametrize("role_id", ROLES)
async def test_the_endpoint_agrees_with_the_grant_matrix(permission, method, path, role_id):
    """403 exactly when the role lacks the permission, and not otherwise.

    Asserting only the refusals would pass for an endpoint that refuses
    everybody, so the holder side is checked too — loosely, since a permitted
    call may still legitimately 404 or 422 on this fixture data. What it must
    never be is 403.
    """
    w = await _world()
    who = await _user(w["tenant"], role_id)
    url = path.format(session=w["session"], recipient=w["recipient"],
                      queued=w["queued"])

    async with _client() as c:
        r = await _call(c, method, url, who["headers"])

    holds = permission in EXPECTED_GRANTS[role_id]
    if holds:
        assert r.status_code != 403, (
            f"role {role_id} holds vpatrol:{permission} but {method} {path} "
            f"refused it ({r.status_code}): {r.text[:200]}")
    else:
        assert r.status_code == 403, (
            f"role {role_id} does NOT hold vpatrol:{permission} yet {method} "
            f"{path} returned {r.status_code} — the endpoint is guarded by the "
            f"wrong permission, or by none")


@pytest.mark.asyncio
async def test_a_role_with_no_patrol_permissions_at_all_is_refused_everywhere():
    """Viewer (6) is granted nothing in the matrix. It should reach no patrol
    endpoint, not even the read ones."""
    w = await _world()
    who = await _user(w["tenant"], VIEWER)
    async with _client() as c:
        for permission, method, path in ENDPOINTS:
            url = path.format(session=w["session"], recipient=w["recipient"],
                              queued=w["queued"])
            r = await _call(c, method, url, who["headers"])
            assert r.status_code == 403, (
                f"a Viewer reached {method} {path} ({r.status_code}) — that role "
                f"holds no vpatrol permission at all")


# ─── C. The snapshot image endpoint ──────────────────────────────────────────
#
# Served to an <img> tag, so the token rides in the query string rather than a
# header. It carries no permission dependency by design; these tests record what
# it actually enforces, so that any later change to it is a visible change.

SNAP = ("/api/v1/virtual-patrol/sessions/{s}/cameras/{c}/snapshot?token={t}")


@pytest.mark.asyncio
async def test_the_snapshot_endpoint_refuses_a_bad_token():
    w = await _world()
    async with _client() as c:
        r = await c.get(SNAP.format(s=w["session"], c=w["sess_cam"], t="not-a-token"))
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_the_snapshot_endpoint_refuses_another_tenants_evidence():
    """The sharpest one. A valid token for tenant B must not fetch tenant A's
    snapshot by knowing the ids — the tenant is read from the token and the
    lookup is scoped by it."""
    a = await _world()
    b = await _world()
    intruder = await _user(b["tenant"], ADMIN)
    token = intruder["headers"]["Authorization"].split(" ", 1)[1]

    async with _client() as c:
        r = await c.get(SNAP.format(s=a["session"], c=a["sess_cam"], t=token))
    assert r.status_code == 404, (
        f"tenant B fetched tenant A's snapshot ({r.status_code}) — patrol "
        f"evidence is leaking across tenants")


@pytest.mark.asyncio
async def test_the_snapshot_endpoint_requires_the_patrol_read_permission():
    """A Viewer holds no vpatrol permission and must not read patrol evidence.

    The endpoint's token arrives as a query parameter, so the usual dependency
    cannot run and the check is made inside the handler. That is exactly the
    kind of check that gets forgotten, which is why it is tested here rather
    than assumed from the route decorator.
    """
    w = await _world()
    viewer = await _user(w["tenant"], VIEWER)
    token = viewer["headers"]["Authorization"].split(" ", 1)[1]

    async with _client() as c:
        r = await c.get(SNAP.format(s=w["session"], c=w["sess_cam"], t=token))
    assert r.status_code == 403, (
        f"a Viewer read a patrol snapshot ({r.status_code}) — the inline "
        f"permission check is missing or wrong")


@pytest.mark.asyncio
async def test_a_permitted_role_still_reaches_the_snapshot():
    """The other half. A gate that refuses everyone is not access control, it
    is an outage — and it would look identical to a passing refusal test."""
    w = await _world()
    officer = await _user(w["tenant"], GUARD)     # holds vpatrol:read
    token = officer["headers"]["Authorization"].split(" ", 1)[1]

    async with _client() as c:
        r = await c.get(SNAP.format(s=w["session"], c=w["sess_cam"], t=token))
    assert r.status_code == 200, (
        f"a Guard holding vpatrol:read could not read the snapshot "
        f"({r.status_code}): {r.text[:200]}")
    assert r.headers["content-type"] == "image/jpeg", r.headers
    assert r.content, "the response carried no image bytes"


@pytest.mark.asyncio
async def test_a_site_restricted_user_cannot_read_another_sites_snapshot():
    """The sharper gate, and the reason site scoping was added here at all.

    A snapshot IS the finding — the camera, at the time, in the state it was in.
    Scoping the Command Centre panel by site while leaving the image reachable
    by id would contain the description and publish the evidence.
    """
    w = await _world()
    other_site = uuid.uuid4()
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Other Site')",
               {"i": other_site, "t": w["tenant"]})

    supervisor = await _user(w["tenant"], SUPERVISOR)   # holds vpatrol:read
    # Assigned to a DIFFERENT site than the one the patrol ran on, which is what
    # switches scoping on for this user.
    await _sql("INSERT INTO user_sites (tenant_id, user_id, site_id) "
               "VALUES (:t,:u,:s) ON CONFLICT DO NOTHING",
               {"t": w["tenant"], "u": supervisor["id"], "s": other_site})
    token = supervisor["headers"]["Authorization"].split(" ", 1)[1]

    async with _client() as c:
        r = await c.get(SNAP.format(s=w["session"], c=w["sess_cam"], t=token))
    assert r.status_code == 404, (
        f"a supervisor restricted to another site read this site's patrol "
        f"evidence ({r.status_code}) — site scoping is not applied")
    # The companion test below reads the SAME row and gets 200, which is what
    # makes this 404 mean "refused" rather than "there was nothing there".


@pytest.mark.asyncio
async def test_a_user_assigned_to_the_patrols_site_still_reaches_it():
    """Complements the test above: scoping must narrow, not block."""
    w = await _world()
    supervisor = await _user(w["tenant"], SUPERVISOR)
    await _sql("INSERT INTO user_sites (tenant_id, user_id, site_id) "
               "VALUES (:t,:u,:s) ON CONFLICT DO NOTHING",
               {"t": w["tenant"], "u": supervisor["id"], "s": w["site"]})
    token = supervisor["headers"]["Authorization"].split(" ", 1)[1]

    async with _client() as c:
        r = await c.get(SNAP.format(s=w["session"], c=w["sess_cam"], t=token))
    assert r.status_code == 200, (
        f"a supervisor assigned to the patrol's own site was blocked "
        f"({r.status_code}): {r.text[:200]}")
    assert r.headers["content-type"] == "image/jpeg", r.headers
    assert r.content, "the response carried no image bytes"
