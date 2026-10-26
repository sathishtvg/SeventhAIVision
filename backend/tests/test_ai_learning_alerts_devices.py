"""The AI module, phase 3: false-alarm learning, and devices likely to go down.

Sections:
  A — False alarms, from the operators' own verdicts (3 tests)
  B — Devices, from their own history of going down (2 tests)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.main import app  # noqa: F401  (module level: the ML import must not land inside a test)
from app.services import ai_device_failure
from tests.test_ai_learning import NOW, SUPERVISOR, _admin_session_run, _client, _run, _sql, _world


async def _cameras(w, *names) -> dict:
    ids = {n: uuid.uuid4() for n in names}
    await _run([("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,:n)",
                 {"i": i, "t": w["tenant"], "s": w["site_a"], "n": n}) for n, i in ids.items()])
    return ids


def _alert(w, cam, at, status, module="intrusion", severity="medium"):
    aid = uuid.uuid4()
    return ("INSERT INTO alerts (id, tenant_id, camera_id, site_id, module_type, severity, title, status, created_at) "
            "VALUES (:i,:t,:c,:s,:m,:sev,'Alert',:st,:at)",
            {"i": aid, "t": w["tenant"], "c": cam, "s": w["site_a"], "m": module, "sev": severity, "st": status,
             "at": at})


async def _alert_history(w) -> dict:
    """June so far: the Car Park camera's intrusion alerts were false 27 times of 30, the Lobby's 3 of 30.
    Then one open alert from each, an hour ago."""
    cams = await _cameras(w, "Car Park", "Lobby")
    stmts = []
    for n in range(30):
        at = datetime(2026, 6, 1, 2, tzinfo=timezone.utc) + timedelta(hours=7 * n)
        stmts.append(_alert(w, cams["Car Park"], at, "false_positive" if n % 10 else "resolved"))
        stmts.append(_alert(w, cams["Lobby"], at + timedelta(minutes=30), "false_positive" if n % 10 == 3 else "resolved"))
    opened = {}
    for name in ("Car Park", "Lobby"):
        st = _alert(w, cams[name], NOW - timedelta(hours=1), "open")
        opened[name] = st[1]["i"]
        stmts.append(st)
    await _run(stmts)
    return {"cams": cams, "open": opened}


# ─── A. False alarms ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_noisy_cameras_open_alert_is_flagged_likely_false_and_the_alert_itself_is_untouched():
    w = await _world(features=("false_alarm",), enable=("false_alarm",), roles=(2, 3, 4))
    h = await _alert_history(w)
    done = await _admin_session_run(w)
    assert done["false_alarm"]["model"] == "READY", done
    rows = await _sql("SELECT subject_id, subject_type, title, detail, score, reasons FROM ai_suggestions "
                      " WHERE tenant_id = :t AND subject_type = 'alert'", {"t": w["tenant"]})
    assert [r["subject_id"] for r in rows] == [h["open"]["Car Park"]], "only the noisy camera's alert"
    assert "is likely a false alarm" in rows[0]["title"] and "Car Park" in rows[0]["title"]
    assert "never closes, hides or changes an alert" in rows[0]["detail"]
    assert float(rows[0]["score"]) >= 0.75
    assert rows[0]["reasons"][0]["factor"] == "camera_false_rate"
    status = await _sql("SELECT status FROM alerts WHERE id = ANY(:ids)", {"ids": list(h["open"].values())})
    assert {r["status"] for r in status} == {"open"}, "the AI changed an alert"


@pytest.mark.asyncio
async def test_a_camera_raising_mostly_false_alarms_is_put_forward_for_review():
    w = await _world(features=("false_alarm",), enable=("false_alarm",))
    h = await _alert_history(w)
    await _admin_session_run(w)
    reviews = await _sql("SELECT title, score, evidence FROM ai_suggestions "
                         " WHERE tenant_id = :t AND subject_type = 'camera_month'", {"t": w["tenant"]})
    assert len(reviews) == 1 and "Car Park raised mostly false alarms this month (27 of 30 decided)" in reviews[0]["title"]
    assert reviews[0]["evidence"] == {"false": 27, "decided": 30, "month": "2026-06"}
    cam = await _sql("SELECT is_active, name FROM cameras WHERE id = :c", {"c": h["cams"]["Car Park"]})
    assert cam[0]["is_active"] is True, "nothing on the camera is changed"


@pytest.mark.asyncio
async def test_the_operators_verdict_is_what_happened_and_closes_the_warning():
    w = await _world(features=("false_alarm",), enable=("false_alarm",))
    h = await _alert_history(w)
    await _admin_session_run(w)
    await _sql("UPDATE alerts SET status = 'false_positive' WHERE id = :a", {"a": h["open"]["Car Park"]})
    await _admin_session_run(w, now=NOW + timedelta(minutes=30))
    row = (await _sql("SELECT status, outcome, decision_note FROM ai_suggestions WHERE subject_id = :s",
                      {"s": h["open"]["Car Park"]}))[0]
    assert row["outcome"] == "HAPPENED" and row["status"] == "EXPIRED"
    assert row["decision_note"] == "The operator decided the alert."


# ─── B. Devices ──────────────────────────────────────────────────────────────

async def _device_history(w) -> dict:
    """120 days: the Gate camera went down every 5 days and came back; the Fence camera never did."""
    cams = await _cameras(w, "Gate", "Fence")
    start = NOW - timedelta(days=125)
    stmts = [("INSERT INTO device_health_changes (tenant_id, device_kind, device_id, site_id, state, observed_at) "
              "VALUES (:t,'CAMERA',:d,:s,'OK',:at)", {"t": w["tenant"], "d": cams[n], "s": w["site_a"], "at": start})
             for n in ("Gate", "Fence")]
    day = start + timedelta(days=2)
    while day < NOW - timedelta(days=1):
        for state, at in (("DOWN", day), ("OK", day + timedelta(hours=3))):
            stmts.append(("INSERT INTO device_health_changes (tenant_id, device_kind, device_id, site_id, state, "
                          "observed_at) VALUES (:t,'CAMERA',:d,:s,:st,:at)",
                          {"t": w["tenant"], "d": cams["Gate"], "s": w["site_a"], "st": state, "at": at}))
        day += timedelta(days=5)
    await _run(stmts)
    return cams


@pytest.mark.asyncio
async def test_the_device_that_keeps_going_down_is_warned_about_and_the_steady_one_is_not():
    w = await _world(features=("device_failure",), enable=("device_failure",))
    cams = await _device_history(w)
    done = await _admin_session_run(w)
    assert done["device_failure"]["model"] == "READY", done
    rows = await _sql("SELECT title, evidence, reasons, detail FROM ai_suggestions "
                      " WHERE tenant_id = :t AND feature_key = 'device_failure'", {"t": w["tenant"]})
    assert len(rows) == 1 and rows[0]["evidence"]["device_id"] == str(cams["Gate"])
    assert rows[0]["title"] == "Camera Gate may go down in the next 7 days"
    assert any("went down" in r["words"] for r in rows[0]["reasons"])
    assert "a work order is yours to create in Maintenance" in rows[0]["detail"]
    orders = await _sql("SELECT count(*) AS n FROM maintenance_work_orders WHERE tenant_id = :t", {"t": w["tenant"]})
    assert orders[0]["n"] == 0, "no work order is raised by itself"


@pytest.mark.asyncio
async def test_whether_it_went_down_is_written_on_the_warning_a_week_later():
    w = await _world(features=("device_failure",), enable=("device_failure",))
    cams = await _device_history(w)
    await _admin_session_run(w)
    await _sql("INSERT INTO device_health_changes (tenant_id, device_kind, device_id, site_id, state, observed_at) "
               "VALUES (:t,'CAMERA',:d,:s,'DOWN',:at)",
               {"t": w["tenant"], "d": cams["Gate"], "s": w["site_a"], "at": NOW + timedelta(days=2)})
    await _admin_session_run(w, now=NOW + timedelta(days=3))
    row = (await _sql("SELECT outcome FROM ai_suggestions WHERE tenant_id = :t AND feature_key = 'device_failure' "
                      " ORDER BY created_at LIMIT 1", {"t": w["tenant"]}))[0]
    assert row["outcome"] == "HAPPENED"
    assert ai_device_failure.went_down([(NOW, "DOWN")], NOW - timedelta(days=1), NOW + timedelta(days=1))
