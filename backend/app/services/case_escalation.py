"""Cases that escalate: an overdue task, a case gone quiet, a close request waiting too long.

Phase 3 of closing the feature reference's known limits (4 November 2026; docs/plans/2026-11-04-case-notify-and-
escalate-design.md). Run by the scheduler every 15 minutes, each organisation in its own transaction, as the
application's role.

  TASK_OVERDUE   a task past its due time and still open
                 1: its assignee and the case's lead     2: after the overdue wait, Managers and Admins
  QUIET          an open case with nothing added for its priority's quiet days
                 1: the lead                             2: after QUIET_SECOND_DAYS more, Managers and Admins
  CLOSE_WAITING  a close request not decided within the close wait
                 1: whoever may approve it (case:manage) - never whoever asked: closing takes two people

NOTHING ADDED means no change to the case, no entry in its history, and no task, link, person named or investigator
made or taken away since. The next of any of those counts the quiet afresh.

NOT BACKDATED. A step is due from the later of its moment and when the organisation's escalation began
(`case_escalation_settings.since`, the row made on the first pass), so a case quiet for a year is not told about on
the first day as if it had been watched all year.

ONCE. Each step is recorded in `case_escalations` before it is told, unique per case, task, trigger, step and
moment; a pass that runs again finds it there. How many people it was addressed to is part of the record, so a step
addressed to nobody shows as that.

NOTHING HERE CHANGES A CASE. Nobody is reassigned, nothing is closed or reprioritised. People are told; a person
decides.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import case_notify

logger = logging.getLogger(__name__)

#: How long after the first that a quiet case's second step falls due.
QUIET_SECOND_DAYS = 2
#: What each timing may be, as the organisation sets it.
LIMITS = {"quiet_high_days": (1, 90), "quiet_normal_days": (1, 90), "quiet_low_days": (1, 90),
          "overdue_hours": (1, 168), "close_wait_days": (1, 30)}
DEFAULTS = {"quiet_high_days": 3, "quiet_normal_days": 7, "quiet_low_days": 14, "overdue_hours": 24,
            "close_wait_days": 2}
QUIET_KEY = {"HIGH": "quiet_high_days", "NORMAL": "quiet_normal_days", "LOW": "quiet_low_days"}
KIND_LABEL = {"TASK_OVERDUE": "A task is overdue", "QUIET": "Nothing has been added", "CLOSE_WAITING":
              "A close request is waiting"}

#: When a case last had anything added: its own change, its history, its tasks, links, people named, investigators.
LAST_ACTIVITY = """
    GREATEST(c.updated_at, c.opened_at,
             (SELECT max(e.occurred_at) FROM case_entries e WHERE e.case_id = c.id),
             (SELECT max(GREATEST(t.created_at, t.updated_at)) FROM case_tasks t WHERE t.case_id = c.id),
             (SELECT max(GREATEST(l.linked_at, COALESCE(l.removed_at, l.linked_at))) FROM case_links l
               WHERE l.case_id = c.id),
             (SELECT max(GREATEST(p.added_at, COALESCE(p.removed_at, p.added_at))) FROM case_parties p
               WHERE p.case_id = c.id),
             (SELECT max(GREATEST(i.added_at, COALESCE(i.removed_at, i.added_at))) FROM case_investigators i
               WHERE i.case_id = c.id))
