"""Gap 81 — Site-scoped user access enforcement (isolated-tenant)

Tests app/dependencies/sites.py + enforcement in sites/cameras/alerts/
incidents/dob/command-centre routers + the user-site assignment API.

Semantics under test:
  - Roles 1-2: always unrestricted (see everything in tenant).
  - Roles 3-6: unrestricted when NO user_sites rows exist (fail-open,
    backwards compatible); restricted to assigned sites when rows exist.
  - Role 7 (client): with NO assignments sees NOTHING (fail-closed);
    with assignments sees only those sites.

Sections:
  A — Assignment API (4 tests)
  B — Sites list/get scoping (4 tests)
  C — Cameras scoping (3 tests)
  D — Alerts + incidents scoping (3 tests)
  E — DOB scoping (2 tests)
  F — Command centre scoping (2 tests)
  G — Client fail-closed (3 tests)
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


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"scp-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Scope Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Scope Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"scp-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_extra_user(tenant_id: uuid.UUID, role_id: int):
    """Seed an additional user in an existing tenant; returns (user_id, token)."""
    from app.core.security import create_access_token

    user_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Scoped User')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"scp-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id: uuid.UUID, name: str) -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)"),
            {"id": site_id, "tid": tenant_id, "name": name},
        )
        await s.commit()
    await engine.dispose()
    return site_id


async def _seed_camera(tenant_id: uuid.UUID, site_id: uuid.UUID | None, name: str) -> uuid.UUID:
    camera_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:id, :tid, :sid, :name)"),
            {"id": camera_id, "tid": tenant_id, "sid": site_id, "name": name},
        )
        await s.commit()
    await engine.dispose()
    return camera_id


async def _seed_alert(tenant_id: uuid.UUID, camera_id: uuid.UUID, title: str) -> uuid.UUID:
    alert_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, title, status) "
                "VALUES (:id, :tid, :cid, 'intrusion', 'high', :title, 'open')"
            ),
            {"id": alert_id, "tid": tenant_id, "cid": camera_id, "title": title},
        )
        await s.commit()
    await engine.dispose()
    return alert_id


async def _seed_incident(tenant_id: uuid.UUID, camera_id: uuid.UUID, title: str) -> uuid.UUID:
    incident_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status) "
                "VALUES (:id, :tid, :cid, :title, 'high', 'open')"
            ),
            {"id": incident_id, "tid": tenant_id, "cid": camera_id, "title": title},
        )
        await s.commit()
    await engine.dispose()
    return incident_id


async def _seed_dob_entry(tenant_id: uuid.UUID, author_id: uuid.UUID,
                          site_id: uuid.UUID | None, body_text: str) -> uuid.UUID:
    entry_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO occurrence_book_entries "
                "(id, tenant_id, author_user_id, entry_type, body, site_id) "
                "VALUES (:id, :tid, :uid, 'general', :body, :sid)"
            ),
            {"id": entry_id, "tid": tenant_id, "uid": author_id,
             "body": body_text, "sid": site_id},
        )
        await s.commit()
    await engine.dispose()
    return entry_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _assign_sites(admin_token: str, user_id: uuid.UUID, site_ids: list[uuid.UUID]):
    async with await _authed(admin_token) as c:
        r = await c.put(f"/api/v1/users/{user_id}/sites",
                        json={"site_ids": [str(s) for s in site_ids]})
    assert r.status_code == 200, f"assign_sites failed: {r.text}"
    return r.json()


# ─── A. Assignment API ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scp_get_user_sites_empty():
    """GET /users/{id}/sites on a user with no assignments returns []."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/users/{user_id}/sites")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_scp_put_and_get_user_sites_round_trip():
    """PUT /users/{id}/sites assigns sites; GET returns them with site_name."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, _ = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "Site Alpha")
    site_b = await _seed_site(tenant_id, "Site Beta")
    await _assign_sites(admin_token, guard_id, [site_a, site_b])
    async with await _authed(admin_token) as c:
        r = await c.get(f"/api/v1/users/{guard_id}/sites")
    rows = r.json()
    assert len(rows) == 2
    names = {row["site_name"] for row in rows}
    assert names == {"Site Alpha", "Site Beta"}


@pytest.mark.asyncio
async def test_scp_put_replaces_assignments():
    """A second PUT fully replaces the previous assignment set."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, _ = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "Old Site")
    site_b = await _seed_site(tenant_id, "New Site")
    await _assign_sites(admin_token, guard_id, [site_a])
    await _assign_sites(admin_token, guard_id, [site_b])
    async with await _authed(admin_token) as c:
        r = await c.get(f"/api/v1/users/{guard_id}/sites")
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["site_name"] == "New Site"


