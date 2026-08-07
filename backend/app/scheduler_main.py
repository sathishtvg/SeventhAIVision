"""Daily maintenance (plan §16.1): pg_partman partition upkeep, per-tenant
evidence retention (file + row deletion), audit log archival (detach +
export, never drop). Runs once per loop iteration; sleeps RUN_INTERVAL_SECONDS
between runs rather than relying on an external scheduler (pg_cron, cron),
keeping the dependency footprint to "one more container," not "one more
Postgres extension to operate."
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.backup import run_database_backup
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.services.violations import VIOLATION_POINTS, create_violation

logger = logging.getLogger(__name__)

EVIDENCE_ROOT = Path(settings.EVIDENCE_ROOT)
ARCHIVE_ROOT = Path(os.environ.get("AUDIT_ARCHIVE_ROOT", "/data/archive"))
RUN_INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", str(24 * 60 * 60)))


async def run_partition_maintenance(db: AsyncSession | None = None) -> None:
    """Pre-creates future partitions + applies pg_partman's own (coarse,
    per-table, not per-tenant) retention as a safety net. Fine-grained
    per-tenant retention is handled separately below, since all tenants share
    the same physical monthly partitions — pg_partman has no concept of
    per-tenant retention.

    run_maintenance_proc() is a PROCEDURE that commits internally as it
    processes each registered table — calling it through a regular session
    (which wraps every execute() in an implicit transaction) raises
    "invalid transaction termination" (confirmed by actually running this,
    not assumed). Needs a connection in AUTOCOMMIT isolation instead; the
    `db` parameter is accepted-but-unused for interface symmetry with the
    other two maintenance functions below, which is the cheaper deviation
    here than rewriting their callers and tests around a special case.
    """
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        try:
            await conn.execute(text("CALL public.run_maintenance_proc()"))
        except Exception as exc:
            # In test/dev environments the app role may lack CREATE on public schema.
            # Log and continue — partman maintenance is best-effort in non-prod.
            logger.warning("run_maintenance_proc skipped: %s", exc)

        # Partitions do NOT inherit their parent's row-level security, and
        # pg_partman's template table does not carry it either (verified —
        # a template with RLS produced partitions with none). So every
        # partition partman just created above is currently unprotected:
        # readable across tenants if queried by name. Re-apply immediately
        # after creating them. Idempotent; returns how many it fixed, which
        # should be 0 on a healthy day. See migration 0083.
        try:
            fixed = await conn.scalar(text("SELECT public.apply_partition_rls()"))
            if fixed:
                logger.info("apply_partition_rls secured %s new partition(s)", fixed)
        except Exception as exc:
            logger.error("apply_partition_rls FAILED — new partitions may be "
                         "readable across tenants: %s", exc)


async def purge_expired_evidence(db: AsyncSession) -> int:
    """Each tenant's evidence.retention_days setting (or the env default)
    determines its own cutoff. File deleted before the row, so a crash
    mid-purge leaves an orphaned *row* pointing at a missing file (caught by
    a 404 if ever served) rather than an orphaned *file* nothing references
    (silently wastes disk forever)."""
    tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    total_deleted = 0
    for (tenant_id,) in tenants:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})
        setting_row = (
            await db.execute(
                text(
                    "SELECT setting_value FROM tenant_settings "
                    "WHERE tenant_id = :tid AND setting_key = 'evidence.retention_days'"
                ),
                {"tid": str(tenant_id)},
            )
        ).first()
        retention_days = setting_row[0] if setting_row else settings.EVIDENCE_RETENTION_DAYS

        expired = (
            await db.execute(
                text("SELECT id, storage_path FROM evidence WHERE captured_at < now() - (:days * INTERVAL '1 day')"),
                {"days": retention_days},
            )
        ).fetchall()
        for evidence_id, storage_path in expired:
            if settings.STORAGE_BACKEND == "s3":
                try:
                    from app.core.object_store import delete_object as _s3_del

                    await asyncio.to_thread(_s3_del, storage_path)
                except Exception:
                    logger.warning("S3 delete failed for %s", storage_path)
            else:
                file_path = EVIDENCE_ROOT / storage_path
                if file_path.exists():
                    file_path.unlink()
            await db.execute(text("DELETE FROM evidence WHERE id = :id"), {"id": evidence_id})
            total_deleted += 1
        await db.commit()
    return total_deleted


async def archive_old_audit_partitions(db: AsyncSession) -> list[str]:
    """Audit partitions are DETACHED + exported to JSON Lines, never DROPPED:
    "immutable audit trails" is the original scope's compliance requirement,
    not a Phase 1 nice-to-have. AUDIT_RETENTION_YEARS is a single global
    cutoff — Phase 1's tenant_settings key set has no per-tenant audit
    retention key."""
    now = datetime.now(timezone.utc)
    cutoff = now.replace(year=now.year - settings.AUDIT_RETENTION_YEARS)
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

    partition_rows = (
        await db.execute(text("SELECT partition_tablename FROM public.show_partitions('public.audit_logs')"))
    ).fetchall()

    archived: list[str] = []
    for (partition_name,) in partition_rows:
        if partition_name.endswith("_default"):
            continue  # never archive the catch-all partition

        # Parse the period from the table's actual min/max timestamps rather
        # than trusting pg_partman's pYYYYMMDD naming convention, in case
        # that format ever changes.
        bounds = (await db.execute(text(f"SELECT min(created_at), max(created_at) FROM {partition_name}"))).first()
        if bounds is None or bounds[1] is None or bounds[1] >= cutoff:
            continue  # not entirely past the retention cutoff yet, or empty

        export_path = ARCHIVE_ROOT / f"{partition_name}.jsonl"
        rows = (await db.execute(text(f"SELECT * FROM {partition_name} ORDER BY created_at"))).mappings().all()
        with export_path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(dict(row), default=str) + "\n")

        await db.execute(text(f"ALTER TABLE public.audit_logs DETACH PARTITION {partition_name}"))
        await db.commit()
        archived.append(partition_name)
        logger.info("archived audit partition %s -> %s (%d rows)", partition_name, export_path, len(rows))

    return archived


CAMERA_OFFLINE_THRESHOLD_SECONDS = int(os.environ.get("CAMERA_OFFLINE_THRESHOLD_SECONDS", "300"))
CAMERA_OFFLINE_ALERT_COOLDOWN_SECONDS = int(os.environ.get("CAMERA_OFFLINE_ALERT_COOLDOWN_SECONDS", "3600"))

# Escalation: how many minutes an unacknowledged alert can sit before severity bumps
_ESCALATION_DEFAULTS = {"critical": 5, "high": 15, "medium": 60, "low": 0}  # 0 = never escalate
_SEVERITY_UP = {"low": "medium", "medium": "high", "high": "critical"}


async def escalate_unacknowledged_alerts(db: AsyncSession, redis: Redis) -> int:
    """Escalate unacknowledged alerts that have been open past the escalation window.

    Reads per-tenant settings: alert.escalation_minutes_critical/high/medium.
    Falls back to _ESCALATION_DEFAULTS. Severity bumps one tier; critical stays
    critical. Records original_severity and escalated_at; publishes realtime event.
    """
    tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    total = 0
    for (tenant_id,) in tenants:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

        # Fetch per-tenant escalation thresholds
        thresholds = {}
        for sev in ("critical", "high", "medium"):
            key = f"alert.escalation_minutes_{sev}"
            row = (await db.execute(
                text("SELECT setting_value FROM tenant_settings WHERE tenant_id = :tid AND setting_key = :k"),
                {"tid": str(tenant_id), "k": key},
            )).first()
            thresholds[sev] = int(row[0]) if row else _ESCALATION_DEFAULTS[sev]

        # Find open, unescalated alerts that have exceeded the window
        stale_rows = (await db.execute(
            text("""
                SELECT id, severity, created_at
                FROM alerts
                WHERE status = 'open'
                  AND escalated_at IS NULL
                  AND severity IN ('low', 'medium', 'high')
            """)
        )).fetchall()

        for alert_id, severity, created_at in stale_rows:
            window = thresholds.get(severity, 0)
            if window <= 0:
                continue
            age_minutes = (datetime.now(timezone.utc) - created_at.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age_minutes < window:
                continue
            new_severity = _SEVERITY_UP.get(severity, severity)
            await db.execute(
                text(
                    "UPDATE alerts SET severity = :new_sev, escalated_at = now(), "
                    "original_severity = :orig WHERE id = :id"
                ),
                {"new_sev": new_severity, "orig": severity, "id": alert_id},
            )
            event_payload = json.dumps({
                "event_type": "alert_escalated",
                "tenant_id": str(tenant_id),
                "payload": {"alert_id": str(alert_id), "from_severity": severity, "to_severity": new_severity},
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            })
            await redis.publish(f"tenant_events:{tenant_id}", event_payload)
            total += 1

        await db.commit()
    return total


async def check_visitor_overstays(db: AsyncSession, redis: Redis) -> int:
    """Alert when a visitor's expected_until has passed and no departure has been logged."""
    tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    total = 0
    for (tenant_id,) in tenants:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})
        overstays = (await db.execute(
            text("""
                SELECT v.id, v.full_name, v.site_id, s.name AS site_name
                FROM visitors v
                LEFT JOIN sites s ON s.id = v.site_id
                WHERE v.is_active = TRUE
                  AND v.expected_until IS NOT NULL
                  AND v.expected_until < now()
                  AND NOT EXISTS (
                    SELECT 1 FROM visitor_logs vl
                    WHERE vl.visitor_id = v.id AND vl.event_type = 'departure'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM alerts a
                    WHERE a.alert_code = 'visitor.overstay'
                      AND (a.message_params->>'visitor_id')::text = v.id::text
                      AND a.created_at > now() - INTERVAL '4 hours'
                  )
            """)
        )).fetchall()

        for visitor_id, visitor_name, site_id, site_name in overstays:
            # Resolve a camera for the alert.  `= :sid` with sid=NULL evaluates to
            # `= NULL` (never true in SQL), so visitors without a site would never
            # trigger an alert under the old single-query pattern.  COALESCE falls
            # back to any tenant camera when no site-specific one is found.
            cam_id = (await db.execute(
                text("""
                    SELECT COALESCE(
                        (SELECT id FROM cameras WHERE tenant_id = :tid AND site_id = :sid LIMIT 1),
                        (SELECT id FROM cameras WHERE tenant_id = :tid LIMIT 1)
                    )
                """),
                {"tid": tenant_id, "sid": site_id},
            )).scalar()

            if cam_id is None:
                continue  # tenant has no cameras at all — skip silently

            alert_row = (await db.execute(
                text(
                    "INSERT INTO alerts (tenant_id, camera_id, module_type, severity, alert_code, "
                    "                   message_params, title, message, status) "
                    "VALUES (:tid, :cid, 'visitor_overstay', 'medium', 'visitor.overstay', "
                    "       CAST(:alert_params AS jsonb), :title, :msg, 'open') "
                    "RETURNING id"
                ),
                {
                    "tid": tenant_id,
                    "cid": cam_id,
                    "alert_params": json.dumps({"visitor_id": str(visitor_id), "visitor_name": visitor_name}),
                    "title": f"Visitor overstay: {visitor_name}",
                    "msg": f"{visitor_name} has not departed and their expected exit time has passed"
                          + (f" at {site_name}" if site_name else ""),
                },
            )).scalar()
            event_payload = json.dumps({
                "event_type": "alert_created",
                "tenant_id": str(tenant_id),
                "payload": {
                    "alert_id": str(alert_row),
                    "alert_code": "visitor.overstay",
                    "visitor_name": visitor_name,
                    "module_type": "visitor_overstay",
                    "severity": "medium",
                },
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            })
            await redis.publish(f"tenant_events:{tenant_id}", event_payload)
            total += 1

        await db.commit()
    return total


