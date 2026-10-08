"""How long records are kept, and where a person appears in them (phase 13).

A. The statement gives every period as the job that applies it reads it.
B. Everything a job deletes, and everything the expansion added, is in it.
C. Who may read the statement and who may ask about a person.
D. A subject report counts every column that names a member of staff.
E. A visitor, and a name or a plate as typed.

The statement and the reports are read as the application's role, under row
level security, the way the API reads them.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.main import app
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import data_governance as api
from app.services import continuous_recording, drone_retention, drone_runner, evidence_hold, recording_policy
from app.services import retention_statement as statement
from app.services import subject_records as subjects
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_investigation_search import _audit
from tests.test_visitor_authorizations import _visit

BASE = "/api/v1/data-governance"
APP = Path(__file__).resolve().parents[1] / "app"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
#: The migrations of the enterprise expansion that create tables.
EXPANSION = range(143, 156)


def _expansion_tables() -> list[str]:
    found = []
    for path in sorted(VERSIONS.glob("01*.py")):
        if int(path.name[:4]) in EXPANSION:
            upgrade = path.read_text(encoding="utf-8").split("def upgrade", 1)[1].split("def downgrade", 1)[0]
            found += re.findall(r"CREATE TABLE (\w+)", upgrade)
    return found


async def _as_app(w: dict, work):
    """Run `work(db)` as the application's role, in the organisation's scope."""
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        try:
            return await work(db)
        finally:
            await db.rollback()


async def _set(w: dict, key: str, value) -> None:
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
               "VALUES (:t, :k, CAST(:v AS jsonb), :u)",
               {"t": w["tenant"], "k": key, "v": str(value) if isinstance(value, int) else f'"{value}"',
                "u": w["users"][ADMIN]})


# ─── A. Every period, as the job reads it ────────────────────────────────────

async def test_the_statement_gives_every_period_as_the_job_that_applies_it_reads_it():
    w = await _world()
    async with _client() as c:
        first = (await c.get(f"{BASE}/retention", headers=w["h"][ADMIN])).json()
    by = {p["key"]: p for p in first["periods"]}
    assert list(by) == ["EVIDENCE", "RECORDINGS", "DRONE_FOOTAGE", "DRONE_TRACKS", "DRONE_RECEIPTS", "AUDIT"]
    # Nothing set: each falls back to the installation's default, and says that the job's own is the one in force.
    assert (by["EVIDENCE"]["period"]["amount"], by["EVIDENCE"]["period"]["unit"]) == (settings.EVIDENCE_RETENTION_DAYS, "days")
    assert by["RECORDINGS"]["period"]["amount"] == continuous_recording.DEFAULT_RETENTION_DAYS
    assert by["DRONE_TRACKS"]["period"]["amount"] == drone_retention.DEFAULT_TELEMETRY_DAYS
    for key in ("EVIDENCE", "RECORDINGS", "DRONE_FOOTAGE", "DRONE_TRACKS", "AUDIT"):
        assert by[key]["period"]["source"] == "INSTALLATION" and by[key]["period"]["note"] == statement.INSTALLATION_NOTE, key
        assert by[key]["period"]["set_at"] is None
    assert by["DRONE_FOOTAGE"]["period"]["amount"] == by["EVIDENCE"]["period"]["amount"], "footage follows evidence"
    assert by["DRONE_FOOTAGE"]["setting_key"] == "evidence.retention_days"
    assert (by["DRONE_RECEIPTS"]["period"]["amount"], by["DRONE_RECEIPTS"]["period"]["source"],
            by["DRONE_RECEIPTS"]["period"]["note"]) == (drone_runner.RECEIPT_RETENTION.days, "FIXED", None)
    assert (by["AUDIT"]["period"]["amount"], by["AUDIT"]["period"]["unit"], by["AUDIT"]["per_organisation"]) == (
        settings.AUDIT_RETENTION_YEARS, "years", False)
    assert "Not deleted" in by["AUDIT"]["how"] and all(by[k]["per_organisation"] for k in by if k != "AUDIT")
    # What a hold stops is what the jobs ask about, in the jobs' own words.
    assert {k: p["hold_stops_it"] for k, p in by.items()} == {
        "EVIDENCE": True, "RECORDINGS": True, "DRONE_FOOTAGE": True, "DRONE_TRACKS": False, "DRONE_RECEIPTS": False, "AUDIT": False}
    held = {p.key: p.hold for p in statement.PERIODS}
    assert (held["EVIDENCE"], held["RECORDINGS"], held["DRONE_FOOTAGE"]) == (
        evidence_hold.FRAMES_AND_CLIPS, evidence_hold.RECORDINGS, evidence_hold.DRONE_MEDIA)
    assert by["EVIDENCE"]["held_now"] == 0 and by["DRONE_TRACKS"]["held_now"] is None
    assert "became an incident" in by["DRONE_FOOTAGE"]["kept_past"] and by["EVIDENCE"]["kept_past"] is None
    assert first["not_law"] == statement.NOT_LAW and first["everything_else"] == statement.EVERYTHING_ELSE
    assert [s["site_name"] for s in first["sites"]] == ["Factory A", "Factory B"]
    assert all(s["source"] == "INSTALLATION" and not s["keeps_none"] for s in first["sites"])

    # The organisation sets its own; a site sets its own; a hold is placed.
    await _set(w, "evidence.retention_days", 30)
    await _set(w, "recording.retention_days", 14)
    await _set(w, "drone.telemetry_retention_days", 100)
    await _run([
        ("INSERT INTO recording_policies (tenant_id, site_id, central_retention_days, local_retention_days, updated_by_user_id) "
         "VALUES (:t, :s, 3, 2, :u)", {"t": w["tenant"], "s": w["site_a"], "u": w["users"][ADMIN]}),
        ("INSERT INTO evidence_holds (tenant_id, kind, ref_id, site_id, reason, placed_by_user_id) VALUES (:t, 'SNAPSHOT', :r, :s, 'A claim', :u)",
         {"t": w["tenant"], "r": uuid.uuid4(), "s": w["site_a"], "u": w["users"][ADMIN]}),
        ("INSERT INTO evidence_holds (tenant_id, kind, ref_id, site_id, reason, placed_by_user_id) VALUES (:t, 'RECORDING', :r, :s, 'A claim', :u)",
         {"t": w["tenant"], "r": uuid.uuid4(), "s": w["site_b"], "u": w["users"][ADMIN]}),
        ("INSERT INTO evidence_holds (tenant_id, kind, ref_id, site_id, reason, placed_by_user_id, released_by_user_id, released_at, release_reason) "
         "VALUES (:t, 'CLIP', :r, :s, 'A claim', :u, :u, now(), 'Settled')",
         {"t": w["tenant"], "r": uuid.uuid4(), "s": w["site_a"], "u": w["users"][ADMIN]}),
    ])
    async with _client() as c:
        second = (await c.get(f"{BASE}/retention", headers=w["h"][MANAGER])).json()
        held_to_a = (await c.get(f"{BASE}/retention", headers=w["h"][SUPERVISOR])).json()
    by = {p["key"]: p for p in second["periods"]}
    assert [(by[k]["period"]["amount"], by[k]["period"]["source"]) for k in ("EVIDENCE", "RECORDINGS", "DRONE_FOOTAGE", "DRONE_TRACKS")] == [
        (30, "TENANT_SETTING"), (14, "TENANT_SETTING"), (30, "TENANT_SETTING"), (100, "TENANT_SETTING")]
    assert by["EVIDENCE"]["period"]["note"] is None and by["EVIDENCE"]["period"]["set_at"]
    assert by["AUDIT"]["period"]["source"] == "INSTALLATION", "the audit log has no setting of the organisation's"
    sites = {s["site_name"]: s for s in second["sites"]}
    assert (sites["Factory A"]["recording_days"], sites["Factory A"]["source"], sites["Factory A"]["at_the_site_days"]) == (3, "SITE_POLICY", 2)
    assert (sites["Factory B"]["recording_days"], sites["Factory B"]["source"]) == (14, "TENANT_SETTING")
    assert second["holds"] == {"in_force": 2, "by_kind": {"SNAPSHOT": 1, "RECORDING": 1}, "words": second["holds"]["words"]}
    assert (by["EVIDENCE"]["held_now"], by["RECORDINGS"]["held_now"], by["DRONE_FOOTAGE"]["held_now"]) == (1, 1, 0)
    # Somebody held to site A is given site A and its hold.
    assert [s["site_name"] for s in held_to_a["sites"]] == ["Factory A"] and held_to_a["holds"]["in_force"] == 1
    assert {p["key"]: p["held_now"] for p in held_to_a["periods"]}["RECORDINGS"] == 0

    # The jobs' own functions, as the application's role, give the same numbers.
    async def jobs(db):
        return (await drone_retention._setting_days(db, "evidence.retention_days", settings.EVIDENCE_RETENTION_DAYS),
                await drone_retention._setting_days(db, "drone.telemetry_retention_days", drone_retention.DEFAULT_TELEMETRY_DAYS),
                await recording_policy.get_tenant_retention_days(db, continuous_recording.DEFAULT_RETENTION_DAYS),
                await recording_policy.get_effective_retention_days(db, str(w["site_a"]), continuous_recording.DEFAULT_RETENTION_DAYS),
                await recording_policy.get_effective_retention_days(db, str(w["site_b"]), continuous_recording.DEFAULT_RETENTION_DAYS))
    assert await _as_app(w, jobs) == (30, 100, 14, 3, 14)

    # A site that keeps nothing centrally says so, and a setting no job would accept falls back as the job does.
    await _run([("UPDATE recording_policies SET central_retention_days = 0 WHERE site_id = :s", {"s": w["site_a"]}),
                ("UPDATE tenant_settings SET setting_value = CAST('0' AS jsonb) WHERE tenant_id = :t AND setting_key = 'recording.retention_days'",
                 {"t": w["tenant"]}),
                ("UPDATE tenant_settings SET setting_value = CAST('\"soon\"' AS jsonb) WHERE tenant_id = :t AND setting_key = 'drone.telemetry_retention_days'",
                 {"t": w["tenant"]})])
    async with _client() as c:
        third = (await c.get(f"{BASE}/retention", headers=w["h"][VIEWER])).json()
    by = {p["key"]: p for p in third["periods"]}
    sites = {s["site_name"]: s for s in third["sites"]}
    assert (sites["Factory A"]["recording_days"], sites["Factory A"]["keeps_none"]) == (0, True)
    assert (by["RECORDINGS"]["period"]["amount"], by["RECORDINGS"]["period"]["source"]) == (continuous_recording.DEFAULT_RETENTION_DAYS, "INSTALLATION")
    assert (by["DRONE_TRACKS"]["period"]["amount"], by["DRONE_TRACKS"]["period"]["source"]) == (drone_retention.DEFAULT_TELEMETRY_DAYS, "INSTALLATION")

    async def jobs_again(db):
        return (await recording_policy.get_tenant_retention_days(db, continuous_recording.DEFAULT_RETENTION_DAYS),
                await drone_retention._setting_days(db, "drone.telemetry_retention_days", drone_retention.DEFAULT_TELEMETRY_DAYS))
    assert await _as_app(w, jobs_again) == (continuous_recording.DEFAULT_RETENTION_DAYS, drone_retention.DEFAULT_TELEMETRY_DAYS)
    # Reading it wrote nothing: it names no person and is not audited.
    assert not await _sql("SELECT 1 FROM audit_logs WHERE tenant_id = :t", {"t": w["tenant"]})


def test_a_period_is_days_the_job_would_accept_or_the_default():
    assert statement._days(30, 90) == (30, True) and statement._days(1, 90) == (1, True)
    for refused in (0, -3, None, "30", 2.5, True):
        assert statement._days(refused, 90) == (90, False), refused
    assert set(statement.SOURCE_WORDS) == set(statement.SOURCES)
    assert "law" in statement.NOT_LAW and "removed by no job" in statement.EVERYTHING_ELSE


# ─── B. What a job deletes, and what the expansion added ─────────────────────

def test_everything_a_job_deletes_is_in_the_statement():
    """A table a job deletes from by age has its period in the statement. Routers are a person's step, and the
    drone gateway's store is its own disk at the site."""
    deleted: dict[str, set[str]] = {}
    for path in [*sorted((APP / "services").glob("*.py")), *sorted(APP.glob("*_main.py")), *sorted((APP / "core").glob("*.py"))]:
        for table in re.findall(r"DELETE FROM (\w+)", path.read_text(encoding="utf-8")):
            deleted.setdefault(table, set()).add(path.name)
    stated = {table for p in statement.PERIODS for table in p.tables}
    assert set(deleted) - stated == set(statement.RECOMPUTED), deleted
    assert stated - set(deleted) == {"audit_logs"}, "the audit log is moved out, not deleted"
    assert "DETACH PARTITION" in (APP / "scheduler_main.py").read_text(encoding="utf-8")
    # Each is removed by the service the statement names.
    by = {p.key: p for p in statement.PERIODS}
    assert deleted["evidence"] == {"scheduler_main.py"} and by["EVIDENCE"].removed_by.startswith("The scheduler")
    assert deleted["recordings"] == {"continuous_recording.py"} and "recording supervisor" in by["RECORDINGS"].removed_by
    assert deleted["drone_event_media"] == deleted["drone_telemetry"] == {"drone_retention.py"}
    assert "drone runner" in by["DRONE_FOOTAGE"].removed_by and "drone runner" in by["DRONE_TRACKS"].removed_by
    assert deleted["drone_sync_receipts"] == {"drone_runner.py"}
    # No job of the platform deletes from anything the expansion added.
    assert not set(deleted) & set(_expansion_tables())
    scheduler = (APP / "scheduler_main.py").read_text(encoding="utf-8")
    assert "not_held(FRAMES_AND_CLIPS" in scheduler and "not_held(RECORDINGS" in (APP / "services" / "continuous_recording.py").read_text(encoding="utf-8")
    assert "not_held(DRONE_MEDIA" in (APP / "services" / "drone_retention.py").read_text(encoding="utf-8")


