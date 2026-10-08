"""Where a person appears in what the enterprise expansion keeps, and how often.

Somebody asks what the organisation holds about them. The account, its
sessions and its audit entries have an export of their own, and attendance,
rosters, leave, pay, training and violations each have their screen. What the
expansion added - investigations, evidence packages, responses, the occurrence
book's reviews, procedures, authorisations of visits, maintenance, answers to
advice, briefings, cases - had no way to be asked the same question at once.

IT SAYS WHERE AND HOW OFTEN, NOT WHAT. For each kind of record: how many name
the person, as what, and between which dates. It does not hand the records
over: a case's history or an investigation's notes hold other people too, and
what of a record is one person's is for somebody to judge, reading it on its
own screen under its own permission.

EVERY COLUMN THAT REFERS TO A PERSON IS READ. `STAFF` lists each column of the
expansion's tables that refers to a member of staff, with what it means to be
named there. A test compares the list with the database's own catalogue, so a
column added later cannot be left out without that test saying so.

ABOUT AND BY are told apart. A record is ABOUT somebody when it concerns them -
the guard who was sent, the host of a visit, whoever was given a task. It is BY
them when they are the one who did the step.

A NAME IS TEXT. A name or a number plate written into a case, or the name of a
vendor on a work order, is found by the words typed, and what is found is text
that matches - not an identification of anybody. A name written in a title or
a note is not looked for.

WHO LOOKED FOR THEM is part of the answer: each investigation search was
already written to the audit log with what it asked, and this counts those
that asked about the person, a plate or the words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ABOUT, BY = "ABOUT", "BY"
PART_WORDS = {ABOUT: "Records that concern them", BY: "Steps they took"}
#: The fewest and the most characters of a name or a plate that is looked for.
TEXT_MIN, TEXT_MAX = 3, 80
#: The most distinct pieces of matching text shown.
MATCHES_SHOWN = 20

WHAT_IT_IS = ("This says where a person appears and how often, not what each record says. Each record is read on its "
              "own screen, under its own permission, by somebody who can judge what of it is that person's.")
NOT_READ = (
    "The account, its sessions and its audit entries: those are in the existing data-subject export.",
    "Attendance, rosters, leave, pay, training and violations: each is on its own screen.",
    "What the cameras saw: a face or a plate is looked for in Smart Investigation.",
    "A name written in a title or a note.",
)
TEXT_IS_TEXT = "What is found is text that matches what was typed. It does not identify anybody."


@dataclass(frozen=True)
class Held:
    """One table, and the columns of it that refer to a member of staff."""
    table: str
    label: str
    #: The column a row's time is read from.
    when: str
    #: column -> (ABOUT or BY, what it means to be named there)
    columns: dict[str, tuple[str, str]]


STAFF: tuple[Held, ...] = (
    Held("investigations", "Investigations", "opened_at", {
        "opened_by_user_id": (BY, "Opened it"), "closed_by_user_id": (BY, "Closed it")}),
    Held("investigation_items", "Items put into investigations", "added_at", {
        "added_by_user_id": (BY, "Put the item in"), "set_aside_by_user_id": (BY, "Set the item aside")}),
    Held("evidence_packages", "Evidence packages", "created_at", {
        "created_by_user_id": (BY, "Made it"), "sealed_by_user_id": (BY, "Sealed it")}),
    Held("evidence_package_items", "Items put into evidence packages", "added_at", {
        "added_by_user_id": (BY, "Put the item in")}),
    Held("evidence_holds", "Holds on evidence", "placed_at", {
        "placed_by_user_id": (BY, "Placed the hold"), "released_by_user_id": (BY, "Released the hold")}),
    Held("evidence_custody_events", "Custody of evidence", "occurred_at", {
        "actor_user_id": (BY, "Took the custody step")}),
    Held("site_places", "The places of a site", "created_at", {
        "created_by_user_id": (BY, "Added the place"), "updated_by_user_id": (BY, "Last changed the place")}),
    Held("incident_responses", "Guards sent to incidents", "dispatched_at", {
        "guard_user_id": (ABOUT, "Was sent to the incident"),
        "stood_down_by_user_id": (BY, "Stood the guard down")}),
    Held("incident_response_steps", "Steps of a response", "occurred_at", {
        "actor_user_id": (BY, "Took the step")}),
    Held("escalation_policies", "Escalation policies", "created_at", {
        "notify_user_id": (ABOUT, "Is the person the policy tells"),
        "created_by_user_id": (BY, "Wrote the policy"), "updated_by_user_id": (BY, "Last changed the policy")}),
    Held("incident_escalations", "Escalations", "created_at", {
        "notify_user_id": (ABOUT, "Was the person told")}),
    Held("occurrence_entry_reviews", "Reviews of occurrence book entries", "reviewed_at", {
        "reviewer_user_id": (BY, "Reviewed the entry")}),
    Held("occurrence_entry_corrections", "Corrections of occurrence book entries", "created_at", {
        "created_by_user_id": (BY, "Wrote the correction")}),
    Held("site_instructions", "Standing instructions", "issued_at", {
        "issued_by_user_id": (BY, "Issued the instruction"), "closed_by_user_id": (BY, "Closed the instruction")}),
    Held("site_instruction_reads", "Instructions read", "read_at", {
        "user_id": (ABOUT, "Read the instruction")}),
    Held("shift_handover_summaries", "Shift summaries", "drafted_at", {
        "guard_user_id": (ABOUT, "It is the summary of their shift"),
        "drafted_by_user_id": (BY, "Asked for the draft"), "confirmed_by_user_id": (BY, "Confirmed it")}),
    Held("sop_documents", "Procedures", "created_at", {
        "created_by_user_id": (BY, "Started the procedure"), "retired_by_user_id": (BY, "Retired the procedure")}),
    Held("sop_versions", "Versions of procedures", "drafted_at", {
        "drafted_by_user_id": (BY, "Drafted the version"),
        "decided_by_user_id": (BY, "Approved or rejected the version")}),
    Held("visitor_authorizations", "Authorisations of visits and work", "requested_at", {
        "host_user_id": (ABOUT, "Is the host"), "escort_user_id": (ABOUT, "Is the escort"),
        "requested_by_user_id": (BY, "Asked for it"), "decided_by_user_id": (BY, "Approved or declined it"),
        "id_checked_by_user_id": (BY, "Checked the ID"), "extended_by_user_id": (BY, "Extended it"),
        "cancelled_by_user_id": (BY, "Cancelled it")}),
    Held("visitor_movement_reviews", "Reviews of where a badge was used", "reviewed_at", {
        "reviewed_by_user_id": (BY, "Reviewed it")}),
    Held("asset_register", "The asset register", "created_at", {
        "created_by_user_id": (BY, "Registered the asset"), "updated_by_user_id": (BY, "Last changed the asset"),
        "retired_by_user_id": (BY, "Retired the asset")}),
    Held("maintenance_schedules", "Maintenance schedules", "created_at", {
        "created_by_user_id": (BY, "Wrote the schedule"), "updated_by_user_id": (BY, "Last changed the schedule")}),
    Held("maintenance_work_orders", "Maintenance work orders", "raised_at", {
        "assigned_to_user_id": (ABOUT, "Was given the work"),
        "raised_by_user_id": (BY, "Raised it"), "accepted_by_user_id": (BY, "Accepted the suggestion"),
        "started_by_user_id": (BY, "Started the work"), "completed_by_user_id": (BY, "Completed the work"),
        "closed_by_user_id": (BY, "Cancelled or dismissed it")}),
    Held("risk_advice_answers", "Answers to risk advice", "answered_at", {
        "answered_by_user_id": (BY, "Answered the advice")}),
    Held("daily_briefings", "Daily briefings", "drafted_at", {
        "drafted_by_user_id": (BY, "Drafted it"), "published_by_user_id": (BY, "Published it"),
        "discarded_by_user_id": (BY, "Set it aside")}),
    Held("workforce_advice_answers", "Answers to workforce recommendations", "answered_at", {
        "subject_user_id": (ABOUT, "The recommendation is about them"),
        "answered_by_user_id": (BY, "Answered the recommendation")}),
    Held("case_files", "Cases", "opened_at", {
        "lead_user_id": (ABOUT, "Leads the case"),
        "opened_by_user_id": (BY, "Opened it"), "close_requested_by_user_id": (BY, "Asked for it to be closed"),
        "closed_by_user_id": (BY, "Approved its closing")}),
    Held("case_investigators", "Investigators on cases", "added_at", {
        "user_id": (ABOUT, "Is, or was, an investigator on the case"),
        "added_by_user_id": (BY, "Put the investigator on"), "removed_by_user_id": (BY, "Took the investigator off")}),
    Held("case_tasks", "Tasks on cases", "created_at", {
        "assigned_to_user_id": (ABOUT, "Was given the task"),
        "created_by_user_id": (BY, "Gave the task"), "done_by_user_id": (BY, "Finished or dropped the task")}),
    Held("case_entries", "The history of cases", "occurred_at", {
        "actor_user_id": (BY, "Took the step")}),
    Held("case_links", "Records linked to cases", "linked_at", {
        "linked_by_user_id": (BY, "Linked the record"), "removed_by_user_id": (BY, "Took the link off")}),
    Held("case_parties", "People and vehicles named in cases", "added_at", {
        "added_by_user_id": (BY, "Wrote the name in"), "removed_by_user_id": (BY, "Took the name off")}),
)

#: The tables whose rows refer to a visitor, by the path to the visitor.
VISITOR_TABLES = ("visitor_authorizations", "visitor_authorization_places", "visitor_movement_reviews")
#: table -> the columns that hold a name or a plate as somebody typed it.
TEXT_COLUMNS = {"case_parties": ("label",), "maintenance_work_orders": ("assigned_to_name",)}


def names_people(table: str) -> bool:
    """Whether a table of the expansion refers to a person: staff, a visitor, or a name as text."""
    return table in {h.table for h in STAFF} or table in VISITOR_TABLES or table in TEXT_COLUMNS


def like(words: str) -> str:
    """The words as a pattern that matches them anywhere, with nothing in them read as a wildcard."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", words) + "%"


