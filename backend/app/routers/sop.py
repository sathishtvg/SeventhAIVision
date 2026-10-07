"""The SOP library: procedures drafted, approved, in force — and found by their own words.

Phase 6 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
SECURITY_SOP_ARCHITECTURE.md.

  GET  /documents                      the library
  POST /documents                      a new procedure, with its first draft
  GET  PATCH /documents/{id}           one procedure and its versions
  PUT  /documents/{id}/incident-types  which kinds of incident it is for
  POST /documents/{id}/versions        draft the next version
  POST /documents/{id}/retire|restore
  GET  PATCH /versions/{id}            one version; correct a draft
  POST /versions/{id}/submit | withdraw | approve | reject
  POST GET /versions/{id}/attachment   the document as issued
  POST /ask                            the passages on something, as approved
  GET  /for-incident/{id}  /for-situation/{id}    the procedure for what has happened
  GET  /incident-types

A VERSION IS IN FORCE ONLY IF SOMEBODY ELSE APPROVED IT. Whoever drafted a
version does not approve it. An approved version is not changed: the next
version is drafted and approved in its turn.

SOMEBODY WHO ONLY READS SEES ONLY WHAT IS IN FORCE. A draft, a version awaiting
approval, a rejection and its reason are for the people who write procedures.

`/ask` RETURNS PROCEDURE TEXT, NOT AN ANSWER (services/sop_library.py). Post
orders are not touched: this is a library beside them.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.uploads import MAX_DOCUMENT_UPLOAD_BYTES, read_upload_limited
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit
from app.services import sop_library as library

MAX_BODY = 60000
#: What may be attached as the document as issued.
ATTACHMENT_TYPES = {".pdf": "application/pdf", ".docx":
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".txt": "text/plain"}
PERMISSIONS = ("sop:read", "sop:write", "sop:approve")

router = APIRouter(prefix="/api/v1/sop", tags=["sop"], dependencies=[Depends(require_permission("sop:read"))])
_WRITE = [Depends(require_permission("sop:write"))]
_APPROVE = [Depends(require_permission("sop:approve"))]


def _a_person(token: TokenPayload) -> None:
    """A procedure is written and approved by people who are named on it."""
    if token.via_api_key:
        raise HTTPException(403, "This is done by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "This is done by the organisation's own staff, not from a support session.")


async def _held(db: AsyncSession, role_id: int) -> frozenset[str]:
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code = ANY(CAST(:codes AS text[]))
    """), {"role": role_id, "codes": list(PERMISSIONS)})
    return frozenset(r.code for r in rows)


# ─── The library ─────────────────────────────────────────────────────────────

_DOCUMENT = f"""
    SELECT d.id, d.code, d.title, d.category, d.site_id, s.name AS site_name, d.is_retired, d.retired_at,
           d.created_at, d.updated_at,
           f.id AS in_force_id, f.version_no AS in_force_no, f.effective_from, f.effective_until,
           (f.id IS NOT NULL AND f.effective_until IS NOT NULL AND f.effective_until <= :now) AS expired,
           o.id AS open_id, o.version_no AS open_no, o.state AS open_state,
           (SELECT count(*) FROM sop_versions x WHERE x.document_id = d.id) AS version_count,
           ARRAY(SELECT t.incident_type FROM sop_incident_types t WHERE t.document_id = d.id ORDER BY 1)
               AS incident_types
      FROM sop_documents d
      LEFT JOIN sites s ON s.id = d.site_id
      LEFT JOIN ({library.IN_FORCE}) f ON f.document_id = d.id
      LEFT JOIN sop_versions o ON o.document_id = d.id AND o.state IN ('DRAFT','SUBMITTED')
"""


def _state(row: Mapping) -> str:
    """Where a procedure stands, in one word."""
    if row["is_retired"]:
        return "RETIRED"
    if row["in_force_id"] is None:
        return "NOT_YET_APPROVED"
    return "EXPIRED" if row["expired"] else "IN_FORCE"


