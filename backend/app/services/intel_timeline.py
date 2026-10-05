"""The timeline: one situation, in the order it happened.

  02:17:04  a camera reported          SOURCE     what a source recorded
  02:18:02  risk assessed HIGH         AI         what the layer made of it
  02:18:06  dispatch suggested         AI         what the layer suggested
  02:18:15  officer decided            PERSON     what a person chose
  02:18:17  guard dispatched           PLATFORM   what the platform then did
  02:24:31  guard arrived              PERSON     what was reported from the ground
  02:28:00  incident resolved          PLATFORM   how it ended

MADE OF ROWS THAT ALREADY EXIST. Nothing is stored for the timeline: every entry
is read from the record it describes — an event, an assessment, a suggestion,
a look, a decision, a verdict, a step, a report, the incident's own times — and
carries a reference back to it. So the timeline cannot say something the
records do not, and cannot drift from them.

EVERY ENTRY SAYS WHOSE IT IS. `actor` is SOURCE, AI, PERSON or PLATFORM, and it
is never guessed: it follows from which table the row came from. What the layer
suggested is AI and says `is_decision: false`; what an officer chose is PERSON;
what was carried out is PLATFORM and names the function it went through. A
reader of the timeline can no more mistake a suggestion for a decision than a
reader of the tables can.

REPEATS ARE ONE LINE. A camera that raised the same alert eleven times is one
entry saying so, with the first and the last time; the alerts themselves are
untouched and are all still in the situation.

`build()` IS PURE. It takes the rows and returns the entries, with no database,
so the order and the words are tests that need nothing running. `load()` reads.
Nothing here writes anywhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import intel_decisions as decisions
from app.services import intel_field

ACTORS = ("SOURCE", "AI", "PERSON", "PLATFORM")
#: Every kind of entry, in the order they are put when two share an instant:
#: what happened, then what was made of it, then what people did about it.
KINDS = ("EVENT", "REPEATS", "ASSESSMENT", "RECOMMENDATION", "REVIEW", "DECISION", "APPROVAL", "ACTION",
         "OBSERVATION", "INCIDENT")
ACTOR_OF = {"EVENT": "SOURCE", "REPEATS": "SOURCE", "ASSESSMENT": "AI", "RECOMMENDATION": "AI", "REVIEW": "PERSON",
            "DECISION": "PERSON", "APPROVAL": "PERSON", "ACTION": "PLATFORM", "OBSERVATION": "PERSON",
            "INCIDENT": "PLATFORM"}

#: A decision or a suggested step, as it reads after "decided to" or "suggests".
STEP_WORDS = {
    "MONITOR": "keep watching", "VERIFY": "confirm it another way", "VIEW_CAMERA": "look at the camera",
    "VERIFY_WITH_DRONE": "verify with a drone", "DISPATCH_GUARD": "dispatch a guard", "ESCALATE": "escalate",
    "INVESTIGATE": "investigate", "CONTACT_SITE": "contact the site", "CREATE_INCIDENT": "open an incident",
    "ACKNOWLEDGE": "acknowledge", "CONFIRM_INCIDENT": "confirm the incident",
    "REQUEST_ASSISTANCE": "ask the command centre for help", "FALSE_POSITIVE": "close it as a false positive",
    "RESOLVE": "resolve it",
}
#: A step the platform carried out: what was done, and what it was trying to do.
DONE = {
    "ALERT_ACKNOWLEDGE": "Alerts acknowledged", "ALERT_FALSE_POSITIVE": "Alert marked false",
    "ALERT_DISMISS": "Alerts closed", "ALERT_ASSIGN": "Alerts assigned", "INCIDENT_CREATE": "Incident opened",
    "INCIDENT_CONFIRM": "Incident confirmed by a person", "INCIDENT_DISPATCH": "Guard dispatched",
    "INCIDENT_ASSIGN": "Incident assigned", "INCIDENT_RESOLVE": "Incident resolved",
    "DRONE_HOLD": "Flight asked to hold and look again", "DRONE_LAUNCH": "Mission started",
}
TO_DO = {
    "ALERT_ACKNOWLEDGE": "acknowledge the alerts", "ALERT_FALSE_POSITIVE": "mark the alert false",
    "ALERT_DISMISS": "close the alerts", "ALERT_ASSIGN": "assign the alerts", "INCIDENT_CREATE": "open an incident",
    "INCIDENT_CONFIRM": "confirm the incident", "INCIDENT_DISPATCH": "dispatch the guard",
    "INCIDENT_ASSIGN": "assign the incident", "INCIDENT_RESOLVE": "resolve the incident",
    "DRONE_HOLD": "have the flight hold and look again", "DRONE_LAUNCH": "start the mission",
}
#: A step from a decision and the time on the incident that records the same thing.
SAME_AS = {"INCIDENT_CREATE": "OPENED", "INCIDENT_DISPATCH": "DISPATCHED", "INCIDENT_RESOLVE": "RESOLVED"}
#: How close in time a step and the incident's own record of it are taken to be one.
SAME_WITHIN = timedelta(seconds=30)
ON_THE_GROUND = {"ACCEPTED": "Accepted — on the way", "ARRIVED": "Arrived"}


def _person(user_id: Any, name: Any, role_id: Any) -> dict:
    return {"user_id": user_id, "name": name, "role_id": role_id}


def _entry(at: datetime, kind: str, title: str, detail: str | None = None, *, ref_type: str, ref_id: Any,
           who: dict | None = None, order: int = 0, **extra) -> dict:
    return {"at": at, "kind": kind, "actor": ACTOR_OF[kind], "title": title, "detail": detail, "who": who,
            "ref": {"type": ref_type, "id": ref_id}, "_order": order, **extra}


# ─── Each record, as an entry ────────────────────────────────────────────────

def _events(events: list[Mapping]) -> list[dict]:
    """What the sources reported. A repeat is not an entry of its own: the
    repeats of one alert from one place are one line."""
    out: list[dict] = []
    repeats: dict[tuple, list[Mapping]] = {}
    for e in events:
        if e.get("is_duplicate"):
            where = str(e.get("camera_id") or e.get("location_label") or "")
            repeats.setdefault((e.get("source_type"), e.get("event_type"), where), []).append(e)
            continue
        first = e.get("method") == "FIRST_EVENT"
        out.append(_entry(e["occurred_at"], "EVENT", str(e["title"]),
                          None if first else e.get("reason"), ref_type="event", ref_id=e["id"],
                          source_type=e.get("source_type"), where=e.get("camera_name") or e.get("location_label"),
                          first=first))
    for (source_type, _, _), group in repeats.items():
        group.sort(key=lambda e: e["occurred_at"])
        n, head, tail = len(group), group[0], group[-1]
        # When the last one was is given as a time, `until`, for the screen to
        # show in the reader's own zone; it is not written into the sentence.
        out.append(_entry(head["occurred_at"], "REPEATS", f"The same alert again, {n} time(s): {head['title']}",
                          "Folded as repeats. Each is still an alert of its own.",
                          ref_type="event", ref_id=head["id"], source_type=source_type, count=n,
                          where=head.get("camera_name") or head.get("location_label"), until=tail["occurred_at"]))
    return out


def _assessments(assessments: list[Mapping]) -> list[dict]:
    out, before = [], None
    for a in sorted(assessments, key=lambda a: a["sequence"]):
        on = f"On {a['event_count']} event(s)."
        detail = on if before is None else (f"Assessed again. {on} Before: {before['risk_level']} "
                                            f"({before['risk_score']}).")
        out.append(_entry(a["assessed_at"], "ASSESSMENT",
                          f"AI-assisted assessment: {a['label']}. Risk {a['risk_level']} ({a['risk_score']}).",
                          detail, ref_type="assessment", ref_id=a["id"], risk_level=a["risk_level"],
                          risk_score=a["risk_score"], sequence=a["sequence"]))
        before = a
    return out


def _recommendations(recommendations: list[Mapping]) -> list[dict]:
    """One entry for each set of suggestions: the step put first among those
    that could be taken, and how many others were listed."""
    sets: dict[Any, list[Mapping]] = {}
    for r in recommendations:
        sets.setdefault(r["assessment_id"], []).append(r)
    out = []
    for recs in sets.values():
        recs.sort(key=lambda r: r["rank"])
        first = next((r for r in recs if r["available"]), None)
        others = len(recs) - 1
        more = f" {others} other step(s) were also listed." if others else ""
        if first is not None:
            title, detail = f"AI suggests: {STEP_WORDS.get(first['action'], first['action'])}", f"{first['reason']}{more}"
        else:
            first = recs[0]
            title = "AI suggests nothing that can be done right now"
            detail = (f"{STEP_WORDS.get(first['action'], first['action']).capitalize()} was listed as not possible: "
                      f"{first.get('unavailable_reason') or 'no reason was recorded'}{more}")
        out.append(_entry(min(r["created_at"] for r in recs), "RECOMMENDATION", title, detail,
                          ref_type="recommendation", ref_id=first["id"], action=first["action"], is_decision=False))
    return out


def _decision_detail(d: Mapping) -> str:
    reason = d.get("reason") or "no reason was recorded"
    basis = d["basis"]
    if basis == "FOLLOWED":
        said = "Followed what the layer suggested."
    elif basis == "OVERRIDE":
        first = d.get("suggested_action")
        put = f" The layer had put “{STEP_WORDS.get(first, first)}” first." if first else ""
        said = f"An override — {reason}.{put}"
    elif basis == "CLOSING":
        said = f"Closed the situation — {reason}."
    else:
        said = "Their own decision."
    if d.get("decided_on_an_earlier_assessment"):
        said += " Made on an earlier assessment than the latest at the time."
    note = (d.get("note") or "").strip()
    return f"{said} Note: {note}" if note else said


def _action_title(a: Mapping) -> str:
    action, result = a["action"], a["result"]
    if action == "NONE":
        return "Recorded — nothing was carried out by the platform"
    if result == "FAILED":
        return f"Could not {TO_DO.get(action, action)}"
    if result == "SKIPPED":
        return f"Did not need to {TO_DO.get(action, action)}"
    return DONE.get(action, action)


def _decisions(trail: list[Mapping]) -> list[dict]:
    out = []
    for d in trail:
        by = d["decided_by"]
        who = _person(by.get("user_id"), by.get("name"), by.get("role_id"))
        proposed = d["authority"] == "WITH_APPROVAL"
        verb = "Proposed, to wait for approval" if proposed else "Decided"
        out.append(_entry(d["decided_at"], "DECISION", f"{verb}: {STEP_WORDS.get(d['action'], d['action'])}",
                          _decision_detail(d), ref_type="decision", ref_id=d["id"], who=who, action=d["action"],
                          basis=d["basis"], is_override=d["basis"] == "OVERRIDE", via=d.get("via")))
        verdict = d.get("approval")
        if verdict is not None:
            vby = verdict["by"]
            approved = verdict["verdict"] == "APPROVED"
            out.append(_entry(verdict["at"], "APPROVAL",
                              ("Approved: " if approved else "Rejected: ") + STEP_WORDS.get(d["action"], d["action"]),
                              (verdict.get("note") or "").strip() or None, ref_type="decision", ref_id=d["id"],
                              who=_person(vby.get("user_id"), vby.get("name"), vby.get("role_id")),
                              verdict=verdict["verdict"]))
        for a in d.get("actions") or []:
            out.append(_entry(a["executed_at"], "ACTION", _action_title(a), a.get("detail"), ref_type="decision",
                              ref_id=d["id"], order=int(a.get("sequence") or 0), action=a["action"],
                              result=a["result"], through=a.get("through"), target_type=a.get("target_type"),
                              target_id=a.get("target_id")))
    return out


def _reviews(reviews: list[Mapping]) -> list[dict]:
    return [_entry(r["viewed_at"], "REVIEW", "Looked at what was suggested", None, ref_type="assessment",
                   ref_id=r.get("assessment_id"), who=_person(r.get("user_id"), r.get("name"), r.get("role_id")),
                   via=r.get("via")) for r in reviews]


def _observations(observations: list[Mapping]) -> list[dict]:
    out = []
    for o in observations:
        title = ON_THE_GROUND.get(o["kind"]) or f"Reported from the ground: {(o.get('note') or '').strip()}"
        out.append(_entry(o["observed_at"], "OBSERVATION", title, None, ref_type="observation", ref_id=o["id"],
                          who=_person(o.get("user_id"), o.get("name"), o.get("role_id")), report=o["kind"],
                          via=o.get("via"), with_position=o.get("latitude") is not None))
    return out


def _incidents(incidents: list[Mapping], events: list[Mapping], actions: list[dict]) -> list[dict]:
    """The times the incident itself records. One that a step from a decision
    already accounts for is left to that step, which says more; an incident that
    is itself one of the situation's events is not said twice either."""
    said = {(str(a.get("target_id")), SAME_AS[a["action"]]): a["at"] for a in actions
            if a.get("action") in SAME_AS and a.get("result") == "OK" and a.get("target_id") is not None}
    is_event = {str(e["source_id"]) for e in events if e.get("source_table") == "incidents"}
    out = []
    for i in incidents:
        opened = ("Incident opened by the platform itself — not confirmed by a person"
                  if i.get("is_auto_created") else "Incident opened")
        marks = (("OPENED", i.get("created_at"), opened),
                 ("DISPATCHED", i.get("dispatched_at"), "Guard dispatched, on the incident's own record"),
                 ("ARRIVED", i.get("guard_arrived_at"), "Guard's arrival recorded on the incident"),
                 ("ESCALATED", i.get("escalated_at"), "Incident escalated"),
                 ("RESOLVED", i.get("resolved_at"), "Incident resolved"))
        for mark, at, title in marks:
            if at is None or (mark == "OPENED" and str(i["id"]) in is_event):
                continue
            stepped = said.get((str(i["id"]), mark))
            if stepped is not None and abs(stepped - at) <= SAME_WITHIN:
                continue
            out.append(_entry(at, "INCIDENT", title, None, ref_type="incident", ref_id=i["id"], milestone=mark))
    return out


