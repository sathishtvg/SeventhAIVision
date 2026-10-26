"""The AI module, phase 2: it learns from an organisation's own shifts, warns, and people decide.

Sections:
  A — The maths, without a database (6 tests)
  B — Late and no-show warnings from a real shift history (4 tests)
  C — The overtime forecast learns how each guard works (1 test)
  D — Decisions, and who sees what (5 tests)
  E — Row-level security, as the app's own role (1 test)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.core.security import create_access_token
from app.services import ai_learning, ai_overtime_fatigue, ai_runner

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)
ADMIN, SUPERVISOR, OPERATOR, GUARD, MANAGER = 2, 3, 4, 5, 8
SGT = ZoneInfo("Asia/Singapore")
#: A fixed moment, so nothing depends on the day the tests run: 10 June 2026, 08:00 in Singapore.
NOW = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)


# ─── helpers ─────────────────────────────────────────────────────────────────

async def _run(statements: list[tuple[str, dict]]) -> list:
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    out = []
    try:
        async with factory() as s:
            for stmt, params in statements:
                r = await s.execute(text(stmt), params)
                out.append(r.mappings().all() if r.returns_rows else [])
            await s.commit()
        return out
    finally:
        await engine.dispose()


async def _sql(stmt: str, params: dict | None = None) -> list:
    return (await _run([(stmt, params or {})]))[0]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app), base_url="http://test")


def _auth(uid, tid, role) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(uid), str(tid), role)}"}


async def _world(*, features=("late_risk", "overtime_fatigue"), enable=("late_risk", "overtime_fatigue"),
                 roles=(2, 3, 8), guards=("Late Lim", "Prompt Tan", "Prompt Raj", "Prompt Ong")) -> dict:
    """An organisation with two sites, staff for each role, guards, the AI licence and the features on."""
    w: dict = {k: uuid.uuid4() for k in ("tenant", "site_a", "site_b")}
    stmts = [
        ("INSERT INTO tenants (id, name, slug, timezone) VALUES (:t,'Learn Co',:s,'Asia/Singapore')",
         {"t": w["tenant"], "s": f"ail-{w['tenant'].hex[:10]}"}),
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Jurong')", {"i": w["site_a"], "t": w["tenant"]}),
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Tuas')", {"i": w["site_b"], "t": w["tenant"]}),
        ("INSERT INTO ai_module_licenses (tenant_id, is_enabled, licensed_at, features) "
         "VALUES (:t, TRUE, now(), CAST(:f AS text[]))", {"t": w["tenant"], "f": list(features)}),
    ]
    for key in enable:
        stmts.append(("INSERT INTO ai_feature_settings (tenant_id, feature_key, enabled, role_ids) "
                      "VALUES (:t, :k, TRUE, CAST(:r AS smallint[]))", {"t": w["tenant"], "k": key, "r": list(roles)}))
    w["users"], w["h"] = {}, {}
    for role in (ADMIN, SUPERVISOR, OPERATOR, GUARD, MANAGER):
        uid = uuid.uuid4()
        w["users"][role], w["h"][role] = uid, _auth(uid, w["tenant"], role)
        stmts.append(("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                      "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
                      {"i": uid, "t": w["tenant"], "r": role, "e": f"r{role}-{uid.hex[:8]}@ail.test", "n": f"Role {role}"}))
    w["guards"] = {}
    for name in guards:
        gid = uuid.uuid4()
        w["guards"][name] = gid
        stmts.append(("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                      "VALUES (:i,:t,5,:e,'x',:n)", {"i": gid, "t": w["tenant"], "e": f"g-{gid.hex[:8]}@ail.test", "n": name}))
    await _run(stmts)
    return w


def _shift(w, guard, start, hours=12, *, site="site_a", status="completed", late_min=None, worked_extra=0.0,
           ot_minutes=None):
    """One shift row. late_min None: not started yet (scheduled) or on time; a number: started that late."""
    sid = uuid.uuid4()
    end = start + timedelta(hours=hours)
    actual_start = actual_end = None
    if status == "completed":
        actual_start = start + timedelta(minutes=late_min or -5)
        actual_end = end + timedelta(hours=worked_extra)
    return ("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, "
            "  actual_start, actual_end, status, is_late, late_minutes, overtime_minutes) "
            "VALUES (:id,:t,:site,:g,:a,:b,:as,:ae,:st,:late,:lm,:ot)",
            {"id": sid, "t": w["tenant"], "site": w[site], "g": w["guards"][guard], "a": start, "b": end,
             "as": actual_start, "ae": actual_end, "st": status, "late": bool(late_min), "lm": late_min,
             "ot": ot_minutes})


def _night(day_offset: int) -> datetime:
    """22:00 in Singapore, `day_offset` days from NOW's date."""
    d = NOW.astimezone(SGT).date() + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, 22, 0, tzinfo=SGT).astimezone(timezone.utc)


