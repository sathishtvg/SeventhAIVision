"""Telling people about a case: when they are put on one, and when one escalates.

Phase 3 of closing the feature reference's known limits (4 November 2026; docs/plans/2026-11-04-case-notify-and-
escalate-design.md). Before it a case said: "Nobody is told."

The three ways guard responses already use (services/response_notify.py), and nothing new:

  the organisation's own screens   `case_assigned` or `case_escalated` on `tenant_events:{tenant}`, carrying the
                                   people it is for; the web shows it to them and to nobody else
  the phones of the people named   Expo push, to the devices they registered
  the notification rules           a rule of the organisation's that asks for the event (email, SMS, webhook)

WHO IS TOLD CAN SEE THE CASE: active, holding case:read, and shown its site - somebody with no sites assigned sees
every site, an Admin sees everything, and a case at no one site is for those held to no sites. The person who made
the change is never told of it.

NOTHING HERE WRITES TO THE DATABASE OR FAILS A CHANGE. Each way of telling is tried on its own and a failure is
logged; the change it is about is already saved.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import response_notify

logger = logging.getLogger(__name__)

ASSIGNED_EVENT = "case_assigned"
ESCALATED_EVENT = "case_escalated"
#: Managers and Admins: who an escalation goes to after the people on the case.
ABOVE = (2, 8)
DEFAULT_ZONE = "Asia/Singapore"


def when(moment: datetime, zone: str | None) -> str:
    """A moment as the organisation's clock reads it: "Thu 05 Nov 14:00"."""
    try:
        tz = ZoneInfo(zone or DEFAULT_ZONE)
    except Exception:
        tz = ZoneInfo(DEFAULT_ZONE)
    return moment.astimezone(tz).strftime("%a %d %b %H:%M")


async def people(db: AsyncSession, site_id: Any, *, user_ids: Iterable[Any] = (), roles: Sequence[int] = (),
                 permission: str | None = None, besides: Iterable[Any] = ()) -> list[str]:
    """The active people, of those named, those in the roles, or those holding the permission, who may read cases
    and see this case's site - less `besides`. Read in the caller's organisation."""
    named = [str(u) for u in user_ids if u]
    if not named and not roles and not permission:
        return []
    rows = await db.execute(text("""
        SELECT DISTINCT u.id FROM users u
         WHERE u.is_active
           AND (u.id = ANY(CAST(:named AS uuid[])) OR u.role_id = ANY(CAST(:roles AS int[]))
                OR (CAST(:perm AS text) IS NOT NULL AND EXISTS (
                    SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                     WHERE rp.role_id = u.role_id AND p.code = CAST(:perm AS text))))
           AND EXISTS (SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                        WHERE rp.role_id = u.role_id AND p.code = 'case:read')
           AND (u.role_id = 2
                OR NOT EXISTS (SELECT 1 FROM user_sites us WHERE us.user_id = u.id)
                OR (CAST(:site AS uuid) IS NOT NULL AND EXISTS (
                    SELECT 1 FROM user_sites us WHERE us.user_id = u.id AND us.site_id = CAST(:site AS uuid))))
    """), {"named": named, "roles": list(roles), "perm": permission, "site": str(site_id) if site_id else None})
    left_out = {str(b) for b in besides if b}
    return sorted(str(r[0]) for r in rows if str(r[0]) not in left_out)


def _case_words(case: Mapping) -> str:
    return f"{case['case_number']}: {case['title']}"


async def tell(redis, session_factory, tenant_id: str, event_type: str, user_ids: Sequence[str], message: str,
               payload: Mapping, now: datetime | None = None) -> bool:
    """Tell these people, the three ways. True when anybody could have been reached."""
    if not user_ids:
        return False
    body = {**payload, "user_ids": list(user_ids), "message": message}
    announced = await response_notify.announce(redis, tenant_id, event_type, body, now)
    devices = await response_notify.push(redis, tenant_id, list(user_ids), message,
                                         {"type": event_type, "case_id": str(payload.get("case_id"))})
    if session_factory is not None:
        try:
            from app.notifications.dispatch import dispatch_notifications_for_event

            async with session_factory() as db:
                await dispatch_notifications_for_event(db, tenant_id, event_type, dict(body),
                                                       ref_id=str(payload.get("case_id")))
        except Exception:
            logger.exception("cases: could not notify %s for tenant %s", event_type, tenant_id)
    return announced or devices > 0


async def who_to_tell(db: AsyncSession, case: Mapping, user_id: Any, by: Any) -> list[str]:
    """Who to tell that they were put on a case: the person, if it was not they who did it and they can see the
    case. Read BEFORE the caller commits (the organisation's scope is the transaction's)."""
    if not user_id or str(user_id) == str(by):
        return []
    try:
        return await people(db, case.get("site_id"), user_ids=[user_id])
    except Exception:
        logger.exception("cases: could not read who to tell for case %s", case.get("id"))
        return []


async def put_on(redis, session_factory, tenant_id: str, case: Mapping, who: Sequence[str], *, as_what: str,
                 task: Mapping | None = None, zone: str | None = None) -> bool:
    """Tell them they were put on a case - as its lead, as an investigator, or given a task. AFTER the commit."""
    if not who:
        return False
    payload = {"case_id": str(case["id"]), "case_number": case["case_number"], "title": case["title"],
               "as": as_what, "task_id": str(task["id"]) if task else None,
               "site_id": str(case["site_id"]) if case.get("site_id") else None, "severity": "medium"}
    try:
        return await tell(redis, session_factory, tenant_id, ASSIGNED_EVENT, who,
                          put_on_words(case, as_what, task, zone), payload)
    except Exception:
        logger.exception("cases: could not tell about case %s", case.get("id"))
        return False


def put_on_words(case: Mapping, as_what: str, task: Mapping | None = None, zone: str | None = None) -> str:
    if as_what == "LEAD":
        return f"You are the lead on {_case_words(case)}"
    if as_what == "INVESTIGATOR":
        return f"You are an investigator on {_case_words(case)}"
    due = ""
    if task and task.get("due_at"):
        due = f", due {when(task['due_at'], zone)}"
    return f"Task for you on {case['case_number']}: {task['title'] if task else ''}{due}"
