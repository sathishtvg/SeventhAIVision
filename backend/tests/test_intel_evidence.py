"""AI security intelligence, phase 12: the evidence of a situation, and its summary.

  A — The rules, with nothing running: where each kind of evidence is served,
      what a summary says and what it never says
  B — From real records: which evidence belongs to a situation, and that
      opening it is a person's act the platform records
  C — The summary through the API, and who sees what

The claims this phase makes, each with tests: the layer keeps and serves no
media and never returns a storage path; a piece of evidence belongs to a
situation because the records say so; opening one needs the permission its own
endpoint asks for, and leaves the platform's chain-of-custody entry and an
audit entry behind; and the summary is fixed templates over the timeline,
marked AI-assisted, each sentence pointing at the records it was read from.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.services import intel_evidence as evidence
from app.services import intel_summary as summary
from app.services import intel_timeline as tl
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql
from tests.test_intel_decisions import BASE, _decide, _pass, _url
from tests.test_intel_drone import _patrol_check, _sighted
from tests.test_intel_events import _alert, _detection, _world
from tests.test_drone_clients import _served
from tests.test_intel_timeline import (
    GUARD_P, OFFICER, SENIOR, _action, _assessment, _at, _built, _decision, _event, _rec, without_docstrings,
)

SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
SGT = "Asia/Singapore"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_each_kind_of_evidence_is_served_by_the_platforms_own_endpoint_never_by_this_layer():
    one, session = uuid.uuid4(), uuid.uuid4()
    assert evidence.served_at("SNAPSHOT", {"id": one}) == {"path": f"/api/v1/evidence/{one}/image",
                                                          "token_in_query": True}
    assert evidence.served_at("CLIP", {"id": one})["path"] == f"/api/v1/evidence/{one}/image"
    assert evidence.served_at("RECORDING", {"id": one}) == {"path": f"/api/v1/recordings/{one}/play",
                                                           "token_in_query": True}
    assert evidence.served_at("DRONE_MEDIA", {"id": one}) == {"path": f"/api/v1/drone-media/{one}/file",
                                                             "token_in_query": False}
    assert evidence.served_at("PATROL_SNAPSHOT", {"id": one, "session_id": session}) == {
        "path": f"/api/v1/virtual-patrol/sessions/{session}/cameras/{one}/snapshot", "token_in_query": True}
    for kind in evidence.KINDS:
        path = evidence.served_at(kind, {"id": one, "session_id": session})["path"]
        assert "security-intelligence" not in path, f"{kind}: the layer serves no media"
    # Every one of those paths is an operation the application really serves.
    spec = app.openapi()
    for kind in evidence.KINDS:
        path = evidence.served_at(kind, {"id": one, "session_id": session})["path"]
        assert _served("GET", path, spec), f"{kind}: {path} is not served"
    assert set(evidence.NEEDS) == set(evidence.CUSTODY) == set(evidence.KINDS)
    assert evidence.NEEDS == {"SNAPSHOT": "evidence:read", "CLIP": "evidence:read", "RECORDING": "recording:read",
                              "DRONE_MEDIA": "drone:event:read", "PATROL_SNAPSHOT": "vpatrol:read"}


def test_a_recording_says_how_far_into_it_the_event_is_or_that_it_was_not_running():
    running = {"started_at": _at(0), "ended_at": _at(600)}
    assert evidence.offset_into(running, _at(0)) == 0 and evidence.offset_into(running, _at(125.9)) == 125
    assert evidence.offset_into(running, _at(600)) == 600
    assert evidence.offset_into(running, _at(-1)) is None and evidence.offset_into(running, _at(601)) is None
    assert evidence.offset_into({"started_at": _at(0), "ended_at": None}, _at(9000)) == 9000, "still recording"


def test_the_layer_never_selects_a_storage_path_and_neither_module_writes():
    for name in ("intel_evidence", "intel_summary"):
        code = without_docstrings(SERVICES / f"{name}.py")
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", code) and ".commit(" not in code, name
        assert "intel_actions" not in code, name
    code = without_docstrings(SERVICES / "intel_evidence.py")
    assert "FROM evidence ev" in code and "FROM recordings r" in code, "the statements are what is being looked at"
    assert "storage_path" not in code and "file_path" not in code
    assert re.findall(r"snapshot_path[^\n]*", code) == ["snapshot_path IS NOT NULL"], "asked whether, never where"
    # The summary is templates: it reads no database and calls no model.
    words = (SERVICES / "intel_summary.py").read_text(encoding="utf-8")
    assert "sqlalchemy" not in words and "AsyncSession" not in words and "http" not in words.lower()
    for name in ("intel_runner", "intel_events", "intel_correlation", "intel_risk", "intel_recommend"):
        text_ = (SERVICES / f"{name}.py").read_text(encoding="utf-8")
        assert "intel_evidence" not in text_ and "intel_summary" not in text_, name


def _story() -> tuple[dict, list[dict], list[dict]]:
    """The specification's example, as a timeline."""
    incident = uuid.uuid4()
    first = _assessment(58, 1, "HIGH", 65, 3)
    entries = _built(
        events=[_event(0, "Person at Gate 1", camera_name="Gate 1"),
                _event(4, "Access denied at the rear door", method="ACCESS_AT_CAMERA", source="ACCESS_CONTROL",
                       reason="An access event at the door this camera watches, 4 s apart."),
                _event(39, "Possible unauthorised person", method="NEAR_POSITION", source="DRONE_PATROL",
                       reason="The drone's sighting was 20 m from Gate 1, 39 s apart.")],
        assessments=[first],
        recommendations=[_rec(62, first, 1, "DISPATCH_GUARD", "More than one kind of source reported this.")],
        decisions_=[
            _decision(71, "DISPATCH_GUARD", actions=[_action(72, 1, "INCIDENT_CREATE", target=incident),
                                                     _action(73, 2, "INCIDENT_DISPATCH", target=incident)]),
            _decision(656, "RESOLVE", by=SENIOR, basis="CLOSING", reason="Authorised activity",
                      actions=[_action(656, 1, "ALERT_DISMISS"), _action(656.5, 2, "INCIDENT_RESOLVE", target=incident)])],
        observations=[{"id": uuid.uuid4(), "kind": "ARRIVED", "note": None, **GUARD_P, "latitude": 1.3, "longitude": 103.8,
                       "via": "mobile", "observed_at": _at(447)},
                      {"id": uuid.uuid4(), "kind": "OBSERVATION", "note": "Authorised maintenance worker, badge checked.",
                       **GUARD_P, "latitude": None, "longitude": None, "via": "mobile", "observed_at": _at(606)}])
    situation = {"id": uuid.uuid4(), "situation_number": "SIT-20261005-0001", "site_name": "Factory A",
                 "decision_status": "RESOLVED", "closed_at": _at(656)}
    return situation, entries, [{**first, "assessed_at": _at(58)}]