async def _late_history(w) -> dict:
    """48 finished night shifts: Late Lim late 9 of 12, Prompt Tan once, the others never; then one
    shift each for Late Lim and Prompt Raj tonight."""
    stmts = []
    for i, name in enumerate(w["guards"]):
        for n in range(12):
            day = -40 + n * 3 + i % 3
            late = (name == "Late Lim" and n % 4 != 0) or (name == "Prompt Tan" and n == 5)
            stmts.append(_shift(w, name, _night(day), late_min=25 if late else None))
    tonight = {}
    for name in ("Late Lim", "Prompt Raj"):
        st = _shift(w, name, _night(0), status="scheduled")
        tonight[name] = st[1]["id"]
        stmts.append(st)
    await _run(stmts)
    return tonight


async def _admin_session_run(w, now=NOW, **kw) -> dict:
    """One organisation's pass as svc_app, the role the scheduler runs as: row-level security applies."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            out = await ai_runner.run_tenant(s, str(w["tenant"]), now=now, **kw)
            await s.commit()
            return out
    finally:
        await engine.dispose()


# ─── A. The maths ────────────────────────────────────────────────────────────

def test_the_model_learns_a_clear_pattern_and_says_which_factor_raised_it():
    rng = np.random.default_rng(7)
    x1 = rng.normal(size=400)
    x2 = rng.normal(size=400)  # noise
    y = (rng.random(400) < 1 / (1 + np.exp(-(2.5 * x1 - 1)))).astype(float)
    fitted = ai_learning.fit_logistic(["habit", "noise"], np.c_[x1, x2], y)
    assert fitted.weights[0] > 1.0 and abs(fitted.weights[1]) < 0.3
    high, parts = ai_learning.score(fitted, {"habit": 2.0, "noise": 0.0})
    low, _ = ai_learning.score(fitted, {"habit": -2.0, "noise": 0.0})
    assert high > 0.8 > 0.2 > low
    assert parts[0]["factor"] == "habit" and parts[0]["push"] > 0
    # Kept as JSON and read back the same: nothing pickled.
    again = ai_learning.Fitted.from_json(fitted.as_json())
    assert ai_learning.score(again, {"habit": 2.0, "noise": 0.0})[0] == pytest.approx(high)


def test_how_well_it_ranks_is_measured_on_the_newest_rows_it_did_not_see():
    assert ai_learning.auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert ai_learning.auc(np.array([0, 1, 0, 1]), np.array([0.5, 0.5, 0.5, 0.5])) == 0.5
    assert ai_learning.auc(np.array([1, 1]), np.array([0.3, 0.4])) is None
    rng = np.random.default_rng(3)
    x = rng.normal(size=200)
    y = (x > 0).astype(float)
    m = ai_learning.evaluate(["x"], x.reshape(-1, 1), y)
    assert m["rows"] == 200 and m["auc_on_newest"] > 0.9


def test_the_bar_starts_at_the_sensitivity_and_moves_only_with_enough_decisions():
    assert ai_learning.threshold_for("HIGH", [])[0] == 0.25
    assert ai_learning.threshold_for("LOW", [])[0] == 0.60
    few = [{"status": "DISMISSED", "outcome": None}] * 5
    value, why = ai_learning.threshold_for("MEDIUM", few)
    assert value == 0.40 and "not enough decisions yet" in why


def test_people_dismissing_most_warnings_raises_the_bar_and_accepting_true_ones_lowers_it():
    dismissed = [{"status": "DISMISSED", "outcome": None}] * 17 + [{"status": "ACCEPTED", "outcome": "HAPPENED"}] * 3
    value, why = ai_learning.threshold_for("MEDIUM", dismissed)
    assert value == 0.60 and "17 of the last 20 warnings were dismissed" in why
    some = [{"status": "DISMISSED", "outcome": None}] * 9 + [{"status": "ACCEPTED", "outcome": "HAPPENED"}] * 11
    assert ai_learning.threshold_for("MEDIUM", some)[0] == 0.45
    useful = [{"status": "ACCEPTED", "outcome": "HAPPENED"}] * 9 + [{"status": "DISMISSED", "outcome": None}]
    value, why = ai_learning.threshold_for("MEDIUM", useful)
    assert value == 0.35 and "came true" in why
    # Never past its limits, whatever the decisions.
    assert ai_learning.threshold_for("LOW", dismissed, cap=0.9)[0] == 0.80
    assert ai_learning.threshold_for("HIGH", useful)[0] == 0.20


def test_a_guard_with_little_history_is_pulled_towards_the_organisation():
    assert ai_overtime_fatigue.shrunk(0, 0.0, 1.0) == 1.0
    assert ai_overtime_fatigue.shrunk(20, 2.0, 1.0) == pytest.approx(1.8)
    assert ai_overtime_fatigue.shrunk(1, 6.0, 0.0) == pytest.approx(1.0)


def test_fatigue_is_read_from_the_roster_rules_and_said_as_a_rule():
    start = datetime(2026, 6, 10, 0, tzinfo=timezone.utc)
    shifts = [{"scheduled_start": start, "scheduled_end": start + timedelta(hours=12)},
              {"scheduled_start": start + timedelta(hours=20), "scheduled_end": start + timedelta(hours=32)}]
    issues = ai_overtime_fatigue.fatigue_issues(shifts, min_rest=11, max_days=6, tz=SGT)
    assert len(issues) == 1 and "only 8.0 hours' rest" in issues[0] and "(the rule is 11)" in issues[0]
    week = [{"scheduled_start": start + timedelta(days=d), "scheduled_end": start + timedelta(days=d, hours=8)}
            for d in range(8)]
    assert any("8 working days in a row" in i for i in ai_overtime_fatigue.fatigue_issues(week, min_rest=11, max_days=6, tz=SGT))
    assert ai_overtime_fatigue.fatigue_issues(week[:6], min_rest=11, max_days=6, tz=SGT) == []


# ─── B. Late and no-show warnings ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_it_warns_about_the_habitually_late_guard_tonight_and_not_the_punctual_one():
    w = await _world()
    tonight = await _late_history(w)
    done = await _admin_session_run(w)
    assert done["late_risk"]["model"] == "READY", done
    rows = await _sql("SELECT subject_id, status, score, reasons, title, threshold, valid_until "
                      "  FROM ai_suggestions WHERE tenant_id = :t AND feature_key = 'late_risk'", {"t": w["tenant"]})
    by = {r["subject_id"]: r for r in rows}
    assert set(by) == {tonight["Late Lim"]}, "only the habitually late guard is warned about"
    warn = by[tonight["Late Lim"]]
    assert warn["status"] == "OPEN" and float(warn["score"]) >= float(warn["threshold"]) == 0.40
    assert "Late Lim may be late or absent for the 22:00 shift" in warn["title"] and "at Jurong" in warn["title"]
    assert warn["reasons"][0]["factor"] == "guard_late_rate" and "late or absent for" in warn["reasons"][0]["words"]
    assert warn["valid_until"] == _night(0)
    model = await _sql("SELECT status, rows_used, positives, metrics, fitted FROM ai_models "
                       " WHERE tenant_id = :t AND feature_key = 'late_risk'", {"t": w["tenant"]})
    assert len(model) == 1 and model[0]["rows_used"] == 48 and model[0]["positives"] == 10
    assert model[0]["fitted"]["factors"][0] == "guard_late_rate"


@pytest.mark.asyncio
async def test_with_too_little_history_it_says_what_it_needs_and_warns_about_nobody():
    w = await _world()
    await _run([_shift(w, "Late Lim", _night(-2 - n), late_min=30) for n in range(6)]
               + [_shift(w, "Late Lim", _night(0), status="scheduled")])
    done = await _admin_session_run(w)
    assert done["late_risk"]["model"] == "LEARNING"
    model = (await _sql("SELECT note FROM ai_models WHERE tenant_id = :t AND feature_key = 'late_risk'",
                        {"t": w["tenant"]}))[0]
    assert "24 more finished shifts" in model["note"] and "5 more shifts that started on time" in model["note"]
    assert await _sql("SELECT 1 FROM ai_suggestions WHERE tenant_id = :t", {"t": w["tenant"]}) == []


@pytest.mark.asyncio
async def test_what_happened_is_written_on_the_warning_after_the_shift():
    w = await _world()
    tonight = await _late_history(w)
    await _admin_session_run(w)
    # Two days on: the shift has ended and nobody checked in for it, so it was missed.
    await _admin_session_run(w, now=NOW + timedelta(days=2))
    row = (await _sql("SELECT status, outcome FROM ai_suggestions WHERE subject_id = :s", {"s": tonight["Late Lim"]}))[0]
    assert row["outcome"] == "HAPPENED"
    assert row["status"] == "EXPIRED", "nobody decided before the shift, so it expired"


@pytest.mark.asyncio
async def test_nothing_runs_without_the_licence_or_with_the_feature_switched_off():
    off = await _world(enable=())
    unlicensed = await _world(features=())
    for w in (off, unlicensed):
        await _late_history(w)
        assert await _admin_session_run(w) == {}
        assert await _sql("SELECT 1 FROM ai_models WHERE tenant_id = :t", {"t": w["tenant"]}) == []


# ─── C. The overtime forecast ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_guard_who_always_stays_late_is_forecast_over_the_limit_and_the_other_is_not():
    """Same roster for both (14 twelve-hour shifts left in June: 56 overtime hours by the roster
    alone, below 90% of 72). One of them has stayed two hours past every shift for two months.
    A third guard has two shifts with 8 hours between them: a fatigue warning, by the rule."""
    w = await _world(enable=("overtime_fatigue",), guards=("Stays Late", "Leaves On Time", "Short Rest"))
    stmts = []
    for name, extra in (("Stays Late", 2.0), ("Leaves On Time", 0.0)):
        for n in range(20):
            stmts.append(_shift(w, name, NOW - timedelta(days=60) + timedelta(days=2 * n) - timedelta(hours=4),
                                worked_extra=extra, ot_minutes=240 + int(extra * 60)))
        for n in range(14):
            stmts.append(_shift(w, name, NOW + timedelta(days=1 + n), status="scheduled"))
    first = NOW + timedelta(days=2)
    stmts += [_shift(w, "Short Rest", first, status="scheduled"),
              _shift(w, "Short Rest", first + timedelta(hours=20), status="scheduled")]
    # Only April and May shifts are in the history above, so nothing is accrued in June yet.
    await _run([s for s in stmts if not (s[1]["st"] == "completed" and s[1]["a"] >= datetime(2026, 6, 1, tzinfo=timezone.utc))])
    done = await _admin_session_run(w)
    assert done["overtime_fatigue"]["model"] == "READY"
    rows = await _sql("SELECT person_user_id, title, score, evidence, reasons FROM ai_suggestions "
                      " WHERE tenant_id = :t AND subject_type = 'guard_month'", {"t": w["tenant"]})
    assert [r["person_user_id"] for r in rows] == [w["guards"]["Stays Late"]]
    ev = rows[0]["evidence"]
    assert ev["roster"] == 56.0 and ev["learned_extra"] > 20 and ev["expected"] > 0.9 * 72
    assert "Stays Late is heading for" in rows[0]["title"] and "(limit 72)" in rows[0]["title"]
    assert any(r["factor"] == "learned_overrun" and "usually stays" in r["words"] for r in rows[0]["reasons"])
    tired = await _sql("SELECT person_user_id, reasons, score FROM ai_suggestions "
                       " WHERE tenant_id = :t AND subject_type = 'guard_week'", {"t": w["tenant"]})
    # Six days in a row is within the rule (6), and twelve hours' rest is more than 11: only Short Rest.
    assert [r["person_user_id"] for r in tired] == [w["guards"]["Short Rest"]]
    assert "only 8.0 hours' rest" in tired[0]["reasons"][0]["words"] and tired[0]["reasons"][0]["factor"] == "rule"


# ─── D. Decisions, and who sees what ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_supervisor_accepts_a_warning_once_and_a_later_run_leaves_it_as_decided():
    w = await _world()
    tonight = await _late_history(w)
    await _admin_session_run(w)
    async with _client() as c:
        listed = (await c.get("/api/v1/ai/insights", headers=w["h"][SUPERVISOR])).json()
        wid = listed["items"][0]["id"]
        ok = await c.post(f"/api/v1/ai/insights/{wid}/decide", headers=w["h"][SUPERVISOR],
                          json={"decision": "ACCEPT", "note": " Standby Ahmad lined up. "})
        again = await c.post(f"/api/v1/ai/insights/{wid}/decide", headers=w["h"][SUPERVISOR],
                             json={"decision": "DISMISS"})
    assert listed["features"] == ["late_risk", "overtime_fatigue"]
    assert ok.status_code == 200 and ok.json()["status"] == "ACCEPTED"
    assert ok.json()["decision_note"] == "Standby Ahmad lined up." and ok.json()["decided_by_name"] == "Role 3"
    assert again.status_code == 409
    await _admin_session_run(w, force_train=True)
    row = (await _sql("SELECT status FROM ai_suggestions WHERE subject_id = :s", {"s": tonight["Late Lim"]}))[0]
    assert row["status"] == "ACCEPTED", "a decided warning is never reopened"
    logged = await _sql("SELECT detail FROM audit_logs WHERE tenant_id = :t AND action = 'ai.insight.decide'",
                        {"t": w["tenant"]})
    assert len(logged) == 1 and logged[0]["detail"]["decision"] == "ACCEPTED"


@pytest.mark.asyncio
async def test_only_the_roles_the_admin_chose_see_the_warnings():
    w = await _world(roles=(2, 3))
    await _late_history(w)
    await _admin_session_run(w)
    async with _client() as c:
        seen = {role: (await c.get("/api/v1/ai/insights", headers=w["h"][role])) for role in
                (ADMIN, SUPERVISOR, OPERATOR, MANAGER, GUARD)}
    assert len(seen[ADMIN].json()["items"]) == 1 and len(seen[SUPERVISOR].json()["items"]) == 1
    assert seen[OPERATOR].json() == {"features": [], "items": []}, "operator's role not chosen"
    assert seen[MANAGER].json()["items"] == [], "manager's role not chosen"
    assert seen[GUARD].status_code == 403


@pytest.mark.asyncio
async def test_a_supervisor_limited_to_another_site_neither_sees_nor_decides_it():
    w = await _world()
    await _late_history(w)
    await _admin_session_run(w)
    await _sql("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)",
               {"u": w["users"][SUPERVISOR], "s": w["site_b"], "t": w["tenant"]})
    wid = (await _sql("SELECT id FROM ai_suggestions WHERE tenant_id = :t", {"t": w["tenant"]}))[0]["id"]
    async with _client() as c:
        listed = (await c.get("/api/v1/ai/insights", headers=w["h"][SUPERVISOR])).json()
        decide = await c.post(f"/api/v1/ai/insights/{wid}/decide", headers=w["h"][SUPERVISOR],
                              json={"decision": "DISMISS"})
    assert listed["items"] == [] and decide.status_code == 404


@pytest.mark.asyncio
async def test_the_learning_view_says_what_it_learned_and_where_the_bar_is():
    w = await _world()
    await _late_history(w)
    await _admin_session_run(w)
    async with _client() as c:
        body = (await c.get("/api/v1/ai/learning", headers=w["h"][ADMIN])).json()
    late = next(f for f in body["features"] if f["key"] == "late_risk")
    assert late["model"]["status"] == "READY" and late["model"]["rows_used"] == 48
    assert "Learned from 48 finished shifts, 10 of them late or missed." == late["model"]["note"]
    assert late["threshold"] == 0.40 and "Set by the sensitivity (Medium)" in late["threshold_reason"]
    top = {x["factor"]: x for x in late["learned"][:2]}
    assert "guard_late_rate" in top, late["learned"]
    assert top["guard_late_rate"]["raises"] and top["guard_late_rate"]["title"] == "The guard's own recent lateness"
    assert set(late["last_30_days"]) == {"open", "accepted", "dismissed", "came_true", "did_not"}


@pytest.mark.asyncio
async def test_learn_now_is_the_admins_and_needs_the_feature_on():
    w = await _world(enable=("overtime_fatigue",))
    await _late_history(w)
    async with _client() as c:
        supervisor = await c.post("/api/v1/ai/learning/late_risk/run", headers=w["h"][SUPERVISOR])
        off = await c.post("/api/v1/ai/learning/late_risk/run", headers=w["h"][ADMIN])
        on = await c.post("/api/v1/ai/learning/overtime_fatigue/run", headers=w["h"][ADMIN])
        unknown = await c.post("/api/v1/ai/learning/mind_reading/run", headers=w["h"][ADMIN])
    assert supervisor.status_code == 403
    assert off.status_code == 422 and "Switch the feature on" in off.json()["detail"]
    assert on.status_code == 200 and on.json()["model"] in ("READY", "LEARNING")
    assert unknown.status_code == 404


# ─── E. Row-level security ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_hourly_pass_runs_for_each_organisation_as_the_app_and_keeps_them_apart():
    """As svc_app over two organisations, the way the scheduler runs it: the
    second organisation's pass must not fail for the first one's commit, and each
    organisation's warnings stay its own."""
    a, b = await _world(), await _world()
    a_tonight, b_tonight = await _late_history(a), await _late_history(b)
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            restricted = (await s.execute(text(
                "SELECT NOT (rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname = current_user"))).scalar()
        assert restricted, "connected as a role that bypasses RLS: proves nothing"
        done = await ai_runner.run_all(factory, now=NOW)
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(b["tenant"])})
            seen = (await s.execute(text("SELECT tenant_id, subject_id FROM ai_suggestions"))).all()
    finally:
        await engine.dispose()
    assert str(a["tenant"]) in done and str(b["tenant"]) in done
    assert [(str(t), s) for t, s in seen] == [(str(b["tenant"]), b_tonight["Late Lim"])]
    a_rows = await _sql("SELECT subject_id FROM ai_suggestions WHERE tenant_id = :t", {"t": a["tenant"]})
    assert [r["subject_id"] for r in a_rows] == [a_tonight["Late Lim"]]