async def test_every_table_the_expansion_added_is_stated_as_kept_with_whether_it_names_a_person():
    tables = _expansion_tables()
    assert len(tables) == len(set(tables)) == 36
    kept = [t for k in statement.KEPT for t in k.tables]
    assert sorted(kept) == sorted(tables) and len(kept) == len(set(kept)), "each is stated once"
    # Whether a table names a person is what the database's own catalogue says of its columns.
    refers = await _sql("""
        SELECT c.conrelid::regclass::text AS tbl, a.attname AS col, c.confrelid::regclass::text AS refs
          FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.contype = 'f' AND c.confrelid::regclass::text IN ('users', 'visitors')
           AND c.conrelid::regclass::text = ANY(:tables)
    """, {"tables": tables})
    to_staff = {(r["tbl"], r["col"]) for r in refers if r["refs"] == "users"}
    assert {(h.table, column) for h in subjects.STAFF for column in h.columns} == to_staff and len(to_staff) == 71
    assert {r["tbl"] for r in refers if r["refs"] == "visitors"} == {"visitor_authorizations"}
    for h in subjects.STAFF:
        for column, (part, words) in h.columns.items():
            assert part in (subjects.ABOUT, subjects.BY) and words and words[0].isupper(), (h.table, column)
    # A time is read from a column the table has, and so is a name as text.
    columns = {(r["table_name"], r["column_name"]) for r in await _sql(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = ANY(:t)",
        {"t": tables})}
    assert all((h.table, h.when) in columns for h in subjects.STAFF)
    assert all((table, column) in columns for table, named in subjects.TEXT_COLUMNS.items() for column in named)
    naming = {r["tbl"] for r in refers} | set(subjects.TEXT_COLUMNS) | set(subjects.VISITOR_TABLES)
    assert {t for t in tables if subjects.names_people(t)} == naming
    assert {t for t in tables if not subjects.names_people(t)} == {"device_health_changes", "sop_passages", "sop_incident_types"}
    w = await _world()
    async with _client() as c:
        answer = (await c.get(f"{BASE}/retention", headers=w["h"][ADMIN])).json()
    assert {t["name"]: t["names_people"] for k in answer["kept"] for t in k["tables"]} == {t: t in naming for t in tables}
    # A row leaves three of them by a person's own step, and the application's role may delete from no other.
    may_delete = {r["t"] for r in await _sql(
        "SELECT c.relname AS t FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(:t) "
        "AND has_table_privilege('svc_app', c.oid, 'DELETE')", {"t": tables})}
    taken = {t for k in statement.KEPT for t in (k.taken_away or {})}
    assert may_delete == {"evidence_package_items", "sop_incident_types", "visitor_authorization_places"}
    assert taken == may_delete | {"visitor_authorizations"}, "an authorisation goes with its visitor, by the database's own rule"
    rule = await _sql("SELECT confdeltype FROM pg_constraint WHERE conname = 'visitor_authorizations_visitor_id_fkey'")
    assert rule[0]["confdeltype"] in ("c", b"c")


