"""The platform owner's console: how the business and the software are doing.

Every figure here answers "how is my Seventh AI Vision business performing",
never "what is happening at my customer's sites". A camera going offline at a
guarded warehouse is that company's emergency and none of the vendor's; the
vendor's emergency is the AI worker that stopped processing for all of them.

WHY THE AGGREGATES COME FROM FUNCTIONS. users, sites, cameras, licences and
subscriptions all have FORCE ROW LEVEL SECURITY and svc_app has no BYPASSRLS,
so a cross-tenant count written here would return zero — correctly, because
that is tenant isolation working. The SECURITY DEFINER functions in migration
0106 are the one sanctioned way through, and they return only counts and sums.
There is no route in this file that can produce a guard's name, an address or a
pay rate: seeing a customer's actual records still means opening a support
session and being logged doing it.

THE PLATFORM TENANT IS EXCLUDED FROM EVERY COUNT, inside those functions.
Seventh AI is not one of its own customers, and a dashboard that counts itself
lies about tenant totals, revenue and churn alike.

platform_errors and tenants carry no RLS — they are cross-tenant by nature —
so those are read directly.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_raw_db
from app.services import platform_health as platform_health_service

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])

#: Held by Super Admin alone. With no RLS confining these queries, this is the
#: only thing between them and every customer's figures, so it is on every
#: route rather than on the router — a route added later without it would be a
#: hole, and this way it cannot be added by omission.
_READ = Depends(require_permission("platform:read"))


async def _customer_or_404(db: AsyncSession, tenant_id: str):
    """A customer, never the vendor's own tenant.

    is_platform is checked here as well as inside the aggregate functions: the
    detail routes take an id from the URL, and 'show me the platform tenant's
    usage' should be a 404 rather than a confusing row of the vendor counting
    itself.
    """
    row = (await db.execute(
        text("""SELECT id, name, slug, status, is_active, created_at,
                       contact_name, contact_email, contact_phone, country,
                       address, timezone, trial_ends_at
                  FROM tenants
                 WHERE id = CAST(:tid AS uuid) AND NOT is_platform"""),
        {"tid": tenant_id},
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return row


@router.get("/dashboard", dependencies=[_READ])
async def dashboard(db: AsyncSession = Depends(get_raw_db)):
    """The KPI cards, plus what is currently broken.

    One round trip rather than eight. These are counts over small tables and
    the page shows them together, so separate endpoints would only give the
    page more ways to render half a picture.
    """
    kpis = (await db.execute(text("SELECT * FROM platform_kpis()"))).mappings().first()
    errors = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE status <> 'resolved' AND severity = 'critical')
                   AS errors_critical,
               count(*) FILTER (WHERE status <> 'resolved' AND severity = 'warning')
                   AS errors_warning,
               count(*) FILTER (WHERE status = 'resolved'
                                  AND resolved_at >= date_trunc('day', now()))
                   AS errors_resolved_today
          FROM platform_errors
    """))).mappings().first()
    return {**dict(kpis), **dict(errors)}


@router.get("/revenue", dependencies=[_READ])
async def revenue(db: AsyncSession = Depends(get_raw_db)):
    """What the customers are worth, derived rather than stored.

    A cached MRR is a number that goes stale silently. This one is computed
    from the subscriptions that exist, every time it is asked for.
    """
    row = (await db.execute(text("SELECT * FROM platform_revenue()"))).mappings().first()
    out = dict(row)
    out["mrr"] = float(out["mrr"] or 0)
    out["arr"] = out["mrr"] * 12
    return out


@router.get("/tenants", dependencies=[_READ])
async def tenant_usage(db: AsyncSession = Depends(get_raw_db)):
    """One row per customer: what they run, and what they pay for. (§7)"""
    rows = (await db.execute(
        text("SELECT * FROM platform_tenant_usage()")
    )).mappings().all()
    return [dict(r) for r in rows]