def build(*, events: list[Mapping], assessments: list[Mapping], recommendations: list[Mapping] | None,
          reviews: list[Mapping], decisions_: list[Mapping], observations: list[Mapping],
          incidents: list[Mapping]) -> list[dict]:
    """The situation's entries, oldest first. Pure.

    `recommendations` is None for a reader who may not see what was suggested:
    the suggestions are then left out, and nothing else changes."""
    stepped = _decisions(decisions_)
    entries = (_events(events) + _assessments(assessments) + _recommendations(recommendations or [])
               + _reviews(reviews) + stepped + _observations(observations)
               + _incidents(incidents, events, [e for e in stepped if e["kind"] == "ACTION"]))
    entries.sort(key=lambda e: (e["at"], KINDS.index(e["kind"]), e["_order"]))
    for e in entries:
        del e["_order"]
    return entries


def counts(entries: list[Mapping]) -> dict:
    """How many entries are each actor's."""
    out = {actor: 0 for actor in ACTORS}
    for e in entries:
        out[e["actor"]] += 1
    return out


# ─── Reading ─────────────────────────────────────────────────────────────────

async def load(db: AsyncSession, situation: Mapping, *, with_suggestions: bool) -> list[dict]:
    """Read every record of the situation and put them in order. The session
    must already be scoped to the situation's tenant."""
    sid = situation["id"]
    events = [dict(r) for r in (await db.execute(text("""
        SELECT e.id, e.source_type, e.source_table, e.source_id, e.event_type, e.occurred_at, e.title,
               e.camera_id, cam.name AS camera_name, e.location_label, e.alert_id, e.incident_id,
               l.method, l.reason, l.is_duplicate
          FROM security_situation_events l
          JOIN security_events e ON e.id = l.event_id
          LEFT JOIN cameras cam ON cam.id = e.camera_id
         WHERE l.situation_id = :s
    """), {"s": sid})).mappings().all()]
    assessments = [dict(r) for r in (await db.execute(text(
        "SELECT id, sequence, assessed_at, label, risk_score, risk_level, event_count "
        "  FROM security_assessments WHERE situation_id = :s"), {"s": sid})).mappings().all()]
    recommendations = None
    if with_suggestions:
        recommendations = [dict(r) for r in (await db.execute(text(
            "SELECT id, assessment_id, rank, action, reason, available, unavailable_reason, created_at "
            "  FROM security_recommendations WHERE situation_id = :s"), {"s": sid})).mappings().all()]
    reviews = [dict(r) for r in (await db.execute(text(
        "SELECT r.user_id, u.full_name AS name, r.actor_role AS role_id, r.assessment_id, r.via, r.viewed_at "
        "  FROM security_reviews r LEFT JOIN users u ON u.id = r.user_id WHERE r.situation_id = :s"),
        {"s": sid})).mappings().all()]
    # The incidents this situation has to do with: the one a person opened or
    # confirmed here, any its events carry, and any the platform opened for one
    # of its alerts.
    incidents = [dict(r) for r in (await db.execute(text("""
        SELECT i.id, i.status, i.is_auto_created, i.created_at, i.dispatched_at, i.guard_arrived_at,
               i.escalated_at, i.resolved_at
          FROM incidents i
         WHERE i.id = CAST(:own AS uuid)
            OR i.id IN (SELECT e.incident_id FROM security_situation_events l
                          JOIN security_events e ON e.id = l.event_id
                         WHERE l.situation_id = :s AND e.incident_id IS NOT NULL)
            OR i.alert_id IN (SELECT e.alert_id FROM security_situation_events l
                                JOIN security_events e ON e.id = l.event_id
                               WHERE l.situation_id = :s AND e.alert_id IS NOT NULL)
    """), {"s": sid, "own": str(situation["incident_id"]) if situation.get("incident_id") else None})).mappings().all()]
    return build(events=events, assessments=assessments, recommendations=recommendations, reviews=reviews,
                 decisions_=await decisions.trail(db, sid),
                 observations=await intel_field.observations(db, sid), incidents=incidents)
