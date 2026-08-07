"""Action Center — role-aware "what needs my attention right now?" feed.

Turns the platform from a passive dashboard into an active guide: for a
control-room / supervisor role it surfaces the operational items needing a
human (guards to contact, unacknowledged alerts, overdue checkpoints,
pending approvals, offline cameras); for a security guard it surfaces that
guard's own pending duties (check in, scan checkpoint, respond to an
assigned incident, renew an expiring document).

Reuses the exact joins already proven in command_centre.py (guards-on-duty,
alert counts, camera status, last-checkpoint), site-scoped for restricted
users via the Gap-81 dependencies. No new tables or permissions — the
endpoint's role-awareness is the gate; each role only ever sees its own
scope, so any authenticated user may call it.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/action-center", tags=["action-center"])

# Roles that get the operational (control-room / supervisor) feed.
_OPS_ROLES = {1, 2, 3, 4, 8}
# Roles that additionally see pending approvals (supervisor+).
_APPROVER_ROLES = {1, 2, 3, 8}
_GUARD_ROLE = 5

# Minutes past a guard's scheduled start before they count as "to contact",
# and minutes since a guard's last checkpoint scan before a patrol is "overdue".
# (Hardcoded for now — roadmap: move to tenant_settings via config_keys.py.)
_CONTACT_GRACE_MIN = 5
_CHECKPOINT_STALE_MIN = 45

_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@router.get("")
async def get_action_center(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    items: list[dict] = []

    if token.role_id == _GUARD_ROLE:
        items = await _guard_duties(db, token.user_id)
    elif token.role_id in _OPS_ROLES:
        items = await _ops_feed(db, token.role_id, allowed_sites)

    items.sort(key=lambda it: (_SEV_RANK.get(it["severity"], 9), it.get("_order", 0)))
    for it in items:
        it.pop("_order", None)

    summary = {
        "total": len(items),
        "critical": sum(1 for i in items if i["severity"] == "critical"),
        "high": sum(1 for i in items if i["severity"] == "high"),
        "medium": sum(1 for i in items if i["severity"] == "medium"),
        "low": sum(1 for i in items if i["severity"] == "low"),
    }
    return {"summary": summary, "items": items}


# ── Operational feed (control room / supervisor) ─────────────────────────────

async def _ops_feed(db: AsyncSession, role_id: int, allowed_sites: list[str] | None) -> list[dict]:
    params: dict = {}
    _scope = site_scope_clause(allowed_sites, "__c__", params)

    def scoped(column: str) -> str:
        if _scope is None:
            return ""
        if _scope == "FALSE":
            return " AND FALSE"
        return f" AND {column} = ANY(:allowed_site_ids)"

    items: list[dict] = []

    # 1. Guards to contact — today's shifts past scheduled start, not checked in.
    contact = await db.execute(text(f"""
        SELECT sh.id, u.full_name AS guard_name, u.phone AS guard_phone,
               s.name AS site_name, sh.scheduled_start,
               EXTRACT(EPOCH FROM (now() - sh.scheduled_start)) / 60 AS mins_over
        FROM shifts sh
        JOIN tenants t ON t.id = sh.tenant_id
        LEFT JOIN users u ON u.id = sh.guard_user_id
        LEFT JOIN sites s ON s.id = sh.site_id
        WHERE sh.status = 'scheduled'
          AND (sh.scheduled_start AT TIME ZONE COALESCE(t.timezone,'UTC'))::date
            = (now() AT TIME ZONE COALESCE(t.timezone,'UTC'))::date
          AND now() > sh.scheduled_start + (INTERVAL '1 minute' * :grace)
          {scoped("sh.site_id")}
        ORDER BY sh.scheduled_start
    """), {**params, "grace": _CONTACT_GRACE_MIN})
    for r in contact.mappings():
        mins = int(r["mins_over"] or 0)
        items.append({
            "id": f"contact_guard:{r['id']}",
            "category": "contact_guard",
            "severity": "high",
            "title": f"Contact {r['guard_name'] or 'guard'} — {mins}m overdue",
            "subtitle": f"{r['site_name'] or 'No site'} · has not checked in",
            "action_route": "/attendance",
            "phone": r["guard_phone"],
            "entity_id": str(r["id"]),
            "_order": -mins,
        })

    # 2. Unacknowledged critical/high alerts.
    alerts = await db.execute(text(f"""
        SELECT a.id, a.title, a.severity, a.module_type,
               s.name AS site_name, c.name AS camera_name, a.created_at
        FROM alerts a
        JOIN cameras c ON c.id = a.camera_id
        LEFT JOIN sites s ON s.id = c.site_id
        WHERE a.status = 'open' AND a.severity IN ('critical','high')
          {scoped("c.site_id")}
        ORDER BY a.created_at DESC
        LIMIT 25
    """), params)
    for r in alerts.mappings():
        items.append({
            "id": f"ack_alert:{r['id']}",
            "category": "ack_alert",
            "severity": r["severity"],
            "title": f"Acknowledge: {r['title']}",
            "subtitle": f"{r['site_name'] or '—'} · {r['camera_name']}",
            "action_route": "/alerts",
            "entity_id": str(r["id"]),
            # Enough to open the full response dialog in place — acknowledge,
            # false-positive, resolve or escalate without leaving this page or
            # making a second request. An operator working a queue of alerts
            # shouldn't have to navigate away and find their place again.
            "alert": {
                "id": str(r["id"]),
                "title": r["title"],
                "severity": r["severity"],
                "module_type": r["module_type"],
                "site_name": r["site_name"],
                "camera_name": r["camera_name"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            },
        })

    # 3. Overdue checkpoints — active guards whose last scan is stale (or none yet).
    overdue = await db.execute(text(f"""
        SELECT sh.id, u.full_name AS guard_name, s.name AS site_name,
               (SELECT MAX(cs.scanned_at) FROM checkpoint_scans cs
                JOIN patrol_sessions ps ON ps.id = cs.session_id
                WHERE ps.guard_user_id = sh.guard_user_id AND cs.scanned_at >= sh.actual_start) AS last_scan
        FROM shifts sh
        JOIN users u ON u.id = sh.guard_user_id
        LEFT JOIN sites s ON s.id = sh.site_id
        WHERE sh.status = 'active'{scoped("sh.site_id")}
    """), params)
    now = datetime.now(timezone.utc)
    for r in overdue.mappings():
        last = r["last_scan"]
        if last is None:
            note, is_overdue = "no checkpoint scan yet this shift", True
        else:
            age = (now - last).total_seconds() / 60
            note, is_overdue = f"last scan {int(age)}m ago", age > _CHECKPOINT_STALE_MIN
        if not is_overdue:
            continue
        items.append({
            "id": f"overdue_checkpoint:{r['id']}",
            "category": "overdue_checkpoint",
            "severity": "medium",
            "title": f"{r['guard_name']}'s patrol overdue",
            "subtitle": f"{r['site_name'] or 'No site'} · {note}",
            "action_route": "/roster",
            "entity_id": str(r["id"]),
        })

    # 4. Pending approvals (supervisor+).
    if role_id in _APPROVER_ROLES:
        pend = await db.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM attendance_corrections WHERE status = 'pending') AS corrections,
              (SELECT COUNT(*) FROM leave_requests        WHERE status = 'pending') AS leaves
        """))
        pr = pend.mappings().first()
        c_ct, l_ct = int(pr["corrections"] or 0), int(pr["leaves"] or 0)
        if c_ct:
            items.append({
                "id": "pending_approval:corrections", "category": "pending_approval",
                "severity": "low", "title": f"{c_ct} attendance correction{'s' if c_ct != 1 else ''} to review",
                "subtitle": "Awaiting approval", "action_route": "/attendance", "entity_id": "corrections",
            })
        if l_ct:
            items.append({
                "id": "pending_approval:leave", "category": "pending_approval",
                "severity": "low", "title": f"{l_ct} leave request{'s' if l_ct != 1 else ''} to review",
                "subtitle": "Awaiting approval", "action_route": "/leave", "entity_id": "leave",
            })

    # 5. Offline cameras per site.
    offline = await db.execute(text(f"""
        SELECT s.id AS site_id, s.name AS site_name,
               SUM(CASE WHEN ls.status = 'offline' THEN 1 ELSE 0 END) AS offline
        FROM sites s
        JOIN cameras c ON c.site_id = s.id AND c.is_active = TRUE
        LEFT JOIN LATERAL (
            SELECT status FROM streams st WHERE st.camera_id = c.id ORDER BY st.updated_at DESC LIMIT 1
        ) ls ON TRUE
        WHERE s.is_active = TRUE{scoped("s.id")}
        GROUP BY s.id, s.name
        HAVING SUM(CASE WHEN ls.status = 'offline' THEN 1 ELSE 0 END) > 0
    """), params)
    for r in offline.mappings():
        n = int(r["offline"] or 0)
        items.append({
            "id": f"camera_offline:{r['site_id']}", "category": "camera_offline",
            "severity": "medium", "title": f"{n} camera{'s' if n != 1 else ''} offline at {r['site_name']}",
            "subtitle": "Check camera / network", "action_route": "/cameras", "entity_id": str(r["site_id"]),
        })

    # 6. Geofence-breach & mock-GPS check-ins/outs — today's shifts flagged at
    #    check-in/out time (shifts.py already captures is_within_geofence /
    #    check_in_is_mock_location / check_out_is_mock_location; this just
    #    surfaces them here so an officer sees it, per the roadmap note).
    flagged = await db.execute(text(f"""
        SELECT sh.id, u.full_name AS guard_name, s.name AS site_name,
               sh.is_within_geofence, sh.check_in_is_mock_location, sh.check_out_is_mock_location
        FROM shifts sh
        JOIN tenants t ON t.id = sh.tenant_id
        LEFT JOIN users u ON u.id = sh.guard_user_id
        LEFT JOIN sites s ON s.id = sh.site_id
        WHERE sh.actual_start IS NOT NULL
          AND (sh.actual_start AT TIME ZONE COALESCE(t.timezone,'UTC'))::date
            = (now() AT TIME ZONE COALESCE(t.timezone,'UTC'))::date
          AND (sh.is_within_geofence = FALSE OR sh.check_in_is_mock_location = TRUE OR sh.check_out_is_mock_location = TRUE)
          {scoped("sh.site_id")}
        ORDER BY sh.actual_start DESC
    """), params)
    for r in flagged.mappings():
        mock = bool(r["check_in_is_mock_location"] or r["check_out_is_mock_location"])
        reasons = []
        if r["check_in_is_mock_location"]:
            reasons.append("mock GPS at check-in")
        if r["check_out_is_mock_location"]:
            reasons.append("mock GPS at check-out")
        if r["is_within_geofence"] is False:
            reasons.append("outside site geofence")
        items.append({
            "id": f"geofence_flag:{r['id']}",
            "category": "geofence_flag",
            "severity": "high" if mock else "medium",
            "title": f"{r['guard_name'] or 'Guard'} — {', '.join(reasons)}",
            "subtitle": r["site_name"] or "No site",
            "action_route": "/attendance",
            "entity_id": str(r["id"]),
        })

    return items


