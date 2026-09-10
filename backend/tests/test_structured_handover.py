"""Handover as a two-signature record (isolated-tenant).

Tests backend/app/routers/handover.py and backend/app/services/handover.py.

Key semantics:
  - A handover is submitted, then accepted or disputed; dispute is the state a
    supervisor has to clear, and resolving it is a different fact from the
    incoming guard accepting
  - The checklist is per site with a tenant-wide fallback, and is SNAPSHOTTED
    onto the handover — editing the template later cannot rewrite history
  - A counted item carries what the system believed, so a mismatch is visible
  - Required checks must be done before acceptance; dispute is the escape hatch
  - Accepting reaches guards (handover:accept), because it is their own act
  - Deleting a template item leaves past handovers intact

Sections:
  A — Templates (5 tests)
  B — Snapshotting (4 tests)
  C — Checks and counts (4 tests)
  D — Accept, dispute, resolve (6 tests)
  E — Permissions and RLS (4 tests)
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
    slug = f"ho-{tenant_id.hex[:10]}"
    await _exec([
        ("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)",
         {"id": tenant_id, "name": f"Handover Test {slug}", "slug": slug}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
         "                   full_name, totp_enabled) "
         "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'HO Tester', CAST(:role AS smallint) = 1)",
         {"id": user_id, "tid": tenant_id, "role": role_id,
          "email": f"ho-{user_id.hex[:8]}@test.local"}),
    ])
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_user(tenant_id, role_id: int, name: str = "HO Guard"):
    from app.core.security import create_access_token

    user_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
         "                   full_name, totp_enabled) "
         "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', :name, CAST(:role AS smallint) = 1)",
         {"id": user_id, "tid": tenant_id, "role": role_id, "name": name,
          "email": f"ho-{user_id.hex[:8]}@test.local"}),
    ])
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id, name: str = "HO Site") -> uuid.UUID:
    site_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)",
         {"id": site_id, "tid": tenant_id, "name": name}),
    ])
    return site_id


async def _seed_shift(tenant_id, user_id, site_id) -> uuid.UUID:
    shift_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO shifts (id, tenant_id, guard_user_id, site_id, scheduled_start, "
         " scheduled_end, status) VALUES (:id, :tid, :uid, :sid, now(), "
         " now() + interval '8 hours', 'in_progress')",
         {"id": shift_id, "tid": tenant_id, "uid": user_id, "sid": site_id}),
    ])
    return shift_id


async def _authed(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _template(c: AsyncClient, name="Standard", site_id=None) -> dict:
    payload = {"name": name}
    if site_id:
        payload["site_id"] = str(site_id)
    r = await c.post("/api/v1/handovers/templates", json=payload)
    assert r.status_code == 201, f"template failed: {r.text}"
    return r.json()


async def _item(c: AsyncClient, template_id, label="Occurrence book read", **extra) -> dict:
    r = await c.post(f"/api/v1/handovers/templates/{template_id}/items",
                     json={"label": label, **extra})
    assert r.status_code == 201, f"item failed: {r.text}"
    return r.json()


async def _raise(c: AsyncClient, shift_id, notes="Nothing outstanding") -> dict:
    r = await c.post(f"/api/v1/shifts/{shift_id}/handover?outgoing_notes={notes}")
    assert r.status_code == 200, f"handover failed: {r.text}"
    return r.json()


# ─── A. Templates ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_active_tenant_wide_checklist():
    """Two would mean the handover picks whichever it read first."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        await _template(c, "Company standard")
        r = await c.post("/api/v1/handovers/templates", json={"name": "Another"})
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_a_site_checklist_coexists_with_the_default():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        await _template(c, "Company standard")
        r = await c.post("/api/v1/handovers/templates",
                         json={"name": "This site", "site_id": str(site_id)})
        assert r.status_code == 201


@pytest.mark.asyncio
async def test_retiring_a_checklist_frees_the_slot():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        first = await _template(c, "Old standard")
        r = await c.put(f"/api/v1/handovers/templates/{first['id']}",
                        json={"is_active": False})
        assert r.status_code == 200
        r = await c.post("/api/v1/handovers/templates", json={"name": "New standard"})
        assert r.status_code == 201


