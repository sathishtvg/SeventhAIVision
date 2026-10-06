"""AI security intelligence, phase 14: what it turned out to be, and how the suggestions fared.

  A — The rules, with nothing running: what a review may say, what the dataset
      holds and never holds, what is counted from it
  B — Through the API: a review of a closed situation, the dataset, the counts
  C — Who may review and export, and that nothing learns from any of it

The claims this phase makes, each with tests: the suggestion, the decision and
the outcome are on one row and stay three things; a review is a person's
statement that changes nothing; the dataset carries no name and no free text;
every export is audited; and no part of the layer that runs by itself reads a
review or the dataset.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.services import intel_feedback as fb
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _sql
from tests.test_intel_decisions import BASE, _decide, _ready, _url
from tests.test_intel_timeline import without_docstrings

SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _row(**over) -> dict:
    base = {c: None for c in fb.COLUMNS}
    base.update(situation_number="SIT-1", kind="RESTRICTED_ZONE", suggested_action="DISPATCH_GUARD",
                first_decision="DISPATCH_GUARD", first_decision_basis="FOLLOWED", closed_as="RESOLVED",
                decisions=2, overrides=0, reviews=0)
    return {**base, **over}


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_a_review_says_one_of_a_few_things_and_undetermined_has_to_say_what_is_not_known():
    fb.validate("AUTHORISED_ACTIVITY", "TOO_HIGH", "NOT_USEFUL", None)
    fb.validate("REAL_INCIDENT", None, None, None)
    fb.validate("UNDETERMINED", None, None, "The gate camera was down; nobody saw who it was.")
    for bad in (("PROBABLY_FINE", None, None, None), ("REAL_INCIDENT", "WRONG", None, None),
                ("REAL_INCIDENT", None, "GREAT", None), ("UNDETERMINED", None, None, None),
                ("UNDETERMINED", None, None, "   ")):
        with pytest.raises(ValueError):
            fb.validate(*bad)
    migration = (VERSIONS / "0140_security_feedback.py").read_text(encoding="utf-8")
    for name, words in (("OUTCOMES", fb.OUTCOMES), ("ASSESSMENT_VERDICTS", fb.ASSESSMENT_VERDICTS),
                        ("RECOMMENDATION_VERDICTS", fb.RECOMMENDATION_VERDICTS)):
        listed = set(re.findall(r"'([A-Z_]+)'", re.search(rf'^{name} = "(.*)"$', migration, re.M).group(1)))
        assert listed == set(words), f"{name}: the database and the code disagree"


def test_the_dataset_has_the_three_things_on_a_row_and_no_column_that_says_who_or_what_was_written():
    assert {"suggested_action", "first_decision", "first_decision_basis", "closed_as", "closing_reason",
            "review_outcome"} <= set(fb.COLUMNS), "what was suggested, what was decided, how it ended"
    for column in fb.COLUMNS:
        assert not any(word in column for word in fb.NEVER), f"{column} would say who somebody is or what they wrote"
    assert "first_decision_role" in fb.COLUMNS, "a role, which is not a person"
    code = without_docstrings(SERVICES / "intel_feedback.py")
    select = code[code.index("async def dataset"):]
    assert "full_name" not in select and "d.note" not in select and "f.note" not in select
    assert "actor_user_id" not in select and "reviewer_user_id" not in select


def test_how_the_suggestions_fared_is_counted_and_a_rate_with_nothing_under_it_is_not_given():
    rows = [
        _row(),
        _row(first_decision="MONITOR", first_decision_basis="OVERRIDE", first_decision_reason="GUARD_RESPONDING",
             overrides=1),
        _row(first_decision="MONITOR", first_decision_basis="OVERRIDE", first_decision_reason="AUTHORISED_ACTIVITY",
             overrides=1, closed_as="FALSE_POSITIVE", review_outcome="AUTHORISED_ACTIVITY",
             review_assessment="TOO_HIGH", review_recommendation="NOT_USEFUL", reviews=1),
        _row(suggested_action="VIEW_CAMERA", first_decision="VIEW_CAMERA", closed_as="FALSE_POSITIVE",
             kind="ACTIVITY", review_outcome="FALSE_DETECTION", review_assessment="TOO_HIGH", reviews=1),
        _row(suggested_action=None, first_decision="ACKNOWLEDGE", first_decision_basis="INDEPENDENT", kind=None),
        _row(suggested_action="MONITOR", first_decision=None, first_decision_basis=None, kind="ACTIVITY"),
    ]
    a = fb.analyse(rows)
    assert (a["situations"], a["with_a_suggestion"], a["decided"]) == (6, 5, 4)
    assert (a["followed"], a["overridden"], a["acceptance_rate"]) == (2, 2, 0.5)
    assert (a["closed_false"], a["false_positive_rate"]) == (2, round(2 / 6, 4))
    assert a["override_reasons"] == {"AUTHORISED_ACTIVITY": 1, "GUARD_RESPONDING": 1}
    assert a["by_suggested_action"]["DISPATCH_GUARD"] == {"suggested": 3, "followed": 1, "overridden": 2,
                                                          "closed_false": 1}
    assert list(a["by_suggested_action"]) == ["DISPATCH_GUARD", "MONITOR", "VIEW_CAMERA"], "most suggested first"
    assert a["by_kind"]["RESTRICTED_ZONE"] == {"situations": 3, "closed_false": 1, "reviewed_real": 0,
                                               "assessed_too_high": 1, "assessed_too_low": 0}
    assert a["by_kind"]["NOT_ASSESSED"]["situations"] == 1
    assert (a["reviewed"], a["review_outcomes"]) == (2, {"AUTHORISED_ACTIVITY": 1, "FALSE_DETECTION": 1})
    assert a["review_of_assessment"] == {"TOO_HIGH": 2} and a["review_of_recommendation"] == {"NOT_USEFUL": 1}
    assert a["use"] == fb.USE and "Nothing in the platform is trained on this dataset" in a["use"]
    nothing = fb.analyse([])
    assert nothing["acceptance_rate"] is None and nothing["false_positive_rate"] is None and nothing["situations"] == 0
    only_own = fb.analyse([_row(suggested_action=None, first_decision_basis="INDEPENDENT")])
    assert only_own["acceptance_rate"] is None, "nothing was suggested, so nothing was followed or overridden"


def test_the_dataset_as_a_file_is_a_header_and_a_line_a_situation():
    at = datetime(2026, 10, 5, 2, 17, 4, tzinfo=timezone.utc)
    text_ = fb.to_csv([_row(started_at=at, source_types=["CCTV_AI", "DRONE_PATROL"], suggestion_confidence=0.85,
                            closing_reason="AUTHORISED_ACTIVITY"), _row(situation_number="SIT-2")])
    lines = list(csv.reader(io.StringIO(text_)))
    assert lines[0] == list(fb.COLUMNS) and len(lines) == 3
    first = dict(zip(lines[0], lines[1]))
    assert first["started_at"] == "2026-10-05T02:17:04+00:00" and first["source_types"] == "CCTV_AI DRONE_PATROL"
    assert first["suggestion_confidence"] == "0.85" and first["review_outcome"] == "", "nothing is written as nothing"
    assert fb.to_csv([]).strip().split(",") == list(fb.COLUMNS)


def test_nothing_that_runs_by_itself_can_read_a_review_or_the_dataset():
    for name in ("intel_runner", "intel_events", "intel_correlation", "intel_risk", "intel_recommend",
                 "intel_context"):
        source = (SERVICES / f"{name}.py").read_text(encoding="utf-8")
        assert "security_feedback" not in source and "intel_feedback" not in source, \
            f"{name} must not read what people said afterwards: nothing here learns by itself"
    runner = (SERVICES.parent / "intelligence_main.py").read_text(encoding="utf-8")
    assert "feedback" not in runner.lower()
    code = without_docstrings(SERVICES / "intel_feedback.py")
    writes = set(re.findall(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)", code))
    assert writes == {"security_feedback"}, "it writes a review, to its own table, and nothing else"
    assert not re.search(r"\b(fit|train|partial_fit|backward|optimizer)\b", code)


# ─── B. Through the API ──────────────────────────────────────────────────────

async def _closed(reason: str = "AUTHORISED_ACTIVITY", **kw) -> tuple[dict, dict]:
    """A situation the layer suggested a step for, that an operator went
    against and a supervisor then closed."""
    w, s = await _ready(severity="critical", **kw)
    async with _client() as c:
        first = await _decide(c, w, s, OPERATOR, "MONITOR", reason_code="GUARD_RESPONDING")
        closed = await _decide(c, w, s, SUPERVISOR, "RESOLVE", reason_code=reason)
    assert first.status_code == 201 and closed.status_code == 201, (first.text, closed.text)
    return w, s


def _review(c, w: dict, s: dict, role: int, **body):
    return c.post(_url(s, "feedback"), headers=w["h"][role], json=body)


@pytest.mark.asyncio
async def test_a_review_of_a_closed_situation_is_a_persons_statement_and_changes_nothing_else():
    w, s = await _closed()
    before = dict((await _sql("SELECT decision_status, closed_at, last_decision_id, risk_score, assessment_id "
                              "  FROM security_situations WHERE id = :s", {"s": s["id"]}))[0])
    async with _client() as c:
        asked = (await c.get(_url(s, "feedback"), headers=w["h"][SUPERVISOR])).json()
        r = await _review(c, w, s, SUPERVISOR, outcome="AUTHORISED_ACTIVITY", assessment_verdict="TOO_HIGH",
                          recommendation_verdict="NOT_USEFUL", note="Night cleaner on the rota.")
        again = await _review(c, w, s, SUPERVISOR, outcome="REAL_INCIDENT")
        second = await _review(c, w, s, MANAGER, outcome="AUTHORISED_ACTIVITY", assessment_verdict="ABOUT_RIGHT")
        listed = (await c.get(_url(s, "feedback"), headers=w["h"][OPERATOR])).json()
    assert asked["may_review"] is True and asked["reviews"] == [] and asked["closed"] is True
    assert [o["code"] for o in asked["outcomes"]] == list(fb.OUTCOMES)
    assert r.status_code == 201, r.text
    assert (r.json()["outcome"], r.json()["assessment_verdict"]) == ("AUTHORISED_ACTIVITY", "TOO_HIGH")
    assert again.status_code == 409 and "already reviewed" in again.json()["detail"], "a review is not edited"
    assert second.status_code == 201, "a second view is a second person's"
    assert [(x["outcome"], x["role_id"], x["name"]) for x in listed["reviews"]] == [
        ("AUTHORISED_ACTIVITY", SUPERVISOR, "Role 3 User"), ("AUTHORISED_ACTIVITY", MANAGER, "Role 8 User")]
    assert listed["reviews"][0]["note"] == "Night cleaner on the rota." and listed["may_review"] is False
    after = dict((await _sql("SELECT decision_status, closed_at, last_decision_id, risk_score, assessment_id "
                             "  FROM security_situations WHERE id = :s", {"s": s["id"]}))[0])
    assert after == before, "the situation is exactly as it was"
    audit = await _sql("SELECT user_id, detail FROM audit_logs WHERE tenant_id = :t AND action = 'intel.feedback.record' "
                       " ORDER BY created_at, id", {"t": w["tenant"]})
    assert [x["user_id"] for x in audit] == [w["users"][SUPERVISOR], w["users"][MANAGER]]
    detail = audit[0]["detail"] if isinstance(audit[0]["detail"], dict) else json.loads(audit[0]["detail"])
    assert detail["outcome"] == "AUTHORISED_ACTIVITY" and "Night cleaner" not in json.dumps(detail), \
        "that a review was made is audited; what was written in it stays in the review"


@pytest.mark.asyncio
async def test_who_may_review_and_what_is_refused():
    w, s = await _closed()
    still_open, o = await _ready(severity="critical")
    async with _client() as c:
        operator = await _review(c, w, s, OPERATOR, outcome="REAL_INCIDENT")
        viewer = await _review(c, w, s, VIEWER, outcome="REAL_INCIDENT")
        unknown = await _review(c, w, s, SUPERVISOR, outcome="PROBABLY_FINE")
        vague = await _review(c, w, s, SUPERVISOR, outcome="UNDETERMINED")
        extra = await _review(c, w, s, SUPERVISOR, outcome="REAL_INCIDENT", risk_score=5)
        too_soon = await _review(c, still_open, o, SUPERVISOR, outcome="REAL_INCIDENT")
        outsider = await c.post(_url(s, "feedback"), headers=still_open["h"][ADMIN], json={"outcome": "REAL_INCIDENT"})
        read_open = (await c.get(_url(o, "feedback"), headers=still_open["h"][SUPERVISOR])).json()
        read_operator = (await c.get(_url(s, "feedback"), headers=w["h"][OPERATOR])).json()
    assert operator.status_code == 403 and viewer.status_code == 403, "reviewing takes the authority to approve"
    assert unknown.status_code == 422 and vague.status_code == 422 and extra.status_code == 422
    assert "what is still not known" in vague.json()["detail"]
    assert too_soon.status_code == 409 and "still open" in too_soon.json()["detail"]
    assert outsider.status_code == 404
    assert read_open["may_review"] is False and read_operator["may_review"] is False
    assert await _sql("SELECT 1 FROM security_feedback WHERE tenant_id = :t", {"t": w["tenant"]}) == []


@pytest.mark.asyncio
async def test_the_dataset_puts_suggestion_decision_and_outcome_on_one_row_with_no_name_and_no_note():
    w, s = await _closed()
    async with _client() as c:
        await _review(c, w, s, SUPERVISOR, outcome="AUTHORISED_ACTIVITY", assessment_verdict="TOO_HIGH",
                      recommendation_verdict="NOT_USEFUL", note="Night cleaner on the rota.")
        r = await c.get(f"{BASE}/feedback/dataset", headers=w["h"][ADMIN])
        as_csv = await c.get(f"{BASE}/feedback/dataset", headers=w["h"][MANAGER], params={"format": "csv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and body["truncated"] is False and body["columns"] == list(fb.COLUMNS)
    assert body["use"] == fb.USE
    row = body["rows"][0]
    assert list(row) == list(fb.COLUMNS)
    suggested = (await _sql("SELECT action FROM security_recommendations WHERE situation_id = :s AND available "
                            " ORDER BY rank LIMIT 1", {"s": s["id"]}))[0]["action"]
    assert row["situation_number"] == s["situation_number"] and row["suggested_action"] == suggested
    assert (row["first_decision"], row["first_decision_basis"], row["first_decision_reason"],
            row["first_decision_role"]) == ("MONITOR", "OVERRIDE", "GUARD_RESPONDING", OPERATOR)
    assert (row["closed_as"], row["closing_reason"]) == ("RESOLVED", "AUTHORISED_ACTIVITY")
    assert (row["review_outcome"], row["review_assessment"], row["review_recommendation"], row["reviews"]) == (
        "AUTHORISED_ACTIVITY", "TOO_HIGH", "NOT_USEFUL", 1)
    assert (row["decisions"], row["overrides"]) == (2, 1) and row["seconds_to_first_decision"] >= 0
    assert row["first_risk_level"] == row["last_risk_level"] == "HIGH" and row["engine_version"] == "rules-2"
    # No name, no id of a person, and none of what anybody wrote.
    for hidden in ("Role 4 User", "Role 3 User", "Night cleaner", str(w["users"][OPERATOR]),
                   str(w["users"][SUPERVISOR])):
        assert hidden not in r.text and hidden not in as_csv.text
    assert as_csv.status_code == 200 and as_csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in as_csv.headers["content-disposition"]
    lines = list(csv.reader(io.StringIO(as_csv.text)))
    assert lines[0] == list(fb.COLUMNS) and lines[1][0] == s["situation_number"] and len(lines) == 2
    audit = await _sql("SELECT user_id, detail FROM audit_logs WHERE tenant_id = :t AND action = 'intel.feedback.export' "
                       " ORDER BY created_at, id", {"t": w["tenant"]})
    assert [x["user_id"] for x in audit] == [w["users"][ADMIN], w["users"][MANAGER]], "every export, with who took it"
    detail = audit[1]["detail"] if isinstance(audit[1]["detail"], dict) else json.loads(audit[1]["detail"])
    assert (detail["rows"], detail["format"], detail["truncated"]) == (1, "csv", False)


@pytest.mark.asyncio
async def test_how_the_suggestions_fared_is_served_as_counts_and_says_nothing_learns_from_them():
    w, s = await _closed()
    async with _client() as c:
        await _review(c, w, s, SUPERVISOR, outcome="AUTHORISED_ACTIVITY", assessment_verdict="TOO_HIGH")
        r = await c.get(f"{BASE}/feedback/analytics", headers=w["h"][OPERATOR])
        wrong = await c.get(f"{BASE}/feedback/analytics", headers=w["h"][OPERATOR],
                            params={"from": "2026-10-05T00:00:00Z", "to": "2026-10-01T00:00:00Z"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["situations"], a["with_a_suggestion"], a["followed"], a["overridden"]) == (1, 1, 0, 1)
    assert a["acceptance_rate"] == 0.0 and a["override_reasons"] == {"GUARD_RESPONDING": 1}
    assert a["review_outcomes"] == {"AUTHORISED_ACTIVITY": 1} and a["review_of_assessment"] == {"TOO_HIGH": 1}
    assert a["use"] == fb.USE and wrong.status_code == 422
    assert "Role 4 User" not in r.text


# ─── C. Who may export, and that nothing learns ──────────────────────────────

@pytest.mark.asyncio
async def test_the_dataset_is_exported_only_by_those_given_it_and_only_for_their_own_sites():
    w, s = await _closed()
    other, _ = await _closed()
    async with _client() as c:
        refused = [await c.get(f"{BASE}/feedback/dataset", headers=w["h"][role])
                   for role in (SUPERVISOR, OPERATOR, GUARD, VIEWER)]
        nobody = await c.get(f"{BASE}/feedback/dataset")
        theirs = await c.get(f"{BASE}/feedback/dataset", headers=other["h"][ADMIN])
        elsewhere = await c.get(f"{BASE}/feedback/dataset", headers=w["h"][ADMIN],
                                params={"site_id": str(w["site_b"])})
        long_ago = await c.get(f"{BASE}/feedback/dataset", headers=w["h"][ADMIN],
                               params={"from": "2020-01-01T00:00:00Z", "to": "2020-02-01T00:00:00Z"})
    assert [r.status_code for r in refused] == [403] * 4 and nobody.status_code in (401, 403)
    assert len(theirs.json()["rows"]) == 1, "their own closed situation, and only that"
    assert all(x["site_id"] != str(w["site_a"]) for x in theirs.json()["rows"]), "another organisation's is not seen"
    assert elsewhere.json()["rows"] == [] and long_ago.json()["rows"] == []
    assert len(await _sql("SELECT 1 FROM audit_logs WHERE tenant_id = :t AND action = 'intel.feedback.export'",
                          {"t": w["tenant"]})) == 2, "an export that found nothing was still an export"


@pytest.mark.asyncio
async def test_reviews_are_written_once_kept_within_the_tenant_and_teach_the_layer_nothing():
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal
    from app.services import intel_recommend as rec
    from app.services import intel_risk as risk

    w, s = await _closed()
    other, _ = await _closed()
    async with _client() as c:
        made = await _review(c, w, s, SUPERVISOR, outcome="FALSE_DETECTION", assessment_verdict="TOO_HIGH",
                             recommendation_verdict="NOT_USEFUL")
    assert made.status_code == 201, made.text
    before = (await _sql("SELECT count(*) AS n FROM security_assessments WHERE tenant_id = :t", {"t": w["tenant"]}))[0]
    now = datetime.now(timezone.utc)
    await risk.assess_tenant(AsyncSessionLocal, w["tenant"], now)
    await rec.recommend_tenant(AsyncSessionLocal, w["tenant"], now)
    after = (await _sql("SELECT count(*) AS n FROM security_assessments WHERE tenant_id = :t", {"t": w["tenant"]}))[0]
    assert after["n"] == before["n"], "a review that says 'too high' changes no assessment: nothing learns from it"
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        seen = (await db.execute(text("SELECT count(*) FROM security_feedback"))).scalar()
        assert seen == 0, "another tenant's reviews are not visible"
        await db.rollback()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert (await db.execute(text("SELECT count(*) FROM security_feedback"))).scalar() == 1
        for statement in ("UPDATE security_feedback SET outcome = 'REAL_INCIDENT'", "DELETE FROM security_feedback"):
            with pytest.raises(Exception, match="permission denied"):
                await db.execute(text(statement))
            await db.rollback()
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
    with pytest.raises(Exception, match="ck_secfb_undetermined"):
        await _sql("INSERT INTO security_feedback (tenant_id, situation_id, outcome, reviewer_role) "
                   "VALUES (:t,:s,'UNDETERMINED',3)", {"t": w["tenant"], "s": s["id"]})
