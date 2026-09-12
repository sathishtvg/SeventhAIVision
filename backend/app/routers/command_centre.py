"""Command & Control Centre — consolidated real-time overview of all sites."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services.attendance_status import (
    get_attendance_setting,
    is_overdue_sql,
    live_status,
)

router = APIRouter(prefix="/api/v1/command-centre", tags=["command-centre"])


@router.get("/overview", dependencies=[Depends(require_permission("alert:read"))])
async def get_cc_overview(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Return a single consolidated payload used by the C&C dashboard:
    per-site camera health, alert counts, guards on active shift, and the
    20 most recent critical/high alerts across all sites. Site-restricted
    users (Gap 81) receive only their assigned sites' data."""
    scope_params: dict = {}
    _scope = site_scope_clause(allowed_sites, "__col__", scope_params)

    def scoped(column: str) -> str:
        """Return ' AND <site filter on column>' or '' when unrestricted."""
        if _scope is None:
            return ""
        if _scope == "FALSE":
            return " AND FALSE"
        return f" AND {column} = ANY(:allowed_site_ids)"

    # ── Sites with camera status breakdown ──────────────────────────────────
    # Camera status lives in the streams table (cameras has no status column).
    # Use a LATERAL subquery to get each camera's latest stream status.
    sites_result = await db.execute(text(f"""
        SELECT
            s.id,
            s.name,
            s.address,
            -- Coordinates so the map view can plot this card without a second
            -- round trip. NULL for sites that haven't been surveyed yet; the
            -- map lists those separately rather than dropping them silently.
            s.latitude,
            s.longitude,
            COALESCE(SUM(CASE WHEN ls.status = 'online'   THEN 1 ELSE 0 END), 0) AS cameras_online,
            COALESCE(SUM(CASE WHEN ls.status = 'offline'  THEN 1 ELSE 0 END), 0) AS cameras_offline,
            COALESCE(SUM(CASE WHEN ls.status = 'degraded' THEN 1 ELSE 0 END), 0) AS cameras_degraded,
            COALESCE(COUNT(DISTINCT c.id), 0) AS cameras_total
        FROM sites s
        LEFT JOIN cameras c ON c.site_id = s.id AND c.is_active = TRUE
        LEFT JOIN LATERAL (
            SELECT status
            FROM streams st
            WHERE st.camera_id = c.id
            ORDER BY st.updated_at DESC
            LIMIT 1
        ) ls ON TRUE
        WHERE s.is_active = TRUE{scoped("s.id")}
        GROUP BY s.id, s.name, s.address
        ORDER BY s.name
    """), scope_params)
    sites = [dict(r._mapping) for r in sites_result]

    # ── Active alert counts per site ─────────────────────────────────────────
    alerts_result = await db.execute(text(f"""
        SELECT
            s.id                                                                  AS site_id,
            COUNT(*)                                                              AS active_alerts,
            SUM(CASE WHEN a.severity = 'critical' THEN 1 ELSE 0 END)            AS critical,
            SUM(CASE WHEN a.severity = 'high'     THEN 1 ELSE 0 END)            AS high,
            SUM(CASE WHEN a.severity = 'medium'   THEN 1 ELSE 0 END)            AS medium
        FROM alerts a
        JOIN cameras c ON c.id = a.camera_id
        JOIN sites   s ON s.id = c.site_id
        WHERE a.status IN ('open', 'acknowledged'){scoped("c.site_id")}
        GROUP BY s.id
    """), scope_params)
    alerts_by_site = {str(r._mapping["site_id"]): dict(r._mapping) for r in alerts_result}

    # ── Active guards with last checkpoint scan time ─────────────────────────
    guards_result = await db.execute(text(f"""
        SELECT
            sh.id           AS shift_id,
            sh.site_id,
            sh.actual_start,
            sh.scheduled_end,
            u.full_name     AS guard_name,
            (
                SELECT MAX(cs.scanned_at)
                FROM checkpoint_scans cs
                JOIN patrol_sessions  ps ON ps.id = cs.session_id
                WHERE ps.guard_user_id = sh.guard_user_id
                  AND cs.scanned_at   >= sh.actual_start
            ) AS last_checkpoint_at
        FROM shifts sh
        JOIN users u ON u.id = sh.guard_user_id
        WHERE sh.status = 'active'{scoped("sh.site_id")}
        ORDER BY u.full_name
    """), scope_params)
    guards_raw = [dict(r._mapping) for r in guards_result]

    guards_by_site: dict = {}
    all_guards = []
    for g in guards_raw:
        sid = str(g["site_id"]) if g["site_id"] else "__none__"
        entry = {
            "guard_name":         g["guard_name"],
            "shift_id":           str(g["shift_id"]),
            "site_id":            sid,
            "actual_start":       g["actual_start"].isoformat()       if g["actual_start"]       else None,
            "scheduled_end":      g["scheduled_end"].isoformat()      if g["scheduled_end"]       else None,
            "last_checkpoint_at": g["last_checkpoint_at"].isoformat() if g["last_checkpoint_at"] else None,
        }
        guards_by_site.setdefault(sid, []).append(entry)
        all_guards.append(entry)

    # ── Today's full roster, with presence state ─────────────────────────────
    # Deliberately SEPARATE from `guards` above rather than widening it.
    # `guards` means "on an active shift" and the Command Centre renders
    # `guards.length` directly as "Guards on Duty" — folding rostered-but-
    # absent guards into it would silently count a no-show as present, which
    # is the exact opposite of what this data is for.
    #
    # The Site Map needs the absences: a marker that can only ever say
    # "manned" is not showing status, and an unmanned site is the single
    # most important thing a site map can tell an operator.
    grace_minutes = await get_attendance_setting(db, "attendance.late_grace_minutes")
    roster_params = {**scope_params, "grace_minutes": grace_minutes}
    roster_result = await db.execute(text(f"""
        SELECT
            sh.id            AS shift_id,
            sh.site_id,
            sh.status,
            sh.is_late,
            sh.late_minutes,
            sh.scheduled_start,
            sh.scheduled_end,
            sh.actual_start,
            u.full_name      AS guard_name,
            u.phone          AS guard_phone,
            EXISTS(
                SELECT 1 FROM shift_breaks b
                WHERE b.shift_id = sh.id AND b.break_end IS NULL
            ) AS on_break,
            {is_overdue_sql("sh")} AS is_overdue
        FROM shifts sh
        JOIN tenants t ON t.id = sh.tenant_id
        LEFT JOIN users u ON u.id = sh.guard_user_id
        WHERE (
            -- (a) Scheduled for the tenant's local calendar day. Local, not
            -- the Postgres server's day: a Singapore tenant's "today" must
            -- not roll over at 08:00 local (same fix as attendance.py).
            (sh.scheduled_start AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date
              = (now() AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date

            -- (b) OR its window covers this moment. Without this a night
            -- shift that began before local midnight silently vanishes at
            -- 00:00 — measured on real data, a 16:00→04:00 shift with
            -- nobody checked in disappeared from the board at exactly the
            -- hour it most needed watching. "Today's date" is the wrong
            -- question for a 24-hour operation; "covering now" is right.
            OR (now() BETWEEN sh.scheduled_start AND sh.scheduled_end)

            -- (c) OR someone is actually on it, whenever it started.
            OR sh.status = 'active'
        )
        {scoped("sh.site_id")}
        ORDER BY sh.scheduled_start, u.full_name
    """), roster_params)

    roster_by_site: dict = {}
    for r in roster_result.mappings():
        row = dict(r)
        sid = str(row["site_id"]) if row["site_id"] else "__none__"
        roster_by_site.setdefault(sid, []).append({
            "shift_id":        str(row["shift_id"]),
            "guard_name":      row["guard_name"],
            "guard_phone":     row["guard_phone"],
            "live_status":     live_status(row),
            "is_overdue":      bool(row["is_overdue"]),
            "late_minutes":    row["late_minutes"],
            "scheduled_start": row["scheduled_start"].isoformat() if row["scheduled_start"] else None,
            "scheduled_end":   row["scheduled_end"].isoformat()   if row["scheduled_end"]   else None,
            "actual_start":    row["actual_start"].isoformat()    if row["actual_start"]    else None,
        })

    # ── Assemble per-site cards ──────────────────────────────────────────────
    site_cards = []
    for s in sites:
        sid        = str(s["id"])
        alert_info = alerts_by_site.get(sid, {})
        site_cards.append({
            "id":               sid,
            "name":             s["name"],
            "address":          s.get("address"),
            # Floats, not Decimal — these are serialised straight to JSON for
            # the map and Decimal would need a custom encoder.
            "latitude":         float(s["latitude"]) if s["latitude"] is not None else None,
            "longitude":        float(s["longitude"]) if s["longitude"] is not None else None,
            "cameras_online":   int(s["cameras_online"]),
            "cameras_offline":  int(s["cameras_offline"]),
            "cameras_degraded": int(s["cameras_degraded"]),
            "cameras_total":    int(s["cameras_total"]),
            "active_alerts":    int(alert_info.get("active_alerts", 0)),
            "critical_alerts":  int(alert_info.get("critical", 0)),
            "high_alerts":      int(alert_info.get("high", 0)),
            "medium_alerts":    int(alert_info.get("medium", 0)),
            "guards":           guards_by_site.get(sid, []),
            "roster":           roster_by_site.get(sid, []),
        })

    # ── Active alert total ────────────────────────────────────────────────────
    # Unrestricted users: tenant-wide (all cameras, with or without a site).
    # Site-restricted users: only alerts from cameras at their allowed sites.
    if _scope is None:
        total_active_alerts_row = await db.execute(text(
            "SELECT COUNT(*) FROM alerts WHERE status IN ('open', 'acknowledged')"
        ))
    else:
        total_active_alerts_row = await db.execute(text(f"""
            SELECT COUNT(*) FROM alerts a
            JOIN cameras c ON c.id = a.camera_id
            WHERE a.status IN ('open', 'acknowledged'){scoped("c.site_id")}
        """), scope_params)
    total_active_alerts = int(total_active_alerts_row.scalar() or 0)

    # ── Tenant-wide summary ──────────────────────────────────────────────────
    summary = {
        "total_sites":       len(site_cards),
        "cameras_online":    sum(s["cameras_online"]   for s in site_cards),
        "cameras_offline":   sum(s["cameras_offline"]  for s in site_cards),
        "cameras_degraded":  sum(s["cameras_degraded"] for s in site_cards),
        "active_alerts":     total_active_alerts,
        "critical_alerts":   sum(s["critical_alerts"]  for s in site_cards),
        "high_alerts":       sum(s["high_alerts"]      for s in site_cards),
        "guards_on_duty":    len(all_guards),
    }

    # ── 20 most recent critical/high alerts (last 4 hours) ───────────────────
    recent_result = await db.execute(text(f"""
        SELECT
            a.id, a.title, a.severity, a.module_type, a.status, a.created_at,
            c.name AS camera_name,
            s.name AS site_name
        FROM alerts a
        JOIN cameras c ON c.id = a.camera_id
        LEFT JOIN sites s ON s.id = c.site_id
        WHERE a.severity IN ('critical', 'high')
          AND a.created_at > now() - INTERVAL '4 hours'{scoped("c.site_id")}
        ORDER BY a.created_at DESC
        LIMIT 20
    """), scope_params)
    recent_alerts = []
    for r in recent_result:
        d = dict(r._mapping)
        d["id"]         = str(d["id"])
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        recent_alerts.append(d)

    return {
        "summary":       summary,
        "sites":         site_cards,
        "recent_alerts": recent_alerts,
        "guards":        all_guards,
    }


