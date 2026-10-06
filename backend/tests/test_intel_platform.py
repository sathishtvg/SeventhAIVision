"""AI security intelligence, phase 15: the vendor's view, and the layer checked as a whole.

  A — The health row, from what was measured
  B — The counts see every organisation, and count only what is owed
  C — The console shows the row, and only as counts
  D — The whole layer, swept: every table, every function, every operation
  E — What the runner asks every three seconds has an index made for it
  F — How long the layer takes, measured from what its stages wrote
  G — The process itself, started as it is deployed

Section B runs as the application's own database role, because that is how the
console asks: this project's other tests connect as a superuser, which no
tenant policy applies to. Section D asks the database and the route table what
is there rather than naming it, so a table or an operation added later is
checked without anybody remembering to add it here.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.services import intel_correlation as corr
from app.services import intel_events as events
from app.services import intel_pipeline as pipeline
from app.services import intel_platform_health as iph
from app.services import intel_recommend as rec
from app.services import intel_risk as risk
from app.services import intel_runner, platform_health
from tests.test_drone_api import ADMIN, OPERATOR, SUPERVISOR, _auth, _client, _sql
from tests.test_intel_docs import _served
from tests.test_intel_events import CLIENT_ROLE, SUPER_ADMIN, _alert, _camera, _world
from tests.test_intel_timeline import without_docstrings

BASE = "/api/v1/security-intelligence"
SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"

NOW = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
NOBODY = {"enabled": 0, "events_waiting": 0, "situations_waiting": 0, "sources_failing": 0}
IN_USE = {**NOBODY, "enabled": 2}
BEAT = {"at": (NOW - timedelta(seconds=4)).isoformat(), "ok": True, "tenants": 2, "events": 0, "failed": 0}
STATUSES = ("ok", "degraded", "down", "unknown")

#: A record of what was assessed, suggested, seen, decided, done or said. The
#: application may add to these and read them, and nothing else.
RECORDS = ("security_assessments", "security_recommendations", "security_reviews", "security_decisions",
           "security_decision_approvals", "security_actions", "security_observations", "security_feedback")


# ─── A. The health row ───────────────────────────────────────────────────────

def test_an_installation_where_nobody_switched_it_on_is_healthy_without_a_runner():
    row = iph.verdict(NOBODY, None, heartbeat_asked=True)
    assert row["status"] == "ok" and row["detail"] == "no organisation has switched security intelligence on"
    # Nobody is waiting on a runner, so not being able to ask after one is not a fault either.
    assert iph.verdict(NOBODY, None, heartbeat_asked=False)["status"] == "ok"


def test_a_check_that_could_not_run_is_never_shown_green():
    row = iph.verdict(None, BEAT, heartbeat_asked=True)
    assert row == {"service": "security-intelligence", "status": "unknown",
                   "detail": "the security intelligence tables could not be asked"}
    row = iph.verdict({**NOBODY, "enabled": 3}, None, heartbeat_asked=False)
    assert row["status"] == "unknown"
    assert row["detail"] == "the runner's heartbeat could not be read; 3 organisations with it on"


def test_a_silent_runner_is_down_and_the_row_says_what_still_works():
    row = iph.verdict({**NOBODY, "enabled": 1}, None, heartbeat_asked=True)
    assert row["status"] == "down"
    assert f"has not reported for {intel_runner.HEARTBEAT_TTL_SECONDS} s; 1 organisation with it on" in row["detail"]
    assert "Nothing new is being assessed or suggested" in row["detail"]
    assert "alerts, incidents and video do not depend on it" in row["detail"]


@pytest.mark.parametrize("counts,beat,said", [
    ({**IN_USE, "events_waiting": 1}, BEAT, "1 event waiting over a minute to be placed"),
    ({**IN_USE, "events_waiting": 40}, BEAT, "40 events waiting over a minute to be placed"),
    ({**IN_USE, "situations_waiting": 1}, BEAT, "1 situation waiting over a minute to be assessed"),
    ({**IN_USE, "sources_failing": 2}, BEAT, "2 sources could not be read"),
    (IN_USE, {**BEAT, "ok": False}, "its last pass had a failure"),
])
def test_a_runner_that_is_alive_but_not_keeping_up_is_degraded_and_says_how(counts, beat, said):
    row = iph.verdict(counts, beat, heartbeat_asked=True, now=NOW)
    assert row["status"] == "degraded" and row["detail"] == f"runner alive, but {said}"


def test_every_way_of_falling_behind_is_named_in_one_row():
    behind = {"enabled": 2, "events_waiting": 3, "situations_waiting": 4, "sources_failing": 1}
    row = iph.verdict(behind, {**BEAT, "ok": False}, heartbeat_asked=True, now=NOW)
    assert row["detail"] == ("runner alive, but its last pass had a failure; "
                             "3 events waiting over a minute to be placed; "
                             "4 situations waiting over a minute to be assessed; 1 source could not be read")


def test_a_runner_keeping_up_is_ok_and_shows_how_long_ago_it_last_reported():
    assert iph.verdict(IN_USE, BEAT, heartbeat_asked=True, now=NOW) == {
        "service": "security-intelligence", "status": "ok", "detail": "runner alive; 2 organisations with it on",
        "enabled": 2, "events_waiting": 0, "situations_waiting": 0, "sources_failing": 0,
        "heartbeat_age_seconds": 4}
    # A heartbeat nobody can read the time of is still a heartbeat.
    row = iph.verdict(IN_USE, {"ok": True, "at": "not a time"}, heartbeat_asked=True, now=NOW)
    assert row["status"] == "ok" and "heartbeat_age_seconds" not in row


def test_no_branch_of_the_row_carries_more_than_counts_a_status_and_a_sentence():
    """What the runner puts in its heartbeat about itself stays there: the row
    is built from the four counts and the heartbeat's time and `ok`, and from
    nothing else — whatever a later heartbeat is given to carry."""
    loud = {**BEAT, "tenant_id": str(uuid.uuid4()), "situation": "S-1", "risk_level": "CRITICAL", "decided_by": "x"}
    for counts in (None, NOBODY, IN_USE, {**IN_USE, "events_waiting": 2}):
        for beat in (None, loud, {**loud, "ok": False}):
            for asked in (True, False):
                row = iph.verdict(counts, beat, heartbeat_asked=asked, now=NOW)
                assert set(row) <= iph.MAY_CARRY, row
                assert row["status"] in STATUSES, row
                assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", row["detail"]) and "CRITICAL" not in row["detail"]
                assert all(isinstance(row[name], int) for name in iph.COUNTS if name in row)


# ─── B. The counts ───────────────────────────────────────────────────────────

async def _event(db, w: dict, *, status: str, minutes_ago: float) -> None:
    await db.execute(text(
        "INSERT INTO security_events (tenant_id, site_id, source_type, source_table, source_id, event_type, "
        "    occurred_at, severity, title, status, ingested_at) "
        "VALUES (:t,:s,'CCTV_AI','alerts',:i,'intrusion.zone_breach', now() - interval '10 minutes','high', "
        "        'Seen', :st, now() - make_interval(secs => :m * 60))"),
        {"t": w["tenant"], "s": w["site_a"], "i": uuid.uuid4(), "st": status, "m": minutes_ago})


async def _situation(db, w: dict, *, changed_minutes_ago: float, assessed: bool) -> None:
    await db.execute(text(
        "INSERT INTO security_situations (tenant_id, site_id, situation_number, title, severity, started_at, "
        "    last_event_at, event_count, updated_at, assessed_at) "
        "VALUES (:t,:s,:n,'Gate 1','high', now() - interval '20 minutes', now() - interval '10 minutes', 1, "
        "        now() - make_interval(secs => :m * 60), "
        "        CASE WHEN :a THEN now() - make_interval(secs => :m * 60) END)"),
        {"t": w["tenant"], "s": w["site_a"], "n": f"T-{uuid.uuid4().hex[:10]}", "m": changed_minutes_ago, "a": assessed})


async def _cursor(db, w: dict, source: str, error: str | None) -> None:
    await db.execute(text(
        "INSERT INTO security_ingest_cursors (tenant_id, source, read_from, last_error) VALUES (:t,:s, now(), :e)"),
        {"t": w["tenant"], "s": source, "e": error})


async def _as(db, w: dict) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})


async def _health(db) -> dict:
    return dict((await db.execute(text("SELECT * FROM platform_intel_health()"))).mappings().first())


@pytest.mark.asyncio
async def test_the_counts_see_every_organisation_as_the_application_role_and_only_what_is_owed(monkeypatch):
    """The console runs outside any tenant. Counting the tables directly would
    see nothing and call a runner that is falling behind idle; the function
    sees past that. Everything is written and counted in one transaction, so
    "a minute ago" is the same instant for both counts, and rolled back."""
    async with AsyncSessionLocal() as db:
        enabled_before = (await _health(db))["enabled"]
        await db.rollback()
    a, b, off = await _world(), await _world(), await _world(enabled=False)

    async with AsyncSessionLocal() as db:
        who = (await db.execute(text(
            "SELECT (SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user)"))).scalar()
        assert who is False, "this test proves nothing as a role that tenant policies do not apply to"
        before = await _health(db)
        assert before["enabled"] == enabled_before + 2, "two switched it on; the third did not"

        await _as(db, a)
        await _event(db, a, status="NEW", minutes_ago=5)        # owed for five minutes
        await _event(db, a, status="NEW", minutes_ago=0.1)      # read a moment ago: not late yet
        await _event(db, a, status="LINKED", minutes_ago=5)     # placed long ago
        await _cursor(db, a, "alerts", "could not be read: timeout")
        await _cursor(db, a, "incidents", None)
        await _as(db, b)
        await _situation(db, b, changed_minutes_ago=5, assessed=False)   # owed
        await _situation(db, b, changed_minutes_ago=5, assessed=True)    # assessed as it stands
        await _situation(db, b, changed_minutes_ago=0.1, assessed=False)  # changed a moment ago
        await _event(db, b, status="NEW", minutes_ago=3)        # a second organisation's, also owed
        # An organisation that has the layer off is owed nothing, whatever it left behind.
        await _as(db, off)
        await _event(db, off, status="NEW", minutes_ago=5)
        await _situation(db, off, changed_minutes_ago=5, assessed=False)
        await _cursor(db, off, "alerts", "could not be read")

        # Still inside the third organisation: asked directly, the tables show its own row and no more.
        assert (await db.execute(text("SELECT count(*) FROM security_events WHERE status = 'NEW'"))).scalar() == 1
        after = await _health(db)
        assert {k: after[k] - before[k] for k in after} == \
            {"enabled": 0, "events_waiting": 2, "situations_waiting": 1, "sources_failing": 1}

        async def no_runner():
            return True, None

        monkeypatch.setattr(intel_runner, "ask_heartbeat", no_runner)
        row = await iph.check_security_intelligence(db)
        assert row["service"] == "security-intelligence" and row["status"] == "down"
        assert {name: row[name] for name in iph.COUNTS} == after and "has not reported" in row["detail"]

        async def alive():
            return True, {"at": datetime.now(timezone.utc).isoformat(), "ok": True}

        monkeypatch.setattr(intel_runner, "ask_heartbeat", alive)
        row = await iph.check_security_intelligence(db)
        assert row["status"] == "degraded" and "waiting over a minute to be placed" in row["detail"]
        await db.rollback()


@pytest.mark.asyncio
async def test_a_probe_that_fails_reports_unknown_and_leaves_the_consoles_session_usable(monkeypatch):
    monkeypatch.setattr(iph, "text", lambda _sql: text("SELECT * FROM no_such_function_anywhere()"))
    async with AsyncSessionLocal() as db:
        row = await iph.check_security_intelligence(db)
        assert row == {"service": "security-intelligence", "status": "unknown",
                       "detail": "the security intelligence tables could not be asked"}
        assert (await db.execute(text("SELECT 1"))).scalar() == 1
        await db.rollback()


# ─── C. The console ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_console_shows_the_layer_once_as_counts_and_every_other_row_as_before():
    from tests.test_platform_health import _client as _console
    from tests.test_platform_health import _token

    async with await _console(await _token(SUPER_ADMIN)) as c:
        r = await c.get("/api/v1/platform/health")
    assert r.status_code == 200, r.text
    body = r.json()
    names = [s["service"] for s in body["services"]]
    assert names.count("security-intelligence") == 1
    # What the console showed before is all still there, in the order it was in.
    assert names[-2:] == ["drone-patrol", "security-intelligence"]
    assert {"database", "redis", "recording", "storage:recordings", "storage:evidence"} <= set(names)
    row = body["services"][-1]
    assert row["status"] in STATUSES and row["detail"]
    # The counts are there, so the database was really asked through the console's own session.
    assert set(iph.COUNTS) <= set(row) <= iph.MAY_CARRY, row
    assert all(isinstance(row[name], int) for name in iph.COUNTS)
    assert body["status"] == platform_health.worst([s["status"] for s in body["services"]])

    async with await _console(await _token(ADMIN)) as c:
        assert (await c.get("/api/v1/platform/health")).status_code == 403


# ─── D. The whole layer, swept ───────────────────────────────────────────────

async def _tables() -> list[dict]:
    rows = await _sql("""
        SELECT c.relname AS name, c.relrowsecurity AS rls, c.relforcerowsecurity AS forced,
               (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS policies,
               (SELECT string_agg(coalesce(p.qual,'') || ' / ' || coalesce(p.with_check,''), ' ; ')
                  FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS policy,
               (SELECT a.attnotnull FROM pg_attribute a
                 WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS tenant_not_null,
               has_table_privilege('svc_app', c.oid, 'SELECT') AS can_read,
               has_table_privilege('svc_app', c.oid, 'INSERT') AS can_add,
               has_table_privilege('svc_app', c.oid, 'UPDATE') AS can_change,
               has_table_privilege('svc_app', c.oid, 'DELETE') AS can_remove,
               has_table_privilege('svc_app', c.oid, 'TRUNCATE') AS can_empty,
               has_table_privilege('svc_app', c.oid, 'REFERENCES') AS can_refer,
               has_table_privilege('svc_app', c.oid, 'TRIGGER') AS can_trigger
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.relname LIKE 'security\\_%'
         ORDER BY c.relname""")
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_every_table_of_the_layer_is_one_organisations_and_the_policy_is_forced():
    tables = await _tables()
    assert len(tables) >= 16 and set(RECORDS) <= {t["name"] for t in tables}
    for t in tables:
        assert t["rls"] and t["forced"], f"{t['name']}: tenant isolation is not forced"
        assert t["policies"] == 1, f"{t['name']}: one policy, the same text as every other table"
        assert t["policy"].count("app.current_tenant") == 2, f"{t['name']}: both reading and writing are scoped"
        assert t["tenant_not_null"] is True, f"{t['name']}: a row with no organisation belongs to nobody's policy"
        assert t["can_read"] and t["can_add"], t["name"]
        # TRUNCATE is not subject to row security: with it, one statement from the application's
        # role would empty every organisation's rows, policy or no policy.
        assert not t["can_empty"], f"{t['name']}: the application could empty it for every organisation at once"
        assert not t["can_refer"] and not t["can_trigger"], f"{t['name']}: more than the application has any use for"


@pytest.mark.asyncio
async def test_what_was_assessed_suggested_decided_done_and_said_can_be_added_to_and_never_changed():
    by_name = {t["name"]: t for t in await _tables()}
    for name in RECORDS:
        t = by_name[name]
        assert not t["can_change"] and not t["can_remove"], f"{name}: the application could rewrite the record"
    # And every other table of the layer is working state, which the application works on —
    # each statement under the tenant policy.
    worked_on = sorted(set(by_name) - set(RECORDS))
    assert worked_on == ["security_camera_links", "security_camera_profiles", "security_decision_policies",
                         "security_events", "security_ingest_cursors", "security_site_profiles",
                         "security_situation_events", "security_situations"]
    for name in worked_on:
        assert by_name[name]["can_change"] and by_name[name]["can_remove"], name


@pytest.mark.asyncio
async def test_the_application_role_cannot_empty_a_table_of_the_layer_for_everybody():
    """Asked for, not looked up: the statement itself is refused. A TRUNCATE
    that was let through would not have been stopped by any tenant policy."""
    async with AsyncSessionLocal() as db:
        for table in ("security_events", "security_situations", "security_decision_policies", "security_decisions"):
            with pytest.raises(Exception, match="permission denied"):
                await db.execute(text(f"TRUNCATE {table} CASCADE"))
            await db.rollback()


@pytest.mark.asyncio
async def test_the_layers_two_functions_that_see_past_a_tenant_return_only_ids_and_counts():
    rows = {r["proname"]: dict(r) for r in await _sql("""
        SELECT p.proname, p.prosecdef, p.proconfig, pg_get_function_result(p.oid) AS returns,
               has_function_privilege('svc_app', p.oid, 'EXECUTE') AS app_may,
               has_function_privilege('public', p.oid, 'EXECUTE') AS anyone_may
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND (p.proname LIKE 'security\\_intel%' OR p.proname LIKE 'platform\\_intel%')""")}
    assert set(rows) == {"security_intel_tenants", "platform_intel_health"}
    for name, f in rows.items():
        assert f["prosecdef"], name
        assert "search_path=public, pg_temp" in (f["proconfig"] or []), f"{name}: the search path is not pinned"
        assert f["app_may"] and not f["anyone_may"], name
    assert rows["security_intel_tenants"]["returns"] == "TABLE(tenant_id uuid)"
    assert rows["platform_intel_health"]["returns"] == (
        "TABLE(enabled bigint, events_waiting bigint, situations_waiting bigint, sources_failing bigint)")


def test_every_operation_of_the_layer_asks_for_a_permission_of_the_layer():
    served = _served()
    assert len(served) >= 40
    for (method, path), permissions in served.items():
        assert "intel:read" in permissions, f"{method} {path}: readable without the layer's own permission"
        if method != "GET":
            assert len(permissions) >= 1 and all(":" in p for p in permissions), (method, path)


@pytest.mark.asyncio
async def test_the_platform_owner_and_a_customers_customer_are_refused_every_operation_of_the_layer():
    """Not one operation: all of them, as the route table lists them. Refused
    before anything is looked up, so the answer is the same for a situation
    that exists and one that does not."""
    from tests.test_platform_health import _token

    w = await _world()
    client_user, nothing_here = uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
               "VALUES (:i,:t,7,:e,'x','A Client')",
               {"i": client_user, "t": w["tenant"], "e": f"client-{client_user.hex[:8]}@intel.test"})
    refused = {"the platform owner": {"Authorization": f"Bearer {await _token(SUPER_ADMIN)}"},
               "a client": _auth(client_user, w["tenant"], CLIENT_ROLE)}
    reached = 0
    async with _client() as c:
        for (method, path) in sorted(_served()):
            url = "/api/v1" + re.sub(r"\{[a-z_]+\}", str(nothing_here), path)
            body = {} if method != "GET" else None
            for who, headers in refused.items():
                r = await c.request(method, url, headers=headers, json=body)
                assert r.status_code == 403, f"{who}: {method} {path} answered {r.status_code}"
            assert (await c.request(method, url, json=body)).status_code in (401, 403), f"nobody: {method} {path}"
            # The same request from somebody who works the site gets past the door,
            # so what refused the other two was who they are.
            if method == "GET":
                reached += (await c.get(url, headers=w["h"][ADMIN])).status_code in (200, 404)
    gets = sum(1 for method, _ in _served() if method == "GET")
    assert reached == gets, f"an administrator reached {reached} of {gets} readings"


@pytest.mark.asyncio
async def test_the_seven_permissions_are_held_by_the_roles_that_work_a_site_and_nobody_else():
    rows = await _sql(
        "SELECT rp.role_id, p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        " WHERE p.code LIKE 'intel:%'")
    codes = {r["code"] for r in rows}
    assert codes == {"intel:read", "intel:recommendation:read", "intel:decide", "intel:override", "intel:approve",
                     "intel:manage", "intel:feedback:export"}
    assert {r["role_id"] for r in rows} == {2, 3, 4, 5, 6, 8}, "not the platform owner (1), not the client (7)"


# ─── E. What the runner asks every three seconds ─────────────────────────────

@pytest.mark.asyncio
async def test_each_question_the_runner_and_the_console_repeat_has_an_index_made_for_it():
    """The runner asks "what is new?" and "what changed?" of every organisation
    every three seconds, and the console asks the same of all of them at once.
    Each is answered by an index that holds only the rows still owed, so the
    cost is the size of the backlog and not the size of the history."""
    indexes = {r["indexname"]: r["indexdef"] for r in await _sql(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename LIKE 'security\\_%'")}
    assert "WHERE ((status)::text = 'NEW'::text)" in indexes["idx_secevent_new"]
    assert "(tenant_id, updated_at)" in indexes["idx_secsit_unassessed"]
    assert "WHERE ((assessed_at IS NULL) OR (assessed_at < updated_at))" in indexes["idx_secsit_unassessed"]
    # The decision queue: open situations only, highest risk first.
    assert "WHERE (closed_at IS NULL)" in indexes["idx_secsit_queue"]
    # A suggestion is looked up by the assessment it was written for.
    assert any("(assessment_id" in d for d in indexes.values() if "security_recommendations" in d)


# ─── F. How long the layer takes ─────────────────────────────────────────────

def test_a_stage_gives_a_figure_only_when_enough_were_measured():
    assert pipeline.FLOOR == 5
    few = pipeline.stage("READ", 4, 1.2, 3.4)
    assert few == {"code": "READ", "label": "From an event happening to its being read", "measured": 4,
                   "median_seconds": None, "p95_seconds": None}, "four says how many, and gives no figure"
    enough = pipeline.stage("READ", 5, 1.24, 3.46)
    assert (enough["median_seconds"], enough["p95_seconds"]) == (1.2, 3.5)
    # Two clocks a moment apart can put the reading before the event. Nothing took less than no time.
    assert pipeline.stage("PLACED", 9, -0.4, 0.2)["median_seconds"] == 0.0
    listed = pipeline.stages({"IN_ALL": (12, 2.0, 6.0)})
    assert [x["code"] for x in listed] == ["READ", "PLACED", "ASSESSED", "SUGGESTED", "IN_ALL"], "in the order it happens"
    assert listed[0]["measured"] == 0 and listed[0]["median_seconds"] is None and listed[4]["median_seconds"] == 2.0


def test_measuring_reads_and_writes_nothing():
    code = without_docstrings(SERVICES / "intel_pipeline.py")
    assert "FROM security_events" in code and "FROM security_situations" in code, "the statements are still there"
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", code)
    assert "commit" not in code


async def _pass_now(w: dict) -> None:
    """Read, place, assess, suggest — at the real time, as the runner does."""
    now = datetime.now(timezone.utc)
    await events.ingest_tenant(AsyncSessionLocal, w["tenant"], now=now)
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], now)
    await risk.assess_tenant(AsyncSessionLocal, w["tenant"], now)
    await rec.recommend_tenant(AsyncSessionLocal, w["tenant"], now)


@pytest.mark.asyncio
async def test_how_long_each_stage_took_is_measured_from_what_the_stages_wrote():
    w, other = await _world(), await _world()
    # There before the layer was switched on: read late because it was there first, and so not measured.
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=datetime.now(timezone.utc) - timedelta(minutes=40))
    await _pass_now(w)
    # Six matters of their own at site A — cameras a kilometre apart — and one at site B.
    for n in range(6):
        w[f"far_{n}"] = await _camera(w, "site_a", f"Far {n}", 1.31 + n * 0.01, 103.81)
        await _alert(w, "intrusion", camera=f"far_{n}", site="site_a", code="intrusion.zone_breach",
                     title=f"Person at Far {n}", at=datetime.now(timezone.utc))
    await _alert(w, "intrusion", camera="cam_b", site="site_b", code="intrusion.zone_breach",
                 at=datetime.now(timezone.utc))
    await _pass_now(w)
    assert len(await _sql("SELECT 1 FROM security_situations WHERE tenant_id = :t", {"t": w["tenant"]})) == 8

    async with _client() as c:
        everything = await c.get(f"{BASE}/pipeline", headers=w["h"][ADMIN])
        one_site = await c.get(f"{BASE}/pipeline", headers=w["h"][SUPERVISOR], params={"hours": 1})
        elsewhere = await c.get(f"{BASE}/pipeline", headers=other["h"][OPERATOR])
        too_far = await c.get(f"{BASE}/pipeline", headers=w["h"][ADMIN], params={"hours": 169})
        none = await c.get(f"{BASE}/pipeline", headers=w["h"][ADMIN], params={"hours": 0})
    assert everything.status_code == 200, everything.text
    body = everything.json()
    assert body["hours"] == 24 and body["floor"] == pipeline.FLOOR and body["note"] == pipeline.NOTE
    stages = {x["code"]: x for x in body["stages"]}
    assert list(stages) == [code for code, _ in pipeline.STAGES]
    assert {code: x["measured"] for code, x in stages.items()} == dict.fromkeys(stages, 7), \
        "seven that happened after it was switched on; the one from before is left out of every stage"
    for code, x in stages.items():
        assert 0 <= x["median_seconds"] <= x["p95_seconds"] < 120, (code, x)
    # Somebody restricted to site A is told about site A.
    assert {x["measured"] for x in one_site.json()["stages"]} == {6}
    # Another organisation has measured nothing, and is given no figure made of nothing.
    assert all(x["measured"] == 0 and x["median_seconds"] is None for x in elsewhere.json()["stages"])
    assert too_far.status_code == 422 and none.status_code == 422


# ─── G. The process itself ───────────────────────────────────────────────────

async def _until(check, what: str, seconds: float = 60.0):
    """Wait for something the runner should do, and say what never happened."""
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        found = await check()
        if found:
            return found
        assert asyncio.get_running_loop().time() < deadline, f"after {seconds:.0f} s the runner had not done this: {what}"
        await asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_the_runner_started_as_it_is_deployed_reads_assesses_suggests_reports_itself_and_stops():
    """Every other test of the runner calls its functions. This one starts the
    process — the command the compose file and the chart run — against the
    test database as the application's role, gives it something to read, and
    reads what it wrote. A heartbeat only a test writes says nothing about the
    loop that is supposed to write it."""
    w = await _world()
    for n in range(3):
        w[f"far_{n}"] = await _camera(w, "site_a", f"Far {n}", 1.31 + n * 0.01, 103.81)
    tenant = {"t": w["tenant"]}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.intelligence_main", cwd=str(SERVICES.parents[1]),
        env={**os.environ, "INTEL_RUNNER_TICK_SECONDS": "1", "INTEL_RUNNER_MIN_GAP_SECONDS": "0.2"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        # Its first pass for this organisation: from here on, what happens is read as it happens.
        await _until(lambda: _sql("SELECT 1 FROM security_ingest_cursors WHERE tenant_id = :t", tenant), "a first read")
        for n in range(3):
            await _alert(w, "intrusion", camera=f"far_{n}", site="site_a", code="intrusion.zone_breach",
                         severity="critical", title=f"Person at Far {n}", at=datetime.now(timezone.utc))

        async def suggested():
            rows = await _sql(
                "SELECT s.id FROM security_situations s WHERE s.tenant_id = :t AND EXISTS "
                "  (SELECT 1 FROM security_recommendations r WHERE r.assessment_id = s.assessment_id)", tenant)
            return rows if len(rows) == 3 else None

        await _until(suggested, "three situations, each assessed and with suggestions")

        # It reports itself, in counts, and the vendor's row is built from that report.
        asked, beat = await intel_runner.ask_heartbeat()
        assert asked and beat is not None, "the loop wrote no heartbeat"
        assert set(beat) == {"at", "ok", "tenants", "events", "failed"} and beat["tenants"] >= 1
        async with AsyncSessionLocal() as db:
            row = await iph.check_security_intelligence(db)
            await db.rollback()
        assert row["status"] in ("ok", "degraded") and row["detail"].startswith("runner alive"), row
        assert row["enabled"] >= 1 and row["heartbeat_age_seconds"] < 30

        # Each stage stamped what it wrote, so how long it took can be read back.
        async with _client() as c:
            measured = (await c.get(f"{BASE}/pipeline", headers=w["h"][ADMIN])).json()["stages"]
        assert {x["code"]: x["measured"] for x in measured} == dict.fromkeys([code for code, _ in pipeline.STAGES], 3)

        # And it did nothing but record: three critical alerts, and not one of them touched.
        assert await _sql("SELECT 1 FROM security_decisions WHERE tenant_id = :t", tenant) == []
        assert await _sql("SELECT 1 FROM security_actions WHERE tenant_id = :t", tenant) == []
        assert await _sql("SELECT 1 FROM incidents WHERE tenant_id = :t", tenant) == []
        untouched = await _sql("SELECT status, acknowledged_at FROM alerts WHERE tenant_id = :t", tenant)
        assert len(untouched) == 3 and all(a["acknowledged_at"] is None for a in untouched)
        assert len({a["status"] for a in untouched}) == 1, "as they were raised"

        # Told to stop, it finishes its pass and stops.
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), 30)
        said = (await process.stdout.read()).decode(errors="replace")
        assert process.returncode == 0, said[-2000:]
        assert "intelligence runner started" in said and "intelligence runner stopping" in said
        assert "Traceback" not in said, said[-2000:]
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
