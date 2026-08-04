"""Visitor Management System — LPR-driven entry/exit for a site.

Wires a site's own ANPR cameras to its visitor workflow:

  entry camera reads a plate
      -> decision engine classifies the vehicle
      -> staff/VIP/whitelist/emergency pass silently (barrier handles them)
      -> anything else opens a visitor-entry prompt on the operator's screen
         and starts the free-parking clock

  exit camera reads the same plate
      -> the open visit is closed automatically, parking clock stops

A site with no entry/exit camera configured is untouched by all of this: the
operator adds visitors by hand exactly as before. Everything here is gated on
sites.vms_enabled AND the relevant camera actually being bound, so switching
VMS on is a per-site decision and never a global behaviour change.

WHY A PLACEHOLDER VISITOR ROW IS CREATED BEFORE THE OPERATOR FILLS THE FORM
    The parking clock has to start when the vehicle arrives, not when someone
    gets around to typing a name. If the row were only created on submit, a
    vehicle whose prompt the operator ignored or dismissed would be invisible
    to the overstay sweep — which is precisely the vehicle most likely to need
    clamping. So the arrival is recorded immediately with a placeholder name
    and status 'pending', and the operator's submission fills it in.
"""
from __future__ import annotations

import json
import uuid as _uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.decision_engine import evaluate_vehicle

# Rules whose verdict means "this vehicle has standing permission" — the
# operator does not need a visitor form for a staff car arriving at 8am.
# Chosen by RULE rather than by decision, because a decision of
# require_operator can arise for several different reasons and only some of
# them warrant a visitor form.
_SILENT_RULES = frozenset({"standing_permission"})

# Rules that mean the vehicle should never have got in at all — the barrier
# refuses and an alert fires; a visitor form would be the wrong response.
_REFUSAL_RULES = frozenset({"blacklisted"})