def test_the_summary_tells_the_specifications_example_in_fixed_words_from_the_records():
    situation, entries, assessments = _story()
    s = summary.summarise(situation, entries, assessments, SGT)
    assert [x["text"] for x in s["sentences"]] == [
        "At 10:17 on 5 Oct 2026, a camera reported “Person at Gate 1” at Gate 1, Factory A.",
        "Within 39 s, 2 more report(s) joined it: access control (“Access denied at the rear door”, 10:17); "
        "a drone (“Possible unauthorised person”, 10:17).",
        "The layer assessed it as HIGH risk (65): Access refused, with activity seen nearby.",
        "Its suggestion was to dispatch a guard. A suggestion is not a decision.",
        "At 10:18, Priya (Operator) decided to dispatch a guard, following what the layer suggested.",
        "The platform then carried out: Incident opened; Guard dispatched.",
        "At 10:24, Tan Wei Ming (Guard) reported from the ground: arrived.",
        "At 10:27, Tan Wei Ming (Guard) reported from the ground: “Authorised maintenance worker, badge checked.”",
        "At 10:28, Kumar (Supervisor) decided to resolve it. Closed the situation — Authorised activity.",
        "The platform then carried out: Alerts closed; Incident resolved.",
        "The situation was closed at 10:28: resolved.",
    ]
    assert s["text"] == " ".join(x["text"] for x in s["sentences"])
    assert (s["is_ai_assisted"], s["label"], s["timezone"]) == (True, "AI-assisted summary", SGT)
    assert s["made_of"].startswith("Made only of what is recorded.")
    assert s["situation_number"] == "SIT-20261005-0001"


