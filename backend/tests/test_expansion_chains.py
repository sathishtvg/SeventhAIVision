"""The eleven chains of the brief, end to end (phase 14).

Each phase was tested by itself. These follow one thing through several of
them, by the routes a person would use, in one organisation:

    CCTV → AI → incident                      ┐
    incident → guard dispatch                 │
    incident → investigation                  │  one matter, start to finish
    investigation → evidence                  │  (test A)
    evidence → case                           │
    case → report                             ┘
    drone → AI → incident                        test B
    virtual patrol → AI → incident               test B
    visitor → access → CCTV → alert              test C
    device health → maintenance                  test D
    risk → AI recommendation → human decision    test E

What each asserts, besides that the chain holds: that every step which mattered
was a person's, that nothing along the way was done by the platform alone, and
that the readings added later - the board, the retention statement, a subject
report - see what the chain left behind.

Where a chain is joined only when an organisation asks, the test holds both:
a visitor's door events are set against their authorisation, and are handed to
the intelligence layer only once that is switched on - and then name nobody.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.main import app  # noqa: F401 — imported first, so no test absorbs the cost
from app.db.session import AsyncSessionLocal
from app.services import intel_runner
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, _client, _run, _sql
from tests.test_evidence_packages import roots  # noqa: F401 — a fixture: the test's own directories for files
from tests.test_intel_decisions import _ago, _decide, _pass, _rows
from tests.test_intel_decisions import _ready as _a_situation
from tests.test_intel_events import _alert, _events
from tests.test_intel_events import _world as _intel_world
from tests.test_investigation_search import _audit
from tests.test_maintenance import _ask_for_suggestions, _orders
from tests.test_security_advice import _pattern
from tests.test_security_assets import _camera as _device_camera
from tests.test_security_assets import _hit, _look
from tests.test_visitor_movement_events import _switch as _hand_over
from tests.test_visitor_authorizations import _approved, _card, _check_in, _period, _site_with_doors, _swipe, _visit

INTEL = "/api/v1/security-intelligence"
RESPONSES = "/api/v1/incident-responses"
INVESTIGATIONS = "/api/v1/investigations"
PACKAGES = "/api/v1/evidence-packages"
CASES = "/api/v1/cases"
VISITS = "/api/v1/visitor-authorizations"
MAINTENANCE = "/api/v1/maintenance"
ADVICE = "/api/v1/security-advice"
GOVERNANCE = "/api/v1/data-governance"

#: The brief's chains, each with the test that follows it.
CHAINS = {
    "CCTV → AI → Incident": "test_what_a_camera_saw_is_followed_from_the_alert_to_the_report_of_its_case",
    "Drone → AI → Incident": "test_a_drone_sighting_and_a_patrol_exception_each_become_an_incident_only_when_a_person_decides",
    "Virtual Patrol → AI → Incident": "test_a_drone_sighting_and_a_patrol_exception_each_become_an_incident_only_when_a_person_decides",
    "Incident → Guard Dispatch": "test_what_a_camera_saw_is_followed_from_the_alert_to_the_report_of_its_case",
    "Incident → Investigation": "test_what_a_camera_saw_is_followed_from_the_alert_to_the_report_of_its_case",
    "Investigation → Evidence": "test_what_a_camera_saw_is_followed_from_the_alert_to_the_report_of_its_case",
    "Evidence → Case": "test_what_a_camera_saw_is_followed_from_the_alert_to_the_report_of_its_case",
    "Case → Report": "test_what_a_camera_saw_is_followed_from_the_alert_to_the_report_of_its_case",
    "Visitor → Access → CCTV → Alert": "test_a_visitors_badge_is_set_against_their_authorisation_and_a_forced_door_joins_what_the_camera_saw",
    "Device Health → Maintenance": "test_a_device_that_is_down_becomes_work_only_when_a_person_accepts_it",
    "Risk → AI Recommendation → Human Decision": "test_a_pattern_becomes_advice_a_person_answers_and_a_briefing_carries",
}


def _iso(moment: datetime) -> str:
    return moment.isoformat()


async def _ok(response, *codes: int) -> dict:
    assert response.status_code in (codes or (200, 201)), response.text
    return response.json()


async def _people_only(w: dict, actions: list[str]) -> None:
    """Every one of these steps is in the audit log, and each was a signed-in person's."""
    for action in actions:
        entries = await _audit(w, action)
        assert entries, f"{action} is not in the audit log"
        for entry in entries:
            assert entry["user_id"] is not None and entry["detail"].get("source", "user") == "user", action