def plate(words: str) -> str:
    """The words as a number plate is kept: upper case, letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", words.upper())


def _line(part: str, words: str, column: str, row) -> dict:
    return {"column": column, "part": part, "words": words, "count": row["n"], "first": row["first_at"],
            "last": row["last_at"]}


async def _searches(db: AsyncSession, where: str, params: dict) -> dict:
    """Investigation searches the audit log holds that asked about the subject."""
    row = (await db.execute(text(f"""
        SELECT count(*) AS n, count(DISTINCT user_id) AS people, min(created_at) AS first_at, max(created_at) AS last_at
          FROM audit_logs
         WHERE resource_type = 'investigation_search'
           AND action IN ('investigation.search', 'investigation.trail') AND ({where})
    """), params)).mappings().one()
    return {"count": row["n"], "by_people": row["people"], "first": row["first_at"], "last": row["last_at"],
            "words": "Investigation searches that asked about them. Who searched, and when, is in the audit log."}


async def staff(db: AsyncSession, user_id: str) -> dict | None:
    """Where one member of staff appears. None when there is nobody of that id in the organisation."""
    who = (await db.execute(text("""
        SELECT u.id, u.full_name, u.is_active, r.name AS role_name
          FROM users u LEFT JOIN roles r ON r.id = u.role_id WHERE u.id = CAST(:u AS uuid)
    """), {"u": user_id})).mappings().first()
    if who is None:
        return None
    # One statement for every column. The names are this module's own, never a caller's.
    parts = [f"SELECT '{h.table}' AS t, '{column}' AS c, count(*) AS n, min({h.when}) AS first_at, "
             f"max({h.when}) AS last_at FROM {h.table} WHERE {column} = CAST(:u AS uuid)"
             for h in STAFF for column in h.columns]
    rows = {(r["t"], r["c"]): r for r in (await db.execute(
        text(" UNION ALL ".join(parts)), {"u": user_id})).mappings()}
    held, nothing_in, totals = [], [], {ABOUT: 0, BY: 0}
    for h in STAFF:
        lines = [_line(part, words, column, rows[(h.table, column)])
                 for column, (part, words) in h.columns.items() if rows[(h.table, column)]["n"]]
        for line in lines:
            totals[line["part"]] += line["count"]
        if lines:
            held.append({"table": h.table, "label": h.label, "lines": lines})
        else:
            nothing_in.append(h.label)
    return {
        "subject": {"kind": "STAFF", "id": str(who["id"]), "name": who["full_name"], "role": who["role_name"],
                    "in_use": who["is_active"]},
        "held": held, "nothing_in": nothing_in,
        "totals": {"about": totals[ABOUT], "by": totals[BY], "kinds_of_record": len(held)},
        "searches": await _searches(db, "detail->'query'->>'staff_user_id' = :u", {"u": user_id}),
        "parts": dict(PART_WORDS), "what_it_is": WHAT_IT_IS, "not_read": list(NOT_READ),
    }


async def visitor(db: AsyncSession, visitor_id: str) -> dict | None:
    """Where one visitor appears. None when there is no visitor of that id in the organisation."""
    who = (await db.execute(text(
        "SELECT id, full_name, company, site_id FROM visitors WHERE id = CAST(:v AS uuid)"),
        {"v": visitor_id})).mappings().first()
    if who is None:
        return None
    rows = (await db.execute(text("""
        SELECT 'visitor_authorizations' AS t, count(*) AS n, min(requested_at) AS first_at, max(requested_at) AS last_at
          FROM visitor_authorizations WHERE visitor_id = CAST(:v AS uuid)
        UNION ALL
        SELECT 'visitor_authorization_places', count(*), NULL, NULL
          FROM visitor_authorization_places p JOIN visitor_authorizations a ON a.id = p.authorization_id
         WHERE a.visitor_id = CAST(:v AS uuid)
        UNION ALL
        SELECT 'visitor_movement_reviews', count(*), min(m.reviewed_at), max(m.reviewed_at)
          FROM visitor_movement_reviews m JOIN visitor_authorizations a ON a.id = m.authorization_id
         WHERE a.visitor_id = CAST(:v AS uuid)
    """), {"v": visitor_id})).mappings().all()
    words = {"visitor_authorizations": ("Authorisations of visits and work", "The authorisation is of their visit"),
             "visitor_authorization_places": ("The places a visit is for", "A place their visit is authorised for"),
             "visitor_movement_reviews": ("Reviews of where a badge was used",
                                          "A review of where the badge they were given was used")}
    held = [{"table": r["t"], "label": words[r["t"]][0],
             "lines": [_line(ABOUT, words[r["t"]][1], "visitor_id", r)]} for r in rows if r["n"]]
    return {
        "subject": {"kind": "VISITOR", "id": str(who["id"]), "name": who["full_name"], "company": who["company"],
                    "site_id": str(who["site_id"]) if who["site_id"] else None},
        "held": held, "nothing_in": [words[r["t"]][0] for r in rows if not r["n"]],
        "totals": {"about": sum(r["n"] for r in rows), "by": 0, "kinds_of_record": len(held)},
        "searches": None,
        "parts": dict(PART_WORDS), "what_it_is": WHAT_IT_IS,
        "not_read": ["The visitor's own record, their visits and the gate's log: those are on the Visitors screen.",
                     *NOT_READ[2:]],
    }


async def written(db: AsyncSession, words: str) -> dict:
    """Where a name or a number plate, as typed, is written in the expansion's records."""
    pattern, as_plate = like(words), plate(words)
    if len(as_plate) < TEXT_MIN:
        as_plate = ""
    # A plate is the same plate however it was spaced when it was written in.
    named = (await db.execute(text("""
        SELECT kind, label, count(*) AS n, count(DISTINCT case_id) AS cases,
               count(*) FILTER (WHERE removed_at IS NOT NULL) AS taken_off,
               min(added_at) AS first_at, max(added_at) AS last_at
          FROM case_parties
         WHERE label ILIKE :p ESCAPE '\\'
            OR (kind = 'VEHICLE' AND :plate <> '' AND regexp_replace(upper(label), '[^A-Z0-9]', '', 'g') = :plate)
         GROUP BY kind, label ORDER BY max(added_at) DESC, label
    """), {"p": pattern, "plate": as_plate})).mappings().all()
    vendors = (await db.execute(text("""
        SELECT assigned_to_name AS label, count(*) AS n, min(raised_at) AS first_at, max(raised_at) AS last_at
          FROM maintenance_work_orders WHERE assigned_to_name ILIKE :p ESCAPE '\\'
         GROUP BY assigned_to_name ORDER BY max(raised_at) DESC, assigned_to_name
    """), {"p": pattern})).mappings().all()
    asked = ["detail->>'phrase' ILIKE :p ESCAPE '\\'", "detail->'query'->>'person' ILIKE :p ESCAPE '\\'",
             "detail->'query'->>'text' ILIKE :p ESCAPE '\\'"]
    params = {"p": pattern}
    if as_plate:
        asked += ["regexp_replace(upper(detail->'query'->>'plate'), '[^A-Z0-9]', '', 'g') = :plate",
                  "detail->'subject'->>'plate' = :plate"]
        params["plate"] = as_plate

    def shown(rows, extra=()):
        return [{"text": r["label"], "count": r["n"], "first": r["first_at"], "last": r["last_at"],
                 **{k: r[k] for k in extra}} for r in rows[:MATCHES_SHOWN]]

    held = []
    if named:
        held.append({"table": "case_parties", "label": "People and vehicles named in cases",
                     "count": sum(r["n"] for r in named), "distinct": len(named),
                     "matches": shown(named, ("kind", "cases", "taken_off"))})
    if vendors:
        held.append({"table": "maintenance_work_orders", "label": "Whoever a work order was given to, by name",
                     "count": sum(r["n"] for r in vendors), "distinct": len(vendors), "matches": shown(vendors)})
    return {
        "subject": {"kind": "TEXT", "text": words, "as_plate": as_plate or None},
        "held": held,
        "nothing_in": [label for table, label in (("case_parties", "People and vehicles named in cases"),
                                                  ("maintenance_work_orders", "Work orders given to somebody by name"))
                       if table not in {h["table"] for h in held}],
        "totals": {"matches": sum(h["count"] for h in held), "kinds_of_record": len(held)},
        "searches": await _searches(db, " OR ".join(asked), params),
        "text_is_text": TEXT_IS_TEXT, "what_it_is": WHAT_IT_IS,
        "not_read": ["A member of staff or a visitor by their record: choose them instead of typing their name.",
                     *NOT_READ[2:]],
        "matches_shown": MATCHES_SHOWN,
    }
