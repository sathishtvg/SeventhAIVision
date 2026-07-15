"""Gap 9 — SCIM 2.0 User Provisioning tests.

25 tests:
  - ServiceProviderConfig + Schemas (2)
  - Token management: create, list, revoke (4)
  - SCIM User: list, create, get, put, patch-deactivate, patch-reactivate, delete (7)
  - SCIM User: filter by userName, filter by externalId (2)
  - SCIM User: idempotent create (upsert) (1)
  - SCIM Groups: list, create noop (2)
  - SCIM auth: missing token 401, invalid token 401 (2)
  - Sync log written on user operations (1)
  - scim:manage permission gate (2)
  - Patch operations: remove externalId, valueless path (2)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password


# ── Fixtures ──────────────────────────────────────────────────────────────────

@dataclass
class _ScimCtx:
    tenant_id: uuid.UUID
    user_id: uuid.UUID

    def admin_headers(self) -> dict:
        token = create_access_token(str(self.user_id), str(self.tenant_id), role_id=2)
        return {"Authorization": f"Bearer {token}"}

    def operator_headers(self) -> dict:
        """Operator role (4) — lacks scim:manage."""
        op_uid = uuid.uuid4()  # random; no DB row needed — 403 fires before INSERT
        token = create_access_token(str(op_uid), str(self.tenant_id), role_id=4)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def s_ctx(admin_session) -> _ScimCtx:
    """Seed a dedicated tenant + admin user for SCIM tests."""
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    slug = f"scim-{tid.hex[:8]}"
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tid, "name": "SCIM Tenant", "slug": slug},
    )
    await admin_session.execute(
        text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
             "VALUES (:id, :tid, 2, :email, :pw)"),
        {"id": uid, "tid": tid, "email": f"scimadmin-{tid.hex[:8]}@test.local",
         "pw": hash_password("scim-test-pass")},
    )
    await admin_session.commit()
    return _ScimCtx(tenant_id=tid, user_id=uid)


@pytest_asyncio.fixture
async def scim_token(app_client: AsyncClient, s_ctx: _ScimCtx) -> str:
    """Create a SCIM token via the management API; return raw bearer value."""
    resp = await app_client.post(
        "/api/v1/scim/tokens",
        headers=s_ctx.admin_headers(),
        json={"name": "test-idp"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _scim_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── ServiceProviderConfig + Schemas ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_service_provider_config(app_client: AsyncClient):
    """GET /scim/v2/ServiceProviderConfig → 200 with patch.supported=True."""
    resp = await app_client.get("/api/v1/scim/v2/ServiceProviderConfig")
    assert resp.status_code == 200
    body = resp.json()
    assert body["patch"]["supported"] is True
    assert body["filter"]["supported"] is True


@pytest.mark.asyncio
async def test_schemas(app_client: AsyncClient):
    """GET /scim/v2/Schemas → returns User and Group schema definitions."""
    resp = await app_client.get("/api/v1/scim/v2/Schemas")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["Resources"]}
    assert "urn:ietf:params:scim:schemas:core:2.0:User" in ids
    assert "urn:ietf:params:scim:schemas:core:2.0:Group" in ids


# ── Token management ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_token_requires_scim_manage(app_client: AsyncClient, s_ctx):
    """Operator (role 4) lacks scim:manage → 403."""
    resp = await app_client.post(
        "/api/v1/scim/tokens",
        headers=s_ctx.operator_headers(),
        json={"name": "bad"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_token_requires_auth(client: AsyncClient):
    """No JWT → 401."""
    resp = await client.post("/api/v1/scim/tokens", json={"name": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_token_returns_raw_token(app_client: AsyncClient, s_ctx):
    """Admin creates a token → response includes raw bearer value."""
    resp = await app_client.post(
        "/api/v1/scim/tokens",
        headers=s_ctx.admin_headers(),
        json={"name": "okta-prod"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "token" in body
    assert len(body["token"]) > 20
    assert "id" in body


@pytest.mark.asyncio
async def test_list_and_revoke_tokens(app_client: AsyncClient, s_ctx, scim_token):
    """Create a token, list it, revoke it, confirm it no longer authenticates."""
    resp = await app_client.get("/api/v1/scim/tokens", headers=s_ctx.admin_headers())
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert len(ids) >= 1

    token_id = ids[0]
    rev = await app_client.delete(
        f"/api/v1/scim/tokens/{token_id}",
        headers=s_ctx.admin_headers(),
    )
    assert rev.status_code == 204

    # Revoked token should no longer authenticate SCIM requests
    resp2 = await app_client.get(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
    )
    assert resp2.status_code == 401


# ── SCIM auth guard ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scim_no_bearer_401(app_client: AsyncClient):
    """Missing Authorization header → 401."""
    resp = await app_client.get("/api/v1/scim/v2/Users")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_scim_invalid_bearer_401(app_client: AsyncClient):
    """Wrong bearer value → 401."""
    resp = await app_client.get(
        "/api/v1/scim/v2/Users",
        headers={"Authorization": "Bearer totally_wrong_token"},
    )
    assert resp.status_code == 401


# ── SCIM Users ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scim_list_users_returns_list_response(
    app_client: AsyncClient, s_ctx, scim_token
):
    """GET /Users → ListResponse with correct schema."""
    resp = await app_client.get(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "urn:ietf:params:scim:api:messages:2.0:ListResponse" in body["schemas"]
    assert "totalResults" in body
    assert "Resources" in body


@pytest.mark.asyncio
async def test_scim_create_user(app_client: AsyncClient, s_ctx, scim_token):
    """POST /Users creates a user; response has correct SCIM shape."""
    email = f"scim.user.{uuid.uuid4().hex[:8]}@example.com"
    resp = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": email,
            "name": {"givenName": "Alice", "familyName": "Smith"},
            "active": True,
            "externalId": "ext-alice-001",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["userName"] == email
    assert body["active"] is True
    assert body["id"]


@pytest.mark.asyncio
async def test_scim_create_user_stores_external_id(
    app_client: AsyncClient, s_ctx, scim_token, admin_session
):
    """externalId from IdP is stored in scim_external_id column."""
    ext_id = f"ext-{uuid.uuid4().hex[:12]}"
    email = f"scim.ext.{uuid.uuid4().hex[:8]}@example.com"
    resp = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True, "externalId": ext_id},
    )
    assert resp.status_code == 201
    uid = resp.json()["id"]

    row = (await admin_session.execute(
        text("SELECT scim_external_id FROM users WHERE id = CAST(:uid AS UUID)"),
        {"uid": uid},
    )).first()
    assert row is not None
    assert row[0] == ext_id


@pytest.mark.asyncio
async def test_scim_get_user(app_client: AsyncClient, s_ctx, scim_token):
    """GET /Users/{id} returns the user that was just created."""
    email = f"scim.get.{uuid.uuid4().hex[:8]}@example.com"
    create_resp = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True},
    )
    assert create_resp.status_code == 201
    uid = create_resp.json()["id"]

    get_resp = await app_client.get(
        f"/api/v1/scim/v2/Users/{uid}",
        headers=_scim_headers(scim_token),
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == uid
    assert get_resp.json()["userName"] == email


@pytest.mark.asyncio
async def test_scim_get_user_404(app_client: AsyncClient, s_ctx, scim_token):
    """GET /Users/{nonexistent} → 404."""
    resp = await app_client.get(
        f"/api/v1/scim/v2/Users/{uuid.uuid4()}",
        headers=_scim_headers(scim_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_scim_replace_user(app_client: AsyncClient, s_ctx, scim_token):
    """PUT /Users/{id} fully replaces the user; name updated."""
    email = f"scim.put.{uuid.uuid4().hex[:8]}@example.com"
    create = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "name": {"givenName": "Old"}, "active": True},
    )
    uid = create.json()["id"]

    put = await app_client.put(
        f"/api/v1/scim/v2/Users/{uid}",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email,
              "name": {"givenName": "New", "familyName": "Name"},
              "active": True},
    )
    assert put.status_code == 200
    assert "New" in put.json()["name"]["givenName"]


@pytest.mark.asyncio
async def test_scim_patch_deactivate_user(
    app_client: AsyncClient, s_ctx, scim_token, admin_session
):
    """PATCH /Users/{id} with active=false → user deactivated in DB."""
    email = f"scim.patch.{uuid.uuid4().hex[:8]}@example.com"
    create = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True},
    )
    uid = create.json()["id"]

    patch = await app_client.patch(
        f"/api/v1/scim/v2/Users/{uid}",
        headers=_scim_headers(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
    )
    assert patch.status_code == 200
    assert patch.json()["active"] is False

    row = (await admin_session.execute(
        text("SELECT is_active FROM users WHERE id = CAST(:uid AS UUID)"),
        {"uid": uid},
    )).first()
    assert row[0] is False


@pytest.mark.asyncio
async def test_scim_patch_reactivate_user(app_client: AsyncClient, s_ctx, scim_token):
    """PATCH with active=true re-enables a deactivated user."""
    email = f"scim.react.{uuid.uuid4().hex[:8]}@example.com"
    create = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": False},
    )
    uid = create.json()["id"]

    patch = await app_client.patch(
        f"/api/v1/scim/v2/Users/{uid}",
        headers=_scim_headers(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": True}],
        },
    )
    assert patch.status_code == 200
    assert patch.json()["active"] is True


@pytest.mark.asyncio
async def test_scim_patch_remove_external_id(
    app_client: AsyncClient, s_ctx, scim_token, admin_session
):
    """PATCH op=remove path=externalId clears the scim_external_id."""
    email = f"scim.rmext.{uuid.uuid4().hex[:8]}@example.com"
    create = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True, "externalId": "ext-to-remove"},
    )
    uid = create.json()["id"]

    patch = await app_client.patch(
        f"/api/v1/scim/v2/Users/{uid}",
        headers=_scim_headers(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "remove", "path": "externalId"}],
        },
    )
    assert patch.status_code == 200

    row = (await admin_session.execute(
        text("SELECT scim_external_id FROM users WHERE id = CAST(:uid AS UUID)"),
        {"uid": uid},
    )).first()
    assert row[0] is None


@pytest.mark.asyncio
async def test_scim_patch_valueless_path(app_client: AsyncClient, s_ctx, scim_token):
    """PATCH with empty path and value object updates active + externalId together."""
    email = f"scim.vpath.{uuid.uuid4().hex[:8]}@example.com"
    create = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True},
    )
    uid = create.json()["id"]

    patch = await app_client.patch(
        f"/api/v1/scim/v2/Users/{uid}",
        headers=_scim_headers(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "", "value": {"active": False, "externalId": "ext-vpath"}}],
        },
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["active"] is False
    assert body["externalId"] == "ext-vpath"


@pytest.mark.asyncio
async def test_scim_delete_user_deactivates(
    app_client: AsyncClient, s_ctx, scim_token, admin_session
):
    """DELETE /Users/{id} sets is_active=false (soft delete)."""
    email = f"scim.del.{uuid.uuid4().hex[:8]}@example.com"
    create = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True},
    )
    uid = create.json()["id"]

    del_resp = await app_client.delete(
        f"/api/v1/scim/v2/Users/{uid}",
        headers=_scim_headers(scim_token),
    )
    assert del_resp.status_code == 204

    row = (await admin_session.execute(
        text("SELECT is_active FROM users WHERE id = CAST(:uid AS UUID)"),
        {"uid": uid},
    )).first()
    assert row[0] is False


@pytest.mark.asyncio
async def test_scim_filter_by_username(app_client: AsyncClient, s_ctx, scim_token):
    """GET /Users?filter=userName eq <email> → returns only matching user."""
    email = f"scim.filter.{uuid.uuid4().hex[:8]}@example.com"
    await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True},
    )

    resp = await app_client.get(
        f'/api/v1/scim/v2/Users?filter=userName eq "{email}"',
        headers=_scim_headers(scim_token),
    )
    assert resp.status_code == 200
    resources = resp.json()["Resources"]
    assert len(resources) == 1
    assert resources[0]["userName"] == email


@pytest.mark.asyncio
async def test_scim_filter_by_external_id(app_client: AsyncClient, s_ctx, scim_token):
    """GET /Users?filter=externalId eq <ext> → returns only matching user."""
    ext_id = f"ext-filter-{uuid.uuid4().hex[:12]}"
    email = f"scim.eid.{uuid.uuid4().hex[:8]}@example.com"
    await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True, "externalId": ext_id},
    )

    resp = await app_client.get(
        f'/api/v1/scim/v2/Users?filter=externalId eq "{ext_id}"',
        headers=_scim_headers(scim_token),
    )
    assert resp.status_code == 200
    resources = resp.json()["Resources"]
    assert any(r["externalId"] == ext_id for r in resources)


@pytest.mark.asyncio
async def test_scim_create_user_idempotent(app_client: AsyncClient, s_ctx, scim_token):
    """POST /Users twice with same userName → upsert, no duplicate, same id."""
    email = f"scim.idem.{uuid.uuid4().hex[:8]}@example.com"
    r1 = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True, "name": {"givenName": "First"}},
    )
    r2 = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True, "name": {"givenName": "Updated"}},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["name"]["givenName"] == "Updated"


# ── SCIM Groups ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scim_list_groups(app_client: AsyncClient, s_ctx, scim_token):
    """GET /Groups → returns all platform roles as groups."""
    resp = await app_client.get(
        "/api/v1/scim/v2/Groups",
        headers=_scim_headers(scim_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalResults"] >= 6
    names = {g["displayName"] for g in body["Resources"]}
    assert "Admin" in names


@pytest.mark.asyncio
async def test_scim_create_group_returns_closest_role(
    app_client: AsyncClient, s_ctx, scim_token
):
    """POST /Groups with a role displayName → returns that role (no new row created)."""
    resp = await app_client.post(
        "/api/v1/scim/v2/Groups",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
              "displayName": "Admin"},
    )
    assert resp.status_code == 201
    assert resp.json()["displayName"] == "Admin"


# ── Sync log ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scim_sync_log_written_on_create(
    app_client: AsyncClient, s_ctx, scim_token, admin_session
):
    """Creating a user via SCIM writes a row to scim_sync_log."""
    email = f"scim.log.{uuid.uuid4().hex[:8]}@example.com"
    create = await app_client.post(
        "/api/v1/scim/v2/Users",
        headers=_scim_headers(scim_token),
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
              "userName": email, "active": True},
    )
    assert create.status_code == 201
    uid = create.json()["id"]

    log = (await admin_session.execute(
        text("""
            SELECT operation, resource_type, resource_id
            FROM scim_sync_log
            WHERE tenant_id = CAST(:tid AS UUID) AND resource_id = :rid
        """),
        {"tid": str(s_ctx.tenant_id), "rid": uid},
    )).first()
    assert log is not None
    assert log[0] == "CREATE"
    assert log[1] == "User"