def test_each_of_the_eleven_chains_has_the_test_that_follows_it():
    assert len(CHAINS) == 11
    for chain, name in CHAINS.items():
        assert callable(globals().get(name)), chain


# ─── A. One matter, from what a camera saw to the report of its case ─────────

async def test_what_a_camera_saw_is_followed_from_the_alert_to_the_report_of_its_case(roots):  # noqa: F811
    evidence_root, _ = roots
    # CCTV → AI: a camera's alert has been read, placed, assessed, and something has been suggested.
    w, situation = await _a_situation(severity="critical", on_shift=True)
    guard, t = w["users"][GUARD], w["tenant"]
    suggested = [r["action"] for r in await _rows(w, "security_recommendations", "rank") if r["available"]]
    assert "DISPATCH_GUARD" in suggested
    assert await _rows(w, "incidents") == [], "a suggestion opens nothing"
    assert await _rows(w, "security_decisions", "decided_at") == []

    async with _client() as c:
        # AI → incident: a person decides, and only then is there an incident and somebody sent.
        decided = await _ok(await _decide(c, w, situation, OPERATOR, "DISPATCH_GUARD", guard_user_id=str(guard),
                                          note="North gate, approach from the car park."), 201)
        (incident,) = await _rows(w, "incidents")
        iid = str(incident["id"])
        assert decided["basis"] == "FOLLOWED" and incident["is_auto_created"] is False
        assert incident["camera_id"] == w["cam_a"] and incident["dispatched_guard_id"] == guard
        (placed,) = await _rows(w, "security_situations")
        assert placed["incident_id"] == incident["id"] and placed["incident_confirmed_at"] is not None

        # Incident → guard dispatch: the guard reads that they were sent, and answers step by step.
        (sent,) = (await _ok(await c.get(f"{RESPONSES}/mine", headers=w["h"][GUARD])))["items"]
        assert sent["response"]["state"] == "SENT" and sent["may"]["accept"]
        for step, body in (("accept", {"note": "On it"}), ("en-route", {}),
                           ("arrived", {"latitude": 1.3001, "longitude": 103.8001}),
                           ("report", {"note": "Padlock cut. Nobody on site."})):
            await _ok(await c.post(f"{RESPONSES}/{iid}/{step}", headers=w["h"][GUARD], json=body), 200)
        desk = await _ok(await c.get(f"{RESPONSES}/{iid}", headers=w["h"][OPERATOR]))
        assert [s["step"] for s in desk["steps"]] == ["SENT", "ACCEPTED", "EN_ROUTE", "ARRIVED", "REPORTED"]
        assert desk["response"]["state"] == "ARRIVED" and desk["steps"][-1]["note"] == "Padlock cut. Nobody on site."
        assert (await _rows(w, "incidents"))[0]["status"] == "on_scene"

        # Incident → investigation: opened from the incident, with the incident and the alert put into it.
        alert_at = (await _sql("SELECT created_at FROM alerts WHERE id = :a", {"a": w["alert"]}))[0]["created_at"]
        investigation = await _ok(await c.post(INVESTIGATIONS, headers=w["h"][ADMIN], json={
            "title": "Who forced the north gate", "reason": "The guard found the padlock cut.",
            "site_id": str(w["site_a"]), "incident_id": iid}), 201)
        added = await _ok(await c.post(f"{INVESTIGATIONS}/{investigation['id']}/items", headers=w["h"][ADMIN], json={
            "records": [{"kind": "INCIDENT", "id": iid, "occurred_at": _iso(incident["created_at"])},
                        {"kind": "ALERT", "id": str(w["alert"]), "occurred_at": _iso(alert_at)}],
            "note": "What the camera saw, and what was opened for it."}), 201)
        # The incident it was opened from was already in it; the alert is what was added.
        assert [x["kind"] for x in added["added"]] == ["ALERT"]
        assert {r["kind"] for r in await _rows(w, "investigation_items", "added_at")} == {"INCIDENT", "ALERT"}

        # Investigation → evidence: the clip kept with the incident is offered, packaged and sealed.
        clip, content = uuid.uuid4(), b"clip-of-the-north-gate-" * 500
        path = f"{t}/clip-{clip}.mp4"
        (evidence_root / path).parent.mkdir(parents=True, exist_ok=True)
        (evidence_root / path).write_bytes(content)
        await _sql("INSERT INTO evidence (id, tenant_id, incident_id, media_type, storage_path, checksum_sha256, captured_at, "
                   "site_id, capture_kind) VALUES (:i,:t,:inc,'video',:p,:sum,:at,:s,'clip')",
                   {"i": clip, "t": t, "inc": incident["id"], "p": path, "sum": hashlib.sha256(content).hexdigest(),
                    "at": incident["created_at"], "s": w["site_a"]})
        package = await _ok(await c.post(PACKAGES, headers=w["h"][ADMIN], json={
            "title": "The north gate", "purpose": "For the client's insurer.", "investigation_id": investigation["id"]}), 201)
        offered = (await _ok(await c.get(f"{PACKAGES}/{package['id']}/candidates", headers=w["h"][ADMIN])))["items"]
        assert [o["id"] for o in offered] == [str(clip)], "what belongs to the investigation's records, and nothing else"
        await _ok(await c.post(f"{PACKAGES}/{package['id']}/items", headers=w["h"][ADMIN], json={
            "items": [{"kind": o["kind"], "id": o["id"], "captured_at": o["captured_at"]} for o in offered]}), 201)
        await _ok(await c.post(f"{PACKAGES}/{package['id']}/seal", headers=w["h"][ADMIN], json={}), 200)
        (sealed,) = await _rows(w, "evidence_packages")
        assert sealed["status"] == "SEALED" and sealed["investigation_id"] == uuid.UUID(investigation["id"])
        (hold,) = await _rows(w, "evidence_holds", "placed_at")
        assert hold["ref_id"] == clip and hold["released_at"] is None and hold["package_id"] == sealed["id"]

        # Evidence → case: opened from the package; the incident and the investigation are linked beside it.
        case = await _ok(await c.post(CASES, headers=w["h"][SUPERVISOR], json={
            "title": "North gate forced", "summary": "The padlock of the north gate was cut on the night shift.",
            "site_id": str(w["site_a"]), "category": "TRESPASS", "from_kind": "EVIDENCE_PACKAGE", "from_id": package["id"]}), 201)
        url = f"{CASES}/{case['id']}"
        for kind, ref in (("INCIDENT", iid), ("INVESTIGATION", investigation["id"])):
            await _ok(await c.post(f"{url}/links", headers=w["h"][SUPERVISOR], json={"kind": kind, "ref_id": ref}), 201)
        await _ok(await c.post(f"{url}/parties", headers=w["h"][SUPERVISOR], json={
            "kind": "PERSON", "label": "The night supervisor", "connection": "REPORTED_IT"}), 201)
        task = await _ok(await c.post(f"{url}/tasks", headers=w["h"][SUPERVISOR], json={
            "title": "Ask the client for the gate's key register", "assigned_to_user_id": str(w["users"][OPERATOR])}), 201)
        task_id = next(x["id"] for x in task["tasks"] if x["title"].startswith("Ask the client"))
        # Somebody given a task finishes that task, and does nothing else to the case.
        assert (await c.post(f"{url}/notes", headers=w["h"][OPERATOR], json={"body": "Not mine to write"})).status_code == 403
        await _ok(await c.post(f"{url}/tasks/{task_id}/done", headers=w["h"][OPERATOR], json={"note": "Register received."}), 200)

        # Case → report: closed by two, and read whole.
        asked = await c.post(f"{url}/request-close", headers=w["h"][SUPERVISOR], json={
            "outcome": "Padlock cut by persons unknown. Gate repaired; lock replaced."})
        assert asked.status_code == 200, asked.text
        assert (await c.post(f"{url}/approve-close", headers=w["h"][SUPERVISOR])).status_code == 409, "not by whoever asked"
        await _ok(await c.post(f"{url}/approve-close", headers=w["h"][MANAGER]), 200)
        report = await _ok(await c.get(f"{url}/report", headers=w["h"][ADMIN]))
        pdf = await c.get(f"{url}/report.pdf", headers=w["h"][ADMIN])
        told = report["case"]
        assert told["status"] == "CLOSED" and told["case_number"] == "CASE-0001"
        assert told["outcome"] == "Padlock cut by persons unknown. Gate repaired; lock replaced."
        assert {(x["kind"], x["state"], x["ref_id"]) for x in told["links"]} == {
            ("EVIDENCE_PACKAGE", "SHOWN", package["id"]), ("INCIDENT", "SHOWN", iid),
            ("INVESTIGATION", "SHOWN", investigation["id"])}
        kinds = [e["kind"] for e in told["entries"]]
        assert kinds[0] == "OPENED" and kinds[-2:] == ["CLOSE_REQUESTED", "CLOSE_APPROVED"]
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and pdf.headers["content-type"] == "application/pdf"

        # The readings added afterwards see what the chain left behind.
        board = {s["key"]: s["figures"] for s in (await _ok(await c.get(
            "/api/v1/operations-board", headers=w["h"][ADMIN], params={"days": 1})))["sections"]}
        assert board["INCIDENTS"]["opened"] == 1 and board["RESPONSE"]["sent"] == 1 and board["RESPONSE"]["arrived"] == 1
        statement = await _ok(await c.get(f"{GOVERNANCE}/retention", headers=w["h"][ADMIN]))
        assert statement["holds"]["in_force"] == 1
        assert {p["key"]: p["held_now"] for p in statement["periods"]}["EVIDENCE"] == 1
        about = await _ok(await c.get(f"{GOVERNANCE}/subjects/staff/{guard}", headers=w["h"][ADMIN]))
        lines = {(h["table"], x["column"]): (x["part"], x["count"]) for h in about["held"] for x in h["lines"]}
        assert lines[("incident_responses", "guard_user_id")] == ("ABOUT", 1)
        assert lines[("incident_response_steps", "actor_user_id")] == ("BY", 4)

    # One thread of ids runs through it: situation → incident → investigation → package → case.
    (file,) = await _rows(w, "investigations", "opened_at")
    assert file["incident_id"] == incident["id"]
    linked = {r["kind"]: r["ref_id"] for r in await _rows(w, "case_links", "linked_at")}
    assert linked == {"EVIDENCE_PACKAGE": sealed["id"], "INCIDENT": incident["id"], "INVESTIGATION": file["id"]}
    # Closing the case settled the case, and nothing it refers to.
    assert (await _rows(w, "incidents"))[0]["status"] == "on_scene" and (await _rows(w, "investigations", "opened_at"))[0]["status"] == "OPEN"
    assert (await _rows(w, "evidence_holds", "placed_at"))[0]["released_at"] is None
    # Every step that mattered is on the record, and was a person's.
    await _people_only(w, ["response.accept", "investigation.open", "investigation.item.add", "evidence.package.create",
                           "evidence.package.item.add", "evidence.package.seal", "case.open", "case.link.add",
                           "case.task.done", "case.close.request", "case.close.approve", "case.report", "subject.report"])
    decision = (await _rows(w, "security_decisions", "decided_at"))[0]
    assert decision["actor_user_id"] == w["users"][OPERATOR] and decision["action"] == "DISPATCH_GUARD"