@router.get("/tenants/{tenant_id}/users", dependencies=[_READ])
async def tenant_user_statistics(
    tenant_id: str, db: AsyncSession = Depends(get_raw_db),
):
    """User statistics for one customer, without entering their tenant.

    The point of §9: answering "how many guards does ABC Security have" should
    not require the vendor to open a support session and walk through their
    staff list. A count is not personal data in the way a name and a pay rate
    are, and this route cannot return the latter.
    """
    tenant = await _customer_or_404(db, tenant_id)
    totals = (await db.execute(
        text("SELECT * FROM platform_tenant_user_stats(CAST(:tid AS uuid))"),
        {"tid": tenant_id},
    )).mappings().first()
    by_role = (await db.execute(
        text("SELECT * FROM platform_tenant_role_counts(CAST(:tid AS uuid))"),
        {"tid": tenant_id},
    )).mappings().all()
    return {"tenant": dict(tenant), **dict(totals),
            "by_role": [dict(r) for r in by_role]}


@router.get("/tenants/{tenant_id}/usage", dependencies=[_READ])
async def tenant_detail(tenant_id: str, db: AsyncSession = Depends(get_raw_db)):
    """One customer in full: company, what they run, and what they pay. (§8)"""
    tenant = await _customer_or_404(db, tenant_id)
    counts = (await db.execute(
        text("SELECT * FROM platform_tenant_counts(CAST(:tid AS uuid))"),
        {"tid": tenant_id},
    )).mappings().first()
    modules = (await db.execute(
        text("SELECT * FROM platform_tenant_modules(CAST(:tid AS uuid))"),
        {"tid": tenant_id},
    )).mappings().all()
    subscription = (await db.execute(
        text("SELECT * FROM platform_tenant_subscription(CAST(:tid AS uuid))"),
        {"tid": tenant_id},
    )).mappings().first()
    return {
        "tenant": dict(tenant),
        **dict(counts),
        "modules": [dict(m) for m in modules],
        "subscription": dict(subscription) if subscription else None,
    }


# ── Error centre (§18, §19) ──────────────────────────────────────────────────

