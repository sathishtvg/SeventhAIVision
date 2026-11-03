"""Periods an organisation may set for the kinds of record the enterprise expansion added.

Until 2026-10-09 nothing the expansion added had a retention period: a closed
case, a visitor's authorisation and an answer about a guard were kept for as
long as the organisation existed (ENTERPRISE_SECURITY_HARDENING.md, section 9,
item 6). How long each ought to be kept is the organisation's to decide, and is
written nowhere in the code. What is here is the means.

NOTHING IS REMOVED UNLESS A PERIOD IS SET. Each kind has a setting. With no
setting the kind is kept, as before, and this module reads it and leaves it
alone.

TWELVE KINDS, each removed whole and each counted from the moment it was over. The first four since 9 October
2026; the rest since 3 November 2026 (Phase 2 of closing the known limits: history only - a setting in use, such as
a procedure or a site's places, has no period):

    CASES                   a closed case, from when its closing was approved
    INVESTIGATIONS          a closed investigation, from when it was closed
    VISITOR_AUTHORIZATIONS  an authorisation, from the end of the period it was for
    WORKFORCE_ANSWERS       an answer to a recommendation about a guard or a site
    RESPONSES               guards sent to an incident and its escalations, from when the incident was resolved
    SHIFT_SUMMARIES         a shift's handover summary, from the end of the shift
    INSTRUCTIONS            a standing instruction that has ended, with who read it, from its end
    DEVICE_HEALTH           a change in a device's health, from when it was seen (never a device's latest)
    WORK_ORDERS             a maintenance order that is over, from when it was done or closed
    ADVICE_ANSWERS          an answer to risk advice
    BRIEFINGS               a daily briefing published or discarded
    EVIDENCE_PACKAGES       an evidence package - REMOVED BY A PERSON, never by the job (below)

A case that is open, or waiting for approval to close, is never removed
whatever its age: only something that is over has a period.

WHAT IS KEPT WHATEVER ITS AGE. An investigation an evidence package was made
from, and one linked to a case that is not closed. Evidence packages, their
custody and their holds have no period here at all: a chain of custody that
expires is not one.

AN EVIDENCE PACKAGE IS REMOVED BY A PERSON. Its period makes it due; somebody with
evidence:package:dispose removes it from the due list, with a reason, and a record of it is kept for good with its
chain of custody (migration 0176, routers/evidence_disposal.py). The daily job passes over it.

A PERIOD IS AT LEAST THIRTY DAYS. That is a guard against a slip of the hand,
not a recommendation: one day typed for one year would remove a year of cases
overnight.

REMOVED BY THE SCHEDULER, ONCE A DAY, ON ITS SUPERUSER SESSION. The
application's own role may not delete from these tables at all - which is the
point of them - so the job runs where the audit log's archive runs. That
session sees every organisation, so every statement here names the
organisation it is for. Each organisation's removal is one line in its audit
log: how many of each kind went, and under what period.

A PARENT IS REMOVED AND ITS PARTS GO WITH IT, by the database's own rule. The
triggers that hold a closed case's parts still let that through, as they let
through an organisation being removed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit import write_audit_log

logger = logging.getLogger(__name__)

#: The fewest days a period may be.
LEAST_DAYS = 30
#: The most days a period may be: a hundred years, so that a number is always a period.
MOST_DAYS = 36_500
#: Rows removed in one statement, and statements in one pass, for one kind of one organisation.
BATCH = 500
MAX_BATCHES = 40
REMOVED_BY = "The scheduler, once a day"
NOT_SET = "No period is set: it is kept."

#: A guard's response or an escalation `x` whose incident is over and older than :cutoff, and is in no evidence
#: package and no case that is not closed.
_OVER_INCIDENT = (
    "EXISTS (SELECT 1 FROM incidents n WHERE n.id = x.incident_id AND n.status = 'resolved' "
    "AND COALESCE(n.resolved_at, n.updated_at) < :cutoff "
    "AND NOT EXISTS (SELECT 1 FROM evidence_packages p WHERE p.incident_id = n.id) "
    "AND NOT EXISTS (SELECT 1 FROM case_links l JOIN case_files c ON c.id = l.case_id "
    "WHERE l.kind = 'INCIDENT' AND l.ref_id = n.id AND l.removed_at IS NULL AND c.status <> 'CLOSED'))")


@dataclass(frozen=True)
class Kind:
    key: str
    setting_key: str
    label: str
    #: The table a row is removed from, and those whose rows go with it.
    table: str
    parts: tuple[str, ...]
    counted_from: str
    removes: str
    kept_whatever: str | None
    #: True of a row `x` that is over and older than :cutoff.
    old_enough: str
    #: Other tables removed under the same period, each with its own test of a row `x` (3 November 2026).
    also: tuple[tuple[str, str], ...] = ()
    #: Removed by a person from a list of those due, never by the job (evidence packages).
    by_person: bool = False
    removed_by: str = REMOVED_BY

    @property
    def tables(self) -> tuple[str, ...]:
        """Every table this kind's period removes rows from, with the parts that go with them."""
        return (self.table, *self.parts, *(t for t, _ in self.also))


