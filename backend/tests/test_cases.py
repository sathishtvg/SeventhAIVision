"""Security cases: opened from an incident or an investigation, worked by the people on them, closed by two.

  A — Who may do what, with nothing running
  B — A case from opening to closing, and reopening
  C — Whose case somebody may read, and what of its links
  D — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced.

The claims, each with tests: a case refers to what it is about and copies
nothing; a person works on a case they are on; closing takes two people and a
closed case is not changed; nothing is removed; being named in a case is not
an accusation and nothing names anybody by itself; every step is a person's,
is in the case's own history, and is audited; nobody is told.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import cases as api
from app.services import case_files
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

BASE = "/api/v1/cases"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0155_case_files.py").read_text(encoding="utf-8")
EVERYTHING = frozenset(api.HELD)
#: Words that would make being named in a case an accusation.
ACCUSING = ("suspect", "offender", "culprit", "perpetrator", "accused", "guilty", "intruder", "thief", "criminal")


# ─── A. Who may do what ──────────────────────────────────────────────────────

def test_a_person_works_on_a_case_they_are_on_and_closing_takes_two():
    me, other = uuid.uuid4(), uuid.uuid4()
    worker, manager = frozenset({"case:read", "case:work"}), frozenset({"case:read", "case:work", "case:manage"})
    case = {"status": "OPEN", "lead_user_id": me, "close_requested_by_user_id": None}
    assert case_files.on_case(case, [], me) and case_files.on_case({**case, "lead_user_id": other}, [str(me)], me)
    assert not case_files.on_case({**case, "lead_user_id": other}, [], me)
    # Open: whoever is on it works on it; whoever manages cases works on any, and assigns.
    assert case_files.may(case, me, worker, True) == {"work": True, "assign": False, "request_close": True,
                                                    "approve_close": False, "decline_close": False, "reopen": False}
    assert not any(case_files.may(case, me, worker, False).values()), "holding case:work is not being on the case"
    assert case_files.may(case, me, manager, False)["work"] and case_files.may(case, me, manager, False)["assign"]
    assert not any(case_files.may(case, me, frozenset({"case:read"}), True).values())
    # Waiting for approval: nobody works on it; whoever manages cases decides — but not whoever asked.
    waiting = {**case, "status": "AWAITING_APPROVAL", "close_requested_by_user_id": me}
    mine = case_files.may(waiting, me, manager, True)
    assert (mine["work"], mine["approve_close"], mine["decline_close"]) == (False, False, True)
    assert case_files.may(waiting, other, manager, False)["approve_close"] is True
    assert not any(case_files.may(waiting, other, worker, True).values())
    # Closed: nothing but reopening, by whoever manages cases.
    closed = {**case, "status": "CLOSED"}
    assert case_files.may(closed, me, manager, True) == {"work": False, "assign": False, "request_close": False,
                                                        "approve_close": False, "decline_close": False, "reopen": True}
    assert not any(case_files.may(closed, me, worker, True).values())
    assert case_files.unfinished([{"state": "OPEN"}, {"state": "DONE"}, {"state": "DROPPED"}, {"state": "OPEN"}]) == 2


def test_the_words_of_a_case_accuse_nobody():
    assert case_files.CONNECTIONS == ("REPORTED_IT", "WITNESS", "AFFECTED", "NAMED", "OTHER")
    said = " ".join([*case_files.CONNECTION_LABEL.values(), *case_files.ENTRY_WORDS.values(), *case_files.STATUS_LABEL.values(),
                     *case_files.CATEGORY_LABEL.values(), *case_files.LINK_LABEL.values(), case_files.PARTY_NOTE]).lower()
    assert not [word for word in ACCUSING if word in said]
    assert "Being named in a case is not an accusation." in case_files.PARTY_NOTE
    for names, labels in ((case_files.STATUSES, case_files.STATUS_LABEL), (case_files.CATEGORIES, case_files.CATEGORY_LABEL),
                          (case_files.LINK_KINDS, case_files.LINK_LABEL), (case_files.LINK_KINDS, case_files.LINK_NEEDS),
                          (case_files.CONNECTIONS, case_files.CONNECTION_LABEL), (case_files.ENTRY_KINDS, case_files.ENTRY_WORDS)):
        assert set(names) == set(labels)
    # A case has no kind that is about a member of staff's conduct: that is not a security case.
    assert not [c for c in case_files.CATEGORIES if re.search(r"CONDUCT|DISCIPLIN|HR|STAFF", c)]
    code = Path(case_files.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", code), "the service only reads"


# ─── B. A case from opening to closing ───────────────────────────────────────

async def _records(w: dict) -> dict:
    """An incident at each site, and an investigation and an evidence package at site A."""
    t = w["tenant"]
    r = {k: uuid.uuid4() for k in ("cam", "far", "incident", "elsewhere", "investigation", "package", "other")}
    r["h_other"] = _auth(r["other"], t, OPERATOR)
    await _run([
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Gate A')", {"i": r["cam"], "t": t, "s": w["site_a"]}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Gate B')", {"i": r["far"], "t": t, "s": w["site_b"]}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status) VALUES (:i,:t,:c,'Forced gate','high','open')",
         {"i": r["incident"], "t": t, "c": r["cam"]}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status) VALUES (:i,:t,:c,'Fence cut','medium','open')",
         {"i": r["elsewhere"], "t": t, "c": r["far"]}),
        ("INSERT INTO investigations (id, tenant_id, site_id, investigation_number, title, reason) "
         "VALUES (:i,:t,:s,'INV-0001','Who forced the gate','Reported by the client')", {"i": r["investigation"], "t": t, "s": w["site_a"]}),
        ("INSERT INTO evidence_packages (id, tenant_id, site_id, package_number, title, purpose) "
         "VALUES (:i,:t,:s,'EP-0001','Forced gate, 7 October','For the insurer')", {"i": r["package"], "t": t, "s": w["site_a"]}),
        # A second operator: holds case:work, and is on no case until put on one.
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) VALUES (:i,:t,4,:e,'x','Other Operator')",
         {"i": r["other"], "t": t, "e": f"other-{r['other'].hex[:8]}@drone.test"}),
    ])
    return r


async def test_a_case_is_opened_worked_closed_by_two_and_reopened():
    w = await _world()
    r = await _records(w)
    op, sup, man, adm = w["h"][OPERATOR], w["h"][SUPERVISOR], w["h"][MANAGER], w["h"][ADMIN]
    async with _client() as c:
        # Opened from an incident: that incident is its first link, and its site is the case's.
        opened = await c.post(BASE, headers=op, json={"title": "  Forced gate, Factory A ", "summary": "The north gate was forced on 7 October.",
                                                      "category": "TRESPASS", "from_kind": "INCIDENT", "from_id": str(r["incident"])})
        assert opened.status_code == 201, opened.text
        case = opened.json()
        cid, url = case["id"], f"{BASE}/{opened.json()['id']}"
        assert (case["case_number"], case["title"], case["status"], case["status_label"]) == ("CASE-0001", "Forced gate, Factory A", "OPEN", "Open")
        assert case["site"]["name"] == "Factory A" and case["category_label"] == "Trespass" and case["priority"] == "NORMAL"
        assert (case["lead_name"], case["opened_by_name"], case["on_case"]) == ("Role 4 User", "Role 4 User", True)
        (link,) = case["links"]
        assert (link["kind"], link["state"], link["label"], link["detail"], link["ref_id"]) == (
            "INCIDENT", "SHOWN", "Forced gate", "high, open", str(r["incident"]))
        assert link["note"] == "What this case was opened from."
        assert [(e["kind"], e["actor_name"], e["words"]) for e in case["entries"]] == [("OPENED", "Role 4 User", "Opened the case")]
        assert case["may"] == {"work": True, "assign": False, "request_close": True, "approve_close": False,
                               "decline_close": False, "reopen": False}
        assert case["party_note"] == case_files.PARTY_NOTE and case["two_people_note"] == case_files.TWO_PEOPLE

        for body, status, words in (
            ({"title": "  ", "summary": "x"}, 422, "Give the case a title"), ({"title": "x", "summary": " "}, 422, "Say what the case is about"),
            ({"title": "x"}, 422, None), ({"title": "x", "summary": "y", "category": "CONDUCT"}, 422, None),
            ({"title": "x", "summary": "y", "from_kind": "INCIDENT"}, 422, "both what kind"),
            ({"title": "x", "summary": "y", "from_kind": "INCIDENT", "from_id": str(uuid.uuid4())}, 404, "Incident not found"),
            ({"title": "x", "summary": "y", "site_id": str(uuid.uuid4())}, 404, "Site not found"),
            ({"title": "x", "summary": "y", "status": "CLOSED"}, 422, None),
            # Naming somebody else to lead is for whoever manages cases.
            ({"title": "x", "summary": "y", "lead_user_id": str(w["users"][SUPERVISOR])}, 403, "for whoever manages cases"),
        ):
            got = await c.post(BASE, headers=op, json=body)
            assert got.status_code == status, (body, got.text)
            if words:
                assert words in str(got.json()["detail"]), (body, got.text)
        # Somebody who may not work on cases cannot be made its lead.
        got = await c.post(BASE, headers=man, json={"title": "x", "summary": "y", "lead_user_id": str(w["users"][VIEWER])})
        assert got.status_code == 422 and "cannot be put on a case" in got.json()["detail"]
        for role in (VIEWER, GUARD):
            assert (await c.post(BASE, headers=w["h"][role], json={"title": "x", "summary": "y"})).status_code == 403

        # Notes, tasks, names: by whoever is on the case, or manages cases — not by somebody who is on no case.
        assert (await c.post(f"{url}/notes", headers=op, json={"body": "  CCTV shows the gate forced at 02:14.  "})).status_code == 201
        refused = await c.post(f"{url}/notes", headers=r["h_other"], json={"body": "Mine"})
        assert refused.status_code == 403 and refused.json()["detail"] == case_files.NOT_YOURS
        assert (await c.post(f"{url}/notes", headers=w["h"][VIEWER], json={"body": "Mine"})).status_code == 403
        assert (await c.post(f"{url}/notes", headers=op, json={"body": "   "})).status_code == 422
        task = await c.post(f"{url}/tasks", headers=op, json={"title": "Ask the night guard what they saw",
                                                             "assigned_to_user_id": str(r["other"])})
        assert task.status_code == 201, task.text
        (first,) = task.json()["tasks"]
        assert (first["state"], first["assigned_to_name"], first["created_by_name"], first["may_finish"]) == ("OPEN", "Other Operator", "Role 4 User", True)
        assert (await c.post(f"{url}/tasks", headers=op, json={"title": "x", "assigned_to_user_id": str(w["users"][VIEWER])})).status_code == 422
        named = await c.post(f"{url}/parties", headers=man, json={"kind": "PERSON", "label": " Tan Wei ", "connection": "WITNESS",
                                                                 "note": "Was on the night shift."})
        assert named.status_code == 201 and named.json()["parties"][0]["connection_label"] == "Saw or heard it"
        plate = await c.post(f"{url}/parties", headers=op, json={"kind": "VEHICLE", "label": "SGX1234A", "connection": "NAMED"})
        assert [p["label"] for p in plate.json()["parties"]] == ["Tan Wei", "SGX1234A"]
        # There is no way to record anybody as a suspect.
        for connection in ("SUSPECT", "OFFENDER", "ACCUSED"):
            assert (await c.post(f"{url}/parties", headers=op, json={"kind": "PERSON", "label": "X", "connection": connection})).status_code == 422

        # Somebody given a task finishes it, though they are not on the case — and can do nothing else to it.
        tid = first["id"]
        seen = (await c.get(url, headers=r["h_other"])).json()
        assert seen["on_case"] is False and seen["may"]["work"] is False and seen["tasks"][0]["may_finish"] is True
        assert (await c.post(f"{url}/tasks/{tid}/drop", headers=r["h_other"], json={})).status_code == 422, "dropping says why"
        done = await c.post(f"{url}/tasks/{tid}/done", headers=r["h_other"], json={"note": "Saw a van leave at 02:20."})
        assert done.status_code == 200 and (done.json()["tasks"][0]["state"], done.json()["tasks"][0]["done_by_name"]) == ("DONE", "Other Operator")
        assert (await c.post(f"{url}/tasks/{tid}/done", headers=op, json={})).status_code == 409
        assert (await c.post(f"{url}/tasks/{uuid.uuid4()}/done", headers=op, json={})).status_code == 404
        assert (await c.post(f"{url}/notes", headers=r["h_other"], json={"body": "Still not on it"})).status_code == 403

        # Links: by reference, to records the linker may read.
        for kind, ref in (("INVESTIGATION", r["investigation"]), ("EVIDENCE_PACKAGE", r["package"])):
            assert (await c.post(f"{url}/links", headers=op, json={"kind": kind, "ref_id": str(ref)})).status_code == 201
        again = await c.post(f"{url}/links", headers=op, json={"kind": "INVESTIGATION", "ref_id": str(r["investigation"])})
        assert again.status_code == 409 and "linked to the case already" in again.json()["detail"]
        assert (await c.post(f"{url}/links", headers=op, json={"kind": "INVESTIGATION", "ref_id": str(uuid.uuid4())})).status_code == 404
        assert (await c.post(f"{url}/links", headers=op, json={"kind": "ALERT", "ref_id": str(uuid.uuid4())})).status_code == 422
        links = (await c.get(url, headers=op)).json()["links"]
        assert [(x["kind_label"], x["label"], x["state"]) for x in links] == [
            ("Incident", "Forced gate", "SHOWN"), ("Investigation", "INV-0001 — Who forced the gate", "SHOWN"),
            ("Evidence package", "EP-0001 — Forced gate, 7 October", "SHOWN")]
        # Taken off with why; the record it referred to is untouched, and may be linked again.
        package = links[2]["id"]
        assert (await c.post(f"{url}/links/{package}/remove", headers=op, json={"reason": " "})).status_code == 422
        off = await c.post(f"{url}/links/{package}/remove", headers=op, json={"reason": "Wrong package."})
        assert off.status_code == 200 and len(off.json()["links"]) == 2
        assert (await c.post(f"{url}/links/{package}/remove", headers=op, json={"reason": "Twice"})).status_code == 404
        assert (await c.post(f"{url}/links", headers=op, json={"kind": "EVIDENCE_PACKAGE", "ref_id": str(r["package"])})).status_code == 201
        van = plate.json()["parties"][1]["id"]
        gone = await c.post(f"{url}/parties/{van}/remove", headers=op, json={"reason": "The plate was misread."})
        assert [p["label"] for p in gone.json()["parties"]] == ["Tan Wei"]

        # Who is on it is for whoever manages cases to say.
        assert (await c.post(f"{url}/investigators", headers=op, json={"user_id": str(r["other"])})).status_code == 403
        on = await c.post(f"{url}/investigators", headers=man, json={"user_id": str(r["other"])})
        assert on.status_code == 201 and [i["name"] for i in on.json()["investigators"]] == ["Other Operator"]
        assert (await c.post(f"{url}/investigators", headers=man, json={"user_id": str(r["other"])})).status_code == 409
        assert (await c.post(f"{url}/investigators", headers=man, json={"user_id": str(w["users"][VIEWER])})).status_code == 422
        assert (await c.post(f"{url}/notes", headers=r["h_other"], json={"body": "Now I am on it."})).status_code == 201
        off = await c.post(f"{url}/investigators/{r['other']}/remove", headers=man)
        assert off.status_code == 200 and off.json()["investigators"] == []
        assert (await c.post(f"{url}/investigators/{r['other']}/remove", headers=man)).status_code == 404
        assert (await c.post(f"{url}/notes", headers=r["h_other"], json={"body": "And off again."})).status_code == 403
        led = await c.put(f"{url}/lead", headers=man, json={"user_id": str(w["users"][SUPERVISOR])})
        assert led.status_code == 200 and led.json()["lead_name"] == "Role 3 User"
        assert (await c.post(f"{url}/notes", headers=op, json={"body": "I no longer lead it."})).status_code == 403
        changed = await c.patch(url, headers=sup, json={"priority": "HIGH", "title": "Forced north gate, Factory A"})
        assert (changed.json()["priority"], changed.json()["title"]) == ("HIGH", "Forced north gate, Factory A")
        assert (await c.patch(url, headers=sup, json={"case_number": "CASE-9999"})).status_code == 422
        assert (await c.patch(url, headers=sup, json={"title": "  "})).status_code == 422

        # Closing: every task finished first; whoever asks says what was found; somebody else approves.
        late = (await c.post(f"{url}/tasks", headers=sup, json={"title": "Check the fence"})).json()["tasks"][-1]["id"]
        blocked = await c.post(f"{url}/request-close", headers=sup, json={"outcome": "Gate repaired."})
        assert blocked.status_code == 409 and blocked.json()["detail"] == "1 task is still open. Finish or drop it first."
        assert (await c.post(f"{url}/tasks/{late}/drop", headers=sup, json={"note": "The fence is the landlord's."})).status_code == 200
        assert (await c.post(f"{url}/request-close", headers=sup, json={"outcome": "  "})).status_code == 422
        asked = await c.post(f"{url}/request-close", headers=sup, json={"outcome": "The gate was forced by a delivery van. Gate repaired."})
        assert asked.status_code == 200, asked.text
        assert (asked.json()["status"], asked.json()["status_label"], asked.json()["close_requested_by_name"]) == (
            "AWAITING_APPROVAL", "Waiting for approval to close", "Role 3 User")
        assert asked.json()["may"] == {"work": False, "assign": False, "request_close": False, "approve_close": False,
                                       "decline_close": True, "reopen": False}
        # It is with the approver: nothing is added to it, and whoever asked does not approve.
        for method, path, body in (("post", "/notes", {"body": "One more thing"}), ("post", "/tasks", {"title": "x"}),
                                   ("patch", "", {"priority": "LOW"}), ("post", "/request-close", {"outcome": "Again"}),
                                   ("put", "/lead", {"user_id": str(w["users"][OPERATOR])})):
            got = await getattr(c, method)(url + path, headers=sup, json=body)
            assert got.status_code == 409 and got.json()["detail"] == api.CLOSED, (path, got.text)
        own = await c.post(f"{url}/approve-close", headers=sup)
        assert own.status_code == 409 and own.json()["detail"] == case_files.TWO_PEOPLE
        assert (await c.post(f"{url}/approve-close", headers=op)).status_code == 403
        assert (await c.post(f"{url}/decline-close", headers=man, json={"reason": " "})).status_code == 422
        back = await c.post(f"{url}/decline-close", headers=man, json={"reason": "Say whether the van was identified."})
        assert (back.json()["status"], back.json()["outcome"], back.json()["close_requested_by_name"]) == ("OPEN", None, None)
        assert (await c.post(f"{url}/approve-close", headers=man)).status_code == 409, "there is nothing to approve"
        await c.post(f"{url}/request-close", headers=sup, json={"outcome": "Forced by a delivery van, SGX1234A. Gate repaired."})
        closed = await c.post(f"{url}/approve-close", headers=adm)
        assert closed.status_code == 200, closed.text
        done = closed.json()
        assert (done["status"], done["closed_by_name"], done["close_requested_by_name"]) == ("CLOSED", "Role 2 User", "Role 3 User")
        assert done["outcome"] == "Forced by a delivery van, SGX1234A. Gate repaired." and done["closed_at"]
        assert done["may"] == {"work": False, "assign": False, "request_close": False, "approve_close": False,
                               "decline_close": False, "reopen": True}

        # A closed case is not changed.
        for method, path, body in (("post", "/notes", {"body": "Afterthought"}), ("post", "/parties", {"kind": "PERSON", "label": "X", "connection": "NAMED"}),
                                   ("post", "/links", {"kind": "INCIDENT", "ref_id": str(r["elsewhere"])}), ("patch", "", {"title": "Rewritten"}),
                                   ("post", f"/links/{links[0]['id']}/remove", {"reason": "x"}), ("post", "/investigators", {"user_id": str(r["other"])})):
            got = await getattr(c, method)(url + path, headers=man, json=body)
            assert got.status_code == 409, (path, got.text)
        assert (await c.post(f"{url}/approve-close", headers=man)).status_code == 409
        assert (await c.get(url, headers=man)).json()["title"] == "Forced north gate, Factory A"

        # Its report: the whole of it, in order — as a reading and as a file.
        report = await c.get(f"{url}/report", headers=w["h"][VIEWER])
        assert report.status_code == 200 and report.json()["made_by"] == "Role 6 User" and report.json()["case"]["case_number"] == "CASE-0001"
        assert [e["kind"] for e in report.json()["case"]["entries"]] == [
            "OPENED", "NOTE", "INVESTIGATOR_ADDED", "NOTE", "INVESTIGATOR_REMOVED", "LEAD_SET", "CLOSE_REQUESTED", "CLOSE_DECLINED",
            "CLOSE_REQUESTED", "CLOSE_APPROVED"]
        assert report.json()["case"]["entries"][7]["body"] == "Say whether the van was identified."
        pdf = await c.get(f"{url}/report.pdf", headers=man)
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
        assert pdf.headers["content-disposition"] == 'attachment; filename="CASE-0001.pdf"' and len(pdf.content) > 2000

        # Reopened by whoever manages cases, with why; what it was closed with stays in its history.
        assert (await c.post(f"{url}/reopen", headers=op, json={"reason": "x"})).status_code == 403
        assert (await c.post(f"{url}/reopen", headers=man, json={"reason": "  "})).status_code == 422
        again = await c.post(f"{url}/reopen", headers=man, json={"reason": "The insurer asks who drove the van."})
        assert (again.json()["status"], again.json()["outcome"], again.json()["closed_at"]) == ("OPEN", None, None)
        assert again.json()["entries"][-1]["kind"] == "REOPENED" and again.json()["entries"][-2]["kind"] == "CLOSE_APPROVED"
        assert again.json()["entries"][-3]["body"] == "Forced by a delivery van, SGX1234A. Gate repaired."
        assert (await c.post(f"{url}/notes", headers=sup, json={"body": "Asked the haulier."})).status_code == 201
        assert (await c.post(f"{url}/reopen", headers=man, json={"reason": "Twice"})).status_code == 409

        # The next case takes the next number.
        nxt = await c.post(BASE, headers=man, json={"title": "Lost keys", "summary": "A set of keys is missing.", "category": "THEFT"})
        assert (nxt.json()["case_number"], nxt.json()["site"], nxt.json()["lead_name"]) == ("CASE-0002", None, "Role 8 User")

    # A step in a case is a person's.
    for token, words in ((TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True),
                          "not by an API key"),):
        app.dependency_overrides[get_token_payload] = lambda token=token: token
        try:
            async with _client() as c:
                for method, path, body in (("post", BASE, {"title": "x", "summary": "y"}), ("post", f"{url}/notes", {"body": "From a key"}),
                                           ("post", f"{url}/request-close", {"outcome": "x"})):
                    got = await getattr(c, method)(path, json=body)
                    assert got.status_code == 403 and words in got.json()["detail"], (path, got.text)
                assert (await c.get(url)).status_code == 200
        finally:
            app.dependency_overrides.pop(get_token_payload, None)

    # Every step is audited, and none of it touched what the case refers to or told anybody.
    acts = {action: len(await _audit(w, action)) for action in (
        "case.open", "case.note", "case.task.add", "case.task.done", "case.task.drop", "case.party.add", "case.party.remove",
        "case.link.add", "case.link.remove", "case.investigator.add", "case.investigator.remove", "case.lead", "case.update",
        "case.close.request", "case.close.approve", "case.close.decline", "case.reopen", "case.report")}
    assert acts == {"case.open": 2, "case.note": 3, "case.task.add": 2, "case.task.done": 1, "case.task.drop": 1,
                    "case.party.add": 2, "case.party.remove": 1, "case.link.add": 3, "case.link.remove": 1,
                    "case.investigator.add": 1, "case.investigator.remove": 1, "case.lead": 1, "case.update": 1,
                    "case.close.request": 2, "case.close.approve": 1, "case.close.decline": 1, "case.reopen": 1, "case.report": 2}
    (rows,) = await _sql("SELECT (SELECT status FROM incidents WHERE id = :i) AS incident, "
                         "(SELECT status FROM investigations WHERE id = :n) AS investigation, "
                         "(SELECT status FROM evidence_packages WHERE id = :p) AS package, "
                         "(SELECT count(*) FROM alerts WHERE tenant_id = :t) AS alerts, "
                         "(SELECT count(*) FROM case_links WHERE case_id = :c) AS links",
                         {"i": r["incident"], "n": r["investigation"], "p": r["package"], "t": w["tenant"], "c": cid})
    assert (rows["incident"], rows["investigation"], rows["package"], rows["alerts"]) == ("open", "OPEN", "DRAFT", 0)
    assert rows["links"] == 4, "a link that was taken off is still a row"
    router = Path(api.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    for word in ("redis", "response_notify", "send_expo_push", "smtp", "webhook"):
        assert word not in router.lower(), word
    written = set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_{}]+)", router))
    assert written <= {"case_files", "case_investigators", "case_tasks", "case_entries", "case_links", "case_parties", "{table}"}
    assert "DELETE FROM" not in router


# ─── C. Whose case somebody may read ─────────────────────────────────────────

async def test_who_may_read_which_case_and_what_of_its_links():
    w, other = await _world(), await _world()
    r = await _records(w)
    async with _client() as c:
        at_a = (await c.post(BASE, headers=w["h"][MANAGER], json={"title": "Gate", "summary": "s", "from_kind": "INCIDENT",
                                                                  "from_id": str(r["incident"])})).json()
        at_b = (await c.post(BASE, headers=w["h"][MANAGER], json={"title": "Fence", "summary": "s", "from_kind": "INCIDENT",
                                                                  "from_id": str(r["elsewhere"])})).json()
        nowhere = (await c.post(BASE, headers=w["h"][MANAGER], json={"title": "Keys", "summary": "s"})).json()
        assert (at_a["site"]["name"], at_b["site"]["name"], nowhere["site"]) == ("Factory A", "Factory B", None)

        # Somebody held to site A reads the case at site A — not the one at site B, nor the one at no one site.
        mine = (await c.get(BASE, headers=w["h"][SUPERVISOR])).json()
        assert [x["case_number"] for x in mine["items"]] == ["CASE-0001"] and mine["total"] == 1 and mine["can_manage"] is True
        for hidden in (at_b, nowhere):
            assert (await c.get(f"{BASE}/{hidden['id']}", headers=w["h"][SUPERVISOR])).status_code == 404
            assert (await c.post(f"{BASE}/{hidden['id']}/notes", headers=w["h"][SUPERVISOR], json={"body": "x"})).status_code == 404
            assert (await c.get(f"{BASE}/{hidden['id']}/report.pdf", headers=w["h"][SUPERVISOR])).status_code == 404
        # They open a case at their site, not one that spans every site, nor one from a record they are not shown.
        got = await c.post(BASE, headers=w["h"][SUPERVISOR], json={"title": "x", "summary": "y"})
        assert got.status_code == 422 and "Choose the site this case is about" in got.json()["detail"]
        got = await c.post(BASE, headers=w["h"][SUPERVISOR], json={"title": "x", "summary": "y", "from_kind": "INCIDENT",
                                                                   "from_id": str(r["elsewhere"])})
        assert got.status_code == 404 and got.json()["detail"] == "Incident not found"
        assert (await c.post(BASE, headers=w["h"][SUPERVISOR], json={"title": "x", "summary": "y", "site_id": str(w["site_b"])})).status_code == 404
        assert (await c.post(f"{BASE}/{at_a['id']}/links", headers=w["h"][SUPERVISOR],
                             json={"kind": "INCIDENT", "ref_id": str(r["elsewhere"])})).status_code == 404

        # The list: every case for somebody who is not held, newest first; narrowed by status, site and "mine".
        every = (await c.get(BASE, headers=w["h"][VIEWER])).json()
        assert [x["case_number"] for x in every["items"]] == ["CASE-0003", "CASE-0002", "CASE-0001"]
        assert (every["can_open"], every["can_manage"], every["items"][2]["links"], every["items"][0]["tasks_open"]) == (False, False, 1, 0)
        assert [x["case_number"] for x in (await c.get(BASE, headers=w["h"][ADMIN], params={"site_id": str(w["site_b"])})).json()["items"]] == ["CASE-0002"]
        assert (await c.get(BASE, headers=w["h"][ADMIN], params={"status": "CLOSED"})).json()["items"] == []
        assert (await c.get(BASE, headers=w["h"][ADMIN], params={"status": "LOST"})).status_code == 422
        assert (await c.get(BASE, headers=w["h"][OPERATOR], params={"mine": True})).json()["items"] == []
        await c.post(f"{BASE}/{at_b['id']}/tasks", headers=w["h"][MANAGER], json={"title": "Walk the fence", "assigned_to_user_id": str(w["users"][OPERATOR])})
        assert [x["case_number"] for x in (await c.get(BASE, headers=w["h"][OPERATOR], params={"mine": True})).json()["items"]] == ["CASE-0002"]
        assert len((await c.get(BASE, headers=w["h"][MANAGER], params={"mine": True})).json()["items"]) == 3

        # What may be chosen: people who may work on cases, and the recent records the caller may read.
        options = (await c.get(f"{BASE}/options", headers=w["h"][SUPERVISOR])).json()
        assert [k["key"] for k in options["categories"]] == list(case_files.CATEGORIES)
        assert [k["key"] for k in options["connections"]] == list(case_files.CONNECTIONS) and options["party_note"] == case_files.PARTY_NOTE
        assert "Role 6 User" not in [p["name"] for p in options["people"]] and "Role 4 User" in [p["name"] for p in options["people"]]
        assert [x["label"] for x in options["recent"]["INCIDENT"]] == ["Forced gate"], "the incident at site B is not theirs to link"
        assert [x["label"] for x in options["recent"]["INVESTIGATION"]] == ["INV-0001 — Who forced the gate"]
        assert len((await c.get(f"{BASE}/options", headers=w["h"][ADMIN])).json()["recent"]["INCIDENT"]) == 2

        # Who may read cases at all, and another organisation.
        for path in ("", f"/{at_a['id']}", "/options", f"/{at_a['id']}/report"):
            assert (await c.get(BASE + path, headers=w["h"][GUARD])).status_code == 403
        assert (await c.get(BASE)).status_code in (401, 403)
        assert (await c.get(BASE, headers=other["h"][ADMIN])).json()["items"] == []
        assert (await c.get(f"{BASE}/{at_a['id']}", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.post(f"{BASE}/{at_a['id']}/reopen", headers=other["h"][ADMIN], json={"reason": "x"})).status_code == 404
        theirs = await c.post(BASE, headers=other["h"][ADMIN], json={"title": "Theirs", "summary": "s"})
        assert theirs.json()["case_number"] == "CASE-0001", "each organisation numbers its own"

    # A link to a record the reader may not read says that it is one, and what kind, and nothing else.
    links = [{"id": uuid.uuid4(), "kind": "INCIDENT", "ref_id": r["incident"], "note": None},
             {"id": uuid.uuid4(), "kind": "INVESTIGATION", "ref_id": r["investigation"], "note": None},
             {"id": uuid.uuid4(), "kind": "INCIDENT", "ref_id": r["elsewhere"], "note": None}]
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        seen = await case_files.linked(db, links, frozenset({"incident:read"}), [str(w["site_a"])])
        await db.rollback()
    assert [(x["state"], x["label"], x["ref_id"]) for x in seen] == [
        ("SHOWN", "Forced gate", r["incident"]), ("NOT_PERMITTED", None, None), ("NOT_AVAILABLE", None, None)]
    assert seen[1]["kind_label"] == "Investigation" and seen[1]["needs"] == "investigation:read" and seen[1]["detail"] is None


# ─── D. What the application role and the database refuse ────────────────────

async def test_what_the_application_role_and_the_database_refuse_of_a_case():
    w, other = await _world(), await _world()
    r = await _records(w)
    async with _client() as c:
        case = (await c.post(BASE, headers=w["h"][SUPERVISOR], json={"title": "Gate", "summary": "s", "site_id": str(w["site_a"])})).json()
        url = f"{BASE}/{case['id']}"
        await c.post(f"{url}/notes", headers=w["h"][SUPERVISOR], json={"body": "A note."})
        await c.post(f"{url}/request-close", headers=w["h"][SUPERVISOR], json={"outcome": "Found."})
        assert (await c.post(f"{url}/approve-close", headers=w["h"][MANAGER])).status_code == 200
        open_case = (await c.post(BASE, headers=w["h"][SUPERVISOR], json={"title": "Fence", "summary": "s", "site_id": str(w["site_a"])})).json()
    cid, oid = case["id"], open_case["id"]

    async def as_app(statement: str, params: dict, tenant=None) -> list:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant or w["tenant"])})
            assert not (await db.execute(text(
                "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
            try:
                result = await db.execute(text(statement), params)
                return [dict(x) for x in result.mappings()] if result.returns_rows else []
            finally:
                await db.rollback()

    # The application's role removes nothing, rewrites no history, and cannot renumber a case or move it.
    for statement in ("DELETE FROM case_files WHERE id = :c", "DELETE FROM case_entries WHERE case_id = :c",
                      "UPDATE case_entries SET body = 'Rewritten' WHERE case_id = :c", "DELETE FROM case_links WHERE case_id = :c",
                      "UPDATE case_files SET case_number = 'CASE-9999' WHERE id = :c", "UPDATE case_files SET opened_at = now() WHERE id = :c",
                      "UPDATE case_files SET site_id = NULL WHERE id = :c", "UPDATE case_tasks SET title = 'x' WHERE case_id = :c",
                      "UPDATE case_parties SET label = 'x' WHERE case_id = :c", "UPDATE case_links SET ref_id = gen_random_uuid() WHERE case_id = :c"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await as_app(statement, {"c": cid})
    # A closed case is not changed, and nothing is added to it — whoever tries.
    with pytest.raises(DBAPIError, match="a closed case is not changed"):
        await as_app("UPDATE case_files SET title = 'Rewritten' WHERE id = :c", {"c": cid})
    for statement in ("INSERT INTO case_tasks (tenant_id, case_id, title) VALUES (:t, :c, 'Late task')",
                      "INSERT INTO case_parties (tenant_id, case_id, kind, label, connection) VALUES (:t, :c, 'PERSON', 'X', 'NAMED')",
                      "INSERT INTO case_links (tenant_id, case_id, kind, ref_id) VALUES (:t, :c, 'INCIDENT', gen_random_uuid())",
                      "INSERT INTO case_investigators (tenant_id, case_id, user_id) VALUES (:t, :c, :u)",
                      "INSERT INTO case_entries (tenant_id, case_id, kind, body) VALUES (:t, :c, 'NOTE', 'Afterthought')"):
        with pytest.raises(DBAPIError, match="the case is closed"):
            await as_app(statement, {"t": w["tenant"], "c": cid, "u": w["users"][OPERATOR]})
    # The same into an open case is the application's to do.
    assert await as_app("INSERT INTO case_entries (tenant_id, case_id, kind, body) VALUES (:t, :c, 'NOTE', 'Fine') RETURNING kind",
                        {"t": w["tenant"], "c": oid}) == [{"kind": "NOTE"}]
    # Another organisation's session reads and changes none of it.
    for table in ("case_files", "case_entries", "case_tasks", "case_links", "case_parties", "case_investigators"):
        assert await as_app(f"SELECT 1 AS x FROM {table}", {}, other["tenant"]) == [], table
    assert await as_app("UPDATE case_files SET title = 'x' WHERE id = :c RETURNING id", {"c": oid}, other["tenant"]) == []

    grants = await _sql("SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                        "WHERE grantee = 'svc_app' AND table_name LIKE 'case\\_%'")
    by_table: dict[str, set] = {}
    for g in grants:
        by_table.setdefault(g["table_name"], set()).add(g["privilege_type"])
    assert by_table == {t: {"SELECT", "INSERT"} for t in ("case_files", "case_investigators", "case_tasks", "case_entries",
                                                         "case_links", "case_parties")}
    columns = await _sql("SELECT table_name, column_name FROM information_schema.column_privileges "
                         "WHERE grantee = 'svc_app' AND table_name LIKE 'case\\_%' AND privilege_type = 'UPDATE'")
    may_change: dict[str, set] = {}
    for col in columns:
        may_change.setdefault(col["table_name"], set()).add(col["column_name"])
    assert "case_entries" not in may_change, "a case's history is added to and never rewritten"
    assert may_change["case_links"] == {"removed_at", "removed_by_user_id", "remove_reason"} == may_change["case_parties"]
    assert may_change["case_tasks"] == {"state", "done_at", "done_by_user_id", "done_note", "dropped_reason", "updated_at"}
    for held in ("case_number", "site_id", "opened_at", "opened_by_user_id", "tenant_id"):
        assert held not in may_change["case_files"], held
    rls = await _sql("SELECT relname, relrowsecurity AND relforcerowsecurity AS forced FROM pg_class WHERE relname LIKE 'case\\_%' AND relkind = 'r'")
    assert len(rls) == 6 and all(x["forced"] for x in rls)

    # What the database itself refuses.
    row = ("INSERT INTO case_files (tenant_id, case_number, title, summary, status, category, outcome, close_requested_at, "
           "close_requested_by_user_id, closed_at, closed_by_user_id) VALUES (:t,:n,:title,'s',:st,:cat,:out,{asked},:by,{closed},:who)")
    base = {"t": w["tenant"], "n": "CASE-0900", "title": "T", "st": "OPEN", "cat": "OTHER", "out": None, "by": None, "who": None}
    for change, sql, constraint in (
        ({"st": "LOST", "out": "Found."}, {"asked": "now()"}, "ck_case_status"), ({"cat": "CONDUCT"}, {}, "ck_case_category"),
        ({"title": "  "}, {}, "ck_case_title"),
        # Waiting for approval, or closed, says what was found; closed has when.
        ({"st": "AWAITING_APPROVAL"}, {}, "ck_case_asked"), ({"st": "AWAITING_APPROVAL", "out": "Found."}, {}, "ck_case_asked"),
        ({"st": "CLOSED", "out": "Found."}, {"asked": "now()"}, "ck_case_closed"),
        ({"out": None}, {"closed": "now()"}, "ck_case_closed"),
        # Closing takes two people.
        ({"st": "CLOSED", "out": "Found.", "by": w["users"][MANAGER], "who": w["users"][MANAGER]}, {"asked": "now()", "closed": "now()"}, "ck_case_two"),
        ({"n": "CASE-0001"}, {}, "uq_case_number"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            await _sql(row.format(**{"asked": "NULL", "closed": "NULL", **sql}), {**base, **change})
    await _sql(row.format(asked="now()", closed="now()"), {**base, "st": "CLOSED", "out": "Found.", "by": w["users"][SUPERVISOR],
                                                           "who": w["users"][MANAGER]})
    for statement, constraint in (
        ("INSERT INTO case_parties (tenant_id, case_id, kind, label, connection) VALUES (:t,:c,'PERSON','X','SUSPECT')", "ck_caseparty_connection"),
        ("INSERT INTO case_parties (tenant_id, case_id, kind, label, connection) VALUES (:t,:c,'ANIMAL','X','NAMED')", "ck_caseparty_kind"),
        ("INSERT INTO case_parties (tenant_id, case_id, kind, label, connection, removed_at) VALUES (:t,:c,'PERSON','X','NAMED', now())", "ck_caseparty_removed"),
        ("INSERT INTO case_tasks (tenant_id, case_id, title, state) VALUES (:t,:c,'T','DROPPED')", "ck_casetask_dropped"),
        ("INSERT INTO case_tasks (tenant_id, case_id, title, state) VALUES (:t,:c,'T','DONE')", "ck_casetask_done"),
        ("INSERT INTO case_links (tenant_id, case_id, kind, ref_id) VALUES (:t,:c,'ALERT', gen_random_uuid())", "ck_caselink_kind"),
        ("INSERT INTO case_links (tenant_id, case_id, kind, ref_id, removed_at) VALUES (:t,:c,'INCIDENT', gen_random_uuid(), now())", "ck_caselink_removed"),
        ("INSERT INTO case_entries (tenant_id, case_id, kind) VALUES (:t,:c,'NOTE')", "ck_caseentry_body"),
        ("INSERT INTO case_entries (tenant_id, case_id, kind) VALUES (:t,:c,'REOPENED')", "ck_caseentry_body"),
        ("INSERT INTO case_entries (tenant_id, case_id, kind, body) VALUES (:t,:c,'VERDICT','Guilty')", "ck_caseentry_kind"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            await _sql(statement, {"t": w["tenant"], "c": oid})
    await _sql("INSERT INTO case_investigators (tenant_id, case_id, user_id) VALUES (:t,:c,:u)", {"t": w["tenant"], "c": oid, "u": r["other"]})
    with pytest.raises(IntegrityError, match="uq_caseinv_on"):
        await _sql("INSERT INTO case_investigators (tenant_id, case_id, user_id) VALUES (:t,:c,:u)", {"t": w["tenant"], "c": oid, "u": r["other"]})


def _needs(route) -> set[str]:
    found: set[str] = set()

    def walk(dep):
        if "require_permission" in getattr(dep.call, "__qualname__", ""):
            found.update(c.cell_contents for c in (dep.call.__closure__ or ()) if isinstance(c.cell_contents, str))
        for sub in dep.dependencies:
            walk(sub)

    for d in route.dependant.dependencies:
        walk(d)
    return found


def test_every_route_asks_for_what_it_should():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if (path == BASE or path.startswith(BASE + "/")) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    shown = re.sub(r"\{([a-z]+)_id:uuid\}", lambda m: "{id}" if m.group(1) == "case" else "{" + m.group(1) + "}",
                                   path.removeprefix(BASE)) or "/"
                    served[(method, shown)] = _needs(route)
    read, work, manage = {"case:read"}, {"case:read", "case:work"}, {"case:read", "case:manage"}
    assert served == {
        ("GET", "/"): read, ("GET", "/options"): read, ("POST", "/"): work, ("GET", "/{id}"): read, ("PATCH", "/{id}"): work,
        ("PUT", "/{id}/lead"): manage, ("POST", "/{id}/investigators"): manage, ("POST", "/{id}/investigators/{user}/remove"): manage,
        ("POST", "/{id}/notes"): work, ("POST", "/{id}/tasks"): work, ("POST", "/{id}/tasks/{task}/done"): work,
        ("POST", "/{id}/tasks/{task}/drop"): work, ("POST", "/{id}/links"): work, ("POST", "/{id}/links/{link}/remove"): work,
        ("POST", "/{id}/parties"): work, ("POST", "/{id}/parties/{party}/remove"): work, ("POST", "/{id}/request-close"): work,
        ("POST", "/{id}/approve-close"): manage, ("POST", "/{id}/decline-close"): manage, ("POST", "/{id}/reopen"): manage,
        ("GET", "/{id}/report"): read, ("GET", "/{id}/report.pdf"): read}
    assert not [m for m, _ in served if m == "DELETE"], "nothing of a case is removed"


async def test_who_holds_the_three_permissions_and_what_this_phase_left_alone():
    rows = await _sql("SELECT p.code, p.category, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code LIKE 'case:%' GROUP BY p.code, p.category")
    assert {x["code"]: list(x["roles"]) for x in rows} == {"case:read": [2, 3, 4, 6, 8], "case:work": [2, 3, 4, 8], "case:manage": [2, 3, 8]}
    assert {x["category"] for x in rows} == {"cases"}
    known = {x["code"] for x in await _sql("SELECT code FROM permissions WHERE code = ANY(:c)", {"c": list(api.HELD)})}
    assert known == set(api.HELD)
    upgrade = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["case_files", "case_investigators", "case_tasks", "case_entries",
                                                          "case_links", "case_parties"]
    assert set(re.findall(r"ALTER TABLE (\S+)", upgrade)) == {"{table}"}, "no existing table is altered"
    assert not re.search(r"CREATE TABLE security_", upgrade) and "TRUNC" + "ATE" not in upgrade
    # A link is by reference: no foreign key reaches into the records a case is about.
    assert set(re.findall(r"REFERENCES (\w+)\(", upgrade)) == {"tenants", "sites", "users", "case_files"}
    (layer,) = await _sql("SELECT count(*) AS n FROM permissions WHERE category = 'security_intelligence'")
    assert layer["n"] == 7
    routers = Path(api.__file__).parent
    for name in ("incidents.py", "investigations.py", "evidence_packages.py"):
        source = (routers / name).read_text(encoding="utf-8")
        assert "case_files" not in source and "case_links" not in source, name
