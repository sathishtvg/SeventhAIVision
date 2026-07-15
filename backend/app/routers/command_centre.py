"""Command & Control Centre — consolidated real-time overview of all sites."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

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

    # ── Assemble per-site cards ──────────────────────────────────────────────
    site_cards = []
    for s in sites:
        sid        = str(s["id"])
        alert_info = alerts_by_site.get(sid, {})
        site_cards.append({
            "id":               sid,
            "name":             s["name"],
            "address":          s.get("address"),
            "cameras_online":   int(s["cameras_online"]),
            "cameras_offline":  int(s["cameras_offline"]),
            "cameras_degraded": int(s["cameras_degraded"]),
            "cameras_total":    int(s["cameras_total"]),
            "active_alerts":    int(alert_info.get("active_alerts", 0)),
            "critical_alerts":  int(alert_info.get("critical", 0)),
            "high_alerts":      int(alert_info.get("high", 0)),
            "medium_alerts":    int(alert_info.get("medium", 0)),
            "guards":           guards_by_site.get(sid, []),
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