@pytest.mark.asyncio
async def test_measuring_a_register_requires_a_count():
    """"Keys checked" as a tick is worth little; the number is the point."""
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        tpl = await _template(c)
        r = await c.post(f"/api/v1/handovers/templates/{tpl['id']}/items", json={
            "label": "Keys", "expected_source": "keys_out",
        })
        assert r.status_code == 422

        r = await c.post(f"/api/v1/handovers/templates/{tpl['id']}/items", json={
            "label": "Keys", "expected_source": "moon_phase", "requires_count": True,
        })
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_templates_list_carries_its_items():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        tpl = await _template(c)
        await _item(c, tpl["id"], "Occurrence book read", sort_order=2)
        await _item(c, tpl["id"], "Radio handed over", sort_order=1)

        rows = (await c.get("/api/v1/handovers/templates")).json()
        mine = next(t for t in rows if t["id"] == tpl["id"])
        assert [i["label"] for i in mine["items"]] == [
            "Radio handed over", "Occurrence book read",
        ]


# ─── B. Snapshotting ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handover_snapshots_the_sites_checklist_over_the_default():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        default = await _template(c, "Company standard")
        await _item(c, default["id"], "Generic item")
        site_tpl = await _template(c, "This site", site_id=site_id)
        await _item(c, site_tpl["id"], "Site item A")
        await _item(c, site_tpl["id"], "Site item B")

        ho = await _raise(c, shift_id)
        assert ho["checklist_items"] == 2

        body = (await c.get(f"/api/v1/handovers/{ho['id']}")).json()
        assert sorted(cx["label"] for cx in body["checks"]) == ["Site item A", "Site item B"]


@pytest.mark.asyncio
async def test_a_site_without_its_own_checklist_falls_back_to_the_default():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        default = await _template(c, "Company standard")
        await _item(c, default["id"], "Generic item")

        ho = await _raise(c, shift_id)
        assert ho["checklist_items"] == 1


@pytest.mark.asyncio
async def test_no_checklist_still_produces_a_handover():
    """A tenant that has set nothing up gets what they had before, not an
    error."""
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        ho = await _raise(c, shift_id)
        assert ho["checklist_items"] == 0
        assert ho["status"] == "submitted"


@pytest.mark.asyncio
async def test_the_handover_carries_the_register_position():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        key = (await c.post("/api/v1/keys", json={
            "site_id": str(site_id), "key_code": "HO-1", "label": "Riser",
        })).json()
        await c.post("/api/v1/keys/issue",
                     json={"key_id": key["id"], "issued_to_name": "Contractor"})
        await c.post("/api/v1/lost-found",
                     json={"description": "Umbrella", "site_id": str(site_id)})
        await c.post("/api/v1/defects",
                     json={"site_id": str(site_id), "description": "Light out"})

        ho = await _raise(c, shift_id)
        assert ho["keys_outstanding"] == 1
        assert ho["lost_found_held"] == 1
        assert ho["open_defects_count"] == 1


# ─── C. Checks and counts ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_count_that_disagrees_with_the_register_is_flagged():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        tpl = await _template(c)
        await _item(c, tpl["id"], "Keys counted",
                    requires_count=True, expected_source="keys_out")
        key = (await c.post("/api/v1/keys", json={
            "site_id": str(site_id), "key_code": "HO-1", "label": "Riser",
        })).json()
        await c.post("/api/v1/keys/issue",
                     json={"key_id": key["id"], "issued_to_name": "Contractor"})

        ho = await _raise(c, shift_id)
        body = (await c.get(f"/api/v1/handovers/{ho['id']}")).json()
        check = body["checks"][0]
        assert check["expected_value"] == 1

        r = await c.put(f"/api/v1/handovers/{ho['id']}/checks", json={
            "checks": [{"id": check["id"], "checked": True, "counted_value": 2}],
        })
        assert r.status_code == 200
        assert r.json()["mismatches"] == 1

        # Correcting the count clears it — the mismatch is computed, not stored.
        r = await c.put(f"/api/v1/handovers/{ho['id']}/checks", json={
            "checks": [{"id": check["id"], "checked": True, "counted_value": 1}],
        })
        assert r.json()["mismatches"] == 0