@pytest.mark.asyncio
async def test_scp_put_unknown_site_returns_404():
    """PUT with a site_id that doesn't exist in the tenant returns 404."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, _ = await _seed_extra_user(tenant_id, role_id=5)
    async with await _authed(admin_token) as c:
        r = await c.put(f"/api/v1/users/{guard_id}/sites",
                        json={"site_ids": [str(uuid.uuid4())]})
    assert r.status_code == 404


# ─── B. Sites list/get scoping ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scp_unassigned_internal_user_sees_all_sites():
    """Supervisor with NO assignments sees all sites (fail-open)."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    _, sup_token = await _seed_extra_user(tenant_id, role_id=3)
    await _seed_site(tenant_id, "Site One")
    await _seed_site(tenant_id, "Site Two")
    async with await _authed(sup_token) as c:
        r = await c.get("/api/v1/sites")
    assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_scp_assigned_user_sees_only_assigned_sites():
    """Guard assigned to Site A sees only Site A in the sites list."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "Assigned Site")
    await _seed_site(tenant_id, "Other Site")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r = await c.get("/api/v1/sites")
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Assigned Site"


@pytest.mark.asyncio
async def test_scp_admin_always_unrestricted():
    """Admin (role 2) sees all sites even if user_sites rows exist for them."""
    tenant_id, admin_id, admin_token = await _seed_tenant_and_token(role_id=2)
    site_a = await _seed_site(tenant_id, "Site A")
    await _seed_site(tenant_id, "Site B")
    await _assign_sites(admin_token, admin_id, [site_a])
    async with await _authed(admin_token) as c:
        r = await c.get("/api/v1/sites")
    assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_scp_get_unassigned_site_returns_404():
    """Supervisor assigned to Site A gets 404 on GET /sites/{site_b}."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    sup_id, sup_token = await _seed_extra_user(tenant_id, role_id=3)
    site_a = await _seed_site(tenant_id, "Mine")
    site_b = await _seed_site(tenant_id, "Not Mine")
    await _assign_sites(admin_token, sup_id, [site_a])
    async with await _authed(sup_token) as c:
        r_mine = await c.get(f"/api/v1/sites/{site_a}")
        r_other = await c.get(f"/api/v1/sites/{site_b}")
    assert r_mine.status_code == 200
    assert r_other.status_code == 404


# ─── C. Cameras scoping ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scp_camera_list_filtered_to_assigned_sites():
    """Guard assigned to Site A sees only Site A's cameras; site-less cameras hidden."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "Cam Site A")
    site_b = await _seed_site(tenant_id, "Cam Site B")
    await _seed_camera(tenant_id, site_a, "Cam A1")
    await _seed_camera(tenant_id, site_b, "Cam B1")
    await _seed_camera(tenant_id, None, "Cam No Site")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r = await c.get("/api/v1/cameras")
    names = [row["name"] for row in r.json()]
    assert names == ["Cam A1"]


@pytest.mark.asyncio
async def test_scp_camera_get_other_site_returns_404():
    """GET /cameras/{id} for a camera at an unassigned site returns 404."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "Allowed")
    site_b = await _seed_site(tenant_id, "Blocked")
    cam_a = await _seed_camera(tenant_id, site_a, "OK Cam")
    cam_b = await _seed_camera(tenant_id, site_b, "Hidden Cam")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r_ok = await c.get(f"/api/v1/cameras/{cam_a}")
        r_blocked = await c.get(f"/api/v1/cameras/{cam_b}")
    assert r_ok.status_code == 200
    assert r_blocked.status_code == 404


@pytest.mark.asyncio
async def test_scp_unassigned_user_sees_all_cameras():
    """Operator with no assignments sees every camera (fail-open)."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    _, op_token = await _seed_extra_user(tenant_id, role_id=4)
    site_a = await _seed_site(tenant_id, "S1")
    await _seed_camera(tenant_id, site_a, "C1")
    await _seed_camera(tenant_id, None, "C2")
    async with await _authed(op_token) as c:
        r = await c.get("/api/v1/cameras")
    assert len(r.json()) == 2


# ─── D. Alerts + incidents scoping ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scp_alerts_filtered_to_assigned_sites():
    """Guard sees only alerts from cameras at assigned sites."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "Alert Site A")
    site_b = await _seed_site(tenant_id, "Alert Site B")
    cam_a = await _seed_camera(tenant_id, site_a, "AC1")
    cam_b = await _seed_camera(tenant_id, site_b, "BC1")
    await _seed_alert(tenant_id, cam_a, "Visible Alert")
    await _seed_alert(tenant_id, cam_b, "Hidden Alert")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r = await c.get("/api/v1/alerts")
    titles = [a["title"] for a in r.json()["items"]]
    assert titles == ["Visible Alert"]


