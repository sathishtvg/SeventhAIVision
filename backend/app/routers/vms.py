"""Visitor Management System — /api/v1/vms

Three surfaces:

* form-fields — the admin-configurable visitor form (Phase 8 dynamic entry
  forms), including dropdowns. Definitions are tenant-wide (site_id NULL) or
  site-specific.
* entries — completing an LPR-triggered visitor prompt, or adding one by hand
  at a site with no ANPR cameras configured. Both land in the same visitors
  row shape, so downstream reporting doesn't care how a visit started.
* onsite — vehicles currently parked, with elapsed time against the site's
  free-parking allowance. This is what the operator watches for clamping.

No new permission codes: form authoring is visitor administration
(visitor:manage), entry completion is a check-in action (visitor:checkin),
and reads are visitor:read.
"""
from __future__ import annotations

import json
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.vms import get_form_fields, validate_custom_fields

router = APIRouter(prefix="/api/v1/vms", tags=["vms"])

_READ = Depends(require_permission("visitor:read"))
_CHECKIN = Depends(require_permission("visitor:checkin"))
_MANAGE = Depends(require_permission("visitor:manage"))

FIELD_TYPES = (
    "text", "textarea", "number", "date", "select",
    "multiselect", "checkbox", "phone", "email",
)
_OPTION_TYPES = {"select", "multiselect"}


def _json(value: Any) -> str:
    """asyncpg needs jsonb parameters as a string; the SQL casts it back."""
    return json.dumps(value)

_FIELD_COLUMNS = (
    "id, site_id, field_key, label, field_type, options, is_required, "
    "placeholder, help_text, sort_order, is_active, created_at"
)


class FormFieldCreate(BaseModel):
    field_key: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=255)
    field_type: str
    site_id: str | None = None
    options: list[Any] = Field(default_factory=list)
    is_required: bool = False
    placeholder: str | None = None
    help_text: str | None = None
    sort_order: int = 0


class FormFieldUpdate(BaseModel):
    label: str | None = None
    field_type: str | None = None
    options: list[Any] | None = None
    is_required: bool | None = None
    placeholder: str | None = None
    help_text: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class CompleteEntry(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    company: str | None = None
    id_number: str | None = None
    host_user_id: str | None = None
    host_name: str | None = None
    purpose: str | None = None
    visitor_email: str | None = None
    free_parking_minutes: int | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class ManualEntry(CompleteEntry):
    site_id: str
    vehicle_plate: str | None = None


def _validate_field_def(field_type: str, options: list[Any] | None) -> None:
    if field_type not in FIELD_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown field_type '{field_type}'; expected one of: {', '.join(FIELD_TYPES)}",
        )
    if field_type in _OPTION_TYPES and not options:
        # A dropdown with nothing to pick is a dead control on the operator's
        # screen — reject it at authoring time rather than shipping it.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"a '{field_type}' field needs at least one option",
        )


# ── Form field configuration ──────────────────────────────────────────────