# ─── B. A drone, and a virtual patrol ────────────────────────────────────────

async def test_a_drone_sighting_and_a_patrol_exception_each_become_an_incident_only_when_a_person_decides():
    w = await _intel_world()
    sighting, session, scam, question, answer = (uuid.uuid4() for _ in range(5))
    await _run([
        # Drone → AI: a sighting the drone module has verified.
        ("INSERT INTO drone_events (id, tenant_id, site_id, module_type, detected_at, risk_level, risk_score, "
         "    ai_confidence, verification_state, verified_at, created_at) "
         "VALUES (:i,:t,:s,'intrusion',:at,'HIGH',82,0.94,'VERIFIED',:v,:at)",
         {"i": sighting, "t": w["tenant"], "s": w["site_b"], "at": _ago(seconds=40), "v": _ago(seconds=10)}),
        # Virtual patrol → AI: an officer answered a question of the round with an exception.
        ("INSERT INTO virtual_patrol_sessions (id, tenant_id, site_id, patrol_number, schedule_name, scheduled_for) "
         "VALUES (:i,:t,:s,'VP-0042','Night round',:at)",
         {"i": session, "t": w["tenant"], "s": w["site_a"], "at": _ago(minutes=5)}),
        ("INSERT INTO virtual_patrol_session_cameras (id, tenant_id, session_id, camera_id, sequence_no, camera_name) "
         "VALUES (:i,:t,:s,:c,1,'Gate 1')", {"i": scam, "t": w["tenant"], "s": session, "c": w["cam_a"]}),
        ("INSERT INTO virtual_patrol_session_questions (id, tenant_id, session_camera_id, question_text, question_type, "
         "    is_required, sequence_no, failure_action) VALUES (:i,:t,:c,'Is the gate closed?','YES_NO',TRUE,1,'CREATE_INCIDENT')",
         {"i": question, "t": w["tenant"], "c": scam}),
        ("INSERT INTO virtual_patrol_session_answers (id, tenant_id, session_question_id, answered_by_user_id, answer_text, "
         "    answered_at, is_exception, exception_reason) VALUES (:i,:t,:q,:u,'NO',:at,TRUE,'Gate left open')",
         {"i": answer, "t": w["tenant"], "q": question, "u": w["users"][OPERATOR], "at": _ago(seconds=15)}),
    ])
    await _pass(w)
    read = {(e["source_table"], e["source_id"]): e for e in await _events(w)}
    assert ("drone_events", sighting) in read and len(read) == 2
    situations = await _rows(w, "security_situations")
    assert len(situations) == 2, "a sighting at one site and an exception at another are two matters"
    by_site = {s["site_id"]: s for s in situations}
    assert set(by_site) == {w["site_a"], w["site_b"]}
    assert await _rows(w, "incidents") == [], "neither opened anything by itself"

    async with _client() as c:
        for site in ("site_b", "site_a"):
            seen = await _ok(await c.get(f"{INTEL}/situations/{by_site[w[site]]['id']}", headers=w["h"][OPERATOR]))
            assert seen["incident"]["state"] != "CONFIRMED"
            # Where opening an incident was suggested the officer follows it; where it was not, they say why.
            offered = [r["action"] for r in await _rows(w, "security_recommendations", "rank")
                       if r["situation_id"] == by_site[w[site]]["id"] and r["available"]]
            why = {} if "CREATE_INCIDENT" in offered else {
                "reason_code": "OTHER", "note": "Worth an incident of its own: the client asks for one each time."}
            made = await _ok(await _decide(c, w, by_site[w[site]], OPERATOR, "CREATE_INCIDENT", **why), 201)
            assert made["basis"] in (("OVERRIDE", "INDEPENDENT") if why else ("FOLLOWED",)), made["basis"]
    incidents = await _rows(w, "incidents")
    assert len(incidents) == 2 and all(i["is_auto_created"] is False for i in incidents)
    after = {s["site_id"]: s for s in await _rows(w, "security_situations")}
    assert {after[w["site_a"]]["incident_id"], after[w["site_b"]]["incident_id"]} == {i["id"] for i in incidents}
    decisions = await _rows(w, "security_decisions", "decided_at")
    assert [d["action"] for d in decisions] == ["CREATE_INCIDENT", "CREATE_INCIDENT"]
    assert {d["actor_user_id"] for d in decisions} == {w["users"][OPERATOR]}
    # What the drone module and the patrol recorded is as they recorded it.
    assert (await _sql("SELECT verification_state FROM drone_events WHERE id = :i", {"i": sighting}))[0]["verification_state"] == "VERIFIED"
    assert (await _sql("SELECT exception_reason FROM virtual_patrol_session_answers WHERE id = :i", {"i": answer}))[0][
        "exception_reason"] == "Gate left open"


