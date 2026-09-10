"""Man-down settings, and the escalation both the phone and the server use.

WHY THIS EXISTS
    Two callers need the same escalation:

        routers/mandown.py    the phone, when its countdown expires
        scheduler_main.py     the sweep, when the phone never called back

    The second is the one that matters. A handset that shattered on impact,
    ran flat, or lost signal is exactly the case man-down exists for, so the
    server must be able to escalate without it. Both routes end in the same
    place — services/sos.raise_guard_sos — because a man-down IS a panic alert,
    just one nobody had a free hand to press.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sos import raise_guard_sos

# Shipped defaults. Every one is a tenant_settings override away from something
# else, because how long a guard can reasonably be still varies enormously
# between a static gatehouse post and a roving patrol.
MANDOWN_DEFAULTS = {
    # Off by default: continuous sensor monitoring on every guard's phone is a
    # battery and privacy decision that belongs to the company, not an upgrade.
    "mandown.enabled": False,
    # How long without meaningful movement before the phone raises an event.
    "mandown.no_motion_seconds": 120,
    # How long the guard then has to cancel before it becomes a panic alert.
    "mandown.countdown_seconds": 30,
    # Acceleration magnitude, in g, that counts as an impact worth watching.
    # An impact shortens the no-motion window rather than triggering directly —
    # a dropped phone is an impact, a fallen guard is an impact then stillness.
    "mandown.impact_threshold_g": 3,
    # How still counts as still, in milli-g of deviation from rest.
    "mandown.stillness_threshold_mg": 60,
}

_BOOL_KEYS = {"mandown.enabled"}


async def get_settings(db: AsyncSession) -> dict:
    """The tenant's thresholds, falling back to the shipped defaults.

    Read as one query rather than five: the phone asks for these on every shift
    start, and five round trips for five integers is five times the work for no
    additional truth.
    """
    rows = (await db.execute(
        text("SELECT setting_key, setting_value FROM tenant_settings "
             "WHERE setting_key = ANY(:keys)"),
        {"keys": list(MANDOWN_DEFAULTS)},
    )).all()
    overrides = {r.setting_key: r.setting_value for r in rows}

    resolved = {}
    for key, default in MANDOWN_DEFAULTS.items():
        value = overrides.get(key)
        if key in _BOOL_KEYS:
            resolved[key] = value if isinstance(value, bool) else default
        else:
            # bool is an int subclass, so a JSONB `true` would otherwise become 1.
            ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
            resolved[key] = value if ok else default
    return resolved


async def escalate_event(db: AsyncSession, redis, event, *, by_server: bool) -> dict:
    """Turn a man-down event into a panic alert.

    Idempotent by construction: the UPDATE only matches a row still in
    'pending', so the phone and the sweep racing each other cannot raise two
    alerts for one guard on one floor. The loser gets `already_escalated` back
    and does nothing.
    """
    claimed = (await db.execute(
        text("""
            UPDATE man_down_events
               SET status = 'escalated',
                   escalated_at = now(),
                   escalated_by_server = :by_server,
                   updated_at = now()
             WHERE id = CAST(:id AS uuid) AND status = 'pending'
            RETURNING id, guard_user_id, site_id, latitude, longitude,
                      trigger, detected_at, battery_level
        """),
        {"id": str(event["id"]), "by_server": by_server},
    )).mappings().first()
    if claimed is None:
        return {"escalated": False, "reason": "already_escalated"}

    guard = (await db.execute(
        text("SELECT full_name, email FROM users WHERE id = CAST(:uid AS uuid)"),
        {"uid": str(claimed["guard_user_id"])},
    )).first()
    name = (guard.full_name or guard.email) if guard else "Unknown guard"

    trigger_words = {
        "no_motion": "has not moved",
        "impact": "was knocked and has not moved since",
        "tilt": "phone is lying flat and has not moved",
        "manual": "reported themselves as needing help",
    }
    battery = claimed["battery_level"]
    battery_note = f" Phone battery {battery}%." if battery is not None else ""
    how = "no response to the countdown" if by_server else "the countdown expired"

    description = (
        f"MAN DOWN: {name} {trigger_words.get(claimed['trigger'], 'may be down')}. "
        f"Detected {claimed['detected_at']:%H:%M}, {how}.{battery_note} "
        "Send someone to their last known position."
    )

    sos = await raise_guard_sos(
        db, redis,
        tenant_id="",  # resolved from the session GUC inside the service
        user_id=str(claimed["guard_user_id"]),
        latitude=claimed["latitude"],
        longitude=claimed["longitude"],
        description=description,
        site_id=str(claimed["site_id"]) if claimed["site_id"] else None,
    )
    return {"escalated": True, "escalated_by_server": by_server, "sos": sos}


async def sweep_pending(db: AsyncSession, redis) -> int:
    """Escalate every event whose countdown ran out without an answer.

    This is the whole point of storing a deadline rather than trusting the
    phone to come back. Runs against one tenant's rows — the caller sets the
    tenant GUC, the same shape every other scheduler sweep uses.
    """
    due = (await db.execute(
        text("""
            SELECT id FROM man_down_events
             WHERE status = 'pending' AND escalate_at <= now()
          ORDER BY escalate_at
             LIMIT 100
        """),
    )).mappings().all()

    # The tenant GUC has to be put back after every escalation.
    #
    # raise_guard_sos commits — it must, because the occurrence-book entry is
    # the record that has to survive whatever happens next. set_config(...,
    # is_local => true) is SET LOCAL, so that commit takes the tenant scope
    # with it, and the NEXT escalation in this loop inserts with
    # app.current_tenant = '' and dies casting '' to uuid.
    #
    # Which means: two guards down at the same site, the first escalates and
    # the second silently does not. For a man-down feature that is the worst
    # possible failure, and it is invisible — the sweep logs one success and
    # one exception nobody reads.
    tenant = (await db.execute(
        text("SELECT current_setting('app.current_tenant', true)")
    )).scalar()

    escalated = 0
    for row in due:
        result = await escalate_event(db, redis, row, by_server=True)
        if result["escalated"]:
            escalated += 1
        if tenant:
            await db.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": tenant},
            )
    return escalated