@pytest.mark.asyncio
async def test_scp_incidents_filtered_to_assigned_sites():
    """Guard sees only incidents from cameras at assigned sites."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "Inc Site A")
    site_b = await _seed_site(tenant_id, "Inc Site B")
    cam_a = await _seed_camera(tenant_id, site_a, "IC1")
    cam_b = await _seed_camera(tenant_id, site_b, "IC2")
    await _seed_incident(tenant_id, cam_a, "Visible Incident")
    await _seed_incident(tenant_id, cam_b, "Hidden Incident")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r = await c.get("/api/v1/incidents")
    titles = [i["title"] for i in r.json()["items"]]
    assert titles == ["Visible Incident"]


@pytest.mark.asyncio
async def test_scp_admin_sees_all_alerts():
    """Admin sees alerts from every site including site-less cameras."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    site_a = await _seed_site(tenant_id, "AA")
    cam_a = await _seed_camera(tenant_id, site_a, "A")
    cam_none = await _seed_camera(tenant_id, None, "N")
    await _seed_alert(tenant_id, cam_a, "Site Alert")
    await _seed_alert(tenant_id, cam_none, "No-Site Alert")
    async with await _authed(admin_token) as c:
        r = await c.get("/api/v1/alerts")
    assert len(r.json()["items"]) == 2


# ─── E. DOB scoping ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scp_dob_list_filtered():
    """Guard sees only DOB entries for assigned sites; site-less entries hidden."""
    tenant_id, admin_id, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "DOB A")
    site_b = await _seed_site(tenant_id, "DOB B")
    await _seed_dob_entry(tenant_id, admin_id, site_a, "entry at A")
    await _seed_dob_entry(tenant_id, admin_id, site_b, "entry at B")
    await _seed_dob_entry(tenant_id, admin_id, None, "entry no site")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r = await c.get("/api/v1/dob")
    bodies = [e["body"] for e in r.json()]
    assert bodies == ["entry at A"]


@pytest.mark.asyncio
async def test_scp_dob_get_other_site_404():
    """GET /dob/{id} for another site's entry returns 404."""
    tenant_id, admin_id, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "DG A")
    site_b = await _seed_site(tenant_id, "DG B")
    entry_a = await _seed_dob_entry(tenant_id, admin_id, site_a, "mine")
    entry_b = await _seed_dob_entry(tenant_id, admin_id, site_b, "not mine")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r_mine = await c.get(f"/api/v1/dob/{entry_a}")
        r_other = await c.get(f"/api/v1/dob/{entry_b}")
    assert r_mine.status_code == 200
    assert r_other.status_code == 404