def _document(row: Mapping, writes: bool) -> dict:
    out = {key: row[key] for key in ("id", "code", "title", "category", "site_id", "site_name", "is_retired",
                                     "retired_at", "created_at", "updated_at", "effective_from", "effective_until",
                                     "version_count")}
    out.update(state=_state(row), in_force_version_id=row["in_force_id"], in_force_version_no=row["in_force_no"],
               incident_types=list(row["incident_types"]))
    # A version being written, or awaiting a decision, is the writers' business.
    out["open_version"] = ({"id": row["open_id"], "version_no": row["open_no"], "state": row["open_state"]}
                           if writes and row["open_id"] else None)
    return out


def _readable(row: Mapping, allowed, writes: bool) -> bool:
    """A procedure for a site is read by whoever may see the site. Somebody who
    only reads sees a procedure while a version of it is in force: not before
    one is approved, not after it has run out, and not once it is retired."""
    if row["site_id"] is not None and not is_site_allowed(allowed, row["site_id"]):
        return False
    return writes or _state(row) == "IN_FORCE"


@router.get("/documents")
async def list_documents(
    q: str | None = Query(None, max_length=200),
    category: str | None = Query(None),
    site_id: uuid.UUID | None = Query(None),
    state: Literal["IN_FORCE", "NOT_YET_APPROVED", "EXPIRED", "RETIRED", "AWAITING"] | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The library. Somebody who only reads sees the procedures that have a
    version in force; whoever writes them sees every procedure and where each
    stands. `AWAITING` gives those with a version waiting for a decision."""
    if category is not None and category not in library.CATEGORIES:
        raise HTTPException(422, f"Unknown category '{category}'. One of: {', '.join(library.CATEGORIES)}.")
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    held = await _held(db, token.role_id)
    writes = "sop:write" in held
    params: dict = {"now": datetime.now(timezone.utc)}
    where = [library.site_clause(allowed, site_id, params)]
    if q and q.strip():
        where.append("(d.title ILIKE :q ESCAPE '\\' OR d.code ILIKE :q ESCAPE '\\')")
        params["q"] = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if category:
        where.append("d.category = :category")
        params["category"] = category
    rows = (await db.execute(text(f"{_DOCUMENT} WHERE {' AND '.join(where)} ORDER BY d.is_retired, d.code"),
                             params)).mappings().all()
    items = []
    for r in rows:
        if not _readable(r, allowed, writes):
            continue
        if state == "AWAITING":
            if not (writes and r["open_state"] == "SUBMITTED"):
                continue
        elif state is not None and _state(r) != state:
            continue
        items.append(_document(r, writes))
    return {"items": items, "categories": list(library.CATEGORIES), "can_write": writes,
            "can_approve": "sop:approve" in held}


class NewDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    category: str = "general"
    site_id: uuid.UUID | None = None
    body: str = Field(..., min_length=1, max_length=MAX_BODY)
    incident_types: list[str] = Field(default_factory=list, max_length=30)


async def _checked_site(db: AsyncSession, site_id, allowed) -> None:
    if site_id is None:
        if allowed is not None:
            raise HTTPException(422, "You are held to particular sites: a procedure you write names one of them.")
        return
    known = (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": str(site_id)})).scalar()
    if not known or not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")


def _checked_types(kinds: list[str]) -> list[str]:
    cleaned = sorted({k.strip().lower() for k in kinds if k.strip()})
    bad = [k for k in cleaned if not library.TYPE.match(k)]
    if bad:
        raise HTTPException(422, f"'{bad[0]}' is not a kind of incident: lower-case letters, digits and "
                                 "underscores, as in an alert code.")
    return cleaned


def _theirs_to_change(document: Mapping, allowed) -> None:
    """What a procedure for every site is called, is for, and which incidents
    it is put beside is settled by somebody who answers for every site. Its
    next version may be drafted by anybody who writes procedures: that still
    has to be approved."""
    if document["site_id"] is None and allowed is not None:
        raise HTTPException(403, "A procedure for every site is changed by somebody who is not held to "
                                 "particular sites.")


async def _one_document(db: AsyncSession, document_id, allowed, writes: bool) -> dict:
    row = (await db.execute(text(f"{_DOCUMENT} WHERE d.id = CAST(:id AS uuid)"),
                            {"id": str(document_id), "now": datetime.now(timezone.utc)})).mappings().first()
    if row is None or not _readable(row, allowed, writes):
        raise HTTPException(404, "Procedure not found")
    return dict(row)


@router.post("/documents", status_code=201, dependencies=_WRITE)
async def write_document(
    body: NewDocument,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Write a new procedure. It is given its code and a first draft; it is not
    in force, and is not shown to anybody who only reads, until that draft is
    submitted and somebody else approves it."""
    _a_person(token)
    if body.category not in library.CATEGORIES:
        raise HTTPException(422, f"Unknown category '{body.category}'. One of: {', '.join(library.CATEGORIES)}.")
    if not body.title.strip() or not body.body.strip():
        raise HTTPException(422, "A procedure has a title and says something.")
    await _checked_site(db, body.site_id, allowed)
    kinds = _checked_types(body.incident_types)
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('sop:' || current_setting('app.current_tenant')))"))
    number = (await db.execute(text(
        "SELECT COALESCE(max(CAST(substring(code FROM 5) AS integer)), 0) + 1 FROM sop_documents"))).scalar()
    new_id = (await db.execute(text("""
        INSERT INTO sop_documents (tenant_id, code, title, category, site_id, created_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :code, :title, :category, CAST(:site AS uuid),
                CAST(:who AS uuid))
        RETURNING id
    """), {"code": f"SOP-{number:04d}", "title": body.title.strip(), "category": body.category,
           "site": str(body.site_id) if body.site_id else None, "who": token.user_id})).scalar()
    await db.execute(text("""
        INSERT INTO sop_versions (tenant_id, document_id, version_no, body, drafted_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :d, 1, :body, CAST(:who AS uuid))
    """), {"d": new_id, "body": body.body.strip(), "who": token.user_id})
    for kind in kinds:
        await db.execute(text("""
            INSERT INTO sop_incident_types (tenant_id, document_id, incident_type)
            VALUES (current_setting('app.current_tenant')::uuid, :d, :kind)
        """), {"d": new_id, "kind": kind})
    await intel_audit.record(db, request, token, "sop.create", "sop_document", new_id, site_id=body.site_id,
                             detail={"code": f"SOP-{number:04d}", "title": body.title.strip()})
    answer = await _detail(db, new_id, token, allowed)
    await db.commit()
    return answer