# ── Guard self-duty feed ─────────────────────────────────────────────────────

async def _guard_duties(db: AsyncSession, user_id: str) -> list[dict]:
    items: list[dict] = []
    p = {"uid": user_id}

    # 1. Check in — today's scheduled shift not yet started.
    checkin = await db.execute(text("""
        SELECT sh.id, s.name AS site_name, sh.scheduled_start
        FROM shifts sh
        JOIN tenants t ON t.id = sh.tenant_id
        LEFT JOIN sites s ON s.id = sh.site_id
        WHERE sh.guard_user_id = CAST(:uid AS uuid) AND sh.status = 'scheduled'
          AND (sh.scheduled_start AT TIME ZONE COALESCE(t.timezone,'UTC'))::date
            = (now() AT TIME ZONE COALESCE(t.timezone,'UTC'))::date
        ORDER BY sh.scheduled_start
    """), p)
    for r in checkin.mappings():
        items.append({
            "id": f"check_in:{r['id']}", "category": "check_in", "severity": "high",
            "title": f"Check in for your shift at {r['site_name'] or 'your site'}",
            "subtitle": "Tap to start your shift", "action_route": "Shift", "entity_id": str(r["id"]),
        })

    # 2. Patrol / checkpoint due on your active shift.
    active = await db.execute(text("""
        SELECT sh.id, s.name AS site_name,
               (SELECT MAX(cs.scanned_at) FROM checkpoint_scans cs
                JOIN patrol_sessions ps ON ps.id = cs.session_id
                WHERE ps.guard_user_id = sh.guard_user_id AND cs.scanned_at >= sh.actual_start) AS last_scan
        FROM shifts sh
        LEFT JOIN sites s ON s.id = sh.site_id
        WHERE sh.guard_user_id = CAST(:uid AS uuid) AND sh.status = 'active'
    """), p)
    now = datetime.now(timezone.utc)
    for r in active.mappings():
        last = r["last_scan"]
        overdue = last is None or (now - last).total_seconds() / 60 > _CHECKPOINT_STALE_MIN
        if overdue:
            items.append({
                "id": f"patrol_due:{r['id']}", "category": "patrol_due", "severity": "medium",
                "title": "Scan your next checkpoint",
                "subtitle": f"{r['site_name'] or 'your site'} · patrol due", "action_route": "Patrol",
                "entity_id": str(r["id"]),
            })

    # 3. Incidents assigned to you and still open.
    inc = await db.execute(text("""
        SELECT id, title FROM incidents
        WHERE assigned_to_user_id = CAST(:uid AS uuid) AND status NOT IN ('resolved','closed')
        ORDER BY created_at DESC LIMIT 10
    """), p)
    for r in inc.mappings():
        items.append({
            "id": f"respond_incident:{r['id']}", "category": "respond_incident", "severity": "high",
            "title": f"Respond: {r['title']}", "subtitle": "Incident assigned to you",
            "action_route": "Incidents", "entity_id": str(r["id"]),
        })

    # 4. Your documents expiring soon / expired.
    docs = await db.execute(text("""
        SELECT id, document_type, expiry_date FROM employee_documents
        WHERE user_id = CAST(:uid AS uuid) AND expiry_date IS NOT NULL
          AND expiry_date <= CURRENT_DATE + INTERVAL '30 days'
        ORDER BY expiry_date
    """), p)
    for r in docs.mappings():
        expired = r["expiry_date"] is not None and str(r["expiry_date"]) < str(now.date())
        items.append({
            "id": f"doc_expiry:{r['id']}", "category": "doc_expiry",
            "severity": "high" if expired else "low",
            "title": f"{'Expired' if expired else 'Expiring'}: {r['document_type']}",
            "subtitle": f"{'Renew immediately' if expired else 'Renew soon'} · {r['expiry_date']}",
            "action_route": "MyRecord", "entity_id": str(r["id"]),
        })

    return items