def test_every_sentence_that_states_a_record_points_at_it():
    situation, entries, assessments = _story()
    s = summary.summarise(situation, entries, assessments, SGT)
    known = {(e["ref"]["type"], str(e["ref"]["id"])) for e in entries} | {("assessment", str(assessments[0]["id"]))}
    for sentence in s["sentences"][:-1]:
        assert sentence["refs"], sentence["text"]
        assert {(r["type"], str(r["id"])) for r in sentence["refs"]} <= known, sentence["text"]
    assert len(s["sentences"][1]["refs"]) == 2, "one for each further report it names"
    assert s["sentences"][-1]["refs"] == [], "where it stands is the situation itself"


def test_a_reader_who_may_not_see_suggestions_gets_a_summary_that_does_not_mention_them():
    situation, entries, assessments = _story()
    without = [e for e in entries if e["kind"] != "RECOMMENDATION"]
    s = summary.summarise(situation, without, assessments, SGT)
    assert "suggestion was" not in s["text"] and "A suggestion is not a decision" not in s["text"]
    assert "following what the layer suggested" in s["text"], "what a person recorded about their own decision stays"
    full = summary.summarise(situation, entries, assessments, SGT)
    assert [x for x in full["sentences"] if "Its suggestion was" not in x["text"]] == s["sentences"]


def test_the_summary_says_an_override_a_proposal_a_refusal_and_a_failure_as_what_they_were():
    def told(**over) -> str:
        situation = {"id": uuid.uuid4(), "situation_number": "SIT-1", "site_name": None, "decision_status": "IN_HAND",
                     "closed_at": None}
        return summary.summarise(situation, _built(**over), [], "UTC")["text"]

    override = told(decisions_=[_decision(60, "MONITOR", basis="OVERRIDE", reason="Guard already responding",
                                          suggested="DISPATCH_GUARD", note="Patrol car is there.")])
    assert ("At 02:18, Priya (Operator) decided to keep watching. An override — Guard already responding. The layer "
            "had put “dispatch a guard” first. Note: Patrol car is there.") in override
    approval = {"verdict": "REJECTED", "note": "Wait for the camera.", "at": _at(120), "by": SENIOR}
    proposal = told(decisions_=[_decision(60, "CREATE_INCIDENT", by=GUARD_P, authority="WITH_APPROVAL",
                                          approval=approval)])
    assert ("At 02:18, Tan Wei Ming (Guard) proposed to open an incident, following what the layer suggested, to wait "
            "for a second person's approval.") in proposal
    assert "At 02:19, Kumar (Supervisor) rejected it. Their note: “Wait for the camera.”" in proposal
    failed = told(decisions_=[_decision(60, "DISPATCH_GUARD", actions=[
        _action(61, 1, "INCIDENT_CREATE", target=uuid.uuid4()),
        _action(62, 2, "INCIDENT_DISPATCH", "FAILED", detail="409: This guard is already dispatched.")])])
    assert ("The platform then carried out: Incident opened. Could not dispatch the guard (409: This guard is already "
            "dispatched.).") in failed
    assert "Guard dispatched" not in failed, "a step that failed is never told as done"
    recorded = told(decisions_=[_decision(60, "MONITOR", actions=[_action(60, 1, "NONE", "RECORDED")])])
    assert "carried out" not in recorded, "a decision that was only a record says nothing was done by saying nothing"
    assert told().endswith("The layer has not assessed it yet. The situation is open: it is in hand.")
    former = told(decisions_=[_decision(60, "ACKNOWLEDGE", basis="INDEPENDENT",
                                        by={"user_id": None, "name": None, "role_id": 99})])
    assert "A former user decided to acknowledge, as their own decision." in former