async def _site_for_camera(db: AsyncSession, camera_id: str, lane: str) -> dict | None:
    """The VMS-enabled site that has this camera bound to the given lane, or
    None. `lane` is 'entry' or 'exit'."""
    column = "entry_lpr_camera_id" if lane == "entry" else "exit_lpr_camera_id"
    row = (
        await db.execute(
            text(
                f"""
                SELECT id, name, free_parking_minutes
                FROM sites
                WHERE {column} = CAST(:cam AS uuid)
                  AND vms_enabled = TRUE AND is_active = TRUE
                LIMIT 1
                """
            ),
            {"cam": camera_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def get_form_fields(db: AsyncSession, site_id: str | None) -> list[dict]:
    """Active form fields for a site: the tenant-wide set plus that site's own,
    site-specific first so a site can front-load its own questions."""
    rows = (
        await db.execute(
            text(
                """
                SELECT id, field_key, label, field_type, options, is_required,
                       placeholder, help_text, sort_order, site_id
                FROM visitor_form_fields
                WHERE is_active = TRUE
                  AND (site_id IS NULL OR site_id = CAST(:site AS uuid))
                ORDER BY (site_id IS NULL), sort_order, label
                """
            ),
            {"site": site_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def validate_custom_fields(fields: list[dict], values: dict[str, Any]) -> None:
    """Raise ValueError describing the first problem found.

    Kept as a plain function taking plain data so the router, any import path
    and the tests all validate identically — a required field that the API
    enforces but a bulk import skips would make the data untrustworthy.
    """
    for f in fields:
        key, label = f["field_key"], f["label"]
        raw = values.get(key)
        missing = raw is None or (isinstance(raw, (str, list)) and len(raw) == 0)
        if f["is_required"] and missing:
            raise ValueError(f"'{label}' is required")
        if missing:
            continue
        if f["field_type"] == "select":
            allowed = [o["value"] if isinstance(o, dict) else o for o in (f["options"] or [])]
            if allowed and raw not in allowed:
                raise ValueError(f"'{label}' must be one of: {', '.join(map(str, allowed))}")
        elif f["field_type"] == "multiselect":
            allowed = [o["value"] if isinstance(o, dict) else o for o in (f["options"] or [])]
            if not isinstance(raw, list):
                raise ValueError(f"'{label}' must be a list")
            bad = [v for v in raw if allowed and v not in allowed]
            if bad:
                raise ValueError(f"'{label}' has invalid values: {', '.join(map(str, bad))}")
        elif f["field_type"] == "number":
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"'{label}' must be a number")
        elif f["field_type"] == "checkbox":
            if not isinstance(raw, bool):
                raise ValueError(f"'{label}' must be true or false")


async def _publish(redis, tenant_id: str, event_type: str, payload: dict) -> None:
    if redis is None:
        return
    try:
        await redis.publish(
            f"tenant_events:{tenant_id}",
            json.dumps(
                {"event_type": event_type, "tenant_id": tenant_id, "payload": payload},
                default=str,
            ),
        )
    except Exception:
        # The visit is already committed; a failed push must not undo it. The
        # operator's next refresh picks it up from Postgres.
        pass


async def _find_prereg_visitor(db: AsyncSession, plate: str, site_id: str) -> dict | None:
    """An existing pre-registration for this plate that is still open today —
    the same predicate visitors.py uses for an open booking."""
    row = (
        await db.execute(
            text(
                """
                SELECT id, full_name, company, host_name, purpose, custom_fields
                FROM visitors
                WHERE vehicle_plate = :plate
                  AND is_active = TRUE
                  AND status NOT IN ('departed', 'cancelled')
                  AND vehicle_entry_at IS NULL
                  AND (site_id = CAST(:site AS uuid) OR site_id IS NULL)
                  AND (expected_from  IS NULL OR expected_from::date  <= CURRENT_DATE)
                  AND (expected_until IS NULL OR expected_until::date >= CURRENT_DATE)
                ORDER BY expected_from NULLS LAST
                LIMIT 1
                """
            ),
            {"plate": plate, "site": site_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def handle_lpr_entry(
    db: AsyncSession,
    redis,
    *,
    tenant_id: str,
    camera_id: str,
    plate_number: str,
    detection_id: str | None = None,
    confidence: float | None = None,
) -> dict | None:
    """Called when the entry LPR camera reads a plate.

    Returns a summary dict, or None when this camera is not a VMS entry lane
    (the overwhelmingly common case — most cameras aren't gate cameras).
    Caller commits.
    """
    site = await _site_for_camera(db, camera_id, "entry")
    if site is None:
        return None

    plate = plate_number.strip().upper()
    outcome, facts = await evaluate_vehicle(db, plate, confidence=confidence)

    # Known-good vehicles pass without bothering anyone. This is the
    # "only unknown/visitor vehicles" behaviour: a staff car park would
    # otherwise pop a form every few seconds.
    if outcome.rule in _SILENT_RULES or outcome.rule in _REFUSAL_RULES:
        return {
            "site_id": str(site["id"]),
            "prompted": False,
            "decision": outcome.decision.value,
            "rule": outcome.rule,
            "reason": outcome.reason,
        }

    prereg = await _find_prereg_visitor(db, plate, str(site["id"]))
    free_minutes = site["free_parking_minutes"]

    if prereg:
        # Expected visitor: start their visit rather than asking the operator
        # to retype what was already booked.
        visitor_id = str(prereg["id"])
        await db.execute(
            text(
                """
                UPDATE visitors
                   SET vehicle_entry_at = now(), status = 'arrived',
                       entry_lpr_detection_id = CAST(:det AS uuid),
                       site_id = COALESCE(site_id, CAST(:site AS uuid)),
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {"det": detection_id, "site": str(site["id"]), "id": visitor_id},
        )
        needs_details = False
        display_name = prereg["full_name"]
    else:
        # Unrecognised vehicle: record the arrival now so the parking clock is
        # running, and ask the operator who this is.
        visitor_id = str(_uuid.uuid4())
        display_name = f"Pending — {plate}"
        await db.execute(
            text(
                """
                INSERT INTO visitors (
                    id, tenant_id, site_id, full_name, vehicle_plate, status,
                    qr_token, vehicle_entry_at, entry_lpr_detection_id
                ) VALUES (
                    CAST(:id AS uuid), current_setting('app.current_tenant')::uuid,
                    CAST(:site AS uuid), :name, :plate, 'pending',
                    :qr, now(), CAST(:det AS uuid)
                )
                """
            ),
            {
                "id": visitor_id,
                "site": str(site["id"]),
                "name": display_name,
                "plate": plate,
                "qr": str(_uuid.uuid4()),
                "det": detection_id,
            },
        )
        needs_details = True

    await db.execute(
        text(
            """
            INSERT INTO visitor_logs (
                tenant_id, visitor_id, site_id, event_type, checkin_method,
                is_unregistered, notes
            ) VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:vid AS uuid), CAST(:site AS uuid), 'arrival', 'lpr',
                :unreg, :notes
            )
            """
        ),
        {
            "vid": visitor_id,
            "site": str(site["id"]),
            "unreg": prereg is None,
            "notes": f"ANPR entry: {plate} ({outcome.reason})",
        },
    )

    payload = {
        "visitor_id": visitor_id,
        "site_id": str(site["id"]),
        "site_name": site["name"],
        "camera_id": camera_id,
        "plate_number": plate,
        "display_name": display_name,
        "company": facts.company,
        "owner_name": facts.owner_name,
        "category": facts.category,
        "decision": outcome.decision.value,
        "reason": outcome.reason,
        "needs_details": needs_details,
        "free_parking_minutes": free_minutes,
        "form_fields": await get_form_fields(db, str(site["id"])),
    }
    # The operator console listens for this and opens the visitor form in
    # place, on the screen they are already watching.
    await _publish(redis, tenant_id, "visitor_entry_prompt", payload)
    return {"site_id": str(site["id"]), "prompted": True, "visitor_id": visitor_id, **payload}


async def handle_lpr_exit(
    db: AsyncSession,
    redis,
    *,
    tenant_id: str,
    camera_id: str,
    plate_number: str,
    detection_id: str | None = None,
) -> dict | None:
    """Called when the exit LPR camera reads a plate. Closes the open visit for
    that vehicle and stops the parking clock. Caller commits."""
    site = await _site_for_camera(db, camera_id, "exit")
    if site is None:
        return None

    plate = plate_number.strip().upper()
    row = (
        await db.execute(
            text(
                """
                UPDATE visitors
                   SET vehicle_exit_at = now(), status = 'departed',
                       exit_lpr_detection_id = CAST(:det AS uuid), updated_at = now()
                 WHERE id = (
                     SELECT id FROM visitors
                      WHERE vehicle_plate = :plate
                        AND vehicle_entry_at IS NOT NULL
                        AND vehicle_exit_at IS NULL
                        AND is_active = TRUE
                      ORDER BY vehicle_entry_at DESC
                      LIMIT 1
                 )
                RETURNING id, full_name, vehicle_entry_at,
                          EXTRACT(EPOCH FROM (now() - vehicle_entry_at))/60 AS minutes_on_site
                """
            ),
            {"plate": plate, "det": detection_id},
        )
    ).mappings().first()

    if row is None:
        # A vehicle leaving that never registered an entry — usually a plate
        # first seen on the way out (entry misread, or it was already on site
        # before VMS was switched on). Reported rather than silently dropped
        # so a systematically misreading entry camera is visible.
        return {"site_id": str(site["id"]), "matched": False, "plate_number": plate}

    await db.execute(
        text(
            """
            INSERT INTO visitor_logs (
                tenant_id, visitor_id, site_id, event_type, checkin_method, notes
            ) VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:vid AS uuid), CAST(:site AS uuid), 'departure', 'lpr', :notes
            )
            """
        ),
        {
            "vid": str(row["id"]),
            "site": str(site["id"]),
            "notes": f"ANPR exit: {plate}",
        },
    )

    payload = {
        "visitor_id": str(row["id"]),
        "site_id": str(site["id"]),
        "site_name": site["name"],
        "plate_number": plate,
        "display_name": row["full_name"],
        "minutes_on_site": int(row["minutes_on_site"] or 0),
    }
    await _publish(redis, tenant_id, "visitor_exit_recorded", payload)
    return {"matched": True, **payload}


async def _actuate_barrier(
    db: AsyncSession, camera_id: str, *, plate: str, decision: str, reason: str
) -> dict | None:
    """Open the barrier bound to this ANPR camera, if there is one and it is
    configured to open automatically.

    Imported locally: execute_command lives beside the barrier endpoints so
    manual and automatic actuations share one implementation and one command
    log. A module-level import here would be a service reaching up into a
    router at import time; deferring it keeps the dependency to call time.
    """
    from app.routers.barriers import execute_command  # noqa: PLC0415

    row = (
        await db.execute(
            text(
                """
                SELECT b.id, b.name, b.vendor, b.host, b.port, b.username,
                       b.password_encrypted, b.relay_channel, b.pulse_ms
                FROM barriers b
                WHERE b.camera_id = CAST(:cam AS uuid)
                  AND b.is_active = TRUE AND b.auto_open_enabled = TRUE
                LIMIT 1
                """
            ),
            {"cam": camera_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return await execute_command(
        db, row, "open",
        source="decision_engine",
        decision=decision,
        plate_number=plate,
        reason=reason,
    )


async def handle_lpr_access(
    db: AsyncSession, redis, *, tenant_id: str, payload: dict
) -> dict:
    """Entry point from the Redis listener for every `lpr_plate_detected`.

    Runs the whole gate chain for one plate read:
        which lane is this camera? -> classify the vehicle -> open or hold
        -> start/stop the visitor's parking clock

    Ordering note: the barrier is actuated only for an entry lane. An exit
    lane opens on its own hardware loop in every real installation (a car
    inside a car park must always be able to leave, even if this software is
    down), so driving it from here would be both redundant and, if the
    network dropped, a way to trap vehicles on site.

    Caller commits. Never raises — a failure here must not kill the listener
    that also carries alerts and camera status.
    """
    camera_id = payload.get("camera_id")
    plate = (payload.get("plate_number") or "").strip().upper()
    if not camera_id or not plate:
        return {"handled": False, "reason": "missing camera_id or plate_number"}

    detection_id = payload.get("detection_id")
    confidence = payload.get("confidence")
    result: dict = {"handled": True, "plate_number": plate}

    # Exit lane first: a plate read at an exit camera is unambiguous, and
    # checking it first avoids the pathological case of one camera somehow
    # bound as both lanes being treated as an arrival forever.
    exit_result = await handle_lpr_exit(
        db, redis, tenant_id=tenant_id, camera_id=camera_id,
        plate_number=plate, detection_id=detection_id,
    )
    if exit_result is not None:
        return {**result, "lane": "exit", **exit_result}

    entry_result = await handle_lpr_entry(
        db, redis, tenant_id=tenant_id, camera_id=camera_id,
        plate_number=plate, detection_id=detection_id, confidence=confidence,
    )

    if entry_result is not None:
        result.update({"lane": "entry", **entry_result})
        decision, reason = entry_result.get("decision"), entry_result.get("reason", "")
    else:
        # Not a VMS lane. A barrier can still be bound to this camera — a gate
        # with no visitor workflow is a perfectly normal deployment — so the
        # access decision still has to run.
        outcome, _facts = await evaluate_vehicle(db, plate, confidence=confidence)
        decision, reason = outcome.decision.value, outcome.reason
        result.update({"lane": "none", "decision": decision, "reason": reason})

    if decision in ("auto_open", "auto_allow"):
        cmd = await _actuate_barrier(
            db, camera_id, plate=plate, decision=decision, reason=reason
        )
        if cmd is not None:
            result["barrier_command"] = {
                "succeeded": cmd["succeeded"], "error": cmd["error"]
            }
    return result
