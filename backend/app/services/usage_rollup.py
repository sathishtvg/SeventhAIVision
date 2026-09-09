"""Remember what today looked like, so tomorrow has something to compare with.

Runs nightly. Takes one snapshot per customer, rolls the month up from the
days, and scores each customer's health.

IDEMPOTENT, because it will be run twice. A scheduler restart, a catch-up after
an outage, somebody running it by hand — all of them must produce the same row
rather than a double count. In an operational table a double count is a bug
somebody notices; in an analytics table it is a lie that survives forever,
because nobody recomputes history.

THE HEALTH SCORE IS FOUR NUMBERS AND A TOTAL. A single figure tells somebody to
worry; the components tell them what about. A customer at 62% because nobody
has logged in for a month needs a different conversation from one at 62%
because they are three invoices behind, and a score that cannot tell those
apart is a number people learn to ignore.

WHAT IS SCORED IS WHAT PREDICTS CHURN. Logging in, using what they bought,
paying, and adopting more than the minimum. Deliberately not: how many cameras
they have, which is size rather than health, and would score a large unhappy
customer above a small delighted one.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: Each part is worth this much of the total. They sum to 100 — asserted in the
#: tests, because a scoring change that quietly stops adding up produces
#: numbers nobody can reason about.
WEIGHTS = {"login": 30, "usage": 25, "billing": 30, "adoption": 15}

#: A customer nobody has signed into for this long is not using the product,
#: whatever else the figures say.
STALE_LOGIN_DAYS = 30


async def snapshot_usage(session, when: date | None = None) -> int:
    """One row per customer for the given day. Upserted, never appended."""
    day = when or (date.today() - timedelta(days=1))
    result = await session.execute(text("""
        INSERT INTO platform_usage_daily
            (tenant_id, day, users, users_active, sites, cameras,
             cameras_active, modules_enabled, ai_events, storage_bytes)
        SELECT s.tenant_id, :day, s.users, s.users_active, s.sites, s.cameras,
               s.cameras_active, s.modules_enabled, s.ai_events, s.storage_bytes
          FROM platform_usage_snapshot() s
        ON CONFLICT (tenant_id, day) DO UPDATE
           SET users           = EXCLUDED.users,
               users_active    = EXCLUDED.users_active,
               sites           = EXCLUDED.sites,
               cameras         = EXCLUDED.cameras,
               cameras_active  = EXCLUDED.cameras_active,
               modules_enabled = EXCLUDED.modules_enabled,
               ai_events       = EXCLUDED.ai_events,
               storage_bytes   = EXCLUDED.storage_bytes,
               recorded_at     = now()
    """), {"day": day})
    return result.rowcount or 0


async def roll_up_month(session, when: date | None = None) -> int:
    """Collapse a month of days into one row.

    Counts are the month's LAST value, not a sum: a customer with fifty cameras
    every day for thirty days has fifty cameras, not fifteen hundred. Events
    are summed, because those really did each happen once.
    """
    day = when or (date.today() - timedelta(days=1))
    month = day.replace(day=1)
    result = await session.execute(text("""
        INSERT INTO platform_usage_monthly
            (tenant_id, month, users, sites, cameras, modules_enabled,
             ai_events, storage_bytes)
        SELECT tenant_id, :month,
               (array_agg(users           ORDER BY day DESC))[1],
               (array_agg(sites           ORDER BY day DESC))[1],
               (array_agg(cameras         ORDER BY day DESC))[1],
               (array_agg(modules_enabled ORDER BY day DESC))[1],
               sum(ai_events),
               (array_agg(storage_bytes   ORDER BY day DESC))[1]
          FROM platform_usage_daily
         -- CAST rather than :month::date. SQLAlchemy's bind parser reads
         -- the first colon as a parameter and leaves the postgres cast
         -- behind it, which reaches the server as a syntax error.
         WHERE day >= :month
           AND day < (CAST(:month AS date) + interval '1 month')
      GROUP BY tenant_id
        ON CONFLICT (tenant_id, month) DO UPDATE
           SET users           = EXCLUDED.users,
               sites           = EXCLUDED.sites,
               cameras         = EXCLUDED.cameras,
               modules_enabled = EXCLUDED.modules_enabled,
               ai_events       = EXCLUDED.ai_events,
               storage_bytes   = EXCLUDED.storage_bytes
    """), {"month": month})
    return result.rowcount or 0


def _login_score(days_since_login: float | None) -> int:
    """Logging in is the strongest single signal that a customer still wants
    the product. Never having logged in scores zero rather than being ignored."""
    if days_since_login is None:
        return 0
    if days_since_login <= 1:
        return WEIGHTS["login"]
    if days_since_login <= 7:
        return int(WEIGHTS["login"] * 0.8)
    if days_since_login <= STALE_LOGIN_DAYS:
        return int(WEIGHTS["login"] * 0.4)
    return 0


def _usage_score(cameras: int, cameras_active: int, users: int,
                 users_active: int) -> int:
    """Are they using what they bought?

    A customer with two hundred cameras of which nine are on is paying for
    something they are not getting value from, which is a cancellation waiting
    for a budget review.
    """
    if cameras == 0 and users == 0:
        return 0
    camera_ratio = (cameras_active / cameras) if cameras else 1.0
    user_ratio = (users_active / users) if users else 1.0
    return int(WEIGHTS["usage"] * (camera_ratio * 0.6 + user_ratio * 0.4))


def _billing_score(subscription_status: str | None, overdue_invoices: int) -> int:
    """Paying, and on what terms. An expired subscription scores zero however
    happily they are using the product."""
    if subscription_status in ("expired", "canceled", "cancelled"):
        return 0
    score = WEIGHTS["billing"]
    if subscription_status == "past_due":
        score = int(score * 0.4)
    elif subscription_status == "trialing":
        score = int(score * 0.7)
    # Each overdue invoice takes a quarter off, so four take all of it.
    #
    # The reduction is computed once against the weight rather than truncated
    # per invoice: int(30 * 0.25) is 7, and four sevens is 28, which left a
    # customer four invoices behind holding two points of billing health. The
    # arithmetic has to reach zero or the sentence describing it is false.
    reduction = WEIGHTS["billing"] * 0.25 * min(overdue_invoices, 4)
    return max(0, int(score - reduction))


def _adoption_score(modules_enabled: int) -> int:
    """How much of the platform they have taken up. Five modules is treated as
    full marks — beyond that it says more about their size than their health."""
    return int(WEIGHTS["adoption"] * min(modules_enabled, 5) / 5)


async def score_health(session) -> int:
    """Score every customer, and keep it. Returns how many were scored."""
    rows = (await session.execute(text("""
        SELECT t.id AS tenant_id, t.name, t.status AS tenant_status,
               u.users, u.users_active, u.cameras, u.cameras_active,
               u.modules_enabled,
               sub.status AS subscription_status,
               (SELECT count(*) FROM platform_invoices i
                 WHERE i.tenant_id = t.id AND i.status = 'overdue') AS overdue,
               (SELECT max(last_login) FROM (
                    SELECT max(day) AS last_login FROM platform_usage_daily d
                     WHERE d.tenant_id = t.id AND d.users_active > 0
               ) x) AS last_active_day
          FROM tenants t
     LEFT JOIN LATERAL (
              SELECT * FROM platform_usage_daily d
               WHERE d.tenant_id = t.id ORDER BY d.day DESC LIMIT 1
          ) u ON TRUE
     LEFT JOIN LATERAL (
              SELECT status FROM platform_subscription_states() s
               WHERE s.tenant_id = t.id LIMIT 1
          ) sub ON TRUE
         WHERE NOT t.is_platform
    """))).mappings().all()

    today = date.today()
    scored = 0
    for row in rows:
        last_day = row["last_active_day"]
        days_since = (today - last_day).days if last_day else None

        login = _login_score(days_since)
        usage = _usage_score(
            int(row["cameras"] or 0), int(row["cameras_active"] or 0),
            int(row["users"] or 0), int(row["users_active"] or 0))
        billing = _billing_score(row["subscription_status"], int(row["overdue"] or 0))
        adoption = _adoption_score(int(row["modules_enabled"] or 0))
        total = min(100, login + usage + billing + adoption)

        await session.execute(text("""
            INSERT INTO tenant_health
                (tenant_id, score, login_score, usage_score, billing_score,
                 adoption_score, detail, computed_at)
            VALUES (:tid, :score, :login, :usage, :billing, :adoption,
                    CAST(:detail AS jsonb), now())
            ON CONFLICT (tenant_id) DO UPDATE
               SET score          = EXCLUDED.score,
                   login_score    = EXCLUDED.login_score,
                   usage_score    = EXCLUDED.usage_score,
                   billing_score  = EXCLUDED.billing_score,
                   adoption_score = EXCLUDED.adoption_score,
                   detail         = EXCLUDED.detail,
                   computed_at    = now()
        """), {
            "tid": row["tenant_id"], "score": total, "login": login,
            "usage": usage, "billing": billing, "adoption": adoption,
            "detail": json.dumps({
                "days_since_activity": days_since,
                "subscription_status": row["subscription_status"],
                "overdue_invoices": int(row["overdue"] or 0),
                "modules_enabled": int(row["modules_enabled"] or 0),
                "cameras": int(row["cameras"] or 0),
                "cameras_active": int(row["cameras_active"] or 0),
            }),
        })
        scored += 1
    return scored


async def run(session) -> dict:
    """The nightly pass. Snapshot, roll up, score."""
    snapshotted = await snapshot_usage(session)
    rolled = await roll_up_month(session)
    scored = await score_health(session)
    await session.commit()
    return {"snapshotted": snapshotted, "rolled_up": rolled, "scored": scored}