def test_the_summary_counts_what_it_does_not_name_and_points_at_the_timeline_when_it_runs_long():
    many = [_event(0, "Person at Gate 1")] + [
        _event(10 * n, f"Report {n}", method="ADJACENT_CAMERA", reason="Nearby.") for n in range(1, 8)]
    again = dict(method="SAME_SOURCE_REPEAT", duplicate=True, reason="Again.")
    repeats = [_event(300 + n, "Person at Gate 1", **again) for n in range(5)]
    situation = {"id": uuid.uuid4(), "situation_number": "SIT-1", "site_name": "Factory A", "decision_status": "AWAITING",
                 "closed_at": None}
    a1, a2 = _assessment(60, 1, "HIGH", 60), _assessment(400, 2, "CRITICAL", 85)
    s = summary.summarise(situation, _built(events=many + repeats), [a2, a1], SGT)
    texts = [x["text"] for x in s["sentences"]]
    assert texts[1].startswith("Within 70 s, 7 more report(s) joined it: ") and texts[1].endswith("; and 3 more.")
    assert texts[2] == ("The same alert repeated 5 time(s); the repeats were folded and each is still an alert of "
                        "its own.")
    assert texts[3] == ("The layer assessed it as CRITICAL risk (85): Access refused, with activity seen nearby. That "
                        "is its assessment number 2; the first, at 10:18, was HIGH (60).")
    assert texts[-1] == "The situation is open: nobody has decided on it yet."
    # Sixty reports from the ground: the summary stops and says where the rest are.
    reports = [{"id": uuid.uuid4(), "kind": "OBSERVATION", "note": f"Check {n}", **GUARD_P, "latitude": None,
                "longitude": None, "via": "mobile", "observed_at": _at(500 + n)} for n in range(60)]
    long = summary.summarise(situation, _built(events=many, observations=reports), [a1], SGT)
    assert len(long["sentences"]) == summary.MAX_SENTENCES
    assert re.fullmatch(r"\d+ further entries are in the timeline and are not repeated here\.",
                        long["sentences"][-2]["text"])
    assert long["sentences"][-1]["text"].startswith("The situation is open")


def test_the_summarys_words_cover_every_source_and_standing_and_state_no_intent():
    from app.services import intel_decisions, intel_events

    assert set(summary.SOURCE_WORDS) == set(intel_events.SOURCE_TYPES)
    assert set(summary.STANDS) == set(intel_decisions.STATUSES)
    templates = without_docstrings(SERVICES / "intel_summary.py")
    for word in ("intruder", "unauthorised", "unauthorized", "criminal", "thief", "trespass", "suspect "):
        assert word not in templates.lower(), f"a template says “{word}” — only a source's own words may, quoted"
    assert set(tl.STEP_WORDS) >= {"DISPATCH_GUARD", "RESOLVE"}, "the summary speaks the timeline's own words"


# ─── B. From real records ────────────────────────────────────────────────────

async def _matter() -> tuple[dict, dict]:
    """A camera's alert with its detection, a patrol's exception on the same
    camera with a snapshot, and everything the platform could have kept."""
    from tests.test_intel_decisions import _ago

    w = await _world()
    w["detection"], w["at"] = await _detection(w, w["cam_a"], "intrusion", at=_ago(seconds=90))
    w["alert"] = await _alert(w, "intrusion", code="intrusion.zone_breach", severity="critical",
                              title="Person at Gate 1", at=w["at"], detection=w["detection"])
    w["patrol"] = await _patrol_check(w, minutes_ago=3, answers=[("NO", True)], snapshot=True)
    ids = {k: uuid.uuid4() for k in ("frame", "crop", "clip", "stray", "stream", "stream_b", "rec", "rec_before",
                                     "rec_b")}
    at = w["at"]
    ev = ("INSERT INTO evidence (id, tenant_id, detection_id, media_type, storage_path, checksum_sha256, captured_at, "
          "    capture_kind, site_id) VALUES (:i,:t,:d,:m,:p,:c,:at,:k,:s)")
    rec = ("INSERT INTO recordings (id, tenant_id, camera_id, stream_id, site_id, started_at, ended_at, status, "
           "    file_path, checksum_sha256) VALUES (:i,:t,:c,:st,:s,:a,:b,'completed',:p,:sum)")
    base = {"t": w["tenant"], "s": w["site_a"]}
    await _run([
        (ev, {**base, "i": ids["frame"], "d": w["detection"], "m": "image", "p": "frames/secret-frame.jpg",
              "c": "a" * 64, "at": at, "k": "frame"}),
        (ev, {**base, "i": ids["crop"], "d": w["detection"], "m": "image", "p": "crops/secret-plate.jpg", "c": None,
              "at": at + timedelta(seconds=1), "k": "plate_crop"}),
        (ev, {**base, "i": ids["clip"], "d": w["detection"], "m": "video", "p": "clips/secret-clip.mp4",
              "c": "c" * 64, "at": at + timedelta(seconds=2), "k": "clip"}),
        # A frame of some other detection at the same moment: not this situation's.
        (ev, {**base, "i": ids["stray"], "d": uuid.uuid4(), "m": "image", "p": "frames/secret-stray.jpg", "c": None,
              "at": at, "k": "frame"}),
        ("INSERT INTO streams (id, tenant_id, camera_id, url) VALUES (:i,:t,:c,'rtsp://10.0.0.9/secret')",
         {"i": ids["stream"], "t": w["tenant"], "c": w["cam_a"]}),
        ("INSERT INTO streams (id, tenant_id, camera_id, url) VALUES (:i,:t,:c,'rtsp://10.0.0.8/secret')",
         {"i": ids["stream_b"], "t": w["tenant"], "c": w["cam_b"]}),
        (rec, {**base, "i": ids["rec"], "c": w["cam_a"], "st": ids["stream"], "a": at - timedelta(minutes=10),
               "b": at + timedelta(minutes=10), "p": "rec/secret-a.mp4", "sum": "d" * 64}),
        # That camera's recording from an hour before, and another camera's from the time: neither belongs.
        (rec, {**base, "i": ids["rec_before"], "c": w["cam_a"], "st": ids["stream"], "a": at - timedelta(hours=2),
               "b": at - timedelta(hours=1), "p": "rec/secret-old.mp4", "sum": None}),
        (rec, {"t": w["tenant"], "s": w["site_b"], "i": ids["rec_b"], "c": w["cam_b"], "st": ids["stream_b"],
               "a": at - timedelta(minutes=10), "b": at + timedelta(minutes=10), "p": "rec/secret-b.mp4", "sum": None}),
    ])
    w["ids"] = ids
    await _pass(w)
    rows = await _sql("""
        SELECT s.* FROM security_situations s JOIN security_situation_events l ON l.situation_id = s.id
          JOIN security_events e ON e.id = l.event_id WHERE e.source_id = :a""", {"a": w["alert"]})
    return w, dict(rows[0])


