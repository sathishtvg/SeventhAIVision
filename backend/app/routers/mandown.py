"""Man-down — notice when a guard stops moving, and act on it.

A lone officer collapses in a plant room at 03:00. The panic button is the only
route to help today and it needs a conscious guard with a free hand, which is
exactly what a man-down event is not.

THE PHONE DETECTS, THE SERVER DECIDES. The app raises an event and shows a
countdown the guard can cancel — most triggers are a phone left on a desk, and
a system that escalates all of them is a system people switch off. But the
escalation deadline lives here and a scheduler sweep enforces it, because a
handset that shattered on impact will never call back and that is the case that
matters most.

Permissions:
  mandown:report — raise and cancel (the phone acting for the guard)
  mandown:read   — see events
  mandown:manage — acknowledge, resolve, and change the thresholds
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services.mandown import MANDOWN_DEFAULTS, escalate_event, get_settings

router = APIRouter(prefix="/api/v1/man-down", tags=["guard-ops"])

VALID_TRIGGERS = {"no_motion", "impact", "tilt", "manual"}
VALID_OUTCOMES = {"false_alarm", "guard_ok", "injury", "other"}
LIVE_STATUSES = ("pending", "escalated", "acknowledged")


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: str = "no_motion"
    shift_id: str | None = None
    site_id: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    battery_level: int | None = Field(default=None, ge=0, le=100)
    device_info: str | None = None


class Cancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str | None = None


class Resolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    notes: str | None = None


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    no_motion_seconds: int | None = Field(default=None, ge=30, le=3600)
    countdown_seconds: int | None = Field(default=None, ge=10, le=300)
    impact_threshold_g: int | None = Field(default=None, ge=2, le=16)
    stillness_threshold_mg: int | None = Field(default=None, ge=10, le=500)


_EVENT_SELECT = """
    SELECT e.id, e.guard_user_id, e.shift_id, e.site_id, e.trigger,
           e.detected_at, e.latitude, e.longitude, e.accuracy_m,
           e.battery_level, e.device_info, e.status, e.escalate_at,
           e.cancelled_at, e.escalated_at, e.escalated_by_server,
           e.acknowledged_at, e.resolved_at, e.outcome, e.notes,
           u.full_name AS guard_name, u.employee_code,
           s.name AS site_name,
           ack.full_name AS acknowledged_by_name,
           res.full_name AS resolved_by_name,
           GREATEST(0, EXTRACT(EPOCH FROM (e.escalate_at - now())))::int
               AS seconds_remaining
      FROM man_down_events e
      JOIN users u ON u.id = e.guard_user_id
 LEFT JOIN sites s ON s.id = e.site_id
 LEFT JOIN users ack ON ack.id = e.acknowledged_by_user_id
 LEFT JOIN users res ON res.id = e.resolved_by_user_id
"""


@router.get("/settings", dependencies=[Depends(require_permission("mandown:read"))])
async def read_settings(db: AsyncSession = Depends(get_db_with_tenant)):
    """The thresholds the app should monitor with.

    Returned unprefixed — the phone does not care that they live under
    `mandown.` in tenant_settings, and stripping it here keeps that a storage
    detail rather than part of the wire format.
    """
    resolved = await get_settings(db)
    return {key.split(".", 1)[1]: value for key, value in resolved.items()}


@router.put("/settings", dependencies=[Depends(require_permission("mandown:manage"))])
async def write_settings(
    body: SettingsUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    updates = {
        f"mandown.{field}": value
        for field, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No settings to update")

    for key, value in updates.items():
        if key not in MANDOWN_DEFAULTS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown setting {key}")
        await db.execute(
            text("""
                INSERT INTO tenant_settings
                    (tenant_id, setting_key, setting_value, updated_by_user_id)
                VALUES (current_setting('app.current_tenant')::uuid, :k,
                        CAST(:v AS jsonb), CAST(:uid AS uuid))
                ON CONFLICT (tenant_id, setting_key) DO UPDATE
                    SET setting_value = EXCLUDED.setting_value,
                        updated_by_user_id = EXCLUDED.updated_by_user_id,
                        updated_at = now()
            """),
            {"k": key, "v": "true" if value is True else
                       ("false" if value is False else str(value)),
             "uid": token.user_id},
        )
    # Read back BEFORE committing. app.current_tenant is set with SET LOCAL and
    # dies with the transaction, so a read after the commit would run with an
    # empty GUC and every RLS policy would fail casting ''::uuid.
    resolved = await get_settings(db)
    await db.commit()
    return {key.split(".", 1)[1]: value for key, value in resolved.items()}


@router.get("", dependencies=[Depends(require_permission("mandown:read"))])
async def list_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    live_only: bool = False,
    limit: int = 100,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Live events first, newest first. Anything still pending or escalated is
    somebody standing in a plant room, so nothing else can outrank it."""
    limit = max(1, min(limit, 500))
    where, params = [], {"lim": limit}
    if live_only:
        where.append("e.status = ANY(:live)"); params["live"] = list(LIVE_STATUSES)
    if site_id:
        where.append("e.site_id = CAST(:site_id AS uuid)"); params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "e.site_id", params)
    if scope:
        # An event with no site still reaches everyone — a guard down off-site
        # is not less urgent for being unassigned.
        where.append(f"(e.site_id IS NULL OR {scope})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            {_EVENT_SELECT}
            {clause}
            ORDER BY (e.status = ANY(ARRAY['pending','escalated','acknowledged'])) DESC,
                     e.detected_at DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("mandown:report"))])