"""


@dataclass(frozen=True)
class Step:
    """One escalation step that has fallen due."""
    case: Mapping
    kind: str
    step: int
    counted_from: datetime
    due_at: datetime
    task: Mapping | None = None


async def settings(db: AsyncSession, *, make: bool = False) -> dict:
    """The calling organisation's timings, with `since` and who last changed them. With `make`, the row is made
    with the defaults when there is none - which is when its escalation begins."""
    row = (await db.execute(text("""
        SELECT s.quiet_high_days, s.quiet_normal_days, s.quiet_low_days, s.overdue_hours, s.close_wait_days, s.since,
               s.updated_at, s.updated_by_user_id, u.full_name AS updated_by_name
          FROM case_escalation_settings s LEFT JOIN users u ON u.id = s.updated_by_user_id
    """))).mappings().first()
    if row is None and make:
        await db.execute(text("INSERT INTO case_escalation_settings (tenant_id) "
                              "VALUES (current_setting('app.current_tenant')::uuid) ON CONFLICT DO NOTHING"))
        return await settings(db)
    if row is None:
        return {**DEFAULTS, "since": None, "updated_at": None, "updated_by_user_id": None, "updated_by_name": None}
    return dict(row)


def due_steps(case: Mapping, tasks: list[Mapping], config: Mapping, since: datetime, now: datetime) -> list[Step]:
    """What has fallen due for one case. Pure: the times are the case's, the timings the organisation's."""
    out: list[Step] = []
    if case["status"] == "OPEN":
        for t in tasks:
            if t["state"] != "OPEN" or t["due_at"] is None:
                continue
            first = max(t["due_at"], since)
            second = first + timedelta(hours=config["overdue_hours"])
            out += [Step(case, "TASK_OVERDUE", n, t["due_at"], at, t) for n, at in ((1, first), (2, second)) if at <= now]
        quiet = timedelta(days=config[QUIET_KEY.get(case["priority"], "quiet_normal_days")])
        first = max(case["last_activity"], since) + quiet
        second = first + timedelta(days=QUIET_SECOND_DAYS)
        out += [Step(case, "QUIET", n, case["last_activity"], at) for n, at in ((1, first), (2, second)) if at <= now]
    elif case["status"] == "AWAITING_APPROVAL" and case["close_requested_at"] is not None:
        at = max(case["close_requested_at"], since) + timedelta(days=config["close_wait_days"])
        if at <= now:
            out.append(Step(case, "CLOSE_WAITING", 1, case["close_requested_at"], at))
    return out


def words(s: Step, config: Mapping) -> str:
    """What the people told read."""
    c = s.case
    name = f"{c['case_number']}: {c['title']}"
    if s.kind == "TASK_OVERDUE":
        late = "is overdue" if s.step == 1 else f"is still overdue after {config['overdue_hours']} hours"
        return f"Task “{s.task['title']}” on {name} {late}"
    if s.kind == "QUIET":
        days = config[QUIET_KEY.get(c["priority"], "quiet_normal_days")] + (QUIET_SECOND_DAYS if s.step == 2 else 0)
        return f"{name} has had nothing added for {days} days ({c['priority'].lower()} priority)"
    return f"{name} has waited {config['close_wait_days']} days for its closing to be approved"


async def _addressed(db: AsyncSession, s: Step) -> list[str]:
    c = s.case
    if s.kind == "CLOSE_WAITING":
        return await case_notify.people(db, c["site_id"], permission="case:manage",
                                        besides=[c["close_requested_by_user_id"]])
    if s.step == 2:
        return await case_notify.people(db, c["site_id"], roles=case_notify.ABOVE)
    named = [c["lead_user_id"]] + ([s.task["assigned_to_user_id"]] if s.task else [])
    return await case_notify.people(db, c["site_id"], user_ids=named)


async def evaluate_tenant(db: AsyncSession, now: datetime | None = None) -> list[dict]:
    """Record every step that has fallen due for the organisation in scope and has not been recorded, and return what
    to tell. The caller commits, then tells."""
    now = now or datetime.now(timezone.utc)
    config = await settings(db, make=True)
    since = config["since"]
    cases = [dict(r) for r in (await db.execute(text(f"""
        SELECT c.id, c.site_id, c.case_number, c.title, c.priority, c.status, c.lead_user_id, c.close_requested_at,
               c.close_requested_by_user_id, {LAST_ACTIVITY} AS last_activity
          FROM case_files c WHERE c.status IN ('OPEN', 'AWAITING_APPROVAL')
    """))).mappings()]
    if not cases:
        return []
    tasks: dict[str, list[dict]] = {}
    for t in (await db.execute(text("""
        SELECT t.id, t.case_id, t.title, t.assigned_to_user_id, t.due_at, t.state FROM case_tasks t
         WHERE t.state = 'OPEN' AND t.due_at IS NOT NULL AND t.case_id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": [str(c["id"]) for c in cases]})).mappings():
        tasks.setdefault(str(t["case_id"]), []).append(dict(t))
    zone = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()

    tell: list[dict] = []
    for case in cases:
        for s in due_steps(case, tasks.get(str(case["id"]), []), config, since, now):
            who = await _addressed(db, s)
            message = words(s, config)
            made = (await db.execute(text("""
                INSERT INTO case_escalations (tenant_id, case_id, task_id, kind, step, counted_from, due_at,
                                              addressed, message)
                VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), CAST(:t AS uuid), :kind,
                        :step, :from, :due, :n, :message)
                ON CONFLICT DO NOTHING RETURNING id
            """), {"c": str(case["id"]), "t": str(s.task["id"]) if s.task else None, "kind": s.kind,
                   "step": s.step, "from": s.counted_from, "due": s.due_at, "n": len(who),
                   "message": message})).scalar()
            if made is None:
                continue
            tell.append({"escalation_id": str(made), "case_id": str(case["id"]), "case_number": case["case_number"],
                         "title": case["title"], "kind": s.kind, "kind_label": KIND_LABEL[s.kind], "step": s.step,
                         "task_id": str(s.task["id"]) if s.task else None,
                         "site_id": str(case["site_id"]) if case["site_id"] else None, "user_ids": who,
                         "message": message, "severity": "high" if s.step == 2 else "medium", "zone": zone})
    return tell


