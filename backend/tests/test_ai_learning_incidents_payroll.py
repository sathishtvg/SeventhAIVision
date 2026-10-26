"""The AI module, phase 5: the incident helper and the payroll check.

Sections:
  A — Incident helper (3 tests)
  B — Payroll check (2 tests)
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.main import app  # noqa: F401  (module level: the ML import must not land inside a test)
from app.services import ai_incident_helper, ai_payroll_check
from tests.test_ai_learning import ADMIN, GUARD, NOW, OPERATOR, _admin_session_run, _client, _run, _sql, _world

FENCE = ["Intruder climbed the fence at the back gate", "Person climbing over back gate fence at night",
         "Fence intrusion at the back gate, intruder fled", "Intruder seen climbing perimeter fence by the back gate"]
BADGE = ["Visitor lost their badge at the lobby", "Lost visitor badge reported at lobby counter",
         "Visitor badge missing after lobby check-out", "Lobby visitor could not find their badge"]


async def _incidents(w) -> uuid.UUID:
    """24 past incidents: fence intrusions (critical, one with what was done) and lost badges (low);
    then a new fence intrusion an hour ago, recorded as low."""
    stmts = []
    for n in range(12):
        for titles, severity in ((FENCE, "critical"), (BADGE, "low")):
            iid = uuid.uuid4()
            stmts.append(("INSERT INTO incidents (id, tenant_id, title, severity, status, created_at) "
                          "VALUES (:i,:t,:ti,:sev,'resolved',:at)",
                          {"i": iid, "t": w["tenant"], "ti": titles[n % 4], "sev": severity,
                           "at": NOW - timedelta(days=60 - 2 * n)}))
            if n == 11 and severity == "critical":
                stmts.append(("INSERT INTO incident_notes (tenant_id, incident_id, note, created_at) "
                              "VALUES (:t,:i,'Police called; the fence was repaired the next day.',:at)",
                              {"t": w["tenant"], "i": iid, "at": NOW - timedelta(days=30)}))
    new = uuid.uuid4()
    stmts.append(("INSERT INTO incidents (id, tenant_id, title, description, severity, status, created_at) "
                  "VALUES (:i,:t,'Someone climbing the back gate fence','Guard saw a person on the fence.','low',"
                  "'open',:at)", {"i": new, "t": w["tenant"], "at": NOW - timedelta(hours=1)}))
    await _run(stmts)
    return new


# ─── A. Incident helper ──────────────────────────────────────────────────────

def test_words_drop_the_little_ones_and_similar_wording_scores_high():
    assert ai_incident_helper.words("The intruder climbed the FENCE at 3am") == ["intruder", "climbed", "fence", "3am"]
    docs = [ai_incident_helper.words(t) for t in FENCE + BADGE]
    idf = ai_incident_helper.fit_idf(docs)
    a = ai_incident_helper.vector(ai_incident_helper.words("fence intruder back gate"), idf)
    same = ai_incident_helper.vector(docs[0], idf)
    other = ai_incident_helper.vector(docs[4], idf)
    assert ai_incident_helper.cosine(a, same) > 0.5 > ai_incident_helper.cosine(a, other)


@pytest.mark.asyncio
async def test_a_new_fence_intrusion_recorded_as_low_is_shown_to_look_like_past_critical_ones():
    w = await _world(features=("incident_helper",), enable=("incident_helper",), roles=(2, 3, 4))
    new = await _incidents(w)
    done = await _admin_session_run(w)
    assert done["incident_helper"]["model"] == "READY", done
    rows = await _sql("SELECT subject_id, title, detail, evidence, reasons FROM ai_suggestions "
                      " WHERE tenant_id = :t AND feature_key = 'incident_helper'", {"t": w["tenant"]})
    assert [r["subject_id"] for r in rows] == [new]
    r = rows[0]
    assert r["title"] == '"Someone climbing the back gate fence" looks like past critical incidents (recorded as low)'
    assert r["evidence"]["suggested"] == "critical" and r["evidence"]["recorded"] == "low"
    assert any("fence" in x["words"].lower() for x in r["reasons"])
    assert "the severity is not changed" in r["detail"]
    severity = await _sql("SELECT severity FROM incidents WHERE id = :i", {"i": new})
    assert severity[0]["severity"] == "low", "the AI changed the incident"


@pytest.mark.asyncio
async def test_the_helper_answers_for_words_not_yet_saved_and_only_for_the_roles_chosen():
    w = await _world(features=("incident_helper",), enable=("incident_helper",), roles=(2, 3, 4))
    await _incidents(w)
    await _admin_session_run(w)
    async with _client() as c:
        ok = await c.post("/api/v1/ai/incident-helper", headers=w["h"][OPERATOR],
                          json={"title": "Intruder on the back gate fence"})
        exact = await c.post("/api/v1/ai/incident-helper", headers=w["h"][OPERATOR], json={"title": FENCE[3]})
        guard = await c.post("/api/v1/ai/incident-helper", headers=w["h"][GUARD], json={"title": "Fence"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["ready"] is True and body["severity"] == "critical" and body["sure"] >= 0.6
    assert body["similar"] and all("fence" in s["title"].lower() for s in body["similar"])
    # The three past incidents worded exactly so are the most similar; one of them says what was done.
    done = [s["what_was_done"] for s in exact.json()["similar"] if s["what_was_done"]]
    assert done == ["Police called; the fence was repaired the next day."]
    assert guard.status_code == 403


# ─── B. Payroll check ────────────────────────────────────────────────────────

async def _payroll(w) -> dict:
    """Six finalised months for Usual Ann and Usual Bob, two for New Nia; then a draft month in which Ann's
    overtime and gross are far above her normal and Bob's are as usual."""
    stmts, slips = [], {}
    for m in range(7):
        rid = uuid.uuid4()
        start = date(2025, 12, 1) + timedelta(days=31 * m)
        start = start.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        draft = m == 6
        stmts.append(("INSERT INTO payroll_runs (id, tenant_id, period_start, period_end, status) "
                      "VALUES (:i,:t,:a,:b,:s)",
                      {"i": rid, "t": w["tenant"], "a": start, "b": end, "s": "draft" if draft else "finalized"}))
        for name in ("Usual Ann", "Usual Bob", "New Nia"):
            if name == "New Nia" and m < 4:
                continue
            gross, ot = 2000 + 10 * (m % 3), 20 + (m % 2)
            if draft and name == "Usual Ann":
                gross, ot = 3600, 75
            sid = uuid.uuid4()
            slips[(name, m)] = sid
            stmts.append(("INSERT INTO payslips (id, tenant_id, payroll_run_id, guard_user_id, regular_hours, "
                          "  overtime_hours, gross_pay) VALUES (:i,:t,:r,:g,190,:ot,:gross)",
                          {"i": sid, "t": w["tenant"], "r": rid, "g": w["guards"][name], "ot": ot, "gross": gross}))
    await _run(stmts)
    return slips


