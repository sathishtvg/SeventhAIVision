"""The key register — what hangs in the cabinet, and what is currently out.

Every guardhouse runs one, almost always in a hardcover book. The book answers
"who signed for this key" adequately and "which keys are unaccounted for right
now" not at all, because that question needs the whole book read backwards.

So the register is built around the open transaction. A key is out when its
latest transaction has no returned_at, and the database enforces that a key
cannot be out twice. Everything a supervisor actually asks — what is overdue,
what is still out at handover, who has held this key over the last month —
falls out of that one fact.

Permissions:
  keyreg:read   — see the cabinet and the outstanding list (all ops roles + viewer)
  keyreg:issue  — issue and receive keys (guards on the gate do this)
  keyreg:manage — add, edit and retire keys (supervisors and up)
Site scoping (Gap 81) applies to every read path and to issuing.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/keys", tags=["guard-ops"])


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: str
    key_code: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=160)
    cabinet_position: str | None = Field(default=None, max_length=40)
    notes: str | None = None


class KeyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_code: str | None = Field(default=None, min_length=1, max_length=40)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    cabinet_position: str | None = Field(default=None, max_length=40)
    notes: str | None = None
    is_active: bool | None = None


class KeyIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_id: str
    # A staff member, or a written name for anyone who is not staff. One of
    # the two is required — the database says so as well, but a 422 here is a
    # readable message and the constraint violation is not.
    issued_to_user_id: str | None = None
    issued_to_name: str | None = Field(default=None, max_length=160)
    issued_to_company: str | None = Field(default=None, max_length=160)
    issued_to_contact: str | None = Field(default=None, max_length=60)
    expected_return_at: datetime | None = None
    purpose: str | None = None


class KeyReturn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_notes: str | None = None


# The cabinet row, with whoever currently holds it. LEFT JOIN on the open
# transaction rather than a status column: a key is out because a transaction
# is open, and there is no second place for that truth to be wrong.
_KEY_SELECT = """
    SELECT k.id, k.site_id, k.key_code, k.label, k.cabinet_position,
           k.notes, k.is_active, k.created_at, k.updated_at,
           s.name AS site_name,
           t.id            AS open_transaction_id,
           t.issued_at,
           t.expected_return_at,
           t.purpose,
           COALESCE(hu.full_name, t.issued_to_name) AS held_by_name,
           t.issued_to_company                      AS held_by_company,
           t.issued_to_contact                      AS held_by_contact,
           iu.full_name                             AS issued_by_name,
           (t.id IS NOT NULL)                       AS is_out,
           (t.id IS NOT NULL AND t.expected_return_at IS NOT NULL
            AND t.expected_return_at < now())       AS is_overdue
      FROM site_keys k
      JOIN sites s ON s.id = k.site_id
 LEFT JOIN key_transactions t ON t.key_id = k.id AND t.returned_at IS NULL
 LEFT JOIN users hu ON hu.id = t.issued_to_user_id
 LEFT JOIN users iu ON iu.id = t.issued_by_user_id
