"""Gap 9 — SCIM 2.0 User Provisioning (RFC 7643 / RFC 7644).

Token management (JWT-gated, scim:manage permission):
  POST   /api/v1/scim/tokens            — create provisioning token
  GET    /api/v1/scim/tokens            — list tokens
  DELETE /api/v1/scim/tokens/{token_id} — revoke token

SCIM protocol (Bearer token from scim_tokens table):
  GET    /api/v1/scim/v2/ServiceProviderConfig
  GET    /api/v1/scim/v2/Schemas
  GET    /api/v1/scim/v2/Users
  POST   /api/v1/scim/v2/Users
  GET    /api/v1/scim/v2/Users/{user_id}
  PUT    /api/v1/scim/v2/Users/{user_id}
  PATCH  /api/v1/scim/v2/Users/{user_id}
  DELETE /api/v1/scim/v2/Users/{user_id}
  GET    /api/v1/scim/v2/Groups
  POST   /api/v1/scim/v2/Groups
"""
from __future__ import annotations

import hashlib
import secrets
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant, get_raw_db

router = APIRouter(prefix="/api/v1/scim", tags=["scim"])

# SCIM URN constants
_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"

_ROLE_NAMES = {1: "SuperAdmin", 2: "Admin", 3: "Supervisor", 4: "Operator", 5: "SecurityGuard", 6: "Viewer"}
_NAME_TO_ROLE = {v.lower(): k for k, v in _ROLE_NAMES.items()}


# ── Token management request bodies ──────────────────────────────────────────

class TokenCreateRequest(BaseModel):
    name: str
    expires_at: datetime | None = None


# ── SCIM token authentication ─────────────────────────────────────────────────

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _get_scim_context(request: Request, db: AsyncSession = Depends(get_raw_db)):
    """Validate SCIM Bearer token; return (token_row, tenant_id)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "SCIM Bearer token required",
                            headers={"WWW-Authenticate": "Bearer"})
    raw_token = auth[7:]
    token_hash = _hash_token(raw_token)
    row = (await db.execute(
        text("""
            SELECT id, tenant_id, is_active, expires_at
            FROM scim_tokens
            WHERE token_hash = :h AND is_active = TRUE
        """),
        {"h": token_hash},
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked SCIM token",
                            headers={"WWW-Authenticate": "Bearer"})
    if row["expires_at"] and row["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "SCIM token has expired")
    # Update last_used_at (best-effort)
    await db.execute(
        text("UPDATE scim_tokens SET last_used_at = now() WHERE id = :tid"),
        {"tid": str(row["id"])},
    )
    await db.commit()
    # Re-scope the GUC to this tenant for the remainder of the request.
    # Committing ends the previous transaction and clears the transaction-local GUC;
    # setting it to the real tenant_id lets the shared db session satisfy RLS
    # WITH CHECK / USING policies on 'users' and other tenant-scoped tables.
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(row["tenant_id"])},
    )
    return row


# ── Token management ──────────────────────────────────────────────────────────

@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def create_scim_token(
    body: TokenCreateRequest,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
    _=Depends(require_permission("scim:manage")),
):
    raw = secrets.token_urlsafe(40)
    h = _hash_token(raw)
    tid = str(_uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO scim_tokens (id, tenant_id, name, token_hash, created_by_user_id, expires_at)
            VALUES (CAST(:id AS UUID), CAST(:tenant AS UUID), :name, :hash,
                    CAST(:uid AS UUID), :exp)
        """),
        {
            "id": tid,
            "tenant": token.tenant_id,
            "name": body.name,
            "hash": h,
            "uid": token.user_id,
            "exp": body.expires_at,
        },
    )
    await db.commit()
    return {
        "id": tid,
        "name": body.name,
        "token": raw,  # returned ONCE — not stored
        "expires_at": body.expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/tokens")
async def list_scim_tokens(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
    _=Depends(require_permission("scim:manage")),
):
    rows = (await db.execute(
        text("""
            SELECT id, name, expires_at, last_used_at, is_active, created_at
            FROM scim_tokens
            WHERE tenant_id = CAST(:tid AS UUID)
            ORDER BY created_at DESC
        """),
        {"tid": token.tenant_id},
    )).mappings().all()
    return [dict(r) for r in rows]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_scim_token(
    token_id: str,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
    _=Depends(require_permission("scim:manage")),
):
    result = await db.execute(
        text("""
            UPDATE scim_tokens SET is_active = FALSE
            WHERE id = CAST(:tid AS UUID) AND tenant_id = CAST(:tenant AS UUID)
        """),
        {"tid": token_id, "tenant": token.tenant_id},
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")


# ── ServiceProviderConfig ─────────────────────────────────────────────────────

@router.get("/v2/ServiceProviderConfig")
async def service_provider_config():
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Authentication via Bearer token from /api/v1/scim/tokens",
            }
        ],
        "meta": {
            "resourceType": "ServiceProviderConfig",
            "location": "/api/v1/scim/v2/ServiceProviderConfig",
        },
    }


