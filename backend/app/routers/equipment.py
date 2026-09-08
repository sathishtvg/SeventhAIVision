"""The equipment and uniform register — company kit, and who has it.

Radios, torches, batons and body cameras are issued to a named officer and
expected back. Today that is a signature in a book, and the shortfall is
discovered at stock-take rather than at the handover where somebody could still
do something about it.

MODELLED LIKE THE KEY REGISTER, deliberately. An item is out because an
assignment row has no returned_at, and a partial unique index makes it
impossible for one radio to be in two officers' hands. The useful question is
"who has the radio that is missing", and that shape answers it directly.

UNIFORMS ARE A SEPARATE THING. A radio is one object issued many times; a
uniform is two shirts in a size, issued once and mostly never returned. So
uniforms are quantity rows with a partial-return count, because a guard who
resigns hands back three of four shirts and a boolean would force somebody to
pick a lie. Deposits live here too, since the deposit is what an agency
actually withholds against the kit.

WHAT AN OFFICER IS HOLDING is one question across both, so /assigned/{user_id}
answers it in one call. That is the screen a supervisor opens on somebody's
last day.

Permissions:
  equipment:read   — see the register (all ops roles + viewer)
  equipment:issue  — issue and receive (supervisors, managers, control room)
  equipment:manage — maintain the inventory (supervisors and up)
Site scoping (Gap 81) applies to the item list.
"""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/equipment", tags=["guard-ops"])

VALID_CATEGORIES = {"radio", "torch", "baton", "handcuffs", "bodycam", "ppe",
                    "phone", "vehicle", "metal_detector", "first_aid", "other"}
VALID_CONDITIONS = {"new", "good", "fair", "poor", "damaged", "lost"}
VALID_UNIFORM_TYPES = {"shirt", "trousers", "jacket", "beret", "cap", "belt",
                       "shoes", "epaulette", "name_tag", "tie", "raincoat", "other"}


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    category: str = "other"
    site_id: str | None = None
    serial_number: str | None = Field(default=None, max_length=120)
    condition: str = "good"
    notes: str | None = None


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_code: str | None = Field(default=None, min_length=1, max_length=60)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = None
    site_id: str | None = None
    serial_number: str | None = Field(default=None, max_length=120)
    condition: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class Assign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    assigned_to_user_id: str
    expected_return_at: datetime | None = None
    purpose: str | None = None


class Receive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition_on_return: str | None = None
    return_notes: str | None = None


class UniformIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    item_type: str
    size: str | None = Field(default=None, max_length=20)
    quantity: int = Field(default=1, ge=1, le=100)
    deposit_amount: Decimal | None = None
    notes: str | None = None


class UniformReturn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # How many came back. Partial on purpose — three of four shirts is the
    # normal case on a resignation.
    returned_quantity: int = Field(ge=0, le=100)
    notes: str | None = None


_ITEM_SELECT = """
    SELECT e.id, e.site_id, e.asset_code, e.name, e.category, e.serial_number,
           e.condition, e.notes, e.is_active, e.created_at, e.updated_at,
           s.name AS site_name,
           a.id AS open_assignment_id,
           a.issued_at, a.expected_return_at, a.purpose,
           a.assigned_to_user_id,
           hu.full_name AS held_by_name,
           hu.employee_code AS held_by_employee_code,
           iu.full_name AS issued_by_name,
           (a.id IS NOT NULL) AS is_out,
           (a.id IS NOT NULL AND a.expected_return_at IS NOT NULL
            AND a.expected_return_at < now()) AS is_overdue
      FROM equipment_items e
 LEFT JOIN sites s ON s.id = e.site_id
 LEFT JOIN equipment_assignments a ON a.item_id = e.id AND a.returned_at IS NULL
 LEFT JOIN users hu ON hu.id = a.assigned_to_user_id
 LEFT JOIN users iu ON iu.id = a.issued_by_user_id
"""


def _check_enum(value: str, allowed: set[str], field: str) -> None:
    if value not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{field} must be one of {sorted(allowed)}")