"""


@router.get("", dependencies=[Depends(require_permission("keyreg:read"))])
async def list_keys(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    out_only: bool = False,
    include_inactive: bool = False,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """The cabinet. Sorted so the keys that are out sort first — a guard
    opening this page at handover is looking for the exceptions."""
    where, params = [], {}
    if not include_inactive:
        where.append("k.is_active = TRUE")
    if site_id:
        where.append("k.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if out_only:
        where.append("t.id IS NOT NULL")
    scope = site_scope_clause(allowed_sites, "k.site_id", params)
    if scope:
        where.append(scope)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            {_KEY_SELECT}
            {clause}
            ORDER BY (t.id IS NOT NULL) DESC,
                     t.expected_return_at NULLS LAST,
                     s.name, k.cabinet_position NULLS LAST, k.key_code
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


@router.get("/outstanding", dependencies=[Depends(require_permission("keyreg:read"))])
async def outstanding_keys(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """What is out right now, overdue first.

    Declared before /{key_id} — FastAPI matches routes in declaration order,
    and "outstanding" is a perfectly good UUID as far as the path is
    concerned until it reaches the cast.
    """
    where = ["t.returned_at IS NULL"]
    params: dict = {}
    if site_id:
        where.append("k.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "k.site_id", params)
    if scope:
        where.append(scope)

    result = await db.execute(
        text(f"""
            SELECT t.id, t.key_id, t.issued_at, t.expected_return_at, t.purpose,
                   k.key_code, k.label, k.cabinet_position, k.site_id,
                   s.name AS site_name,
                   COALESCE(hu.full_name, t.issued_to_name) AS held_by_name,
                   t.issued_to_company AS held_by_company,
                   t.issued_to_contact AS held_by_contact,
                   iu.full_name AS issued_by_name,
                   (t.expected_return_at IS NOT NULL AND t.expected_return_at < now())
                       AS is_overdue,
                   EXTRACT(EPOCH FROM (now() - t.issued_at)) / 3600.0 AS hours_out
              FROM key_transactions t
              JOIN site_keys k ON k.id = t.key_id
              JOIN sites s ON s.id = k.site_id
         LEFT JOIN users hu ON hu.id = t.issued_to_user_id
         LEFT JOIN users iu ON iu.id = t.issued_by_user_id
             WHERE {' AND '.join(where)}
          ORDER BY (t.expected_return_at IS NOT NULL AND t.expected_return_at < now()) DESC,
                   t.expected_return_at NULLS LAST, t.issued_at
        """),
        params,
    )
    rows = [dict(r) for r in result.mappings()]
    return {
        "out": len(rows),
        "overdue": sum(1 for r in rows if r["is_overdue"]),
        "keys": rows,
    }


@router.get("/transactions", dependencies=[Depends(require_permission("keyreg:read"))])
async def list_transactions(
    db: AsyncSession = Depends(get_db_with_tenant),
    key_id: str | None = None,
    site_id: str | None = None,
    limit: int = 100,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """The book, read forwards. This is what an audit asks for."""
    limit = max(1, min(limit, 500))
    where, params = [], {"lim": limit}
    if key_id:
        where.append("t.key_id = CAST(:key_id AS uuid)")
        params["key_id"] = key_id
    if site_id:
        where.append("k.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "k.site_id", params)
    if scope:
        where.append(scope)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            SELECT t.id, t.key_id, t.issued_at, t.expected_return_at, t.purpose,
                   t.returned_at, t.return_notes,
                   k.key_code, k.label, k.site_id, s.name AS site_name,
                   COALESCE(hu.full_name, t.issued_to_name) AS held_by_name,
                   t.issued_to_company AS held_by_company,
                   iu.full_name AS issued_by_name,
                   ru.full_name AS received_by_name
              FROM key_transactions t
              JOIN site_keys k ON k.id = t.key_id
              JOIN sites s ON s.id = k.site_id
         LEFT JOIN users hu ON hu.id = t.issued_to_user_id
         LEFT JOIN users iu ON iu.id = t.issued_by_user_id
         LEFT JOIN users ru ON ru.id = t.received_by_user_id
            {clause}
          ORDER BY t.issued_at DESC
             LIMIT :lim
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("keyreg:manage"))])
async def create_key(
    body: KeyCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    if not is_site_allowed(allowed_sites, body.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    if (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:id AS uuid)"),
                         {"id": body.site_id})).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    try:
        result = await db.execute(
            text("""
                INSERT INTO site_keys
                    (tenant_id, site_id, key_code, label, cabinet_position, notes)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:site AS uuid), :code, :label, :pos, :notes)
                RETURNING id, site_id, key_code, label, cabinet_position, notes,
                          is_active, created_at, updated_at
            """),
            {"site": body.site_id, "code": body.key_code, "label": body.label,
             "pos": body.cabinet_position, "notes": body.notes},
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Key {body.key_code} already exists at this site") from exc
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/issue", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("keyreg:issue"))])
async def issue_key(
    body: KeyIssue,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Hand a key over. Fails if it is already out — which is the point."""
    if not (body.issued_to_user_id or (body.issued_to_name or "").strip()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Record who took the key: either a staff member or a name",
        )
    if body.expected_return_at is not None:
        due = body.expected_return_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due < datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "expected_return_at is in the past")

    key = (await db.execute(
        text("SELECT k.id, k.site_id, k.is_active, k.key_code, k.label "
             "FROM site_keys k WHERE k.id = CAST(:id AS uuid)"),
        {"id": body.key_id},
    )).first()
    if key is None or not is_site_allowed(allowed_sites, key.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    if not key.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Key {key.key_code} has been retired from the cabinet")

    if body.issued_to_user_id and (await db.execute(
        text("SELECT 1 FROM users WHERE id = CAST(:id AS uuid)"),
        {"id": body.issued_to_user_id},
    )).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")

    try:
        result = await db.execute(
            text("""
                INSERT INTO key_transactions
                    (tenant_id, key_id, issued_to_user_id, issued_to_name,
                     issued_to_company, issued_to_contact, issued_by_user_id,
                     expected_return_at, purpose)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:key AS uuid), CAST(:to_uid AS uuid), :to_name,
                        :company, :contact, CAST(:by AS uuid), :due, :purpose)
                RETURNING id, key_id, issued_to_user_id, issued_to_name,
                          issued_to_company, issued_to_contact, issued_at,
                          expected_return_at, purpose
            """),
            {"key": body.key_id, "to_uid": body.issued_to_user_id,
             "to_name": body.issued_to_name, "company": body.issued_to_company,
             "contact": body.issued_to_contact, "by": token.user_id,
             "due": body.expected_return_at, "purpose": body.purpose},
        )
    except IntegrityError as exc:
        # uq_key_single_open_issue. Two guards at a handover reaching for the
        # same fob is exactly the race this catches.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Key {key.key_code} is already out — it has to come back first",
        ) from exc
    row = dict(result.mappings().first())
    await db.commit()
    return {**row, "key_code": key.key_code, "label": key.label}


