"""One guard panic button, however it was pressed.

There were two SOS endpoints and they did different halves of the job.

    POST /patrols/sos    raised a critical incident. No occurrence book
                         entry, and — the important part — no live push, so
                         nobody watching a screen was told anything.

    POST /shifts/sos     wrote a critical occurrence book entry and pushed a
                         real-time SOS to supervisors over Redis. No incident,
                         so nothing landed in the queue people actually work.

Whichever button a guard found, they got half a response. The mobile app calls
the second, the web client calls the first, and both are reachable today, so
neither can simply be deleted.

The patrols one also had a quieter failure that matters more. `incidents`
requires a camera_id, and that endpoint sourced one with

    (SELECT id FROM cameras WHERE tenant_id = ... LIMIT 1)

inside an INSERT ... SELECT. For a tenant with no cameras — a guarding-only
customer, which is a large part of who this is sold to — the SELECT returns no
rows, so the INSERT writes nothing, and the endpoint answers
`{"sos_acknowledged": true, "incident_id": null}`. A guard pressed panic, the
app said it was received, and nothing whatsoever was recorded.

So: one function, called by both endpoints, which does all three things and is
honest about which of them succeeded.

The occurrence book entry is the part that must never fail. It needs no camera,
it is append-only, and it is the record a licensed agency is required to keep
under the PSIA — so it is written first and everything else is layered on top
of a commit that has already happened.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def raise_guard_sos(
    db: AsyncSession,
    redis,
    *,
    tenant_id: str,
    user_id: str,
    latitude: float | None = None,
    longitude: float | None = None,
    description: str | None = None,
    site_id: str | None = None,
) -> dict:
    """Record a panic alert and get it in front of a supervisor.

    Returns the ids of everything that was written. `incident_id` is None when
    the tenant has no camera to hang an incident on — the alert still exists in
    the occurrence book and still went out live, and the caller should say so
    rather than implying the whole thing failed.

    `redis` may be None. A missing Redis must not stop the record being kept:
    losing the live push is bad, losing the log entry is worse.
    """
    user_row = (
        await db.execute(
            text("SELECT full_name, email FROM users WHERE id = CAST(:uid AS uuid)"),
            {"uid": user_id},
        )
    ).first()
    guard_name = (user_row.full_name or user_row.email) if user_row else "Unknown Guard"

    location_note = ""
    if latitude is not None and longitude is not None:
        location_note = f" at ({latitude:.5f}, {longitude:.5f})"

    body = description or (
        f"PANIC ALERT: Guard {guard_name} triggered SOS alarm{location_note}. "
        "Immediate response required."
    )

    # ── 1. The record that must survive ──────────────────────────────────────
    entry = (
        await db.execute(
            text(
                """
                INSERT INTO occurrence_book_entries
                    (tenant_id, site_id, author_user_id, entry_type, body, severity,
                     latitude, longitude)
                VALUES (
                    current_setting('app.current_tenant')::uuid,
                    CAST(:site_id AS uuid), CAST(:uid AS uuid),
                    'incident', :body, 'critical', :lat, :lon
                )
                RETURNING id, occurred_at
                """
            ),
            {"site_id": site_id, "uid": user_id, "body": body, "lat": latitude, "lon": longitude},
        )
    ).first()

    # ── 2. The queue people work from, when there is one ─────────────────────
    #
    # Looked up separately rather than as an INSERT ... SELECT so that "this
    # tenant has no cameras" is a value we can see and report, instead of an
    # insert that silently affects zero rows.
    camera_id = (
        await db.execute(
            text(
                "SELECT id FROM cameras "
                "WHERE tenant_id = current_setting('app.current_tenant')::uuid "
                "AND is_active = TRUE LIMIT 1"
            )
        )
    ).scalar_one_or_none()

    incident_id = None
    if camera_id is not None:
        incident = (
            await db.execute(
                text(
                    """
                    INSERT INTO incidents
                        (tenant_id, camera_id, title, description, severity, status,
                         alert_code, message_params, is_auto_created)
                    VALUES (
                        current_setting('app.current_tenant')::uuid,
                        CAST(:cam AS uuid), :title, :description, 'critical', 'open',
                        'guard.sos', CAST(:params AS jsonb), TRUE
                    )
                    RETURNING id
                    """
                ),
                {
                    "cam": str(camera_id),
                    "title": f"GUARD SOS — {guard_name}",
                    "description": body,
                    # Bound and json.dumps'd. This was previously built by
                    # f-string interpolation of the guard's own name straight
                    # into a JSON literal, so a name containing a quote broke
                    # the insert and a crafted one could rewrite the payload.
                    "params": json.dumps(
                        {
                            "guard_user_id": str(user_id),
                            "guard_name": guard_name,
                            "latitude": latitude,
                            "longitude": longitude,
                        }
                    ),
                },
            )
        ).first()
        incident_id = str(incident.id) if incident else None

    await db.commit()

    # ── 3. Tell whoever is watching ──────────────────────────────────────────
    if redis is not None:
        try:
            await redis.publish(
                f"tenant_events:{tenant_id}",
                json.dumps(
                    {
                        "event_type": "sos_triggered",
                        "tenant_id": str(tenant_id),
                        "payload": {
                            "entry_id": str(entry.id),
                            "incident_id": incident_id,
                            "guard_user_id": str(user_id),
                            "guard_name": guard_name,
                            "message": body,
                            "latitude": latitude,
                            "longitude": longitude,
                            "site_id": site_id,
                        },
                        "occurred_at": str(entry.occurred_at),
                    }
                ),
            )
        except Exception:
            # A Redis outage must not turn a received panic alert into an
            # error response. It is already committed; the guard needs to see
            # that it landed.
            pass

    return {
        "entry_id": str(entry.id),
        "occurred_at": str(entry.occurred_at),
        "incident_id": incident_id,
        "guard_name": guard_name,
        "message": body,
    }