# ─── C. Who may read the statement, and who may ask about a person ───────────

async def test_who_may_read_the_statement_and_who_may_ask_about_a_person():
    w = await _world()
    visit = await _visit(w)
    staff_url = f"{BASE}/subjects/staff/{w['users'][GUARD]}"
    asks = (("get", staff_url, None), ("get", f"{BASE}/subjects/visitor/{visit}", None),
            ("post", f"{BASE}/subjects/written", {"text": "Tan Wei Ming"}),
            ("post", f"{BASE}/subjects/find", {"kind": "STAFF", "words": "Role"}))
    async with _client() as c:
        for role, statement_status, subject_status in ((ADMIN, 200, 200), (MANAGER, 200, 200), (SUPERVISOR, 200, 403),
                                                       (VIEWER, 200, 403), (OPERATOR, 403, 403), (GUARD, 403, 403)):
            assert (await c.get(f"{BASE}/retention", headers=w["h"][role])).status_code == statement_status, role
            for method, url, body in asks:
                got = await (c.get(url, headers=w["h"][role]) if method == "get" else c.post(url, headers=w["h"][role], json=body))
                assert got.status_code == subject_status, (role, url, got.text)
        assert (await c.get(f"{BASE}/retention")).status_code in (401, 403)
        # What is asked is checked before anything is read.
        for body in ({"text": "ab"}, {"text": "x" * 81}, {"text": "Tan", "site": "x"}, {}):
            assert (await c.post(f"{BASE}/subjects/written", headers=w["h"][ADMIN], json=body)).status_code == 422, body
        assert (await c.post(f"{BASE}/subjects/written", headers=w["h"][ADMIN], json={"text": "  a   b  "})).status_code == 200
        assert (await c.post(f"{BASE}/subjects/written", headers=w["h"][ADMIN], json={"text": "   ab    "})).status_code == 422
        assert (await c.post(f"{BASE}/subjects/find", headers=w["h"][ADMIN], json={"kind": "CAMERA", "words": "Role"})).status_code == 422
        assert (await c.get(f"{BASE}/subjects/staff/not-an-id", headers=w["h"][ADMIN])).status_code == 404
        assert (await c.get(f"{BASE}/subjects/staff/{uuid.uuid4()}", headers=w["h"][ADMIN])).status_code == 404
        assert (await c.get(f"{BASE}/subjects/visitor/{uuid.uuid4()}", headers=w["h"][ADMIN])).status_code == 404

    # A subject report is the organisation's own, by a person, and covers every site.
    me = {"user_id": str(w["users"][ADMIN]), "tenant_id": str(w["tenant"]), "role_id": ADMIN}
    with pytest.raises(HTTPException) as key:
        api._the_organisations_own(TokenPayload(**me, via_api_key=True), None)
    with pytest.raises(HTTPException) as support:
        api._the_organisations_own(TokenPayload(**me, support_session_id=str(uuid.uuid4())), None)
    with pytest.raises(HTTPException) as held:
        api._the_organisations_own(TokenPayload(**me), [str(w["site_a"])])
    assert (key.value.status_code, support.value.status_code, held.value.status_code) == (403, 403, 403)
    assert "not by an API key" in key.value.detail and "not from a support session" in support.value.detail
    assert held.value.detail == api.EVERY_SITE
    api._the_organisations_own(TokenPayload(**me), None)
    app.dependency_overrides[get_token_payload] = lambda: TokenPayload(**me, via_api_key=True)
    try:
        async with _client() as c:
            for method, url, body in asks:
                got = await (c.get(url) if method == "get" else c.post(url, json=body))
                assert got.status_code == 403 and "not by an API key" in got.json()["detail"], (url, got.text)
            assert (await c.get(f"{BASE}/retention")).status_code == 200, "the statement names nobody"
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    # Only a report that was given is on the record: one each for the two who may ask, and none for a refusal.
    reports = await _audit(w, "subject.report")
    assert sorted(r["detail"]["kind"] for r in reports) == ["STAFF", "STAFF", "TEXT", "TEXT", "TEXT", "VISITOR", "VISITOR"]
    assert {r["user_id"] for r in reports} == {w["users"][ADMIN], w["users"][MANAGER]}