# ── Schemas ───────────────────────────────────────────────────────────────────

@router.get("/v2/Schemas")
async def list_schemas():
    return {
        "schemas": [_LIST_SCHEMA],
        "totalResults": 2,
        "Resources": [_user_schema_def(), _group_schema_def()],
    }


def _user_schema_def() -> dict:
    return {
        "id": _USER_SCHEMA,
        "name": "User",
        "description": "User account",
        "attributes": [
            {"name": "userName", "type": "string", "required": True},
            {"name": "name", "type": "complex"},
            {"name": "emails", "type": "complex", "multiValued": True},
            {"name": "active", "type": "boolean"},
            {"name": "externalId", "type": "string"},
        ],
        "meta": {"resourceType": "Schema", "location": "/api/v1/scim/v2/Schemas/urn:ietf:params:scim:schemas:core:2.0:User"},
    }


def _group_schema_def() -> dict:
    return {
        "id": _GROUP_SCHEMA,
        "name": "Group",
        "description": "Platform role group",
        "attributes": [
            {"name": "displayName", "type": "string", "required": True},
            {"name": "members", "type": "complex", "multiValued": True},
        ],
        "meta": {"resourceType": "Schema", "location": "/api/v1/scim/v2/Schemas/urn:ietf:params:scim:schemas:core:2.0:Group"},
    }


# ── User helpers ──────────────────────────────────────────────────────────────

def _user_to_scim(row: dict) -> dict:
    uid = str(row["id"])
    name_parts = (row.get("full_name") or "").split(" ", 1)
    return {
        "schemas": [_USER_SCHEMA],
        "id": uid,
        "externalId": row.get("scim_external_id"),
        "userName": row["email"],
        "name": {
            "givenName": name_parts[0] if name_parts else "",
            "familyName": name_parts[1] if len(name_parts) > 1 else "",
            "formatted": row.get("full_name") or "",
        },
        "emails": [{"value": row["email"], "primary": True, "type": "work"}],
        "active": bool(row["is_active"]),
        "roles": [{"value": str(row["role_id"]), "display": _ROLE_NAMES.get(row["role_id"], "Viewer"), "primary": True}],
        "meta": {
            "resourceType": "User",
            "created": row["created_at"].isoformat() if row.get("created_at") else None,
            "lastModified": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "location": f"/api/v1/scim/v2/Users/{uid}",
        },
    }


async def _get_user_row(db: AsyncSession, tenant_id: str, user_id: str) -> dict | None:
    row = (await db.execute(
        text("""
            SELECT id, tenant_id, email, full_name, role_id, is_active,
                   scim_external_id, created_at, updated_at
            FROM users
            WHERE id = CAST(:uid AS UUID) AND tenant_id = CAST(:tid AS UUID)
        """),
        {"uid": user_id, "tid": tenant_id},
    )).mappings().first()
    return dict(row) if row else None


