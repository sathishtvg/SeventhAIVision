"""Periods an organisation may set for four kinds of record (services/record_retention.py).

A. A period is whole days, thirty or more.
B. Nothing is removed until a period is set; then only what is over and older, of that organisation's.
C. Setting a period is a person's, and is asked for twice when it would remove something.
D. The scheduler runs it on its superuser session, and the application's role still cannot delete.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.core.config_keys import SETTING_VALIDATORS
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import data_governance as api
from app.services import record_retention as retention
from tests.test_drone_api import (
    ADMIN, ADMIN_DATABASE_URL, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world,
)
from tests.test_investigation_search import _audit
from tests.test_visitor_authorizations import _approved, _period, _visit

BASE = "/api/v1/data-governance"
PERIODS = f"{BASE}/retention/periods"
OLD = timedelta(days=400)


async def _as_the_scheduler(now: datetime | None = None) -> dict:
    """The job, on a superuser session of its own - as scheduler_main gives it one."""
    engine = create_async_engine(ADMIN_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as db:
            assert (await db.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
            return await retention.run(db, now)
    finally:
        await engine.dispose()


async def _case(w: dict, number: str, *, closed_ago: timedelta | None, opened_ago: timedelta = OLD) -> uuid.UUID:
    """A case with a note and a name on it; closed that long ago, or still open."""
    cid, now = uuid.uuid4(), datetime.now(timezone.utc)
    await _run([
        ("INSERT INTO case_files (id, tenant_id, site_id, case_number, title, summary, lead_user_id, opened_by_user_id, opened_at) "
         "VALUES (:i,:t,:s,:n,'Forced gate','The east gate was forced.',:u,:u,:at)",
         {"i": cid, "t": w["tenant"], "s": w["site_a"], "n": number, "u": w["users"][ADMIN], "at": now - opened_ago}),
        ("INSERT INTO case_entries (tenant_id, case_id, kind, body, actor_user_id) VALUES (:t,:c,'NOTE','Spoke to the gatehouse.',:u)",
         {"t": w["tenant"], "c": cid, "u": w["users"][ADMIN]}),
        ("INSERT INTO case_parties (tenant_id, case_id, kind, label, connection, added_by_user_id) "
         "VALUES (:t,:c,'PERSON','Tan Wei Ming','WITNESS',:u)", {"t": w["tenant"], "c": cid, "u": w["users"][ADMIN]}),
    ])
    if closed_ago is not None:
        await _sql("UPDATE case_files SET status = 'CLOSED', outcome = 'Gate repaired.', close_requested_by_user_id = :a, "
                   "close_requested_at = :at, closed_by_user_id = :m, closed_at = :at WHERE id = :i",
                   {"i": cid, "a": w["users"][ADMIN], "m": w["users"][MANAGER], "at": now - closed_ago})
    return cid


async def _investigation(w: dict, number: str, *, closed_ago: timedelta | None) -> uuid.UUID:
    fid, now = uuid.uuid4(), datetime.now(timezone.utc)
    await _run([
        ("INSERT INTO investigations (id, tenant_id, site_id, investigation_number, title, reason, opened_at) "
         "VALUES (:i,:t,:s,:n,'Who forced the gate','Reported by the client.',:at)",
         {"i": fid, "t": w["tenant"], "s": w["site_a"], "n": number, "at": now - OLD - timedelta(days=5)}),
        ("INSERT INTO investigation_items (tenant_id, investigation_id, kind, occurred_at, site_id, note) "
         "VALUES (:t,:f,'NOTE',:at,:s,'Asked the gatehouse.')",
         {"t": w["tenant"], "f": fid, "at": now - OLD, "s": w["site_a"]}),
    ])
    if closed_ago is not None:
        await _sql("UPDATE investigations SET status = 'CLOSED', closed_at = :at, closing_note = 'Nothing more to find.' WHERE id = :i",
                   {"i": fid, "at": now - closed_ago})
    return fid


async def _answer(w: dict, *, ago: timedelta) -> None:
    await _sql("INSERT INTO workforce_advice_answers (tenant_id, kind, subject_user_id, advice_key, code, statement, answer, "
               "answered_by_user_id, answered_at) VALUES (:t,'TRAINING',:g,'k','MISSED_TOURS','Missed 3 of 5 tours.','ACCEPTED',:u,:at)",
               {"t": w["tenant"], "g": w["users"][GUARD], "u": w["users"][ADMIN], "at": datetime.now(timezone.utc) - ago})


async def _count(w: dict, table: str) -> int:
    return (await _sql(f"SELECT count(*) AS n FROM {table} WHERE tenant_id = :t", {"t": w["tenant"]}))[0]["n"]


async def _old_records(c, w: dict) -> dict:
    """One of each kind that is over and old, and beside each what must stay."""
    r = {
        "gone_case": await _case(w, "CASE-0001", closed_ago=OLD),
        "recent_case": await _case(w, "CASE-0002", closed_ago=timedelta(days=10)),
        "open_case": await _case(w, "CASE-0003", closed_ago=None),
        "gone_file": await _investigation(w, "INV-0001", closed_ago=OLD),
        "packaged_file": await _investigation(w, "INV-0002", closed_ago=OLD),
        "linked_file": await _investigation(w, "INV-0003", closed_ago=OLD),
        "open_file": await _investigation(w, "INV-0004", closed_ago=None),
    }
    await _run([
        # An evidence package was made from one; another is linked to the case that is still open.
        ("INSERT INTO evidence_packages (tenant_id, site_id, package_number, title, purpose, investigation_id) "
         "VALUES (:t,:s,'EP-0001','Evidence of the gate','For the insurer.',:f)",
         {"t": w["tenant"], "s": w["site_a"], "f": r["packaged_file"]}),
        ("INSERT INTO case_links (tenant_id, case_id, kind, ref_id, linked_by_user_id) VALUES (:t,:c,'INVESTIGATION',:f,:u)",
         {"t": w["tenant"], "c": r["open_case"], "f": r["linked_file"], "u": w["users"][ADMIN]}),
    ])
    old_visit, new_visit = await _visit(w, "Lim Mei Ling"), await _visit(w, "Ong Kai Wen")
    r["gone_visit"] = (await _approved(c, w, old_visit))["id"]
    r["recent_visit"] = (await _approved(c, w, new_visit))["id"]
    await _period(r["gone_visit"], 401 * 1440, 400 * 1440)
    await _answer(w, ago=OLD)
    await _answer(w, ago=timedelta(days=5))
    return r


# ─── A. What a period is ─────────────────────────────────────────────────────

def test_a_period_is_whole_days_thirty_or_more_and_four_kinds_may_have_one():
    assert [k.key for k in retention.KINDS] == ["CASES", "INVESTIGATIONS", "VISITOR_AUTHORIZATIONS", "WORKFORCE_ANSWERS"]
    assert retention.days_of(30) == 30 and retention.days_of(365) == 365 and retention.days_of(36_500) == 36_500
    for not_a_period in (29, 0, -1, 36_501, None, "365", 90.5, True):
        assert retention.days_of(not_a_period) is None, not_a_period
    for kind in retention.KINDS:
        validate = SETTING_VALIDATORS[kind.setting_key]
        validate(30)
        validate(36_500)
        for bad in (29, 36_501, 45.5, "90", True, None):
            with pytest.raises(ValueError):
                validate(bad)
        # Only something that is over has a period, and every statement is about a row that is over.
        assert kind.old_enough.startswith(("x.status = 'CLOSED' AND x.closed_at < :cutoff", "x.valid_until < :cutoff",
                                           "x.answered_at < :cutoff")), kind.key
    # Evidence, its custody and its holds have no period here at all.
    every = {t for k in retention.KINDS for t in (k.table, *k.parts)}
    assert not {t for t in every if t.startswith("evidence_")} and len(every) == 12
    assert retention.LEAST_DAYS == 30 and "kept" in retention.NOT_SET


# ─── B. What goes, and what stays ────────────────────────────────────────────

async def test_nothing_is_removed_until_a_period_is_set_and_then_only_what_is_over_and_older():
    w, other = await _world(), await _world()
    async with _client() as c:
        r = await _old_records(c, w)
        theirs = await _old_records(c, other)
        tables = ("case_files", "case_entries", "case_parties", "investigations", "investigation_items",
                  "visitor_authorizations", "workforce_advice_answers")
        before = {t: await _count(w, t) for t in tables}
        assert before == {"case_files": 3, "case_entries": 3, "case_parties": 3, "investigations": 4, "investigation_items": 4,
                          "visitor_authorizations": 2, "workforce_advice_answers": 2}

        # No period is set: the job reads that, and leaves everything.
        first = await _as_the_scheduler()
        assert first["removed"] == {} and {t: await _count(w, t) for t in tables} == before

        # A year, for each kind, for the first organisation only.
        for kind in retention.KINDS:
            asked = await c.put(f"{PERIODS}/{kind.key}", headers=w["h"][MANAGER], json={"days": 365})
            assert asked.status_code == 409 and asked.json()["detail"]["already_older"] == 1, (kind.key, asked.text)
            assert "will be removed for good" in asked.json()["detail"]["message"]
            done = await c.put(f"{PERIODS}/{kind.key}", headers=w["h"][MANAGER], json={"days": 365, "already_older": 1})
            assert done.status_code == 200 and done.json()["days"] == 365, done.text
        assert {t: await _count(w, t) for t in tables} == before, "setting a period removes nothing"

    result = await _as_the_scheduler()
    assert result["removed"] == {"CASES": 1, "INVESTIGATIONS": 1, "VISITOR_AUTHORIZATIONS": 1, "WORKFORCE_ANSWERS": 1}
    assert result["organisations"] == 1 and result["failed"] == 0
    # What was over and older went, with its parts. What is open, recent, packaged or linked to an open case stayed.
    assert {t: await _count(w, t) for t in tables} == {
        "case_files": 2, "case_entries": 2, "case_parties": 2, "investigations": 3, "investigation_items": 3,
        "visitor_authorizations": 1, "workforce_advice_answers": 1}
    cases = {row["id"] for row in await _sql("SELECT id FROM case_files WHERE tenant_id = :t", {"t": w["tenant"]})}
    files = {row["id"] for row in await _sql("SELECT id FROM investigations WHERE tenant_id = :t", {"t": w["tenant"]})}
    assert cases == {r["recent_case"], r["open_case"]} and files == {r["packaged_file"], r["linked_file"], r["open_file"]}
    visits = [str(row["id"]) for row in await _sql("SELECT id FROM visitor_authorizations WHERE tenant_id = :t", {"t": w["tenant"]})]
    assert visits == [r["recent_visit"]]
    # The visitor, the visit's own record and the evidence package are untouched.
    assert await _count(w, "visitors") == 2 and await _count(w, "evidence_packages") == 1
    # The other organisation set no period: all of its records are there.
    assert {t: await _count(other, t) for t in tables} == before and theirs["gone_case"] != r["gone_case"]

    # One line in the organisation's audit log: how many of each kind, and under what period. No name, no title.
    (entry,) = await _audit(w, "retention.purge")
    assert entry["user_id"] is None and entry["resource_type"] == "record_retention"
    assert entry["detail"] == {"removed": result["removed"], "days": {k.key: 365 for k in retention.KINDS}}
    assert await _audit(other, "retention.purge") == []
    # The next day there is nothing older to remove, and nothing more is written.
    again = await _as_the_scheduler()
    assert again["removed"] == {k.key: 0 for k in retention.KINDS} and len(await _audit(w, "retention.purge")) == 1
    # A year on, the case closed ten days ago has had its year. The open one never has.
    later = await _as_the_scheduler(datetime.now(timezone.utc) + timedelta(days=366))
    assert later["removed"]["CASES"] == 1
    assert {row["id"] for row in await _sql("SELECT id FROM case_files WHERE tenant_id = :t", {"t": w["tenant"]})} == {r["open_case"]}


# ─── C. Setting one ──────────────────────────────────────────────────────────

async def test_setting_a_period_is_a_persons_and_says_first_what_it_will_remove():
    w = await _world()
    await _case(w, "CASE-0001", closed_ago=OLD)
    url = f"{PERIODS}/CASES"
    async with _client() as c:
        shown = (await c.get(f"{BASE}/retention", headers=w["h"][MANAGER])).json()
        cases = next(o for o in shown["optional"] if o["key"] == "CASES")
        assert (cases["days"], cases["set_at"], cases["setting_key"]) == (None, None, "retention.closed_cases_days")
        assert [t["name"] for t in cases["tables"]] == ["case_files", "case_investigators", "case_tasks", "case_entries",
                                                         "case_links", "case_parties"]
        assert shown["may_set"] is True and shown["least_days"] == 30 and "kept until" in shown["optional_note"]
        # Who may: whoever may change the organisation's settings. Reading the statement is not enough.
        for role, status in ((SUPERVISOR, 403), (VIEWER, 403), (OPERATOR, 403), (GUARD, 403)):
            assert (await c.put(url, headers=w["h"][role], json={"days": 365})).status_code == status, role
        assert (await c.get(f"{BASE}/retention", headers=w["h"][SUPERVISOR])).json()["may_set"] is False
        # What is asked.
        for body in ({"days": 29}, {"days": 36_501}, {"days": 90.5}, {"days": "365"}, {}, {"days": 365, "site": "x"}):
            assert (await c.put(url, headers=w["h"][ADMIN], json=body)).status_code == 422, body
        assert (await c.put(f"{PERIODS}/EVIDENCE", headers=w["h"][ADMIN], json={"days": 365})).status_code == 404
        # A period that removes nothing yet is set at once.
        long = await c.put(url, headers=w["h"][ADMIN], json={"days": 3650})
        assert long.status_code == 200 and (long.json()["days"], long.json()["already_older"]) == (3650, 0)
        # One that would remove something says how much, and is set only when that number is said back.
        told = await c.put(url, headers=w["h"][ADMIN], json={"days": 365})
        assert told.status_code == 409 and told.json()["detail"] == {
            "message": "1 of these is already older than 365 days and will be removed for good when the scheduler next "
                       "runs. Confirm to set the period.", "already_older": 1, "days": 365}
        assert (await c.put(url, headers=w["h"][ADMIN], json={"days": 365, "already_older": 7})).status_code == 409
        done = await c.put(url, headers=w["h"][ADMIN], json={"days": 365, "already_older": 1})
        assert done.status_code == 200 and done.json()["days"] == 365 and done.json()["set_at"]
        after = (await c.get(f"{BASE}/retention", headers=w["h"][VIEWER])).json()
        assert next(o for o in after["optional"] if o["key"] == "CASES")["days"] == 365 and after["may_set"] is False
        # Taken away again: the kind is kept, as before, and the setting is gone.
        off = await c.put(url, headers=w["h"][MANAGER], json={"days": None})
        assert off.status_code == 200 and off.json()["days"] is None
        assert await _sql("SELECT 1 FROM tenant_settings WHERE tenant_id = :t AND setting_key = 'retention.closed_cases_days'",
                          {"t": w["tenant"]}) == []
        assert await _count(w, "case_files") == 1, "nothing was removed by setting or unsetting"

    # A person of the organisation, who sees every site.
    me = {"user_id": str(w["users"][ADMIN]), "tenant_id": str(w["tenant"]), "role_id": ADMIN}
    for token, words in ((TokenPayload(**me, via_api_key=True), "A retention period is set by a person who is signed in, not by an API key."),
                         (TokenPayload(**me, support_session_id=str(uuid.uuid4())),
                          "A retention period is set by the organisation's own staff, not from a support session.")):
        with pytest.raises(Exception) as refused:
            api._the_organisations_own(token, None, "A retention period", "set", api.PERIOD_EVERY_SITE)
        assert refused.value.status_code == 403 and refused.value.detail == words
    with pytest.raises(Exception) as held:
        api._the_organisations_own(TokenPayload(**me), [str(w["site_a"])], "A retention period", "set", api.PERIOD_EVERY_SITE)
    assert held.value.detail == api.PERIOD_EVERY_SITE
    app.dependency_overrides[get_token_payload] = lambda: TokenPayload(**me, via_api_key=True)
    try:
        async with _client() as c:
            got = await c.put(url, json={"days": 365})
            assert got.status_code == 403 and "not by an API key" in got.json()["detail"]
            assert (await c.get(f"{BASE}/retention")).json()["may_set"] is False
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    # Each change is on the record, with what it was and what it became.
    changes = [(e["user_id"], e["detail"]["kind"], e["detail"]["from_days"], e["detail"]["to_days"], e["detail"]["already_older"])
               for e in await _audit(w, "retention.period.set")]
    assert changes == [(w["users"][ADMIN], "CASES", None, 3650, 0), (w["users"][ADMIN], "CASES", 3650, 365, 1),
                       (w["users"][MANAGER], "CASES", 365, None, 0)]


# ─── D. Who removes ──────────────────────────────────────────────────────────

async def test_the_scheduler_runs_it_on_its_superuser_session_and_the_application_role_still_cannot_delete():
    from pathlib import Path

    scheduler = (Path(__file__).resolve().parents[1] / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    step = scheduler.split("# Records an organisation has set a period for", 1)[1].split("run_database_backup()", 1)[0]
    assert "async with admin_session() as db:" in step and "await record_retention.run(db)" in step
    assert "non-fatal, maintenance continues" in step
    w = await _world()
    cid = await _case(w, "CASE-0001", closed_ago=OLD)
    for statement in ("DELETE FROM case_files WHERE id = :c", "DELETE FROM case_entries WHERE case_id = :c"):
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement), {"c": cid})
            await db.rollback()
    # As the application's role, the job's own question - how many are older - sees only the organisation in scope.
    other = await _world()
    await _case(other, "CASE-0001", closed_ago=OLD)
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert await retention.waiting(db, retention.BY_KEY["CASES"], 365) == 1
        assert await retention.waiting(db, retention.BY_KEY["CASES"], 3650) == 0
        assert (await retention.periods(db))["CASES"] == {"days": None, "set_at": None}
        await db.rollback()
    # A setting that is not a period the job will act on removes nothing.
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
               "VALUES (:t, 'retention.closed_cases_days', CAST('7' AS jsonb), :u)", {"t": w["tenant"], "u": w["users"][ADMIN]})
    assert (await _as_the_scheduler())["removed"] == {} and await _count(w, "case_files") == 1
