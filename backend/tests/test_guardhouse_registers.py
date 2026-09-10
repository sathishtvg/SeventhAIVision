"""The guardhouse registers — keys and lost & found (isolated-tenant).

Tests backend/app/routers/keyreg.py and backend/app/routers/lost_found.py.

Key semantics:
  - A key is out because an open transaction exists, not because a column
    says so; the database enforces that it cannot be out twice
  - A key that is out cannot be retired, and a retired key cannot be issued
  - Overdue is derived from expected_return_at, so it is never stale
  - Found property moves held -> claimed | disposed | handed_to_police, once
  - Only the last few characters of a claimant's identity document are kept
  - Retention reporting surfaces items held past the tenant's period
  - Handover and the pre-shift briefing both report keys still out
  - Site scoping (Gap 81) applies to every read path

Sections:
  A — Key cabinet (5 tests)
  B — Issue and return (7 tests)
  C — Lost & found lifecycle (7 tests)
  D — Retention (3 tests)
  E — Permissions, scoping and RLS (6 tests)
  F — Handover integration (2 tests)
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

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


# Imported at module level, not lazily inside a helper. pyproject documents the
# one-time cost of importing app.main (the ML stack — insightface, onnxruntime,
# torch) and raised the per-test timeout to 120s because of it. That cost still
# lands entirely on whichever test touches the app first, and on a loaded
# machine it crossed 120s and failed the first test in this file twice. Paying
# it at collection time instead puts it outside pytest-timeout's clock, where a
# module import belongs.
from app.main import app as _fastapi_app  # noqa: E402


def _app():
    return _fastapi_app


async def _exec(statements: list[tuple[str, dict]]):
    engine = _admin_engine()
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
    slug = f"gh-{tenant_id.hex[:10]}"
    await _exec([
        ("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)",
         {"id": tenant_id, "name": f"Guardhouse Test {slug}", "slug": slug}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
         "                   full_name, totp_enabled) "
         "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'GH Tester', CAST(:role AS smallint) = 1)",
         {"id": user_id, "tid": tenant_id, "role": role_id,
          "email": f"gh-{user_id.hex[:8]}@test.local"}),
    ])
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_user(tenant_id, role_id: int, name: str = "GH Guard"):
    from app.core.security import create_access_token

    user_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
         "                   full_name, totp_enabled) "
         "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', :name, CAST(:role AS smallint) = 1)",
         {"id": user_id, "tid": tenant_id, "role": role_id, "name": name,
          "email": f"gh-{user_id.hex[:8]}@test.local"}),
    ])
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id, name: str = "GH Site") -> uuid.UUID:
    site_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)",
         {"id": site_id, "tid": tenant_id, "name": name}),
    ])
    return site_id


async def _assign_site(tenant_id, user_id, site_id):
    await _exec([
        ("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:uid, :sid, :tid)",
         {"uid": user_id, "sid": site_id, "tid": tenant_id}),
    ])


async def _authed(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _create_key(c: AsyncClient, site_id, code="GH-01", label="Main door") -> dict:
    r = await c.post("/api/v1/keys", json={
        "site_id": str(site_id), "key_code": code, "label": label,
    })
    assert r.status_code == 201, f"create_key failed: {r.text}"
    return r.json()


async def _issue(c: AsyncClient, key_id, name="Contractor", **extra) -> dict:
    payload = {"key_id": str(key_id), "issued_to_name": name, **extra}
    return await c.post("/api/v1/keys/issue", json=payload)


async def _log_item(c: AsyncClient, site_id=None, description="Black wallet") -> dict:
    payload = {"description": description, "category": "wallet"}
    if site_id:
        payload["site_id"] = str(site_id)
    r = await c.post("/api/v1/lost-found", json=payload)
    assert r.status_code == 201, f"log_item failed: {r.text}"
    return r.json()


# ─── A. Key cabinet ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_key_and_list_shows_it_not_out():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        r = await c.get(f"/api/v1/keys?site_id={site_id}")
        assert r.status_code == 200
        rows = [k for k in r.json() if k["id"] == key["id"]]
        assert len(rows) == 1
        assert rows[0]["is_out"] is False
        assert rows[0]["held_by_name"] is None


@pytest.mark.asyncio
async def test_duplicate_key_code_at_same_site_conflicts():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        await _create_key(c, site_id, code="A-1")
        r = await c.post("/api/v1/keys", json={
            "site_id": str(site_id), "key_code": "A-1", "label": "Second",
        })
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_same_key_code_at_a_different_site_is_fine():
    """Codes are stencilled on the fob and reused across sites."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "Site A")
    site_b = await _seed_site(tenant_id, "Site B")
    async with await _authed(token) as c:
        await _create_key(c, site_a, code="A-1")
        r = await c.post("/api/v1/keys", json={
            "site_id": str(site_b), "key_code": "A-1", "label": "Same code, other site",
        })
        assert r.status_code == 201