@router.get("/errors", dependencies=[_READ])
async def list_errors(
    db: AsyncSession = Depends(get_raw_db),
    status_filter: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Distinct problems, worst and most recent first.

    Grouped, so one bad deploy is a single row with a count rather than forty
    thousand rows nobody scrolls through. The affected-tenant count is what
    turns "something is broken" into "this is hurting three customers", which
    is the difference between a log and a console.
    """
    where = ["1 = 1"]
    params: dict = {"limit": limit}
    if status_filter:
        where.append("e.status = :status")
        params["status"] = status_filter
    if severity:
        where.append("e.severity = :severity")
        params["severity"] = severity

    rows = (await db.execute(text(f"""
        SELECT e.id, e.fingerprint, e.service, e.severity, e.error_type,
               e.message, e.method, e.path_pattern, e.first_seen_at,
               e.last_seen_at, e.occurrence_count, e.status, e.resolution,
               e.resolved_at,
               (SELECT count(DISTINCT ev.tenant_id) FROM platform_error_events ev
                 WHERE ev.error_id = e.id AND ev.tenant_id IS NOT NULL)
                                                        AS tenants_affected
          FROM platform_errors e
         WHERE {' AND '.join(where)}
      ORDER BY CASE e.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
               e.last_seen_at DESC
         LIMIT :limit
    """), params)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/errors/{error_id}", dependencies=[_READ])
async def error_detail(error_id: str, db: AsyncSession = Depends(get_raw_db)):
    """One problem, its recent occurrences, and which customers they hit."""
    e = (await db.execute(
        text("SELECT * FROM platform_errors WHERE id = CAST(:id AS uuid)"),
        {"id": error_id},
    )).mappings().first()
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Error not found")

    events = (await db.execute(text("""
        SELECT ev.id, ev.occurred_at, ev.tenant_id, t.name AS tenant_name,
               ev.status_code, ev.request_id, ev.path, ev.stack
          FROM platform_error_events ev
     LEFT JOIN tenants t ON t.id = ev.tenant_id
         WHERE ev.error_id = CAST(:id AS uuid)
      ORDER BY ev.occurred_at DESC
         LIMIT 25
    """), {"id": error_id})).mappings().all()

    affected = (await db.execute(text("""
        SELECT t.id, t.name, count(*) AS occurrences
          FROM platform_error_events ev
          JOIN tenants t ON t.id = ev.tenant_id
         WHERE ev.error_id = CAST(:id AS uuid)
      GROUP BY t.id, t.name
      ORDER BY count(*) DESC
    """), {"id": error_id})).mappings().all()

    return {
        "error": dict(e),
        "recent_events": [dict(ev) for ev in events],
        "tenants_affected": [dict(a) for a in affected],
    }


class ErrorStatusUpdate(BaseModel):
    status: str = Field(pattern="^(open|acknowledged|resolved)$")
    resolution: str | None = None


@router.put("/errors/{error_id}", dependencies=[_READ])
async def update_error_status(
    error_id: str,
    body: ErrorStatusUpdate,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    """Acknowledge or resolve a problem.

    Resolving is not deleting. If the same fingerprint is seen again the
    collector reopens it, because a fix that did not hold is worse news than a
    new bug and quietly re-resolving it would bury exactly that.
    """
    # `resolving` is passed separately rather than comparing :status inline.
    # asyncpg deduces a parameter's type from how it is used, and :status was
    # both assigned to a varchar(20) column and compared against a text
    # literal — "inconsistent types deduced for parameter $1", which surfaces
    # as a 500 and says nothing about which parameter.
    row = (await db.execute(text("""
        UPDATE platform_errors
           SET status      = :status,
               resolution  = COALESCE(:resolution, resolution),
               resolved_at = CASE WHEN :resolving THEN now() ELSE NULL END,
               resolved_by = CASE WHEN :resolving THEN CAST(:uid AS uuid) ELSE NULL END
         WHERE id = CAST(:id AS uuid)
     RETURNING id, status, resolution, resolved_at
    """), {"id": error_id, "status": body.status,
           "resolving": body.status == "resolved",
           "resolution": body.resolution, "uid": token.user_id})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Error not found")
    await db.commit()
    return dict(row)

# ── Platform health (§4, §18) ────────────────────────────────────────────────

@router.get("/health", dependencies=[_READ])
async def platform_health(db: AsyncSession = Depends(get_raw_db)):
    """Is the platform itself working?

    Measured, not reported: every figure comes from asking the thing itself,
    and anything that cannot be asked comes back `unknown` rather than as a
    reassuring green. A dashboard that shows healthy because a check failed to
    run is worse than no dashboard, because it is believed.
    """
    return await platform_health_service.collect(db)


# ── Notifications and policy (§15, §17) ──────────────────────────────────────

@router.get("/notifications", dependencies=[_READ])
async def list_notifications(
    db: AsyncSession = Depends(get_raw_db),
    include_acknowledged: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Business events needing a decision.

    Not platform_errors, which is things that broke. A trial ending and a
    database falling over want different people at different times, and mixing
    them buries one in the other.
    """
    where = "" if include_acknowledged else "WHERE n.acknowledged_at IS NULL"
    rows = (await db.execute(text(f"""
        SELECT n.*, t.name AS tenant_name
          FROM platform_notifications n
     LEFT JOIN tenants t ON t.id = n.tenant_id
        {where}
      ORDER BY CASE n.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1
                               ELSE 2 END,
               n.last_seen_at DESC
         LIMIT :limit
    """), {"limit": limit})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/notifications/{notification_id}/acknowledge", dependencies=[_READ])
async def acknowledge_notification(
    notification_id: str,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    """Mark one dealt with.

    The same thing recurring afterwards raises a fresh notice rather than being
    swallowed — deduplication only applies while one is still open.
    """
    row = (await db.execute(text("""
        UPDATE platform_notifications
           SET acknowledged_at = now(), acknowledged_by = CAST(:uid AS uuid)
         WHERE id = CAST(:id AS uuid) AND acknowledged_at IS NULL
     RETURNING id, kind, acknowledged_at
    """), {"id": notification_id, "uid": token.user_id})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No open notification with that id")
    await db.commit()
    return dict(row)


@router.get("/settings", dependencies=[_READ])
async def list_settings(db: AsyncSession = Depends(get_raw_db)):
    """Vendor policy. Every number the lifecycle acts on is here rather than in
    code, because they are commercial decisions that should change without a
    deploy."""
    rows = (await db.execute(text(
        "SELECT key, value, description, updated_at FROM platform_settings "
        " ORDER BY key"))).mappings().all()
    return [dict(r) for r in rows]


class SettingUpdate(BaseModel):
    value: str


@router.put("/settings/{key}", dependencies=[_READ])
async def update_setting(
    key: str,
    body: SettingUpdate,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    """Change one policy value.

    updated_by is recorded because one of these switches off a customer's
    access, and "who decided that" should have an answer.
    """
    row = (await db.execute(text("""
        UPDATE platform_settings
           SET value = :value, updated_at = now(), updated_by = CAST(:uid AS uuid)
         WHERE key = :key
     RETURNING key, value, description, updated_at
    """), {"key": key, "value": body.value, "uid": token.user_id})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such setting")
    await db.commit()
    return dict(row)


# ── Analytics (§20-§24) ──────────────────────────────────────────────────────

@router.get("/analytics/growth", dependencies=[_READ])
async def growth(
    db: AsyncSession = Depends(get_raw_db),
    days: int = Query(default=90, ge=7, le=730),
):
    """The platform day by day: customers, users, cameras, AI events, storage.

    Read from the daily snapshots rather than computed live, because the whole
    point is the direction. Counting now tells you there are 128 tenants; it
    cannot tell you whether that is good news.

    Counts are summed ACROSS tenants per day and events are summed too, but
    they mean different things: a hundred cameras on Tuesday and a hundred on
    Wednesday is a hundred cameras, whereas a hundred detections each day is
    two hundred detections.
    """
    rows = (await db.execute(text("""
        SELECT day,
               count(*)                     AS tenants,
               sum(users)                   AS users,
               sum(sites)                   AS sites,
               sum(cameras)                 AS cameras,
               sum(ai_events)               AS ai_events,
               sum(storage_bytes)           AS storage_bytes
          FROM platform_usage_daily
         WHERE day >= CURRENT_DATE - make_interval(days => :days)
      GROUP BY day
      ORDER BY day
    """), {"days": days})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/analytics/adoption", dependencies=[_READ])
async def adoption(db: AsyncSession = Depends(get_raw_db)):
    """Which modules customers actually switched on (§22).

    The question behind it is a product one: a module nobody adopted is either
    priced wrong, hard to find, or not worth building further.
    """
    rows = (await db.execute(
        text("SELECT * FROM platform_module_adoption()")
    )).mappings().all()
    total = (await db.execute(
        text("SELECT count(*) FROM tenants WHERE NOT is_platform")
    )).scalar() or 0
    return {
        "customers": total,
        "modules": [
            {**dict(r),
             "adoption_percent": round(100 * (r["tenants"] or 0) / total, 1)
                                 if total else 0}
            for r in rows
        ],
    }


@router.get("/analytics/health", dependencies=[_READ])
async def tenant_health(db: AsyncSession = Depends(get_raw_db)):
    """Every customer's health score, worst first (§23).

    Worst first because this list is a work queue, not a leaderboard — the
    customer at 38% is the one somebody should telephone this week.
    """
    rows = (await db.execute(text("""
        SELECT h.*, t.name AS tenant_name, t.slug, t.status AS tenant_status
          FROM tenant_health h
          JOIN tenants t ON t.id = h.tenant_id AND NOT t.is_platform
      ORDER BY h.score
    """))).mappings().all()
    return [dict(r) for r in rows]