async def run(session_factory, redis=None, now: datetime | None = None) -> dict:
    """One pass over every organisation: record what fell due, commit, then tell. A telling that fails cannot undo
    the record, and one organisation's trouble is its own."""
    from app.services.audit import write_audit_log

    async with session_factory() as db:
        tenants = [str(r[0]) for r in await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))]
    counts = {"escalations": 0, "failed": 0}
    for tenant_id in tenants:
        try:
            async with session_factory() as db:
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
                tell = await evaluate_tenant(db, now)
                for item in tell:
                    await write_audit_log(db, tenant_id=tenant_id, user_id=None, action="case.escalate",
                                          resource_type="case", resource_id=item["case_id"],
                                          detail={"kind": item["kind"], "step": item["step"],
                                                  "addressed": len(item["user_ids"]),
                                                  "escalation_id": item["escalation_id"]})
                await db.commit()
        except Exception:
            logger.exception("case escalation: could not judge tenant %s", tenant_id)
            counts["failed"] += 1
            continue
        counts["escalations"] += len(tell)
        for item in tell:
            payload = {k: v for k, v in item.items() if k not in ("user_ids", "message", "zone")}
            await case_notify.tell(redis, session_factory, tenant_id, case_notify.ESCALATED_EVENT, item["user_ids"],
                                   item["message"], payload, now)
    return counts


async def for_case(db: AsyncSession, case_id: Any) -> list[dict]:
    """A case's escalations, newest first."""
    rows = await db.execute(text("""
        SELECT e.id, e.kind, e.step, e.task_id, t.title AS task_title, e.counted_from, e.due_at, e.sent_at,
               e.addressed, e.message
          FROM case_escalations e LEFT JOIN case_tasks t ON t.id = e.task_id
         WHERE e.case_id = CAST(:c AS uuid) ORDER BY e.sent_at DESC, e.step DESC
    """), {"c": str(case_id)})
    return [{**dict(r), "kind_label": KIND_LABEL[r["kind"]]} for r in rows.mappings()]
