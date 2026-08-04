"""Barrier / gate control — /api/v1/barriers

Device CRUD plus the actuation endpoints an operator uses to physically open
a gate, and the append-only command log that makes every one of those
actuations accountable.

Two things this router is deliberately strict about:

* EVERY actuation attempt is logged, including failures and including the
  ones the decision engine issues without a human. A device that admits
  vehicles to a site has to be answerable to "who opened it, when, and
  why" — logging only successes would hide exactly the events an
  investigation cares about.

* The stored device password is never returned by any endpoint. Responses
  carry `has_credentials` instead, so the UI can show configuration state
  without the API ever becoming a credential-exfiltration route.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.barrier import (
    SUPPORTED_VENDORS,
    VENDOR_CAPABILITIES,
    BarrierConfig,
    get_driver,
    supports,
)

router = APIRouter(prefix="/api/v1/barriers", tags=["barriers"])

_READ = Depends(require_permission("barrier:read"))
_OPERATE = Depends(require_permission("barrier:operate"))
_MANAGE = Depends(require_permission("barrier:manage"))

# Commands an operator may issue directly. 'status' is excluded — it is a
# read, exposed separately, and logging it would bury real actuations in
# polling noise.
_OPERATOR_COMMANDS = ("open", "close", "hold_open", "release_hold", "emergency_override")

_SELECT_COLUMNS = """
    b.id, b.tenant_id, b.site_id, b.camera_id, b.door_id, b.name,
    b.lane_direction, b.vendor, b.host, b.port, b.username,
    b.relay_channel, b.pulse_ms, b.auto_open_enabled, b.is_active,
    b.last_status, b.last_status_at, b.last_error, b.created_at, b.updated_at,
    (b.password_encrypted IS NOT NULL) AS has_credentials
