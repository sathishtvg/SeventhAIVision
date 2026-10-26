"""What a guard may do from the phone (migration 0169, the owner's choice on 26 October 2026).

An audit of every call the phone makes against the guard role found buttons the server refused. The owner chose:
guards write the handover of their own shift (without handover:create, which the occurrence book reads as "every
guard's handover" - the first version granted it and two occurrence book tests caught that), add notes to an incident
and move its status (incident:update), and pre-register a visitor and send the QR (the new, narrower visitor:register
- not visitor:manage, so deleting a visitor and the visitor form's fields stay with supervisors). Guards do not send
emergency broadcasts.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from tests.test_shifts import _admin_engine

ROLES = {"admin": 2, "operator": 4, "guard": 5, "viewer": 6}


async def _holders() -> dict[str, set[int]]:
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        rows = (await s.execute(text(
            "SELECT p.code, rp.role_id FROM permissions p JOIN role_permissions rp ON rp.permission_id = p.id "
            "WHERE p.code IN ('visitor:register', 'visitor:manage', 'handover:create', 'incident:update', "
            "'broadcast:send')"
        ))).all()
    await engine.dispose()
    out: dict[str, set[int]] = {}
    for code, role in rows:
        out.setdefault(code, set()).add(role)
    return out


async def _seed() -> dict:
    from app.core.security import create_access_token, hash_password

    tenant_id, site_id = uuid.uuid4(), uuid.uuid4()
    people = {name: uuid.uuid4() for name in ROLES}
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        await s.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Guard rights', :s)"),
                        {"id": tenant_id, "s": f"grights-{tenant_id.hex[:8]}"})
        for name, uid in people.items():
            await s.execute(text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                                 "VALUES (:id, :t, :r, :e, :pw)"),
                            {"id": uid, "t": tenant_id, "r": ROLES[name], "e": f"{name}-{uid.hex[:6]}@test.local",
                             "pw": hash_password("x")})
        await s.execute(text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :t, 'Gate')"),
                        {"id": site_id, "t": tenant_id})
        await s.commit()
    await engine.dispose()
    return {"tokens": {n: create_access_token(str(u), str(tenant_id), ROLES[n]) for n, u in people.items()},
            "ids": {n: str(u) for n, u in people.items()}, "site": str(site_id)}


def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


@pytest.mark.asyncio
async def test_who_holds_what_after_0169():
    held = await _holders()
    assert {"incident:update", "visitor:register"} <= {c for c, roles in held.items() if 5 in roles}
    assert 5 not in held.get("handover:create", set()), "the occurrence book reads it as every guard's handover"
    assert 5 not in held.get("visitor:manage", set()), "guards do not delete visitors or edit the visitor form"
    assert 5 not in held.get("broadcast:send", set()), "guards do not send emergency broadcasts"
    assert held["visitor:register"] == held["visitor:manage"] | {5}, "everyone who could pre-register still can"


@pytest.mark.asyncio
async def test_a_guard_pre_registers_a_visitor_but_cannot_delete_one_or_change_the_form():
    org = await _seed()
    async with _client(org["tokens"]["guard"]) as g:
        r = await g.post("/api/v1/visitors", json={"full_name": "Walk-in Courier", "site_id": org["site"]})
        assert r.status_code == 201, r.text
        visitor_id = r.json()["id"]
        assert (await g.delete(f"/api/v1/visitors/{visitor_id}")).status_code == 403
        assert (await g.post("/api/v1/vms/form-fields", json={"label": "Mine"})).status_code == 403
    async with _client(org["tokens"]["operator"]) as o:
        r = await o.post("/api/v1/visitors", json={"full_name": "Someone"})
        assert r.status_code == 403, "operators did not pre-register before, and still do not"


@pytest.mark.asyncio
async def test_a_guard_adds_a_note_to_an_incident_and_moves_its_status_and_a_viewer_cannot():
    org = await _seed()
    async with _client(org["tokens"]["admin"]) as a:
        r = await a.post("/api/v1/incidents", json={"title": "Door forced", "severity": "medium"})
        assert r.status_code == 201, r.text
        incident_id = r.json()["id"]
    async with _client(org["tokens"]["guard"]) as g:
        r = await g.post(f"/api/v1/incidents/{incident_id}/notes", json={"note": "On scene, door secured."})
        assert r.status_code in (200, 201), r.text
        r = await g.put(f"/api/v1/incidents/{incident_id}/status", json={"status": "dispatched"})
        assert r.status_code == 200, r.text
    async with _client(org["tokens"]["viewer"]) as v:
        r = await v.post(f"/api/v1/incidents/{incident_id}/notes", json={"note": "x"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_guard_writes_the_handover_of_their_own_shift_and_not_of_anyone_elses():
    org = await _seed()
    shifts = {}
    async with _client(org["tokens"]["admin"]) as a:
        for who in ("guard", "operator"):
            r = await a.post("/api/v1/shifts", json={
                "guard_user_id": org["ids"][who], "site_id": org["site"],
                "scheduled_start": "2026-10-26T00:00:00+00:00", "scheduled_end": "2026-10-26T12:00:00+00:00",
            })
            assert r.status_code == 200, r.text
            shifts[who] = r.json()["id"]
    async with _client(org["tokens"]["guard"]) as g:
        r = await g.post(f"/api/v1/shifts/{shifts['guard']}/handover", params={"outgoing_notes": "All quiet."})
        assert r.status_code == 200, r.text
        r = await g.post(f"/api/v1/shifts/{shifts['operator']}/handover")
        assert r.status_code == 403 and "handover:create" in r.json()["detail"]
    async with _client(org["tokens"]["admin"]) as a:
        assert (await a.post(f"/api/v1/shifts/{shifts['operator']}/handover")).status_code == 200