async def check_parking_overstays(db: AsyncSession, redis: Redis) -> int:
    """Alert when a visitor's vehicle has exceeded the site's free-parking
    allowance — the trigger for wheel-clamping or further action.

    Distinct from check_visitor_overstays above: that one is about the PERSON
    staying past their expected departure, this is about the VEHICLE occupying
    a bay past its free allowance. A visitor can legitimately still be on site
    while their car has overstayed, and vice versa.

    Allowance resolves per-visit override first, then the site default. NULL at
    both levels means this site does not meter parking and is skipped entirely
    — an unset allowance must never be read as "zero minutes free".
    """
    tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    total = 0
    for (tenant_id,) in tenants:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)}
        )
        overstays = (await db.execute(
            text("""
                SELECT v.id, v.full_name, v.vehicle_plate, v.site_id, s.name AS site_name,
                       FLOOR(EXTRACT(EPOCH FROM (now() - v.vehicle_entry_at))/60)::int AS mins,
                       COALESCE(v.free_parking_minutes, s.free_parking_minutes) AS allowance
                FROM visitors v
                JOIN sites s ON s.id = v.site_id
                WHERE v.is_active = TRUE
                  AND v.vehicle_entry_at IS NOT NULL
                  AND v.vehicle_exit_at IS NULL
                  AND COALESCE(v.free_parking_minutes, s.free_parking_minutes) IS NOT NULL
                  AND EXTRACT(EPOCH FROM (now() - v.vehicle_entry_at))/60
                      > COALESCE(v.free_parking_minutes, s.free_parking_minutes)
                  AND NOT EXISTS (
                    SELECT 1 FROM alerts a
                    WHERE a.alert_code = 'parking.overstay'
                      AND (a.message_params->>'visitor_id')::text = v.id::text
                      AND a.created_at > now() - INTERVAL '4 hours'
                  )
            """)
        )).fetchall()

        for visitor_id, name, plate, site_id, site_name, mins, allowance in overstays:
            cam_id = (await db.execute(
                text("""
                    SELECT COALESCE(
                        (SELECT id FROM cameras WHERE tenant_id = :tid AND site_id = :sid LIMIT 1),
                        (SELECT id FROM cameras WHERE tenant_id = :tid LIMIT 1)
                    )
                """),
                {"tid": tenant_id, "sid": site_id},
            )).scalar()
            if cam_id is None:
                continue

            over_by = mins - allowance
            alert_id = (await db.execute(
                text(
                    "INSERT INTO alerts (tenant_id, camera_id, module_type, severity, alert_code, "
                    "                   message_params, title, message, status) "
                    "VALUES (:tid, :cid, 'parking_overstay', 'medium', 'parking.overstay', "
                    "       CAST(:params AS jsonb), :title, :msg, 'open') "
                    "RETURNING id"
                ),
                {
                    "tid": tenant_id,
                    "cid": cam_id,
                    "params": json.dumps({
                        "visitor_id": str(visitor_id), "visitor_name": name,
                        "plate_number": plate, "minutes_on_site": mins,
                        "allowance_minutes": allowance, "over_by_minutes": over_by,
                    }),
                    "title": f"Parking overstay: {plate or name}",
                    "msg": (
                        f"{plate or name} has been parked {mins} min, exceeding the "
                        f"{allowance} min free allowance by {over_by} min"
                        + (f" at {site_name}" if site_name else "")
                        + " — review for wheel clamping or further action"
                    ),
                },
            )).scalar()
            await redis.publish(f"tenant_events:{tenant_id}", json.dumps({
                "event_type": "alert_created",
                "tenant_id": str(tenant_id),
                "payload": {
                    "alert_id": str(alert_id),
                    "alert_code": "parking.overstay",
                    "plate_number": plate,
                    "module_type": "parking_overstay",
                    "severity": "medium",
                },
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }))
            total += 1

        await db.commit()
    return total


