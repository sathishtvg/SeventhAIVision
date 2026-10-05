"""How long drone footage and flight tracks are kept.

  A — Which footage goes and which stays, file and record
  B — Each organisation's own period, and nobody else's footage
  C — Flight tracks, and the flight record that outlives them
  D — When a file will not delete; when there is nothing to do

Run as the application's own database role, on two organisations, because that
is how the runner runs it and where a tenant scope lost after a commit shows.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.core.config import settings
from app.core.config_keys import SETTING_VALIDATORS
from app.db.session import AsyncSessionLocal
from app.services import drone_retention as retention
from tests.test_drone_ai_pipeline import _last
from tests.test_drone_ai_pipeline import _world as _ai_world
from tests.test_drone_api import ADMIN
from tests.test_drone_api import _world as _roles_world
from tests.test_drone_edge_sync import _client, _run, _sql
from tests.test_drone_reports import _flight_with_event, _snapshot_for

NOW = datetime.now(timezone.utc)


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture
def evidence(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "EVIDENCE_ROOT", str(tmp_path))
    return tmp_path


async def _event(w: dict, *, status: str = "RESOLVED", verification: str = "VERIFIED") -> uuid.UUID:
    eid = uuid.uuid4()
    await _sql("INSERT INTO drone_events (id, tenant_id, site_id, module_type, detected_at, risk_level, status, "
               "    verification_state) VALUES (:i,:t,:s,'intrusion',:at,'HIGH',:st,:v)",
               {"i": eid, "t": w["tenant"], "s": w["site_a"], "at": _ago(120), "st": status, "v": verification})
    return eid


async def _flight(w: dict, *, status: str = "COMPLETED") -> uuid.UUID:
    sid = uuid.uuid4()
    await _sql("INSERT INTO drone_patrol_sessions (id, tenant_id, session_number, site_id, mission_name, drone_name, "
               "    status, distance_m) VALUES (:i,:t,:n,:s,'Night Watch','Drone One',:st,412.5)",
               {"i": sid, "t": w["tenant"], "n": f"DP-{sid.hex[:10]}", "s": w["site_a"], "st": status})
    return sid


async def _media(w: dict, evidence: Path, *, event=None, session=None, days: float = 100,
                 location: str = "central", kind: str = "SNAPSHOT") -> dict:
    """One file in the evidence store and its record, captured `days` ago."""
    mid = uuid.uuid4()
    rel = f"drone/{w['tenant']}/old/{mid.hex}.jpg"
    payload = b"\xff\xd8\xff\xe0" + mid.bytes * 20 + b"\xff\xd9"
    if location == "central":
        (evidence / rel).parent.mkdir(parents=True, exist_ok=True)
        (evidence / rel).write_bytes(payload)
    await _sql("INSERT INTO drone_event_media (id, tenant_id, event_id, session_id, media_kind, storage_path, "
               "    storage_location, sync_state, checksum_sha256, size_bytes, captured_at) "
               "VALUES (:i,:t,:e,:s,:k,:p,:loc,:sync,:sum,:n,:at)",
               {"i": mid, "t": w["tenant"], "e": event, "s": session, "k": kind, "p": rel, "loc": location,
                "sync": "synced" if location == "central" else "pending",
                "sum": hashlib.sha256(payload).hexdigest(), "n": len(payload), "at": _ago(days)})
    return {"id": mid, "path": evidence / rel, "size": len(payload)}


async def _kept(media: dict) -> bool:
    return bool(await _sql("SELECT 1 FROM drone_event_media WHERE id = :i", {"i": media["id"]}))


async def _purge(tenant) -> dict:
    """As the runner does it: the application's role, scoped to one organisation."""
    async with AsyncSessionLocal() as db:
        who = (await db.execute(text(
            "SELECT current_user, (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"))).first()
        assert who[1] is False, f"retention ran as {who[0]!r}, which bypasses row level security"
        return await retention.purge_tenant(db, str(tenant), NOW)


async def _audit(tenant) -> list[dict]:
    rows = await _sql("SELECT user_id, detail FROM audit_logs WHERE tenant_id = :t AND action = 'drone.retention.purge' "
                      " ORDER BY created_at", {"t": tenant})
    return [json.loads(r["detail"]) if isinstance(r["detail"], str) else dict(r["detail"]) for r in rows]