@pytest.mark.asyncio
async def test_unknown_field_is_rejected():
    """extra=forbid: a misspelled field must not be silently dropped."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/keys", json={
            "site_id": str(site_id), "key_code": "A-1", "label": "X", "colour": "red",
        })
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_key_details():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        r = await c.put(f"/api/v1/keys/{key['id']}", json={"cabinet_position": "B7"})
        assert r.status_code == 200 and r.json()["cabinet_position"] == "B7"


# ─── B. Issue and return ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_issue_then_key_shows_holder():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        r = await _issue(c, key["id"], name="Kumar (Otis)", issued_to_company="Otis")
        assert r.status_code == 201

        r = await c.get(f"/api/v1/keys/{key['id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["is_out"] is True
        assert body["held_by_name"] == "Kumar (Otis)"
        assert body["held_by_company"] == "Otis"


@pytest.mark.asyncio
async def test_a_key_cannot_be_out_twice():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        assert (await _issue(c, key["id"], name="First")).status_code == 201
        r = await _issue(c, key["id"], name="Second")
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_issue_requires_somebody_identifiable():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        r = await c.post("/api/v1/keys/issue", json={"key_id": key["id"]})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_issue_rejects_a_due_time_in_the_past():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        r = await _issue(c, key["id"], expected_return_at=past)
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_return_closes_the_transaction_and_frees_the_key():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        tx = (await _issue(c, key["id"])).json()

        r = await c.post(f"/api/v1/keys/transactions/{tx['id']}/return",
                         json={"return_notes": "Back at shift change"})
        assert r.status_code == 200 and r.json()["returned_at"] is not None

        # Returning twice is a conflict, not a silent second write.
        r = await c.post(f"/api/v1/keys/transactions/{tx['id']}/return", json={})
        assert r.status_code == 409

        # And the key can go out again.
        assert (await _issue(c, key["id"], name="Next holder")).status_code == 201


@pytest.mark.asyncio
async def test_overdue_is_derived_from_the_due_time():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        tx = (await _issue(c, key["id"], expected_return_at=due)).json()

        r = await c.get(f"/api/v1/keys/outstanding?site_id={site_id}")
        assert r.json()["out"] == 1 and r.json()["overdue"] == 0

        # Nothing writes an "overdue" flag — moving the clock is enough.
        await _exec([("UPDATE key_transactions SET expected_return_at = now() - interval '1 hour' "
                      "WHERE id = :id", {"id": uuid.UUID(tx["id"])})])
        r = await c.get(f"/api/v1/keys/outstanding?site_id={site_id}")
        assert r.json()["overdue"] == 1
        assert r.json()["keys"][0]["is_overdue"] is True


@pytest.mark.asyncio
async def test_a_key_that_is_out_cannot_be_retired():
    """Retiring it would erase the only record that it has to come back."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        tx = (await _issue(c, key["id"])).json()

        r = await c.put(f"/api/v1/keys/{key['id']}", json={"is_active": False})
        assert r.status_code == 409

        await c.post(f"/api/v1/keys/transactions/{tx['id']}/return", json={})
        r = await c.put(f"/api/v1/keys/{key['id']}", json={"is_active": False})
        assert r.status_code == 200

        # A retired key cannot go back out.
        assert (await _issue(c, key["id"])).status_code == 409


# ─── C. Lost & found lifecycle ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_and_read_back_a_found_item():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        item = await _log_item(c, site_id)
        assert item["status"] == "held"
        r = await c.get(f"/api/v1/lost-found/{item['id']}")
        assert r.status_code == 200 and r.json()["has_photo"] is False