_VERSION = """
    SELECT v.id, v.document_id, v.version_no, v.body, v.change_note, v.state, v.drafted_by_user_id,
           a.full_name AS drafted_by_name, v.drafted_at, v.submitted_at, v.decided_by_user_id,
           b.full_name AS decided_by_name, v.decided_at, v.decision_note, v.effective_from, v.effective_until,
           v.attachment_name, v.attachment_sha256, v.updated_at
      FROM sop_versions v
      LEFT JOIN users a ON a.id = v.drafted_by_user_id
      LEFT JOIN users b ON b.id = v.decided_by_user_id
"""


def _version(row: Mapping, in_force_id, token: TokenPayload, held: frozenset[str]) -> dict:
    out = {k: row[k] for k in row.keys()}
    mine = str(row["drafted_by_user_id"]) == token.user_id
    writes, approves = "sop:write" in held, "sop:approve" in held
    out["in_force"] = row["id"] == in_force_id
    out["has_attachment"] = row["attachment_name"] is not None
    out["may"] = {
        "edit": writes and row["state"] == "DRAFT", "submit": writes and row["state"] == "DRAFT",
        "withdraw": writes and row["state"] == "SUBMITTED",
        # Not by whoever drafted it.
        "decide": approves and row["state"] == "SUBMITTED" and not mine,
    }
    return out


async def _detail(db: AsyncSession, document_id, token: TokenPayload, allowed) -> dict:
    """A procedure with its versions. Somebody who only reads is given the
    version in force and no other."""
    held = await _held(db, token.role_id)
    writes = "sop:write" in held
    row = await _one_document(db, document_id, allowed, writes)
    versions = (await db.execute(text(f"{_VERSION} WHERE v.document_id = :d ORDER BY v.version_no DESC"),
                                 {"d": row["id"]})).mappings().all()
    shown = [v for v in versions if writes or v["id"] == row["in_force_id"]]
    return {**_document(row, writes), "versions": [_version(v, row["in_force_id"], token, held) for v in shown],
            "can_write": writes, "can_approve": "sop:approve" in held}