# ─── C. A visitor, a door and a camera ───────────────────────────────────────

async def test_a_visitors_badge_is_set_against_their_authorisation_and_a_forced_door_joins_what_the_camera_saw():
    w = await _intel_world()
    doors = await _site_with_doors(w)
    visit = await _visit(w, "Lim Mei Ling")
    card = await _card(w, "V-17")
    async with _client() as c:
        # Visitor → access: authorised for Block A; the badge is used at a door of Block B.
        yes = await _approved(c, w, visit, place_ids=[str(doors["block_a"])])
        await _period(yes["id"], 180, -60)
        await _check_in(c, w, visit, "V-17", minutes_ago=150)
        inside = await _swipe(w, doors["d_a2"], card, 120)
        outside = await _swipe(w, doors["d_b"], card, 90)
        seen = (await _ok(await c.get(f"{VISITS}/{yes['id']}", headers=w["h"][SUPERVISOR])))["movements"]
        got = {i["access_event_id"]: i for i in seen["items"]}
        assert (got[str(inside)]["within"], got[str(inside)]["to_look_at"]) == (True, False)
        assert (got[str(outside)]["within"], got[str(outside)]["to_look_at"]) == (False, True)
        todo = await _ok(await c.get(f"{VISITS}/to-review", headers=w["h"][SUPERVISOR]))
        assert [i["access_event_id"] for i in todo["items"]] == [str(outside)]
        # It is listed for a person to look at. It raised nothing and accused nobody.
        assert await _sql("SELECT 1 FROM alerts WHERE tenant_id = :t", {"t": w["tenant"]}) == []
        assert await _sql("SELECT 1 FROM incidents WHERE tenant_id = :t", {"t": w["tenant"]}) == []
        done = await c.post(f"{VISITS}/{yes['id']}/movements/{outside}/review", headers=w["h"][SUPERVISOR],
                            json={"outcome": "IN_ORDER", "note": "Escorted to the canteen in Block B."})
        assert done.status_code == 201, done.text

    # Access → CCTV → alert: a door forced, and a person the camera saw there a moment later, are one matter.
    forced = await _alert(w, "access", code="access.door_forced", severity="high", title="Door forced: B lobby",
                          at=_ago(seconds=50))
    seen_by_camera = await _alert(w, "intrusion", code="intrusion.zone_breach", severity="high", title="Person at Gate 1",
                                  at=_ago(seconds=30))
    await _pass(w)
    events = await _events(w)
    assert {(e["source_table"], e["source_id"]) for e in events} == {("alerts", forced), ("alerts", seen_by_camera)}
    assert {e["source_type"] for e in events} == {"ACCESS_CONTROL", "CCTV_AI"}
    situations = await _rows(w, "security_situations")
    members = await _sql("SELECT situation_id, event_id FROM security_situation_events WHERE tenant_id = :t", {"t": w["tenant"]})
    assert len(situations) == 1, "placed together, as one situation"
    assert {m["event_id"] for m in members} == {e["id"] for e in events} and {m["situation_id"] for m in members} == {situations[0]["id"]}
    assert [r["action"] for r in await _rows(w, "security_recommendations", "rank")], "and something is suggested"
    assert await _rows(w, "security_decisions", "decided_at") == [] and await _sql(
        "SELECT 1 FROM incidents WHERE tenant_id = :t", {"t": w["tenant"]}) == []
    # Until the organisation asks, the chain stops there: the visitor's door events were set against the
    # authorisation, and are not events of the intelligence layer.
    assert all(e["source_table"] == "alerts" for e in events)
    assert "Lim Mei Ling" not in str(events) and "V-17" not in str(events)
    (review,) = await _audit(w, "visitorauth.movement_review")
    assert review["detail"]["outcome"] == "IN_ORDER" and review["user_id"] == w["users"][SUPERVISOR]

    # Asked for, it is joined: the next door event outside the authorisation is handed to the layer as an event
    # to look at, and still names nobody. The one a person already looked at is not handed over.
    async with _client() as c:
        await _hand_over(c, w, True)
    again = await _swipe(w, doors["d_b"], card, 1)
    tick = await intel_runner.run_ingest_tick(AsyncSessionLocal)   # the runner's own tick, as it is deployed
    assert tick["by_source"].get("visitor_movements") == 1
    await _pass(w)
    handed = [e for e in await _events(w) if e["source_table"] == "access_events"]
    assert [e["source_id"] for e in handed] == [again] and handed[0]["severity"] == "low" and handed[0]["subject_kind"] == "NONE"
    assert "Lim Mei Ling" not in str(handed) and "V-17" not in str(handed)
    assert await _rows(w, "security_decisions", "decided_at") == [], "handed over, placed, and still nothing decided"


