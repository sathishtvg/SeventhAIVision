"""Move subscriptions through their life, and say so before it happens.

Runs nightly. For every customer it asks the same four questions — is a trial
about to end, is a renewal about to fall due, has a period ended, has grace run
out — and raises a notice when the answer is yes.

NOTICE BEFORE ACTION, always. Every state change here is preceded by a warning
raised days earlier, because the failure mode of billing automation is not that
it charges too little: it is that a security company arrives one morning to
find their cameras off and nobody told them.

SUSPENSION IS OFF BY DEFAULT and stays a decision somebody makes. The policy
lives in platform_settings so the vendor can turn it on with their name against
it, and even then it only fires after the period ended AND grace ran out —
never on a single missed payment.

GRACE IS THE POINT. A period ending is not a customer leaving. Cards expire,
finance departments are slow, and a fortnight of lights-on costs the vendor
almost nothing next to losing the account.

EVERY CROSS-TENANT READ GOES THROUGH A SECURITY DEFINER FUNCTION. This service
runs unscoped and billing_subscriptions is RLS-protected; read directly it
reports a platform with no subscriptions, which is silent and wrong rather than
loud and wrong. It is the fourth place in this feature where that mattered.

NOTHING HERE TELLS A CUSTOMER ANYTHING. These notices are for the vendor: §17
is explicit that a tenant's operational alerts belong to the tenant, and the
reverse holds too — the customer's own dunning is a separate conversation, not
something to bolt onto a maintenance job.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: Read once per run. Falls back to these if the row is missing, so a deleted
#: setting degrades to the conservative default rather than to zero — a grace
#: period of zero days would suspend every customer the moment a period ended.
DEFAULTS = {
    "billing.grace_period_days": 14,
    "billing.trial_warning_days": 7,
    "billing.renewal_warning_days": 30,
    "billing.invoice_overdue_days": 7,
    "billing.suspend_on_nonpayment": False,
    "billing.module_expiry_warning_days": 14,
}


async def load_settings(session) -> dict:
    rows = (await session.execute(
        text("SELECT key, value FROM platform_settings")
    )).all()
    out = dict(DEFAULTS)
    for key, value in rows:
        if key not in DEFAULTS:
            continue
        default = DEFAULTS[key]
        try:
            out[key] = (value.strip().lower() in ("true", "1", "yes")
                        if isinstance(default, bool) else int(value))
        except (ValueError, AttributeError):
            # A malformed setting falls back rather than crashing the run. A
            # typo in a policy field should not stop every other customer's
            # lifecycle from being evaluated.
            logger.warning("platform setting %s is not readable (%r); using %r",
                           key, value, default)
    return out


async def notify(
    session, *, kind: str, severity: str, title: str,
    tenant_id: str | None = None, subject: str | None = None,
    detail: dict | None = None,
) -> None:
    """Raise a notice, or note that an existing one is still true.

    Deduplicated while unacknowledged, so a nightly job running for a month
    produces one notice about a trial ending rather than thirty. occurrences
    and last_seen_at carry the recency instead — a console full of the same
    sentence is a console nobody reads.
    """
    await session.execute(text("""
        INSERT INTO platform_notifications
            (id, kind, severity, tenant_id, subject, title, detail)
        VALUES (CAST(:id AS uuid), :kind, :severity,
                CASE WHEN :tid = '' THEN NULL ELSE CAST(:tid AS uuid) END,
                :subject, :title, CAST(:detail AS jsonb))
        ON CONFLICT (kind,
                     COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid),
                     COALESCE(subject, ''))
        WHERE acknowledged_at IS NULL
        DO UPDATE SET last_seen_at = now(),
                      occurrences  = platform_notifications.occurrences + 1,
                      severity     = EXCLUDED.severity,
                      title        = EXCLUDED.title,
                      detail       = EXCLUDED.detail
    """), {
        "id": str(uuid.uuid4()), "kind": kind, "severity": severity,
        "tid": tenant_id or "", "subject": subject, "title": title,
        "detail": json.dumps(detail or {}),
    })


async def _suspend_tenant(session, tenant_id: str, reason: str) -> None:
    """Switch a customer off.

    The heaviest thing this service does, so it is one function with the reason
    written into the notice. The tenants trigger sets is_active FALSE from the
    status, so access stops as a consequence of the commercial state rather
    than through a second switch somebody could forget.
    """
    await session.execute(
        text("UPDATE tenants SET status = 'suspended' WHERE id = CAST(:tid AS uuid)"),
        {"tid": tenant_id},
    )
    logger.warning("subscription lifecycle: suspended tenant %s (%s)", tenant_id, reason)


async def evaluate(session) -> dict:
    """One pass. Returns what it did, so the scheduler can log something useful."""
    settings = await load_settings(session)
    now = datetime.now(timezone.utc)
    counts = {"warned": 0, "graced": 0, "expired": 0, "suspended": 0}

    subscriptions = (await session.execute(
        text("SELECT * FROM platform_subscription_states()")
    )).mappings().all()

    for sub in subscriptions:
        tenant_id = str(sub["tenant_id"])
        name = sub["tenant_name"]
        sub_id = str(sub["subscription_id"])
        period_end = sub["current_period_end"]
        trial_end = sub["trial_end"]

        # ── A trial about to end ─────────────────────────────────────────────
        if sub["status"] == "trialing" and trial_end:
            days = (trial_end - now).days
            if 0 <= days <= settings["billing.trial_warning_days"]:
                await notify(
                    session, kind="trial_ending", severity="warning",
                    tenant_id=tenant_id, subject=sub_id,
                    title=f"{name}'s trial ends in {days} day{'s' if days != 1 else ''}",
                    detail={"tenant": name, "days_remaining": days,
                            "trial_end": trial_end.isoformat()},
                )
                counts["warned"] += 1

        if not period_end:
            continue

        # ── A renewal coming up ──────────────────────────────────────────────
        if sub["status"] == "active" and period_end > now:
            days = (period_end - now).days
            if days <= settings["billing.renewal_warning_days"]:
                kind = ("cancellation_pending" if sub["cancel_at_period_end"]
                        else "renewal_due")
                await notify(
                    session, kind=kind,
                    severity="warning" if sub["cancel_at_period_end"] else "info",
                    tenant_id=tenant_id, subject=sub_id,
                    title=(f"{name} is set to cancel in {days} days"
                           if sub["cancel_at_period_end"]
                           else f"{name} renews in {days} day{'s' if days != 1 else ''}"),
                    detail={"tenant": name, "days_remaining": days,
                            "period_end": period_end.isoformat()},
                )
                counts["warned"] += 1
            continue

        # ── The period has ended ─────────────────────────────────────────────
        if sub["status"] == "active" and period_end <= now:
            grace_until = now + timedelta(days=settings["billing.grace_period_days"])
            await session.execute(
                text("SELECT platform_set_subscription_state("
                     "  CAST(:sid AS uuid), 'past_due', :grace)"),
                {"sid": sub_id, "grace": grace_until},
            )
            await notify(
                session, kind="subscription_past_due", severity="warning",
                tenant_id=tenant_id, subject=sub_id,
                title=f"{name}'s subscription period has ended",
                detail={"tenant": name, "grace_until": grace_until.isoformat(),
                        "grace_days": settings["billing.grace_period_days"]},
            )
            counts["graced"] += 1
            continue

        # ── Grace has run out ────────────────────────────────────────────────
        if sub["status"] == "past_due":
            grace_until = sub["grace_until"]
            if grace_until and grace_until > now:
                continue
            await session.execute(
                text("SELECT platform_set_subscription_state("
                     "  CAST(:sid AS uuid), 'expired', NULL)"),
                {"sid": sub_id},
            )
            counts["expired"] += 1

            if settings["billing.suspend_on_nonpayment"]:
                # Only here: period ended AND grace ran out AND the vendor has
                # deliberately turned this on. Never on one missed payment.
                await _suspend_tenant(session, tenant_id,
                                      "subscription expired after grace")
                counts["suspended"] += 1
                await notify(
                    session, kind="tenant_suspended", severity="critical",
                    tenant_id=tenant_id, subject=sub_id,
                    title=f"{name} has been suspended for non-payment",
                    detail={"tenant": name, "policy": "suspend_on_nonpayment"},
                )
            else:
                await notify(
                    session, kind="subscription_expired", severity="critical",
                    tenant_id=tenant_id, subject=sub_id,
                    title=f"{name}'s subscription has expired",
                    detail={"tenant": name,
                            "note": "Still active: automatic suspension is off."},
                )

    # ── Module licences about to lapse ───────────────────────────────────────
    expiring = (await session.execute(
        text("SELECT * FROM platform_expiring_modules(:days)"),
        {"days": settings["billing.module_expiry_warning_days"]},
    )).mappings().all()
    for row in expiring:
        days = (row["expires_at"] - now).days
        await notify(
            session, kind="module_expiring", severity="warning",
            tenant_id=str(row["tenant_id"]), subject=row["module_type"],
            title=(f"{row['tenant_name']}'s {row['module_type']} licence lapses "
                   f"in {days} day{'s' if days != 1 else ''}"),
            detail={"tenant": row["tenant_name"], "module": row["module_type"],
                    "expires_at": row["expires_at"].isoformat()},
        )
        counts["warned"] += 1

    # ── Invoices nobody has paid ─────────────────────────────────────────────
    #
    # platform_invoices has no RLS, so this reads directly.
    overdue = (await session.execute(text("""
        UPDATE platform_invoices i
           SET status = 'overdue'
         WHERE i.status = 'issued'
           AND i.due_date < CURRENT_DATE - make_interval(days => :grace)
     RETURNING i.id, i.invoice_number, i.tenant_id, i.total_amount, i.due_date
    """), {"grace": settings["billing.invoice_overdue_days"]})).mappings().all()
    for inv in overdue:
        await notify(
            session, kind="invoice_overdue", severity="warning",
            tenant_id=str(inv["tenant_id"]), subject=inv["invoice_number"],
            title=f"Invoice {inv['invoice_number']} is overdue",
            detail={"invoice": inv["invoice_number"],
                    "amount": str(inv["total_amount"]),
                    "due_date": inv["due_date"].isoformat()},
        )
        counts["warned"] += 1

    await session.commit()
    return counts