async def check_no_show_shifts(db: AsyncSession, redis: Redis) -> int:
    """Auto-flags a no_show violation (ShiftSecure Phase 3) for any shift
    still 'scheduled' well past its start time. Mirrors
    check_visitor_overstays's tenant-iteration/dedup/publish/commit shape.
    Dedup against a repeat run is handled by create_violation itself
    (checks for any existing violation of the same type+shift_id)."""
    tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    total = 0
    for (tenant_id,) in tenants:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

        setting_row = (await db.execute(
            text("SELECT setting_value FROM tenant_settings WHERE setting_key = 'attendance.late_grace_minutes'"),
        )).first()
        grace_minutes = setting_row[0] if setting_row is not None and isinstance(setting_row[0], int) else 10

        no_shows = (await db.execute(
            text("""
                SELECT sh.id, sh.guard_user_id, sh.site_id
                FROM shifts sh
                WHERE sh.status = 'scheduled'
                  AND sh.guard_user_id IS NOT NULL
                  AND sh.scheduled_start < now() - make_interval(mins => :grace)
                  AND NOT EXISTS (
                    SELECT 1 FROM violations v
                    WHERE v.shift_id = sh.id AND v.violation_type = 'no_show'
                  )
            """),
            {"grace": grace_minutes},
        )).fetchall()

        for shift_id, guard_user_id, site_id in no_shows:
            violation_id = await create_violation(
                db, str(tenant_id), str(guard_user_id), "no_show",
                shift_id=str(shift_id), site_id=str(site_id) if site_id else None,
                points=VIOLATION_POINTS["no_show"], is_auto_generated=True,
            )
            if violation_id is None:
                continue
            event_payload = json.dumps({
                "event_type": "violation_created",
                "tenant_id": str(tenant_id),
                "payload": {
                    "violation_id": violation_id,
                    "violation_type": "no_show",
                    "guard_user_id": str(guard_user_id),
                },
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            })
            await redis.publish(f"tenant_events:{tenant_id}", event_payload)
            total += 1

        await db.commit()
    return total


