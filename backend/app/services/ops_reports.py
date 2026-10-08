"""The operations reports: records the platform already keeps, as rows for a file.

  REPORTS      each report: what it holds, what it is read under, its columns
  rows()       one report's rows for a period and a set of sites
  as_csv()     rows as a CSV file's text. Pure.
  cell()       one value as it is written into a file. Pure.

NINE REPORTS, each a list of records and none a judgement: the board site by
site; the response to each incident; device health as it is read now;
maintenance work orders; visitor and contractor authorisations; door events;
what stands out and what was answered; evidence packages; investigations.

EACH IS READ UNDER THE PERMISSION ITS OWN SCREEN ASKS FOR. The permission to
take a report out is held on top of that, and by itself opens none of them.

A FILE IS OPENED IN A SPREADSHEET, AND A SPREADSHEET RUNS FORMULAS. A value that
a person typed — a visitor's name, a title, a reason — and that begins with
`=`, `+`, `-`, `@`, a tab or a return is written with an apostrophe before it,
so that it is read as text.

A TIME IS WRITTEN WHERE THE ORGANISATION IS, and the heading of its column says
which time zone that is.

A REPORT THAT IS CUT SAYS SO. One file holds at most `MAX_ROWS` records; past
that its last line says it was cut, in words.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import device_health, ops_board, risk_patterns

#: The most records one file holds.
MAX_ROWS = 10000
PERIOD_DAYS = (1, 7, 30, 90)
#: Over how many weeks what stands out is counted, whatever period the other reports are asked for.
ADVICE_WEEKS = 4
#: What a spreadsheet would run as a formula.
RUNS = ("=", "+", "-", "@", "\t", "\r")
CUT = "Cut at {n} records. Narrow the period or choose one site to have the rest."
DEVICE_STATE = {"OK": "Working", "DEGRADED": "Degraded", "DOWN": "Down", "NOT_KNOWN": "Not known", "OFF": "Switched off"}


@dataclass(frozen=True)
class Report:
    key: str
    title: str
    #: What a row of it is, in a sentence.
    holds: str
    #: Every permission its records are read under.
    needs: tuple[str, ...]
    #: (what a row calls it, what the file's heading calls it). A heading ending `at` is a time.
    columns: tuple[tuple[str, str], ...]
    #: False for a report of how things are now: it has no period.
    periodic: bool = True


_BOARD = (
    ("site", "Site"), ("customer", "Customer"),
    ("incidents_opened", "Incidents opened"), ("incidents_resolved", "Incidents resolved"),
    ("incidents_open_now", "Incidents open now"),
    ("acted_on", "Incidents acted on"), ("acted_on_seconds", "Middle seconds until acted on"),
    ("resolved", "Of those opened, resolved"), ("resolved_seconds", "Middle seconds until resolved"),
    ("guards_sent", "Guards sent"), ("guards_arrived", "Guards arrived"),
    ("arrived_seconds", "Middle seconds from sent to arrived"),
    ("missed_acknowledge", "Clocks missed: acknowledge"), ("missed_arrival", "Clocks missed: arrival"),
    ("missed_resolve", "Clocks missed: resolve"),
    ("tours_done", "Guard tours done"), ("tours_over", "Guard tours over"), ("tours_missed", "Guard tours missed"),
    ("virtual_done", "Virtual patrols done"), ("virtual_over", "Virtual patrols over"),
    ("virtual_missed", "Virtual patrols missed or failed"),
    ("drone_done", "Drone patrols done"), ("drone_over", "Drone patrols over"),
    ("drone_missed", "Drone patrols missed or failed"),
    ("on_shift_now", "Guards on shift now"), ("shifts", "Shifts due to begin"), ("shifts_worked", "Shifts worked"),
    ("shifts_late", "Shifts started late"), ("shifts_not_started", "Shifts not started"),
    ("devices", "Devices"), ("devices_down", "Devices down"), ("devices_degraded", "Devices degraded"),
    ("devices_not_known", "Devices with no reading"),
    ("visitors_on_site_now", "Visitors on site now"), ("visitors_arrived", "Arrivals logged"),
    ("visitors_departed", "Departures logged"), ("visitors_refused", "Visitors refused"),
    ("orders_suggested_now", "Suggestions waiting now"), ("orders_in_hand_now", "Orders in hand now"),
    ("orders_overdue_now", "Orders overdue now"), ("orders_raised", "Orders raised"), ("orders_done", "Orders completed"),
)

REPORTS: tuple[Report, ...] = (
    Report("board-sites", "The operations board, site by site",
           "One row for each site with the board's figures for the period; a figure of a section you may not read is blank.",
           ("board:read",), _BOARD),
    Report("response", "The response to each incident",
           "One row for each incident opened in the period: when somebody first acted on it, when it was resolved, "
           "the guards sent, and the response clocks recorded as missed.",
           ("response:read", "incident:read"),
           (("opened_at", "Opened at"), ("site", "Site"), ("camera", "Camera"), ("title", "Incident"),
            ("severity", "Severity"), ("status", "Status"), ("acknowledged_at", "First acted on at"),
            ("acknowledge_seconds", "Seconds until acted on"), ("resolved_at", "Resolved at"),
            ("resolve_seconds", "Seconds until resolved"), ("guards_sent", "Guards sent"),
            ("first_arrived_at", "First arrival at"), ("clocks_missed", "Clocks missed"))),
    Report("device-health", "Device health, as it is read now",
           "One row for each device with how it is read now and why. It measures what the device reports and "
           "nothing else, and has no period.",
           ("asset:read",),
           (("kind", "Kind"), ("name", "Device"), ("site", "Site"), ("state", "Read as"), ("since", "Since at"),
            ("why", "Why"), ("asset_code", "Asset")), periodic=False),
    Report("maintenance", "Maintenance work orders",
           "One row for each work order raised or completed in the period, and each still waiting or in hand.",
           ("maintenance:read",),
           (("number", "Order"), ("title", "Work"), ("kind", "Kind"), ("priority", "Priority"), ("state", "State"),
            ("origin", "Put forward by"), ("site", "Site"), ("asset_code", "Asset"), ("asset", "Asset name"),
            ("raised_at", "Raised at"), ("accepted_at", "Accepted at"), ("due_at", "Due at"),
            ("started_at", "Started at"), ("completed_at", "Completed at"), ("downtime_minutes", "Minutes out of use"),
            ("given_to", "Given to"), ("suggestion_reason", "Why it was put forward"),
            ("completion_note", "What was done"), ("closed_reason", "Why it was closed"))),
    Report("visitors", "Visitor and contractor authorisations",
           "One row for each authorisation asked for in the period: who for, where, by whom it was decided, and "
           "for how long it holds.",
           ("visitorauth:read",),
           (("requested_at", "Asked for at"), ("site", "Site"), ("subject", "Visitor or permit"), ("company", "Company"),
            ("purpose", "Purpose"), ("state", "State"), ("valid_from", "Valid from at"), ("valid_until", "Valid until at"),
            ("escort_required", "Escort required"), ("host", "Host"), ("id_seen", "Identity document seen"),
            ("decided_by", "Decided by"), ("decided_at", "Decided at"), ("decision_note", "Decision note"))),
    Report("access", "Door events",
           "One row for each door event in the period, with the door, what happened and the credential used.",
           ("access:read",),
           (("occurred_at", "Happened at"), ("site", "Site"), ("door", "Door"), ("event_type", "Event"),
            ("denial_reason", "Why refused"), ("credential_ref", "Credential"), ("holder_name", "Held by"))),
    Report("risk", "What stands out, and what was answered",
           "One row for each piece of advice that stands over the last 4 weeks, with how much history it rests on "
           "and the latest answer a person gave it. A pattern that recurred is not a forecast. It has no period "
           "of its own.",
           ("advice:read",),
           (("source", "Kind"), ("statement", "What stands out"), ("level", "Rests on"), ("why", "How much history"),
            ("records", "Records"), ("weeks", "Weeks"), ("held_in_weeks", "Weeks it held in"), ("answer", "Answer"),
            ("answered_by", "Answered by"), ("answered_at", "Answered at"), ("reason", "Reason")), periodic=False),
    Report("evidence", "Evidence packages",
           "One row for each evidence package made or sealed in the period: what it is for, who sealed it, its "
           "checksum, and how many items, custody steps and holds it has.",
           ("evidence:package:read",),
           (("package_number", "Package"), ("title", "Title"), ("purpose", "Purpose"), ("status", "Status"),
            ("site", "Site"), ("created_at", "Made at"), ("created_by", "Made by"), ("sealed_at", "Sealed at"),
            ("sealed_by", "Sealed by"), ("manifest_sha256", "Manifest SHA-256"), ("items", "Items"),
            ("custody_steps", "Custody steps"), ("holds", "Holds in force"))),
    Report("investigations", "Investigations",
           "One row for each investigation opened or closed in the period, and each still open.",
           ("investigation:read",),
           (("investigation_number", "Investigation"), ("title", "Title"), ("status", "Status"), ("site", "Site"),
            ("opened_at", "Opened at"), ("opened_by", "Opened by"), ("closed_at", "Closed at"),
            ("closed_by", "Closed by"), ("reason", "Why it was opened"), ("closing_note", "Closing note"),
            ("items", "Items held"))),
)
BY_KEY = {r.key: r for r in REPORTS}
LEVEL_WORDS = {"HIGH": "Much history", "MEDIUM": "Some history", "LOW": "Little history"}

_SQL = {
    "response": f"""
        SELECT i.created_at AS opened_at, s.name AS site, c.name AS camera, i.title, i.severity, i.status,
               {ops_board.ACKNOWLEDGED_AT} AS acknowledged_at, i.resolved_at,
               (SELECT count(*) FROM incident_responses r WHERE r.incident_id = i.id) AS guards_sent,
               (SELECT min(r.arrived_at) FROM incident_responses r WHERE r.incident_id = i.id) AS first_arrived_at,
               (SELECT string_agg(lower(e.clock), ', ' ORDER BY e.clock) FROM incident_escalations e
                 WHERE e.incident_id = i.id AND e.kind = 'SLA_BREACH') AS clocks_missed, c.site_id
          FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id LEFT JOIN sites s ON s.id = c.site_id
         WHERE i.created_at >= :start AND i.created_at < :end""",
    "maintenance": """
        SELECT w.number, w.title, w.kind, w.priority, w.state, w.origin, s.name AS site, a.asset_code, a.name AS asset,
               w.raised_at, w.accepted_at, w.due_at, w.started_at, w.completed_at, w.downtime_minutes,
               COALESCE(u.full_name, w.assigned_to_name) AS given_to, w.suggestion_reason, w.completion_note,
               w.closed_reason, w.site_id
          FROM maintenance_work_orders w LEFT JOIN sites s ON s.id = w.site_id
          LEFT JOIN asset_register a ON a.id = w.asset_id LEFT JOIN users u ON u.id = w.assigned_to_user_id
         WHERE ((w.raised_at >= :start AND w.raised_at < :end) OR (w.completed_at >= :start AND w.completed_at < :end)
                OR w.state IN ('SUGGESTED', 'OPEN', 'IN_PROGRESS'))""",
    "visitors": """
        SELECT a.requested_at, s.name AS site, COALESCE(v.full_name, 'Work permit ' || wp.permit_number) AS subject,
               COALESCE(v.company, k.company_name) AS company, COALESCE(a.purpose, wp.work_description) AS purpose,
               a.state, a.valid_from, a.valid_until, a.escort_required, h.full_name AS host,
               a.id_document_kind IS NOT NULL AS id_seen, d.full_name AS decided_by, a.decided_at, a.decision_note,
               a.site_id
          FROM visitor_authorizations a JOIN sites s ON s.id = a.site_id
          LEFT JOIN visitors v ON v.id = a.visitor_id
          LEFT JOIN work_permits wp ON wp.id = a.work_permit_id LEFT JOIN contractors k ON k.id = wp.contractor_id
          LEFT JOIN users h ON h.id = a.host_user_id LEFT JOIN users d ON d.id = a.decided_by_user_id
         WHERE a.requested_at >= :start AND a.requested_at < :end""",
    "access": """
        SELECT e.occurred_at, s.name AS site, d.name AS door, e.event_type, e.denial_reason, k.credential_ref,
               k.holder_name, d.site_id
          FROM access_events e JOIN access_doors d ON d.id = e.door_id LEFT JOIN sites s ON s.id = d.site_id
          LEFT JOIN access_credentials k ON k.id = e.credential_id
         WHERE e.occurred_at >= :start AND e.occurred_at < :end""",
    "evidence": """
        SELECT p.package_number, p.title, p.purpose, p.status, s.name AS site, p.created_at, cu.full_name AS created_by,
               p.sealed_at, su.full_name AS sealed_by, p.manifest_sha256,
               (SELECT count(*) FROM evidence_package_items i WHERE i.package_id = p.id) AS items,
               (SELECT count(*) FROM evidence_custody_events e WHERE e.package_id = p.id) AS custody_steps,
               (SELECT count(*) FROM evidence_holds h WHERE h.package_id = p.id AND h.released_at IS NULL) AS holds,
               p.site_id
          FROM evidence_packages p LEFT JOIN sites s ON s.id = p.site_id
          LEFT JOIN users cu ON cu.id = p.created_by_user_id LEFT JOIN users su ON su.id = p.sealed_by_user_id
         WHERE ((p.created_at >= :start AND p.created_at < :end) OR (p.sealed_at >= :start AND p.sealed_at < :end))""",
    "investigations": """
        SELECT n.investigation_number, n.title, n.status, s.name AS site, n.opened_at, ou.full_name AS opened_by,
               n.closed_at, cu.full_name AS closed_by, n.reason, n.closing_note,
               (SELECT count(*) FROM investigation_items i
                 WHERE i.investigation_id = n.id AND i.set_aside_at IS NULL) AS items, n.site_id
          FROM investigations n LEFT JOIN sites s ON s.id = n.site_id
          LEFT JOIN users ou ON ou.id = n.opened_by_user_id LEFT JOIN users cu ON cu.id = n.closed_by_user_id
         WHERE ((n.opened_at >= :start AND n.opened_at < :end) OR (n.closed_at >= :start AND n.closed_at < :end)
                OR n.status = 'OPEN')""",
}
#: What each of those is put in order by.
_ORDER = {"response": "opened_at", "maintenance": "raised_at", "visitors": "requested_at", "access": "occurred_at",
          "evidence": "created_at", "investigations": "opened_at"}


# ─── Pure ────────────────────────────────────────────────────────────────────

def cell(value: Any, zone: ZoneInfo) -> str:
    """One value as it is written into a file: nothing as nothing, a time where
    the organisation is, yes or no in words, and text that a spreadsheet would
    run as a formula made plain text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return str(int(round(value)))
    if isinstance(value, (int, Decimal)):
        return str(value)
    said = str(value)
    return "'" + said if said.startswith(RUNS) else said