async def _logged(w: dict) -> tuple[list, list]:
    custody = await _sql("SELECT evidence_id, user_id, action FROM evidence_access_log WHERE tenant_id = :t "
                         " ORDER BY accessed_at", {"t": w["tenant"]})
    audit = await _sql("SELECT user_id, resource_id, detail FROM audit_logs WHERE tenant_id = :t "
                       "   AND action = 'intel.evidence.open' ORDER BY created_at, id", {"t": w["tenant"]})
    return [dict(r) for r in custody], [dict(r) for r in audit]


def _open(c, w: dict, s: dict, role: int, kind: str, item_id):
    return c.post(_url(s, "evidence/open"), headers=w["h"][role], json={"kind": kind, "id": str(item_id)})


@pytest.mark.asyncio
async def test_what_belongs_to_a_situation_is_found_by_what_the_records_say_and_no_path_is_handed_out():
    w, s = await _matter()
    ids = w["ids"]
    async with _client() as c:
        r = await c.get(_url(s, "evidence"), headers=w["h"][OPERATOR])
    assert r.status_code == 200, r.text
    body = r.json()
    items = body["items"]
    assert {(i["kind"], i["id"]) for i in items} == {
        ("SNAPSHOT", str(ids["frame"])), ("SNAPSHOT", str(ids["crop"])), ("CLIP", str(ids["clip"])),
        ("RECORDING", str(ids["rec"])), ("PATROL_SNAPSHOT", str(w["patrol"]["camera"]))}, \
        "the stray frame, the old recording and the other camera's recording are not this situation's"
    assert [i["captured_at"] for i in items] == sorted(i["captured_at"] for i in items)
    by = {i["id"]: i for i in items}
    frame, crop, clip = by[str(ids["frame"])], by[str(ids["crop"])], by[str(ids["clip"])]
    assert (frame["what"], crop["what"], clip["what"]) == ("Frame at the detection", "Number plate, cropped",
                                                           "Clip of the detection")
    assert frame["checksum_sha256"] == "a" * 64 and crop["checksum_sha256"] is None
    assert frame["served_at"] == {"path": f"/api/v1/evidence/{ids['frame']}/image", "token_in_query": True}
    assert (frame["needs"], frame["logged_in"], frame["camera_name"]) == ("evidence:read", "evidence_access_log",
                                                                        "Gate 1")
    assert (clip["media_type"], frame["media_type"]) == ("video", "image")
    # The recording points at the first thing that happened on that camera while
    # it was running — here the patrol's check, a little before the alert — and
    # says how far in it is.
    rec = by[str(ids["rec"])]
    earliest = (await _sql(
        "SELECT e.id, e.occurred_at FROM security_situation_events l JOIN security_events e ON e.id = l.event_id "
        " WHERE l.situation_id = :s AND e.camera_id = :c ORDER BY e.occurred_at LIMIT 1",
        {"s": s["id"], "c": w["cam_a"]}))[0]
    expected = int((earliest["occurred_at"] - (w["at"] - timedelta(minutes=10))).total_seconds())
    assert (rec["what"], rec["needs"]) == ("Recording of Gate 1", "recording:read")
    assert rec["offset_seconds"] == expected and 0 < expected <= 600 and rec["event_id"] == str(earliest["id"])
    assert rec["served_at"]["path"] == f"/api/v1/recordings/{ids['rec']}/play" and rec["checksum_sha256"] == "d" * 64
    snap = by[str(w["patrol"]["camera"])]
    assert snap["what"] == "Snapshot taken at the virtual patrol's check" and snap["needs"] == "vpatrol:read"
    assert snap["served_at"] == {
        "path": f"/api/v1/virtual-patrol/sessions/{w['patrol']['session']}/cameras/{w['patrol']['camera']}/snapshot",
        "token_in_query": True}
    # Each belongs to one of the situation's own events.
    events = {str(x["id"]) for x in await _sql(
        "SELECT e.id FROM security_situation_events l JOIN security_events e ON e.id = l.event_id "
        " WHERE l.situation_id = :s", {"s": s["id"]})}
    assert {i["event_id"] for i in items} <= events and all(i["event_id"] for i in items)
    assert body["summary"] == {"total": 5, "may_open": 5, "by_kind": {
        "SNAPSHOT": 2, "CLIP": 1, "RECORDING": 1, "DRONE_MEDIA": 0, "PATROL_SNAPSHOT": 1}}
    # Where a file is kept on disk, and where a camera is on the network, never leave.
    assert "secret" not in r.text and "storage_path" not in r.text and "file_path" not in r.text