@router.post("/transactions/{transaction_id}/return",
             dependencies=[Depends(require_permission("keyreg:issue"))])
async def return_key(
    transaction_id: str,
    body: KeyReturn,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take a key back. Returning an already-returned key is a 409 rather
    than a silent no-op: it usually means two people are looking at the same
    row and one of them is about to write the wrong time."""
    tx = (await db.execute(
        text("""
            SELECT t.id, t.returned_at, k.site_id, k.key_code
              FROM key_transactions t
              JOIN site_keys k ON k.id = t.key_id
             WHERE t.id = CAST(:id AS uuid)
        """),
        {"id": transaction_id},
    )).first()
    if tx is None or not is_site_allowed(allowed_sites, tx.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key issue not found")
    if tx.returned_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Key {tx.key_code} was already returned")

    result = await db.execute(
        text("""
            UPDATE key_transactions
               SET returned_at = now(),
                   received_by_user_id = CAST(:uid AS uuid),
                   return_notes = :notes
             WHERE id = CAST(:id AS uuid)
            RETURNING id, key_id, issued_at, returned_at, return_notes,
                      expected_return_at,
                      (expected_return_at IS NOT NULL AND returned_at > expected_return_at)
                          AS returned_late
        """),
        {"id": transaction_id, "uid": token.user_id, "notes": body.return_notes},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return {**row, "key_code": tx.key_code}


@router.get("/{key_id}", dependencies=[Depends(require_permission("keyreg:read"))])
async def get_key(
    key_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    row = (await db.execute(
        text(f"{_KEY_SELECT} WHERE k.id = CAST(:id AS uuid)"),
        {"id": key_id},
    )).mappings().first()
    if row is None or not is_site_allowed(allowed_sites, row["site_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    return dict(row)


@router.put("/{key_id}", dependencies=[Depends(require_permission("keyreg:manage"))])
async def update_key(
    key_id: str,
    body: KeyUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    existing = (await db.execute(
        text("SELECT site_id FROM site_keys WHERE id = CAST(:id AS uuid)"),
        {"id": key_id},
    )).first()
    if existing is None or not is_site_allowed(allowed_sites, existing.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")

    sets, params = [], {"id": key_id}
    if body.key_code is not None:
        sets.append("key_code = :code"); params["code"] = body.key_code
    if body.label is not None:
        sets.append("label = :label"); params["label"] = body.label
    if body.cabinet_position is not None:
        sets.append("cabinet_position = :pos"); params["pos"] = body.cabinet_position
    if body.notes is not None:
        sets.append("notes = :notes"); params["notes"] = body.notes
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")

    # Retiring a key that somebody is holding would erase the only record
    # that it needs to come back.
    if body.is_active is False:
        out = (await db.execute(
            text("SELECT 1 FROM key_transactions "
                 "WHERE key_id = CAST(:id AS uuid) AND returned_at IS NULL"),
            {"id": key_id},
        )).first()
        if out is not None:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "This key is currently out — receive it back before retiring it")

    sets.append("updated_at = now()")
    try:
        result = await db.execute(
            text(f"""
                UPDATE site_keys SET {', '.join(sets)}
                 WHERE id = CAST(:id AS uuid)
                RETURNING id, site_id, key_code, label, cabinet_position, notes,
                          is_active, updated_at
            """),
            params,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Another key at this site already uses that code") from exc
    row = dict(result.mappings().first())
    await db.commit()
    return row