KINDS: tuple[Kind, ...] = (
    Kind("CASES", "retention.closed_cases_days", "Closed cases", "case_files",
         ("case_investigators", "case_tasks", "case_entries", "case_links", "case_parties"),
         "when its closing was approved",
         "The case with its people, tasks, notes, history, links and the people and vehicles named in it. "
         "The records it referred to are not touched",
         "A case that is open, or waiting for approval to close",
         "x.status = 'CLOSED' AND x.closed_at < :cutoff"),
    Kind("INVESTIGATIONS", "retention.closed_investigations_days", "Closed investigations", "investigations",
         ("investigation_items",),
         "when it was closed",
         "The investigation with what was put into it and its notes. The records it pointed at are not touched",
         "An investigation that is open; one an evidence package was made from; one linked to a case that is "
         "not closed",
         "x.status = 'CLOSED' AND x.closed_at < :cutoff "
         "AND NOT EXISTS (SELECT 1 FROM evidence_packages p WHERE p.investigation_id = x.id) "
         "AND NOT EXISTS (SELECT 1 FROM case_links l JOIN case_files c ON c.id = l.case_id "
         "                 WHERE l.kind = 'INVESTIGATION' AND l.ref_id = x.id AND l.removed_at IS NULL "
         "                   AND c.status <> 'CLOSED')"),
    Kind("VISITOR_AUTHORIZATIONS", "retention.visitor_authorisations_days", "Authorisations of visits and work",
         "visitor_authorizations", ("visitor_authorization_places", "visitor_movement_reviews"),
         "the end of the period it was for",
         "The authorisation with the places it was for and the reviews of where the badge was used. The visitor, "
         "the visit and the gate's log are not touched",
         None,
         "x.valid_until < :cutoff"),
    Kind("WORKFORCE_ANSWERS", "retention.workforce_answers_days", "Answers to workforce recommendations",
         "workforce_advice_answers", (),
         "when the answer was given",
         "The answer, with the recommendation as it stood",
         None,
         "x.answered_at < :cutoff"),
    # ── Since 3 November 2026: the history of modules 20 to 26 ──
    Kind("RESPONSES", "retention.incident_responses_days", "Guards sent to incidents, and escalations",
         "incident_responses", ("incident_response_steps",),
         "when the incident was resolved",
         "Each guard sent to the incident with every step they took, and the incident's escalations. The incident "
         "itself is not touched",
         "Those of an incident that is not resolved, that an evidence package was made for, or that a case not "
         "closed links",
         _OVER_INCIDENT,
         also=(("incident_escalations", _OVER_INCIDENT),)),
    Kind("SHIFT_SUMMARIES", "retention.shift_summaries_days", "Shift handover summaries",
         "shift_handover_summaries", (),
         "the end of the shift it covers",
         "The summary, as drafted and as confirmed. The shift and the occurrence book are not touched",
         "A summary still being written",
         "x.state <> 'DRAFT' AND x.period_end < :cutoff"),
    Kind("INSTRUCTIONS", "retention.ended_instructions_days", "Standing instructions that have ended",
         "site_instructions", ("site_instruction_reads",),
         "when it was closed, or when it expired",
         "The instruction with who read it and when",
         "An instruction still in force, and who read it",
         "COALESCE(x.closed_at, x.expires_at) < :cutoff"),
    Kind("DEVICE_HEALTH", "retention.device_health_changes_days", "Changes in devices' health",
         "device_health_changes", (),
         "when it was seen",
         "The change with what was read. How long a device was down, and what risk patterns and the AI read, are "
         "then known only from the changes kept",
         "Each device's latest change: it is the device's state now",
         "x.observed_at < :cutoff AND EXISTS (SELECT 1 FROM device_health_changes y "
         "WHERE y.tenant_id = x.tenant_id AND y.device_kind = x.device_kind AND y.device_id = x.device_id "
         "AND (y.observed_at, y.id) > (x.observed_at, x.id))"),
    Kind("WORK_ORDERS", "retention.finished_work_orders_days", "Maintenance work orders that are over",
         "maintenance_work_orders", (),
         "when it was done, cancelled or dismissed",
         "The order with what was done, the parts used and why it was closed. The asset and its schedule are not "
         "touched",
         "An order suggested, open or in progress; a suggestion for a device still down, or for the date its "
         "schedule still stands at, so that it is not put forward again",
         "x.state IN ('DONE', 'CANCELLED', 'DISMISSED') AND COALESCE(x.completed_at, x.closed_at) < :cutoff "
         "AND NOT (x.origin = 'HEALTH' AND EXISTS (SELECT 1 FROM device_health_changes h "
         "WHERE h.tenant_id = x.tenant_id AND h.device_kind = split_part(x.origin_key, ':', 2) "
         "AND h.device_id::text = split_part(x.origin_key, ':', 3) AND h.state = 'DOWN' "
         "AND NOT EXISTS (SELECT 1 FROM device_health_changes h2 WHERE h2.tenant_id = h.tenant_id "
         "AND h2.device_kind = h.device_kind AND h2.device_id = h.device_id "
         "AND (h2.observed_at, h2.id) > (h.observed_at, h.id)))) "
         "AND NOT (x.origin = 'SCHEDULE' AND EXISTS (SELECT 1 FROM maintenance_schedules m "
         "WHERE m.id = x.schedule_id AND x.origin_key = 'schedule:' || m.id::text || ':' || m.next_due_on::text))"),
    Kind("ADVICE_ANSWERS", "retention.advice_answers_days", "Answers to risk advice",
         "risk_advice_answers", (),
         "when the answer was given",
         "The answer, with the advice as it stood",
         None,
         "x.answered_at < :cutoff"),
    Kind("BRIEFINGS", "retention.daily_briefings_days", "Daily briefings published or discarded",
         "daily_briefings", (),
         "the end of the day it was for, or when it was discarded",
         "The briefing as it was published, or the draft that was discarded",
         "A briefing still being drafted",
         "((x.state = 'PUBLISHED' AND CAST(x.briefing_date + 1 AS timestamptz) < :cutoff) "
         "OR (x.state = 'DISCARDED' AND x.discarded_at < :cutoff))"),
    Kind("EVIDENCE_PACKAGES", "retention.evidence_packages_days", "Evidence packages",
         "evidence_packages", ("evidence_package_items",),
         "when it was sealed, or a draft's last change",
         "The package and its list of items, by a person. A record of each removal and the chain of custody are "
         "kept for good; the pictures and video it pointed to follow their own periods",
         "A package with anything in it on hold; one whose investigation is open, whose incident is not resolved, "
         "or that a case not closed links",
         "evidence_package_due(x.id, :cutoff)",
         by_person=True,
         removed_by="A person who may remove evidence packages, from the due list on the Evidence Packages "
                    "screen. The daily job never removes one"),
)
BY_KEY = {k.key: k for k in KINDS}
#: The kinds the daily job removes: all but those a person removes.
BY_THE_JOB = tuple(k for k in KINDS if not k.by_person)
SETTING_KEYS = tuple(k.setting_key for k in KINDS)