@pytest.mark.asyncio
async def test_a_check_from_another_handover_is_rejected():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_a = await _seed_shift(tenant_id, user_id, site_id)
    shift_b = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        tpl = await _template(c)
        await _item(c, tpl["id"], "Occurrence book read")
        ho_a = await _raise(c, shift_a)
        ho_b = await _raise(c, shift_b)

        other = (await c.get(f"/api/v1/handovers/{ho_b['id']}")).json()["checks"][0]
        r = await c.put(f"/api/v1/handovers/{ho_a['id']}/checks", json={
            "checks": [{"id": other["id"], "checked": True}],
        })
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_checks_are_final_once_the_handover_closes():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        tpl = await _template(c)
        await _item(c, tpl["id"], "Occurrence book read")
        ho = await _raise(c, shift_id)
        check = (await c.get(f"/api/v1/handovers/{ho['id']}")).json()["checks"][0]

        await c.put(f"/api/v1/handovers/{ho['id']}/checks",
                    json={"checks": [{"id": check["id"], "checked": True}]})
        await c.post(f"/api/v1/handovers/{ho['id']}/accept", json={})

        r = await c.put(f"/api/v1/handovers/{ho['id']}/checks",
                        json={"checks": [{"id": check["id"], "checked": False}]})
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_deleting_a_template_item_leaves_past_handovers_intact():
    """The evidence that something was once checked must survive the checklist
    being reorganised."""
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        tpl = await _template(c)
        item = await _item(c, tpl["id"], "Torch handed over")
        ho = await _raise(c, shift_id)

        assert (await c.delete(f"/api/v1/handovers/items/{item['id']}")).status_code == 204

        body = (await c.get(f"/api/v1/handovers/{ho['id']}")).json()
        assert [cx["label"] for cx in body["checks"]] == ["Torch handed over"]
        assert body["checks"][0]["item_id"] is None


# ─── D. Accept, dispute, resolve ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_required_checks_block_acceptance():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        tpl = await _template(c)
        await _item(c, tpl["id"], "Keys counted")
        await _item(c, tpl["id"], "Torch handed over", is_required=False)
        ho = await _raise(c, shift_id)

        r = await c.post(f"/api/v1/handovers/{ho['id']}/accept", json={})
        assert r.status_code == 409

        required = next(cx for cx in
                        (await c.get(f"/api/v1/handovers/{ho['id']}")).json()["checks"]
                        if cx["is_required"])
        await c.put(f"/api/v1/handovers/{ho['id']}/checks",
                    json={"checks": [{"id": required["id"], "checked": True}]})

        # The optional item is still unticked, and acceptance goes through.
        r = await c.post(f"/api/v1/handovers/{ho['id']}/accept", json={})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_dispute_is_available_when_the_checks_cannot_be_completed():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        tpl = await _template(c)
        await _item(c, tpl["id"], "Keys counted")
        ho = await _raise(c, shift_id)

        r = await c.post(f"/api/v1/handovers/{ho['id']}/dispute",
                         json={"dispute_reason": "Eleven keys on the board, handover says twelve"})
        assert r.status_code == 200 and r.json()["status"] == "disputed"


@pytest.mark.asyncio
async def test_a_dispute_needs_a_reason():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        ho = await _raise(c, shift_id)
        r = await c.post(f"/api/v1/handovers/{ho['id']}/dispute",
                         json={"dispute_reason": ""})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_resolving_is_a_different_fact_from_accepting():
    """A supervisor deciding what happened about a discrepancy is not the same
    as the incoming guard taking responsibility, and the record keeps both."""
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        ho = await _raise(c, shift_id)
        await c.post(f"/api/v1/handovers/{ho['id']}/dispute",
                     json={"dispute_reason": "Key count is wrong"})

        r = await c.post(f"/api/v1/handovers/{ho['id']}/resolve",
                         json={"resolution_notes": "Issued after the handover was raised"})
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"
        assert r.json()["dispute_reason"] == "Key count is wrong"

        assert (await c.post(f"/api/v1/handovers/{ho['id']}/resolve",
                             json={})).status_code == 409


@pytest.mark.asyncio
async def test_only_a_disputed_handover_can_be_resolved():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, user_id, site_id)
    async with await _authed(token) as c:
        ho = await _raise(c, shift_id)
        r = await c.post(f"/api/v1/handovers/{ho['id']}/resolve", json={})
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_open_queue_holds_exactly_what_needs_acting_on():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    accepted_shift = await _seed_shift(tenant_id, user_id, site_id)
    disputed_shift = await _seed_shift(tenant_id, user_id, site_id)
    pending_shift = await _seed_shift(tenant_id, user_id, site_id)

    async with await _authed(token) as c:
        done = await _raise(c, accepted_shift)
        await c.post(f"/api/v1/handovers/{done['id']}/accept", json={})
        disputed = await _raise(c, disputed_shift)
        await c.post(f"/api/v1/handovers/{disputed['id']}/dispute",
                     json={"dispute_reason": "Short one radio"})
        pending = await _raise(c, pending_shift)

        rows = (await c.get("/api/v1/handovers?open_only=true")).json()
        assert sorted(h["id"] for h in rows) == sorted([disputed["id"], pending["id"]])