# ─── A. Which footage goes ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_old_footage_nobody_acted_on_goes_and_everything_else_stays(evidence):
    w = await _roles_world()
    gone = {
        "of a resolved event": await _media(w, evidence, event=await _event(w, status="RESOLVED")),
        "of a false positive": await _media(w, evidence, event=await _event(w, status="FALSE_POSITIVE"), kind="CLIP"),
        "of a sighting never confirmed": await _media(
            w, evidence, event=await _event(w, status="NEW", verification="UNVERIFIED")),
        "routine, attached to no event": await _media(w, evidence, session=await _flight(w)),
    }
    at_site = await _media(w, evidence, event=await _event(w), location="local")
    stays = {
        "of a confirmed event still open": await _media(
            w, evidence, event=await _event(w, status="INVESTIGATING", verification="VERIFIED")),
        "not yet past the period": await _media(w, evidence, event=await _event(w), days=30),
        "of a flight still in the air": await _media(w, evidence, session=await _flight(w, status="ACTIVE")),
    }

    result = await _purge(w["tenant"])

    for what, m in gone.items():
        assert not await _kept(m), f"footage {what} was kept"
        assert not m["path"].exists(), f"the file {what} is still on disk"
    for what, m in stays.items():
        assert await _kept(m), f"footage {what} was deleted"
        assert m["path"].exists(), f"the file {what} was deleted"
    # A file the site still holds was never here to delete; its record goes.
    assert not await _kept(at_site)
    assert result["media_deleted"] == 5 and result["media_failed"] == 0
    assert result["bytes_freed"] == sum(m["size"] for m in gone.values())

    [entry] = await _audit(w["tenant"])
    assert entry["media_deleted"] == 5 and entry["footage_kept_days"] == settings.EVIDENCE_RETENTION_DAYS
    # The events themselves are records, and stay.
    assert (await _sql("SELECT count(*) AS n FROM drone_events WHERE tenant_id = :t", {"t": w["tenant"]}))[0]["n"] == 6

    again = await _purge(w["tenant"])
    assert again["media_deleted"] == 0 and len(await _audit(w["tenant"])) == 1, "a night with nothing to do is not logged"


@pytest.mark.asyncio
async def test_footage_of_an_incident_is_kept_whatever_its_age(evidence):
    """The reason drone media was kept out of the platform's own purge, which
    deletes by age alone."""
    w = await _ai_world()
    _, event = await _flight_with_event(w, _last(2, 17))
    assert event["incident_id"], "this flight should have raised an incident"
    rel = await _snapshot_for(w, event, evidence)
    await _sql("UPDATE drone_event_media SET captured_at = :at WHERE event_id = :e", {"at": _ago(900), "e": event["id"]})
    await _sql("UPDATE drone_events SET status = 'RESOLVED' WHERE id = :e", {"e": event["id"]})

    result = await _purge(w["tenant"])

    assert result["media_deleted"] == 0
    assert (evidence / rel).exists()
    assert (await _sql("SELECT count(*) AS n FROM drone_event_media WHERE event_id = :e", {"e": event["id"]}))[0]["n"] == 1


# ─── B. Each organisation's own period ───────────────────────────────────────

@pytest.mark.asyncio
async def test_each_organisation_keeps_footage_for_its_own_period(evidence):
    short, standard = await _roles_world(), await _roles_world()
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
               "VALUES (:t,'evidence.retention_days','30',:u)", {"t": short["tenant"], "u": short["users"][ADMIN]})
    theirs = await _media(short, evidence, event=await _event(short), days=40)
    ours = await _media(standard, evidence, event=await _event(standard), days=40)

    totals = await retention.run_retention(AsyncSessionLocal, NOW)

    assert not await _kept(theirs), "forty days old, in an organisation that keeps thirty"
    assert await _kept(ours) and ours["path"].exists(), "forty days old, in an organisation that keeps ninety"
    assert totals["media_deleted"] >= 1
    [entry] = await _audit(short["tenant"])
    assert entry["footage_kept_days"] == 30
    assert await _audit(standard["tenant"]) == []


# ─── C. Flight tracks ────────────────────────────────────────────────────────

async def _track(w: dict, drone: uuid.UUID, session: uuid.UUID, days: float, n: int = 3) -> None:
    await _run([("INSERT INTO drone_telemetry (tenant_id, drone_id, session_id, recorded_at, latitude, longitude) "
                 "VALUES (:t,:d,:s,:at,1.3,103.8)",
                 {"t": w["tenant"], "d": drone, "s": session, "at": _ago(days) + timedelta(seconds=i)})
                for i in range(n)])


async def _samples(session: uuid.UUID) -> int:
    return (await _sql("SELECT count(*) AS n FROM drone_telemetry WHERE session_id = :s", {"s": session}))[0]["n"]


