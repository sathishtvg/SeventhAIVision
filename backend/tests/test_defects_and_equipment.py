"""The defect log and the equipment/uniform register (isolated-tenant).

Tests backend/app/routers/defects.py and backend/app/routers/equipment.py.

Key semantics:
  - A defect's status ladder stops where a security company's control does:
    open -> reported -> in_progress -> resolved | closed
  - Re-referring keeps the FIRST referral time; the building reassigns work and
    the reference number changes with it
  - Closing without a fix requires a reason, so "resolved" keeps meaning fixed
  - A resolved or closed defect is read-only — the reference number has to keep
    pointing at what was reported
  - Equipment is out because an assignment row is open, and the database
    refuses a second one; condition on return writes through to the item
  - Kit marked lost or damaged cannot be issued
  - Uniforms are quantity rows with a PARTIAL return count, because three of
    four shirts coming back is the normal case
  - /assigned/{user_id} answers "what is this officer holding" across both
  - Site scoping (Gap 81) applies; pool kit with no site stays visible

Sections:
  A — Defect lifecycle (7 tests)
  B — Defect reporting and scoping (5 tests)
  C — Equipment (7 tests)
  D — Uniforms (5 tests)
  E — What an officer holds (2 tests)
  F — Permissions and RLS (5 tests)
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
    slug = f"de-{tenant_id.hex[:10]}"
    await _exec([
        ("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)",
         {"id": tenant_id, "name": f"Defect Test {slug}", "slug": slug}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:id, :tid, :role, :email, 'hashed', 'DE Tester')",
         {"id": user_id, "tid": tenant_id, "role": role_id,
          "email": f"de-{user_id.hex[:8]}@test.local"}),
    ])
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_user(tenant_id, role_id: int, name: str = "DE Guard"):
    from app.core.security import create_access_token

    user_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:id, :tid, :role, :email, 'hashed', :name)",
         {"id": user_id, "tid": tenant_id, "role": role_id, "name": name,
          "email": f"de-{user_id.hex[:8]}@test.local"}),
    ])
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id, name: str = "DE Site") -> uuid.UUID:
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


async def _report(c: AsyncClient, site_id, description="Stairwell light out",
                  category="lighting", severity="medium") -> dict:
    r = await c.post("/api/v1/defects", json={
        "site_id": str(site_id), "description": description,
        "category": category, "severity": severity,
    })
    assert r.status_code == 201, f"report failed: {r.text}"
    return r.json()


async def _create_item(c: AsyncClient, site_id=None, code="RAD-01",
                       name="Handheld radio", category="radio") -> dict:
    payload = {"asset_code": code, "name": name, "category": category}
    if site_id:
        payload["site_id"] = str(site_id)
    r = await c.post("/api/v1/equipment", json=payload)
    assert r.status_code == 201, f"create_item failed: {r.text}"
    return r.json()


# ─── A. Defect lifecycle ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_defect_starts_open_and_reads_back():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        d = await _report(c, site_id)
        assert d["status"] == "open"
        r = await c.get(f"/api/v1/defects/{d['id']}")
        assert r.status_code == 200
        assert r.json()["reported_by_name"] == "DE Tester"
        assert r.json()["has_photo"] is False


@pytest.mark.asyncio
async def test_referring_records_the_buildings_own_ticket_number():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        d = await _report(c, site_id)
        r = await c.post(f"/api/v1/defects/{d['id']}/refer", json={
            "referred_to": "Marina Bay FM desk", "reference_no": "FM-2026-0412",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "reported"
        assert r.json()["reference_no"] == "FM-2026-0412"


@pytest.mark.asyncio
async def test_rereferring_keeps_the_first_referral_time():
    """The building reassigns the job to a contractor and the reference number
    changes; how long it has been with them does not restart."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        d = await _report(c, site_id)
        first = (await c.post(f"/api/v1/defects/{d['id']}/refer",
                              json={"referred_to": "FM desk"})).json()
        second = (await c.post(f"/api/v1/defects/{d['id']}/refer", json={
            "referred_to": "Sunlite Electrical", "reference_no": "SE-8891",
            "in_progress": True,
        })).json()
        assert second["status"] == "in_progress"
        assert second["referred_at"] == first["referred_at"]
        assert second["reference_no"] == "SE-8891"