# ─── F. Command centre scoping ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scp_command_centre_filtered_sites():
    """C&C overview shows only assigned sites' cards for a restricted user."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    sup_id, sup_token = await _seed_extra_user(tenant_id, role_id=3)
    site_a = await _seed_site(tenant_id, "CC Mine")
    await _seed_site(tenant_id, "CC Other")
    await _assign_sites(admin_token, sup_id, [site_a])
    async with await _authed(sup_token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    body = r.json()
    assert body["summary"]["total_sites"] == 1
    assert [s["name"] for s in body["sites"]] == ["CC Mine"]


@pytest.mark.asyncio
async def test_scp_command_centre_alert_counts_scoped():
    """C&C active-alert total excludes other sites for restricted users."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    sup_id, sup_token = await _seed_extra_user(tenant_id, role_id=3)
    site_a = await _seed_site(tenant_id, "CCA")
    site_b = await _seed_site(tenant_id, "CCB")
    cam_a = await _seed_camera(tenant_id, site_a, "CCC1")
    cam_b = await _seed_camera(tenant_id, site_b, "CCC2")
    await _seed_alert(tenant_id, cam_a, "a1")
    await _seed_alert(tenant_id, cam_b, "b1")
    await _assign_sites(admin_token, sup_id, [site_a])
    async with await _authed(sup_token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    assert r.json()["summary"]["active_alerts"] == 1


# ─── G. Client fail-closed ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scp_client_with_no_sites_sees_nothing():
    """Client (role 7) with NO assignments gets empty sites/cameras/alerts."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    _, client_token = await _seed_extra_user(tenant_id, role_id=7)
    site_a = await _seed_site(tenant_id, "Client Site")
    cam_a = await _seed_camera(tenant_id, site_a, "Client Cam")
    await _seed_alert(tenant_id, cam_a, "Client Alert")
    async with await _authed(client_token) as c:
        r_sites = await c.get("/api/v1/sites")
        r_cams = await c.get("/api/v1/cameras")
        r_alerts = await c.get("/api/v1/alerts")
    assert r_sites.json() == []
    assert r_cams.json() == []
    assert r_alerts.json()["items"] == []


@pytest.mark.asyncio
async def test_scp_client_with_assignment_sees_their_site_only():
    """Client assigned to their site sees it — and only it."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    client_id, client_token = await _seed_extra_user(tenant_id, role_id=7)
    site_theirs = await _seed_site(tenant_id, "Their Building")
    site_other = await _seed_site(tenant_id, "Other Building")
    cam_theirs = await _seed_camera(tenant_id, site_theirs, "Their Cam")
    cam_other = await _seed_camera(tenant_id, site_other, "Other Cam")
    await _seed_alert(tenant_id, cam_theirs, "Their Alert")
    await _seed_alert(tenant_id, cam_other, "Other Alert")
    await _assign_sites(admin_token, client_id, [site_theirs])
    async with await _authed(client_token) as c:
        r_sites = await c.get("/api/v1/sites")
        r_alerts = await c.get("/api/v1/alerts")
    assert [s["name"] for s in r_sites.json()] == ["Their Building"]
    assert [a["title"] for a in r_alerts.json()["items"]] == ["Their Alert"]


# ─── H. Streams/recordings scoping (Gap 89 hardening) ───────────────────────

@pytest.mark.asyncio
async def test_scp_global_streams_list_scoped():
    """Client with no assignments sees zero streams; assigned guard sees
    only their site's streams (global /api/v1/streams)."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    _, client_token = await _seed_extra_user(tenant_id, role_id=7)
    site_a = await _seed_site(tenant_id, "Str Site A")
    site_b = await _seed_site(tenant_id, "Str Site B")
    cam_a = await _seed_camera(tenant_id, site_a, "Str Cam A")
    cam_b = await _seed_camera(tenant_id, site_b, "Str Cam B")
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        for cam in (cam_a, cam_b):
            await s.execute(
                text("INSERT INTO streams (tenant_id, camera_id, url) "
                     "VALUES (:tid, :cid, 'rtsp://203.0.113.9:554/x')"),
                {"tid": tenant_id, "cid": cam},
            )
        await s.commit()
    await engine.dispose()
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r_guard = await c.get("/api/v1/streams")
    async with await _authed(client_token) as c:
        r_client = await c.get("/api/v1/streams")
    assert [row["camera_name"] for row in r_guard.json()] == ["Str Cam A"]
    assert r_client.json() == []


@pytest.mark.asyncio
async def test_scp_per_camera_streams_other_site_404():
    """GET /cameras/{id}/streams for another site's camera → 404."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "PCS A")
    site_b = await _seed_site(tenant_id, "PCS B")
    cam_b = await _seed_camera(tenant_id, site_b, "PCS Cam B")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r = await c.get(f"/api/v1/cameras/{cam_b}/streams")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_scp_recording_timeline_other_site_404():
    """Recording timeline for another site's camera → 404."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "TL A")
    site_b = await _seed_site(tenant_id, "TL B")
    cam_b = await _seed_camera(tenant_id, site_b, "TL Cam B")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        r = await c.get(f"/api/v1/recordings/timeline?camera_id={cam_b}&date=2026-07-04")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_scp_clearing_assignments_restores_default_semantics():
    """PUT with empty site_ids: internal role becomes unrestricted again;
    client loses all visibility."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id, "R1")
    await _seed_site(tenant_id, "R2")
    await _assign_sites(admin_token, guard_id, [site_a])
    async with await _authed(guard_token) as c:
        assert len((await c.get("/api/v1/sites")).json()) == 1
    await _assign_sites(admin_token, guard_id, [])
    async with await _authed(guard_token) as c:
        assert len((await c.get("/api/v1/sites")).json()) == 2