async def check_camera_offline_alerts(db: AsyncSession, redis: Redis) -> int:
    """Finds cameras whose last_frame_at is stale, marks them offline, and fires
    a tenant_events pub/sub notification once per cooldown window using the
    last_offline_alert_at column added in migration 0007.

    BUG FIX: this previously ran one cross-tenant SELECT with no
    set_config('app.current_tenant') at all. cameras and streams are both
    RLS-protected, and their policy casts current_setting('app.current_tenant',
    true) to uuid. On a connection that had never set the GUC that yields NULL
    and the query silently returns zero rows; on a POOLED connection recycled
    from a job that set the GUC and then committed, it yields the empty string
    and '' ::uuid raises InvalidTextRepresentationError. Observed live: the job
    was failing outright, so camera-offline alerts were not firing.

    Now iterates tenants and scopes per tenant, the same shape as every other
    job in this module. Third instance of this bug class in the codebase after
    streams.py and webhooks.py.
    """
    tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    rows: list = []
    for (tenant_id,) in tenants:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)}
        )
        rows.extend(
            (
                await db.execute(
                    text(
                        """
                SELECT c.id, c.tenant_id, c.name, c.last_offline_alert_at
                FROM cameras c
                JOIN streams s ON s.camera_id = c.id
                WHERE c.is_active = TRUE
                  AND s.status != 'offline'
                  AND (
                    s.last_frame_at IS NULL
                    OR s.last_frame_at < now() - (:threshold * INTERVAL '1 second')
                  )
                  AND (
                    c.last_offline_alert_at IS NULL
                    OR c.last_offline_alert_at < now() - (:cooldown * INTERVAL '1 second')
                  )
                """
                    ),
                    {
                        "threshold": CAMERA_OFFLINE_THRESHOLD_SECONDS,
                        "cooldown": CAMERA_OFFLINE_ALERT_COOLDOWN_SECONDS,
                    },
                )
            ).fetchall()
        )

    fired = 0
    for row in rows:
        camera_id, tenant_id, camera_name, _ = row
        # Re-scope before the UPDATE: rows were gathered across several tenants,
        # so the GUC still holds whichever tenant was scanned last and the RLS
        # WITH CHECK would reject an update to any other tenant's camera.
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)}
        )
        await db.execute(
            text("UPDATE cameras SET last_offline_alert_at = now() WHERE id = :id"),
            {"id": camera_id},
        )
        event_payload = json.dumps(
            {
                "event_type": "camera_status_changed",
                "tenant_id": str(tenant_id),
                "payload": {
                    "camera_id": str(camera_id),
                    "camera_name": camera_name,
                    "new_status": "offline",
                    "reason": "no_frames",
                },
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        await redis.publish(f"tenant_events:{tenant_id}", event_payload)
        fired += 1
        logger.info("camera offline alert fired for camera %s (tenant %s)", camera_id, tenant_id)

    await db.commit()
    return fired


SCHEDULED_REPORT_INTERVAL = int(os.environ.get("SCHEDULED_REPORT_INTERVAL_SECONDS", "300"))   # 5 min


async def run_scheduled_reports(db: AsyncSession) -> int:
    """Check for due report schedules (next_run_at <= now()) and deliver PDFs.

    Each schedule specifies a report_type, delivery_method, and recipients/webhook_url.
    After delivery (or failure), last_run_at and next_run_at are updated and a
    report_deliveries row is written.
    """
    try:
        from app.routers.reports import build_site_summary_bytes, build_dob_bytes
    except ImportError:
        logger.warning("reports module not available; skipping scheduled reports")
        return 0

    from app.routers.scheduled_reports import _compute_next_run

    tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    total = 0

    for (tenant_id,) in tenants:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

        due = (await db.execute(text("""
            SELECT id, name, report_type, frequency, day_of_week, day_of_month,
                   hour_utc, site_id, delivery_method, recipients, webhook_url
            FROM report_schedules
            WHERE is_active = TRUE AND next_run_at <= now()
        """))).fetchall()

        for row in due:
            sid, name, report_type, frequency, dow, dom, hour_utc, site_id, method, recipients, webhook_url = row
            # Re-set GUC: commit() at the end of each iteration clears the transaction-local
            # setting, so the RLS policy on the next iteration would see an empty string.
            await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})
            delivery_status = "failed"
            error_msg = None

            # Compute reporting period based on frequency
            now_utc = datetime.now(timezone.utc)
            if frequency == "daily":
                period_end = now_utc
                period_start = now_utc - timedelta(days=1)
            elif frequency == "weekly":
                period_end = now_utc
                period_start = now_utc - timedelta(weeks=1)
            else:  # monthly
                period_end = now_utc
                period_start = now_utc - timedelta(days=30)
            df_str = period_start.isoformat()
            du_str = period_end.isoformat()

            try:
                # Generate PDF bytes
                if report_type == "dob":
                    pdf_bytes = await build_dob_bytes(db, str(site_id) if site_id else None, df_str, du_str)
                else:
                    # site_summary and incident_summary both use the same base PDF
                    pdf_bytes = await build_site_summary_bytes(db, str(site_id) if site_id else None, df_str, du_str)

                # Deliver
                if method == "email" and recipients:
                    recipient_list = recipients if isinstance(recipients, list) else json.loads(recipients)
                    await _send_report_email(name, pdf_bytes, recipient_list)
                elif method == "webhook" and webhook_url:
                    await _send_report_webhook(webhook_url, name, pdf_bytes, str(tenant_id), str(sid))

                delivery_status = "success"
            except Exception as e:
                error_msg = str(e)[:500]
                logger.exception("scheduled report %s failed for tenant %s", sid, tenant_id)

            next_run = _compute_next_run(frequency, dow, dom, hour_utc)

            await db.execute(text("""
                UPDATE report_schedules
                SET last_run_at = now(), next_run_at = :next_run
                WHERE id = CAST(:sid AS uuid)
            """), {"sid": str(sid), "next_run": next_run})

            await db.execute(text("""
                INSERT INTO report_deliveries
                    (tenant_id, schedule_id, status, delivered_at, error_message,
                     report_period_start, report_period_end)
                VALUES (
                    current_setting('app.current_tenant')::uuid,
                    CAST(:sid AS uuid), :status,
                    :delivered_at,
                    :error_msg, :period_start, :period_end
                )
            """), {
                "sid": str(sid),
                "status": delivery_status,
                "delivered_at": datetime.now(timezone.utc) if delivery_status == "success" else None,
                "error_msg": error_msg,
                "period_start": period_start,
                "period_end": period_end,
            })
            await db.commit()
            total += 1

    return total