@pytest.mark.asyncio
async def test_resolving_twice_conflicts_and_editing_afterwards_is_refused():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        d = await _report(c, site_id)
        r = await c.post(f"/api/v1/defects/{d['id']}/resolve",
                         json={"resolution_notes": "Tubes replaced"})
        assert r.status_code == 200 and r.json()["status"] == "resolved"

        assert (await c.post(f"/api/v1/defects/{d['id']}/resolve",
                             json={})).status_code == 409
        # The reference number has to keep pointing at what was reported.
        assert (await c.put(f"/api/v1/defects/{d['id']}",
                            json={"description": "Rewritten"})).status_code == 409


@pytest.mark.asyncio
async def test_closing_without_a_fix_requires_a_reason():
    """Otherwise "resolved" and "closed" both collapse into "somebody clicked
    the button", and the client asks which."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        d = await _report(c, site_id)
        r = await c.post(f"/api/v1/defects/{d['id']}/resolve",
                         json={"close_without_fix": True})
        assert r.status_code == 422

        r = await c.post(f"/api/v1/defects/{d['id']}/resolve", json={
            "close_without_fix": True,
            "resolution_notes": "Duplicate of the report filed on the morning shift",
        })
        assert r.status_code == 200 and r.json()["status"] == "closed"


@pytest.mark.asyncio
async def test_summary_counts_hazards_and_ageing_separately():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        hazard = await _report(c, site_id, severity="safety_hazard")
        old = await _report(c, site_id, description="Slow leak", severity="low")
        await _exec([("UPDATE facility_defects SET reported_at = now() - interval '21 days' "
                      "WHERE id = :id", {"id": uuid.UUID(old["id"])})])

        r = await c.get(f"/api/v1/defects/summary?site_id={site_id}")
        body = r.json()
        assert body["open"] == 2
        assert body["open_safety_hazards"] == 1
        assert body["ageing"] == 1

        # Fixing the hazard removes it from the hazard count but not the log.
        await c.post(f"/api/v1/defects/{hazard['id']}/resolve", json={})
        body = (await c.get(f"/api/v1/defects/summary?site_id={site_id}")).json()
        assert body["open_safety_hazards"] == 0 and body["resolved"] == 1


@pytest.mark.asyncio
async def test_list_puts_open_hazards_first():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        await _report(c, site_id, description="Low priority", severity="low")
        hazard = await _report(c, site_id, description="Hazard", severity="safety_hazard")
        done = await _report(c, site_id, description="Already fixed", severity="high")
        await c.post(f"/api/v1/defects/{done['id']}/resolve", json={})

        rows = (await c.get(f"/api/v1/defects?site_id={site_id}")).json()
        assert rows[0]["id"] == hazard["id"]
        assert rows[-1]["id"] == done["id"]


# ─── B. Defect reporting and scoping ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_category_and_severity_are_rejected():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        assert (await c.post("/api/v1/defects", json={
            "site_id": str(site_id), "description": "x", "category": "asteroid",
        })).status_code == 422
        assert (await c.post("/api/v1/defects", json={
            "site_id": str(site_id), "description": "x", "severity": "catastrophic",
        })).status_code == 422


@pytest.mark.asyncio
async def test_reported_at_cannot_be_in_the_future():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        r = await c.post("/api/v1/defects", json={
            "site_id": str(site_id), "description": "x", "reported_at": future,
        })
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_unknown_field_is_rejected():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/defects", json={
            "site_id": str(site_id), "description": "x", "prioriy": "high",
        })
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_guard_reports_but_does_not_refer_or_resolve():
    tenant_id, _, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    _, guard_token = await _seed_user(tenant_id, 5)
    async with await _authed(guard_token) as guard:
        d = await _report(guard, site_id)
        assert (await guard.post(f"/api/v1/defects/{d['id']}/refer",
                                 json={"referred_to": "FM"})).status_code == 403
        assert (await guard.post(f"/api/v1/defects/{d['id']}/resolve",
                                 json={})).status_code == 403
    async with await _authed(admin_token) as admin:
        assert (await admin.post(f"/api/v1/defects/{d['id']}/refer",
                                 json={"referred_to": "FM"})).status_code == 200


@pytest.mark.asyncio
async def test_site_scoping_hides_defects_at_unassigned_sites():
    tenant_id, _, admin_token = await _seed_tenant()
    mine = await _seed_site(tenant_id, "Assigned")
    theirs = await _seed_site(tenant_id, "Not assigned")
    guard_id, guard_token = await _seed_user(tenant_id, 5)
    await _assign_site(tenant_id, guard_id, mine)

    async with await _authed(admin_token) as admin:
        ours = await _report(admin, mine)
        other = await _report(admin, theirs)

    async with await _authed(guard_token) as guard:
        ids = [d["id"] for d in (await guard.get("/api/v1/defects")).json()]
        assert ours["id"] in ids and other["id"] not in ids
        assert (await guard.get(f"/api/v1/defects/{other['id']}")).status_code == 404
        r = await guard.post("/api/v1/defects",
                             json={"site_id": str(theirs), "description": "x"})
        assert r.status_code == 404


# ─── C. Equipment ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_asset_codes_are_unique_across_the_tenant():
    """Unlike key codes, which are stencilled per site — company kit is
    numbered once by the company."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "A")
    site_b = await _seed_site(tenant_id, "B")
    async with await _authed(token) as c:
        await _create_item(c, site_a, code="RAD-01")
        r = await c.post("/api/v1/equipment", json={
            "site_id": str(site_b), "asset_code": "RAD-01", "name": "Same code",
        })
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_issue_then_item_shows_the_holder():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    guard_id, _ = await _seed_user(tenant_id, 5, "Tan Wei Ming")
    async with await _authed(token) as c:
        item = await _create_item(c, site_id)
        r = await c.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(guard_id),
            "purpose": "Night shift",
        })
        assert r.status_code == 201

        body = (await c.get(f"/api/v1/equipment/{item['id']}")).json()
        assert body["is_out"] is True
        assert body["held_by_name"] == "Tan Wei Ming"