def headings(report: Report, zone: str) -> list[str]:
    """The file's first line. A column of times says which time zone they are in."""
    return [f"{heading} ({zone})" if heading.endswith(" at") else heading for _, heading in report.columns]


def as_csv(report: Report, rows: Sequence[Mapping], zone: str, cut: bool = False) -> str:
    """A report's rows as a file's text: a line of headings, a line for each
    row, and — when the report was cut — a last line that says so."""
    tz = ZoneInfo(zone)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(headings(report, zone))
    for row in rows:
        writer.writerow([cell(row.get(key), tz) for key, _ in report.columns])
    if cut:
        writer.writerow([CUT.format(n=f"{MAX_ROWS:,}")])
    return out.getvalue()


def may_have(report: Report, held: frozenset[str] | set[str]) -> bool:
    return all(code in held for code in report.needs)


def _board_row(site: Mapping, f: Mapping) -> dict:
    """A site's figures, flat. A section or a part that was not read is left blank."""
    row: dict = {"site": site.get("name"), "customer": site.get("client_name")}
    if "INCIDENTS" in f:
        row.update(incidents_opened=f["INCIDENTS"]["opened"], incidents_resolved=f["INCIDENTS"]["resolved"],
                   incidents_open_now=f["INCIDENTS"]["open_now"])
    if "RESPONSE" in f:
        r = f["RESPONSE"]
        row.update(acted_on=r["acknowledged"], acted_on_seconds=r["acknowledge_seconds"], resolved=r["resolved"],
                   resolved_seconds=r["resolve_seconds"], guards_sent=r["sent"], guards_arrived=r["arrived"],
                   arrived_seconds=r["arrive_seconds"], missed_acknowledge=r["missed"]["acknowledge"],
                   missed_arrival=r["missed"]["arrival"], missed_resolve=r["missed"]["resolve"])
    for kind in ops_board.PATROL_KINDS:
        p = f.get("PATROLS", {}).get(kind)
        if p is not None:
            row.update({f"{kind}_done": p["done"], f"{kind}_over": ops_board.over(p),
                        f"{kind}_missed": p["missed"] + p["failed"]})
    if "GUARDS" in f:
        g = f["GUARDS"]
        row.update(on_shift_now=g["on_shift_now"], shifts=g["shifts"], shifts_worked=g["worked"],
                   shifts_late=g["late"], shifts_not_started=g["not_started"])
    if "DEVICES" in f:
        d = f["DEVICES"]
        row.update(devices=d["devices"], devices_down=d["by_state"]["DOWN"], devices_degraded=d["by_state"]["DEGRADED"],
                   devices_not_known=d["by_state"]["NOT_KNOWN"])
    if "VISITORS" in f:
        v = f["VISITORS"]
        row.update(visitors_on_site_now=v["on_site_now"], visitors_arrived=v["arrived"],
                   visitors_departed=v["departed"], visitors_refused=v["refused"])
    if "MAINTENANCE" in f:
        m = f["MAINTENANCE"]
        row.update(orders_suggested_now=m["suggested_now"], orders_in_hand_now=m["open_now"] + m["in_progress_now"],
                   orders_overdue_now=m["overdue_now"], orders_raised=m["raised"], orders_done=m["done"])
    return row