async def _send_report_email(schedule_name: str, pdf_bytes: bytes, recipients: list[str]) -> None:
    """Send PDF report via SMTP using aiosmtplib if available."""
    try:
        import aiosmtplib
        from email.mime.application import MIMEApplication
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
    except ImportError:
        logger.warning("aiosmtplib not installed; skipping email delivery")
        return

    msg = MIMEMultipart()
    msg["Subject"] = f"Scheduled Report: {schedule_name}"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(f"Please find attached the scheduled report: {schedule_name}.", "plain"))
    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=f"{schedule_name}.pdf")
    msg.attach(attachment)

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASSWORD or None,
        start_tls=settings.SMTP_PORT == 587,
    )


async def _send_report_webhook(url: str, schedule_name: str, pdf_bytes: bytes,
                               tenant_id: str, schedule_id: str) -> None:
    """POST the PDF as multipart/form-data to the webhook URL."""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; skipping webhook delivery")
        return
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, files={"report": (f"{schedule_name}.pdf", pdf_bytes, "application/pdf")},
                                 data={"schedule_id": schedule_id, "tenant_id": tenant_id})
        resp.raise_for_status()


ALERT_ESCALATION_INTERVAL = int(os.environ.get("ALERT_ESCALATION_INTERVAL_SECONDS", "300"))   # 5 min
VISITOR_OVERSTAY_INTERVAL = int(os.environ.get("VISITOR_OVERSTAY_INTERVAL_SECONDS", "900"))    # 15 min
CAMERA_OFFLINE_INTERVAL  = int(os.environ.get("CAMERA_OFFLINE_INTERVAL_SECONDS", "300"))       # 5 min
COMPLIANCE_INTERVAL      = int(os.environ.get("COMPLIANCE_INTERVAL_SECONDS", "900"))           # 15 min
CONTRACTOR_EXPIRY_INTERVAL = int(os.environ.get("CONTRACTOR_EXPIRY_INTERVAL_SECONDS", "3600"))  # 1 hr
NO_SHOW_CHECK_INTERVAL   = int(os.environ.get("NO_SHOW_CHECK_INTERVAL_SECONDS", "900"))         # 15 min