@pytest.mark.asyncio
async def test_kit_cannot_be_issued_twice():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    a, _ = await _seed_user(tenant_id, 5, "First")
    b, _ = await _seed_user(tenant_id, 5, "Second")
    async with await _authed(token) as c:
        item = await _create_item(c, site_id)
        assert (await c.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(a),
        })).status_code == 201
        r = await c.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(b),
        })
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_condition_on_return_writes_through_to_the_item():
    """The next person issued the torch should not have to read the assignment
    history to find out it is broken."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    guard_id, _ = await _seed_user(tenant_id, 5)
    async with await _authed(token) as c:
        item = await _create_item(c, site_id)
        assign = (await c.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(guard_id),
        })).json()

        r = await c.post(f"/api/v1/equipment/assignments/{assign['id']}/receive",
                         json={"condition_on_return": "damaged",
                               "return_notes": "Belt clip snapped"})
        assert r.status_code == 200

        body = (await c.get(f"/api/v1/equipment/{item['id']}")).json()
        assert body["condition"] == "damaged"
        assert body["is_out"] is False
        assert len(body["history"]) == 1

        # And damaged kit cannot go straight back out.
        r = await c.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(guard_id),
        })
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_receiving_twice_conflicts():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    guard_id, _ = await _seed_user(tenant_id, 5)
    async with await _authed(token) as c:
        item = await _create_item(c, site_id)
        assign = (await c.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(guard_id),
        })).json()
        assert (await c.post(
            f"/api/v1/equipment/assignments/{assign['id']}/receive", json={},
        )).status_code == 200
        assert (await c.post(
            f"/api/v1/equipment/assignments/{assign['id']}/receive", json={},
        )).status_code == 409


@pytest.mark.asyncio
async def test_kit_that_is_out_cannot_be_retired():
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    guard_id, _ = await _seed_user(tenant_id, 5)
    async with await _authed(token) as c:
        item = await _create_item(c, site_id)
        assign = (await c.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(guard_id),
        })).json()
        assert (await c.put(f"/api/v1/equipment/{item['id']}",
                            json={"is_active": False})).status_code == 409
        await c.post(f"/api/v1/equipment/assignments/{assign['id']}/receive", json={})
        assert (await c.put(f"/api/v1/equipment/{item['id']}",
                            json={"is_active": False})).status_code == 200


@pytest.mark.asyncio
async def test_pool_kit_with_no_site_stays_visible_to_a_scoped_user():
    """A radio issued from head office belongs to nobody's site. Hiding it from
    site-assigned staff would mean the pool is invisible to everyone who uses
    it."""
    tenant_id, _, admin_token = await _seed_tenant()
    mine = await _seed_site(tenant_id, "Assigned")
    theirs = await _seed_site(tenant_id, "Not assigned")
    guard_id, guard_token = await _seed_user(tenant_id, 5)
    await _assign_site(tenant_id, guard_id, mine)

    async with await _authed(admin_token) as admin:
        pool = await _create_item(admin, None, code="POOL-1", name="Pool radio")
        theirs_item = await _create_item(admin, theirs, code="THEIRS-1")

    async with await _authed(guard_token) as guard:
        ids = [i["id"] for i in (await guard.get("/api/v1/equipment")).json()]
        assert pool["id"] in ids
        assert theirs_item["id"] not in ids


# ─── D. Uniforms ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uniform_is_issued_by_quantity():
    tenant_id, _, token = await _seed_tenant()
    guard_id, _ = await _seed_user(tenant_id, 5)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/equipment/uniforms", json={
            "user_id": str(guard_id), "item_type": "shirt", "size": "L",
            "quantity": 4, "deposit_amount": "80.00",
        })
        assert r.status_code == 201
        assert r.json()["quantity"] == 4 and r.json()["returned_quantity"] == 0


@pytest.mark.asyncio
async def test_partial_uniform_return_is_recorded_as_partial():
    """Three of four shirts back is the normal case on a resignation, and a
    boolean would force somebody to pick a lie."""
    tenant_id, _, token = await _seed_tenant()
    guard_id, _ = await _seed_user(tenant_id, 5)
    async with await _authed(token) as c:
        issue = (await c.post("/api/v1/equipment/uniforms", json={
            "user_id": str(guard_id), "item_type": "shirt", "quantity": 4,
        })).json()

        r = await c.post(f"/api/v1/equipment/uniforms/{issue['id']}/return",
                         json={"returned_quantity": 3, "notes": "Fourth lost"})
        assert r.status_code == 200
        assert r.json()["outstanding_quantity"] == 1
        assert r.json()["returned_at"] is None

        r = await c.post(f"/api/v1/equipment/uniforms/{issue['id']}/return",
                         json={"returned_quantity": 4})
        assert r.json()["outstanding_quantity"] == 0
        assert r.json()["returned_at"] is not None


@pytest.mark.asyncio
async def test_cannot_return_more_uniform_than_was_issued():
    tenant_id, _, token = await _seed_tenant()
    guard_id, _ = await _seed_user(tenant_id, 5)
    async with await _authed(token) as c:
        issue = (await c.post("/api/v1/equipment/uniforms", json={
            "user_id": str(guard_id), "item_type": "shirt", "quantity": 2,
        })).json()
        r = await c.post(f"/api/v1/equipment/uniforms/{issue['id']}/return",
                         json={"returned_quantity": 5})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_uniform_rejects_unknown_type_and_zero_quantity():
    tenant_id, _, token = await _seed_tenant()
    guard_id, _ = await _seed_user(tenant_id, 5)
    async with await _authed(token) as c:
        assert (await c.post("/api/v1/equipment/uniforms", json={
            "user_id": str(guard_id), "item_type": "spacesuit",
        })).status_code == 422
        assert (await c.post("/api/v1/equipment/uniforms", json={
            "user_id": str(guard_id), "item_type": "shirt", "quantity": 0,
        })).status_code == 422


@pytest.mark.asyncio
async def test_uniform_list_can_show_only_what_is_outstanding():
    tenant_id, _, token = await _seed_tenant()
    guard_id, _ = await _seed_user(tenant_id, 5)
    async with await _authed(token) as c:
        settled = (await c.post("/api/v1/equipment/uniforms", json={
            "user_id": str(guard_id), "item_type": "cap", "quantity": 1,
        })).json()
        await c.post(f"/api/v1/equipment/uniforms/{settled['id']}/return",
                     json={"returned_quantity": 1})
        still_out = (await c.post("/api/v1/equipment/uniforms", json={
            "user_id": str(guard_id), "item_type": "jacket", "quantity": 1,
        })).json()

        rows = (await c.get(
            f"/api/v1/equipment/uniforms?user_id={guard_id}&outstanding_only=true",
        )).json()
        assert [r["id"] for r in rows] == [still_out["id"]]


# ─── E. What an officer holds ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assigned_to_user_answers_kit_and_uniform_in_one_call():
    """The screen a supervisor opens on somebody's last day. Two registers is
    how a body camera walks out of the building."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    guard_id, _ = await _seed_user(tenant_id, 5, "Leaver")
    async with await _authed(token) as c:
        item = await _create_item(c, site_id, code="CAM-1", name="Body camera",
                                  category="bodycam")
        await c.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(guard_id),
        })
        await c.post("/api/v1/equipment/uniforms", json={
            "user_id": str(guard_id), "item_type": "jacket", "quantity": 1,
            "deposit_amount": "50.00",
        })

        body = (await c.get(f"/api/v1/equipment/assigned/{guard_id}")).json()
        assert body["user"]["full_name"] == "Leaver"
        assert body["summary"]["equipment_out"] == 1
        assert body["summary"]["uniform_pieces_out"] == 1
        assert float(body["summary"]["deposit_held"]) == 50.0