# ─── D. A device that is down ────────────────────────────────────────────────

async def test_a_device_that_is_down_becomes_work_only_when_a_person_accepts_it():
    w = await _intel_world(enabled=False)
    dark = await _device_camera(w, "Loading bay", streams=("offline",))
    await _hit(w, dark, "stream_disconnected", minutes_ago=300)
    async with _client() as c:
        asset = await _ok(await c.post("/api/v1/security-assets", headers=w["h"][MANAGER], json={
            "name": "Loading bay camera", "kind": "CAMERA", "device_id": str(dark)}), 201)
        # Device health: read as down, and why.
        health = await _ok(await c.get(f"/api/v1/security-assets/health/CAMERA/{dark}", headers=w["h"][MANAGER]))
        assert health["state"] == "DOWN"
        # Nothing is put forward until the organisation asks for it.
        await _look()
        assert await _orders(w) == []
        await _ask_for_suggestions(c, w)
        await _look()
        (put,) = await _orders(w)
        assert (put["state"], put["origin"], put["asset_id"], put["assigned_to_user_id"]) == (
            "SUGGESTED", "HEALTH", uuid.UUID(asset["id"]), None)
        assert "has been down since" in put["suggestion_reason"]
        # Health → maintenance: it is work once a person accepts it, gives it to somebody, and it is done.
        url = f"{MAINTENANCE}/work-orders/{put['id']}"
        accepted = await _ok(await c.post(f"{url}/accept", headers=w["h"][MANAGER], json={
            "assigned_to_user_id": str(w["users"][OPERATOR]), "priority": "HIGH"}), 200)
        assert accepted["state"] == "OPEN"
        await _ok(await c.post(f"{url}/start", headers=w["h"][OPERATOR], json={}), 200)
        finished = await _ok(await c.post(f"{url}/complete", headers=w["h"][OPERATOR], json={
            "completion_note": "Power supply replaced.", "parts_used": "12 V supply", "downtime_minutes": 310}), 200)
        assert finished["state"] == "DONE"
        board = {s["key"]: s["figures"] for s in (await _ok(await c.get(
            "/api/v1/operations-board", headers=w["h"][ADMIN], params={"days": 1})))["sections"]}
        assert board["MAINTENANCE"]["done"] == 1 and board["MAINTENANCE"]["suggested_now"] == 0
    (order,) = await _orders(w)
    assert order["state"] == "DONE" and order["accepted_by_user_id"] == w["users"][MANAGER]
    # The work order says what was done. It did not touch the camera, and the reading is still what the camera reports.
    assert (await _sql("SELECT is_active FROM cameras WHERE id = :c", {"c": dark}))[0]["is_active"] is True
    assert (await _sql("SELECT status FROM streams WHERE camera_id = :c", {"c": dark}))[0]["status"] == "offline"
    await _people_only(w, ["maintenance.order.accept", "maintenance.order.start", "maintenance.order.complete"])