async def _log_scim(db: AsyncSession, tenant_id: str, token_id: str,
                    operation: str, resource_type: str, resource_id: str | None,
                    external_id: str | None, status: str = "success", detail: dict | None = None):
    import json as _json
    await db.execute(
        text("""
            INSERT INTO scim_sync_log
                (tenant_id, scim_token_id, operation, resource_type,
                 resource_id, external_id, status, detail)
            VALUES
                (CAST(:tid AS UUID), CAST(:stid AS UUID), :op, :rtype,
                 :rid, :eid, :st, CAST(:detail AS JSONB))
        """),
        {
            "tid": tenant_id,
            "stid": token_id,
            "op": operation,
            "rtype": resource_type,
            "rid": resource_id,
            "eid": external_id,
            "st": status,
            "detail": _json.dumps(detail or {}),
        },
    )


# ── SCIM Users ────────────────────────────────────────────────────────────────

@router.get("/v2/Users")
async def scim_list_users(
    request: Request,
    filter: str | None = None,
    startIndex: int = 1,
    count: int = 100,
    scim_ctx=Depends(_get_scim_context),
    db: AsyncSession = Depends(get_raw_db),
):
    tenant_id = str(scim_ctx["tenant_id"])
    where = "WHERE u.tenant_id = CAST(:tid AS UUID)"
    params: dict[str, Any] = {"tid": tenant_id}

    if filter:
        # Minimal SCIM filter: "userName eq <value>" and "externalId eq <value>"
        fl = filter.strip()
        if fl.lower().startswith("username eq "):
            val = fl[12:].strip().strip('"').strip("'")
            where += " AND u.email = :filter_val"
            params["filter_val"] = val
        elif fl.lower().startswith("externalid eq "):
            val = fl[14:].strip().strip('"').strip("'")
            where += " AND u.scim_external_id = :filter_val"
            params["filter_val"] = val

    rows = (await db.execute(
        text(f"""
            SELECT u.id, u.tenant_id, u.email, u.full_name, u.role_id,
                   u.is_active, u.scim_external_id, u.created_at, u.updated_at
            FROM users u
            {where}
            ORDER BY u.created_at
            LIMIT :lim OFFSET :off
        """),
        {**params, "lim": min(count, 200), "off": max(0, startIndex - 1)},
    )).mappings().all()

    total = (await db.execute(
        text(f"SELECT COUNT(*) FROM users u {where}"),
        params,
    )).scalar() or 0

    return {
        "schemas": [_LIST_SCHEMA],
        "totalResults": total,
        "startIndex": startIndex,
        "itemsPerPage": len(rows),
        "Resources": [_user_to_scim(dict(r)) for r in rows],
    }


@router.post("/v2/Users", status_code=status.HTTP_201_CREATED)
async def scim_create_user(
    payload: dict,
    scim_ctx=Depends(_get_scim_context),
    db: AsyncSession = Depends(get_raw_db),
):
    tenant_id = str(scim_ctx["tenant_id"])
    token_id = str(scim_ctx["id"])

    user_name = payload.get("userName") or ""
    if not user_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "userName is required")

    external_id = payload.get("externalId")
    active = payload.get("active", True)

    # Resolve full_name from SCIM name object
    name_obj = payload.get("name") or {}
    full_name = name_obj.get("formatted") or (
        f"{name_obj.get('givenName', '')} {name_obj.get('familyName', '')}".strip()
    ) or user_name

    # Resolve role from roles array (first entry) or default to Viewer (6)
    role_id = 6
    for r in payload.get("roles", []):
        display = (r.get("display") or "").lower()
        if display in _NAME_TO_ROLE:
            role_id = _NAME_TO_ROLE[display]
            break

    # Upsert on email + tenant_id to be idempotent
    existing = (await db.execute(
        text("SELECT id FROM users WHERE email = :email AND tenant_id = CAST(:tid AS UUID)"),
        {"email": user_name, "tid": tenant_id},
    )).first()

    if existing:
        uid = str(existing[0])
        await db.execute(
            text("""
                UPDATE users
                SET full_name = :fn, is_active = :act, scim_external_id = :eid,
                    role_id = :rid, updated_at = now()
                WHERE id = CAST(:uid AS UUID)
            """),
            {"fn": full_name, "act": active, "eid": external_id, "rid": role_id, "uid": uid},
        )
    else:
        uid = str(_uuid.uuid4())
        tmp_password = secrets.token_urlsafe(20)
        await db.execute(
            text("""
                INSERT INTO users
                    (id, tenant_id, email, hashed_password, full_name,
                     role_id, is_active, scim_external_id)
                VALUES
                    (CAST(:uid AS UUID), CAST(:tid AS UUID), :email, :hp, :fn,
                     :rid, :act, :eid)
            """),
            {
                "uid": uid,
                "tid": tenant_id,
                "email": user_name,
                "hp": hash_password(tmp_password),
                "fn": full_name,
                "rid": role_id,
                "act": active,
                "eid": external_id,
            },
        )

    await _log_scim(db, tenant_id, token_id, "CREATE", "User", uid, external_id)
    row = await _get_user_row(db, tenant_id, uid)
    await db.commit()
    return _user_to_scim(row)


