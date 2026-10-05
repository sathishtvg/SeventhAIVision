"""The AI-assisted summary: a situation in a paragraph, made only of what is recorded.

  At 02:17 on 5 Oct 2026, a camera reported “Person at Gate 1” at Gate 1,
  Factory A. Within 1 min, 2 more reports joined it: access control (“Access
  denied at the rear door”, 02:17); a drone (“Possible unauthorised person”,
  02:17). The layer assessed it as HIGH risk (65): Access refused, with activity
  seen nearby. Its suggestion was to dispatch a guard. At 02:18, Priya
  (Operator) decided to dispatch a guard, following what the layer suggested.
  The platform then carried out: Incident opened; Guard dispatched. At 02:24,
  Tan Wei Ming (Guard) reported from the ground: arrived. …

TEMPLATES OVER RECORDS, NOT A LANGUAGE MODEL. There is none in the platform, and
none is used here (owner decision D3). Each sentence is a fixed form filled from
entries of the situation's timeline, which are themselves read from stored
rows — so a sentence cannot state what was not recorded, and each one carries
the references of the records it was made from.

IT SAYS WHAT IT IS. The summary is labelled AI-assisted wherever it goes
(`is_ai_assisted`, `label`). It never states intent, never calls a person
anything a source did not, and quotes what a source or a person wrote as their
words, in quotation marks.

WHAT THE READER MAY NOT SEE IS NOT SUMMARISED. Built from the reader's own
timeline: someone who may not read suggestions gets a summary without them.

`summarise()` is pure. Nothing here reads or writes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping
from zoneinfo import ZoneInfo

from app.services.intel_timeline import STEP_WORDS

LABEL = "AI-assisted summary"
MADE_OF = "Made only of what is recorded. Every sentence is read from the records listed with it."
DEFAULT_TZ = "Asia/Singapore"
#: How many further reports are named before the rest are counted.
NAMED_REPORTS = 4
#: How many sentences a summary runs to before it points at the timeline.
MAX_SENTENCES = 24

SOURCE_WORDS = {
    "CCTV_AI": "a camera", "DRONE_PATROL": "a drone", "VIRTUAL_PATROL": "an officer on a virtual patrol",
    "LPR": "number plate recognition", "FACE_RECOGNITION": "face recognition", "ACCESS_CONTROL": "access control",
    "ALARM": "an alarm panel", "GUARD": "a guard", "SENSOR": "a sensor", "SYSTEM": "the platform itself",
    "OTHER": "another source",
}
ROLE_WORDS = {1: "Platform owner", 2: "Admin", 3: "Supervisor", 4: "Operator", 5: "Guard", 6: "Viewer", 7: "Client",
              8: "Manager"}
STANDS = {
    "AWAITING": "nobody has decided on it yet", "ACKNOWLEDGED": "it has been acknowledged", "IN_HAND": "it is in hand",
    "PENDING_APPROVAL": "a decision on it is waiting for approval",
    "ASSISTANCE_REQUESTED": "assistance has been requested", "RESOLVED": "resolved", "FALSE_POSITIVE": "a false positive",
}
BASIS_WORDS = {"FOLLOWED": ", following what the layer suggested", "INDEPENDENT": ", as their own decision"}
REPORT_WORDS = {"ACCEPTED": "accepted, on the way", "ARRIVED": "arrived"}


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:  # noqa: BLE001 — an unknown zone name must not stop a summary
        return ZoneInfo(DEFAULT_TZ)


def _who(entry: Mapping) -> str:
    who = entry.get("who") or {}
    name = who.get("name") or "A former user"
    role = ROLE_WORDS.get(who.get("role_id"))
    return f"{name} ({role})" if role else name


def _span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{round(seconds / 3600)} h"


def _upper_first(text_: str) -> str:
    return text_[:1].upper() + text_[1:]


def summarise(situation: Mapping, entries: list[Mapping], assessments: list[Mapping],
              timezone_name: str | None = None) -> dict:
    """The summary of one situation, from its timeline entries (oldest first)
    and its assessments. Pure: the same records, the same words."""
    tz = _zone(timezone_name)
    sentences: list[dict] = []

    def clock(at: datetime) -> str:
        return f"{at.astimezone(tz):%H:%M}"

    def say(text_: str, *refs: Mapping) -> None:
        sentences.append({"text": text_, "refs": [dict(r) for r in refs if r]})

    # ── What was reported ────────────────────────────────────────────────────
    reports = [e for e in entries if e["kind"] == "EVENT"]
    if reports:
        first = reports[0]
        local = first["at"].astimezone(tz)
        place = ", ".join(str(p) for p in (first.get("where"), situation.get("site_name")) if p)
        say(f"At {local:%H:%M} on {local.day} {local:%b %Y}, {SOURCE_WORDS.get(first.get('source_type'), 'a source')} "
            f"reported “{first['title']}”" + (f" at {place}." if place else "."), first["ref"])
        rest = reports[1:]
        if rest:
            named = "; ".join(f"{SOURCE_WORDS.get(e.get('source_type'), 'a source')} (“{e['title']}”, {clock(e['at'])})"
                              for e in rest[:NAMED_REPORTS])
            more = f"; and {len(rest) - NAMED_REPORTS} more" if len(rest) > NAMED_REPORTS else ""
            within = _span((rest[-1]["at"] - first["at"]).total_seconds())
            say(f"Within {within}, {len(rest)} more report(s) joined it: {named}{more}.", *[e["ref"] for e in rest])
    repeats = [e for e in entries if e["kind"] == "REPEATS"]
    if repeats:
        total = sum(int(e.get("count") or 0) for e in repeats)
        say(f"The same alert repeated {total} time(s); the repeats were folded and each is still an alert of its own.",
            *[e["ref"] for e in repeats])

    # ── What the layer made of it ────────────────────────────────────────────
    ordered = sorted(assessments, key=lambda a: a["sequence"])
    if ordered:
        latest = ordered[-1]
        ref = {"type": "assessment", "id": latest["id"]}
        text_ = f"The layer assessed it as {latest['risk_level']} risk ({latest['risk_score']}): {latest['label']}."
        if len(ordered) > 1:
            was = ordered[0]
            text_ += (f" That is its assessment number {len(ordered)}; the first, at {clock(was['assessed_at'])}, "
                      f"was {was['risk_level']} ({was['risk_score']}).")
        say(text_, ref, {"type": "assessment", "id": ordered[0]["id"]} if len(ordered) > 1 else None)
    else:
        say("The layer has not assessed it yet.")
    suggested = [e for e in entries if e["kind"] == "RECOMMENDATION"]
    if suggested:
        last = suggested[-1]
        if last["title"].startswith("AI suggests nothing"):
            say("The layer suggested nothing that could be done at the time.", last["ref"])
        else:
            say(f"Its suggestion was to {STEP_WORDS.get(last.get('action'), 'take a step')}. A suggestion is not a "
                f"decision.", last["ref"])

    # ── What people did, and what the platform then did ──────────────────────
    after = [e for e in entries if e["kind"] in ("DECISION", "APPROVAL", "ACTION", "OBSERVATION", "INCIDENT")]
    i = 0
    while i < len(after):
        e = after[i]
        if e["kind"] == "DECISION":
            words = STEP_WORDS.get(e.get("action"), "take a step")
            proposed = e["title"].startswith("Proposed")
            verb = "proposed to" if proposed else "decided to"
            tail = BASIS_WORDS.get(e.get("basis"), "")
            text_ = f"At {clock(e['at'])}, {_who(e)} {verb} {words}{tail}"
            text_ += ", to wait for a second person's approval." if proposed else "."
            if e.get("basis") in ("OVERRIDE", "CLOSING") and e.get("detail"):
                text_ += f" {e['detail']}"
            say(text_, e["ref"])
        elif e["kind"] == "APPROVAL":
            approved = e.get("verdict") == "APPROVED"
            note = f" Their note: “{e['detail']}”" if e.get("detail") else ""
            say(f"At {clock(e['at'])}, {_who(e)} {'approved' if approved else 'rejected'} it.{note}", e["ref"])
        elif e["kind"] == "ACTION":
            # Every step of one decision, said together.
            steps = [e]
            while i + 1 < len(after) and after[i + 1]["kind"] == "ACTION" and after[i + 1]["ref"] == e["ref"]:
                i += 1
                steps.append(after[i])
            done = [s["title"] for s in steps if s.get("result") in ("OK", "RECORDED") and s.get("action") != "NONE"]
            skipped = [s["title"] for s in steps if s.get("result") == "SKIPPED"]
            failed = [s for s in steps if s.get("result") == "FAILED"]
            parts = []
            if done:
                parts.append(f"The platform then carried out: {'; '.join(done)}.")
            for s in failed:
                parts.append(f"{s['title']}" + (f" ({s['detail']})." if s.get("detail") else "."))
            if skipped:
                parts.append(f"{'; '.join(skipped)}.")
            if parts:
                say(" ".join(parts), e["ref"])
        elif e["kind"] == "OBSERVATION":
            report = e.get("report")
            if report in REPORT_WORDS:
                said = REPORT_WORDS[report] + "."
            else:
                # Their words, as they wrote them — full stop and all.
                words = e["title"].removeprefix("Reported from the ground: ")
                said = f"“{words}”" + ("" if words.endswith((".", "!", "?")) else ".")
            say(f"At {clock(e['at'])}, {_who(e)} reported from the ground: {said}", e["ref"])
        else:
            say(f"At {clock(e['at'])}: {_upper_first(e['title'])}.", e["ref"])
        i += 1

    # ── Where it stands ──────────────────────────────────────────────────────
    status = situation.get("decision_status") or "AWAITING"
    if situation.get("closed_at") is not None:
        say(f"The situation was closed at {clock(situation['closed_at'])}: {STANDS.get(status, status.lower())}.")
    else:
        say(f"The situation is open: {STANDS.get(status, status.lower())}.")

    if len(sentences) > MAX_SENTENCES:
        left_out = len(sentences) - (MAX_SENTENCES - 1)
        sentences = sentences[:MAX_SENTENCES - 2] + [
            {"text": f"{left_out} further entries are in the timeline and are not repeated here.", "refs": []},
            sentences[-1]]
    return {
        "is_ai_assisted": True, "label": LABEL, "made_of": MADE_OF, "timezone": tz.key,
        "situation_id": situation.get("id"), "situation_number": situation.get("situation_number"),
        "sentences": sentences, "text": " ".join(s["text"] for s in sentences),
    }