@pytest.mark.asyncio
async def test_assigned_to_an_unknown_person_is_404():
    _, _, token = await _seed_tenant()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/equipment/assigned/{uuid.uuid4()}")
        assert r.status_code == 404


# ─── F. Permissions and RLS ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guard_reads_the_equipment_register_but_cannot_issue():
    tenant_id, _, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    guard_id, guard_token = await _seed_user(tenant_id, 5)
    async with await _authed(admin_token) as admin:
        item = await _create_item(admin, site_id)
    async with await _authed(guard_token) as guard:
        assert (await guard.get("/api/v1/equipment")).status_code == 200
        assert (await guard.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(guard_id),
        })).status_code == 403
        assert (await guard.post("/api/v1/equipment", json={
            "asset_code": "X-1", "name": "Nope",
        })).status_code == 403


@pytest.mark.asyncio
async def test_operator_can_issue_kit_but_not_maintain_inventory():
    """The control room hands out radios at shift start; buying them is not
    their job."""
    tenant_id, _, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    guard_id, _ = await _seed_user(tenant_id, 5)
    _, operator_token = await _seed_user(tenant_id, 4, "Control Room")
    async with await _authed(admin_token) as admin:
        item = await _create_item(admin, site_id)
    async with await _authed(operator_token) as operator:
        assert (await operator.post("/api/v1/equipment/issue", json={
            "item_id": item["id"], "assigned_to_user_id": str(guard_id),
        })).status_code == 201
        assert (await operator.post("/api/v1/equipment", json={
            "asset_code": "X-2", "name": "Nope",
        })).status_code == 403