@router.get("/documents/{document_id:uuid}")
async def read_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One procedure: where it stands, which kinds of incident it is for, and
    its versions, newest first."""
    return await _detail(db, document_id, token, allowed)


class DocumentChange(BaseModel):
    """Only what is given is changed. A procedure's code does not change."""
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = None
    site_id: uuid.UUID | None = None


@router.patch("/documents/{document_id:uuid}", dependencies=_WRITE)
async def change_document(
    document_id: uuid.UUID,
    body: DocumentChange,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Change a procedure's title, category or site. What it says is changed by
    drafting its next version, not here."""
    _a_person(token)
    current = await _one_document(db, document_id, allowed, True)
    given = body.model_fields_set
    if not given:
        raise HTTPException(422, "Nothing was given to change.")
    _theirs_to_change(current, allowed)
    title = body.title.strip() if "title" in given and body.title else current["title"]
    category = body.category if "category" in given and body.category else current["category"]
    site = body.site_id if "site_id" in given else current["site_id"]
    if category not in library.CATEGORIES:
        raise HTTPException(422, f"Unknown category '{category}'. One of: {', '.join(library.CATEGORIES)}.")
    if not title:
        raise HTTPException(422, "A procedure has a title.")
    if "site_id" in given:
        await _checked_site(db, site, allowed)
    await db.execute(text("""
        UPDATE sop_documents SET title = :title, category = :category, site_id = CAST(:site AS uuid),
               updated_at = now() WHERE id = CAST(:id AS uuid)
    """), {"title": title, "category": category, "site": str(site) if site else None, "id": str(document_id)})
    await intel_audit.record(db, request, token, "sop.update", "sop_document", document_id, site_id=site,
                             detail={"code": current["code"], "changed": sorted(given)})
    answer = await _detail(db, document_id, token, allowed)
    await db.commit()
    return answer


class TypesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_types: list[str] = Field(..., max_length=30)


@router.put("/documents/{document_id:uuid}/incident-types", dependencies=_WRITE)
async def set_incident_types(
    document_id: uuid.UUID,
    body: TypesBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say which kinds of incident a procedure is for. The list given replaces
    the list there was. It is what puts the procedure beside an incident of
    that kind."""
    _a_person(token)
    current = await _one_document(db, document_id, allowed, True)
    _theirs_to_change(current, allowed)
    kinds = _checked_types(body.incident_types)
    await db.execute(text("DELETE FROM sop_incident_types WHERE document_id = :d"), {"d": current["id"]})
    for kind in kinds:
        await db.execute(text("""
            INSERT INTO sop_incident_types (tenant_id, document_id, incident_type)
            VALUES (current_setting('app.current_tenant')::uuid, :d, :kind)
        """), {"d": current["id"], "kind": kind})
    await intel_audit.record(db, request, token, "sop.tag", "sop_document", document_id,
                             site_id=current["site_id"], detail={"code": current["code"], "incident_types": kinds,
                                                                 "were": list(current["incident_types"])})
    answer = await _detail(db, document_id, token, allowed)
    await db.commit()
    return answer


class NextVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(None, min_length=1, max_length=MAX_BODY)
    change_note: str | None = Field(None, max_length=2000)


@router.post("/documents/{document_id:uuid}/versions", status_code=201, dependencies=_WRITE)
async def draft_version(
    document_id: uuid.UUID,
    request: Request,
    body: NextVersion | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Draft a procedure's next version, starting from the text of its latest.
    The version in force stays in force until the new one is approved and its
    date comes."""
    _a_person(token)
    body = body or NextVersion()
    current = await _one_document(db, document_id, allowed, True)
    if current["is_retired"]:
        raise HTTPException(409, "This procedure is retired. Restore it to draft a new version.")
    if current["open_id"] is not None:
        raise HTTPException(409, f"Version {current['open_no']} is already being "
                                 f"{'written' if current['open_state'] == 'DRAFT' else 'considered'}.")
    latest = (await db.execute(text(
        "SELECT version_no, body FROM sop_versions WHERE document_id = :d ORDER BY version_no DESC LIMIT 1"),
        {"d": current["id"]})).first()
    try:
        new_id = (await db.execute(text("""
            INSERT INTO sop_versions (tenant_id, document_id, version_no, body, change_note, drafted_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :d, :n, :body, :note, CAST(:who AS uuid))
            RETURNING id
        """), {"d": current["id"], "n": latest.version_no + 1, "body": (body.body or latest.body).strip(),
               "note": (body.change_note or "").strip() or None, "who": token.user_id})).scalar()
    except IntegrityError as exc:   # somebody else began one in the same moment
        raise HTTPException(409, "Another version of this procedure is already being written.") from exc
    await intel_audit.record(db, request, token, "sop.version.draft", "sop_version", new_id,
                             site_id=current["site_id"], detail={"code": current["code"],
                                                                 "version_no": latest.version_no + 1})
    answer = await _detail(db, document_id, token, allowed)
    await db.commit()
    return answer


async def _set_retired(db, request, token, document_id, allowed, retired: bool) -> dict:
    _a_person(token)
    current = await _one_document(db, document_id, allowed, True)
    _theirs_to_change(current, allowed)
    if current["is_retired"] == retired:
        raise HTTPException(409, "This procedure is already retired." if retired
                            else "This procedure is not retired.")
    await db.execute(text("""
        UPDATE sop_documents
           SET is_retired = :r, retired_at = :at, retired_by_user_id = CAST(:who AS uuid), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"r": retired, "at": datetime.now(timezone.utc) if retired else None,
           "who": token.user_id if retired else None, "id": str(document_id)})
    await intel_audit.record(db, request, token, "sop.retire" if retired else "sop.restore", "sop_document",
                             document_id, site_id=current["site_id"], detail={"code": current["code"]})
    answer = await _detail(db, document_id, token, allowed)
    await db.commit()
    return answer


