"""Overtime requests and approval, the check-out flag, and pay (docs/plans/2026-10-25-roster-autoclose-overtime-design.md,
sections 3-5, agreed with the owner on 25 October 2026).

A guard asks for extra hours on their own shift (while on, or up to 48 h after); a supervisor or admin approves or
rejects (a rejection needs a reason); only approved overtime is paid, never more than the time worked past the
planned end. A check-out away from the site goes through and is flagged. Through the real endpoints, as the
restricted app user.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.routers import shifts as shifts_router
from app.routers.payroll import _TIMESHEET_HOURS_SQL
from app.services import staff_faces
from tests.test_shifts import _admin_engine

SITE_LAT, SITE_LON = 1.3521, 103.8198
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


async def _sql(statements):
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        for sql, params in statements:
            await s.execute(text(sql), params)
        await s.commit()
    await engine.dispose()


async def _one(sql, params):
    engine = _admin_engine()
    async with engine.connect() as conn:
        row = (await conn.execute(text(sql), params)).mappings().first()
    await engine.dispose()
    return row


async def _org() -> dict:
    from app.core.security import create_access_token

    t, site = uuid.uuid4(), uuid.uuid4()
    people = {"admin": (uuid.uuid4(), 2), "sup": (uuid.uuid4(), 3), "guard": (uuid.uuid4(), 5), "other": (uuid.uuid4(), 5)}
    statements = [
        ("INSERT INTO tenants (id, name, slug) VALUES (:id, 'OT', :s)", {"id": t, "s": f"ot-{t.hex[:8]}"}),
        ("INSERT INTO sites (id, tenant_id, name, latitude, longitude, geofence_radius_meters) "
         "VALUES (:id, :t, 'Gate', :la, :lo, 200)", {"id": site, "t": t, "la": SITE_LAT, "lo": SITE_LON}),
    ]
    for name, (uid, role) in people.items():
        statements.append(("INSERT INTO users (id, tenant_id, role_id, email, full_name, hashed_password) "
                           "VALUES (:id, :t, :r, :e, :n, 'x')",
                           {"id": uid, "t": t, "r": role, "e": f"{name}-{uid.hex[:6]}@t.local", "n": name.title()}))
    await _sql(statements)
    out = {name: {"id": str(uid), "token": create_access_token(str(uid), str(t), role)}
           for name, (uid, role) in people.items()}
    out["tenant"], out["site"] = str(t), str(site)
    return out


async def _shift(org, *, status="active", started_ago=timedelta(hours=1), planned=timedelta(hours=12),
                 ended_ago: timedelta | None = None, needs_approval=True, who="guard") -> str:
    sid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    start = now - started_ago
    await _sql([("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
                 "actual_end, status, overtime_needs_approval) VALUES (:id, :t, :s, :g, :ss, :se, :as, :ae, :st, :na)",
                 {"id": sid, "t": org["tenant"], "s": org["site"], "g": org[who]["id"], "ss": start, "se": start + planned,
                  "as": start, "ae": (now - ended_ago) if ended_ago is not None else None, "st": status,
                  "na": needs_approval})])
    return str(sid)


def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.mark.asyncio
async def test_a_guard_asks_once_on_their_own_shift_and_only_in_time():
    org = await _org()
    on = await _shift(org)
    async with _client(org["guard"]["token"]) as g:
        r = await g.post("/api/v1/overtime-requests", json={"shift_id": on, "hours": 2.5, "reason": "Relief is late"})
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "pending" and r.json()["hours_requested"] == 2.5
        r = await g.post("/api/v1/overtime-requests", json={"shift_id": on, "hours": 1, "reason": "Again"})
        assert r.status_code == 409 and "waiting" in r.json()["detail"]
        r = await g.post("/api/v1/overtime-requests", json={"shift_id": on, "hours": 1, "reason": "   "})
        assert r.status_code in (409, 422)
        long_ago = await _shift(org, status="completed", started_ago=timedelta(days=4), ended_ago=timedelta(days=3))
        r = await g.post("/api/v1/overtime-requests", json={"shift_id": long_ago, "hours": 1, "reason": "Forgot"})
        assert r.status_code == 409 and "48 hours" in r.json()["detail"]
        recent = await _shift(org, status="completed", started_ago=timedelta(hours=20), ended_ago=timedelta(hours=6))
        r = await g.post("/api/v1/overtime-requests", json={"shift_id": recent, "hours": 1, "reason": "Forgot to ask"})
        assert r.status_code == 201
        r = await g.post("/api/v1/overtime-requests", json={"shift_id": on, "hours": 13, "reason": "Too much"})
        assert r.status_code == 422
    async with _client(org["other"]["token"]) as o:
        r = await o.post("/api/v1/overtime-requests", json={"shift_id": on, "hours": 1, "reason": "Not mine"})
        assert r.status_code == 403
        assert (await o.get("/api/v1/overtime-requests")).json() == [], "a guard sees only their own"


@pytest.mark.asyncio
async def test_a_supervisor_decides_a_rejection_needs_a_reason_and_a_guard_cannot_decide():
    org = await _org()
    on = await _shift(org)
    async with _client(org["guard"]["token"]) as g:
        req = (await g.post("/api/v1/overtime-requests", json={"shift_id": on, "hours": 2, "reason": "Incident"})).json()
        assert (await g.post(f"/api/v1/overtime-requests/{req['id']}/approve")).status_code == 403
    async with _client(org["sup"]["token"]) as s:
        listed = (await s.get("/api/v1/overtime-requests", params={"request_status": "pending"})).json()
        assert [x["id"] for x in listed] == [req["id"]] and listed[0]["guard_name"] == "Guard"
        r = await s.post(f"/api/v1/overtime-requests/{req['id']}/reject", json={"note": ""})
        assert r.status_code == 422 and "why" in r.json()["detail"]
        r = await s.post(f"/api/v1/overtime-requests/{req['id']}/approve")
        assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decided_by_name"] == "Sup"
        r = await s.post(f"/api/v1/overtime-requests/{req['id']}/reject", json={"note": "Changed my mind"})
        assert r.status_code == 409 and "already approved" in r.json()["detail"]
    async with _client(org["guard"]["token"]) as g:
        req2 = (await g.post("/api/v1/overtime-requests", json={"shift_id": on, "hours": 1, "reason": "More"})).json()
    async with _client(org["admin"]["token"]) as a:
        r = await a.post(f"/api/v1/overtime-requests/{req2['id']}/reject", json={"note": "Relief arrived"})
        assert r.status_code == 200 and r.json()["decision_note"] == "Relief arrived"
    audit = await _one("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = CAST(:t AS uuid) AND action LIKE 'overtime.%'",
                       {"t": org["tenant"]})
    assert audit["n"] == 4, "two requests, two decisions"


@pytest.mark.asyncio
async def test_approving_a_closed_shift_pays_what_was_approved_up_to_the_time_worked():
    org = await _org()
    # 12 h planned, worked 15 h: 3 h past the planned end, closed 2 h ago.
    closed = await _shift(org, status="completed", started_ago=timedelta(hours=17), ended_ago=timedelta(hours=2))
    async with _client(org["guard"]["token"]) as g:
        big = (await g.post("/api/v1/overtime-requests", json={"shift_id": closed, "hours": 5, "reason": "Stayed"})).json()
    async with _client(org["sup"]["token"]) as s:
        extra = (await s.get("/api/v1/overtime-requests/unapproved-extra")).json()
        assert [round(x["unapproved_extra_minutes"]) for x in extra if x["shift_id"] == closed] == [180]
        await s.post(f"/api/v1/overtime-requests/{big['id']}/approve")
    row = await _one("SELECT overtime_minutes FROM shifts WHERE id = CAST(:s AS uuid)", {"s": closed})
    assert row["overtime_minutes"] == 180, "approved 5 h, worked 3 h past the end: 3 h"
    async with _client(org["sup"]["token"]) as s:
        assert [x for x in (await s.get("/api/v1/overtime-requests/unapproved-extra")).json() if x["shift_id"] == closed] == []


@pytest.mark.asyncio
async def test_paid_hours_stop_at_the_planned_end_plus_approved_overtime_and_old_shifts_are_unchanged():
    org = await _org()
    approved = await _shift(org, status="completed", started_ago=timedelta(hours=16), ended_ago=timedelta(hours=1))
    await _sql([("UPDATE shifts SET overtime_minutes = 60 WHERE id = CAST(:s AS uuid)", {"s": approved})])
    nothing = await _shift(org, status="completed", started_ago=timedelta(hours=16), ended_ago=timedelta(hours=1),
                           who="other")
    old = await _shift(org, status="completed", started_ago=timedelta(hours=40), ended_ago=timedelta(hours=25),
                       needs_approval=False, who="sup")
    await _sql([("UPDATE shifts SET overtime_minutes = 165 WHERE id = CAST(:s AS uuid)", {"s": old})])
    today = datetime.now(timezone.utc).date()
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": org["tenant"]})

        async def hours(who):
            r = (await db.execute(text(_TIMESHEET_HOURS_SQL), {"gid": org[who]["id"], "ps": today - timedelta(days=3),
                                                              "pe": today})).mappings().first()
            return round(float(r["total_hours"]), 2), round(float(r["ot_hours"]), 2)

        assert await hours("guard") == (13.0, 1.0), "12 h planned + 1 h approved; the other 2 h are not paid"
        assert await hours("other") == (12.0, 0.0), "nothing approved: the planned 12 h"
        assert await hours("sup") == (15.0, 2.75), "a shift from before: check-in to check-out, as always"


@pytest.mark.asyncio
async def test_check_out_flags_away_from_site_and_uses_the_one_overtime_rule(monkeypatch, tmp_path):
    org = await _org()
    monkeypatch.setattr(settings, "ATTENDANCE_PHOTOS_ROOT", str(tmp_path))
    monkeypatch.setattr(shifts_router, "check_liveness_sync", lambda _b: 0.99)

    async def no_face_check(db, **kw):
        return {}

    monkeypatch.setattr(shifts_router, "_face_check", no_face_check)
    now = datetime.now(timezone.utc)
    away = uuid.uuid4()
    inside = uuid.uuid4()
    await _sql([("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, status) "
                 "VALUES (:id, :t, :s, :g, :ss, :se, 'scheduled')",
                 {"id": sid, "t": org["tenant"], "s": org["site"], "g": org["guard"]["id"], "ss": now - timedelta(minutes=1),
                  "se": now + timedelta(hours=8)}) for sid in (away, inside)])
    photo = {"photo": ("s.jpg", io.BytesIO(JPEG), "image/jpeg")}
    async with _client(org["guard"]["token"]) as g:
        for sid, lat in ((away, SITE_LAT + 0.05), (inside, SITE_LAT)):
            r = await g.post(f"/api/v1/shifts/{sid}/start", files={"photo": ("s.jpg", io.BytesIO(JPEG), "image/jpeg")},
                             params={"latitude": SITE_LAT, "longitude": SITE_LON, "is_mock_location": "false"})
            assert r.status_code == 200, r.text
            r = await g.post(f"/api/v1/shifts/{sid}/end", files={"photo": ("s.jpg", io.BytesIO(JPEG), "image/jpeg")},
                             params={"latitude": lat, "longitude": SITE_LON, "is_mock_location": "false"})
            assert r.status_code == 200, r.text
    a = await _one("SELECT checked_out_away_from_site, overtime_needs_approval, overtime_minutes FROM shifts "
                   "WHERE id = CAST(:s AS uuid)", {"s": str(away)})
    i = await _one("SELECT checked_out_away_from_site FROM shifts WHERE id = CAST(:s AS uuid)", {"s": str(inside)})
    assert a["checked_out_away_from_site"] is True and i["checked_out_away_from_site"] is False
    assert a["overtime_needs_approval"] is True, "a shift checked in now follows the approval rule"
    assert a["overtime_minutes"] == 0
    async with _client(org["sup"]["token"]) as s:
        listed = {x["id"]: x for x in (await s.get("/api/v1/shifts")).json()}
        assert listed[str(away)]["checked_out_away_from_site"] is True and "auto_closed_at" in listed[str(away)]