# ─── D. A member of staff ────────────────────────────────────────────────────

async def _a_case(c, w: dict) -> dict:
    """A case the admin opens and the operator works: an investigator, a task, a note and two names."""
    admin, operator = w["h"][ADMIN], w["h"][OPERATOR]
    case = (await c.post("/api/v1/cases", headers=admin, json={
        "title": "Forced gate", "summary": "The east gate was forced.", "site_id": str(w["site_a"])})).json()
    url = f"/api/v1/cases/{case['id']}"
    for path, body in (("investigators", {"user_id": str(w["users"][OPERATOR])}),
                       ("tasks", {"title": "Ask the haulier", "assigned_to_user_id": str(w["users"][OPERATOR])})):
        r = await c.post(f"{url}/{path}", headers=admin, json=body)
        assert r.status_code in (200, 201), r.text
    for path, body in (("notes", {"body": "Spoke to the gatehouse."}),
                       ("parties", {"kind": "PERSON", "label": "Tan Wei Ming", "connection": "WITNESS"}),
                       ("parties", {"kind": "VEHICLE", "label": "SGX 1234 A", "connection": "NAMED"})):
        r = await c.post(f"{url}/{path}", headers=operator, json=body)
        assert r.status_code == 201, r.text
    return case


async def test_a_staff_report_counts_every_column_that_names_them_and_says_who_looked():
    w, other = await _world(), await _world()
    operator, admin = str(w["users"][OPERATOR]), str(w["users"][ADMIN])
    async with _client() as c:
        await _a_case(c, w)
        await _a_case(c, other)
        # A guard is sent to an incident; a recommendation about them is answered.
        incident = uuid.uuid4()
        await _run([
            ("INSERT INTO incidents (id, tenant_id, title, severity, status) VALUES (:i, :t, 'Gate', 'high', 'open')",
             {"i": incident, "t": w["tenant"]}),
            ("INSERT INTO incident_responses (tenant_id, incident_id, site_id, guard_user_id, dispatched_at, state) "
             "VALUES (:t, :i, :s, :g, :at, 'SENT')",
             {"t": w["tenant"], "i": incident, "s": w["site_a"], "g": w["users"][OPERATOR],
              "at": datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)}),
            ("INSERT INTO workforce_advice_answers (tenant_id, kind, subject_user_id, advice_key, code, statement, rests_on, answer, answered_by_user_id) "
             "VALUES (:t, 'TRAINING', :g, 'k', 'MISSED_TOURS', 'Missed 3 of 5 tours.', CAST('{}' AS jsonb), 'ACCEPTED', :u)",
             {"t": w["tenant"], "g": w["users"][OPERATOR], "u": w["users"][ADMIN]}),
        ])
        # Somebody searches for them, twice; somebody else searches for another person.
        for who in (ADMIN, MANAGER):
            r = await c.post("/api/v1/investigations/search", headers=w["h"][who], json={"staff_user_id": operator})
            assert r.status_code == 200, r.text
        await c.post("/api/v1/investigations/search", headers=w["h"][ADMIN], json={"staff_user_id": str(w["users"][GUARD])})

        got = await c.get(f"{BASE}/subjects/staff/{operator}", headers=w["h"][ADMIN])
        assert got.status_code == 200, got.text
        report = got.json()
        theirs = (await c.get(f"{BASE}/subjects/staff/{admin}", headers=w["h"][MANAGER])).json()
        # Another organisation's person is nobody here, and its records are not counted here.
        assert (await c.get(f"{BASE}/subjects/staff/{other['users'][OPERATOR]}", headers=w["h"][ADMIN])).status_code == 404
        found = (await c.post(f"{BASE}/subjects/find", headers=w["h"][ADMIN], json={"kind": "STAFF", "words": "role 4"})).json()

    assert report["subject"] == {"kind": "STAFF", "id": operator, "name": "Role 4 User", "role": report["subject"]["role"], "in_use": True}
    lines = {(h["table"], line["column"]): line for h in report["held"] for line in h["lines"]}
    assert {key: (line["part"], line["count"]) for key, line in lines.items()} == {
        ("incident_responses", "guard_user_id"): ("ABOUT", 1),
        ("workforce_advice_answers", "subject_user_id"): ("ABOUT", 1),
        ("case_investigators", "user_id"): ("ABOUT", 1),
        ("case_tasks", "assigned_to_user_id"): ("ABOUT", 1),
        ("case_entries", "actor_user_id"): ("BY", 1),
        ("case_parties", "added_by_user_id"): ("BY", 2),
    }
    assert lines[("incident_responses", "guard_user_id")]["words"] == "Was sent to the incident"
    assert lines[("incident_responses", "guard_user_id")]["first"].startswith("2026-09-01T08:00")
    assert report["totals"] == {"about": 4, "by": 3, "kinds_of_record": 6}
    assert [h["label"] for h in report["held"]] == [h.label for h in subjects.STAFF if h.table in {t for t, _ in lines}]
    assert len(report["held"]) + len(report["nothing_in"]) == len(subjects.STAFF) and "Daily briefings" in report["nothing_in"]
    assert (report["searches"]["count"], report["searches"]["by_people"]) == (2, 2)
    assert report["what_it_is"] == subjects.WHAT_IT_IS and report["not_read"] == list(subjects.NOT_READ)
    # It says where and how often. Nothing of what a record says is in it.
    said = str(report)
    for inside in ("Forced gate", "Ask the haulier", "Spoke to the gatehouse", "Tan Wei Ming", "SGX", "Missed 3 of 5"):
        assert inside not in said, inside
    # The admin opened the case and leads it; gave the task; put the investigator on; answered the recommendation.
    mine = {(h["table"], line["column"]): (line["part"], line["count"]) for h in theirs["held"] for line in h["lines"]}
    assert mine[("case_files", "lead_user_id")] == ("ABOUT", 1) and mine[("case_files", "opened_by_user_id")] == ("BY", 1)
    assert mine[("case_investigators", "added_by_user_id")] == mine[("case_tasks", "created_by_user_id")] == ("BY", 1)
    assert mine[("workforce_advice_answers", "answered_by_user_id")] == ("BY", 1) and theirs["searches"]["count"] == 0
    assert found == {"kind": "STAFF", "found": [{"id": operator, "name": "Role 4 User", "detail": found["found"][0]["detail"], "in_use": True}],
                     "more": False}

    # Each report is one line in the audit log: who asked, about whom, and how much was found - and no name.
    reports = await _audit(w, "subject.report")
    assert [(r["user_id"], r["resource_type"], str(r["resource_id"])) for r in reports] == [
        (w["users"][ADMIN], "subject_report", operator), (w["users"][MANAGER], "subject_report", admin)]
    assert {k: reports[0]["detail"][k] for k in ("kind", "words", "kinds_of_record", "found", "searches", "source")} == {
        "kind": "STAFF", "words": None, "kinds_of_record": 6, "found": 7, "searches": 2, "source": "user"}
    assert "Role 4 User" not in str(reports[0]["detail"])
    assert not await _audit(other, "subject.report") and not await _audit(w, "subject.find")

    # As the application's role, under row level security, it reads the same - and changes nothing.
    before = await _sql("SELECT count(*) AS n FROM case_entries WHERE tenant_id = :t", {"t": w["tenant"]})
    again = await _as_app(w, lambda db: subjects.staff(db, operator))
    assert again["totals"] == report["totals"]
    assert await _as_app(other, lambda db: subjects.staff(db, operator)) is None, "nobody of that id in the other organisation"
    assert await _sql("SELECT count(*) AS n FROM case_entries WHERE tenant_id = :t", {"t": w["tenant"]}) == before