async def raise_event(
    body: EventCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The phone reports that its guard has stopped moving.

    Returns the countdown the guard is being shown. That deadline is stored, so
    escalation happens whether or not this handset is ever heard from again.
    """
    if body.trigger not in VALID_TRIGGERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"trigger must be one of {sorted(VALID_TRIGGERS)}")

    settings = await get_settings(db)
    countdown = int(settings["mandown.countdown_seconds"])

    try:
        row = (await db.execute(
            text("""
                INSERT INTO man_down_events
                    (tenant_id, guard_user_id, shift_id, site_id, trigger,
                     latitude, longitude, accuracy_m, battery_level, device_info,
                     escalate_at)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:uid AS uuid), CAST(:shift AS uuid), CAST(:site AS uuid),
                        :trigger, :lat, :lon, :acc, :battery, :device,
                        now() + make_interval(secs => :countdown))
                RETURNING id, status, detected_at, escalate_at
            """),
            {"uid": token.user_id, "shift": body.shift_id, "site": body.site_id,
             "trigger": body.trigger, "lat": body.latitude, "lon": body.longitude,
             "acc": body.accuracy_m, "battery": body.battery_level,
             "device": body.device_info, "countdown": countdown},
        )).mappings().first()
    except IntegrityError as exc:
        # uq_man_down_one_live_per_guard. A phone retrying, or a second trigger
        # while the first is still counting down, is the same incident.
        await db.rollback()
        # The rollback took app.current_tenant with it — it is set with SET
        # LOCAL and scoped to the transaction — so the read below would hit an
        # RLS policy casting ''::uuid. Re-establish it the way the dependency
        # does before asking anything else of this session.
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
            {"tenant_id": token.tenant_id},
        )
        existing = (await db.execute(
            text("""
                SELECT id, status, escalate_at,
                       GREATEST(0, EXTRACT(EPOCH FROM (escalate_at - now())))::int
                           AS seconds_remaining
                  FROM man_down_events
                 WHERE guard_user_id = CAST(:uid AS uuid)
                   AND status = ANY(:live)
            """),
            {"uid": token.user_id, "live": list(LIVE_STATUSES)},
        )).mappings().first()
        if existing is None:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "A man-down event is already live for this guard") from exc
        return {**dict(existing), "already_live": True,
                "countdown_seconds": countdown}

    await db.commit()
    return {**dict(row), "countdown_seconds": countdown, "already_live": False}


@router.post("/{event_id}/cancel", dependencies=[Depends(require_permission("mandown:report"))])
async def cancel_event(
    event_id: str,
    body: Cancel,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """"I am fine" — before the countdown runs out.

    Cancelling is recorded rather than deleted: a guard cancelling six times a
    shift means the thresholds are wrong for how they work, and that is only
    visible if the cancellations are kept.
    """
    row = (await db.execute(
        text("SELECT id, status, guard_user_id FROM man_down_events "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": event_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Man-down event not found")
    # Anyone else cancelling somebody's man-down is a supervisor deciding a
    # guard is fine without being asked, which is exactly backwards.
    if str(row.guard_user_id) != token.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the guard themselves can cancel their man-down")
    if row.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This event is already {row.status} — it cannot be cancelled",
        )

    result = await db.execute(
        text("""
            UPDATE man_down_events
               SET status = 'cancelled', cancelled_at = now(),
                   notes = COALESCE(:notes, notes), updated_at = now()
             WHERE id = CAST(:id AS uuid) AND status = 'pending'
            RETURNING id, status, cancelled_at
        """),
        {"id": event_id, "notes": body.notes},
    )
    updated = result.mappings().first()
    if updated is None:
        # The sweep escalated it between the read and the write. The guard is
        # fine and a supervisor is already moving; say so rather than pretend.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This escalated a moment ago — tell your supervisor you are all right",
        )
    await db.commit()
    return dict(updated)


@router.post("/{event_id}/escalate", dependencies=[Depends(require_permission("mandown:report"))])
async def escalate_now(
    event_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """The countdown expired on the phone, or the guard pressed for help.

    The fast path. The server would have swept this up within the minute
    anyway, which is the point — this only makes it quicker when the handset
    is still working.
    """
    row = (await db.execute(
        text("SELECT id, status FROM man_down_events WHERE id = CAST(:id AS uuid)"),
        {"id": event_id},
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Man-down event not found")
    if row["status"] != "pending":
        return {"escalated": False, "reason": f"already {row['status']}"}

    redis = getattr(request.app.state, "redis", None)
    result = await escalate_event(db, redis, row, by_server=False)
    await db.commit()
    return result


@router.post("/{event_id}/acknowledge",
             dependencies=[Depends(require_permission("mandown:manage"))])
async def acknowledge_event(
    event_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """A supervisor has seen it and is doing something about it.

    Separate from resolving, because "somebody is on their way" and "we know
    what happened" are different facts and the gap between them is the response
    time this whole feature is judged on.
    """
    result = await db.execute(
        text("""
            UPDATE man_down_events
               SET status = 'acknowledged',
                   acknowledged_by_user_id = CAST(:uid AS uuid),
                   acknowledged_at = now(), updated_at = now()
             WHERE id = CAST(:id AS uuid) AND status = 'escalated'
            RETURNING id, status, acknowledged_at,
                      EXTRACT(EPOCH FROM (acknowledged_at - escalated_at))::int
                          AS seconds_to_acknowledge
        """),
        {"id": event_id, "uid": token.user_id},
    )
    row = result.mappings().first()
    if row is None:
        # No rollback: the UPDATE matched nothing, so there is nothing to undo,
        # and rolling back here would drop app.current_tenant — set with SET
        # LOCAL — and make the read below fail inside the RLS policy.
        current = (await db.execute(
            text("SELECT status FROM man_down_events WHERE id = CAST(:id AS uuid)"),
            {"id": event_id},
        )).scalar_one_or_none()
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Man-down event not found")
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Only an escalated event can be acknowledged — this is {current}")
    await db.commit()
    return dict(row)


@router.post("/{event_id}/resolve", dependencies=[Depends(require_permission("mandown:manage"))])
async def resolve_event(
    event_id: str,
    body: Resolve,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Close it out, and say what actually happened.

    The outcome is required. "Resolved" with no outcome would make the false
    alarms indistinguishable from the real ones, and the false-alarm rate is
    the number that tells you whether the thresholds are right.
    """
    if body.outcome not in VALID_OUTCOMES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"outcome must be one of {sorted(VALID_OUTCOMES)}")

    result = await db.execute(
        text("""
            UPDATE man_down_events
               SET status = 'resolved',
                   resolved_by_user_id = CAST(:uid AS uuid),
                   resolved_at = now(), outcome = :outcome,
                   notes = COALESCE(:notes, notes), updated_at = now()
             WHERE id = CAST(:id AS uuid)
               AND status = ANY(ARRAY['escalated', 'acknowledged'])
            RETURNING id, status, outcome, resolved_at,
                      EXTRACT(EPOCH FROM (resolved_at - escalated_at))::int
                          AS seconds_to_resolve
        """),
        {"id": event_id, "uid": token.user_id, "outcome": body.outcome,
         "notes": body.notes},
    )
    row = result.mappings().first()
    if row is None:
        # No rollback: the UPDATE matched nothing, so there is nothing to undo,
        # and rolling back here would drop app.current_tenant — set with SET
        # LOCAL — and make the read below fail inside the RLS policy.
        current = (await db.execute(
            text("SELECT status FROM man_down_events WHERE id = CAST(:id AS uuid)"),
            {"id": event_id},
        )).scalar_one_or_none()
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Man-down event not found")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only a live event can be resolved — this one is {current}",
        )
    await db.commit()
    return dict(row)


@router.get("/{event_id}", dependencies=[Depends(require_permission("mandown:read"))])
async def get_event(event_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(
        text(f"{_EVENT_SELECT} WHERE e.id = CAST(:id AS uuid)"),
        {"id": event_id},
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Man-down event not found")
    return dict(row)