def test_far_is_measured_against_the_guards_own_spread():
    usual = [2000, 2010, 2020, 2000, 2010, 2020]
    assert ai_payroll_check.deviations(2015, usual) < 1
    assert ai_payroll_check.deviations(3600, usual) > 30
    assert ai_payroll_check.deviations(2000, [2000] * 6) == 0.0


@pytest.mark.asyncio
async def test_a_draft_payslip_far_from_the_guards_normal_is_put_forward_and_payroll_is_not_touched():
    w = await _world(features=("payroll_check",), enable=("payroll_check",), roles=(2, 8),
                     guards=("Usual Ann", "Usual Bob", "New Nia"))
    slips = await _payroll(w)
    done = await _admin_session_run(w)
    assert done["payroll_check"]["model"] == "READY", done
    rows = await _sql("SELECT subject_id, title, detail, evidence FROM ai_suggestions "
                      " WHERE tenant_id = :t AND feature_key = 'payroll_check'", {"t": w["tenant"]})
    assert [r["subject_id"] for r in rows] == [slips[("Usual Ann", 6)]]
    assert "Check Usual Ann's pay for" in rows[0]["title"] and "against a usual" in rows[0]["title"]
    assert set(rows[0]["evidence"]) >= {"gross_pay", "overtime_hours"}
    assert "Nothing in the payroll run or the payslip is changed" in rows[0]["detail"]
    async with _client() as c:
        seen = (await c.get("/api/v1/ai/insights", headers=w["h"][ADMIN])).json()
    assert [i["feature_key"] for i in seen["items"]] == ["payroll_check"]
    run = await _sql("SELECT count(*) FILTER (WHERE status = 'draft') AS d FROM payroll_runs WHERE tenant_id = :t",
                     {"t": w["tenant"]})
    assert run[0]["d"] == 1, "the payroll run was changed"
    # Once the run is finalised, the warning is no longer for checking.
    await _sql("UPDATE payroll_runs SET status = 'finalized' WHERE tenant_id = :t", {"t": w["tenant"]})
    await _admin_session_run(w)
    left = await _sql("SELECT status FROM ai_suggestions WHERE subject_id = :s", {"s": slips[("Usual Ann", 6)]})
    assert left[0]["status"] == "EXPIRED"
