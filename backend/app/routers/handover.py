"""Handovers as a two-signature record, and the checklists behind them.

Creating a handover still lives on the shift (POST /shifts/{id}/handover) —
that is where the outgoing guard is and where all the numbers are gathered.
Everything that happens afterwards lives here: ticking the checklist, the
incoming guard accepting or disputing it, a supervisor closing out a dispute,
and maintaining the per-site checklists themselves.

WHY DISPUTE IS A FIRST-CLASS STATE. The incoming guard counted eleven keys
against a handover claiming twelve. If the only options are "accept" and "do
nothing", that discrepancy is either signed for by someone who does not believe
it or lost entirely. Disputed is the state a supervisor has to clear, and it is
the reason the whole feature is worth having.

Permissions:
  handover:read   — see handovers and templates
  handover:create — raise one (already granted; unchanged here)
  handover:accept — tick the checklist, accept or dispute; reaches guards,
                    because accepting is inherently the incoming guard's act
  handover:manage — maintain checklists, resolve disputes
Site scoping (Gap 81) applies through the handover's shift.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services.handover import COUNT_SOURCES

router = APIRouter(prefix="/api/v1/handovers", tags=["guard-ops"])

OPEN_STATUSES = ("submitted", "disputed")


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    # None means the tenant-wide fallback, used by sites with no template.
    site_id: str | None = None


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=160)
    is_active: bool | None = None


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=200)
    requires_count: bool = False
    expected_source: str | None = None
    is_required: bool = True
    sort_order: int = 0


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, min_length=1, max_length=200)
    requires_count: bool | None = None
    expected_source: str | None = None
    is_required: bool | None = None
    sort_order: int | None = None


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    checked: bool = True
    counted_value: int | None = Field(default=None, ge=0, le=100000)
    notes: str | None = None


class CheckUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[CheckResult] = Field(min_length=1)


class Accept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incoming_notes: str | None = None


class Dispute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dispute_reason: str = Field(min_length=1)


class ResolveDispute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution_notes: str | None = None


_HANDOVER_SELECT = """
    SELECT h.id, h.shift_id, h.status, h.created_at,
           h.open_incidents_count, h.open_alerts_count,
           h.patrol_routes_completed, h.patrol_routes_total,
           h.checkpoints_scanned, h.checkpoints_total,
           h.keys_outstanding, h.keys_overdue, h.lost_found_held,
           h.open_defects_count, h.equipment_out_count,
           h.outgoing_notes, h.incoming_notes, h.dispute_reason,
           h.accepted_at, h.resolved_at,
           sh.site_id, s.name AS site_name,
           sh.scheduled_start, sh.scheduled_end,
           og.full_name AS outgoing_guard_name,
           ig.full_name AS incoming_guard_name,
           au.full_name AS accepted_by_name,
           ru.full_name AS resolved_by_name,
           h.incoming_guard_id
      FROM shift_handovers h
      JOIN shifts sh ON sh.id = h.shift_id
 LEFT JOIN sites s ON s.id = sh.site_id
 LEFT JOIN users og ON og.id = h.outgoing_guard_id
 LEFT JOIN users ig ON ig.id = h.incoming_guard_id
 LEFT JOIN users au ON au.id = h.accepted_by_user_id
 LEFT JOIN users ru ON ru.id = h.resolved_by_user_id
