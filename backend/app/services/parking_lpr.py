"""Parking ↔ LPR automation service (P3-D).

When the Redis listener receives an `lpr_plate_detected` event it calls
`handle_lpr_plate_detected` here.  This function:
  1. Checks `parking_lpr_cameras` to see if the camera is configured as an
     entry, exit, or dual-role parking camera for a specific carpark.
  2. Entry trigger  → if no active session already exists for this plate, auto-
     creates one (status='active', lpr_triggered=TRUE).
  3. Exit trigger   → finds the most-recent active session for this plate, sets
     exit_at and status='completed' and marks the bay available.
  4. 'both'         → direction hint from the LPR event decides entry vs exit;
     falls back to entry if direction is unknown and no active session exists,
     or exit if an active session exists.
  5. Publishes a WebSocket event so dashboards refresh immediately.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.constants import tenant_events_channel

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _publish_parking_event(redis_client: Any, tenant_id: str, event_type: str, payload: dict) -> None:
    try:
        message = json.dumps({
            "schema_version": 1,
            "event_type": event_type,
            "tenant_id": tenant_id,
            "payload": payload,
            "occurred_at": _now().isoformat(),
        })
        await redis_client.publish(tenant_events_channel(tenant_id), message)
    except Exception as exc:
        logger.warning("parking_lpr publish failed: %s", exc)


async def _get_lpr_camera_config(session: AsyncSession, camera_id: str) -> dict | None:
    """Look up parking LPR camera config using a SECURITY DEFINER function that
    bypasses RLS — the camera_id scopes the result to one row, and the caller
    (handle_lpr_plate_detected) immediately sets app.current_tenant to the
    returned tenant_id before making any further RLS-protected queries."""
    result = await session.execute(
        text("SELECT * FROM get_lpr_config_for_camera(CAST(:cam AS uuid))"),
        {"cam": camera_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def _find_active_session(session: AsyncSession, car_park_id: str, plate_number: str) -> dict | None:
    result = await session.execute(text("""
        SELECT id, bay_id FROM parking_sessions
        WHERE car_park_id = CAST(:cpid AS uuid)
          AND vehicle_plate = :plate
          AND status IN ('active', 'overstay')
        ORDER BY entry_at DESC
        LIMIT 1
    """), {"cpid": car_park_id, "plate": plate_number})
    row = result.mappings().first()
    return dict(row) if row else None


async def _create_entry_session(
    session: AsyncSession,
    tenant_id: str,
    car_park_id: str,
    zone_id: str | None,
    plate_number: str,
    camera_id: str,
    detection_id: str,
) -> UUID:
    session_id = uuid4()
    await session.execute(text("""
        INSERT INTO parking_sessions (
            id, tenant_id, car_park_id, zone_id, vehicle_plate,
            entry_at, entry_camera_id, status, payment_status,
            lpr_triggered, lpr_detection_id
        ) VALUES (
            :sid, CAST(:tid AS uuid), CAST(:cpid AS uuid), CAST(:zid AS uuid),
            :plate, :entry_at, CAST(:cam AS uuid), 'active', 'unpaid', TRUE, CAST(:det AS uuid)
        )
    """), {
        "sid":      session_id,
        "tid":      tenant_id,
        "cpid":     car_park_id,
        "zid":      zone_id,
        "plate":    plate_number,
        "entry_at": _now(),
        "cam":      camera_id,
        "det":      detection_id,
    })
    return session_id


async def _close_exit_session(
    session: AsyncSession,
    session_id: str,
    bay_id: str | None,
    camera_id: str,
    detection_id: str,
) -> None:
    exit_at = _now()
    await session.execute(text("""
        UPDATE parking_sessions
        SET exit_at = :exit_at,
            exit_camera_id = CAST(:cam AS uuid),
            status = 'completed',
            lpr_triggered = TRUE,
            lpr_detection_id = CAST(:det AS uuid),
            duration_minutes = EXTRACT(EPOCH FROM (:exit_at - entry_at)) / 60
        WHERE id = CAST(:sid AS uuid)
    """), {"exit_at": exit_at, "cam": camera_id, "det": detection_id, "sid": session_id})

    if bay_id:
        await session.execute(text("""
            UPDATE parking_bays
            SET status = 'available', vehicle_plate = NULL, occupied_since = NULL
            WHERE id = CAST(:bay_id AS uuid)
        """), {"bay_id": bay_id})


async def handle_lpr_plate_detected(
    db_session: AsyncSession,
    redis_client: Any,
    tenant_id: str,
    data: dict,
) -> None:
    """Called by redis_listener on every `lpr_plate_detected` event."""
    camera_id    = data.get("camera_id", "")
    plate_number = data.get("plate_number", "")
    direction    = data.get("direction", "unknown")
    detection_id = data.get("detection_id", "")

    if not camera_id or not plate_number:
        return

    # _get_lpr_camera_config uses a SECURITY DEFINER function that bypasses RLS
    # so it returns the correct config + real tenant_id regardless of what
    # app.current_tenant is currently set to (important in tests where db_session
    # may have no tenant GUC set).
    config = await _get_lpr_camera_config(db_session, camera_id)
    if not config:
        return  # this camera is not configured as a parking LPR camera

    # Use the real tenant_id from the camera record, not the passed-in one
    # (they differ in tests where db_session has no tenant context).
    real_tenant_id = str(config["tenant_id"])
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": real_tenant_id},
    )
    tenant_id = real_tenant_id

    trigger_type = config["trigger_type"]
    car_park_id  = str(config["car_park_id"])
    zone_id      = str(config["default_zone_id"]) if config.get("default_zone_id") else None
    car_park_name = config.get("car_park_name", "")

    active_session = await _find_active_session(db_session, car_park_id, plate_number)

    # Decide action based on trigger_type and direction
    do_entry = False
    do_exit  = False

    if trigger_type == "entry":
        do_entry = True
    elif trigger_type == "exit":
        do_exit = True
    else:  # 'both' — use direction hint or presence of active session
        if direction == "in":
            do_entry = True
        elif direction == "out":
            do_exit = True
        elif active_session:
            do_exit = True   # plate seen again on a dual camera — assume exit
        else:
            do_entry = True  # no active session — assume new entry

    try:
        if do_entry and not active_session:
            new_session_id = await _create_entry_session(
                db_session, tenant_id, car_park_id, zone_id,
                plate_number, camera_id, detection_id,
            )
            await db_session.commit()
            logger.info("parking_lpr: auto-entry plate=%s session=%s", plate_number, new_session_id)
            asyncio.create_task(_publish_parking_event(redis_client, tenant_id, "parking_session_created", {
                "session_id": str(new_session_id),
                "plate_number": plate_number,
                "car_park_name": car_park_name,
                "trigger": "lpr_auto_entry",
            }))

        elif do_exit and active_session:
            await _close_exit_session(
                db_session, str(active_session["id"]),
                str(active_session["bay_id"]) if active_session.get("bay_id") else None,
                camera_id, detection_id,
            )
            await db_session.commit()
            logger.info("parking_lpr: auto-exit plate=%s session=%s", plate_number, active_session["id"])
            asyncio.create_task(_publish_parking_event(redis_client, tenant_id, "parking_session_closed", {
                "session_id": str(active_session["id"]),
                "plate_number": plate_number,
                "car_park_name": car_park_name,
                "trigger": "lpr_auto_exit",
            }))

        else:
            # entry triggered but session already active, or exit triggered but no session — no-op
            logger.debug(
                "parking_lpr: no-op plate=%s trigger=%s active=%s",
                plate_number, trigger_type, bool(active_session),
            )

    except Exception as exc:
        logger.error("parking_lpr handle_lpr_plate_detected error: %s", exc)
        await db_session.rollback()