@pytest.mark.asyncio
async def test_opening_is_a_persons_act_and_leaves_the_custody_entry_and_the_audit_entry_behind():
    w, s = await _matter()
    ids = w["ids"]
    assert await _logged(w) == ([], []), "listing the evidence opened none of it"
    async with _client() as c:
        await c.get(_url(s, "evidence"), headers=w["h"][OPERATOR])
        assert await _logged(w) == ([], [])
        frame = await _open(c, w, s, OPERATOR, "SNAPSHOT", ids["frame"])
        recording = await _open(c, w, s, SUPERVISOR, "RECORDING", ids["rec"])
    assert frame.status_code == 200 and recording.status_code == 200, (frame.text, recording.text)
    f = frame.json()
    assert f["served_at"] == {"path": f"/api/v1/evidence/{ids['frame']}/image", "token_in_query": True}
    assert (f["kind"], f["media_type"], f["checksum_sha256"], f["audited"]) == ("SNAPSHOT", "image", "a" * 64, True)
    custody, audit = await _logged(w)
    assert [(x["evidence_id"], x["user_id"], x["action"]) for x in custody] == [
        (ids["frame"], w["users"][OPERATOR], "view")], "the platform's own chain of custody, one entry, theirs"
    assert f["custody_entry"] is not None and recording.json()["custody_entry"] is None, \
        "a recording has no custody log in the platform: its opening is in the audit log"
    assert [x["user_id"] for x in audit] == [w["users"][OPERATOR], w["users"][SUPERVISOR]]
    first = audit[0]["detail"] if isinstance(audit[0]["detail"], dict) else json.loads(audit[0]["detail"])
    assert str(audit[0]["resource_id"]) == str(s["id"])
    assert (first["kind"], first["evidence_id"], first["situation_number"], first["checksum_sha256"]) == (
        "SNAPSHOT", str(ids["frame"]), s["situation_number"], "a" * 64)
    assert first["actor_role"] == OPERATOR and first["result"] == "ok" and "secret" not in json.dumps(first)


