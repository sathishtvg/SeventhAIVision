"""The occurrence book: searched, reviewed, corrected by a further entry — and what one shift hands the next.

  A — The summary's words, with nothing running: fixed sentences over counts
  B — The book, searched
  C — Review: by somebody other than who wrote it, and shown to those who keep the book
  D — Correction: a further entry, and the first stays as written
  E — Instructions in force at a site
  F — A shift's summary: drafted by the platform, confirmed by a person
  G — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced. Entries are written through the EXISTING endpoint, unchanged.

The claims, each with tests: an entry is never edited or removed; a review and
a correction are added to and never rewritten; what a reviewer wrote is not
shown to a client or a viewer; a summary says only what was recorded, is a
draft until a person confirms it, and cannot be changed after.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import occurrence_book as api
from app.routers.dob import VALID_ENTRY_TYPES
from app.services import shift_summary as summary
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql, _world
from tests.test_incident_responses import FakeRedis, _guard, told  # noqa: F401
from tests.test_investigation_search import _audit

BASE = "/api/v1/occurrence-book"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0147_occurrence_book_review.py").read_text(encoding="utf-8")
TABLES = ("occurrence_entry_reviews", "occurrence_entry_corrections", "site_instructions", "site_instruction_reads",
          "shift_handover_summaries")
CLIENT = 7


async def _write(c, w: dict, body: str, *, who: int = GUARD, kind: str = "general", site: str | None = "site_a",
                 severity: str | None = None, headers: dict | None = None, shift=None) -> dict:
    """An entry, through the endpoint that has always written them."""
    r = await c.post("/api/v1/dob", headers=headers or w["h"][who], json={
        "entry_type": kind, "body": body, "severity": severity, "site_id": str(w[site]) if site else None,
        "shift_id": str(shift) if shift else None})
    assert r.status_code == 200, r.text
    return r.json()


async def _backdate(entry_id, minutes: int) -> None:
    await _sql("UPDATE occurrence_book_entries SET occurred_at = now() - make_interval(mins => :m) WHERE id = :i",
               {"m": minutes, "i": entry_id})


async def _on_shift(w: dict, guard=None, *, site: str = "site_a", hours_ago: float = 3, ended: bool = False) -> uuid.UUID:
    sid, now = uuid.uuid4(), datetime.now(timezone.utc)
    await _sql("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
               "actual_end, status) VALUES (:i,:t,:s,:g,:a,:b,:a,:e,:st)",
               {"i": sid, "t": w["tenant"], "s": w[site] if site else None, "g": guard or w["users"][GUARD],
                "a": now - timedelta(hours=hours_ago), "b": now + timedelta(hours=5),
                "e": now if ended else None, "st": "completed" if ended else "active"})
    return sid


# ─── A. The summary's words ──────────────────────────────────────────────────

def _facts(**over) -> dict:
    base = {
        "shift": {"id": "s", "guard_name": "Tan Wei Ming", "site_id": "x", "site_name": "Factory A",
                  "start": "2026-10-06T23:00:00+00:00", "end": "2026-10-07T11:00:00+00:00", "ended": True,
                  "timezone": "Asia/Singapore"},
        "entries": {"total": 0, "by_type": {}, "of_note_total": 0, "of_note": []},
        "incidents": {"opened": 0, "resolved": 0, "open_now": 0, "still_open": []},
        "alerts": {"raised": 0, "open_now": 0, "by_severity": {}},
        "patrols": {"sessions": 0, "completed": 0, "checkpoints_scanned": 0, "checkpoints_total": 0},
        "dispatches": {"sent": 0, "arrived": 0, "declined": 0},
        "visitors": {"arrived": 0, "left": 0, "turned_away": 0},
        "site": {"keys_outstanding": 0, "keys_overdue": 0, "lost_found_held": 0, "equipment_out_count": 0,
                 "open_defects_count": 0},
        "instructions": [], "follow_ups": [],
    }
    return {**base, **over}


def test_a_shift_on_which_nothing_was_recorded_says_so_under_every_heading():
    assert summary.write(_facts()) == "\n".join([
        "Shift summary: Factory A, 7 Oct 2026, 07:00 to 19:00 — Tan Wei Ming",
        summary.PROVENANCE,
        "", "OCCURRENCE BOOK", "No entries were written.",
        "", "INCIDENTS", "0 opened and 0 resolved during the shift. None open at the site.",
        "", "ALERTS", "None raised.",
        "", "PATROLS", "No patrol was started on this shift.",
        "", "DISPATCHES", "Not sent to any incident.",
        "", "VISITORS", "None recorded.",
        "", "IN HAND AT THE SITE",
        "0 keys out. 0 items of lost property held. 0 items of kit signed out. 0 defects open.",
        "", "INSTRUCTIONS IN FORCE", "None.",
    ])
    # What is told to the person writing it is not left in what the next shift reads.
    assert "does not interpret" in summary.PROVENANCE and "confirm" not in summary.PROVENANCE
    assert summary.NOTE.startswith(summary.PROVENANCE) and summary.NOTE.endswith("before you confirm it.")


def test_a_summary_counts_and_quotes_and_says_no_more():
    words = summary.write(_facts(
        entries={"total": 14, "by_type": {"general": 8, "visitor_arrival": 3, "delivery": 2, "unusual_activity": 1},
                 "of_note_total": 4, "of_note": [
                     {"at": "2026-10-07T06:05:00+00:00", "type": "unusual_activity", "severity": "high",
                      "body": "Van parked at the east fence for twenty minutes.", "author_name": "Tan Wei Ming"},
                     {"at": "2026-10-07T08:30:00+00:00", "type": "incident", "severity": None,
                      "body": "Padlock found cut.", "author_name": "Tan Wei Ming"}]},
        incidents={"opened": 2, "resolved": 1, "open_now": 3, "still_open": [
            {"title": "Forced gate", "severity": "high", "status": "on_scene"}]},
        alerts={"raised": 12, "open_now": 2, "by_severity": {"medium": 8, "critical": 1, "high": 3}},
        patrols={"sessions": 4, "completed": 3, "checkpoints_scanned": 22, "checkpoints_total": 26},
        dispatches={"sent": 2, "arrived": 1, "declined": 1},
        visitors={"arrived": 6, "left": 5, "turned_away": 1},
        site={"keys_outstanding": 2, "keys_overdue": 1, "lost_found_held": 1, "equipment_out_count": 0,
              "open_defects_count": 3},
        instructions=[{"body": "Gate 3 stays locked until Friday.", "issued_by_name": "Lim Mei Ling",
                       "issued_at": "2026-10-05T02:00:00+00:00", "expires_at": "2026-10-09T10:00:00+00:00"},
                      {"body": "Call the duty manager before letting a contractor on the roof.", "issued_by_name": None,
                       "issued_at": "2026-10-01T02:00:00+00:00", "expires_at": None}],
        follow_ups=[{"at": "2026-10-06T14:10:00+00:00", "type": "general", "body": "Fence light out by bay 4.",
                     "note": "Raise a defect and check it is fixed."}],
    )).split("\n")
    assert words[3:] == [
        "OCCURRENCE BOOK",
        "14 entries: general 8, visitor arrival 3, delivery 2, unusual activity 1.",
        "- 14:05 Unusual activity (high): Van parked at the east fence for twenty minutes.",
        "- 16:30 Incident: Padlock found cut.",
        "…and 2 more of note, in the book.",
        "", "INCIDENTS", "2 opened and 1 resolved during the shift. 3 open at the site now:",
        "- Forced gate (high, on scene)", "…and 2 more.",
        "", "ALERTS", "12 raised: 1 critical, 3 high, 8 medium. 2 of them still open.",
        "", "PATROLS", "3 of 4 patrols completed; 22 of 26 checkpoints scanned.",
        "", "DISPATCHES", "Sent to 2 incidents; arrived at 1; could not attend 1.",
        "", "VISITORS", "6 arrived, 5 left, 1 turned away.",
        "", "IN HAND AT THE SITE",
        "2 keys out (1 overdue). 1 item of lost property held. 0 items of kit signed out. 3 defects open.",
        "", "INSTRUCTIONS IN FORCE",
        "- Gate 3 stays locked until Friday. (Lim Mei Ling, 5 Oct; until 9 Oct)",
        "- Call the duty manager before letting a contractor on the roof. (1 Oct)",
        "", "TO BE FOLLOWED UP",
        "- 6 Oct 22:10 General: Fence light out by bay 4. — to follow up: Raise a defect and check it is fixed.",
    ]


def test_the_heading_gives_the_shift_in_the_organisations_own_time_and_says_when_it_is_still_running():
    night = _facts()
    night["shift"] = {**night["shift"], "start": "2026-10-06T11:00:00+00:00", "end": "2026-10-06T23:00:00+00:00",
                      "ended": False, "site_name": None, "guard_name": None}
    assert summary.write(night).split("\n")[0] == \
        "Shift summary: No site, 6 Oct 2026, 19:00 to 7 Oct 07:00 (shift still running) — a guard"
    utc = _facts()
    utc["shift"] = {**utc["shift"], "timezone": "UTC"}
    assert "6 Oct 2026, 23:00 to 7 Oct 11:00" in summary.write(utc)
    roving = _facts(visitors=None)
    assert "VISITORS" not in summary.write(roving), "a shift with no site has no visitors to count"
    one = _facts(alerts={"raised": 1, "open_now": 0, "by_severity": {"low": 1}},
                 patrols={"sessions": 1, "completed": 1, "checkpoints_scanned": 1, "checkpoints_total": 1},
                 dispatches={"sent": 1, "arrived": 1, "declined": 0},
                 entries={"total": 1, "by_type": {"general": 1}, "of_note_total": 0, "of_note": []})
    text_of = summary.write(one)
    for said in ("1 entry: general 1.", "1 raised: 1 low. All dealt with.", "1 of 1 patrol completed; 1 of 1 checkpoint scanned.",
                 "Sent to 1 incident; arrived at 1."):
        assert said in text_of, said


def test_a_quotation_is_cut_and_never_reworded():
    assert summary.quote("  Two\nlines   and\tspaces ") == "Two lines and spaces"
    long = "word " * 80
    cut = summary.quote(long)
    assert len(cut) == summary.QUOTE_CHARS and cut.endswith("…") and long.startswith(cut[:-1])
    assert summary.quote(None) == "" and summary.METHOD == "TEMPLATE"
    assert summary.label("unusual_activity") == "unusual activity"


# ─── B. The book, searched ───────────────────────────────────────────────────

async def test_a_delivery_and_something_unusual_are_things_a_guard_writes_down():
    w = await _world()
    assert {"delivery", "unusual_activity"} <= VALID_ENTRY_TYPES and len(VALID_ENTRY_TYPES) == 15
    async with _client() as c:
        made = await _write(c, w, "Two pallets for Bay 4, signed for.", kind="delivery")
        await _write(c, w, "Van at the east fence for twenty minutes.", kind="unusual_activity", severity="high")
        refused = await c.post("/api/v1/dob", headers=w["h"][GUARD], json={"entry_type": "gossip", "body": "x"})
        kinds = (await c.get(f"{BASE}/kinds", headers=w["h"][GUARD])).json()
        viewer = (await c.get(f"{BASE}/kinds", headers=w["h"][VIEWER])).json()
        through_the_old = (await c.get("/api/v1/dob", headers=w["h"][GUARD])).json()
    assert made["entry_type"] == "delivery" and refused.status_code == 422
    assert {k["key"] for k in kinds["kinds"]} == VALID_ENTRY_TYPES
    assert [k["key"] for k in kinds["kinds"]][:4] == ["general", "incident", "unusual_activity", "delivery"]
    assert {"key": "unusual_activity", "label": "Unusual activity"} in kinds["kinds"]
    assert (kinds["can_write"], kinds["can_review"], kinds["can_read_handovers"]) == (True, False, True)
    assert kinds["review_states"] == list(api.REVIEW_STATES)
    assert (viewer["can_write"], viewer["can_review"], viewer["review_states"]) == (False, False, [])
    assert {e["entry_type"] for e in through_the_old} == {"delivery", "unusual_activity"}, \
        "and the existing list gives them, as it gives every entry"


async def test_the_book_is_searched_by_words_kind_site_author_and_period():
    w, other = await _world(), await _world()
    second, second_h = await _guard(w, "Second Guard")
    async with _client() as c:
        fence = await _write(c, w, "Blue lorry left by the east fence.", kind="unusual_activity", severity="high")
        await _write(c, w, "Pallets for Bay 4, 100% checked.", kind="delivery")
        await _write(c, w, "File named night_log.txt found.", headers=second_h)
        b = await _write(c, w, "Gate B alarm tested.", kind="alarm_activation", site="site_b", who=OPERATOR)
        old = await _write(c, w, "Lorry seen again yesterday.", kind="unusual_activity")
        await _write(c, other, "Lorry at their fence.")
        await _backdate(old["id"], 60 * 30)
        await _backdate(fence["id"], 10)

        async def find(who=ADMIN, **params):
            r = await c.get(f"{BASE}/entries", headers=w["h"][who], params=params)
            assert r.status_code == 200, r.text
            return [e["body"] for e in r.json()["items"]]

        everything = await find()
        assert len(everything) == 5 and everything[-1] == "Lorry seen again yesterday.", "newest first"
        assert await find(q="lorry") == ["Blue lorry left by the east fence.", "Lorry seen again yesterday."]
        assert await find(q="  LORRY  ", entry_type=["unusual_activity"], site_id=str(w["site_a"])) == \
            ["Blue lorry left by the east fence.", "Lorry seen again yesterday."]
        # A percent sign and an underscore are looked for, not treated as wildcards.
        assert await find(q="100%") == ["Pallets for Bay 4, 100% checked."]
        assert await find(q="night_log") == ["File named night_log.txt found."]
        assert await find(q="nightXlog") == [] and await find(q="%") == ["Pallets for Bay 4, 100% checked."]
        assert await find(entry_type=["delivery", "alarm_activation"]) == ["Gate B alarm tested.", "Pallets for Bay 4, 100% checked."]
        assert await find(site_id=str(w["site_b"])) == ["Gate B alarm tested."]
        assert await find(author_user_id=str(second)) == ["File named night_log.txt found."]
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert "Lorry seen again yesterday." not in await find(date_from=since)
        assert await find(date_until=since) == ["Lorry seen again yesterday."]
        page = (await c.get(f"{BASE}/entries", headers=w["h"][ADMIN], params={"limit": 2, "offset": 4})).json()
        first = (await c.get(f"{BASE}/entries", headers=w["h"][ADMIN], params={"limit": 2})).json()
        assert len(page["items"]) == 1 and page["has_more"] is False and first["has_more"] is True

        # Held to site A: site A's entries, and nothing of site B.
        assert "Gate B alarm tested." not in await find(SUPERVISOR) and len(await find(SUPERVISOR)) == 4
        assert (await c.get(f"{BASE}/entries", headers=w["h"][SUPERVISOR],
                            params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(f"{BASE}/entries/{b['id']}", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"{BASE}/entries", headers=w["h"][ADMIN], params={"entry_type": ["gossip"]})).status_code == 422
        theirs = (await c.get(f"{BASE}/entries", headers=other["h"][ADMIN])).json()["items"]
        assert [e["body"] for e in theirs] == ["Lorry at their fence."]
        assert (await c.get(f"{BASE}/entries/{fence['id']}", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.get(f"{BASE}/entries")).status_code == 401


async def test_somebody_held_to_other_sites_still_reads_what_they_wrote_themselves():
    w = await _world()
    async with _client() as c:
        # The supervisor is held to site A, and wrote one entry at no site at all.
        mine = await _write(c, w, "Briefed the night team by phone.", who=SUPERVISOR, site=None)
        await _write(c, w, "Something at site B.", who=OPERATOR, site="site_b")
        seen = (await c.get(f"{BASE}/entries", headers=w["h"][SUPERVISOR])).json()["items"]
        one = await c.get(f"{BASE}/entries/{mine['id']}", headers=w["h"][SUPERVISOR])
    assert [e["body"] for e in seen] == ["Briefed the night team by phone."] and seen[0]["mine"] is True
    assert one.status_code == 200 and one.json()["site_name"] is None


# ─── C. Review ───────────────────────────────────────────────────────────────

async def test_a_supervisor_notes_an_entry_or_says_what_is_to_be_followed_up_and_later_what_was_done():
    w = await _world()
    async with _client() as c:
        light = await _write(c, w, "Fence light out by bay 4.")
        quiet = await _write(c, w, "All quiet on the north side.")
        url = f"{BASE}/entries/{light['id']}/review"
        for body, why in (({"outcome": "FOLLOW_UP"}, "Say what is to be followed up."),
                          ({"outcome": "FOLLOW_UP", "note": "   "}, "Say what is to be followed up."),
                          ({"outcome": "CLOSED", "note": "Done"}, "There is no follow-up on this entry to close.")):
            r = await c.post(url, headers=w["h"][SUPERVISOR], json=body)
            assert r.status_code in (409, 422) and r.json()["detail"] == why, body
        assert (await c.post(url, headers=w["h"][SUPERVISOR], json={"outcome": "SHRUG"})).status_code == 422
        follow = await c.post(url, headers=w["h"][SUPERVISOR], json={"outcome": "FOLLOW_UP",
                                                                    "note": " Raise a defect and check it is fixed. "})
        assert follow.status_code == 200, follow.text
        noted_over = await c.post(url, headers=w["h"][MANAGER], json={})
        assert noted_over.status_code == 409 and "Close the follow-up" in noted_over.json()["detail"]
        assert (await c.post(url, headers=w["h"][MANAGER], json={"outcome": "CLOSED"})).status_code == 422
        closed = await c.post(url, headers=w["h"][MANAGER], json={"outcome": "CLOSED", "note": "Lamp replaced."})
        noted = await c.post(f"{BASE}/entries/{quiet['id']}/review", headers=w["h"][SUPERVISOR], json={})
        one = (await c.get(f"{BASE}/entries/{light['id']}", headers=w["h"][GUARD])).json()

        async def states(state):
            r = await c.get(f"{BASE}/entries", headers=w["h"][ADMIN], params={"review": state})
            return [e["body"] for e in r.json()["items"]]

        assert await states("closed") == ["Fence light out by bay 4."]
        assert await states("noted") == ["All quiet on the north side."]
        assert await states("follow_up") == [] and await states("unreviewed") == []
    assert follow.json()["review"] == {"state": "follow_up", "note": "Raise a defect and check it is fixed.",
                                       "reviewed_at": follow.json()["review"]["reviewed_at"],
                                       "reviewed_by_name": f"Role {SUPERVISOR} User"}
    assert closed.json()["review"]["state"] == "closed" and closed.json()["review"]["reviewed_by_name"] == f"Role {MANAGER} User"
    assert noted.json()["review"]["state"] == "noted" and noted.json()["review"]["note"] is None
    assert [(r["outcome"], r["note"], r["reviewed_by_name"]) for r in one["reviews"]] == [
        ("FOLLOW_UP", "Raise a defect and check it is fixed.", f"Role {SUPERVISOR} User"),
        ("CLOSED", "Lamp replaced.", f"Role {MANAGER} User")], "every review is kept, in order"
    assert one["body"] == "Fence light out by bay 4.", "and the entry says what it said"
    entries = await _audit(w, "dob.review")
    assert [(e["detail"]["outcome"], e["detail"]["was"]) for e in entries] == [
        ("FOLLOW_UP", None), ("CLOSED", "FOLLOW_UP"), ("NOTED", None)]
    assert entries[0]["detail"]["site_id"] == str(w["site_a"]) and entries[0]["detail"]["actor_role"] == SUPERVISOR


async def test_a_review_is_by_somebody_else_who_holds_the_permission_and_only_as_a_person():
    w, other = await _world(), await _world()
    async with _client() as c:
        theirs = await _write(c, w, "Checked the plant room.")
        own = await _write(c, w, "Walked the site with the client.", who=SUPERVISOR)
        at_b = await _write(c, w, "Gate B alarm tested.", who=OPERATOR, site="site_b")
        url = f"{BASE}/entries/{theirs['id']}/review"
        for who in (GUARD, OPERATOR, VIEWER):
            r = await c.post(url, headers=w["h"][who], json={})
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: dob:review", who
        mine = await c.post(f"{BASE}/entries/{own['id']}/review", headers=w["h"][SUPERVISOR], json={})
        assert mine.status_code == 403 and "somebody other than who wrote it" in mine.json()["detail"]
        assert (await c.post(f"{BASE}/entries/{at_b['id']}/review", headers=w["h"][SUPERVISOR], json={})).status_code == 404
        assert (await c.post(url, headers=other["h"][ADMIN], json={})).status_code == 404
        assert (await c.post(f"{BASE}/entries/{uuid.uuid4()}/review", headers=w["h"][ADMIN], json={})).status_code == 404
        assert (await c.post(url, headers=w["h"][ADMIN], json={"outcome": "NOTED", "verdict": "x"})).status_code == 422
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.post(url, json={})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
            assert (await c.get(f"{BASE}/entries")).status_code == 200, "an integration may read the book it may read"
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    assert await _sql("SELECT 1 FROM occurrence_entry_reviews WHERE tenant_id = :t", {"t": w["tenant"]}) == []


async def test_what_a_reviewer_wrote_is_for_those_who_keep_the_book_and_not_for_a_client_or_a_viewer():
    w = await _world()
    client, client_h = await _guard(w, "Building Owner", role=CLIENT)
    await _sql("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)",
               {"u": client, "s": w["site_a"], "t": w["tenant"]})
    async with _client() as c:
        entry = await _write(c, w, "Guard found asleep at 03:10; woken and logged.", severity="medium")
        await c.post(f"{BASE}/entries/{entry['id']}/review", headers=w["h"][SUPERVISOR],
                     json={"outcome": "FOLLOW_UP", "note": "Disciplinary interview on Monday."})
        for reader in (client_h, w["h"][VIEWER]):
            listed = await c.get(f"{BASE}/entries", headers=reader)
            assert listed.status_code == 200, listed.text
            (seen,) = listed.json()["items"]
            assert seen["body"].startswith("Guard found asleep") and seen["review"] is None
            one = (await c.get(f"{BASE}/entries/{entry['id']}", headers=reader)).json()
            assert one["review"] is None and one["reviews"] == []
            assert "Disciplinary" not in listed.text + str(one)
            refused = await c.get(f"{BASE}/entries", headers=reader, params={"review": "follow_up"})
            assert refused.status_code == 403 and "people who keep the book" in refused.json()["detail"]
            assert listed.json()["can_review"] is False and listed.json()["can_write"] is False
        guard = (await c.get(f"{BASE}/entries/{entry['id']}", headers=w["h"][GUARD])).json()
    assert guard["review"]["state"] == "follow_up" and guard["review"]["note"] == "Disciplinary interview on Monday."


async def test_a_page_of_entries_is_noted_at_once_and_the_rest_are_left_with_why():
    w = await _world()
    async with _client() as c:
        a = await _write(c, w, "One.")
        b = await _write(c, w, "Two.")
        own = await _write(c, w, "Mine.", who=SUPERVISOR)
        done = await _write(c, w, "Already seen.")
        at_b = await _write(c, w, "At B.", who=OPERATOR, site="site_b")
        await c.post(f"{BASE}/entries/{done['id']}/review", headers=w["h"][MANAGER], json={})
        missing = str(uuid.uuid4())
        r = await c.post(f"{BASE}/entries/review", headers=w["h"][SUPERVISOR], json={
            "entry_ids": [a["id"], b["id"], a["id"], own["id"], done["id"], at_b["id"], missing]})
        assert r.status_code == 200, r.text
        assert (await c.post(f"{BASE}/entries/review", headers=w["h"][SUPERVISOR], json={"entry_ids": []})).status_code == 422
        assert (await c.post(f"{BASE}/entries/review", headers=w["h"][GUARD], json={"entry_ids": [a["id"]]})).status_code == 403
        too_many = [str(uuid.uuid4()) for _ in range(api.MAX_BULK + 1)]
        assert (await c.post(f"{BASE}/entries/review", headers=w["h"][ADMIN], json={"entry_ids": too_many})).status_code == 422
    assert r.json()["reviewed"] == [a["id"], b["id"]], "once each, though one was named twice"
    assert {x["id"]: x["why"] for x in r.json()["left"]} == {
        own["id"]: "You wrote it.", done["id"]: "Already reviewed.", at_b["id"]: "Not found.", missing: "Not found."}
    rows = await _sql("SELECT outcome FROM occurrence_entry_reviews WHERE tenant_id = :t", {"t": w["tenant"]})
    assert [r["outcome"] for r in rows] == ["NOTED"] * 3


# ─── D. Correction ───────────────────────────────────────────────────────────

async def test_an_entry_is_put_right_by_a_further_entry_and_stays_as_it_was_written():
    w = await _world()
    other_guard, other_h = await _guard(w, "Another Guard")
    async with _client() as c:
        wrong = await _write(c, w, "Delivery for Bay 4 at 14:00, 3 pallets.", kind="delivery", severity="low")
        url = f"{BASE}/entries/{wrong['id']}/correct"
        for body in ({"body": "x"}, {"reason": "x"}, {"body": " ", "reason": "x"}, {"body": "x", "reason": " "}):
            assert (await c.post(url, headers=w["h"][GUARD], json=body)).status_code == 422, body
        not_theirs = await c.post(url, headers=other_h, json={"body": "x", "reason": "y"})
        assert not_theirs.status_code == 403 and "whoever wrote it" in not_theirs.json()["detail"]
        assert (await c.post(url, headers=w["h"][VIEWER], json={"body": "x", "reason": "y"})).status_code == 403
        right = await c.post(url, headers=w["h"][GUARD], json={
            "body": " Delivery for Bay 4 at 14:00, 5 pallets. ", "reason": " Miscounted: the driver's note says five. "})
        assert right.status_code == 201, right.text
        # A reviewer may correct somebody else's entry — and a correction can itself be corrected.
        again = await c.post(f"{BASE}/entries/{right.json()['id']}/correct", headers=w["h"][SUPERVISOR], json={
            "body": "Delivery for Bay 4 at 14:20, 5 pallets.", "reason": "The gate log gives 14:20."})
        assert again.status_code == 201, again.text
        first = (await c.get(f"{BASE}/entries/{wrong['id']}", headers=w["h"][GUARD])).json()
        middle = (await c.get(f"{BASE}/entries/{right.json()['id']}", headers=w["h"][GUARD])).json()
        book = (await c.get("/api/v1/dob", headers=w["h"][GUARD])).json()
        only_corrected = (await c.get(f"{BASE}/entries", headers=w["h"][ADMIN], params={"corrected": True})).json()["items"]
        never = (await c.get(f"{BASE}/entries", headers=w["h"][ADMIN], params={"corrected": False})).json()["items"]
    new = right.json()
    assert new["body"] == "Delivery for Bay 4 at 14:00, 5 pallets." and new["entry_type"] == "delivery"
    assert new["severity"] == "low" and new["site_id"] == str(w["site_a"]) and new["mine"] is True
    assert new["corrects_entry_id"] == wrong["id"] and new["correction_reason"] == "Miscounted: the driver's note says five."
    assert first["body"] == "Delivery for Bay 4 at 14:00, 3 pallets.", "the first entry is exactly as it was written"
    assert first["corrected_by_entry_id"] == new["id"] and [x["id"] for x in first["corrected_by"]] == [new["id"]]
    assert first["corrects"] is None
    assert middle["corrects"]["id"] == wrong["id"] and [x["id"] for x in middle["corrected_by"]] == [again.json()["id"]]
    assert again.json()["author_name"] == f"Role {SUPERVISOR} User"
    assert len(book) == 3, "all three are in the book, through the endpoint that has always listed it"
    assert {e["id"] for e in only_corrected} == {wrong["id"], new["id"]} and [e["id"] for e in never] == [again.json()["id"]]
    stored = await _sql("SELECT body FROM occurrence_book_entries WHERE id = :i", {"i": wrong["id"]})
    assert stored[0]["body"] == "Delivery for Bay 4 at 14:00, 3 pallets."
    audits = await _audit(w, "dob.correct")
    assert [a["detail"]["corrects_entry_id"] for a in audits] == [wrong["id"], new["id"]]
    assert other_guard


def test_nothing_in_this_router_edits_or_removes_an_entry():
    code = Path(api.__file__).read_text(encoding="utf-8")
    assert "UPDATE occurrence_book_entries" not in code and "DELETE FROM" not in code
    assert code.count("INSERT INTO occurrence_book_entries") == 1, "only a correction writes an entry here"
    served = set()
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            if getattr(route, "path", "").startswith(BASE):
                served |= {(m, route.path.removeprefix(BASE)) for m in route.methods - {"HEAD"}}
    assert not [m for m, _ in served if m in ("DELETE", "PUT")]
    assert [p for m, p in served if m == "PATCH"] == ["/shift-summaries/{summary_id:uuid}"], "only a draft is edited"


# ─── E. Instructions in force at a site ──────────────────────────────────────

async def test_an_instruction_is_issued_read_and_closed_and_the_guards_on_shift_are_told(told):
    w, other = await _world(), await _world()
    on_duty = await _on_shift(w)
    told.tokens[f"push_tokens:{w['tenant']}:{w['users'][GUARD]}"] = {"ExponentPushToken[guard]"}
    told.tokens[f"push_tokens:{w['tenant']}:{w['users'][SUPERVISOR]}"] = {"ExponentPushToken[issuer]"}
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    async with _client() as c:
        url = f"{BASE}/instructions"
        good = {"site_id": str(w["site_a"]), "body": "  Gate 3 stays locked until Friday.  ", "expires_at": soon}
        for who in (GUARD, OPERATOR):
            assert (await c.post(url, headers=w["h"][who], json=good)).status_code == 403, who
        assert (await c.get(url, headers=w["h"][VIEWER])).status_code == 403, "an instruction is handover material"
        for bad, status in (({**good, "body": "  "}, 422), ({**good, "expires_at": "2020-01-01T00:00:00Z"}, 422),
                            ({**good, "expires_at": "2030-01-01T00:00:00"}, 422),
                            ({**good, "site_id": str(w["site_b"])}, 404), ({**good, "site_id": str(other["site_a"])}, 404)):
            r = await c.post(url, headers=w["h"][SUPERVISOR], json=bad)
            assert r.status_code == status, (bad, r.text)
        made = await c.post(url, headers=w["h"][SUPERVISOR], json=good)
        assert made.status_code == 201, made.text
        standing = await c.post(url, headers=w["h"][ADMIN], json={"site_id": str(w["site_b"]), "body": "No hot work."})
        n = made.json()
        guard_sees = (await c.get(url, headers=w["h"][GUARD])).json()
        read = await c.post(f"{url}/{n['id']}/read", headers=w["h"][GUARD])
        again = await c.post(f"{url}/{n['id']}/read", headers=w["h"][GUARD])
        assert (await c.post(f"{url}/{n['id']}/close", headers=w["h"][GUARD], json={"note": "x"})).status_code == 403
        assert (await c.post(f"{url}/{n['id']}/close", headers=w["h"][SUPERVISOR], json={"note": " "})).status_code == 422
        assert (await c.post(f"{url}/{standing.json()['id']}/close", headers=w["h"][SUPERVISOR],
                             json={"note": "x"})).status_code == 404, "held to site A"
        closed = await c.post(f"{url}/{n['id']}/close", headers=w["h"][MANAGER], json={"note": "The gate was repaired."})
        assert (await c.post(f"{url}/{n['id']}/close", headers=w["h"][MANAGER], json={"note": "x"})).status_code == 409
        in_force = (await c.get(url, headers=w["h"][ADMIN])).json()["items"]
        ended = (await c.get(url, headers=w["h"][ADMIN], params={"state": "ended"})).json()["items"]
        at_a = (await c.get(url, headers=w["h"][ADMIN], params={"state": "all", "site_id": str(w["site_a"])})).json()["items"]
        assert (await c.get(url, headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(url, headers=other["h"][ADMIN], params={"state": "all"})).json()["items"] == []
        assert (await c.post(f"{url}/{n['id']}/read", headers=other["h"][GUARD])).status_code == 404
    assert n["body"] == "Gate 3 stays locked until Friday." and n["site_name"] == "Factory A" and n["in_force"] is True
    assert n["issued_by_name"] == f"Role {SUPERVISOR} User" and n["reads"] == 0 and n["read_by_me"] is False
    assert [i["body"] for i in guard_sees["items"]] == ["No hot work.", "Gate 3 stays locked until Friday."]
    assert guard_sees["can_issue"] is False
    assert read.json()["read_by_me"] is True and read.json()["reads"] == 1 and again.json()["reads"] == 1
    assert closed.json()["in_force"] is False and closed.json()["close_note"] == "The gate was repaired."
    assert closed.json()["closed_by_name"] == f"Role {MANAGER} User"
    assert [i["body"] for i in in_force] == ["No hot work."] and [i["body"] for i in ended] == ["Gate 3 stays locked until Friday."]
    assert [i["id"] for i in at_a] == [n["id"]]
    # The guard on shift at the site is told; the person who issued it is not told what they just wrote.
    assert [p["tokens"] for p in told.pushes] == [["ExponentPushToken[guard]"]]
    assert told.pushes[0]["body"] == "New instruction at Factory A: Gate 3 stays locked until Friday."
    events = told.events(api.INSTRUCTION_EVENT)
    assert [e["site_name"] for e in events] == ["Factory A", "Factory B"]
    assert len(await _audit(w, "dob.instruction.issue")) == 2 and len(await _audit(w, "dob.instruction.close")) == 1
    assert on_duty


async def test_an_instruction_that_has_run_out_is_no_longer_in_force():
    w = await _world()
    await _run([
        ("INSERT INTO site_instructions (tenant_id, site_id, body, issued_at, expires_at) "
         "VALUES (:t,:s,'Ran out yesterday.', now() - interval '3 days', now() - interval '1 day')",
         {"t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO site_instructions (tenant_id, site_id, body) VALUES (:t,:s,'Still stands.')",
         {"t": w["tenant"], "s": w["site_a"]}),
    ])
    async with _client() as c:
        in_force = (await c.get(f"{BASE}/instructions", headers=w["h"][GUARD])).json()["items"]
        ended = (await c.get(f"{BASE}/instructions", headers=w["h"][GUARD], params={"state": "ended"})).json()["items"]
    assert [i["body"] for i in in_force] == ["Still stands."]
    assert [(i["body"], i["in_force"], i["closed_at"]) for i in ended] == [("Ran out yesterday.", False, None)]


# ─── F. A shift's summary ────────────────────────────────────────────────────

async def _busy_shift(c, w: dict) -> dict:
    """A shift on which a little of everything was recorded."""
    t, s, guard, now = w["tenant"], w["site_a"], w["users"][GUARD], datetime.now(timezone.utc)
    shift = await _on_shift(w)
    ids = {k: uuid.uuid4() for k in ("camera", "open", "done", "before", "route", "session", "visitor", "elsewhere")}
    await _run([
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Gate')", {"i": ids["camera"], "t": t, "s": s}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Gate B')",
         {"i": ids["elsewhere"], "t": t, "s": w["site_b"]}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
         "VALUES (:i,:t,:c,'Forced gate','high','on_scene',:at)",
         {"i": ids["open"], "t": t, "c": ids["camera"], "at": now - timedelta(hours=1)}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at, resolved_at) "
         "VALUES (:i,:t,:c,'Door held open','low','resolved',:at,:done)",
         {"i": ids["done"], "t": t, "c": ids["camera"], "at": now - timedelta(hours=2), "done": now - timedelta(minutes=90)}),
        # Opened the day before and still open: counted as open at the site, not as opened on this shift.
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
         "VALUES (:i,:t,:c,'Broken fence panel','medium','open',:at)",
         {"i": ids["before"], "t": t, "c": ids["camera"], "at": now - timedelta(days=1)}),
        # Another site's incident is none of this shift's business.
        ("INSERT INTO incidents (tenant_id, camera_id, title, severity, status) VALUES (:t,:c,'At site B','critical','open')",
         {"t": t, "c": ids["elsewhere"]}),
        ("INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity, title, status, created_at) "
         "VALUES (:t,:c,:s,'intrusion','critical','Zone breach','open',:at)",
         {"t": t, "c": ids["camera"], "s": s, "at": now - timedelta(minutes=50)}),
        ("INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity, title, status, created_at) "
         "VALUES (:t,:c,:s,'loitering','medium','Loitering','acknowledged',:at)",
         {"t": t, "c": ids["camera"], "s": s, "at": now - timedelta(minutes=40)}),
        ("INSERT INTO patrol_routes (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Perimeter')",
         {"i": ids["route"], "t": t, "s": s}),
        ("INSERT INTO patrol_sessions (id, tenant_id, route_id, guard_user_id, shift_id, status, total_checkpoints, "
         "scanned_checkpoints) VALUES (:i,:t,:r,:g,:sh,'completed',6,5)",
         {"i": ids["session"], "t": t, "r": ids["route"], "g": guard, "sh": shift}),
        ("INSERT INTO visitors (id, tenant_id, site_id, full_name, qr_token) VALUES (:i,:t,:s,'A Visitor',:q)",
         {"i": ids["visitor"], "t": t, "s": s, "q": uuid.uuid4().hex}),
        *[("INSERT INTO visitor_logs (tenant_id, visitor_id, site_id, guard_user_id, event_type, occurred_at) "
           "VALUES (:t,:v,:s,:g,:e,:at)", {"t": t, "v": ids["visitor"], "s": s, "g": guard, "e": event,
                                           "at": now - timedelta(minutes=30 - n)})
          for n, event in enumerate(("check_in", "arrival", "check_out", "denied"))],
        ("INSERT INTO incident_responses (tenant_id, incident_id, site_id, guard_user_id, dispatched_at, state, arrived_at) "
         "VALUES (:t,:i,:s,:g,:at,'ARRIVED',:there)",
         {"t": t, "i": ids["open"], "s": s, "g": guard, "at": now - timedelta(minutes=55), "there": now - timedelta(minutes=50)}),
        ("INSERT INTO site_instructions (tenant_id, site_id, body, issued_by_user_id) VALUES (:t,:s,:b,:u)",
         {"t": t, "s": s, "b": "Gate 3 stays locked until Friday.", "u": w["users"][SUPERVISOR]}),
    ])
    await _write(c, w, "Van at the east fence for twenty minutes.", kind="unusual_activity", severity="high", shift=shift)
    await _write(c, w, "Two pallets for Bay 4.", kind="delivery", shift=shift)
    await _write(c, w, "All quiet.", shift=shift)
    earlier = await _write(c, w, "Fence light out by bay 4.", who=OPERATOR)
    await _backdate(earlier["id"], 60 * 20)       # before this shift: not one of its entries
    await c.post(f"{BASE}/entries/{earlier['id']}/review", headers=w["h"][SUPERVISOR],
                 json={"outcome": "FOLLOW_UP", "note": "Raise a defect."})
    return {"shift": shift, **ids}


async def test_the_guard_drafts_the_summary_of_their_own_shift_from_what_was_recorded():
    w = await _world()
    async with _client() as c:
        b = await _busy_shift(c, w)
        r = await c.post(f"{BASE}/shift-summaries", headers=w["h"][GUARD], json={"shift_id": str(b["shift"])})
        assert r.status_code == 201, r.text
    s = r.json()
    facts = s["facts"]
    assert (s["state"], s["method"], s["drafted_again"], s["edited"]) == ("DRAFT", "TEMPLATE", False, False)
    assert s["final_text"] == s["drafted_text"] and s["guard_name"] == f"Role {GUARD} User" and s["site_name"] == "Factory A"
    assert s["may"] == {"edit": True, "confirm": True, "discard": True} and s["handover_id"] is None
    assert facts["entries"]["total"] == 3 and facts["entries"]["by_type"] == {"unusual_activity": 1, "delivery": 1, "general": 1}
    assert [e["body"] for e in facts["entries"]["of_note"]] == ["Van at the east fence for twenty minutes."]
    assert (facts["incidents"]["opened"], facts["incidents"]["resolved"], facts["incidents"]["open_now"]) == (2, 1, 2)
    assert [i["title"] for i in facts["incidents"]["still_open"]] == ["Forced gate", "Broken fence panel"]
    assert facts["alerts"] == {"raised": 2, "open_now": 1, "by_severity": {"critical": 1, "medium": 1}}
    assert facts["patrols"] == {"sessions": 1, "completed": 1, "checkpoints_scanned": 5, "checkpoints_total": 6}
    assert facts["dispatches"] == {"sent": 1, "arrived": 1, "declined": 0}
    assert facts["visitors"] == {"arrived": 2, "left": 1, "turned_away": 1}
    assert [n["body"] for n in facts["instructions"]] == ["Gate 3 stays locked until Friday."]
    assert [(f["body"], f["note"]) for f in facts["follow_ups"]] == [("Fence light out by bay 4.", "Raise a defect.")]
    words = s["drafted_text"]
    assert words == summary.write(facts), "the words are the facts, written out, and nothing else"
    for said in ("3 entries: delivery 1, general 1, unusual activity 1.",
                 "Unusual activity (high): Van at the east fence for twenty minutes.",
                 "2 opened and 1 resolved during the shift. 2 open at the site now:", "- Forced gate (high, on scene)",
                 "2 raised: 1 critical, 1 medium. 1 of them still open.",
                 "1 of 1 patrol completed; 5 of 6 checkpoints scanned.", "Sent to 1 incident; arrived at 1.",
                 "2 arrived, 1 left, 1 turned away.", "- Gate 3 stays locked until Friday. (Role 3 User,",
                 "to follow up: Raise a defect.", "(shift still running)"):
        assert said in words, said
    assert "At site B" not in words and "All quiet." not in words, "another site's incident, and an entry of no note"
    (entry,) = await _audit(w, "dob.summary.draft")
    assert entry["detail"]["shift_id"] == str(b["shift"]) and entry["detail"]["drafted_again"] is False


async def test_a_draft_is_its_writers_until_it_is_confirmed_and_then_it_stands():
    w, other = await _world(), await _world()
    someone, someone_h = await _guard(w, "Another Guard")
    async with _client() as c:
        b = await _busy_shift(c, w)
        made = (await c.post(f"{BASE}/shift-summaries", headers=w["h"][GUARD], json={"shift_id": str(b["shift"])})).json()
        url = f"{BASE}/shift-summaries/{made['id']}"
        # Not yet confirmed: the guard's own, and whoever manages handovers. Nobody else's.
        for reader, status in ((w["h"][GUARD], 200), (w["h"][SUPERVISOR], 200), (w["h"][MANAGER], 200),
                               (w["h"][OPERATOR], 404), (someone_h, 404), (other["h"][ADMIN], 404), (w["h"][VIEWER], 403)):
            assert (await c.get(url, headers=reader)).status_code == status
        assert (await c.get(f"{BASE}/shift-summaries", headers=w["h"][OPERATOR])).json()["items"] == []
        assert len((await c.get(f"{BASE}/shift-summaries", headers=w["h"][GUARD])).json()["items"]) == 1
        assert (await c.patch(url, headers=someone_h, json={"final_text": "x"})).status_code == 404
        assert (await c.patch(url, headers=w["h"][GUARD], json={"final_text": "   "})).status_code == 422
        edited = await c.patch(url, headers=w["h"][GUARD], json={
            "final_text": made["drafted_text"] + "\n\nThe van's plate was SGX1234A; told the day shift in person."})
        assert edited.status_code == 200 and edited.json()["edited"] is True
        assert edited.json()["drafted_text"] == made["drafted_text"], "what the platform drafted is kept as written"
        confirmed = await c.post(f"{url}/confirm", headers=w["h"][GUARD])
        assert confirmed.status_code == 200, confirmed.text
        for step, body in (("confirm", None), ("discard", None)):
            assert (await c.post(f"{url}/{step}", headers=w["h"][GUARD], json=body)).status_code == 409, step
        assert (await c.patch(url, headers=w["h"][MANAGER], json={"final_text": "Rewritten"})).status_code == 409
        over = await c.post(f"{BASE}/shift-summaries", headers=w["h"][MANAGER], json={"shift_id": str(b["shift"])})
        assert over.status_code == 409 and "It stands as it is." in over.json()["detail"]
        # Confirmed: read by whoever reads handovers.
        for reader in (w["h"][OPERATOR], someone_h, w["h"][SUPERVISOR]):
            seen = await c.get(url, headers=reader)
            assert seen.status_code == 200 and seen.json()["may"] == {"edit": False, "confirm": False, "discard": False}
        assert (await c.get(url, headers=other["h"][ADMIN])).status_code == 404
        # The handover for the shift, made through the endpoint that has always made it.
        handover = await c.post(f"/api/v1/shifts/{b['shift']}/handover", headers=w["h"][SUPERVISOR])
        assert handover.status_code == 200, handover.text
        by_shift = (await c.get(f"{BASE}/shift-summaries", headers=w["h"][OPERATOR],
                                params={"shift_id": str(b["shift"])})).json()["items"]
    done = confirmed.json()
    assert done["state"] == "CONFIRMED" and done["confirmed_by_name"] == f"Role {GUARD} User" and done["confirmed_at"]
    assert done["final_text"].endswith("told the day shift in person.")
    assert [x["id"] for x in by_shift] == [made["id"]] and by_shift[0]["handover_id"] == handover.json()["id"]
    (entry,) = await _audit(w, "dob.summary.confirm")
    assert entry["detail"]["edited"] is True and entry["detail"]["shift_id"] == str(b["shift"])
    assert someone


async def test_a_draft_is_set_aside_and_drafted_again_from_what_is_recorded_now():
    w = await _world()
    async with _client() as c:
        shift = await _on_shift(w)
        url = f"{BASE}/shift-summaries"
        first = (await c.post(url, headers=w["h"][GUARD], json={"shift_id": str(shift)})).json()
        assert "No entries were written." in first["drafted_text"]
        await _write(c, w, "Sprinkler test at 15:00.", kind="fire_drill", shift=shift)
        second = await c.post(url, headers=w["h"][MANAGER], json={"shift_id": str(shift)})
        assert second.status_code == 201 and second.json()["drafted_again"] is True
        assert "1 entry: fire drill 1." in second.json()["drafted_text"]
        assert second.json()["drafted_by_name"] == f"Role {MANAGER} User"
        assert (await c.get(f"{url}/{first['id']}", headers=w["h"][GUARD])).status_code == 404, "the first is set aside"
        gone = await c.post(f"{url}/{second.json()['id']}/discard", headers=w["h"][GUARD])
        assert gone.status_code == 200 and gone.json() == {"state": "DISCARDED"}
        assert (await c.get(url, headers=w["h"][GUARD])).json()["items"] == []
        third = await c.post(url, headers=w["h"][GUARD], json={"shift_id": str(shift)})
        assert third.status_code == 201 and third.json()["drafted_again"] is False
    states = await _sql("SELECT state FROM shift_handover_summaries WHERE shift_id = :s ORDER BY drafted_at", {"s": shift})
    assert [r["state"] for r in states] == ["DISCARDED", "DISCARDED", "DRAFT"]
    assert len(await _audit(w, "dob.summary.draft")) == 3 and len(await _audit(w, "dob.summary.discard")) == 1


async def test_who_may_draft_which_shift():
    w, other = await _world(), await _world()
    someone, someone_h = await _guard(w, "Another Guard")
    async with _client() as c:
        mine = await _on_shift(w)
        at_b = await _on_shift(w, someone, site="site_b")
        nowhere = await _on_shift(w, someone, site=None)
        not_started = uuid.uuid4()
        await _sql("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end) "
                   "VALUES (:i,:t,:s,:g, now() + interval '1 hour', now() + interval '9 hours')",
                   {"i": not_started, "t": w["tenant"], "s": w["site_a"], "g": w["users"][GUARD]})
        url = f"{BASE}/shift-summaries"

        async def draft(who, shift):
            return await c.post(url, headers=who, json={"shift_id": str(shift)})

        theirs = await draft(someone_h, mine)
        assert theirs.status_code == 403 and "guard whose shift it is" in theirs.json()["detail"]
        assert (await draft(w["h"][OPERATOR], mine)).status_code == 403, "reads handovers; does not manage them"
        assert (await draft(w["h"][VIEWER], mine)).status_code == 403
        assert (await draft(w["h"][SUPERVISOR], at_b)).status_code == 404, "held to site A"
        assert (await draft(w["h"][SUPERVISOR], nowhere)).status_code == 404
        assert (await draft(other["h"][ADMIN], mine)).status_code == 404
        assert (await draft(w["h"][ADMIN], uuid.uuid4())).status_code == 404
        early = await draft(w["h"][GUARD], not_started)
        assert early.status_code == 409 and "has not started" in early.json()["detail"]
        assert (await draft(w["h"][SUPERVISOR], mine)).status_code == 201
        roving = await draft(someone_h, nowhere)
        assert roving.status_code == 201, roving.text
    facts = roving.json()["facts"]
    assert facts["visitors"] is None and facts["instructions"] == [] and facts["shift"]["site_name"] is None
    assert "VISITORS" not in roving.json()["drafted_text"]
    key = TokenPayload(user_id=str(w["users"][GUARD]), tenant_id=str(w["tenant"]), role_id=GUARD, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.post(url, json={"shift_id": str(mine)})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_token_payload, None)


# ─── G. What the application role and the database refuse ────────────────────

async def test_what_the_application_role_cannot_do_to_the_book():
    w, other = await _world(), await _world()
    async with _client() as c:
        entry = await _write(c, w, "Fence light out.")
        await c.post(f"{BASE}/entries/{entry['id']}/review", headers=w["h"][SUPERVISOR], json={})
        await c.post(f"{BASE}/entries/{entry['id']}/correct", headers=w["h"][GUARD], json={"body": "Two lights out.", "reason": "Miscounted."})
        note = (await c.post(f"{BASE}/instructions", headers=w["h"][ADMIN],
                             json={"site_id": str(w["site_a"]), "body": "No hot work."})).json()
        await c.post(f"{BASE}/instructions/{note['id']}/read", headers=w["h"][GUARD])
        shift = await _on_shift(w)
        made = (await c.post(f"{BASE}/shift-summaries", headers=w["h"][GUARD], json={"shift_id": str(shift)})).json()
        await c.post(f"{BASE}/shift-summaries/{made['id']}/confirm", headers=w["h"][GUARD])
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        scoped = "tenant_id = current_setting('app.current_tenant')::uuid"
        for statement, refusal in (
            (f"UPDATE occurrence_entry_reviews SET note = 'rewritten' WHERE {scoped}", "permission denied"),
            (f"DELETE FROM occurrence_entry_reviews WHERE {scoped}", "permission denied"),
            (f"UPDATE occurrence_entry_corrections SET reason = 'rewritten' WHERE {scoped}", "permission denied"),
            (f"DELETE FROM occurrence_entry_corrections WHERE {scoped}", "permission denied"),
            (f"DELETE FROM site_instructions WHERE {scoped}", "permission denied"),
            (f"UPDATE site_instructions SET body = 'rewritten' WHERE {scoped}", "permission denied"),
            (f"DELETE FROM site_instruction_reads WHERE {scoped}", "permission denied"),
            (f"DELETE FROM shift_handover_summaries WHERE {scoped}", "permission denied"),
            (f"UPDATE shift_handover_summaries SET drafted_text = 'rewritten' WHERE {scoped}", "permission denied"),
            (f"UPDATE shift_handover_summaries SET facts = '{{}}'::jsonb WHERE {scoped}", "permission denied"),
            # What it MAY change of a summary, it may not change once the summary is confirmed.
            (f"UPDATE shift_handover_summaries SET final_text = 'rewritten' WHERE {scoped}", "confirmed is not changed"),
            (f"UPDATE shift_handover_summaries SET state = 'DRAFT', confirmed_at = NULL WHERE {scoped}",
             "confirmed is not changed"),
        ):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match=refusal):
                await db.execute(text(statement))
            await db.rollback()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        for table in TABLES:
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0, table
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text("INSERT INTO site_instructions (tenant_id, site_id, body) VALUES (:t, :s, 'Planted')"),
                             {"t": w["tenant"], "s": w["site_a"]})
        await db.rollback()
    for table in TABLES:
        row = (await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n", {"n": table}))[0]
        assert row["relrowsecurity"] and row["relforcerowsecurity"], table
        can = (await _sql("SELECT has_table_privilege('svc_app', :n, 'DELETE') AS d, "
                          "has_table_privilege('svc_app', :n, 'UPDATE') AS u", {"n": table}))[0]
        assert not can["d"] and not can["u"], f"{table}: no DELETE, and no UPDATE of the whole row"
    assert "GRANT ALL" not in MIGRATION and MIGRATION.count("REVOKE ALL ON {table} FROM svc_app") == 1


async def test_what_the_database_refuses_of_a_review_an_instruction_and_a_summary():
    w = await _world()
    t, s, guard = w["tenant"], w["site_a"], w["users"][GUARD]
    (entry,) = await _sql("INSERT INTO occurrence_book_entries (tenant_id, site_id, author_user_id, body) "
                          "VALUES (:t,:s,:g,'An entry') RETURNING id", {"t": t, "s": s, "g": guard})
    (second,) = await _sql("INSERT INTO occurrence_book_entries (tenant_id, site_id, author_user_id, body) "
                           "VALUES (:t,:s,:g,'A correction') RETURNING id", {"t": t, "s": s, "g": guard})
    shift = await _on_shift(w)
    e = entry["id"]
    review = "INSERT INTO occurrence_entry_reviews (tenant_id, entry_id, outcome{more}) VALUES (:t, :e, :o{values})"
    for outcome, more, values, constraint in (("SHRUG", ", note", ", 'x'", "ck_dobreview_outcome"),
                                              ("FOLLOW_UP", "", "", "ck_dobreview_note"),
                                              ("CLOSED", ", note", ", '  '", "ck_dobreview_note")):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(review.format(more=more, values=values), {"t": t, "e": e, "o": outcome})
    link = ("INSERT INTO occurrence_entry_corrections (tenant_id, entry_id, corrects_entry_id, reason) "
            "VALUES (:t, :new, :old, :why)")
    for new, old, why, constraint in ((e, e, "Itself", "ck_dobcorrection_other"), (second["id"], e, " ", "ck_dobcorrection_reason")):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(link, {"t": t, "new": new, "old": old, "why": why})
    await _sql(link, {"t": t, "new": second["id"], "old": e, "why": "Miscounted"})
    with pytest.raises(DBAPIError, match="uq_dobcorrection_entry"):
        await _sql(link, {"t": t, "new": second["id"], "old": e, "why": "Again"})
    note = "INSERT INTO site_instructions (tenant_id, site_id, body{more}) VALUES (:t, :s, :b{values})"
    for body, more, values, constraint in (
        (" ", "", "", "ck_instruction_body"),
        ("x", ", expires_at", ", now() - interval '1 hour'", "ck_instruction_expiry"),
        ("x", ", closed_at", ", now()", "ck_instruction_closed"),
        ("x", ", close_note", ", 'Closed without a time'", "ck_instruction_closed"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(note.format(more=more, values=values), {"t": t, "s": s, "b": body})
    made = ("INSERT INTO shift_handover_summaries (tenant_id, shift_id, period_start, period_end, facts, drafted_text, "
            "final_text{more}) VALUES (:t, :sh, :a, :b, '{{}}'::jsonb, 'Drafted', :final{values})")
    now = datetime.now(timezone.utc)
    ok = {"t": t, "sh": shift, "a": now - timedelta(hours=1), "b": now, "final": "Drafted"}
    for change, more, values, constraint in (
        ({"final": "  "}, "", "", "ck_shiftsummary_text"),
        ({"b": now - timedelta(hours=2)}, "", "", "ck_shiftsummary_period"),
        ({}, ", state", ", 'PUBLISHED'", "ck_shiftsummary_state"),
        ({}, ", state", ", 'CONFIRMED'", "ck_shiftsummary_confirmed"),
        ({}, ", confirmed_at", ", now()", "ck_shiftsummary_confirmed"),
        # No language model writes here: the column allows one method and no other.
        ({}, ", method", ", 'LLM'", "ck_shiftsummary_method"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(made.format(more=more, values=values), {**ok, **change})
    await _sql(made.format(more="", values=""), ok)
    with pytest.raises(DBAPIError, match="uq_shiftsummary_live"):
        await _sql(made.format(more="", values=""), ok)


async def test_a_person_who_leaves_does_not_take_a_confirmed_summary_with_them():
    w = await _world()
    gone, gone_h = await _guard(w, "Leaving Guard")
    async with _client() as c:
        shift = await _on_shift(w, gone)
        made = (await c.post(f"{BASE}/shift-summaries", headers=gone_h, json={"shift_id": str(shift)})).json()
        assert (await c.post(f"{BASE}/shift-summaries/{made['id']}/confirm", headers=gone_h)).status_code == 200
    # The housekeeping that removes a person: the summary stays, without their name on it.
    await _run([("DELETE FROM audit_logs WHERE user_id = :u", {"u": gone}),
                ("UPDATE shifts SET guard_user_id = :a WHERE id = :s", {"a": w["users"][GUARD], "s": shift}),
                ("DELETE FROM users WHERE id = :u", {"u": gone})])
    (kept,) = await _sql("SELECT state, guard_user_id, confirmed_by_user_id, final_text FROM shift_handover_summaries "
                         "WHERE id = :i", {"i": made["id"]})
    assert kept["state"] == "CONFIRMED" and kept["guard_user_id"] is None and kept["confirmed_by_user_id"] is None
    assert kept["final_text"] == made["drafted_text"]


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
            if path.startswith(BASE) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.replace(":uuid", "").removeprefix(BASE))] = _needs(route)
    read, write, review, handover = "dob:read", "dob:write", "dob:review", "handover:read"
    assert served == {
        ("GET", "/kinds"): {read}, ("GET", "/entries"): {read}, ("GET", "/entries/{entry_id}"): {read},
        ("POST", "/entries/{entry_id}/review"): {read, review}, ("POST", "/entries/review"): {read, review},
        ("POST", "/entries/{entry_id}/correct"): {read, write},
        ("GET", "/instructions"): {handover}, ("POST", "/instructions"): {handover, review},
        ("POST", "/instructions/{instruction_id}/read"): {handover},
        ("POST", "/instructions/{instruction_id}/close"): {handover, review},
        ("GET", "/shift-summaries"): {handover}, ("POST", "/shift-summaries"): {handover},
        ("GET", "/shift-summaries/{summary_id}"): {handover}, ("PATCH", "/shift-summaries/{summary_id}"): {handover},
        ("POST", "/shift-summaries/{summary_id}/confirm"): {handover},
        ("POST", "/shift-summaries/{summary_id}/discard"): {handover},
    }


async def test_who_holds_the_permission_to_review():
    rows = await _sql("SELECT array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code = 'dob:review'")
    assert list(rows[0]["roles"]) == [2, 3, 8]
    assert _auth and FakeRedis