@router.get("/v2/Users/{user_id}")
async def scim_get_user(
    user_id: str,
    scim_ctx=Depends(_get_scim_context),
    db: AsyncSession = Depends(get_raw_db),
):
    tenant_id = str(scim_ctx["tenant_id"])
    row = await _get_user_row(db, tenant_id, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return _user_to_scim(row)


@router.put("/v2/Users/{user_id}")
async def scim_replace_user(
    user_id: str,
    payload: dict,
    scim_ctx=Depends(_get_scim_context),
    db: AsyncSession = Depends(get_raw_db),
):
    tenant_id = str(scim_ctx["tenant_id"])
    token_id = str(scim_ctx["id"])
    row = await _get_user_row(db, tenant_id, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    external_id = payload.get("externalId", row.get("scim_external_id"))
    active = payload.get("active", True)
    name_obj = payload.get("name") or {}
    full_name = name_obj.get("formatted") or (
        f"{name_obj.get('givenName', '')} {name_obj.get('familyName', '')}".strip()
    ) or row["email"]

    role_id = row["role_id"]
    for r in payload.get("roles", []):
        display = (r.get("display") or "").lower()
        if display in _NAME_TO_ROLE:
            role_id = _NAME_TO_ROLE[display]
            break

    await db.execute(
        text("""
            UPDATE users
            SET full_name = :fn, is_active = :act, scim_external_id = :eid,
                role_id = :rid, updated_at = now()
            WHERE id = CAST(:uid AS UUID) AND tenant_id = CAST(:tid AS UUID)
        """),
        {"fn": full_name, "act": active, "eid": external_id, "rid": role_id,
         "uid": user_id, "tid": tenant_id},
    )
    await _log_scim(db, tenant_id, token_id, "REPLACE", "User", user_id, external_id)
    row = await _get_user_row(db, tenant_id, user_id)
    await db.commit()
    return _user_to_scim(row)


@router.patch("/v2/Users/{user_id}")
async def scim_patch_user(
    user_id: str,
    payload: dict,
    scim_ctx=Depends(_get_scim_context),
    db: AsyncSession = Depends(get_raw_db),
):
    tenant_id = str(scim_ctx["tenant_id"])
    token_id = str(scim_ctx["id"])
    row = await _get_user_row(db, tenant_id, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    updates: dict[str, Any] = {}
    for op in payload.get("Operations", []):
        op_type = (op.get("op") or "").lower()
        path = (op.get("path") or "").lower()
        value = op.get("value")

        if op_type in ("replace", "add"):
            if path == "active":
                updates["is_active"] = bool(value)
            elif path == "externalid":
                updates["scim_external_id"] = value
            elif path in ("name.givenname", "name.familyname", "name.formatted"):
                pass  # handled below via full_name composite
            elif path == "" and isinstance(value, dict):
                # Valueless path — entire object replacement
                if "active" in value:
                    updates["is_active"] = bool(value["active"])
                if "externalId" in value:
                    updates["scim_external_id"] = value["externalId"]
                name_obj = value.get("name") or {}
                if name_obj:
                    fn = name_obj.get("formatted") or (
                        f"{name_obj.get('givenName', '')} {name_obj.get('familyName', '')}".strip()
                    )
                    if fn:
                        updates["full_name"] = fn
        elif op_type == "remove":
            if path == "externalid":
                updates["scim_external_id"] = None

    if not updates:
        row = await _get_user_row(db, tenant_id, user_id)
        return _user_to_scim(row)

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(
        text(f"UPDATE users SET {set_clause}, updated_at = now() WHERE id = CAST(:uid AS UUID) AND tenant_id = CAST(:tid AS UUID)"),
        {**updates, "uid": user_id, "tid": tenant_id},
    )
    await _log_scim(db, tenant_id, token_id, "PATCH", "User", user_id,
                    updates.get("scim_external_id", row.get("scim_external_id")))
    row = await _get_user_row(db, tenant_id, user_id)
    await db.commit()
    return _user_to_scim(row)


@router.delete("/v2/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def scim_delete_user(
    user_id: str,
    scim_ctx=Depends(_get_scim_context),
    db: AsyncSession = Depends(get_raw_db),
):
    tenant_id = str(scim_ctx["tenant_id"])
    token_id = str(scim_ctx["id"])
    row = await _get_user_row(db, tenant_id, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    await db.execute(
        text("UPDATE users SET is_active = FALSE, updated_at = now() WHERE id = CAST(:uid AS UUID) AND tenant_id = CAST(:tid AS UUID)"),
        {"uid": user_id, "tid": tenant_id},
    )
    await _log_scim(db, tenant_id, token_id, "DELETE", "User", user_id,
                    row.get("scim_external_id"))
    await db.commit()


# ── SCIM Groups (roles) ───────────────────────────────────────────────────────

@router.get("/v2/Groups")
async def scim_list_groups(
    scim_ctx=Depends(_get_scim_context),
    db: AsyncSession = Depends(get_raw_db),
):
    """Expose platform roles as read-only SCIM groups."""
    tenant_id = str(scim_ctx["tenant_id"])
    rows = (await db.execute(
        text("SELECT id, code, name FROM roles ORDER BY id")
    )).mappings().all()

    resources = []
    for r in rows:
        member_rows = (await db.execute(
            text("SELECT id FROM users WHERE role_id = :rid AND tenant_id = CAST(:tid AS UUID) AND is_active = TRUE"),
            {"rid": r["id"], "tid": tenant_id},
        )).all()
        resources.append({
            "schemas": [_GROUP_SCHEMA],
            "id": str(r["id"]),
            "displayName": r["name"],
            "members": [{"value": str(m[0])} for m in member_rows],
            "meta": {"resourceType": "Group", "location": f"/api/v1/scim/v2/Groups/{r['id']}"},
        })

    return {
        "schemas": [_LIST_SCHEMA],
        "totalResults": len(resources),
        "startIndex": 1,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


@router.post("/v2/Groups", status_code=status.HTTP_201_CREATED)
async def scim_create_group(
    payload: dict,
    scim_ctx=Depends(_get_scim_context),
):
    """Groups map to fixed roles — creation is not supported; return the closest role."""
    display = (payload.get("displayName") or "").lower()
    role_id = _NAME_TO_ROLE.get(display, 6)
    return {
        "schemas": [_GROUP_SCHEMA],
        "id": str(role_id),
        "displayName": _ROLE_NAMES.get(role_id, "Viewer"),
        "members": [],
        "meta": {"resourceType": "Group", "location": f"/api/v1/scim/v2/Groups/{role_id}"},
    }
