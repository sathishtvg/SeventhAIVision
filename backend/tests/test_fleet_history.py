"""Fleet journey history, its export and its retention (GPS fleet upgrade, phase 4).

  A — A vehicle's day, read back as journeys, stops and a route
  B — Who may read it and who may export it
  C — How long positions and archived vehicles are kept

Every request goes through the real app over ASGI, as svc_app with RLS enforced;
the retention job runs on a superuser session, as the scheduler runs it.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.services import fleet_retention
from tests.test_drone_api import ADMIN, ADMIN_DATABASE_URL, GUARD, MANAGER, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_fleet_journeys import _day
from tests.test_investigation_search import _audit

BASE = "/api/v1/fleet"


async def _vehicle(c, w, plate: str, site: str = "site_a") -> dict:
    r = await c.post(f"{BASE}/vehicles", headers=w["h"][ADMIN],
                     json={"name": f"Car {plate}", "plate_number": plate, "site_id": str(w[site])})
    assert r.status_code == 201, r.text
    return r.json()


async def _positions(w, vehicle_id: str, fixes, shift: timedelta = timedelta(0)) -> None:
    rows = [("INSERT INTO vehicle_positions (tenant_id, vehicle_id, lat, lon, speed, accuracy, ignition, recorded_at) "
             "VALUES (:t, :v, :lat, :lon, :s, :a, :i, :at) ON CONFLICT (vehicle_id, recorded_at) DO NOTHING",
             {"t": w["tenant"], "v": vehicle_id, "lat": f.lat, "lon": f.lon, "s": f.speed, "a": f.accuracy,
              "i": f.ignition, "at": f.at + shift}) for f in fixes]
    await _run(rows)


async def _retention(now: datetime | None = None) -> dict:
    engine = create_async_engine(ADMIN_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as db:
            return await fleet_retention.run(db, now)
    finally:
        await engine.dispose()


# ─── A. A vehicle's day ──────────────────────────────────────────────────────

async def test_a_day_is_read_back_as_two_drives_two_stops_and_the_time_without_positions():
    w = await _world(licensed=False)
    async with _client() as c:
        v = await _vehicle(c, w, "HIS 1")
        fixes = _day()
        await _positions(w, v["id"], fixes)
        start, end = fixes[0].at - timedelta(minutes=1), fixes[-1].at + timedelta(minutes=1)
        r = await c.get(f"{BASE}/vehicles/{v['id']}/journeys", headers=w["h"][VIEWER],
                        params={"start": start.isoformat(), "end": end.isoformat()})
        assert r.status_code == 200, r.text
        h = r.json()
        assert h["enough"] is True and h["positions"] == len(fixes) - 1, "the fix sent twice at one moment is kept once"
        assert len(h["trips"]) == 2 and [round(s["minutes"]) for s in h["stops"]] == [5, 10]
        assert h["stops"][0]["ignition_off"] is True and len(h["gaps"]) == 1
        assert 10.0 < h["summary"]["distance_km"] < 10.8 and h["route_thinned"] is False
        assert h["left_out"] == {"inaccurate": 0, "impossible_jump": 0}
        # One position is not a journey; a range is a week at most and ends after it starts.
        lone = await c.get(f"{BASE}/vehicles/{v['id']}/journeys", headers=w["h"][VIEWER],
                           params={"start": start.isoformat(), "end": (start + timedelta(minutes=1, seconds=1)).isoformat()})
        assert lone.json()["enough"] is False and lone.json()["trips"] == []
        for bad in ((start, start), (start, start + timedelta(days=8))):
            assert (await c.get(f"{BASE}/vehicles/{v['id']}/journeys", headers=w["h"][VIEWER],
                                params={"start": bad[0].isoformat(), "end": bad[1].isoformat()})).status_code == 422


# ─── B. Who may read it and who may export it ────────────────────────────────

async def test_who_may_read_and_export_and_every_export_is_on_the_record():
    w = await _world(licensed=False)
    async with _client() as c:
        a, b = await _vehicle(c, w, "EXP A"), await _vehicle(c, w, "EXP B", site="site_b")
        fixes = _day()
        await _positions(w, a["id"], fixes)
        window = {"start": (fixes[0].at - timedelta(minutes=1)).isoformat(), "end": (fixes[-1].at + timedelta(minutes=1)).isoformat()}
        assert (await c.get(f"{BASE}/vehicles/{a['id']}/journeys", headers=w["h"][GUARD], params=window)).status_code == 403
        assert (await c.get(f"{BASE}/vehicles/{b['id']}/journeys", headers=w["h"][SUPERVISOR], params=window)).status_code == 404
        assert (await c.get(f"{BASE}/vehicles/{a['id']}/journeys", headers=w["h"][SUPERVISOR], params=window)).status_code == 200
        assert (await c.get(f"{BASE}/vehicles/{a['id']}/journeys.csv", headers=w["h"][SUPERVISOR], params=window)).status_code == 403
        r = await c.get(f"{BASE}/vehicles/{a['id']}/journeys.csv", headers=w["h"][MANAGER], params={**window, "kind": "trips"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert 'filename="EXPA-trips-' in r.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
        assert rows[0][:3] == ["start (UTC)", "end (UTC)", "minutes"] and len(rows) == 3
        stops = await c.get(f"{BASE}/vehicles/{a['id']}/journeys.csv", headers=w["h"][ADMIN], params={**window, "kind": "stops"})
        assert len(list(csv.reader(io.StringIO(stops.text.lstrip("﻿"))))) == 3
    lines = await _audit(w, "fleet.history.exported")
    assert [x["detail"]["kind"] for x in lines] == ["trips", "stops"] and lines[0]["detail"]["rows"] == 2


# ─── C. How long positions and archived vehicles are kept ────────────────────

async def test_nothing_is_removed_until_a_period_is_set_and_then_only_what_is_older():
    w = await _world(licensed=False)
    now = datetime.now(timezone.utc)
    async with _client() as c:
        v = await _vehicle(c, w, "RET 1")
        await _positions(w, v["id"], _day(), shift=now - timedelta(days=60) - _day()[0].at)
        await _positions(w, v["id"], _day(), shift=now - timedelta(days=1) - _day()[0].at)
        total = (await _sql("SELECT count(*) AS n FROM vehicle_positions WHERE vehicle_id = :v", {"v": v["id"]}))[0]["n"]
        assert (await _retention())["removed"].get("FLEET_POSITIONS", 0) == 0, "no period: kept"
        url = f"{BASE}/retention/FLEET_POSITIONS"
        assert (await c.put(url, headers=w["h"][SUPERVISOR], json={"days": 30})).status_code == 403
        assert (await c.put(url, headers=w["h"][ADMIN], json={"days": 7})).status_code == 422, "thirty days at least"
        first = await c.put(url, headers=w["h"][ADMIN], json={"days": 30})
        assert first.status_code == 409 and first.json()["detail"]["already_older"] == total // 2
        r = await c.put(url, headers=w["h"][ADMIN], json={"days": 30, "already_older": total // 2})
        assert r.status_code == 200 and r.json()["days"] == 30
        kinds = {k["key"]: k for k in (await c.get(f"{BASE}/retention", headers=w["h"][VIEWER])).json()["kinds"]}
        assert kinds["FLEET_POSITIONS"]["days"] == 30 and kinds["ARCHIVED_VEHICLES"]["days"] is None
        statement = (await c.get("/api/v1/data-governance/retention", headers=w["h"][ADMIN])).json()
        assert [k["key"] for k in statement["fleet"]] == ["FLEET_POSITIONS", "ARCHIVED_VEHICLES"]
    result = await _retention()
    assert result["removed"]["FLEET_POSITIONS"] == total // 2
    left = (await _sql("SELECT count(*) AS n FROM vehicle_positions WHERE vehicle_id = :v", {"v": v["id"]}))[0]["n"]
    assert left == total - total // 2
    purge = (await _sql("SELECT detail FROM audit_logs WHERE tenant_id = :t AND action = 'retention.purge' "
                        "AND resource_type = 'fleet_retention'", {"t": w["tenant"]}))[0]["detail"]
    purge = purge if isinstance(purge, dict) else json.loads(purge)
    assert purge == {"removed": {"FLEET_POSITIONS": total // 2}, "days": {"FLEET_POSITIONS": 30}}


async def test_an_archived_vehicle_goes_with_its_history_unless_an_open_alert_still_refers_to_it():
    w = await _world(licensed=False)
    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    async with _client() as c:
        gone, held = await _vehicle(c, w, "OLD 1"), await _vehicle(c, w, "OLD 2")
        for v in (gone, held):
            await _positions(w, v["id"], _day()[:5], shift=long_ago - _day()[0].at)
            await c.post(f"{BASE}/vehicles/{v['id']}/archive", headers=w["h"][ADMIN], json={"reason": "Sold"})
        await _run([
            ("UPDATE vehicles SET archived_at = :at WHERE id = ANY(CAST(:ids AS uuid[]))",
             {"at": long_ago, "ids": [gone["id"], held["id"]]}),
            ("INSERT INTO vehicle_journeys (tenant_id, vehicle_id) VALUES (:t, :v)", {"t": w["tenant"], "v": gone["id"]}),
            ("INSERT INTO alerts (tenant_id, module_type, severity, alert_code, title, message, status, message_params) "
             "VALUES (:t, 'gps', 'medium', 'gps.exit', 'Exit', 'Exit', 'acknowledged', CAST(:p AS jsonb))",
             {"t": w["tenant"], "p": json.dumps({"vehicle_id": held["id"]})}),
        ])
        r = await c.put(f"{BASE}/retention/ARCHIVED_VEHICLES", headers=w["h"][ADMIN], json={"days": 365})
        assert r.status_code == 409 and r.json()["detail"]["already_older"] == 1, "the one an alert refers to is not counted"
        await c.put(f"{BASE}/retention/ARCHIVED_VEHICLES", headers=w["h"][ADMIN], json={"days": 365, "already_older": 1})
    result = await _retention()
    assert result["removed"]["ARCHIVED_VEHICLES"] == 1
    left = {r["id"] for r in await _sql("SELECT CAST(id AS text) AS id FROM vehicles WHERE tenant_id = :t", {"t": w["tenant"]})}
    assert left == {held["id"]}
    for table in ("vehicle_positions", "vehicle_journeys"):
        assert (await _sql(f"SELECT count(*) AS n FROM {table} WHERE vehicle_id = :v", {"v": gone["id"]}))[0]["n"] == 0
    assert (await _sql("SELECT count(*) AS n FROM vehicle_positions WHERE vehicle_id = :v", {"v": held["id"]}))[0]["n"] == 5
    assert (await _sql("SELECT count(*) AS n FROM alerts WHERE tenant_id = :t", {"t": w["tenant"]}))[0]["n"] == 1, "alerts stay"