@router.get("/form-fields", dependencies=[_READ])
async def list_form_fields(
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Fields that apply at this site — tenant-wide plus site-specific.
    Omitting site_id returns every definition, which is the admin view."""
    if site_id:
        return await get_form_fields(db, site_id)
    rows = (
        await db.execute(
            text(
                f"SELECT {_FIELD_COLUMNS} FROM visitor_form_fields "
                "ORDER BY (site_id IS NULL) DESC, sort_order, label"
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/form-fields", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_form_field(
    body: FormFieldCreate, db: AsyncSession = Depends(get_db_with_tenant)
):
    _validate_field_def(body.field_type, body.options)
    try:
        row = (
            await db.execute(
                text(
                    f"""
                    INSERT INTO visitor_form_fields (
                        tenant_id, site_id, field_key, label, field_type, options,
                        is_required, placeholder, help_text, sort_order
                    ) VALUES (
                        current_setting('app.current_tenant')::uuid,
                        CAST(:site_id AS uuid), :field_key, :label, :field_type,
                        CAST(:options AS jsonb), :is_required, :placeholder, :help_text, :sort_order
                    )
                    RETURNING {_FIELD_COLUMNS}
                    """
                ),
                {
                    **body.model_dump(exclude={"options"}),
                    "options": _json(body.options),
                },
            )
        ).mappings().first()
    except Exception as exc:
        if "uq_visitor_form_fields" in str(exc):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"field_key '{body.field_key}' already exists in this scope",
            ) from None
        raise
    await db.commit()
    return dict(row)


@router.put("/form-fields/{field_id}", dependencies=[_MANAGE])
async def update_form_field(
    field_id: str, body: FormFieldUpdate, db: AsyncSession = Depends(get_db_with_tenant)
):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no fields to update")

    current = (
        await db.execute(
            text("SELECT field_type, options FROM visitor_form_fields WHERE id = CAST(:id AS uuid)"),
            {"id": field_id},
        )
    ).mappings().first()
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form field not found")
    _validate_field_def(
        fields.get("field_type", current["field_type"]),
        fields.get("options", current["options"]),
    )

    if "options" in fields:
        fields["options"] = _json(fields["options"])
    sets = ", ".join(
        f"options = CAST(:options AS jsonb)" if k == "options" else f"{k} = :{k}" for k in fields
    )
    row = (
        await db.execute(
            text(
                f"UPDATE visitor_form_fields SET {sets}, updated_at = now() "
                f"WHERE id = CAST(:field_id AS uuid) RETURNING {_FIELD_COLUMNS}"
            ),
            {**fields, "field_id": field_id},
        )
    ).mappings().first()
    await db.commit()
    return dict(row)


@router.delete("/form-fields/{field_id}", dependencies=[_MANAGE])
async def deactivate_form_field(field_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    # Soft delete: historical visitors.custom_fields still reference this key,
    # and a hard delete would leave stored answers with no label to render.
    result = await db.execute(
        text("UPDATE visitor_form_fields SET is_active = FALSE, updated_at = now() "
             "WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": field_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form field not found")
    await db.commit()
    return {"id": field_id, "is_active": False}


# ── Visitor entries ───────────────────────────────────────────────────────

async def _apply_custom_fields(db: AsyncSession, site_id: str | None, values: dict) -> None:
    try:
        validate_custom_fields(await get_form_fields(db, site_id), values)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


@router.post("/entries/{visitor_id}/complete", dependencies=[_CHECKIN])
async def complete_entry(
    visitor_id: str,
    body: CompleteEntry,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Operator fills in the form that the entry-LPR prompt opened.

    The visitor row already exists (created at the moment the plate was read so
    the parking clock started) — this fills in who the vehicle actually is.
    """
    existing = (
        await db.execute(
            text("SELECT id, site_id, status FROM visitors WHERE id = CAST(:id AS uuid)"),
            {"id": visitor_id},
        )
    ).mappings().first()
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visitor entry not found")
    await _apply_custom_fields(db, str(existing["site_id"]) if existing["site_id"] else None,
                               body.custom_fields)

    row = (
        await db.execute(
            text(
                """
                UPDATE visitors SET
                    full_name = :full_name, company = :company, id_number = :id_number,
                    host_user_id = CAST(:host_user_id AS uuid), host_name = :host_name,
                    purpose = :purpose, visitor_email = :visitor_email,
                    free_parking_minutes = COALESCE(:free_parking_minutes, free_parking_minutes),
                    custom_fields = CAST(:custom_fields AS jsonb),
                    status = CASE WHEN status = 'pending' THEN 'arrived' ELSE status END,
                    updated_at = now()
                WHERE id = CAST(:visitor_id AS uuid)
                RETURNING id, full_name, company, vehicle_plate, status,
                          vehicle_entry_at, free_parking_minutes, custom_fields
                """
            ),
            {
                **body.model_dump(exclude={"custom_fields"}),
                "custom_fields": _json(body.custom_fields),
                "visitor_id": visitor_id,
            },
        )
    ).mappings().first()
    await db.execute(
        text(
            """
            INSERT INTO visitor_logs (tenant_id, visitor_id, site_id, guard_user_id,
                                      event_type, checkin_method, notes)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:vid AS uuid),
                    CAST(:site AS uuid), CAST(:uid AS uuid), 'arrival', 'manual',
                    'Details completed by operator')
            """
        ),
        {
            "vid": visitor_id,
            "site": str(existing["site_id"]) if existing["site_id"] else None,
            "uid": str(token.user_id),
        },
    )
    await db.commit()
    return dict(row)


@router.post("/entries/manual", status_code=status.HTTP_201_CREATED, dependencies=[_CHECKIN])
async def manual_entry(
    body: ManualEntry,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Add a visitor by hand — the path for a site with no entry LPR camera
    configured, which is the default state for every site."""
    await _apply_custom_fields(db, body.site_id, body.custom_fields)
    visitor_id = str(_uuid.uuid4())
    plate = body.vehicle_plate.strip().upper() if body.vehicle_plate else None
    row = (
        await db.execute(
            text(
                """
                INSERT INTO visitors (
                    id, tenant_id, site_id, full_name, company, id_number,
                    host_user_id, host_name, purpose, visitor_email, vehicle_plate,
                    status, qr_token, custom_fields, free_parking_minutes,
                    vehicle_entry_at, created_by_user_id
                ) VALUES (
                    CAST(:id AS uuid), current_setting('app.current_tenant')::uuid,
                    CAST(:site_id AS uuid), :full_name, :company, :id_number,
                    CAST(:host_user_id AS uuid), :host_name, :purpose, :visitor_email, :plate,
                    'arrived', :qr, CAST(:custom_fields AS jsonb), :free_parking_minutes,
                    -- The cast is load-bearing: a bare `:plate IS NULL` gives
                    -- the driver no type to infer from, and asyncpg rejects the
                    -- whole statement with AmbiguousParameterError.
                    -- The parking clock only starts if a vehicle was recorded;
                    -- a walk-in visitor has no plate and no parking to meter.
                    CASE WHEN CAST(:plate AS varchar) IS NULL THEN NULL ELSE now() END,
                    CAST(:uid AS uuid)
                )
                RETURNING id, full_name, company, vehicle_plate, status,
                          vehicle_entry_at, free_parking_minutes, custom_fields
                """
            ),
            {
                **body.model_dump(exclude={"custom_fields", "vehicle_plate"}),
                "id": visitor_id,
                "plate": plate,
                "qr": str(_uuid.uuid4()),
                "custom_fields": _json(body.custom_fields),
                "uid": str(token.user_id),
            },
        )
    ).mappings().first()
    await db.execute(
        text(
            """
            INSERT INTO visitor_logs (tenant_id, visitor_id, site_id, guard_user_id,
                                      event_type, checkin_method, is_unregistered, notes)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:vid AS uuid),
                    CAST(:site AS uuid), CAST(:uid AS uuid), 'arrival', 'manual', TRUE,
                    'Manual visitor entry')
            """
        ),
        {"vid": visitor_id, "site": body.site_id, "uid": str(token.user_id)},
    )
    await db.commit()
    return dict(row)


@router.get("/onsite", dependencies=[_READ])
async def vehicles_onsite(
    site_id: str | None = None,
    overstayed_only: bool = False,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Vehicles currently on site with their parking clock.

    `free_parking_minutes` resolves per-visit override first, then the site
    default; NULL at both levels means this site does not meter parking, and
    such a visit can never be flagged as overstayed.
    """
    clauses = ["v.vehicle_entry_at IS NOT NULL", "v.vehicle_exit_at IS NULL", "v.is_active = TRUE"]
    params: dict[str, Any] = {}
    if site_id:
        clauses.append("v.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if overstayed_only:
        clauses.append(
            "COALESCE(v.free_parking_minutes, s.free_parking_minutes) IS NOT NULL "
            "AND EXTRACT(EPOCH FROM (now() - v.vehicle_entry_at))/60 "
            "    > COALESCE(v.free_parking_minutes, s.free_parking_minutes)"
        )
    rows = (
        await db.execute(
            text(
                f"""
                SELECT v.id, v.full_name, v.company, v.vehicle_plate, v.status,
                       v.vehicle_entry_at, v.site_id, s.name AS site_name,
                       v.custom_fields,
                       COALESCE(v.free_parking_minutes, s.free_parking_minutes)
                           AS allowance_minutes,
                       FLOOR(EXTRACT(EPOCH FROM (now() - v.vehicle_entry_at))/60)::int
                           AS minutes_on_site,
                       (COALESCE(v.free_parking_minutes, s.free_parking_minutes) IS NOT NULL
                        AND EXTRACT(EPOCH FROM (now() - v.vehicle_entry_at))/60
                            > COALESCE(v.free_parking_minutes, s.free_parking_minutes))
                           AS is_overstayed
                FROM visitors v
                LEFT JOIN sites s ON s.id = v.site_id
                WHERE {' AND '.join(clauses)}
                ORDER BY v.vehicle_entry_at
                """
            ),
            params,
        )
    ).mappings().all()
    return [dict(r) for r in rows]