@pytest.mark.asyncio
async def test_unknown_category_is_rejected():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/lost-found",
                         json={"description": "x", "category": "spaceship"})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_found_at_cannot_be_in_the_future():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        r = await c.post("/api/v1/lost-found",
                         json={"description": "x", "found_at": future})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_release_records_the_claimant_and_closes_the_record():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        item = await _log_item(c, site_id)
        r = await c.post(f"/api/v1/lost-found/{item['id']}/release", json={
            "claimed_by_name": "Michelle Tan", "claimed_by_contact": "98765432",
            "claimed_id_type": "NRIC", "claimed_id_last4": "567A",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "claimed"
        assert r.json()["claimed_id_last4"] == "567A"

        # Releasing twice, and editing after release, are both conflicts —
        # the record is what somebody signed for.
        assert (await c.post(f"/api/v1/lost-found/{item['id']}/release",
                             json={"claimed_by_name": "Someone else"})).status_code == 409
        assert (await c.put(f"/api/v1/lost-found/{item['id']}",
                            json={"description": "Rewritten"})).status_code == 409


@pytest.mark.asyncio
async def test_only_the_document_tail_is_accepted():
    """A whole NRIC in the last-four field is a PDPA problem, not a typo."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        item = await _log_item(c, site_id)
        r = await c.post(f"/api/v1/lost-found/{item['id']}/release", json={
            "claimed_by_name": "Michelle Tan", "claimed_id_last4": "S1234567D",
        })
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_dispose_requires_a_method_and_police_handover_is_its_own_status():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        item = await _log_item(c, site_id)
        assert (await c.post(f"/api/v1/lost-found/{item['id']}/dispose",
                             json={})).status_code == 422

        r = await c.post(f"/api/v1/lost-found/{item['id']}/dispose", json={
            "disposal_method": "Handed to Neighbourhood Police Post",
            "handed_to_police": True,
        })
        assert r.status_code == 200 and r.json()["status"] == "handed_to_police"
        assert (await c.post(f"/api/v1/lost-found/{item['id']}/dispose",
                             json={"disposal_method": "Binned"})).status_code == 409


@pytest.mark.asyncio
async def test_search_matches_description_and_location():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        item = await _log_item(c, site_id, description="Blue golf umbrella")
        await _log_item(c, site_id, description="Set of car keys")

        r = await c.get("/api/v1/lost-found?search=umbrella")
        ids = [i["id"] for i in r.json()]
        assert item["id"] in ids and len(ids) == 1


# ─── D. Retention ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_fresh_item_is_not_due_for_disposal():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        item = await _log_item(c, site_id)
        r = await c.get(f"/api/v1/lost-found/{item['id']}")
        assert r.json()["due_for_disposal"] is False
        assert r.json()["retention_days"] == 90


@pytest.mark.asyncio
async def test_an_item_past_the_retention_period_is_reported():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        item = await _log_item(c, site_id)
        await _exec([("UPDATE lost_found_items SET found_at = now() - interval '120 days' "
                      "WHERE id = :id", {"id": uuid.UUID(item["id"])})])

        r = await c.get(f"/api/v1/lost-found?site_id={site_id}&overdue_only=true")
        assert [i["id"] for i in r.json()] == [item["id"]]
        assert r.json()[0]["due_for_disposal"] is True

        r = await c.get(f"/api/v1/lost-found/summary?site_id={site_id}")
        assert r.json()["due_for_disposal"] == 1 and r.json()["held"] == 1


@pytest.mark.asyncio
async def test_tenant_can_shorten_the_retention_period():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    await _exec([
        ("INSERT INTO tenant_settings "
         "  (tenant_id, setting_key, setting_value, updated_by_user_id) "
         "VALUES (:tid, 'lostfound.retention_days', '30'::jsonb, :uid)",
         {"tid": tenant_id, "uid": user_id}),
    ])
    async with await _authed(token) as c:
        item = await _log_item(c, site_id)
        await _exec([("UPDATE lost_found_items SET found_at = now() - interval '45 days' "
                      "WHERE id = :id", {"id": uuid.UUID(item["id"])})])
        r = await c.get(f"/api/v1/lost-found/summary?site_id={site_id}")
        assert r.json()["retention_days"] == 30
        assert r.json()["due_for_disposal"] == 1


# ─── E. Permissions, scoping and RLS ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_guard_can_issue_but_not_maintain_the_cabinet():
    tenant_id, _, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    _, guard_token = await _seed_user(tenant_id, 5)

    async with await _authed(admin_token) as admin:
        key = await _create_key(admin, site_id)
    async with await _authed(guard_token) as guard:
        assert (await _issue(guard, key["id"])).status_code == 201
        r = await guard.post("/api/v1/keys", json={
            "site_id": str(site_id), "key_code": "NEW-1", "label": "Nope",
        })
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_guard_can_log_property_but_not_dispose_of_it():
    tenant_id, _, _ = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    _, guard_token = await _seed_user(tenant_id, 5)
    async with await _authed(guard_token) as guard:
        item = await _log_item(guard, site_id)
        r = await guard.post(f"/api/v1/lost-found/{item['id']}/dispose",
                             json={"disposal_method": "Binned"})
        assert r.status_code == 403
        # Releasing to a claimant is the counter job, and is allowed.
        r = await guard.post(f"/api/v1/lost-found/{item['id']}/release",
                             json={"claimed_by_name": "Owner"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_viewer_reads_both_registers_and_changes_neither():
    tenant_id, _, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    _, viewer_token = await _seed_user(tenant_id, 6, "Control Room")
    async with await _authed(admin_token) as admin:
        key = await _create_key(admin, site_id)
    async with await _authed(viewer_token) as viewer:
        assert (await viewer.get("/api/v1/keys")).status_code == 200
        assert (await viewer.get("/api/v1/lost-found")).status_code == 200
        assert (await _issue(viewer, key["id"])).status_code == 403
        assert (await viewer.post("/api/v1/lost-found",
                                  json={"description": "x"})).status_code == 403


@pytest.mark.asyncio
async def test_site_scoping_hides_keys_at_unassigned_sites():
    tenant_id, _, admin_token = await _seed_tenant()
    mine = await _seed_site(tenant_id, "Assigned")
    theirs = await _seed_site(tenant_id, "Not assigned")
    guard_id, guard_token = await _seed_user(tenant_id, 5)
    await _assign_site(tenant_id, guard_id, mine)

    async with await _authed(admin_token) as admin:
        ours = await _create_key(admin, mine, code="MINE-1")
        other = await _create_key(admin, theirs, code="THEIRS-1")

    async with await _authed(guard_token) as guard:
        ids = [k["id"] for k in (await guard.get("/api/v1/keys")).json()]
        assert ours["id"] in ids and other["id"] not in ids
        assert (await guard.get(f"/api/v1/keys/{other['id']}")).status_code == 404
        assert (await _issue(guard, other["id"])).status_code == 404


@pytest.mark.asyncio
async def test_site_scoping_hides_property_at_unassigned_sites():
    tenant_id, _, admin_token = await _seed_tenant()
    mine = await _seed_site(tenant_id, "Assigned")
    theirs = await _seed_site(tenant_id, "Not assigned")
    guard_id, guard_token = await _seed_user(tenant_id, 5)
    await _assign_site(tenant_id, guard_id, mine)

    async with await _authed(admin_token) as admin:
        other = await _log_item(admin, theirs, description="Not this guard's site")

    async with await _authed(guard_token) as guard:
        assert (await guard.get(f"/api/v1/lost-found/{other['id']}")).status_code == 404
        r = await guard.post("/api/v1/lost-found",
                             json={"description": "x", "site_id": str(theirs)})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_another_tenant_cannot_see_these_registers():
    tenant_a, _, token_a = await _seed_tenant()
    site_a = await _seed_site(tenant_a)
    _, _, token_b = await _seed_tenant()

    async with await _authed(token_a) as a:
        key = await _create_key(a, site_a)
        item = await _log_item(a, site_a)

    async with await _authed(token_b) as b:
        assert (await b.get(f"/api/v1/keys/{key['id']}")).status_code == 404
        assert (await b.get(f"/api/v1/lost-found/{item['id']}")).status_code == 404
        assert (await b.get("/api/v1/keys")).json() == []


# ─── F. Handover integration ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handover_reports_keys_still_out():
    """A register nobody is forced to look at is a page nobody opens."""
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO shifts (id, tenant_id, guard_user_id, site_id, scheduled_start, "
         " scheduled_end, status) VALUES (:id, :tid, :uid, :sid, now(), "
         " now() + interval '8 hours', 'in_progress')",
         {"id": shift_id, "tid": tenant_id, "uid": user_id, "sid": site_id}),
    ])
    async with await _authed(token) as c:
        key = await _create_key(c, site_id)
        await _issue(c, key["id"], name="M&E contractor")
        await _log_item(c, site_id)

        r = await c.post(f"/api/v1/shifts/{shift_id}/handover")
        assert r.status_code == 200
        assert r.json()["keys_outstanding"] == 1
        assert r.json()["lost_found_held"] == 1


@pytest.mark.asyncio
async def test_briefing_tells_the_incoming_guard_which_keys_are_out():
    tenant_id, user_id, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    shift_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO shifts (id, tenant_id, guard_user_id, site_id, scheduled_start, "
         " scheduled_end, status) VALUES (:id, :tid, :uid, :sid, now(), "
         " now() + interval '8 hours', 'scheduled')",
         {"id": shift_id, "tid": tenant_id, "uid": user_id, "sid": site_id}),
    ])
    async with await _authed(token) as c:
        key = await _create_key(c, site_id, code="RISER-3", label="Riser room")
        await _issue(c, key["id"], name="M&E contractor")

        r = await c.get(f"/api/v1/shifts/{shift_id}/briefing")
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["keys_outstanding"] == 1
        assert body["outstanding_keys"][0]["key_code"] == "RISER-3"
        assert body["outstanding_keys"][0]["held_by_name"] == "M&E contractor"