async def check_contractor_expiry(
    db: AsyncSession,
    redis: Redis,
    tenant_ids: list | None = None,
) -> dict:
    """Hourly contractor / permit expiry maintenance per active tenant.

    tenant_ids: optional list of UUIDs to restrict processing (used in tests to
    prevent parallel test suites from interfering via the shared test database).
    """
    if tenant_ids is not None:
        tenants = (await db.execute(
            text("SELECT id FROM tenants WHERE is_active = TRUE AND id = ANY(:ids)"),
            {"ids": [str(t) for t in tenant_ids]},
        )).fetchall()
    else:
        tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    expired_permits = 0
    permit_alerts = 0
    accred_alerts = 0
    now_utc = datetime.now(timezone.utc)

    for (tenant_id,) in tenants:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )

        # ── 1. Auto-expire past-end work permits ──────────────────────────────
        expired_rows = (await db.execute(text("""
            UPDATE work_permits
            SET status = 'expired', updated_at = now()
            WHERE status IN ('approved', 'active')
              AND end_at < now()
            RETURNING id, contractor_id, site_id, permit_number
        """))).fetchall()
        expired_permits += len(expired_rows)

        # ── 2. Alert for permits ending within 4 hours ────────────────────────
        ending_soon = (await db.execute(text("""
            SELECT wp.id, wp.contractor_id, wp.site_id, wp.permit_number,
                   c.company_name
            FROM work_permits wp
            JOIN contractors c ON c.id = wp.contractor_id
            WHERE wp.status IN ('approved', 'active')
              AND wp.end_at BETWEEN now() AND now() + INTERVAL '4 hours'
              AND NOT EXISTS (
                  SELECT 1 FROM alerts a
                  WHERE a.alert_code = 'contractor.permit_expiring'
                    AND (a.message_params->>'permit_id') = wp.id::text
                    AND a.created_at > now() - INTERVAL '6 hours'
              )
        """))).fetchall()

        for permit_id, contractor_id, site_id, permit_number, company_name in ending_soon:
            pnum = permit_number or "N/A"
            inserted = (await db.execute(text("""
                INSERT INTO alerts (
                    tenant_id, camera_id, module_type, severity,
                    alert_code, message_params, title, message, status
                )
                SELECT CAST(:tid AS uuid),
                       COALESCE(
                           (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid)
                            AND site_id = CAST(:sid AS uuid) LIMIT 1),
                           (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1)
                       ),
                       'contractor', 'medium',
                       'contractor.permit_expiring',
                       CAST(:params AS jsonb),
                       :title, :msg, 'open'
                WHERE COALESCE(
                    (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid)
                     AND site_id = CAST(:sid AS uuid) LIMIT 1),
                    (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1)
                ) IS NOT NULL
                RETURNING id
            """), {
                "tid": str(tenant_id),
                "sid": str(site_id) if site_id else "00000000-0000-0000-0000-000000000000",
                "params": json.dumps({"permit_id": str(permit_id), "company": company_name,
                                      "permit_number": pnum}),
                "title": f"Work permit expiring: {company_name}",
                "msg": f"Permit #{pnum} for {company_name} expires within 4 hours.",
            })).first()
            if inserted:
                permit_alerts += 1
                await redis.publish(f"tenant_events:{tenant_id}", json.dumps({
                    "event_type": "alert_created",
                    "tenant_id": str(tenant_id),
                    "payload": {
                        "alert_id": str(inserted[0]),
                        "alert_code": "contractor.permit_expiring",
                        "company": company_name,
                        "module_type": "contractor",
                        "severity": "medium",
                    },
                    "occurred_at": now_utc.isoformat(),
                }))

        # ── 3. Alert for accreditations expiring within 30 days ───────────────
        expiring_accreds = (await db.execute(text("""
            SELECT ca.id, ca.contractor_id, ca.document_type,
                   ca.expires_at, c.company_name
            FROM contractor_accreditations ca
            JOIN contractors c ON c.id = ca.contractor_id
            WHERE ca.expires_at IS NOT NULL
              AND ca.expires_at BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days'
              AND NOT EXISTS (
                  SELECT 1 FROM alerts a
                  WHERE a.alert_code = 'contractor.accreditation_expiring'
                    AND (a.message_params->>'accreditation_id') = ca.id::text
                    AND a.created_at > now() - INTERVAL '7 days'
              )
        """))).fetchall()

        for acred_id, contractor_id, acred_type, expires_at, company_name in expiring_accreds:
            days_left = (expires_at - now_utc.date()).days
            inserted = (await db.execute(text("""
                INSERT INTO alerts (
                    tenant_id, camera_id, module_type, severity,
                    alert_code, message_params, title, message, status
                )
                SELECT CAST(:tid AS uuid),
                       (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1),
                       'contractor', 'low',
                       'contractor.accreditation_expiring',
                       CAST(:params AS jsonb),
                       :title, :msg, 'open'
                WHERE (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1) IS NOT NULL
                RETURNING id
            """), {
                "tid": str(tenant_id),
                "params": json.dumps({"accreditation_id": str(acred_id),
                                      "company": company_name, "type": acred_type,
                                      "days_left": days_left}),
                "title": f"Accreditation expiring: {company_name}",
                "msg": f"{acred_type} accreditation for {company_name} expires in {days_left} days.",
            })).first()
            if inserted:
                accred_alerts += 1
                await redis.publish(f"tenant_events:{tenant_id}", json.dumps({
                    "event_type": "alert_created",
                    "tenant_id": str(tenant_id),
                    "payload": {
                        "alert_id": str(inserted[0]),
                        "alert_code": "contractor.accreditation_expiring",
                        "company": company_name,
                        "module_type": "contractor",
                        "severity": "low",
                    },
                    "occurred_at": now_utc.isoformat(),
                }))

        await db.commit()

    return {
        "expired_permits": expired_permits,
        "permit_alerts": permit_alerts,
        "accreditation_alerts": accred_alerts,
    }