"""


class BarrierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    vendor: str
    site_id: str | None = None
    camera_id: str | None = None
    door_id: str | None = None
    lane_direction: str = "entry"
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    relay_channel: int | None = Field(default=None, ge=1)
    pulse_ms: int = Field(default=1000, ge=100, le=10_000)
    auto_open_enabled: bool = True


class BarrierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    vendor: str | None = None
    site_id: str | None = None
    camera_id: str | None = None
    door_id: str | None = None
    lane_direction: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    relay_channel: int | None = Field(default=None, ge=1)
    pulse_ms: int | None = Field(default=None, ge=100, le=10_000)
    auto_open_enabled: bool | None = None
    is_active: bool | None = None


class CommandRequest(BaseModel):
    command: str
    reason: str | None = None


def _validate_vendor(vendor: str) -> None:
    if vendor not in SUPPORTED_VENDORS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unsupported vendor '{vendor}'; supported: {', '.join(SUPPORTED_VENDORS)}",
        )


def _config_from_row(row) -> BarrierConfig:
    """Build a driver config, decrypting the device password only at the
    moment it is needed and never storing it back anywhere."""
    password = None
    if row["password_encrypted"]:
        try:
            password = decrypt_secret(row["password_encrypted"])
        except ValueError:
            # A credential encrypted under a rotated/missing key. Surfaced as
            # a device auth failure downstream rather than a 500 — the
            # operator's actual problem is "this gate won't open", and the
            # command log will carry the reason.
            password = None
    return BarrierConfig(
        vendor=row["vendor"],
        host=row["host"],
        port=row["port"],
        username=row["username"],
        password=password,
        relay_channel=row["relay_channel"],
        pulse_ms=row["pulse_ms"] or 1000,
        device_key=str(row["id"]),
    )


async def _fetch_barrier(db: AsyncSession, barrier_id: str, *, with_secret: bool = False):
    cols = _SELECT_COLUMNS + (", b.password_encrypted" if with_secret else "")
    row = (
        await db.execute(
            text(f"SELECT {cols} FROM barriers b WHERE b.id = CAST(:id AS uuid)"),
            {"id": barrier_id},
        )
    ).mappings().first()
    if row is None:
        # 404 rather than 403 for a row scoped away by RLS — matches the
        # not-found-not-forbidden convention used across this codebase.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Barrier not found")
    return row


async def _publish(request: Request, tenant_id: str, payload: dict) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    try:
        await redis.publish(f"tenant_events:{tenant_id}", json.dumps(payload, default=str))
    except Exception:
        # A realtime push failure must never fail the actuation it describes —
        # the command already happened and is already logged in Postgres,
        # which is the source of truth.
        pass


# ── Vendor capability matrix ──────────────────────────────────────────────

@router.get("/vendors", dependencies=[_READ])
async def list_vendors():
    """Drives the operator UI so it never offers a button that is guaranteed
    to fail (several Dahua barrier models cannot latch open at all)."""
    return {
        "vendors": [
            {"vendor": v, "commands": list(VENDOR_CAPABILITIES[v])} for v in SUPPORTED_VENDORS
        ]
    }


# ── CRUD ──────────────────────────────────────────────────────────────────

@router.get("", dependencies=[_READ])
async def list_barriers(
    site_id: str | None = None,
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    clauses, params = [], {}
    if site_id:
        clauses.append("b.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if is_active is not None:
        clauses.append("b.is_active = :is_active")
        params["is_active"] = is_active
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT {_SELECT_COLUMNS},
                       s.name AS site_name,
                       c.name AS camera_name
                FROM barriers b
                LEFT JOIN sites   s ON s.id = b.site_id
                LEFT JOIN cameras c ON c.id = b.camera_id
                {where}
                ORDER BY b.name
                """
            ),
            params,
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_barrier(body: BarrierCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    _validate_vendor(body.vendor)
    row = (
        await db.execute(
            text(
                f"""
                INSERT INTO barriers (
                    tenant_id, site_id, camera_id, door_id, name, lane_direction,
                    vendor, host, port, username, password_encrypted,
                    relay_channel, pulse_ms, auto_open_enabled
                ) VALUES (
                    current_setting('app.current_tenant')::uuid,
                    CAST(:site_id AS uuid), CAST(:camera_id AS uuid), CAST(:door_id AS uuid),
                    :name, :lane_direction, :vendor, :host, :port, :username, :password_encrypted,
                    :relay_channel, :pulse_ms, :auto_open_enabled
                )
                RETURNING {_SELECT_COLUMNS.replace('b.', '')}
                """
            ),
            {
                "site_id": body.site_id,
                "camera_id": body.camera_id,
                "door_id": body.door_id,
                "name": body.name,
                "lane_direction": body.lane_direction,
                "vendor": body.vendor,
                "host": body.host,
                "port": body.port,
                "username": body.username,
                "password_encrypted": encrypt_secret(body.password) if body.password else None,
                "relay_channel": body.relay_channel,
                "pulse_ms": body.pulse_ms,
                "auto_open_enabled": body.auto_open_enabled,
            },
        )
    ).mappings().first()
    await db.commit()
    return dict(row)


@router.get("/{barrier_id}", dependencies=[_READ])
async def get_barrier(barrier_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    return dict(await _fetch_barrier(db, barrier_id))


@router.put("/{barrier_id}", dependencies=[_MANAGE])
async def update_barrier(
    barrier_id: str, body: BarrierUpdate, db: AsyncSession = Depends(get_db_with_tenant)
):
    await _fetch_barrier(db, barrier_id)
    if body.vendor is not None:
        _validate_vendor(body.vendor)

    fields = body.model_dump(exclude_unset=True)
    # A password is write-only: supplying it replaces the stored ciphertext,
    # omitting it leaves the existing credential untouched. There is no way to
    # read it back out.
    if "password" in fields:
        pw = fields.pop("password")
        fields["password_encrypted"] = encrypt_secret(pw) if pw else None
    if not fields:
        return dict(await _fetch_barrier(db, barrier_id))

    casts = {"site_id": "uuid", "camera_id": "uuid", "door_id": "uuid"}
    sets = ", ".join(
        f"{k} = CAST(:{k} AS {casts[k]})" if k in casts else f"{k} = :{k}" for k in fields
    )
    row = (
        await db.execute(
            text(
                f"""
                UPDATE barriers SET {sets}, updated_at = now()
                WHERE id = CAST(:barrier_id AS uuid)
                RETURNING {_SELECT_COLUMNS.replace('b.', '')}
                """
            ),
            {**fields, "barrier_id": barrier_id},
        )
    ).mappings().first()
    await db.commit()
    return dict(row)


@router.delete("/{barrier_id}", dependencies=[_MANAGE])
async def deactivate_barrier(barrier_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await _fetch_barrier(db, barrier_id)
    # Soft delete: the command history references this row and is an audit
    # record, so the barrier itself must survive its own decommissioning.
    await db.execute(
        text("UPDATE barriers SET is_active = FALSE, updated_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": barrier_id},
    )
    await db.commit()
    return {"status": "deactivated"}


# ── Actuation ─────────────────────────────────────────────────────────────

async def execute_command(
    db: AsyncSession,
    barrier_row,
    command: str,
    *,
    source: str,
    reason: str | None = None,
    issued_by_user_id: str | None = None,
    decision: str | None = None,
    plate_number: str | None = None,
) -> dict:
    """Run one command against the device and log the attempt.

    Shared by the operator endpoint below and the decision engine's automatic
    path, so an automatic opening is recorded in exactly the same place and
    the same shape as a manual one — one command history, not two.

    The caller commits. Network I/O happens inside the caller's transaction,
    which is acceptable here because driver timeouts are hard-bounded (see
    services/barrier/base.py) and gate commands are low-volume.
    """
    # emergency_override is a latched open with a mandatory reason. It maps to
    # the driver's hold_open but is logged under its own command name so it
    # stands out in an audit rather than blending into routine holds.
    driver_method = "hold_open" if command == "emergency_override" else command

    if not supports(barrier_row["vendor"], driver_method):
        result_ok, result_status, error, latency = (
            False, None, f"'{command}' is not supported by the {barrier_row['vendor']} driver", 0,
        )
    else:
        driver = get_driver(_config_from_row(barrier_row))
        result = await getattr(driver, driver_method)()
        result_ok, result_status, error, latency = (
            result.ok, result.status, result.error, result.latency_ms,
        )

    cmd_row = (
        await db.execute(
            text(
                """
                INSERT INTO barrier_commands (
                    tenant_id, barrier_id, command, source, decision, plate_number,
                    reason, issued_by_user_id, succeeded, error, latency_ms
                ) VALUES (
                    current_setting('app.current_tenant')::uuid,
                    CAST(:barrier_id AS uuid), :command, :source, :decision, :plate_number,
                    :reason, CAST(:issued_by AS uuid), :succeeded, :error, :latency_ms
                )
                RETURNING id, command, source, decision, plate_number, reason,
                          succeeded, error, latency_ms, created_at
                """
            ),
            {
                "barrier_id": str(barrier_row["id"]),
                "command": command,
                "source": source,
                "decision": decision,
                "plate_number": plate_number,
                "reason": reason,
                "issued_by": issued_by_user_id,
                "succeeded": result_ok,
                "error": error,
                "latency_ms": latency,
            },
        )
    ).mappings().first()

    # Cache last-known position so the wall/list view doesn't have to poll the
    # device. last_error is cleared on success so a stale fault doesn't linger
    # on a gate that is now working.
    await db.execute(
        text(
            """
            UPDATE barriers
               SET last_status = COALESCE(:st, last_status),
                   last_status_at = now(),
                   last_error = :err,
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            """
        ),
        {"st": result_status if result_ok else None, "err": error, "id": str(barrier_row["id"])},
    )
    return dict(cmd_row)


@router.post("/{barrier_id}/command", dependencies=[_OPERATE])
async def issue_command(
    barrier_id: str,
    body: CommandRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.command not in _OPERATOR_COMMANDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown command '{body.command}'; expected one of: {', '.join(_OPERATOR_COMMANDS)}",
        )
    if body.command == "emergency_override" and not (body.reason or "").strip():
        # An override bypasses every access rule. Requiring a stated reason is
        # what makes it reviewable afterwards.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "emergency_override requires a reason",
        )

    row = await _fetch_barrier(db, barrier_id, with_secret=True)
    if not row["is_active"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Barrier is deactivated")

    cmd = await execute_command(
        db, row, body.command,
        source="operator",
        reason=body.reason,
        issued_by_user_id=str(token.user_id),
    )
    await db.commit()

    await _publish(request, str(token.tenant_id), {
        "event_type": "barrier_command",
        "tenant_id": str(token.tenant_id),
        "payload": {
            "barrier_id": barrier_id,
            "barrier_name": row["name"],
            "command": body.command,
            "succeeded": cmd["succeeded"],
            "error": cmd["error"],
        },
    })

    if not cmd["succeeded"]:
        # 502: the request was valid, the downstream device refused or was
        # unreachable. The attempt is committed and visible in the log either
        # way — the operator must not be told a gate opened when it did not.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, cmd["error"] or "device command failed")
    return cmd


@router.get("/{barrier_id}/status", dependencies=[_READ])
async def barrier_status(barrier_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Live position query. Not written to the command log — it is a read, and
    logging polls would drown the real actuations."""
    row = await _fetch_barrier(db, barrier_id, with_secret=True)
    result = await get_driver(_config_from_row(row)).status()
    await db.execute(
        text(
            "UPDATE barriers SET last_status = :st, last_status_at = now(), last_error = :err "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"st": result.status if result.ok else None, "err": result.error, "id": barrier_id},
    )
    await db.commit()
    return {
        "barrier_id": barrier_id,
        "reachable": result.ok,
        "status": result.status,
        "error": result.error,
        "latency_ms": result.latency_ms,
    }


@router.get("/{barrier_id}/commands", dependencies=[_READ])
async def list_commands(
    barrier_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    await _fetch_barrier(db, barrier_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT bc.id, bc.command, bc.source, bc.decision, bc.plate_number,
                       bc.reason, bc.succeeded, bc.error, bc.latency_ms, bc.created_at,
                       u.full_name AS issued_by_name
                FROM barrier_commands bc
                LEFT JOIN users u ON u.id = bc.issued_by_user_id
                WHERE bc.barrier_id = CAST(:id AS uuid)
                ORDER BY bc.created_at DESC
                LIMIT :limit
                """
            ),
            {"id": barrier_id, "limit": min(max(limit, 1), 500)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]