# ─── E. Permissions and RLS ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_guard_can_accept_the_handover_they_are_taking_over():
    """The whole feature exists for this person. Gating acceptance on
    handover:create, which stops at supervisors, made it unusable."""
    tenant_id, admin_id, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, admin_id, site_id)
    guard_id, guard_token = await _seed_user(tenant_id, 5, "Incoming Guard")

    async with await _authed(admin_token) as admin:
        ho = await _raise(admin, shift_id)

    async with await _authed(guard_token) as guard:
        assert (await guard.get(f"/api/v1/handovers/{ho['id']}")).status_code == 200
        r = await guard.post(f"/api/v1/handovers/{ho['id']}/accept",
                             json={"incoming_notes": "Taking over"})
        assert r.status_code == 200

    body = None
    async with await _authed(admin_token) as admin:
        body = (await admin.get(f"/api/v1/handovers/{ho['id']}")).json()
    assert body["accepted_by_name"] == "Incoming Guard"
    assert body["incoming_guard_id"] == str(guard_id)


@pytest.mark.asyncio
async def test_a_guard_cannot_maintain_checklists_or_resolve_disputes():
    tenant_id, admin_id, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = await _seed_shift(tenant_id, admin_id, site_id)
    _, guard_token = await _seed_user(tenant_id, 5)

    async with await _authed(admin_token) as admin:
        tpl = await _template(admin)
        ho = await _raise(admin, shift_id)

    async with await _authed(guard_token) as guard:
        await guard.post(f"/api/v1/handovers/{ho['id']}/dispute",
                         json={"dispute_reason": "Short one radio"})
        assert (await guard.post("/api/v1/handovers/templates",
                                 json={"name": "Mine"})).status_code == 403
        assert (await guard.post(f"/api/v1/handovers/templates/{tpl['id']}/items",
                                 json={"label": "Mine"})).status_code == 403
        assert (await guard.post(f"/api/v1/handovers/{ho['id']}/resolve",
                                 json={})).status_code == 403


@pytest.mark.asyncio
async def test_site_scoping_hides_handovers_at_unassigned_sites():
    tenant_id, admin_id, admin_token = await _seed_tenant()
    mine = await _seed_site(tenant_id, "Assigned")
    theirs = await _seed_site(tenant_id, "Not assigned")
    guard_id, guard_token = await _seed_user(tenant_id, 5)
    await _exec([
        ("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:uid, :sid, :tid)",
         {"uid": guard_id, "sid": mine, "tid": tenant_id}),
    ])
    ours_shift = await _seed_shift(tenant_id, admin_id, mine)
    theirs_shift = await _seed_shift(tenant_id, admin_id, theirs)

    async with await _authed(admin_token) as admin:
        ours = await _raise(admin, ours_shift)
        other = await _raise(admin, theirs_shift)

    async with await _authed(guard_token) as guard:
        ids = [h["id"] for h in (await guard.get("/api/v1/handovers")).json()]
        assert ours["id"] in ids and other["id"] not in ids
        assert (await guard.get(f"/api/v1/handovers/{other['id']}")).status_code == 404
        assert (await guard.post(f"/api/v1/handovers/{other['id']}/accept",
                                 json={})).status_code == 404


@pytest.mark.asyncio
async def test_another_tenant_sees_neither_handovers_nor_checklists():
    tenant_a, user_a, token_a = await _seed_tenant()
    site_a = await _seed_site(tenant_a)
    shift_a = await _seed_shift(tenant_a, user_a, site_a)
    _, _, token_b = await _seed_tenant()

    async with await _authed(token_a) as a:
        await _template(a, "A's standard")
        ho = await _raise(a, shift_a)

    async with await _authed(token_b) as b:
        assert (await b.get(f"/api/v1/handovers/{ho['id']}")).status_code == 404
        assert (await b.get("/api/v1/handovers")).json() == []
        assert (await b.get("/api/v1/handovers/templates")).json() == []