# ─── E. A visitor, and a name as typed ───────────────────────────────────────

async def test_a_visitor_report_and_a_name_or_a_plate_as_it_was_typed():
    w, other = await _world(), await _world()
    visit, elsewhere = await _visit(w, "Lim Mei Ling"), await _visit(other, "Lim Mei Ling")
    async with _client() as c:
        asked = await c.post("/api/v1/visitor-authorizations", headers=w["h"][GUARD], json={"visitor_id": str(visit)})
        assert asked.status_code == 201, asked.text
        await _a_case(c, w)
        await _a_case(c, other)
        await _sql("INSERT INTO maintenance_work_orders (tenant_id, number, site_id, title, kind, priority, state, origin, "
                   "    raised_by_user_id, assigned_to_name) "
                   "VALUES (:t, 'WO-0001', :s, 'Gate motor', 'CORRECTIVE', 'NORMAL', 'OPEN', 'PERSON', :u, 'Tan Wei Ming Engineering')",
                   {"t": w["tenant"], "s": w["site_a"], "u": w["users"][ADMIN]})
        # Somebody follows the plate, and somebody searches the name.
        assert (await c.get("/api/v1/investigations/trail", headers=w["h"][ADMIN], params={"plate": "SGX1234A"})).status_code == 200
        assert (await c.post("/api/v1/investigations/search", headers=w["h"][MANAGER], json={"person": "Tan Wei Ming"})).status_code == 200

        visitor = (await c.get(f"{BASE}/subjects/visitor/{visit}", headers=w["h"][ADMIN])).json()
        assert (await c.get(f"{BASE}/subjects/visitor/{elsewhere}", headers=w["h"][ADMIN])).status_code == 404
        people = (await c.post(f"{BASE}/subjects/find", headers=w["h"][ADMIN], json={"kind": "VISITOR", "words": "mei ling"})).json()
        name = (await c.post(f"{BASE}/subjects/written", headers=w["h"][ADMIN], json={"text": "tan  wei ming"})).json()
        plate = (await c.post(f"{BASE}/subjects/written", headers=w["h"][MANAGER], json={"text": "sgx1234a"})).json()
        wild = (await c.post(f"{BASE}/subjects/written", headers=w["h"][ADMIN], json={"text": "%_%"})).json()

    assert visitor["subject"] == {"kind": "VISITOR", "id": str(visit), "name": "Lim Mei Ling", "company": "Acme Lifts", "site_id": str(w["site_a"])}
    assert [(h["table"], h["lines"][0]["count"], h["lines"][0]["part"]) for h in visitor["held"]] == [("visitor_authorizations", 1, "ABOUT")]
    assert visitor["totals"] == {"about": 1, "by": 0, "kinds_of_record": 1} and visitor["searches"] is None
    assert len(visitor["nothing_in"]) == 2 and "Visitors screen" in visitor["not_read"][0]
    assert [p["name"] for p in people["found"]] == ["Lim Mei Ling"] and people["found"][0]["id"] == str(visit), "this organisation's only"

    # A name is found where somebody wrote it, as text - in this organisation's cases and work orders only.
    assert name["subject"] == {"kind": "TEXT", "text": "tan wei ming", "as_plate": "TANWEIMING"}
    by_table = {h["table"]: h for h in name["held"]}
    assert (by_table["case_parties"]["count"], by_table["case_parties"]["distinct"]) == (1, 1)
    assert by_table["case_parties"]["matches"][0] | {"first": None, "last": None} == {
        "text": "Tan Wei Ming", "count": 1, "kind": "PERSON", "cases": 1, "taken_off": 0, "first": None, "last": None}
    assert by_table["maintenance_work_orders"]["matches"][0]["text"] == "Tan Wei Ming Engineering"
    assert name["totals"] == {"matches": 2, "kinds_of_record": 2} and name["text_is_text"] == subjects.TEXT_IS_TEXT
    assert (name["searches"]["count"], name["searches"]["by_people"]) == (1, 1)
    # A plate is the same plate however it was spaced; who followed it is counted.
    assert plate["subject"]["as_plate"] == "SGX1234A"
    assert [(h["table"], h["matches"][0]["text"], h["matches"][0]["kind"]) for h in plate["held"]] == [("case_parties", "SGX 1234 A", "VEHICLE")]
    assert plate["searches"]["count"] == 1 and plate["nothing_in"] == ["Work orders given to somebody by name"]
    # What is typed is words, not a pattern.
    assert wild["held"] == [] and wild["totals"] == {"matches": 0, "kinds_of_record": 0} and wild["searches"]["count"] == 0
    assert subjects.like("50%_a\\b") == "%50\\%\\_a\\\\b%" and subjects.plate(" sgx-1234 a ") == "SGX1234A"

    # The words asked about are on the record, with who asked.
    reports = [r for r in await _audit(w, "subject.report") if r["detail"]["kind"] == "TEXT"]
    assert [(r["user_id"], r["detail"]["words"], r["detail"]["found"], r["resource_id"]) for r in reports] == [
        (w["users"][ADMIN], "tan wei ming", 2, None), (w["users"][MANAGER], "sgx1234a", 1, None), (w["users"][ADMIN], "%_%", 0, None)]
    assert [r["detail"]["kind"] for r in await _audit(w, "subject.report")][0] == "VISITOR"