# ─── E. A pattern, advice, and a person's answer ─────────────────────────────

async def test_a_pattern_becomes_advice_a_person_answers_and_a_briefing_carries():
    w = await _intel_world(enabled=False)
    p = await _pattern(w)
    site = str(w["site_a"])
    async with _client() as c:
        # Risk: where what went wrong gathers.
        got = await _ok(await c.get(f"{ADVICE}/advice", headers=w["h"][OPERATOR], params={"site_id": site}))
        place = {f["code"]: f for f in got["findings"]}["RECURRING_PLACE"]
        assert place["statement"] == "Loading bay accounts for 86% of the incidents of the last 4 weeks (24 of 28)."
        assert place["key"] == f"RECURRING_PLACE:INCIDENT:{site}:{p['bay']}" and got["is_forecast"] is False
        # → recommendation: one thing to consider, with how much history it rests on.
        assert place["consider"] and place["confidence"]["why"].startswith("Rests on 28 records over 4 weeks")
        # → human decision: an answer, kept with the statement as it stood. An operator reads it and does not answer.
        body = {"site_id": site, "key": place["key"], "answer": "ACCEPTED"}
        assert (await c.post(f"{ADVICE}/advice/answer", headers=w["h"][OPERATOR], json=body)).status_code == 403
        answered = await _ok(await c.post(f"{ADVICE}/advice/answer", headers=w["h"][MANAGER], json=body), 201)
        assert answered["answer"]["answer"] == "ACCEPTED" and answered["answer"]["answered_by_name"] == "Role 8 User"
        # A briefing for the day carries the advice word for word, and a person publishes it.
        day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        draft = await _ok(await c.post("/api/v1/daily-briefings", headers=w["h"][MANAGER], json={
            "site_id": site, "briefing_date": day}), 201)
        stands_out = next(s for s in draft["sections"] if s["key"] == "ADVICE")
        assert any(place["statement"] in line["text"] for line in stands_out["lines"])
        published = await _ok(await c.post(f"/api/v1/daily-briefings/{draft['id']}/publish", headers=w["h"][MANAGER]), 200)
        assert published["state"] == "PUBLISHED"
    (kept,) = await _rows(w, "risk_advice_answers", "answered_at")
    assert kept["statement"] == place["statement"] and kept["answered_by_user_id"] == w["users"][MANAGER]
    # Accepting it changed nothing else: no work was raised, no guard was sent, no roster was touched.
    for table in ("maintenance_work_orders", "incident_responses", "shifts", "security_decisions"):
        assert await _sql(f"SELECT 1 FROM {table} WHERE tenant_id = :t", {"t": w["tenant"]}) == [], table
    assert len(await _rows(w, "incidents")) == 28, "the incidents the pattern was counted from, and no other"
    await _people_only(w, ["advice.answer", "briefing.draft", "briefing.publish"])