@pytest.mark.asyncio
async def test_opening_is_refused_without_the_endpoints_own_permission_or_for_what_is_not_this_situations():
    w, s = await _matter()
    other, _ = await _matter()
    ids = w["ids"]
    async with _client() as c:
        listed = (await c.get(_url(s, "evidence"), headers=w["h"][VIEWER])).json()["items"]
        patrol = await _open(c, w, s, VIEWER, "PATROL_SNAPSHOT", w["patrol"]["camera"])
        allowed = await _open(c, w, s, VIEWER, "SNAPSHOT", ids["frame"])
        stray = await _open(c, w, s, OPERATOR, "SNAPSHOT", ids["stray"])
        wrong_kind = await _open(c, w, s, OPERATOR, "RECORDING", ids["frame"])
        theirs = await _open(c, w, s, OPERATOR, "SNAPSHOT", other["ids"]["frame"])
        outsider = await c.post(_url(s, "evidence/open"), headers=other["h"][ADMIN],
                                json={"kind": "SNAPSHOT", "id": str(ids["frame"])})
        outsider_list = await c.get(_url(s, "evidence"), headers=other["h"][ADMIN])
        extra = await c.post(_url(s, "evidence/open"), headers=w["h"][OPERATOR],
                             json={"kind": "SNAPSHOT", "id": str(ids["frame"]), "path": "/etc/passwd"})
        unknown = await c.post(_url(s, "evidence/open"), headers=w["h"][OPERATOR],
                               json={"kind": "MEMORY", "id": str(ids["frame"])})
        nobody = await c.get(_url(s, "evidence"))
    may = {i["kind"]: i["may_open"] for i in listed}
    assert may == {"SNAPSHOT": True, "CLIP": True, "RECORDING": True, "PATROL_SNAPSHOT": False}, \
        "a viewer may read evidence and recordings on this platform, and not a patrol's records"
    assert patrol.status_code == 403 and patrol.json()["detail"] == "Opening this needs the permission vpatrol:read."
    assert allowed.status_code == 200
    assert [x.status_code for x in (stray, wrong_kind, theirs, outsider, outsider_list)] == [404] * 5
    assert stray.json()["detail"] == "That is not a piece of this situation's evidence."
    assert extra.status_code == 422 and unknown.status_code == 422 and nobody.status_code in (401, 403)
    custody, audit = await _logged(w)
    assert len(custody) == 1 and len(audit) == 1, "only what was opened was recorded as opened"
    assert custody[0]["user_id"] == w["users"][VIEWER]


@pytest.mark.asyncio
async def test_evidence_kept_with_the_incident_and_a_drones_media_belong_too():
    w, s = await _matter()
    kept = uuid.uuid4()
    async with _client() as c:
        made = await _decide(c, w, s, OPERATOR, "CREATE_INCIDENT")
        assert made.status_code == 201, made.text
        incident = (await _sql("SELECT incident_id FROM security_situations WHERE id = :s", {"s": s["id"]}))[0][
            "incident_id"]
        await _sql("INSERT INTO evidence (id, tenant_id, incident_id, media_type, storage_path, captured_at, site_id) "
                   "VALUES (:i,:t,:inc,'image','frames/secret-scene.jpg',now(),:s)",
                   {"i": kept, "t": w["tenant"], "inc": incident, "s": w["site_a"]})
        items = (await c.get(_url(s, "evidence"), headers=w["h"][OPERATOR])).json()["items"]
    scene = next(i for i in items if i["id"] == str(kept))
    assert scene["what"] == "Frame at the detection — kept with the incident" and scene["event_id"] is None

    # A drone's sighting, from a real simulated flight, with the media the edge would have synced.
    d, sid, t, e, ds = await _sighted()
    snapshot, clip = uuid.uuid4(), uuid.uuid4()
    media = ("INSERT INTO drone_event_media (id, tenant_id, event_id, session_id, media_kind, storage_path, "
             "    captured_at, checksum_sha256) VALUES (:i,:t,:e,:s,:k,:p,:at,:c)")
    await _run([
        (media, {"i": snapshot, "t": d["tenant"], "e": e["id"], "s": uuid.UUID(sid), "k": "SNAPSHOT",
                 "p": "drone/secret-1.jpg", "at": t, "c": "e" * 64}),
        (media, {"i": clip, "t": d["tenant"], "e": e["id"], "s": uuid.UUID(sid), "k": "EVENT_CLIP",
                 "p": "drone/secret-2.mp4", "at": t + timedelta(seconds=1), "c": None})])
    async with _client() as c:
        listed = await c.get(f"{BASE}/situations/{ds['id']}/evidence", headers=d["h_op"])
        opened = await c.post(f"{BASE}/situations/{ds['id']}/evidence/open", headers=d["h_op"],
                              json={"kind": "DRONE_MEDIA", "id": str(snapshot)})
    assert listed.status_code == 200 and "secret" not in listed.text
    drone_items = [(i["kind"], i["what"], i["media_type"]) for i in listed.json()["items"]]
    assert drone_items == [("DRONE_MEDIA", "Drone snapshot", "image"),
                           ("DRONE_MEDIA", "Drone clip of the sighting", "video")]
    assert opened.status_code == 200 and opened.json()["served_at"] == {
        "path": f"/api/v1/drone-media/{snapshot}/file", "token_in_query": False}
    assert opened.json()["custody_entry"] is None
    custody, audit = await _logged(d)
    assert custody == [] and len(audit) == 1 and audit[0]["user_id"] == d["operator"]