@router.post("/documents/{document_id:uuid}/retire", dependencies=_WRITE + _APPROVE)
async def retire_document(
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take a procedure out of force. It is kept, with every version, and is no
    longer found or put beside an incident."""
    return await _set_retired(db, request, token, document_id, allowed, True)


@router.post("/documents/{document_id:uuid}/restore", dependencies=_WRITE + _APPROVE)
async def restore_document(
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Put a retired procedure back. Its approved version is in force again if
    it has not run out."""
    return await _set_retired(db, request, token, document_id, allowed, False)


# ─── A version ───────────────────────────────────────────────────────────────

async def _one_version(db: AsyncSession, version_id, token: TokenPayload, allowed) -> tuple[dict, dict, frozenset]:
    """A version and its procedure, as this caller may read them."""
    held = await _held(db, token.role_id)
    writes = "sop:write" in held
    row = (await db.execute(text(f"{_VERSION} WHERE v.id = CAST(:id AS uuid)"), {"id": str(version_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Version not found")
    document = await _one_document(db, row["document_id"], allowed, writes)
    if not writes and row["id"] != document["in_force_id"]:
        raise HTTPException(404, "Version not found")
    return dict(row), document, held


@router.get("/versions/{version_id:uuid}")
async def read_version(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One version of a procedure, with its text cut into the passages it is
    found by."""
    row, document, held = await _one_version(db, version_id, token, allowed)
    return {**_version(row, document["in_force_id"], token, held), "passages": library.passages(row["body"]),
            "procedure": _document(document, "sop:write" in held)}


class VersionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(None, min_length=1, max_length=MAX_BODY)
    change_note: str | None = Field(None, max_length=2000)


@router.patch("/versions/{version_id:uuid}", dependencies=_WRITE)
async def change_version(
    version_id: uuid.UUID,
    body: VersionChange,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Correct a draft. A version that has been submitted is withdrawn first;
    one that has been approved or rejected is not changed at all."""
    _a_person(token)
    row, document, held = await _one_version(db, version_id, token, allowed)
    if row["state"] != "DRAFT":
        raise HTTPException(409, "Only a draft is changed. " + (
            "Withdraw it to correct it." if row["state"] == "SUBMITTED" else "Draft the next version instead."))
    given = body.model_fields_set
    if not given:
        raise HTTPException(422, "Nothing was given to change.")
    if "body" in given and not (body.body or "").strip():
        raise HTTPException(422, "A procedure says something.")
    await db.execute(text("""
        UPDATE sop_versions SET body = :body, change_note = :note, updated_at = now() WHERE id = CAST(:id AS uuid)
    """), {"body": body.body.strip() if "body" in given and body.body else row["body"],
           "note": ((body.change_note or "").strip() or None) if "change_note" in given else row["change_note"],
           "id": str(version_id)})
    after, document, held = await _one_version(db, version_id, token, allowed)
    answer = _version(after, document["in_force_id"], token, held)
    await db.commit()
    return answer


async def _move(db, request, token, version_id, allowed, *, from_state: str, to_state: str, action: str,
                refusal: str) -> dict:
    _a_person(token)
    row, document, held = await _one_version(db, version_id, token, allowed)
    if row["state"] != from_state:
        raise HTTPException(409, refusal)
    if to_state == "SUBMITTED" and row["version_no"] > 1 and not (row["change_note"] or "").strip():
        raise HTTPException(422, "Say what is different from the version before, so that whoever approves it "
                                 "knows what they are approving.")
    await db.execute(text(
        "UPDATE sop_versions SET state = :s, submitted_at = CASE WHEN :submitting THEN now() END, "
        "updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"s": to_state, "submitting": to_state == "SUBMITTED", "id": str(version_id)})
    await intel_audit.record(db, request, token, action, "sop_version", version_id, site_id=document["site_id"],
                             detail={"code": document["code"], "version_no": row["version_no"]})
    after, document, held = await _one_version(db, version_id, token, allowed)
    answer = _version(after, document["in_force_id"], token, held)
    await db.commit()
    return answer


@router.post("/versions/{version_id:uuid}/submit", dependencies=_WRITE)
async def submit_version(
    version_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Put a draft forward for approval. After the first version, it says what
    is different from the one before."""
    return await _move(db, request, token, version_id, allowed, from_state="DRAFT", to_state="SUBMITTED",
                       action="sop.version.submit", refusal="Only a draft is submitted.")


@router.post("/versions/{version_id:uuid}/withdraw", dependencies=_WRITE)
async def withdraw_version(
    version_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take a submitted version back to a draft, to correct it before anybody
    decides on it."""
    return await _move(db, request, token, version_id, allowed, from_state="SUBMITTED", to_state="DRAFT",
                       action="sop.version.withdraw", refusal="Only a version awaiting a decision is withdrawn.")


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: When it comes into force. Now, if not given.
    effective_from: datetime | None = None
    #: When it runs out and has to be looked at again. Not at all, if not given.
    effective_until: datetime | None = None
    note: str | None = Field(None, max_length=2000)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


async def _to_decide(db, token, version_id, allowed) -> tuple[dict, dict, frozenset]:
    _a_person(token)
    row, document, held = await _one_version(db, version_id, token, allowed)
    if row["state"] != "SUBMITTED":
        raise HTTPException(409, "Only a version that has been submitted is approved or rejected.")
    if str(row["drafted_by_user_id"]) == token.user_id:
        raise HTTPException(403, "A procedure is approved by somebody other than who wrote it.")
    if document["is_retired"]:
        raise HTTPException(409, "This procedure is retired. Restore it first.")
    return row, document, held


@router.post("/versions/{version_id:uuid}/approve", dependencies=_WRITE + _APPROVE)
async def approve_version(
    version_id: uuid.UUID,
    request: Request,
    body: ApproveBody | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Approve a submitted version: from its date it is the procedure in force,
    in place of the version before. Not by whoever wrote it. Its text is cut
    into the passages it will be found by, and from then on neither can be
    changed."""
    body = body or ApproveBody()
    row, document, held = await _to_decide(db, token, version_id, allowed)
    now = datetime.now(timezone.utc)
    for moment in (body.effective_from, body.effective_until):
        if moment is not None and moment.tzinfo is None:
            raise HTTPException(422, "Give dates with a time zone.")
    start = body.effective_from or now
    if body.effective_until is not None and body.effective_until <= max(start, now):
        raise HTTPException(422, "It runs out after it comes into force, and in the future.")
    cut = library.passages(row["body"])
    if not cut:
        raise HTTPException(422, "There is nothing in this version to approve: it has headings and no text.")
    await db.execute(text("""
        UPDATE sop_versions
           SET state = 'APPROVED', decided_by_user_id = CAST(:who AS uuid), decided_at = now(), decision_note = :note,
               effective_from = :start, effective_until = :until, updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"who": token.user_id, "note": (body.note or "").strip() or None, "start": start,
           "until": body.effective_until, "id": str(version_id)})
    for ordinal, passage in enumerate(cut, start=1):
        await db.execute(text("""
            INSERT INTO sop_passages (tenant_id, version_id, document_id, ordinal, heading, body)
            VALUES (current_setting('app.current_tenant')::uuid, :v, :d, :n, :heading, :body)
        """), {"v": row["id"], "d": document["id"], "n": ordinal, "heading": passage["heading"],
               "body": passage["body"]})
    await intel_audit.record(db, request, token, "sop.version.approve", "sop_version", version_id,
                             site_id=document["site_id"],
                             detail={"code": document["code"], "version_no": row["version_no"],
                                     "effective_from": start.isoformat(), "passages": len(cut),
                                     "effective_until": body.effective_until.isoformat() if body.effective_until else None})
    answer = await _detail(db, document["id"], token, allowed)
    await db.commit()
    return answer


@router.post("/versions/{version_id:uuid}/reject", dependencies=_WRITE + _APPROVE)
async def reject_version(
    version_id: uuid.UUID,
    body: RejectBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Reject a submitted version, and say why. It is kept as rejected; the
    next version is drafted from it."""
    row, document, held = await _to_decide(db, token, version_id, allowed)
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is rejected.")
    await db.execute(text("""
        UPDATE sop_versions SET state = 'REJECTED', decided_by_user_id = CAST(:who AS uuid), decided_at = now(),
               decision_note = :note, updated_at = now() WHERE id = CAST(:id AS uuid)
    """), {"who": token.user_id, "note": body.reason.strip(), "id": str(version_id)})
    await intel_audit.record(db, request, token, "sop.version.reject", "sop_version", version_id,
                             site_id=document["site_id"],
                             detail={"code": document["code"], "version_no": row["version_no"]})
    answer = await _detail(db, document["id"], token, allowed)
    await db.commit()
    return answer


def _attachment(tenant_id: str, relative: str) -> Path:
    return Path(settings.EMPLOYEE_DOCS_ROOT) / str(tenant_id) / relative


@router.post("/versions/{version_id:uuid}/attachment", dependencies=_WRITE)
async def attach_document(
    version_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Attach the document as it was issued — a PDF, a Word file, a scan — to a
    draft. It is kept beside the version with its checksum. The platform does
    not read it: what is searched and quoted is the version's text."""
    _a_person(token)
    row, document, held = await _one_version(db, version_id, token, allowed)
    if row["state"] != "DRAFT":
        raise HTTPException(409, "A document is attached to a draft, before it is submitted.")
    name = Path(file.filename or "").name
    extension = Path(name).suffix.lower()
    if extension not in ATTACHMENT_TYPES:
        raise HTTPException(422, f"That kind of file is not taken. One of: {', '.join(sorted(ATTACHMENT_TYPES))}.")
    data = await read_upload_limited(file, MAX_DOCUMENT_UPLOAD_BYTES)
    if not data:
        raise HTTPException(422, "That file is empty.")
    relative = f"sop/{document['id']}/{row['id']}{extension}"
    target = _attachment(token.tenant_id, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    await db.execute(text("""
        UPDATE sop_versions SET attachment_path = :path, attachment_name = :name, attachment_sha256 = :sha,
               updated_at = now() WHERE id = CAST(:id AS uuid)
    """), {"path": relative, "name": name[:255], "sha": digest, "id": str(version_id)})
    await intel_audit.record(db, request, token, "sop.attach", "sop_version", version_id,
                             site_id=document["site_id"],
                             detail={"code": document["code"], "version_no": row["version_no"], "sha256": digest,
                                     "bytes": len(data)})
    await db.commit()
    return {"attachment_name": name[:255], "attachment_sha256": digest, "bytes": len(data)}


@router.get("/versions/{version_id:uuid}/attachment")
async def download_attachment(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The document as it was issued, for a version this caller may read."""
    await _one_version(db, version_id, token, allowed)
    stored = (await db.execute(text(
        "SELECT attachment_path, attachment_name FROM sop_versions WHERE id = CAST(:id AS uuid)"),
        {"id": str(version_id)})).first()
    if stored is None or stored.attachment_path is None:
        raise HTTPException(404, "This version has no document attached.")
    target = _attachment(token.tenant_id, stored.attachment_path)
    if not target.is_file():
        raise HTTPException(404, "The attached document is no longer on this server.")
    return FileResponse(target, filename=stored.attachment_name,
                        media_type=ATTACHMENT_TYPES.get(target.suffix.lower(), "application/octet-stream"))


# ─── Finding the procedure on something ──────────────────────────────────────

class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=2, max_length=300)
    site_id: uuid.UUID | None = None


@router.post("/ask")
async def ask(
    body: Question,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The passages of approved procedures in force that use the words of the
    question — as they were approved, each with its procedure, version and who
    approved it. It is not an answer: nothing is composed, and when no passage
    uses those words it says so."""
    if body.site_id is not None and not is_site_allowed(allowed, str(body.site_id)):
        raise HTTPException(404, "Site not found")
    return await library.ask(db, body.question.strip(), datetime.now(timezone.utc), allowed, site_id=body.site_id)


@router.get("/incident-types")
async def incident_types(db: AsyncSession = Depends(get_db_with_tenant)):
    """The kinds of incident a procedure can be said to be for: the ones always
    offered, the ones this organisation's incidents have had in the last 90
    days, and the ones its procedures already name."""
    seen = {r[0] for r in await db.execute(text("""
        SELECT DISTINCT split_part(alert_code, '.', 1) FROM incidents
         WHERE alert_code IS NOT NULL AND created_at >= now() - interval '90 days'
        UNION SELECT DISTINCT incident_type FROM sop_incident_types
    """)) if r[0] and library.TYPE.match(r[0])}
    return {"items": sorted(seen | set(library.KNOWN_TYPES))}


@router.get("/for-incident/{incident_id:uuid}")
async def for_incident(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The procedures in force for an incident of this kind at its site. The
    kind is the first part of the incident's alert code; an incident raised by
    hand has none, and then nothing is put beside it — ask the library."""
    row = (await db.execute(text("""
        SELECT i.id, i.title, split_part(i.alert_code, '.', 1) AS kind, c.site_id
          FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id WHERE i.id = CAST(:id AS uuid)
    """), {"id": str(incident_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Incident not found")
    kinds = [row["kind"]] if row["kind"] else []
    found = await library.relevant(db, kinds, row["site_id"], datetime.now(timezone.utc), allowed)
    return {"incident_id": row["id"], "incident_types": kinds, "procedures": found,
            "why_none": None if found else (
                "This incident was raised by hand and has no kind: no procedure can be put beside it."
                if not kinds else f"No procedure in force is for an incident of the kind '{kinds[0]}' at this site.")}


@router.get("/for-situation/{situation_id:uuid}")
async def for_situation(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The procedures in force for what a situation is made of: the kinds of
    its events, at its site."""
    row = (await db.execute(text("SELECT id, site_id FROM security_situations WHERE id = CAST(:id AS uuid)"),
                            {"id": str(situation_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Situation not found")
    kinds = sorted({r[0] for r in await db.execute(text("""
        SELECT DISTINCT split_part(e.event_type, '.', 1)
          FROM security_situation_events l JOIN security_events e ON e.id = l.event_id
         WHERE l.situation_id = :s
    """), {"s": row["id"]}) if r[0]})
    found = await library.relevant(db, kinds, row["site_id"], datetime.now(timezone.utc), allowed)
    return {"situation_id": row["id"], "incident_types": kinds, "procedures": found,
            "why_none": None if found else "No procedure in force is for what this situation is made of."}
