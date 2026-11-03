"""Phase 2 of the known limits (3 November 2026): periods for the history of modules 20 to 26, and evidence packages
removed by a person (services/record_retention.py, routers/evidence_disposal.py, migration 0176).

A. Seven kinds of history: kept with no period; with one, only what is over and older goes; what is always kept
   stays; another organisation's records are untouched.
B. Evidence packages: due only when the period is over, nothing is held and what it was made for is over; the daily
   job never removes one; a person with the right removes one, with a reason and its number; a record and the chain
   of custody stay; the pictures are untouched.
C. As the application's own role: a sealed package still cannot be changed or deleted, a package that is not due
   cannot be removed by inserting a record, and another organisation sees and removes nothing.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import app  # noqa: F401 - imported first, so the first test does not absorb the import
from app.db.session import AsyncSessionLocal
from app.services import record_retention as retention
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql
from tests.test_drone_api import _world as _a_world
from tests.test_investigation_search import _audit
from tests.test_record_retention import _as_the_scheduler, _case, _count

PERIODS = "/api/v1/data-governance/retention/periods"
DISPOSALS = "/api/v1/evidence-disposals"
HISTORY = ("RESPONSES", "SHIFT_SUMMARIES", "INSTRUCTIONS", "DEVICE_HEALTH", "WORK_ORDERS", "ADVICE_ANSWERS",
           "BRIEFINGS")
OLD, RECENT = timedelta(days=400), timedelta(days=10)


_MADE: list[uuid.UUID] = []


async def _world() -> dict:
    """An organisation, remembered so that its periods are taken away after the test."""
    w = await _a_world()
    _MADE.append(w["tenant"])
    return w


@pytest.fixture(autouse=True)
async def _periods_taken_away():
    """The scheduler's pass reads every organisation: a period left set here would be applied in the next test
    that runs it (test_record_retention.py counts what it removes across all of them)."""
    yield
    if _MADE:
        await _sql("DELETE FROM tenant_settings WHERE tenant_id = ANY(:t) AND setting_key LIKE 'retention.%'",
                   {"t": list(_MADE)})
        _MADE.clear()


def _mine(result: dict) -> dict:
    """What the pass removed of the history kinds."""
    return {k: n for k, n in result["removed"].items() if k in HISTORY}


def _ago(d: timedelta) -> datetime:
    return datetime.now(timezone.utc) - d


async def _set(c, w: dict, kind: str, days: int = 365) -> dict:
    """Set a period as a person does: asked, told how many are older, and said back."""
    first = await c.put(f"{PERIODS}/{kind}", headers=w["h"][MANAGER], json={"days": days})
    if first.status_code == 409:
        told = first.json()["detail"]
        done = await c.put(f"{PERIODS}/{kind}", headers=w["h"][MANAGER],
                           json={"days": days, "already_older": told["already_older"]})
        assert done.status_code == 200, done.text
        return {"told": told, **done.json()}
    assert first.status_code == 200, first.text
    return {"told": None, **first.json()}


# ─── Builders: for each kind, what goes and beside it what stays ─────────────

async def _incident(w: dict, *, resolved: timedelta | None) -> uuid.UUID:
    """An incident (resolved that long ago, or open) with a guard sent, one step and one escalation."""
    i, r = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO incidents (id, tenant_id, title, status, resolved_at, created_at) VALUES (:i,:t,'Gate forced',:s,:r,:c)",
         {"i": i, "t": w["tenant"], "s": "resolved" if resolved else "open", "r": _ago(resolved) if resolved else None,
          "c": _ago(OLD + timedelta(days=1))}),
        ("INSERT INTO incident_responses (id, tenant_id, incident_id, site_id, guard_user_id, dispatched_at) "
         "VALUES (:r,:t,:i,:s,:g,:at)", {"r": r, "t": w["tenant"], "i": i, "s": w["site_a"], "g": w["users"][GUARD],
                                        "at": _ago(OLD + timedelta(days=1))}),
        ("INSERT INTO incident_response_steps (tenant_id, response_id, incident_id, step, actor_user_id) "
         "VALUES (:t,:r,:i,'SENT',:u)", {"t": w["tenant"], "r": r, "i": i, "u": w["users"][SUPERVISOR]}),
        ("INSERT INTO incident_escalations (tenant_id, incident_id, site_id, kind, clock, due_at) "
         "VALUES (:t,:i,:s,'SLA_BREACH','ARRIVAL',:at)", {"t": w["tenant"], "i": i, "s": w["site_a"],
                                                          "at": _ago(OLD + timedelta(days=1))}),
    ])
    return i


async def _history(w: dict) -> dict:
    """One record of each kind that is over and old, and beside each what must stay."""
    t, s, u = w["tenant"], w["site_a"], w["users"][ADMIN]
    r = {"gone_incident": await _incident(w, resolved=OLD), "open_incident": await _incident(w, resolved=None),
         "recent_incident": await _incident(w, resolved=RECENT),
         "packaged_incident": await _incident(w, resolved=OLD), "cased_incident": await _incident(w, resolved=OLD)}
    open_case = await _case(w, f"CASE-{uuid.uuid4().hex[:6]}", closed_ago=None)
    shifts = [uuid.uuid4() for _ in range(3)]
    devices = {k: uuid.uuid4() for k in ("a", "b", "c", "down")}
    stmts = [
        # Responses: one incident is in an evidence package, another is linked by a case that is open.
        ("INSERT INTO evidence_packages (tenant_id, site_id, package_number, title, purpose, incident_id) "
         "VALUES (:t,:s,:n,'The gate','For the insurer.',:i)",
         {"t": t, "s": s, "n": f"EP-{uuid.uuid4().hex[:6]}", "i": r["packaged_incident"]}),
        ("INSERT INTO case_links (tenant_id, case_id, kind, ref_id, linked_by_user_id) VALUES (:t,:c,'INCIDENT',:i,:u)",
         {"t": t, "c": open_case, "i": r["cased_incident"], "u": u}),
    ]
    for sid in shifts:
        stmts.append(("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end) "
                      "VALUES (:i,:t,:s,:g,:a,:b)", {"i": sid, "t": t, "s": s, "g": w["users"][GUARD],
                                                     "a": _ago(OLD), "b": _ago(OLD) + timedelta(hours=8)}))
    for sid, state, ended in ((shifts[0], "CONFIRMED", OLD), (shifts[1], "DRAFT", OLD), (shifts[2], "CONFIRMED", RECENT)):
        stmts.append((
            "INSERT INTO shift_handover_summaries (tenant_id, shift_id, site_id, guard_user_id, period_start, period_end, "
            "facts, drafted_text, final_text, state, confirmed_at) VALUES (:t,:sh,:s,:g,:a,:b,'{}'::jsonb,'Quiet.','Quiet.',:st,:c)",
            {"t": t, "sh": sid, "s": s, "g": w["users"][GUARD], "a": _ago(ended) - timedelta(hours=8), "b": _ago(ended),
             "st": state, "c": _ago(ended) if state == "CONFIRMED" else None}))
    # Instructions: one closed long ago, one in force, one that expired lately. Each was read.
    for key, closed, expires in (("gone_instruction", OLD, None), ("in_force", None, None), ("expired_lately", None, RECENT)):
        r[key] = uuid.uuid4()
        stmts += [
            ("INSERT INTO site_instructions (id, tenant_id, site_id, body, issued_by_user_id, issued_at, expires_at, closed_at, "
             "closed_by_user_id, close_note) VALUES (:i,:t,:s,'Check the east gate hourly.',:u,:at,:e,:c,:cu,:cn)",
             {"i": r[key], "t": t, "s": s, "u": u, "at": _ago(OLD + timedelta(days=30)),
              "e": _ago(expires) if expires else None, "c": _ago(closed) if closed else None,
              "cu": u if closed else None, "cn": "The gate is fixed." if closed else None}),
            ("INSERT INTO site_instruction_reads (tenant_id, instruction_id, user_id) VALUES (:t,:i,:g)",
             {"t": t, "i": r[key], "g": w["users"][GUARD]}),
        ]
    # Device health: a's old change goes, its latest stays; b's only change stays; c's old one goes.
    for device, state, ago in (("a", "DOWN", timedelta(days=500)), ("a", "OK", timedelta(days=450)),
                               ("b", "DOWN", timedelta(days=500)),
                               ("c", "DOWN", timedelta(days=500)), ("c", "OK", timedelta(days=5)),
                               ("down", "DOWN", timedelta(days=500))):
        stmts.append(("INSERT INTO device_health_changes (tenant_id, device_kind, device_id, site_id, state, observed_at) "
                      "VALUES (:t,'SENSOR',:d,:s,:st,:at)", {"t": t, "d": devices[device], "s": s, "st": state, "at": _ago(ago)}))
    # Work orders: done long ago goes; open, done lately, and a dismissed suggestion for a device still down stay.
    since = _ago(timedelta(days=500)).isoformat()
    for n, (state, origin, ago, key) in enumerate((
            ("DONE", "PERSON", OLD, None), ("OPEN", "PERSON", OLD, None), ("DONE", "PERSON", RECENT, None),
            ("DISMISSED", "HEALTH", OLD, f"health:SENSOR:{devices['down']}:{since}"))):
        at = _ago(ago)
        stmts.append((
            "INSERT INTO maintenance_work_orders (tenant_id, number, site_id, title, state, origin, origin_key, raised_at, "
            "started_at, completed_at, completion_note, closed_at, closed_reason, suggestion_reason) "
            "VALUES (:t,:n,:s,'Replace the gate motor',:st,:o,:k,:r,:sa,:ca,:cn,:cl,:cr,:why)",
            {"t": t, "n": f"WO-{n + 1:04d}", "s": s, "st": state, "o": origin, "k": key, "r": at - timedelta(days=3),
             "sa": at - timedelta(days=1) if state == "DONE" else None, "ca": at if state == "DONE" else None,
             "cn": "Motor replaced." if state == "DONE" else None, "cl": at if state == "DISMISSED" else None,
             "cr": "Not needed." if state == "DISMISSED" else None,
             "why": "The gate sensor has been down since June." if origin == "HEALTH" else None}))
    # Answers to risk advice.
    for ago in (OLD, RECENT):
        stmts.append((
            "INSERT INTO risk_advice_answers (tenant_id, site_id, advice_key, code, source, statement, confidence, "
            "period_weeks, period_end, answer, answered_by_user_id, answered_at) VALUES "
            "(:t,:s,'k','MORE_PATROLS','RULE','Patrol the east gate more.','MEDIUM',4,:pe,'ACCEPTED',:u,:at)",
            {"t": t, "s": s, "pe": _ago(ago), "u": u, "at": _ago(ago)}))
    # Briefings: published long ago and discarded long ago go; a draft of long ago and one published lately stay.
    for revision, (state, ago) in enumerate((("PUBLISHED", OLD), ("DISCARDED", OLD), ("DRAFT", OLD + timedelta(days=1)),
                                             ("PUBLISHED", RECENT)), start=1):
        day = _ago(ago).date()
        stmts.append((
            "INSERT INTO daily_briefings (tenant_id, site_id, briefing_date, revision, state, period_start, period_end, "
            "timezone, content, drafted_by_user_id, published_by_user_id, published_at, discarded_by_user_id, discarded_at) "
            "VALUES (:t,:s,:d,:rev,:st,:a,:b,'Asia/Singapore','{}'::jsonb,:u,:pu,:pa,:du,:da)",
            {"t": t, "s": s, "d": day, "rev": revision, "st": state, "a": _ago(ago) - timedelta(days=1), "b": _ago(ago), "u": u,
             "pu": u if state == "PUBLISHED" else None, "pa": _ago(ago) if state == "PUBLISHED" else None,
             "du": u if state == "DISCARDED" else None, "da": _ago(ago) if state == "DISCARDED" else None}))
    await _run(stmts)
    r["devices"] = devices
    return r


TABLES = ("incident_responses", "incident_response_steps", "incident_escalations", "shift_handover_summaries",
          "site_instructions", "site_instruction_reads", "device_health_changes", "maintenance_work_orders",
          "risk_advice_answers", "daily_briefings")


async def _counts(w: dict) -> dict:
    return {t: await _count(w, t) for t in TABLES}


# ─── A. History ──────────────────────────────────────────────────────────────

async def test_history_is_kept_until_a_period_is_set_and_then_only_what_is_over_and_older_goes():
    w, other = await _world(), await _world()
    r = await _history(w)
    await _history(other)
    before = await _counts(w)
    assert before == {"incident_responses": 5, "incident_response_steps": 5, "incident_escalations": 5,
                      "shift_handover_summaries": 3, "site_instructions": 3, "site_instruction_reads": 3,
                      "device_health_changes": 6, "maintenance_work_orders": 4, "risk_advice_answers": 2,
                      "daily_briefings": 4}
    assert not any(_mine(await _as_the_scheduler()).values()) and await _counts(w) == before, "no period, nothing goes"

    async with _client() as c:
        told = {k: (await _set(c, w, k))["told"]["already_older"] for k in HISTORY}
    # What a person is told before setting each: a guard's response and its escalation are counted together.
    assert told == {"RESPONSES": 2, "SHIFT_SUMMARIES": 1, "INSTRUCTIONS": 1, "DEVICE_HEALTH": 2, "WORK_ORDERS": 1,
                    "ADVICE_ANSWERS": 1, "BRIEFINGS": 2}
    assert await _counts(w) == before, "setting a period removes nothing"

    result = await _as_the_scheduler()
    assert _mine(result) == {"RESPONSES": 1, "SHIFT_SUMMARIES": 1, "INSTRUCTIONS": 1, "DEVICE_HEALTH": 2,
                             "WORK_ORDERS": 1, "ADVICE_ANSWERS": 1, "BRIEFINGS": 2}
    assert await _counts(w) == {"incident_responses": 4, "incident_response_steps": 4, "incident_escalations": 4,
                                "shift_handover_summaries": 2, "site_instructions": 2, "site_instruction_reads": 2,
                                "device_health_changes": 4, "maintenance_work_orders": 3, "risk_advice_answers": 1,
                                "daily_briefings": 2}
    # The incident itself stays; its response went. Those open, recent, packaged or on an open case kept theirs.
    kept = {row["incident_id"] for row in await _sql("SELECT incident_id FROM incident_responses WHERE tenant_id = :t",
                                                     {"t": w["tenant"]})}
    assert kept == {r["open_incident"], r["recent_incident"], r["packaged_incident"], r["cased_incident"]}
    assert await _count(w, "incidents") == 5
    # Each device keeps its latest change: it is the device's state now.
    latest = await _sql("SELECT device_id, state FROM device_health_changes WHERE tenant_id = :t", {"t": w["tenant"]})
    assert sorted((str(x["device_id"]), x["state"]) for x in latest) == sorted(
        (str(r["devices"][d]), s) for d, s in (("a", "OK"), ("b", "DOWN"), ("c", "OK"), ("down", "DOWN")))
    # A dismissed suggestion for a device still down stays, or it would be put forward again.
    orders = {(x["state"], x["origin"]) for x in await _sql(
        "SELECT state, origin FROM maintenance_work_orders WHERE tenant_id = :t", {"t": w["tenant"]})}
    assert orders == {("OPEN", "PERSON"), ("DONE", "PERSON"), ("DISMISSED", "HEALTH")}
    instructions = {x["id"] for x in await _sql("SELECT id FROM site_instructions WHERE tenant_id = :t", {"t": w["tenant"]})}
    assert instructions == {r["in_force"], r["expired_lately"]}
    states = sorted(x["state"] for x in await _sql("SELECT state FROM daily_briefings WHERE tenant_id = :t", {"t": w["tenant"]}))
    assert states == ["DRAFT", "PUBLISHED"]
    # The other organisation set no period: everything of theirs is there.
    assert await _counts(other) == before
    (entry,) = await _audit(w, "retention.purge")
    assert entry["detail"]["removed"] == _mine(result) and entry["detail"]["days"] == {k: 365 for k in HISTORY}


async def test_the_statement_lists_the_new_kinds_and_says_who_removes_a_package():
    w = await _world()
    async with _client() as c:
        shown = (await c.get("/api/v1/data-governance/retention", headers=w["h"][ADMIN])).json()
    by = {o["key"]: o for o in shown["optional"]}
    assert list(by) == [k.key for k in retention.KINDS]
    assert [t["name"] for t in by["RESPONSES"]["tables"]] == ["incident_responses", "incident_response_steps",
                                                              "incident_escalations"]
    assert by["EVIDENCE_PACKAGES"]["by_person"] is True and "never removes one" in by["EVIDENCE_PACKAGES"]["removed_by"]
    assert all(by[k]["by_person"] is False and by[k]["removed_by"] == retention.REMOVED_BY for k in HISTORY)
    assert "for a person to remove" in shown["optional_note"]
    kept = {k["key"] for k in shown["kept"]}
    assert kept == {"EVIDENCE_CUSTODY", "SITE_MAP", "ESCALATION_POLICIES", "OCCURRENCE_BOOK", "SOP", "ASSETS"}


# ─── B. Evidence packages ────────────────────────────────────────────────────

async def _package(w: dict, *, sealed: timedelta | None, updated: timedelta = OLD, investigation=None, incident=None,
                   hold: bool = False) -> dict:
    """A package with one frame in it, sealed that long ago (or a draft last changed that long ago), with a
    package-level custody entry and an item-level one. `hold` keeps a hold in force on its frame."""
    t = w["tenant"]
    p, frame, number = uuid.uuid4(), uuid.uuid4(), f"EVP-{uuid.uuid4().hex[:6].upper()}"
    await _run([
        ("INSERT INTO evidence (id, tenant_id, storage_path) VALUES (:e,:t,:path)",
         {"e": frame, "t": t, "path": f"/data/evidence/{frame}.jpg"}),
        ("INSERT INTO evidence_packages (id, tenant_id, site_id, package_number, title, purpose, investigation_id, "
         "incident_id, created_by_user_id, created_at, updated_at) VALUES (:p,:t,:s,:n,'The gate','For the insurer.',:v,:i,:u,:c,:at)",
         {"p": p, "t": t, "s": w["site_a"], "n": number, "v": investigation, "i": incident, "u": w["users"][ADMIN],
          "c": _ago(updated + timedelta(days=2)), "at": _ago(updated)}),
        ("INSERT INTO evidence_package_items (tenant_id, package_id, kind, ref_id, captured_at, site_id) "
         "VALUES (:t,:p,'SNAPSHOT',:e,:at,:s)", {"t": t, "p": p, "e": frame, "at": _ago(updated + timedelta(days=3)),
                                                "s": w["site_a"]}),
        ("INSERT INTO evidence_custody_events (tenant_id, package_id, kind, ref_id, step, actor_user_id, actor_role, occurred_at) "
         "VALUES (:t,:p,'SNAPSHOT',:e,'COLLECTED',:u,2,:at)", {"t": t, "p": p, "e": frame, "u": w["users"][ADMIN],
                                                              "at": _ago(updated + timedelta(days=1))}),
    ])
    if sealed is not None:
        await _run([
            ("UPDATE evidence_packages SET status = 'SEALED', sealed_by_user_id = :u, sealed_at = :at, "
             "manifest = '{\"items\": 1}'::jsonb, manifest_sha256 = :sha WHERE id = :p",
             {"u": w["users"][ADMIN], "at": _ago(sealed), "sha": "a" * 64, "p": p}),
            ("INSERT INTO evidence_custody_events (tenant_id, package_id, step, actor_user_id, actor_role, occurred_at) "
             "VALUES (:t,:p,'SEALED',:u,2,:at)", {"t": t, "p": p, "u": w["users"][ADMIN], "at": _ago(sealed)}),
            ("INSERT INTO evidence_holds (tenant_id, kind, ref_id, site_id, package_id, reason, placed_by_user_id, placed_at, "
             "released_by_user_id, released_at, release_reason) VALUES (:t,'SNAPSHOT',:e,:s,:p,'Sealed.',:u,:at,:ru,:ra,:rr)",
             {"t": t, "e": frame, "s": w["site_a"], "p": p, "u": w["users"][ADMIN], "at": _ago(sealed),
              "ru": None if hold else w["users"][MANAGER], "ra": None if hold else _ago(sealed) + timedelta(days=30),
              "rr": None if hold else "The claim is settled."}),
        ])
    return {"id": p, "number": number, "frame": frame}


async def _investigation(w: dict, *, closed: bool) -> uuid.UUID:
    i = uuid.uuid4()
    await _sql("INSERT INTO investigations (id, tenant_id, site_id, investigation_number, title, reason, opened_at, "
               "status, closed_at, closing_note) VALUES (:i,:t,:s,:n,'Who forced the gate','Reported.',:o,:st,:c,:cn)",
               {"i": i, "t": w["tenant"], "s": w["site_a"], "n": f"INV-{uuid.uuid4().hex[:6]}", "o": _ago(OLD * 2),
                "st": "CLOSED" if closed else "OPEN", "c": _ago(OLD) if closed else None,
                "cn": "Nothing more." if closed else None})
    return i


async def _packages(w: dict) -> dict:
    open_case = await _case(w, f"CASE-{uuid.uuid4().hex[:6]}", closed_ago=None)
    p = {
        "due": await _package(w, sealed=OLD, investigation=await _investigation(w, closed=True)),
        "draft": await _package(w, sealed=None, updated=OLD),
        "held": await _package(w, sealed=OLD, hold=True),
        "open_investigation": await _package(w, sealed=OLD, investigation=await _investigation(w, closed=False)),
        "open_incident": await _package(w, sealed=OLD, incident=await _incident(w, resolved=None)),
        "recent": await _package(w, sealed=RECENT),
        "cased": await _package(w, sealed=OLD),
    }
    await _sql("INSERT INTO case_links (tenant_id, case_id, kind, ref_id, linked_by_user_id) "
               "VALUES (:t,:c,'EVIDENCE_PACKAGE',:p,:u)",
               {"t": w["tenant"], "c": open_case, "p": p["cased"]["id"], "u": w["users"][ADMIN]})
    return p


async def test_a_package_is_due_only_when_its_period_is_over_nothing_is_held_and_its_matter_is_over():
    w = await _world()
    p = await _packages(w)
    async with _client() as c:
        none = await c.get(f"{DISPOSALS}/due", headers=w["h"][MANAGER])
        assert none.status_code == 200 and none.json()["period_days"] is None and none.json()["packages"] == []
        refused = await c.post(DISPOSALS, headers=w["h"][MANAGER], json={
            "package_id": str(p["due"]["id"]), "package_number": p["due"]["number"], "reason": "The claim is settled."})
        assert refused.status_code == 409 and "No period is set" in refused.json()["detail"]

        set_ = await _set(c, w, "EVIDENCE_PACKAGES")
        assert set_["told"]["already_older"] == 2 and "listed as due for removal" in set_["told"]["message"]
        assert set_["removed_by"].startswith("A person who may remove evidence packages")
        due = (await c.get(f"{DISPOSALS}/due", headers=w["h"][ADMIN])).json()
    assert due["period_days"] == 365
    assert {x["package_number"] for x in due["packages"]} == {p["due"]["number"], p["draft"]["number"]}
    # The daily job passes over packages, period or none.
    result = await _as_the_scheduler()
    assert "EVIDENCE_PACKAGES" not in result["removed"] and await _count(w, "evidence_packages") == 7


async def test_a_person_removes_a_due_package_and_its_record_and_chain_of_custody_stay():
    w = await _world()
    p = await _packages(w)
    due, held = p["due"], p["held"]
    async with _client() as c:
        await _set(c, w, "EVIDENCE_PACKAGES")
        # Who may: Admin and Manager.
        for role in (SUPERVISOR, OPERATOR, VIEWER, GUARD):
            assert (await c.get(f"{DISPOSALS}/due", headers=w["h"][role])).status_code == 403, role
            assert (await c.post(DISPOSALS, headers=w["h"][role], json={
                "package_id": str(due["id"]), "package_number": due["number"], "reason": "Settled."})).status_code == 403
        body = {"package_id": str(due["id"]), "package_number": due["number"], "reason": "The claim was settled in 2025."}
        assert (await c.post(DISPOSALS, headers=w["h"][ADMIN], json={**body, "reason": "ok"})).status_code == 422
        wrong = await c.post(DISPOSALS, headers=w["h"][ADMIN], json={**body, "package_number": "EVP-0000"})
        assert wrong.status_code == 409 and due["number"] in wrong.json()["detail"]
        not_due = await c.post(DISPOSALS, headers=w["h"][ADMIN], json={**body, "package_id": str(held["id"]),
                                                                       "package_number": held["number"]})
        assert not_due.status_code == 409 and "not due for removal" in not_due.json()["detail"]

        done = await c.post(DISPOSALS, headers=w["h"][ADMIN], json=body)
        assert done.status_code == 201, done.text
        record = done.json()
        assert (record["package_number"], record["items"], record["was_sealed"], record["period_days"]) == (
            due["number"], 1, True, 365)
        assert record["manifest_sha256"] == "a" * 64
        again = await c.post(DISPOSALS, headers=w["h"][ADMIN], json=body)
        assert again.status_code == 404

        listed = await c.get(DISPOSALS, headers=w["h"][VIEWER])
        assert listed.status_code == 200 and [x["package_number"] for x in listed.json()["items"]] == [due["number"]]
        assert listed.json()["items"][0]["removed_by_name"] == "Role 2 User"
        chain = await c.get(f"{DISPOSALS}/{record['id']}/custody", headers=w["h"][MANAGER])
        assert chain.status_code == 200
    # The package and its items are gone; the picture is not.
    assert await _sql("SELECT 1 FROM evidence_packages WHERE id = :p", {"p": due["id"]}) == []
    assert await _sql("SELECT 1 FROM evidence_package_items WHERE package_id = :p", {"p": due["id"]}) == []
    assert len(await _sql("SELECT 1 FROM evidence WHERE id = :e", {"e": due["frame"]})) == 1
    # Its chain is kept, tied to the record, and ends with the removal: who, why, under what period.
    steps = [x["step"] for x in chain.json()["chain"]]
    assert steps == ["COLLECTED", "SEALED", "DISPOSED"]
    last = chain.json()["chain"][-1]
    assert last["reason"] == "The claim was settled in 2025." and last["actor_role"] == ADMIN
    assert last["detail"]["period_days"] == 365 and last["detail"]["package_number"] == due["number"]
    linked = await _sql("SELECT package_id, disposal_id FROM evidence_custody_events WHERE disposal_id = :d",
                        {"d": record["id"]})
    assert len(linked) == 3 and all(x["package_id"] is None for x in linked)
    (entry,) = await _audit(w, "evidence.package.dispose")
    assert entry["user_id"] == w["users"][ADMIN] and entry["detail"]["number"] == due["number"]
    assert await _count(w, "evidence_packages") == 6


# ─── C. As the application's own role ────────────────────────────────────────

async def _as_app(w: dict, statement: str, params: dict) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        try:
            await db.execute(text(statement), params)
        finally:
            await db.rollback()


async def test_as_the_application_a_sealed_package_is_still_sealed_and_only_a_due_one_is_removed():
    w, other = await _world(), await _world()
    p = await _packages(w)
    async with _client() as c:
        await _set(c, w, "EVIDENCE_PACKAGES")
        await _set(c, other, "EVIDENCE_PACKAGES")
        # Another organisation sees none of these, and cannot remove one.
        assert (await c.get(f"{DISPOSALS}/due", headers=other["h"][ADMIN])).json()["packages"] == []
        theirs = await c.post(DISPOSALS, headers=other["h"][ADMIN], json={
            "package_id": str(p["due"]["id"]), "package_number": p["due"]["number"], "reason": "Not ours to remove."})
        assert theirs.status_code == 404
    who = {"t": w["tenant"], "u": w["users"][ADMIN]}
    for statement, params, match in (
            ("DELETE FROM evidence_packages WHERE id = :p", {"p": p["due"]["id"]}, "permission denied"),
            ("UPDATE evidence_packages SET title = 'Changed' WHERE id = :p", {"p": p["due"]["id"]}, "permission denied"),
            ("UPDATE evidence_packages SET status = 'DRAFT' WHERE id = :p", {"p": p["due"]["id"]}, "is sealed"),
            ("DELETE FROM evidence_custody_events WHERE package_id = :p", {"p": p["due"]["id"]}, "permission denied"),
            # Asking the database straight for one that is not due is refused by the database itself.
            ("INSERT INTO evidence_package_disposals (tenant_id, package_id, reason, removed_by_user_id) "
             "VALUES (:t, :p, 'Straight to the database.', :u)", {**who, "p": p["held"]["id"]}, "not due for removal"),
            ("UPDATE evidence_package_disposals SET reason = 'x'", {}, "permission denied"),
            ("DELETE FROM evidence_package_disposals", {}, "permission denied"),
    ):
        with pytest.raises(DBAPIError, match=match):
            await _as_app(w, statement, params)
    # Another organisation's scope cannot ask for this one's package.
    with pytest.raises(DBAPIError, match="no such evidence package"):
        await _as_app(other, "INSERT INTO evidence_package_disposals (tenant_id, package_id, reason, removed_by_user_id) "
                             "VALUES (:t, :p, 'Not ours.', :u)", {"t": other["tenant"], "p": p["due"]["id"],
                                                                 "u": other["users"][ADMIN]})
    assert await _count(w, "evidence_packages") == 7 and await _count(w, "evidence_package_disposals") == 0
    detail = await _sql("SELECT detail FROM evidence_custody_events WHERE tenant_id = :t AND step = 'DISPOSED'",
                        {"t": w["tenant"]})
    assert detail == [] and json.dumps({}) == "{}"