@router.get("/virtual-patrol",
            dependencies=[Depends(require_permission("vpatrol:read"))])
async def get_virtual_patrol_overview(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """What the operations room needs to know about camera patrols right now.

    A SEPARATE ENDPOINT, NOT BOLTED ONTO /overview. That call is made
    constantly by every open Command Centre; adding four more aggregates to it
    would slow the board everybody watches in order to serve a panel not every
    tenant has. It is also gated on vpatrol:read, so a tenant without the module
    gets a clean 403 rather than an empty section implying something is broken.

    SITE SCOPING IS APPLIED, exactly as it is to alerts and guards. A supervisor
    restricted to two sites must not read the findings of a third — a patrol
    exception names a camera, a time and what was wrong with it, which is
    precisely the kind of detail site restrictions exist to contain.
    """
    params: dict = {}
    scope = site_scope_clause(allowed_sites, "s.site_id", params)
    where_scope = f" AND {scope}" if scope else ""

    summary = (await db.execute(text(f"""
        SELECT
          count(*) FILTER (WHERE s.status IN ('STARTED','IN_PROGRESS'))   AS active,
          count(*) FILTER (WHERE s.status = 'SCHEDULED')                  AS scheduled,
          count(*) FILTER (WHERE s.status = 'COMPLETED'
                             AND s.completed_at > now() - interval '24 hours') AS completed_today,
          count(*) FILTER (WHERE s.status = 'PARTIALLY_COMPLETED'
                             AND s.completed_at > now() - interval '24 hours') AS partial_today,
          count(*) FILTER (WHERE s.status = 'MISSED'
                             AND s.scheduled_for > now() - interval '24 hours') AS missed_today
          FROM virtual_patrol_sessions s
         WHERE TRUE{where_scope}
    """), params)).mappings().first()

    in_progress = (await db.execute(text(f"""
        SELECT s.id, s.patrol_number, s.schedule_name, s.status,
               s.camera_count, s.completed_camera_count, s.started_at,
               si.name AS site_name, u.full_name AS officer_name
          FROM virtual_patrol_sessions s
          JOIN sites si ON si.id = s.site_id
          LEFT JOIN users u ON u.id = s.officer_user_id
         WHERE s.status IN ('STARTED','IN_PROGRESS','SCHEDULED'){where_scope}
         ORDER BY s.scheduled_for
         LIMIT 20
    """), params)).mappings().all()

    # Exceptions carry everything an operator needs to act without opening the
    # patrol: which camera, what was asked, what the officer answered, whether
    # there is a picture, and whether an incident already exists.
    exceptions = (await db.execute(text(f"""
        SELECT a.id, a.answer_text, a.answered_at, a.incident_id,
               q.question_text,
               c.id AS session_camera_id, c.camera_name,
               (c.snapshot_path IS NOT NULL) AS has_snapshot,
               s.id AS session_id, s.patrol_number, s.schedule_name,
               si.name AS site_name, u.full_name AS officer_name
          FROM virtual_patrol_session_answers a
          JOIN virtual_patrol_session_questions q ON q.id = a.session_question_id
          JOIN virtual_patrol_session_cameras c ON c.id = q.session_camera_id
          JOIN virtual_patrol_sessions s ON s.id = c.session_id
          JOIN sites si ON si.id = s.site_id
          LEFT JOIN users u ON u.id = a.answered_by_user_id
         WHERE a.is_exception
           AND a.answered_at > now() - interval '48 hours'{where_scope}
         ORDER BY a.answered_at DESC
         LIMIT 25
    """), params)).mappings().all()

    return {
        "summary": dict(summary) if summary else {},
        "in_progress": [dict(r) for r in in_progress],
        "exceptions": [dict(r) for r in exceptions],
    }