# ─── What no chain did ───────────────────────────────────────────────────────

async def test_the_application_role_follows_a_chain_s_links_only_within_its_organisation():
    """The same links, read as the application's role from another organisation's scope, are not there."""
    w, other = await _intel_world(enabled=False), await _intel_world(enabled=False)
    async with _client() as c:
        case = await _ok(await c.post(CASES, headers=w["h"][ADMIN], json={"title": "Gate", "summary": "Forced."}), 201)
        file = await _ok(await c.post(INVESTIGATIONS, headers=w["h"][ADMIN], json={
            "title": "Who forced the gate", "reason": "Reported by the client."}), 201)
        await _ok(await c.post(f"{CASES}/{case['id']}/links", headers=w["h"][ADMIN], json={
            "kind": "INVESTIGATION", "ref_id": file["id"]}), 201)
        # Another organisation cannot link the first one's record to a case of its own: to it, there is no such record.
        theirs = await _ok(await c.post(CASES, headers=other["h"][ADMIN], json={"title": "Ours", "summary": "Ours."}), 201)
        refused = await c.post(f"{CASES}/{theirs['id']}/links", headers=other["h"][ADMIN], json={
            "kind": "INVESTIGATION", "ref_id": file["id"]})
        assert refused.status_code in (404, 422), refused.text
        assert (await c.get(f"{INVESTIGATIONS}/{file['id']}", headers=other["h"][ADMIN])).status_code == 404
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        seen = (await db.execute(text("SELECT count(*) FROM case_links WHERE ref_id = CAST(:r AS uuid)"), {"r": file["id"]})).scalar()
        await db.rollback()
    assert seen == 0