"""


async def _load(db: AsyncSession, handover_id: str, allowed_sites: list[str] | None):
    row = (await db.execute(
        text("""
            SELECT h.id, h.status, h.incoming_guard_id, sh.site_id
              FROM shift_handovers h
              JOIN shifts sh ON sh.id = h.shift_id
             WHERE h.id = CAST(:id AS uuid)
        """),
        {"id": handover_id},
    )).first()
    if row is None or (row.site_id is not None
                       and not is_site_allowed(allowed_sites, row.site_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Handover not found")
    return row


async def _checks_for(db: AsyncSession, handover_id: str) -> list[dict]:
    rows = (await db.execute(
        text("""
            SELECT id, item_id, label, requires_count, is_required, sort_order,
                   checked, counted_value, expected_value, notes
              FROM handover_checks
             WHERE handover_id = CAST(:id AS uuid)
          ORDER BY sort_order, label
        """),
        {"id": handover_id},
    )).mappings().all()
    out = []
    for r in rows:
        row = dict(r)
        # The whole reason counts exist: a number that does not match what the
        # system believed. Computed rather than stored, so it stays true if a
        # count is corrected before acceptance.
        row["mismatch"] = (
            row["counted_value"] is not None
            and row["expected_value"] is not None
            and row["counted_value"] != row["expected_value"]
        )
        out.append(row)
    return out


# ── Templates ────────────────────────────────────────────────────────────────

@router.get("/templates", dependencies=[Depends(require_permission("handover:read"))])
async def list_templates(
    db: AsyncSession = Depends(get_db_with_tenant),
    include_inactive: bool = False,
):
    """Every checklist, with its items. Small enough to return whole — a
    company has one default and a handful of site exceptions, not hundreds."""
    where = "" if include_inactive else "WHERE t.is_active"
    templates = (await db.execute(
        text(f"""
            SELECT t.id, t.site_id, t.name, t.is_active, t.created_at, t.updated_at,
                   s.name AS site_name
              FROM handover_checklist_templates t
         LEFT JOIN sites s ON s.id = t.site_id
            {where}
          ORDER BY (t.site_id IS NULL) DESC, s.name NULLS FIRST, t.name
        """),
    )).mappings().all()

    items = (await db.execute(
        text("""
            SELECT id, template_id, label, requires_count, expected_source,
                   is_required, sort_order
              FROM handover_checklist_items
          ORDER BY sort_order, label
        """),
    )).mappings().all()

    by_template: dict[str, list[dict]] = {}
    for item in items:
        by_template.setdefault(str(item["template_id"]), []).append(dict(item))

    return [
        {**dict(t), "items": by_template.get(str(t["id"]), [])}
        for t in templates
    ]


@router.post("/templates", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("handover:manage"))])
async def create_template(
    body: TemplateCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    if body.site_id is not None:
        if not is_site_allowed(allowed_sites, body.site_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
        if (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:id AS uuid)"),
                             {"id": body.site_id})).first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    try:
        result = await db.execute(
            text("""
                INSERT INTO handover_checklist_templates (tenant_id, site_id, name)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:site AS uuid), :name)
                RETURNING id, site_id, name, is_active, created_at
            """),
            {"site": body.site_id, "name": body.name},
        )
    except IntegrityError as exc:
        # One active checklist per site, and one tenant-wide fallback. Two
        # would mean the handover picks whichever it read first.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An active checklist already exists here — edit it, or retire it first",
        ) from exc
    row = dict(result.mappings().first())
    await db.commit()
    return {**row, "items": []}


@router.put("/templates/{template_id}",
            dependencies=[Depends(require_permission("handover:manage"))])
async def update_template(
    template_id: str,
    body: TemplateUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    sets, params = [], {"id": template_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")
    sets.append("updated_at = now()")

    try:
        result = await db.execute(
            text(f"""
                UPDATE handover_checklist_templates SET {', '.join(sets)}
                 WHERE id = CAST(:id AS uuid)
                RETURNING id, site_id, name, is_active, updated_at
            """),
            params,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "An active checklist already exists here") from exc
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checklist not found")
    await db.commit()
    return dict(row)


@router.post("/templates/{template_id}/items", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("handover:manage"))])
async def add_item(
    template_id: str,
    body: ItemCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if body.expected_source is not None and body.expected_source not in COUNT_SOURCES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"expected_source must be one of {sorted(COUNT_SOURCES)}")
    # Measuring against a register means producing a number to compare.
    if body.expected_source is not None and not body.requires_count:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "An item measured against a register has to ask for a count",
        )
    if (await db.execute(
        text("SELECT 1 FROM handover_checklist_templates WHERE id = CAST(:id AS uuid)"),
        {"id": template_id},
    )).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checklist not found")

    result = await db.execute(
        text("""
            INSERT INTO handover_checklist_items
                (tenant_id, template_id, label, requires_count, expected_source,
                 is_required, sort_order)
            VALUES (current_setting('app.current_tenant')::uuid,
                    CAST(:tid AS uuid), :label, :needs_count, :source, :required, :sort)
            RETURNING id, template_id, label, requires_count, expected_source,
                      is_required, sort_order
        """),
        {"tid": template_id, "label": body.label, "needs_count": body.requires_count,
         "source": body.expected_source, "required": body.is_required,
         "sort": body.sort_order},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.put("/items/{item_id}", dependencies=[Depends(require_permission("handover:manage"))])
async def update_item(
    item_id: str,
    body: ItemUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    existing = (await db.execute(
        text("SELECT requires_count FROM handover_checklist_items "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": item_id},
    )).first()
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checklist item not found")

    if body.expected_source is not None and body.expected_source not in COUNT_SOURCES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"expected_source must be one of {sorted(COUNT_SOURCES)}")
    needs_count = body.requires_count if body.requires_count is not None else existing.requires_count
    if body.expected_source is not None and not needs_count:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "An item measured against a register has to ask for a count",
        )

    sets, params = [], {"id": item_id}
    if body.label is not None:
        sets.append("label = :label"); params["label"] = body.label
    if body.requires_count is not None:
        sets.append("requires_count = :needs_count"); params["needs_count"] = body.requires_count
    if body.expected_source is not None:
        sets.append("expected_source = :source"); params["source"] = body.expected_source
    if body.is_required is not None:
        sets.append("is_required = :required"); params["required"] = body.is_required
    if body.sort_order is not None:
        sets.append("sort_order = :sort"); params["sort"] = body.sort_order
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")

    result = await db.execute(
        text(f"""
            UPDATE handover_checklist_items SET {', '.join(sets)}
             WHERE id = CAST(:id AS uuid)
            RETURNING id, template_id, label, requires_count, expected_source,
                      is_required, sort_order
        """),
        params,
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_permission("handover:manage"))])
async def delete_item(item_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Remove an item from future handovers.

    Past handovers keep theirs: handover_checks holds its own copy of the label
    and the FK is ON DELETE SET NULL, so deleting a template item cannot delete
    the evidence that it was once checked.
    """
    result = await db.execute(
        text("DELETE FROM handover_checklist_items WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": item_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checklist item not found")
    await db.commit()


# ── Handovers ────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_permission("handover:read"))])
async def list_handovers(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    status_filter: str | None = None,
    open_only: bool = False,
    limit: int = 100,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Anything awaiting acceptance or in dispute sorts first — those are the
    only two states somebody has to act on."""
    limit = max(1, min(limit, 500))
    where, params = [], {"lim": limit}

    if status_filter:
        if status_filter not in ("submitted", "accepted", "disputed", "resolved"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown status")
        where.append("h.status = :st"); params["st"] = status_filter
    if open_only:
        where.append("h.status = ANY(:open)"); params["open"] = list(OPEN_STATUSES)
    if site_id:
        where.append("sh.site_id = CAST(:site_id AS uuid)"); params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "sh.site_id", params)
    if scope:
        where.append(scope)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            {_HANDOVER_SELECT}
            {clause}
            ORDER BY (h.status = ANY(ARRAY['submitted','disputed'])) DESC,
                     h.created_at DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


@router.get("/{handover_id}", dependencies=[Depends(require_permission("handover:read"))])
async def get_handover(
    handover_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    row = (await db.execute(
        text(f"{_HANDOVER_SELECT} WHERE h.id = CAST(:id AS uuid)"),
        {"id": handover_id},
    )).mappings().first()
    if row is None or (row["site_id"] is not None
                       and not is_site_allowed(allowed_sites, row["site_id"])):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Handover not found")

    checks = await _checks_for(db, handover_id)
    return {
        **dict(row),
        "checks": checks,
        "mismatches": sum(1 for c in checks if c["mismatch"]),
        "unchecked_required": sum(
            1 for c in checks if c["is_required"] and not c["checked"]
        ),
    }


@router.put("/{handover_id}/checks",
            dependencies=[Depends(require_permission("handover:accept"))])
async def update_checks(
    handover_id: str,
    body: CheckUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Tick the checklist off. Only while the handover is still open — once it
    is accepted, the ticks are what somebody signed for."""
    existing = await _load(db, handover_id, allowed_sites)
    if existing.status not in OPEN_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This handover is {existing.status} — its checks are final")

    updated = 0
    for check in body.checks:
        result = await db.execute(
            text("""
                UPDATE handover_checks
                   SET checked = :checked,
                       counted_value = :counted,
                       notes = COALESCE(:notes, notes),
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid) AND handover_id = CAST(:hid AS uuid)
                RETURNING id
            """),
            {"id": check.id, "hid": handover_id, "checked": check.checked,
             "counted": check.counted_value, "notes": check.notes},
        )
        if result.first() is None:
            await db.rollback()
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                f"Check {check.id} is not on this handover")
        updated += 1

    # Read before committing, not after. get_db_with_tenant sets
    # app.current_tenant with SET LOCAL, which dies with the transaction — a
    # query issued after the commit runs with an empty GUC and the RLS policy's
    # ''::uuid cast fails. The updates above are already visible here.
    checks = await _checks_for(db, handover_id)
    await db.commit()
    return {
        "updated": updated,
        "checks": checks,
        "mismatches": sum(1 for c in checks if c["mismatch"]),
        "unchecked_required": sum(
            1 for c in checks if c["is_required"] and not c["checked"]
        ),
    }


@router.post("/{handover_id}/accept",
             dependencies=[Depends(require_permission("handover:accept"))])
async def accept_handover(
    handover_id: str,
    body: Accept,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """The incoming guard takes responsibility for what the outgoing guard is
    walking away from.

    A disputed handover can still be accepted — the incoming guard raised a
    discrepancy, the supervisor sorted it out on the phone, and the shift has to
    start. Accepting after a dispute keeps the dispute reason on the record.
    """
    existing = await _load(db, handover_id, allowed_sites)
    if existing.status == "accepted":
        raise HTTPException(status.HTTP_409_CONFLICT, "This handover was already accepted")

    unchecked = (await db.execute(
        text("""
            SELECT COUNT(*)::int FROM handover_checks
             WHERE handover_id = CAST(:id AS uuid) AND is_required AND NOT checked
        """),
        {"id": handover_id},
    )).scalar()
    if unchecked:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{unchecked} required {'check is' if unchecked == 1 else 'checks are'} "
            "still outstanding — dispute it instead if they cannot be completed",
        )

    result = await db.execute(
        text("""
            UPDATE shift_handovers
               SET status = 'accepted',
                   accepted_at = now(),
                   accepted_by_user_id = CAST(:uid AS uuid),
                   incoming_guard_id = COALESCE(incoming_guard_id, CAST(:uid AS uuid)),
                   incoming_notes = COALESCE(:notes, incoming_notes)
             WHERE id = CAST(:id AS uuid)
            RETURNING id, shift_id, status, accepted_at, incoming_notes, dispute_reason
        """),
        {"id": handover_id, "uid": token.user_id, "notes": body.incoming_notes},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/{handover_id}/dispute",
             dependencies=[Depends(require_permission("handover:accept"))])
async def dispute_handover(
    handover_id: str,
    body: Dispute,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """The incoming guard does not agree with what they are being handed.

    Eleven keys against a handover claiming twelve. Without this the guard
    either signs for something they do not believe or the discrepancy is lost,
    and both are worse than a row a supervisor has to clear.
    """
    existing = await _load(db, handover_id, allowed_sites)
    if existing.status == "accepted":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This handover was already accepted — raise an occurrence instead",
        )

    result = await db.execute(
        text("""
            UPDATE shift_handovers
               SET status = 'disputed',
                   dispute_reason = :reason,
                   incoming_guard_id = COALESCE(incoming_guard_id, CAST(:uid AS uuid))
             WHERE id = CAST(:id AS uuid)
            RETURNING id, shift_id, status, dispute_reason
        """),
        {"id": handover_id, "uid": token.user_id, "reason": body.dispute_reason},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/{handover_id}/resolve",
             dependencies=[Depends(require_permission("handover:manage"))])
async def resolve_dispute(
    handover_id: str,
    body: ResolveDispute,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """A supervisor closes out a disputed handover.

    Resolved rather than accepted, because the two are different facts: the
    incoming guard accepting is one thing, and a supervisor deciding what
    happened about a discrepancy is another. Collapsing them would lose which
    of the two occurred.
    """
    existing = await _load(db, handover_id, allowed_sites)
    if existing.status != "disputed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only a disputed handover can be resolved — this one is {existing.status}",
        )

    result = await db.execute(
        text("""
            UPDATE shift_handovers
               SET status = 'resolved',
                   resolved_at = now(),
                   resolved_by_user_id = CAST(:uid AS uuid),
                   incoming_notes = COALESCE(:notes, incoming_notes)
             WHERE id = CAST(:id AS uuid)
            RETURNING id, shift_id, status, resolved_at, dispute_reason, incoming_notes
        """),
        {"id": handover_id, "uid": token.user_id, "notes": body.resolution_notes},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row