def days_of(value) -> int | None:
    """A setting's value as a period in days, or None when it is not one this job will act on."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if LEAST_DAYS <= value <= MOST_DAYS else None


async def periods(db: AsyncSession) -> dict[str, dict]:
    """The caller's organisation's periods, by kind: `{"days": n | None, "set_at": when | None}`.
    Read under row level security, as the application's role."""
    rows = (await db.execute(text("""
        SELECT setting_key, setting_value, updated_at FROM tenant_settings
         WHERE setting_key = ANY(CAST(:keys AS text[]))
    """), {"keys": list(SETTING_KEYS)})).mappings().all()
    said = {r["setting_key"]: r for r in rows}
    out = {}
    for kind in KINDS:
        row = said.get(kind.setting_key)
        days = days_of(row["setting_value"]) if row else None
        out[kind.key] = {"days": days, "set_at": row["updated_at"] if days is not None else None}
    return out


async def waiting(db: AsyncSession, kind: Kind, days: int, now: datetime | None = None) -> int:
    """How many rows of this kind are already older than a period of `days`, for the caller's organisation:
    what a person is told before they set it. Of a kind removed with another table, both are counted."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    total = 0
    for table, condition in ((kind.table, kind.old_enough), *kind.also):
        total += (await db.execute(text(f"SELECT count(*) FROM {table} x WHERE {condition}"),
                                   {"cutoff": cutoff})).scalar() or 0
    return total


async def _remove(db: AsyncSession, tenant_id, kind: Kind, cutoff: datetime) -> int:
    """Remove one organisation's rows of one kind that are over and older than the cutoff: its own table first,
    then any removed with it. Returns the rows of its own table."""
    assert not kind.by_person, "a person removes these"
    counted = 0
    for table, condition in ((kind.table, kind.old_enough), *kind.also):
        total = 0
        for _ in range(MAX_BATCHES):
            gone = (await db.execute(text(f"""
                DELETE FROM {table}
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND id IN (SELECT x.id FROM {table} x
                               WHERE x.tenant_id = CAST(:t AS uuid) AND {condition}
                               LIMIT :n)
            """), {"t": str(tenant_id), "cutoff": cutoff, "n": BATCH})).rowcount
            total += gone
            if gone < BATCH:
                break
        if table == kind.table:
            counted = total
    return counted


async def run(db: AsyncSession, now: datetime | None = None) -> dict:
    """Every organisation that has set a period, on a superuser session. Returns
    {"organisations": n, "removed": {kind: rows}, "failed": n}. One organisation's
    trouble is that organisation's: the others are still done."""
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(text("""
        SELECT s.tenant_id, s.setting_key, s.setting_value
          FROM tenant_settings s JOIN tenants t ON t.id = s.tenant_id
         WHERE s.setting_key = ANY(CAST(:keys AS text[])) AND t.is_active = TRUE
         ORDER BY s.tenant_id, s.setting_key
    """), {"keys": list(SETTING_KEYS)})).mappings().all()
    by_setting = {k.setting_key: k for k in KINDS}
    wanted: dict[str, list[tuple[Kind, int]]] = {}
    for row in rows:
        if by_setting[row["setting_key"]].by_person:
            continue
        days = days_of(row["setting_value"])
        if days is None:
            logger.warning("record retention: %s of tenant %s is not a period of %d days or more; nothing removed",
                           row["setting_key"], row["tenant_id"], LEAST_DAYS)
            continue
        wanted.setdefault(str(row["tenant_id"]), []).append((by_setting[row["setting_key"]], days))
    await db.commit()

    out = {"organisations": 0, "removed": {}, "failed": 0}
    for tenant_id, kinds in wanted.items():
        try:
            # The audit log's own trigger and chain read the organisation in scope.
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
            removed = {}
            for kind, days in kinds:
                removed[kind.key] = await _remove(db, tenant_id, kind, now - timedelta(days=days))
            if any(removed.values()):
                await write_audit_log(
                    db, tenant_id=tenant_id, user_id=None, action="retention.purge",
                    resource_type="record_retention", resource_id=None,
                    detail={"removed": removed, "days": {kind.key: days for kind, days in kinds}})
            await db.commit()
        except Exception:  # noqa: BLE001 — this organisation's pass, not everyone's
            await db.rollback()
            logger.exception("record retention failed for tenant %s", tenant_id)
            out["failed"] += 1
            continue
        out["organisations"] += 1
        for key, n in removed.items():
            out["removed"][key] = out["removed"].get(key, 0) + n
    return out