# ─── Reading ─────────────────────────────────────────────────────────────────

async def rows(db: AsyncSession, report: Report, held: frozenset[str] | set[str], sites: Sequence[Mapping],
               site_ids: Sequence[Any] | None, zone: str, start: datetime, end: datetime,
               now: datetime, one_site: Any = None) -> tuple[list[dict], bool]:
    """One report's rows, and whether it was cut at `MAX_ROWS`. `sites` is the
    sites of the scope, each with its customer; `site_ids` None is every site
    and what has no site, a list is those sites only. `one_site` is the site
    that was asked for by itself, when one was: advice is answered for a site,
    so its answers are given only then."""
    if report.key == "board-sites":
        board = await ops_board.read(db, held, site_ids, start, end, now)
        out = [_board_row(s, board["sites"].get(str(s["id"]), {})) for s in sites
               if s["is_active"] or str(s["id"]) in board["sites"]]
        if site_ids is None and None in board["sites"]:
            out.append(_board_row({"name": "At no site"}, board["sites"][None]))
        return out, False
    if report.key == "device-health":
        items = await device_health.readings(db, now, allowed=None if site_ids is None else [str(s) for s in site_ids])
        return [{"kind": r["kind_label"], "name": r["name"], "site": r["site_name"],
                 "state": DEVICE_STATE[r["state"]], "since": r["since"], "why": " ".join(r["reasons"]),
                 "asset_code": r["asset_code"]} for r in items[:MAX_ROWS]], len(items) > MAX_ROWS
    if report.key == "risk":
        scope = str(one_site) if one_site is not None else "ALL"
        found = (await risk_patterns.read(db, zone, risk_patterns.period(now, ADVICE_WEEKS), now, ADVICE_WEEKS,
                                          site_ids, scope))["findings"]
        answers: dict[str, Mapping] = {}
        if found and scope != "ALL":
            answered = await db.execute(text("""
                SELECT DISTINCT ON (a.advice_key) a.advice_key, a.answer, a.reason, a.answered_at, u.full_name AS answered_by
                  FROM risk_advice_answers a LEFT JOIN users u ON u.id = a.answered_by_user_id
                 WHERE a.advice_key = ANY(:keys) ORDER BY a.advice_key, a.answered_at DESC, a.id DESC
            """), {"keys": [f["key"] for f in found]})
            answers = {r["advice_key"]: r for r in answered.mappings()}
        return [{"source": f["source_label"], "statement": f["statement"], "level": LEVEL_WORDS[f["confidence"]["level"]],
                 "why": f["confidence"]["why"], "records": f["confidence"]["records"], "weeks": f["confidence"]["weeks"],
                 "held_in_weeks": f["confidence"]["held_in_weeks"],
                 "answer": {"ACCEPTED": "Accepted", "NOT_ACCEPTED": "Not accepted"}.get(
                     (answers.get(f["key"]) or {}).get("answer")),
                 "answered_by": (answers.get(f["key"]) or {}).get("answered_by"),
                 "answered_at": (answers.get(f["key"]) or {}).get("answered_at"),
                 "reason": (answers.get(f["key"]) or {}).get("reason")} for f in found], False
    params: dict = {"start": start, "end": end, "cap": MAX_ROWS + 1}
    scope_sql = "TRUE"
    if site_ids is not None:
        scope_sql = "x.site_id = ANY(CAST(:sites AS uuid[]))"
        params["sites"] = [str(s) for s in site_ids]
    result = await db.execute(text(f"""
        SELECT x.* FROM ({_SQL[report.key]}) x WHERE {scope_sql} ORDER BY x.{_ORDER[report.key]} LIMIT :cap
    """), params)
    found_rows = [dict(r) for r in result.mappings()]
    for r in found_rows:
        if report.key == "response":
            r["acknowledge_seconds"] = _seconds(r["opened_at"], r["acknowledged_at"])
            r["resolve_seconds"] = _seconds(r["opened_at"], r["resolved_at"])
    return found_rows[:MAX_ROWS], len(found_rows) > MAX_ROWS


def _seconds(start: datetime | None, end: datetime | None) -> int | None:
    return None if start is None or end is None else int((end - start).total_seconds())