@pytest.mark.asyncio
async def test_viewer_reads_both_and_changes_neither():
    tenant_id, _, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    _, viewer_token = await _seed_user(tenant_id, 6, "Control Room")
    async with await _authed(admin_token) as admin:
        await _create_item(admin, site_id)
    async with await _authed(viewer_token) as viewer:
        assert (await viewer.get("/api/v1/defects")).status_code == 200
        assert (await viewer.get("/api/v1/equipment")).status_code == 200
        assert (await viewer.post("/api/v1/defects", json={
            "site_id": str(site_id), "description": "x",
        })).status_code == 403


@pytest.mark.asyncio
async def test_client_sees_the_defect_log_and_writes_nothing():
    """What your officers found at their building is one of the few things a
    security contract is judged on."""
    tenant_id, _, admin_token = await _seed_tenant()
    site_id = await _seed_site(tenant_id)
    client_id, client_token = await _seed_user(tenant_id, 7, "Property Manager")
    await _assign_site(tenant_id, client_id, site_id)

    async with await _authed(admin_token) as admin:
        d = await _report(admin, site_id)

    async with await _authed(client_token) as client:
        rows = (await client.get("/api/v1/defects")).json()
        assert [r["id"] for r in rows] == [d["id"]]
        assert (await client.post("/api/v1/defects", json={
            "site_id": str(site_id), "description": "x",
        })).status_code == 403
        assert (await client.post(f"/api/v1/defects/{d['id']}/resolve",
                                  json={})).status_code == 403
        # And nothing about the company's own kit.
        assert (await client.get("/api/v1/equipment")).status_code == 403


@pytest.mark.asyncio
async def test_another_tenant_sees_neither_register():
    tenant_a, _, token_a = await _seed_tenant()
    site_a = await _seed_site(tenant_a)
    _, _, token_b = await _seed_tenant()

    async with await _authed(token_a) as a:
        d = await _report(a, site_a)
        item = await _create_item(a, site_a)

    async with await _authed(token_b) as b:
        assert (await b.get(f"/api/v1/defects/{d['id']}")).status_code == 404
        assert (await b.get(f"/api/v1/equipment/{item['id']}")).status_code == 404
        assert (await b.get("/api/v1/defects")).json() == []
        assert (await b.get("/api/v1/equipment")).json() == []
