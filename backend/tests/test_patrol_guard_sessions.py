"""A guard starts, scans and finishes their own patrol from the phone.

Owner's phone test, 26 October 2026: "Failed to start patrol session" - starting (and finishing) a patrol required
patrol:manage, which no guard holds, so a guard could scan checkpoints but never start the patrol. The person walking
the patrol now needs patrol:scan; finishing someone else's patrol still needs patrol:manage. Every earlier patrol test
signed in as an admin, which is how this went unseen.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from tests.test_shifts import _admin_engine

ROLES = {"admin": 2, "supervisor": 3, "guard": 5, "other": 5, "viewer": 6}


async def _seed() -> dict:
    from app.core.security import create_access_token, hash_password

    tenant_id, site_id = uuid.uuid4(), uuid.uuid4()
    people = {name: uuid.uuid4() for name in ROLES}
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        await s.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Patrol guards', :s)"),
                        {"id": tenant_id, "s": f"patg-{tenant_id.hex[:8]}"})
        for name, uid in people.items():
            await s.execute(text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                                 "VALUES (:id, :t, :r, :e, :pw)"),
                            {"id": uid, "t": tenant_id, "r": ROLES[name], "e": f"{name}-{uid.hex[:6]}@test.local",
                             "pw": hash_password("x")})
        await s.execute(text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :t, 'Site')"),
                        {"id": site_id, "t": tenant_id})
        await s.commit()
    await engine.dispose()
    tokens = {name: create_access_token(str(uid), str(tenant_id), ROLES[name]) for name, uid in people.items()}
    return {"tokens": tokens, "ids": {k: str(v) for k, v in people.items()}, "site": str(site_id)}


def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _route(org: dict) -> tuple[str, str]:
    async with _client(org["tokens"]["admin"]) as a:
        route = (await a.post("/api/v1/patrols/routes", json={"site_id": org["site"], "name": "Perimeter"})).json()
        cp = (await a.post(f"/api/v1/patrols/routes/{route['id']}/checkpoints",
                           json={"sequence": 1, "name": "Gate 1"})).json()
    return route["id"], cp["id"]


@pytest.mark.asyncio
async def test_a_guard_starts_scans_and_finishes_their_own_patrol():
    org = await _seed()
    route_id, checkpoint_id = await _route(org)
    async with _client(org["tokens"]["guard"]) as g:
        r = await g.post(f"/api/v1/patrols/routes/{route_id}/sessions")
        assert r.status_code == 200, r.text
        session = r.json()
        assert session["status"] == "in_progress" and session["guard_user_id"] == org["ids"]["guard"]
        r = await g.post(f"/api/v1/patrols/sessions/{session['id']}/scan",
                         json={"checkpoint_id": checkpoint_id, "scan_method": "manual"})
        assert r.status_code == 200, r.text
        r = await g.post(f"/api/v1/patrols/sessions/{session['id']}/complete")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed" and r.json()["scanned_checkpoints"] == 1


@pytest.mark.asyncio
async def test_only_the_guard_on_a_patrol_or_a_supervisor_finishes_it():
    org = await _seed()
    route_id, _ = await _route(org)
    async with _client(org["tokens"]["guard"]) as g:
        session_id = (await g.post(f"/api/v1/patrols/routes/{route_id}/sessions")).json()["id"]
    async with _client(org["tokens"]["other"]) as o:
        r = await o.post(f"/api/v1/patrols/sessions/{session_id}/complete")
        assert r.status_code == 403 and "patrol:manage" in r.json()["detail"]
    async with _client(org["tokens"]["supervisor"]) as s:
        assert (await s.post(f"/api/v1/patrols/sessions/{session_id}/complete")).status_code == 200


@pytest.mark.asyncio
async def test_a_viewer_still_cannot_start_a_patrol():
    org = await _seed()
    route_id, _ = await _route(org)
    async with _client(org["tokens"]["viewer"]) as v:
        assert (await v.post(f"/api/v1/patrols/routes/{route_id}/sessions")).status_code == 403
