"""Gaps in what the platform records itself (Phase 6 of the known limits, 4 November 2026).

A. The stretches with no usable recording: none, a gap, one too short to count, a piece that holds nothing, a file
   missing, one still going on; and when a stream was meant to be recording.
B. The switch: the database notes each time "record all the time" is switched on or off, and nothing else; the
   application cannot write a note itself.
C. The pass, as the application's role: a gap between two pieces kept once with "the camera was offline then"; a time
   switched off is not a gap; a gap still going on, then closed; nothing before watching began; and a gap over the
   limit makes the camera Degraded.
D. Keeping: checks and gaps older than the device health history period are removed, 90 days when it is not set,
   and a stream's latest check never.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.main import app  # noqa: F401 - imported first, so the first test does not absorb the import
from app.db.session import AsyncSessionLocal
from app.services import recording_gaps as gaps
from tests.test_drone_api import ADMIN, _client, _sql, _world

T0 = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _piece(a, b, status="completed", size=1000, checksum=None):
    return {"started_at": _at(a), "ended_at": None if b is None else _at(b), "status": status,
            "file_size_bytes": size, "checksum_status": checksum}


# ─── A. The stretches with no usable recording ──────────────────────────────

def test_pieces_end_to_end_leave_no_gap_and_the_moment_between_two_is_not_one():
    pieces = [_piece(0, 15), _piece(15.02, 30), _piece(30.03, None, status="recording")]
    assert gaps.unrecorded(_at(0), _at(40), pieces, open_at_end=True) == []


def test_a_gap_between_pieces_one_that_holds_nothing_and_a_file_missing_are_each_found():
    pieces = [_piece(0, 10), _piece(12, 20), _piece(20, 25, size=0), _piece(25, 30, status="failed", size=None),
              _piece(30, 40, checksum="FILE_MISSING"), _piece(40, 50)]
    found = gaps.unrecorded(_at(0), _at(50), pieces, open_at_end=False)
    assert [(g["kind"], g["started_at"], g["ended_at"], g["seconds"]) for g in found] == [
        ("NOT_RECORDED", _at(10), _at(12), 120),
        ("FILE_MISSING", _at(20), _at(40), 1200),
    ], "a piece that holds nothing, then one that failed, then a missing file: one stretch, said as the worst"
    only_empty = gaps.unrecorded(_at(0), _at(30), [_piece(0, 10), _piece(10, 20, size=0), _piece(20, 30)],
                                 open_at_end=False)
    assert [(g["kind"], g["seconds"]) for g in only_empty] == [("EMPTY", 600)]


def test_a_gap_still_going_on_has_no_end_and_one_that_stopped_being_meant_ends_there():
    still = gaps.unrecorded(_at(0), _at(30), [_piece(0, 20)], open_at_end=True)
    assert still == [{"started_at": _at(20), "ended_at": None, "kind": "NOT_RECORDED", "seconds": 600}]
    stopped = gaps.unrecorded(_at(0), _at(30), [_piece(0, 20)], open_at_end=False)
    assert stopped[0]["ended_at"] == _at(30)
    assert gaps.unrecorded(_at(0), _at(30), [_piece(0, 29.9)], open_at_end=True) == [], "six seconds is not a gap"


def _row(**over):
    base = {"switch_on": True, "camera_on": True, "policy_records": True, "first_watched_at": _at(-60),
            "others_ok_since": _at(-60), "seen_at": _at(-15), "last_on_at": _at(-120), "last_off_at": None}
    return {**base, **over}


def test_a_stream_is_meant_to_be_recording_from_the_latest_of_switched_on_seen_on_and_first_watched():
    assert gaps.expected(_row(), _at(0)) == (_at(-60), _at(0), True, _at(-60))
    assert gaps.expected(_row(last_on_at=_at(-5)), _at(0))[0] == _at(-5), "switched on five minutes ago"
    assert gaps.expected(_row(first_watched_at=None), _at(0)) is None, "first seen now: watching begins"
    # The camera was switched off since the last pass: up to when it was last seen on, never beyond.
    assert gaps.expected(_row(camera_on=False), _at(0)) == (_at(-60), _at(-15), False, None)
    # The site's policy stopped asking for recording before the last pass: nothing to look at.
    assert gaps.expected(_row(policy_records=False, others_ok_since=None), _at(0)) is None
    # The switch went off since the last pass: to the moment.
    off = _row(switch_on=False, last_off_at=_at(-7))
    assert gaps.expected(off, _at(0)) == (_at(-60), _at(-7), False, _at(-60))
    assert gaps.expected(_row(switch_on=False, last_off_at=_at(-30)), _at(0)) is None, "it was off at the last pass"


# ─── B. The switch ──────────────────────────────────────────────────────────

async def _camera(w, *, recording=True, site="site_a") -> tuple[uuid.UUID, uuid.UUID]:
    cam, stream = uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:c, :t, :s, 'Loading bay')",
               {"c": cam, "t": w["tenant"], "s": w[site]})
    await _sql("INSERT INTO streams (id, tenant_id, camera_id, url, status, continuous_recording) "
               "VALUES (:i, :t, :c, 'rtsp://10.2.2.2/live', 'online', :r)",
               {"i": stream, "t": w["tenant"], "c": cam, "r": recording})
    return cam, stream


async def _switches(stream):
    return [r["switched_on"] for r in await _sql(
        "SELECT switched_on FROM recording_switches WHERE stream_id = :s ORDER BY at", {"s": stream})]


async def test_the_database_notes_each_switch_on_or_off_and_nothing_else():
    w = await _world()
    _, on = await _camera(w)
    _, off = await _camera(w, recording=False)
    assert await _switches(on) == [True] and await _switches(off) == []
    await _sql("UPDATE streams SET url = 'rtsp://10.2.2.3/live', status = 'degraded' WHERE id = :s", {"s": on})
    await _sql("UPDATE streams SET continuous_recording = TRUE WHERE id = :s", {"s": on})
    assert await _switches(on) == [True], "a change that is not the switch, or sets it as it was, is not noted"
    await _sql("UPDATE streams SET continuous_recording = FALSE WHERE id = :s", {"s": on})
    await _sql("UPDATE streams SET continuous_recording = TRUE WHERE id = :s", {"s": off})
    assert await _switches(on) == [True, False] and await _switches(off) == [True]
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        # The application switches it as it always has; the note is the database's own.
        await db.execute(text("UPDATE streams SET continuous_recording = FALSE WHERE id = :s"), {"s": off})
        await db.commit()
    assert await _switches(off) == [True, False]
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        try:
            await db.execute(text("INSERT INTO recording_switches (tenant_id, stream_id, camera_id, switched_on) "
                                  "SELECT tenant_id, id, camera_id, TRUE FROM streams WHERE id = :s"), {"s": off})
            raise AssertionError("the application wrote a note itself")
        except Exception as exc:
            assert "permission denied" in str(exc)
        await db.rollback()


# ─── C. The pass ────────────────────────────────────────────────────────────

async def _pass(w, now):
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles "
                                          "WHERE rolname = current_user"))).scalar()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        found = await gaps.watch_tenant(db, now)
        kept = await gaps.keep_period(db, now)
        await db.commit()
    return found, kept


async def _pieces(w, cam, stream, *spans):
    for a, b, status, size in spans:
        await _sql("INSERT INTO recordings (tenant_id, camera_id, stream_id, status, started_at, ended_at, "
                   "    file_size_bytes) VALUES (:t, :c, :s, :st, :a, :b, :size)",
                   {"t": w["tenant"], "c": cam, "s": stream, "st": status, "a": a, "b": b, "size": size})


async def _gaps(stream):
    return await _sql("SELECT kind, started_at, ended_at, seconds, camera_was_offline, over_limit FROM recording_gaps "
                      "WHERE stream_id = :s ORDER BY started_at", {"s": stream})


async def _watched_since(stream, since):
    await _sql("UPDATE recording_watch SET first_watched_at = :a, others_ok_since = :a, seen_at = :a "
               "WHERE stream_id = :s", {"a": since, "s": stream})
    await _sql("UPDATE recording_switches SET at = :a WHERE stream_id = :s", {"a": since, "s": stream})


async def test_a_gap_between_pieces_is_kept_once_with_the_camera_offline_then_and_makes_it_degraded():
    w = await _world()
    cam, stream = await _camera(w)
    now = datetime.now(timezone.utc)
    first, _ = await _pass(w, now - timedelta(minutes=40))
    assert first["gaps"] == 0, "the first pass begins watching and counts nothing"
    await _watched_since(stream, now - timedelta(minutes=40))
    await _pieces(w, cam, stream,
                  (now - timedelta(minutes=40), now - timedelta(minutes=25), "completed", 5000),
                  (now - timedelta(minutes=22), None, "recording", None))
    await _sql("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
               "VALUES (:t, :c, 'stream_disconnected', :at)", {"t": w["tenant"], "c": cam,
                                                               "at": now - timedelta(minutes=24)})
    for _ in range(2):
        await _pass(w, now)
    kept = await _gaps(stream)
    assert [(g["kind"], g["seconds"], g["camera_was_offline"], g["over_limit"]) for g in kept] == [
        ("NOT_RECORDED", 180, True, True)], "kept once, however often the pass runs"
    async with _client() as c:
        health = (await c.get("/api/v1/security-assets/health", headers=w["h"][ADMIN],
                              params={"kind": "CAMERA"})).json()["items"]
        week = (await c.get(f"/api/v1/camera-checks/cameras/{cam}", headers=w["h"][ADMIN])).json()
        latest = (await c.get("/api/v1/camera-checks/latest", headers=w["h"][ADMIN],
                              params={"camera_id": str(cam)})).json()["cameras"][0]
    assert health[0]["state"] == "DEGRADED"
    assert "1 recording gap longer than its limit in the last 24 hours." in health[0]["reasons"]
    assert week["gaps"][0]["kind_text"] == "Nothing was recorded" and week["gaps"][0]["camera_was_offline"] is True
    assert latest["gaps_24h"] == {"gaps": 1, "longest_s": 180, "open": False}


async def test_a_time_switched_off_is_not_a_gap_and_one_still_going_on_is_closed_when_recording_resumes():
    w = await _world()
    cam, stream = await _camera(w)
    now = datetime.now(timezone.utc)
    await _pass(w, now - timedelta(hours=2))
    await _watched_since(stream, now - timedelta(hours=2))
    await _pieces(w, cam, stream, (now - timedelta(hours=2), now - timedelta(minutes=100), "completed", 5000))
    # Switched off at 98 minutes ago, after the pass at 2 hours: the two minutes before it count, nothing after.
    await _sql("UPDATE streams SET continuous_recording = FALSE WHERE id = :s", {"s": stream})
    await _sql("UPDATE recording_switches SET at = :a WHERE stream_id = :s AND NOT switched_on",
               {"a": now - timedelta(minutes=98), "s": stream})
    await _pass(w, now - timedelta(minutes=90))
    await _pass(w, now - timedelta(minutes=60))
    assert [(g["seconds"], g["ended_at"] is not None) for g in await _gaps(stream)] == [(120, True)]
    # Switched on again 30 minutes ago; nothing recorded since: a gap still going on, from the switch.
    await _sql("UPDATE streams SET continuous_recording = TRUE WHERE id = :s", {"s": stream})
    await _sql("UPDATE recording_switches SET at = :a WHERE stream_id = :s AND switched_on AND at > :b",
               {"a": now - timedelta(minutes=30), "s": stream, "b": now - timedelta(minutes=95)})
    await _pass(w, now - timedelta(minutes=10))
    going = (await _gaps(stream))[-1]
    assert going["ended_at"] is None and going["started_at"] == now - timedelta(minutes=30) and going["over_limit"]
    async with _client() as c:
        latest = (await c.get("/api/v1/camera-checks/latest", headers=w["h"][ADMIN],
                              params={"camera_id": str(cam)})).json()["cameras"][0]
        health = (await c.get("/api/v1/security-assets/health", headers=w["h"][ADMIN],
                              params={"kind": "CAMERA"})).json()["items"][0]
    assert latest["gaps_24h"]["open"] is True
    assert "It is not recording now, for longer than its limit." in health["reasons"]
    await _pieces(w, cam, stream, (now - timedelta(minutes=5), None, "recording", None))
    await _pass(w, now)
    closed = (await _gaps(stream))[-1]
    assert closed["ended_at"] == now - timedelta(minutes=5) and closed["seconds"] == 25 * 60
    assert len(await _gaps(stream)) == 2


# ─── D. Keeping ─────────────────────────────────────────────────────────────

async def _check(w, cam, stream, days_ago):
    await _sql("INSERT INTO camera_checks (tenant_id, camera_id, stream_id, checked_at, result, frames) "
               "VALUES (:t, :c, :s, now() - make_interval(days => :d), 'OK', 10)",
               {"t": w["tenant"], "c": cam, "s": stream, "d": days_ago})


async def test_checks_and_gaps_are_kept_for_the_period_else_90_days_and_a_streams_latest_check_never():
    w = await _world()
    cam, stream = await _camera(w)
    lone_cam, lone = await _camera(w, recording=False)
    for days in (120, 95, 40, 1):
        await _check(w, cam, stream, days)
    await _check(w, lone_cam, lone, 200)
    await _sql("INSERT INTO recording_gaps (tenant_id, camera_id, stream_id, kind, started_at, ended_at, seconds, "
               "    limit_s, over_limit) VALUES (:t, :c, :s, 'NOT_RECORDED', now() - interval '100 days', "
               "    now() - interval '100 days' + interval '5 minutes', 300, 60, TRUE)",
               {"t": w["tenant"], "c": cam, "s": stream})

    async def left():
        return sorted(r["d"] for r in await _sql(
            "SELECT extract(day FROM now() - checked_at)::int AS d FROM camera_checks WHERE tenant_id = :t",
            {"t": w["tenant"]}))

    _, kept = await _pass(w, datetime.now(timezone.utc))
    assert kept == {"days": 90, "checks_removed": 2, "gaps_removed": 1}
    assert await left() == [1, 40, 200], "the lone stream's only check is its latest, and stays"
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
               "VALUES (:t, 'retention.device_health_changes_days', '30'::jsonb, :u)",
               {"t": w["tenant"], "u": w["users"][ADMIN]})
    _, kept = await _pass(w, datetime.now(timezone.utc))
    assert kept["days"] == 30 and await left() == [1, 200]
