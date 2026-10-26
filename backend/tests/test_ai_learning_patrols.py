"""The AI module, phase 4: roster suggestions, missed patrols, patrol times and guard trends.

Sections:
  A — Roster suggestions (2 tests)
  B — Missed-patrol warnings (1 test)
  C — Best patrol times (1 test)
  D — Guard trends (2 tests)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.main import app  # noqa: F401  (module level: the ML import must not land inside a test)
from app.services import ai_guard_trends, ai_patrol_timing
from tests.test_ai_learning import (
    NOW, SGT, _admin_session_run, _late_history, _night, _run, _shift, _sql, _world,
)


# ─── A. Roster suggestions ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_free_punctual_guard_who_knows_the_site_is_put_forward_for_the_late_guards_shift():
    w = await _world(features=("roster_fit",), enable=("roster_fit",))
    tonight = await _late_history(w)
    # Prompt Ong is on approved leave tonight, so only Prompt Tan is free (Prompt Raj works tonight himself).
    lt = uuid.uuid4()
    await _run([
        ("INSERT INTO leave_types (id, tenant_id, name) VALUES (:i,:t,'Annual')", {"i": lt, "t": w["tenant"]}),
        ("INSERT INTO leave_requests (tenant_id, guard_user_id, leave_type_id, start_date, end_date, days_count, status) "
         "VALUES (:t,:g,:lt,:a,:a,1,'approved')",
         {"t": w["tenant"], "g": w["guards"]["Prompt Ong"], "lt": lt, "a": NOW.astimezone(SGT).date()}),
    ])
    done = await _admin_session_run(w)
    assert done["roster_fit"]["model"] == "READY", done
    rows = await _sql("SELECT subject_id, title, detail, evidence, reasons FROM ai_suggestions "
                      " WHERE tenant_id = :t AND feature_key = 'roster_fit'", {"t": w["tenant"]})
    assert [r["subject_id"] for r in rows] == [tonight["Late Lim"]]
    r = rows[0]
    assert r["evidence"]["alternative_user_id"] == str(w["guards"]["Prompt Tan"])
    assert "Prompt Tan may be a better fit than Late Lim" in r["title"]
    assert r["evidence"]["rostered_chance"] - r["evidence"]["alternative_chance"] >= 0.20
    assert "has worked at Jurong" in r["reasons"][1]["words"] and "free then" in r["reasons"][1]["words"]
    assert "The roster is not changed" in r["detail"]
    shift = await _sql("SELECT guard_user_id FROM shifts WHERE id = :s", {"s": tonight["Late Lim"]})
    assert shift[0]["guard_user_id"] == w["guards"]["Late Lim"], "the roster was changed"


@pytest.mark.asyncio
async def test_nobody_is_put_forward_when_every_other_guard_is_busy_or_away():
    w = await _world(features=("roster_fit",), enable=("roster_fit",))
    await _late_history(w)
    await _run([_shift(w, name, _night(0) + timedelta(hours=1), status="scheduled")
                for name in ("Prompt Tan", "Prompt Ong")])
    await _admin_session_run(w)
    assert await _sql("SELECT 1 FROM ai_suggestions WHERE tenant_id = :t AND feature_key = 'roster_fit'",
                      {"t": w["tenant"]}) == []


# ─── B. Missed patrols ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_often_missed_patrol_due_tonight_is_warned_about_and_the_reliable_one_is_not():
    w = await _world(features=("patrol_miss_risk",), enable=("patrol_miss_risk",))
    sched = {n: uuid.uuid4() for n in ("Back Gate round", "Lobby round")}
    stmts = [("INSERT INTO virtual_patrol_schedules (id, tenant_id, site_id, name, schedule_type, start_date, "
              "  patrol_time) VALUES (:i,:t,:s,:n,'DAILY',:d,:pt)",
              {"i": i, "t": w["tenant"], "s": w["site_a"], "n": n, "d": date(2026, 1, 1), "pt": time(22, 0)})
             for n, i in sched.items()]

    def session(name, day, status):
        sid = uuid.uuid4()
        return sid, ("INSERT INTO virtual_patrol_sessions (id, tenant_id, site_id, schedule_id, patrol_number, "
                     "  schedule_name, scheduled_for, status) VALUES (:i,:t,:s,:sc,:pn,:n,:at,:st)",
                     {"i": sid, "t": w["tenant"], "s": w["site_a"], "sc": sched[name], "pn": f"VP-{sid.hex[:8]}",
                      "n": name, "at": _night(day), "st": status})
    for n in range(10):
        stmts.append(session("Back Gate round", -30 + 3 * n, "MISSED" if n % 5 else "COMPLETED")[1])
    for n in range(20):
        stmts.append(session("Lobby round", -30 + n, "COMPLETED")[1])
    due = {}
    for name in sched:
        sid, st = session(name, 0, "SCHEDULED")
        due[name] = sid
        stmts.append(st)
    await _run(stmts)
    done = await _admin_session_run(w)
    assert done["patrol_miss_risk"]["model"] == "READY", done
    rows = await _sql("SELECT subject_id, subject_type, title, reasons FROM ai_suggestions "
                      " WHERE tenant_id = :t AND feature_key = 'patrol_miss_risk'", {"t": w["tenant"]})
    assert [r["subject_id"] for r in rows] == [due["Back Gate round"]]
    assert rows[0]["subject_type"] == "virtual_patrol"
    assert "Back Gate round (virtual patrol) due at 22:00" in rows[0]["title"] and "may be missed" in rows[0]["title"]
    assert any("of this schedule's last" in r["words"] for r in rows[0]["reasons"])


# ─── C. Best patrol times ────────────────────────────────────────────────────

def test_the_busiest_window_wraps_past_midnight():
    hours = [0] * 24
    for h in (22, 23, 0, 1):
        hours[h] = 5
    assert ai_patrol_timing.busiest_window(hours) == 22
    assert ai_patrol_timing.window_share(hours, 22) == 1.0


@pytest.mark.asyncio
async def test_a_site_whose_problems_come_at_night_but_whose_patrols_come_in_the_morning_is_pointed_out():
    w = await _world(features=("patrol_timing",), enable=("patrol_timing",))
    cam = uuid.uuid4()
    stmts = [("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Gate')",
              {"i": cam, "t": w["tenant"], "s": w["site_a"]})]
    for n in range(12):
        at = datetime(2026, 5, 20, 23, 0, tzinfo=SGT).astimezone(timezone.utc) + timedelta(days=n)
        stmts.append(("INSERT INTO incidents (tenant_id, camera_id, title, created_at) VALUES (:t,:c,'Intrusion',:at)",
                      {"t": w["tenant"], "c": cam, "at": at}))
    for n in range(10):
        at = datetime(2026, 5, 20, 10, 0, tzinfo=SGT).astimezone(timezone.utc) + timedelta(days=n)
        stmts.append(("INSERT INTO virtual_patrol_sessions (tenant_id, site_id, patrol_number, schedule_name, "
                      "  scheduled_for, started_at, status) VALUES (:t,:s,:pn,'Morning',:at,:at,'COMPLETED')",
                      {"t": w["tenant"], "s": w["site_a"], "pn": f"VP-{n}-{cam.hex[:6]}", "at": at}))
    await _run(stmts)
    done = await _admin_session_run(w)
    assert done["patrol_timing"]["model"] == "READY", done
    rows = await _sql("SELECT title, evidence FROM ai_suggestions WHERE tenant_id = :t AND feature_key = 'patrol_timing'",
                      {"t": w["tenant"]})
    assert len(rows) == 1
    assert rows[0]["evidence"]["problem_share"] == 1.0 and rows[0]["evidence"]["patrol_share"] == 0.0
    assert "At Jurong, 100% of problems happen" in rows[0]["title"] and "but 0% of patrols do" in rows[0]["title"]


# ─── D. Guard trends ─────────────────────────────────────────────────────────

def test_a_clear_drop_is_measured_and_a_rise_is_not():
    assert ai_guard_trends.z_drop(10, 10, 5, 10) == pytest.approx(2.582, abs=0.01)
    assert ai_guard_trends.z_drop(5, 10, 10, 10) == 0.0
    assert ai_guard_trends.z_drop(0, 0, 5, 10) == 0.0


@pytest.mark.asyncio
async def test_the_guard_whose_punctuality_fell_is_raised_for_a_conversation_and_the_steady_one_is_not():
    w = await _world(features=("guard_trends",), enable=("guard_trends",), guards=("Slipping Sam", "Steady Sue"))
    stmts = []
    for name in ("Slipping Sam", "Steady Sue"):
        for n in range(10):  # the 8 weeks before the last 4: always on time
            stmts.append(_shift(w, name, _night(-80 + 5 * n)))
        for n in range(10):  # the last 4 weeks: Sam late half the time
            late = name == "Slipping Sam" and n % 2 == 0
            stmts.append(_shift(w, name, _night(-27 + 2 * n), late_min=30 if late else None))
    await _run(stmts)
    done = await _admin_session_run(w)
    assert done["guard_trends"]["model"] == "READY", done
    rows = await _sql("SELECT person_user_id, title, detail, reasons FROM ai_suggestions "
                      " WHERE tenant_id = :t AND feature_key = 'guard_trends'", {"t": w["tenant"]})
    assert [r["person_user_id"] for r in rows] == [w["guards"]["Slipping Sam"]]
    assert "Slipping Sam's punctuality fell from 100% to 50% in the last 4 weeks" == rows[0]["title"]
    assert "Nothing about the guard is changed, scored or kept against them" in rows[0]["detail"]
    model = await _sql("SELECT fitted FROM ai_models WHERE tenant_id = :t AND feature_key = 'guard_trends'",
                       {"t": w["tenant"]})
    assert set(model[0]["fitted"]) == {"comparable"}, "nothing per guard is kept in the model"