@pytest.mark.asyncio
async def test_listing_evidence_and_reading_the_summary_write_nothing():
    w, s = await _matter()

    async def marks() -> list:
        return [(await _sql(f"SELECT count(*) AS n, max(xmin::text::bigint) AS x FROM {name} WHERE tenant_id = :t",
                            {"t": w["tenant"]}))[0] for name in (
            "evidence", "evidence_access_log", "recordings", "audit_logs", "security_situations", "security_events",
            "security_assessments", "security_recommendations", "alerts", "incidents")]

    before = await marks()
    async with _client() as c:
        for _ in range(2):
            assert (await c.get(_url(s, "evidence"), headers=w["h"][OPERATOR])).status_code == 200
            assert (await c.get(_url(s, "summary"), headers=w["h"][OPERATOR])).status_code == 200
    assert await marks() == before


# ─── C. The summary through the API ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_summary_of_a_real_situation_is_marked_ai_assisted_and_every_reference_is_to_a_real_record():
    w, s = await _matter()
    async with _client() as c:
        decided = await _decide(c, w, s, OPERATOR, "CREATE_INCIDENT")
        assert decided.status_code == 201, decided.text
        closed = await _decide(c, w, s, SUPERVISOR, "RESOLVE", reason_code="AUTHORISED_ACTIVITY")
        assert closed.status_code == 201, closed.text
        r = await c.get(_url(s, "summary"), headers=w["h"][OPERATOR])
        viewer = await c.get(_url(s, "summary"), headers=w["h"][VIEWER])
        outsider = await c.get(f"{BASE}/situations/{uuid.uuid4()}/summary", headers=w["h"][ADMIN])
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["is_ai_assisted"], body["label"], body["suggestions_shown"]) == (True, "AI-assisted summary", True)
    assert body["timezone"] == "Asia/Singapore" and body["situation_number"] == s["situation_number"]
    texts = [x["text"] for x in body["sentences"]]
    assert re.fullmatch(r"At \d\d:\d\d on \d{1,2} \w{3} \d{4}, .+ reported “.+” at Gate 1, .+\.", texts[0]), texts[0]
    assert any(t.startswith("The layer assessed it as ") for t in texts)
    assert any("Role 4 User (Operator) decided to open an incident" in t for t in texts)
    assert any(t == "The platform then carried out: Incident opened." for t in texts)
    assert any("Role 3 User (Supervisor) decided to resolve it. Closed the situation — Authorised activity." in t
               for t in texts)
    assert texts[-1].startswith("The situation was closed at ") and texts[-1].endswith(": resolved.")
    assert body["text"] == " ".join(texts)
    tables = {"event": "security_events", "assessment": "security_assessments",
              "recommendation": "security_recommendations", "decision": "security_decisions",
              "observation": "security_observations", "incident": "incidents"}
    refs = {(ref["type"], ref["id"]) for x in body["sentences"] for ref in x["refs"]}
    assert len(refs) >= 5
    for kind, ref_id in refs:
        found = await _sql(f"SELECT 1 FROM {tables[kind]} WHERE id = :i AND tenant_id = :t",
                           {"i": uuid.UUID(ref_id), "t": w["tenant"]})
        assert found, f"the summary points at a {kind} that does not exist: {ref_id}"
    assert viewer.status_code == 200 and viewer.json()["suggestions_shown"] is False
    assert "suggestion was" not in viewer.json()["text"] and "suggestion was" in body["text"]
    assert outsider.status_code == 404