@pytest.mark.asyncio
async def test_a_flights_track_goes_after_its_period_and_the_flight_record_stays(evidence):
    a, b = await _roles_world(), await _roles_world()
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
               "VALUES (:t,'drone.telemetry_retention_days','200',:u)", {"t": b["tenant"], "u": b["users"][ADMIN]})
    flights = {}
    for w in (a, b):
        drone = uuid.uuid4()
        await _sql("INSERT INTO drones (id, tenant_id, site_id, name, code, status) VALUES (:i,:t,:s,'Drone One','D-01','READY')",
                   {"i": drone, "t": w["tenant"], "s": w["site_a"]})
        flights[w["tenant"]] = {days: await _flight(w) for days in (400, 300, 20)}
        for days, sid in flights[w["tenant"]].items():
            await _track(w, drone, sid, days)

    await retention.run_retention(AsyncSessionLocal, NOW)

    year, shorter = flights[a["tenant"]], flights[b["tenant"]]
    assert [await _samples(year[d]) for d in (400, 300, 20)] == [0, 3, 3], "a year is the default"
    assert [await _samples(shorter[d]) for d in (400, 300, 20)] == [0, 0, 3], "this organisation keeps 200 days"
    [entry] = await _audit(a["tenant"])
    assert entry["telemetry_deleted"] == 3 and entry["tracks_kept_days"] == retention.DEFAULT_TELEMETRY_DAYS
    assert (await _audit(b["tenant"]))[0]["telemetry_deleted"] == 6

    # The flight is still there, with what it measured, and still reports.
    old = (await _sql("SELECT status, distance_m FROM drone_patrol_sessions WHERE id = :s", {"s": year[400]}))[0]
    assert old["status"] == "COMPLETED" and float(old["distance_m"]) == 412.5
    async with _client() as c:
        report = await c.get(f"/api/v1/drone-patrols/{year[400]}/report", headers=a["h"][ADMIN])
        pdf = await c.get(f"/api/v1/drone-patrols/{year[400]}/report/pdf", headers=a["h"][ADMIN])
    assert report.status_code == 200 and report.json()["route"]["track"] == []
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_the_track_period_is_a_setting_an_organisation_can_change():
    assert "drone.telemetry_retention_days" in SETTING_VALIDATORS
    SETTING_VALIDATORS["drone.telemetry_retention_days"](180)
    for bad in (-1, "180", 1.5, True):
        with pytest.raises(ValueError):
            SETTING_VALIDATORS["drone.telemetry_retention_days"](bad)


@pytest.mark.asyncio
async def test_a_period_of_zero_means_the_default_not_delete_everything(evidence):
    """The platform's validator accepts 0. For something that cannot be undone,
    "keep for no days" is read as "not set", never as "delete it all tonight"."""
    w = await _roles_world()
    for key in ("evidence.retention_days", "drone.telemetry_retention_days"):
        await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
                   "VALUES (:t,:k,'0',:u)", {"t": w["tenant"], "k": key, "u": w["users"][ADMIN]})
    recent = await _media(w, evidence, event=await _event(w), days=5)
    drone, flight = uuid.uuid4(), await _flight(w)
    await _sql("INSERT INTO drones (id, tenant_id, site_id, name, code, status) VALUES (:i,:t,:s,'Drone One','D-01','READY')",
               {"i": drone, "t": w["tenant"], "s": w["site_a"]})
    await _track(w, drone, flight, days=5)

    result = await _purge(w["tenant"])

    assert result == {"media_deleted": 0, "bytes_freed": 0, "media_failed": 0, "telemetry_deleted": 0}
    assert await _kept(recent) and recent["path"].exists() and await _samples(flight) == 3


# ─── D. When it cannot ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_file_that_will_not_delete_keeps_its_record(evidence):
    """The record is the only thing that still knows the file exists."""
    w = await _roles_world()
    stuck = await _media(w, evidence, event=await _event(w))
    fine = await _media(w, evidence, event=await _event(w))

    async def refuses_one(path: str) -> None:
        if stuck["path"].name in path:
            raise PermissionError("the disk says no")
        await retention.delete_stored_file(path)

    async with AsyncSessionLocal() as db:
        result = await retention.purge_tenant(db, str(w["tenant"]), NOW, refuses_one)

    assert result["media_deleted"] == 1 and result["media_failed"] == 1
    assert await _kept(stuck) and stuck["path"].exists()
    assert not await _kept(fine) and not fine["path"].exists()


@pytest.mark.asyncio
async def test_a_path_outside_the_evidence_store_is_never_deleted(evidence, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "keep.txt"
    outside.write_text("not ours")
    await retention.delete_stored_file(f"../{outside.parent.name}/keep.txt")
    await retention.delete_stored_file(str(outside))
    assert outside.exists()