# ── Items ────────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_permission("equipment:read"))])
async def list_items(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    category: str | None = None,
    out_only: bool = False,
    include_inactive: bool = False,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """The inventory, issued items first."""
    where, params = [], {}
    if not include_inactive:
        where.append("e.is_active = TRUE")
    if category:
        _check_enum(category, VALID_CATEGORIES, "category")
        where.append("e.category = :cat"); params["cat"] = category
    if site_id:
        where.append("e.site_id = CAST(:site_id AS uuid)"); params["site_id"] = site_id
    if out_only:
        where.append("a.id IS NOT NULL")
    scope = site_scope_clause(allowed_sites, "e.site_id", params)
    if scope:
        # Pool kit belongs to no site and stays visible; see the migration.
        where.append(f"(e.site_id IS NULL OR {scope})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            {_ITEM_SELECT}
            {clause}
            ORDER BY (a.id IS NOT NULL) DESC, a.expected_return_at NULLS LAST,
                     e.category, e.asset_code
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


@router.get("/outstanding", dependencies=[Depends(require_permission("equipment:read"))])
async def outstanding(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Everything currently in somebody's hands, overdue first.

    Declared before /{item_id} — FastAPI matches in declaration order.
    """
    where, params = ["a.returned_at IS NULL"], {}
    if site_id:
        where.append("e.site_id = CAST(:site_id AS uuid)"); params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "e.site_id", params)
    if scope:
        where.append(f"(e.site_id IS NULL OR {scope})")

    result = await db.execute(
        text(f"""
            SELECT a.id, a.item_id, a.issued_at, a.expected_return_at, a.purpose,
                   e.asset_code, e.name, e.category, e.site_id,
                   s.name AS site_name,
                   a.assigned_to_user_id,
                   hu.full_name AS held_by_name,
                   hu.employee_code AS held_by_employee_code,
                   iu.full_name AS issued_by_name,
                   (a.expected_return_at IS NOT NULL AND a.expected_return_at < now())
                       AS is_overdue,
                   EXTRACT(EPOCH FROM (now() - a.issued_at)) / 3600.0 AS hours_out
              FROM equipment_assignments a
              JOIN equipment_items e ON e.id = a.item_id
         LEFT JOIN sites s ON s.id = e.site_id
         LEFT JOIN users hu ON hu.id = a.assigned_to_user_id
         LEFT JOIN users iu ON iu.id = a.issued_by_user_id
             WHERE {' AND '.join(where)}
          ORDER BY (a.expected_return_at IS NOT NULL AND a.expected_return_at < now()) DESC,
                   a.expected_return_at NULLS LAST, a.issued_at
        """),
        params,
    )
    rows = [dict(r) for r in result.mappings()]
    return {
        "out": len(rows),
        "overdue": sum(1 for r in rows if r["is_overdue"]),
        "items": rows,
    }


@router.get("/assigned/{user_id}", dependencies=[Depends(require_permission("equipment:read"))])
async def assigned_to_user(user_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Everything one officer is holding — kit and uniform in one answer.

    This is the screen a supervisor opens on somebody's last day, and having to
    check two registers is how a body camera walks out of the building.
    """
    person = (await db.execute(
        text("SELECT id, full_name, email, employee_code FROM users "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": user_id},
    )).mappings().first()
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    kit = (await db.execute(
        text("""
            SELECT a.id, a.item_id, a.issued_at, a.expected_return_at, a.purpose,
                   e.asset_code, e.name, e.category, e.serial_number,
                   (a.expected_return_at IS NOT NULL AND a.expected_return_at < now())
                       AS is_overdue
              FROM equipment_assignments a
              JOIN equipment_items e ON e.id = a.item_id
             WHERE a.assigned_to_user_id = CAST(:uid AS uuid) AND a.returned_at IS NULL
          ORDER BY a.issued_at
        """),
        {"uid": user_id},
    )).mappings().all()

    uniform = (await db.execute(
        text("""
            SELECT u.id, u.item_type, u.size, u.quantity, u.returned_quantity,
                   (u.quantity - u.returned_quantity) AS outstanding_quantity,
                   u.issued_at, u.deposit_amount, u.notes
              FROM uniform_issues u
             WHERE u.user_id = CAST(:uid AS uuid) AND u.returned_quantity < u.quantity
          ORDER BY u.issued_at
        """),
        {"uid": user_id},
    )).mappings().all()

    deposit = sum(
        Decimal(str(r["deposit_amount"] or 0)) for r in uniform
    )
    return {
        "user": dict(person),
        "equipment": [dict(r) for r in kit],
        "uniform": [dict(r) for r in uniform],
        "summary": {
            "equipment_out": len(kit),
            "equipment_overdue": sum(1 for r in kit if r["is_overdue"]),
            "uniform_pieces_out": sum(int(r["outstanding_quantity"]) for r in uniform),
            "deposit_held": deposit,
        },
    }


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("equipment:manage"))])
async def create_item(
    body: ItemCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    _check_enum(body.category, VALID_CATEGORIES, "category")
    _check_enum(body.condition, VALID_CONDITIONS, "condition")
    if body.site_id is not None:
        if not is_site_allowed(allowed_sites, body.site_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
        if (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:id AS uuid)"),
                             {"id": body.site_id})).first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    try:
        result = await db.execute(
            text("""
                INSERT INTO equipment_items
                    (tenant_id, site_id, asset_code, name, category,
                     serial_number, condition, notes)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:site AS uuid), :code, :name, :cat, :serial, :cond, :notes)
                RETURNING id, site_id, asset_code, name, category, serial_number,
                          condition, notes, is_active, created_at
            """),
            {"site": body.site_id, "code": body.asset_code, "name": body.name,
             "cat": body.category, "serial": body.serial_number,
             "cond": body.condition, "notes": body.notes},
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Asset code {body.asset_code} is already in use") from exc
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/issue", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("equipment:issue"))])
async def issue_item(
    body: Assign,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Sign a piece of kit out to an officer. Fails if it is already out."""
    if body.expected_return_at is not None:
        due = body.expected_return_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due < datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "expected_return_at is in the past")

    item = (await db.execute(
        text("SELECT id, site_id, is_active, asset_code, name, condition "
             "FROM equipment_items WHERE id = CAST(:id AS uuid)"),
        {"id": body.item_id},
    )).first()
    if item is None or (item.site_id is not None
                        and not is_site_allowed(allowed_sites, item.site_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    if not item.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{item.asset_code} has been retired from the inventory")
    # Issuing kit already written off as lost or damaged is almost always a
    # mistaken asset code rather than an intention.
    if item.condition in ("lost", "damaged"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{item.asset_code} is marked {item.condition} — fix or write it off first",
        )

    if (await db.execute(text("SELECT 1 FROM users WHERE id = CAST(:id AS uuid)"),
                         {"id": body.assigned_to_user_id})).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")

    try:
        result = await db.execute(
            text("""
                INSERT INTO equipment_assignments
                    (tenant_id, item_id, assigned_to_user_id, issued_by_user_id,
                     expected_return_at, purpose)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:item AS uuid), CAST(:to_uid AS uuid),
                        CAST(:by AS uuid), :due, :purpose)
                RETURNING id, item_id, assigned_to_user_id, issued_at,
                          expected_return_at, purpose
            """),
            {"item": body.item_id, "to_uid": body.assigned_to_user_id,
             "by": token.user_id, "due": body.expected_return_at,
             "purpose": body.purpose},
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{item.asset_code} is already issued — it has to come back first",
        ) from exc
    row = dict(result.mappings().first())
    await db.commit()
    return {**row, "asset_code": item.asset_code, "name": item.name}


@router.post("/assignments/{assignment_id}/receive",
             dependencies=[Depends(require_permission("equipment:issue"))])
async def receive_item(
    assignment_id: str,
    body: Receive,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take kit back, and record the state it came back in.

    The condition on return writes through to the item, because the next person
    to be issued it should not have to open the assignment history to find out
    the torch is broken.
    """
    if body.condition_on_return is not None:
        _check_enum(body.condition_on_return, VALID_CONDITIONS, "condition_on_return")

    row = (await db.execute(
        text("""
            SELECT a.id, a.returned_at, a.item_id, e.site_id, e.asset_code
              FROM equipment_assignments a
              JOIN equipment_items e ON e.id = a.item_id
             WHERE a.id = CAST(:id AS uuid)
        """),
        {"id": assignment_id},
    )).first()
    if row is None or (row.site_id is not None
                       and not is_site_allowed(allowed_sites, row.site_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    if row.returned_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{row.asset_code} was already returned")

    result = await db.execute(
        text("""
            UPDATE equipment_assignments
               SET returned_at = now(),
                   received_by_user_id = CAST(:uid AS uuid),
                   condition_on_return = :cond,
                   return_notes = :notes
             WHERE id = CAST(:id AS uuid)
            RETURNING id, item_id, issued_at, returned_at, condition_on_return,
                      return_notes, expected_return_at,
                      (expected_return_at IS NOT NULL AND returned_at > expected_return_at)
                          AS returned_late
        """),
        {"id": assignment_id, "uid": token.user_id,
         "cond": body.condition_on_return, "notes": body.return_notes},
    )
    updated = dict(result.mappings().first())

    if body.condition_on_return is not None:
        await db.execute(
            text("UPDATE equipment_items SET condition = :cond, updated_at = now() "
                 "WHERE id = CAST(:id AS uuid)"),
            {"cond": body.condition_on_return, "id": str(row.item_id)},
        )
    await db.commit()
    return {**updated, "asset_code": row.asset_code}


# ── Uniforms ─────────────────────────────────────────────────────────────────

@router.get("/uniforms", dependencies=[Depends(require_permission("equipment:read"))])
async def list_uniform_issues(
    db: AsyncSession = Depends(get_db_with_tenant),
    user_id: str | None = None,
    outstanding_only: bool = False,
):
    where, params = [], {}
    if user_id:
        where.append("u.user_id = CAST(:uid AS uuid)"); params["uid"] = user_id
    if outstanding_only:
        where.append("u.returned_quantity < u.quantity")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            SELECT u.id, u.user_id, u.item_type, u.size, u.quantity,
                   u.returned_quantity,
                   (u.quantity - u.returned_quantity) AS outstanding_quantity,
                   u.issued_at, u.returned_at, u.deposit_amount, u.notes,
                   p.full_name, p.employee_code,
                   iu.full_name AS issued_by_name
              FROM uniform_issues u
              JOIN users p ON p.id = u.user_id
         LEFT JOIN users iu ON iu.id = u.issued_by_user_id
            {clause}
          ORDER BY u.issued_at DESC
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


@router.post("/uniforms", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("equipment:issue"))])
async def issue_uniform(
    body: UniformIssue,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    _check_enum(body.item_type, VALID_UNIFORM_TYPES, "item_type")
    if body.deposit_amount is not None and body.deposit_amount < 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "deposit_amount cannot be negative")
    if (await db.execute(text("SELECT 1 FROM users WHERE id = CAST(:id AS uuid)"),
                         {"id": body.user_id})).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")

    result = await db.execute(
        text("""
            INSERT INTO uniform_issues
                (tenant_id, user_id, item_type, size, quantity,
                 issued_by_user_id, deposit_amount, notes)
            VALUES (current_setting('app.current_tenant')::uuid,
                    CAST(:uid AS uuid), :type, :size, :qty,
                    CAST(:by AS uuid), :deposit, :notes)
            RETURNING id, user_id, item_type, size, quantity, returned_quantity,
                      issued_at, deposit_amount, notes
        """),
        {"uid": body.user_id, "type": body.item_type, "size": body.size,
         "qty": body.quantity, "by": token.user_id,
         "deposit": body.deposit_amount, "notes": body.notes},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/uniforms/{issue_id}/return",
             dependencies=[Depends(require_permission("equipment:issue"))])
async def return_uniform(
    issue_id: str,
    body: UniformReturn,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Record how many pieces came back.

    The count is absolute rather than incremental — "three of the four are
    back" is what somebody counting a pile can state truthfully, and an
    incremental API turns a double-submit into a wrong number.
    """
    row = (await db.execute(
        text("SELECT id, quantity, returned_quantity FROM uniform_issues "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": issue_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Uniform issue not found")
    if body.returned_quantity > row.quantity:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Only {row.quantity} were issued — cannot receive {body.returned_quantity} back",
        )

    result = await db.execute(
        text("""
            UPDATE uniform_issues
               SET returned_quantity = :qty,
                   returned_at = CASE WHEN :qty >= quantity THEN now() ELSE NULL END,
                   notes = COALESCE(:notes, notes),
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING id, user_id, item_type, size, quantity, returned_quantity,
                      (quantity - returned_quantity) AS outstanding_quantity,
                      returned_at
        """),
        {"id": issue_id, "qty": body.returned_quantity, "notes": body.notes},
    )
    updated = dict(result.mappings().first())
    await db.commit()
    return updated


# ── Single item, declared last ───────────────────────────────────────────────

@router.get("/{item_id}", dependencies=[Depends(require_permission("equipment:read"))])
async def get_item(
    item_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    row = (await db.execute(
        text(f"{_ITEM_SELECT} WHERE e.id = CAST(:id AS uuid)"),
        {"id": item_id},
    )).mappings().first()
    if row is None or (row["site_id"] is not None
                       and not is_site_allowed(allowed_sites, row["site_id"])):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")

    history = (await db.execute(
        text("""
            SELECT a.id, a.issued_at, a.expected_return_at, a.returned_at,
                   a.condition_on_return, a.return_notes, a.purpose,
                   hu.full_name AS held_by_name,
                   iu.full_name AS issued_by_name,
                   ru.full_name AS received_by_name
              FROM equipment_assignments a
         LEFT JOIN users hu ON hu.id = a.assigned_to_user_id
         LEFT JOIN users iu ON iu.id = a.issued_by_user_id
         LEFT JOIN users ru ON ru.id = a.received_by_user_id
             WHERE a.item_id = CAST(:id AS uuid)
          ORDER BY a.issued_at DESC
             LIMIT 50
        """),
        {"id": item_id},
    )).mappings().all()
    return {**dict(row), "history": [dict(r) for r in history]}


@router.put("/{item_id}", dependencies=[Depends(require_permission("equipment:manage"))])
async def update_item(
    item_id: str,
    body: ItemUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    existing = (await db.execute(
        text("SELECT site_id FROM equipment_items WHERE id = CAST(:id AS uuid)"),
        {"id": item_id},
    )).first()
    if existing is None or (existing.site_id is not None
                            and not is_site_allowed(allowed_sites, existing.site_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    if body.category is not None:
        _check_enum(body.category, VALID_CATEGORIES, "category")
    if body.condition is not None:
        _check_enum(body.condition, VALID_CONDITIONS, "condition")

    sets, params = [], {"id": item_id}
    if body.asset_code is not None:
        sets.append("asset_code = :code"); params["code"] = body.asset_code
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.category is not None:
        sets.append("category = :cat"); params["cat"] = body.category
    if body.site_id is not None:
        sets.append("site_id = CAST(:site AS uuid)"); params["site"] = body.site_id
    if body.serial_number is not None:
        sets.append("serial_number = :serial"); params["serial"] = body.serial_number
    if body.condition is not None:
        sets.append("condition = :cond"); params["cond"] = body.condition
    if body.notes is not None:
        sets.append("notes = :notes"); params["notes"] = body.notes
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")

    # Retiring kit somebody is holding would erase the only record that it has
    # to come back — same rule as the key cabinet.
    if body.is_active is False:
        out = (await db.execute(
            text("SELECT 1 FROM equipment_assignments "
                 "WHERE item_id = CAST(:id AS uuid) AND returned_at IS NULL"),
            {"id": item_id},
        )).first()
        if out is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This is currently issued — receive it back before retiring it",
            )

    sets.append("updated_at = now()")
    try:
        result = await db.execute(
            text(f"""
                UPDATE equipment_items SET {', '.join(sets)}
                 WHERE id = CAST(:id AS uuid)
                RETURNING id, site_id, asset_code, name, category, serial_number,
                          condition, notes, is_active, updated_at
            """),
            params,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Another item already uses that asset code") from exc
    row = dict(result.mappings().first())
    await db.commit()
    return row