async def run_compliance_maintenance(db: AsyncSession, redis: Redis) -> dict:
    """Two sub-tasks run per active tenant every 15 minutes:

    1. Auto-expire: pending tour occurrences whose window_end has passed with no
       linked session are marked 'missed'.

    2. Auto-link: recently completed patrol sessions (within 48h) are matched to
       the closest pending occurrence on the same route (±2h window), the
       compliance_score is calculated from scanned/total checkpoints, and the
       occurrence status is set to completed / incomplete / late.
    """
    tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))).fetchall()
    expired_total = 0
    linked_total = 0
    now_utc = datetime.now(timezone.utc)

    for (tenant_id,) in tenants:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )

        # ── 1. Auto-expire pending occurrences past their window_end ───────────
        expired_rows = (await db.execute(text("""
            UPDATE tour_occurrences
            SET status = 'missed', updated_at = now()
            WHERE status = 'pending'
              AND session_id IS NULL
              AND window_end < now()
            RETURNING id, schedule_id, scheduled_at
        """))).fetchall()

        for occ_id, schedule_id, scheduled_at in expired_rows:
            expired_total += 1
            await redis.publish(f"tenant_events:{tenant_id}", json.dumps({
                "event_type": "tour_occurrence_missed",
                "tenant_id": str(tenant_id),
                "payload": {
                    "occurrence_id": str(occ_id),
                    "schedule_id": str(schedule_id),
                    "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                },
                "occurred_at": now_utc.isoformat(),
            }))

        # ── 2. Auto-link recently completed patrol sessions ─────────────────────
        # Find sessions finished in the last 48 h that aren't linked to any occurrence yet.
        sessions = (await db.execute(text("""
            SELECT ps.id, ps.route_id, ps.started_at, ps.completed_at,
                   COALESCE(ps.scanned_checkpoints, 0) AS scanned,
                   COALESCE(ps.total_checkpoints, 0)   AS total
            FROM patrol_sessions ps
            WHERE ps.status = 'completed'
              AND ps.completed_at IS NOT NULL
              AND ps.started_at >= now() - INTERVAL '48 hours'
              AND NOT EXISTS (
                  SELECT 1 FROM tour_occurrences o WHERE o.session_id = ps.id
              )
        """))).fetchall()

        for sess_id, route_id, started_at, completed_at, scanned, total in sessions:
            # Find the pending occurrence on the same route closest to session start
            # (within a ±2-hour tolerance so a slightly early/late start still matches).
            occ_row = (await db.execute(text("""
                SELECT o.id, o.scheduled_at, o.window_end
                FROM tour_occurrences o
                JOIN tour_schedules ts ON ts.id = o.schedule_id
                WHERE ts.route_id = CAST(:route_id AS uuid)
                  AND o.status = 'pending'
                  AND o.session_id IS NULL
                  AND ABS(EXTRACT(EPOCH FROM (o.scheduled_at - :started_at))) < 7200
                ORDER BY ABS(EXTRACT(EPOCH FROM (o.scheduled_at - :started_at)))
                LIMIT 1
            """), {"route_id": str(route_id), "started_at": started_at})).first()

            if not occ_row:
                continue  # no matching scheduled occurrence — unscheduled patrol, skip

            occ_id, scheduled_at_occ, window_end = occ_row

            # Compliance score from checkpoint scan ratio
            score = round(scanned / total * 100, 1) if total > 0 else 100.0
            missed_cps = max(0, total - scanned) if total > 0 else 0

            # Status: late if session started after window closed; else by score
            if started_at and window_end and started_at > window_end:
                occ_status = "late"
            elif score >= 100:
                occ_status = "completed"
            else:
                occ_status = "incomplete"

            await db.execute(text("""
                UPDATE tour_occurrences
                SET session_id        = CAST(:session_id AS uuid),
                    status            = :status,
                    compliance_score  = :score,
                    missed_checkpoints = :missed,
                    updated_at        = now()
                WHERE id = CAST(:occ_id AS uuid)
            """), {
                "session_id": str(sess_id),
                "status": occ_status,
                "score": score,
                "missed": missed_cps,
                "occ_id": str(occ_id),
            })
            linked_total += 1

            await redis.publish(f"tenant_events:{tenant_id}", json.dumps({
                "event_type": "tour_occurrence_updated",
                "tenant_id": str(tenant_id),
                "payload": {
                    "occurrence_id": str(occ_id),
                    "status": occ_status,
                    "compliance_score": score,
                    "session_id": str(sess_id),
                },
                "occurred_at": now_utc.isoformat(),
            }))

        await db.commit()

    return {"expired": expired_total, "linked": linked_total}


async def run_once(redis: Redis | None = None) -> None:
    """Full daily maintenance cycle (partitions, evidence purge, audit archive, backup)."""
    await run_partition_maintenance()

    async with AsyncSessionLocal() as db:
        deleted = await purge_expired_evidence(db)
        logger.info("purge_expired_evidence: deleted %d expired evidence rows/files", deleted)

    async with AsyncSessionLocal() as db:
        archived = await archive_old_audit_partitions(db)
        if archived:
            logger.info("archive_old_audit_partitions: archived %d partitions", len(archived))

    try:
        result = await run_database_backup()
        logger.info("run_database_backup: %s (%d bytes)", result["filename"], result["size_bytes"])
    except Exception:
        logger.exception("run_database_backup failed (non-fatal, maintenance continues)")

    # Roster auto-generation (Gap 86): expand recurring shift patterns into
    # concrete shifts for the next 7 days across all tenants.
    try:
        from app.services.roster import generate_roster_for_all_tenants
        created = await generate_roster_for_all_tenants(AsyncSessionLocal)
        if created:
            logger.info("roster generation: created %d shifts", created)
    except Exception:
        logger.exception("roster generation failed (non-fatal, maintenance continues)")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)

    last_daily = 0.0
    last_escalation = 0.0
    last_overstay = 0.0
    last_camera = 0.0
    last_report = 0.0
    last_compliance = 0.0
    last_contractor = 0.0
    last_no_show = 0.0

    try:
        while True:
            now = asyncio.get_event_loop().time()

            # Daily jobs
            if now - last_daily >= RUN_INTERVAL_SECONDS:
                try:
                    await run_once(redis)
                except Exception:
                    logger.exception("daily run_once failed")
                last_daily = now

            # Camera offline check (every 5 min)
            if now - last_camera >= CAMERA_OFFLINE_INTERVAL:
                try:
                    async with AsyncSessionLocal() as db:
                        fired = await check_camera_offline_alerts(db, redis)
                        if fired:
                            logger.info("check_camera_offline_alerts: fired %d alerts", fired)
                except Exception:
                    logger.exception("check_camera_offline_alerts failed")
                last_camera = now

            # Alert auto-escalation (every 5 min)
            if now - last_escalation >= ALERT_ESCALATION_INTERVAL:
                try:
                    async with AsyncSessionLocal() as db:
                        n = await escalate_unacknowledged_alerts(db, redis)
                        if n:
                            logger.info("escalate_unacknowledged_alerts: escalated %d alerts", n)
                except Exception:
                    logger.exception("escalate_unacknowledged_alerts failed")
                last_escalation = now

            # Scheduled report delivery (every 5 min)
            if now - last_report >= SCHEDULED_REPORT_INTERVAL:
                try:
                    async with AsyncSessionLocal() as db:
                        n = await run_scheduled_reports(db)
                        if n:
                            logger.info("run_scheduled_reports: processed %d schedules", n)
                except Exception:
                    logger.exception("run_scheduled_reports failed")
                last_report = now

            # Visitor overstay (every 15 min)
            if now - last_overstay >= VISITOR_OVERSTAY_INTERVAL:
                try:
                    async with AsyncSessionLocal() as db:
                        n = await check_visitor_overstays(db, redis)
                        if n:
                            logger.info("check_visitor_overstays: created %d alerts", n)
                except Exception:
                    logger.exception("check_visitor_overstays failed")
                # Parking overstay shares the same 15-minute cadence and its own
                # session, so a failure in one sweep never suppresses the other.
                try:
                    async with AsyncSessionLocal() as db:
                        n = await check_parking_overstays(db, redis)
                        if n:
                            logger.info("check_parking_overstays: created %d alerts", n)
                except Exception:
                    logger.exception("check_parking_overstays failed")
                last_overstay = now

            # Tour compliance: auto-expire missed + auto-link completed sessions (every 15 min)
            if now - last_compliance >= COMPLIANCE_INTERVAL:
                try:
                    async with AsyncSessionLocal() as db:
                        result = await run_compliance_maintenance(db, redis)
                        if result["expired"] or result["linked"]:
                            logger.info(
                                "run_compliance_maintenance: expired=%d missed, linked=%d sessions",
                                result["expired"], result["linked"],
                            )
                except Exception:
                    logger.exception("run_compliance_maintenance failed")
                last_compliance = now

            # Contractor permit expiry + accreditation alerts (every hour)
            if now - last_contractor >= CONTRACTOR_EXPIRY_INTERVAL:
                try:
                    async with AsyncSessionLocal() as db:
                        result = await check_contractor_expiry(db, redis)
                        if any(result.values()):
                            logger.info(
                                "check_contractor_expiry: expired=%d permits, alerts: permits=%d accreds=%d",
                                result["expired_permits"], result["permit_alerts"],
                                result["accreditation_alerts"],
                            )
                except Exception:
                    logger.exception("check_contractor_expiry failed")
                last_contractor = now

            # No-show violation detection (every 15 min)
            if now - last_no_show >= NO_SHOW_CHECK_INTERVAL:
                try:
                    async with AsyncSessionLocal() as db:
                        n = await check_no_show_shifts(db, redis)
                        if n:
                            logger.info("check_no_show_shifts: created %d violations", n)
                except Exception:
                    logger.exception("check_no_show_shifts failed")
                last_no_show = now

            await asyncio.sleep(60)  # check every minute which jobs are due
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
