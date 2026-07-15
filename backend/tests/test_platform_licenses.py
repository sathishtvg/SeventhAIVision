"""Platform product licensing tests.

Covers:
1. GET /api/v1/platform/products — lists seeded products with modules
2. GET /api/v1/platform/tenants/{id}/products — all products, licensed state per tenant
3. POST assign product to tenant
4. DELETE revoke product
5. PUT toggle module within product
6. Auth login response includes tenant info + licensed_products
7. GET /api/v1/auth/resolve-tenant/{subdomain} — subdomain resolution
8. Tenant CRUD now exposes subdomain/branding/timezone fields
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token


def _super_admin_auth(tenant_id, user_id):
    token = create_access_token(str(user_id), str(tenant_id), role_id=1)
    return {"Authorization": f"Bearer {token}"}


async def _seed_super_admin(db):
    """Create a tenant + super-admin user. Returns (tenant_id, user_id, slug)."""
    from app.core.security import hash_password
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"sa-{tenant_id.hex[:8]}"
    await db.execute(
        text("INSERT INTO tenants (id, name, slug, subdomain) VALUES (:id, :name, :slug, :sub)"),
        {"id": tenant_id, "name": "SuperAdmin Co", "slug": slug, "sub": slug},
    )
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)}
    )
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
            "VALUES (:id, :tid, 1, :email, :pw)"
        ),
        {"id": user_id, "tid": tenant_id, "email": f"sa@{slug}.com",
         "pw": hash_password("superpass")},
    )
    await db.commit()
    return tenant_id, user_id, slug


async def _seed_target_tenant(db):
    """Create a plain tenant to be the target of licensing operations."""
    tenant_id = uuid.uuid4()
    slug = f"target-{tenant_id.hex[:8]}"
    await db.execute(
        text("INSERT INTO tenants (id, name, slug, subdomain) VALUES (:id, :name, :slug, :sub)"),
        {"id": tenant_id, "name": "Target Co", "slug": slug, "sub": slug},
    )
    await db.commit()
    return tenant_id, slug


# ─── 1. Product catalog ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_products_returns_seeded_catalog(app_client):
    """Product catalog is seeded by migration 0022; no auth required."""
    resp = await app_client.get("/api/v1/platform/products")
    assert resp.status_code == 200
    products = resp.json()
    ids = [p["id"] for p in products]
    assert "seventh_ai_vision" in ids
    assert "shift_secure" in ids

    vision = next(p for p in products if p["id"] == "seventh_ai_vision")
    module_codes = [m["module_code"] for m in vision["modules"]]
    assert "vms" in module_codes
    assert "detections" in module_codes


@pytest.mark.asyncio
async def test_get_single_product(app_client):
    resp = await app_client.get("/api/v1/platform/products/seventh_ai_vision")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "seventh_ai_vision"
    assert len(data["modules"]) >= 6


@pytest.mark.asyncio
async def test_get_nonexistent_product_404(app_client):
    resp = await app_client.get("/api/v1/platform/products/nonexistent_product")
    assert resp.status_code == 404


# ─── 2. Tenant product listing (all products, licensed state) ─────────────────

@pytest.mark.asyncio
async def test_list_tenant_products_not_licensed(app_client, admin_session):
    """A fresh tenant has no licensed products — all show is_licensed=False."""
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    resp = await app_client.get(
        f"/api/v1/platform/tenants/{target_tid}/products",
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    for p in data:
        assert p["is_licensed"] is False
        assert p["is_enabled"] is False


@pytest.mark.asyncio
async def test_list_tenant_products_nonexistent_tenant(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    resp = await app_client.get(
        f"/api/v1/platform/tenants/{uuid.uuid4()}/products",
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 404


# ─── 3. Assign product to tenant ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assign_product_to_tenant(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    resp = await app_client.post(
        f"/api/v1/platform/tenants/{target_tid}/products/seventh_ai_vision",
        json={"is_enabled": True},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_enabled"] is True

    # Verify it now shows as licensed
    list_resp = await app_client.get(
        f"/api/v1/platform/tenants/{target_tid}/products",
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    products = list_resp.json()
    vision = next(p for p in products if p["product_id"] == "seventh_ai_vision")
    assert vision["is_licensed"] is True
    assert vision["is_enabled"] is True
    assert len(vision["modules"]) >= 6


@pytest.mark.asyncio
async def test_assign_nonexistent_product_404(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    resp = await app_client.post(
        f"/api/v1/platform/tenants/{target_tid}/products/nonexistent",
        json={"is_enabled": True},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_assign_product_non_super_admin_403(app_client, admin_session):
    """Only super_admin (role_id=1) can assign products."""
    from tests.test_rbac import _seed_user_with_role
    tid, uid = await _seed_user_with_role(admin_session, role_id=2)  # admin
    target_tid, _ = await _seed_target_tenant(admin_session)

    resp = await app_client.post(
        f"/api/v1/platform/tenants/{target_tid}/products/seventh_ai_vision",
        json={"is_enabled": True},
        headers={"Authorization": f"Bearer {create_access_token(str(uid), str(tid), role_id=2)}"},
    )
    assert resp.status_code == 403


# ─── 4. Revoke product ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_product(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    # Assign first
    await app_client.post(
        f"/api/v1/platform/tenants/{target_tid}/products/shift_secure",
        json={"is_enabled": True},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )

    # Revoke
    resp = await app_client.delete(
        f"/api/v1/platform/tenants/{target_tid}/products/shift_secure",
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 200
    assert resp.json()["is_enabled"] is False


@pytest.mark.asyncio
async def test_revoke_unlicensed_product_404(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    resp = await app_client.delete(
        f"/api/v1/platform/tenants/{target_tid}/products/seventh_ai_vision",
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 404


# ─── 5. Module toggle ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_toggle_module_within_licensed_product(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    # Assign seventh_ai_vision
    await app_client.post(
        f"/api/v1/platform/tenants/{target_tid}/products/seventh_ai_vision",
        json={"is_enabled": True},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )

    # Disable the client_portal module
    resp = await app_client.put(
        f"/api/v1/platform/tenants/{target_tid}/products/seventh_ai_vision/modules/client_portal",
        json={"is_enabled": False},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 200
    assert resp.json()["is_enabled"] is False

    # Verify the override appears in the listing
    list_resp = await app_client.get(
        f"/api/v1/platform/tenants/{target_tid}/products",
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    vision = next(p for p in list_resp.json() if p["product_id"] == "seventh_ai_vision")
    portal_mod = next(m for m in vision["modules"] if m["module_code"] == "client_portal")
    assert portal_mod["is_enabled"] is False

    # Other modules still enabled by default
    vms_mod = next(m for m in vision["modules"] if m["module_code"] == "vms")
    assert vms_mod["is_enabled"] is True


@pytest.mark.asyncio
async def test_toggle_module_on_unlicensed_product_400(app_client, admin_session):
    """Cannot toggle a module if the product isn't licensed for this tenant."""
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    resp = await app_client.put(
        f"/api/v1/platform/tenants/{target_tid}/products/seventh_ai_vision/modules/vms",
        json={"is_enabled": False},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_toggle_nonexistent_module_404(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    await app_client.post(
        f"/api/v1/platform/tenants/{target_tid}/products/seventh_ai_vision",
        json={"is_enabled": True},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )

    resp = await app_client.put(
        f"/api/v1/platform/tenants/{target_tid}/products/seventh_ai_vision/modules/nonexistent",
        json={"is_enabled": False},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 404


# ─── 6. Login response includes tenant info + licensed_products ───────────────

@pytest.mark.asyncio
async def test_login_response_includes_tenant_and_products(admin_session):
    """Login service returns tenant info and licensed_products array.
    Calls auth_service directly to avoid consuming rate-limit budget shared by
    other tests that hit the /api/v1/auth/login HTTP endpoint.
    """
    from app.core.security import hash_password
    from app.services.auth_service import authenticate_and_issue_tokens

    tenant_id = uuid.uuid4()
    slug = f"login-test-{tenant_id.hex[:8]}"
    password = "login-pass-123"

    await admin_session.execute(
        text(
            "INSERT INTO tenants (id, name, slug, subdomain, timezone) "
            "VALUES (:id, :name, :slug, :sub, 'Asia/Singapore')"
        ),
        {"id": tenant_id, "name": "Login Test Co", "slug": slug, "sub": slug},
    )
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)}
    )
    await admin_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
            "VALUES (:id, :tid, 2, :email, :pw)"
        ),
        {"id": uuid.uuid4(), "tid": tenant_id, "email": f"user@{slug}.com",
         "pw": hash_password(password)},
    )
    await admin_session.commit()

    result = await authenticate_and_issue_tokens(
        tenant_slug=slug,
        email=f"user@{slug}.com",
        password=password,
    )
    assert result.access_token
    assert result.tenant is not None
    assert result.tenant.slug == slug
    assert result.tenant.timezone == "Asia/Singapore"
    assert isinstance(result.licensed_products, list)


# ─── 7. Subdomain resolution ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_tenant_by_subdomain(app_client, admin_session):
    tenant_id = uuid.uuid4()
    subdomain = f"testco-{tenant_id.hex[:8]}"
    await admin_session.execute(
        text(
            "INSERT INTO tenants (id, name, slug, subdomain) "
            "VALUES (:id, :name, :slug, :sub)"
        ),
        {"id": tenant_id, "name": "Test Company", "slug": subdomain, "sub": subdomain},
    )
    await admin_session.commit()

    resp = await app_client.get(f"/api/v1/auth/resolve-tenant/{subdomain}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["subdomain"] == subdomain
    assert data["name"] == "Test Company"


@pytest.mark.asyncio
async def test_resolve_nonexistent_subdomain_404(app_client):
    resp = await app_client.get("/api/v1/auth/resolve-tenant/no-such-tenant-xyz999")
    assert resp.status_code == 404


# ─── 8. Tenant CRUD exposes subdomain / branding / timezone ──────────────────

@pytest.mark.asyncio
async def test_create_tenant_with_subdomain_and_branding(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)

    unique = uuid.uuid4().hex[:8]
    resp = await app_client.post(
        "/api/v1/tenants",
        json={
            "name": "Branded Corp",
            "slug": f"branded-{unique}",
            "subdomain": f"brandedcorp-{unique}",
            "timezone": "Asia/Kuala_Lumpur",
            "branding": {"primary_color": "#FF5500", "logo_url": "https://example.com/logo.png"},
        },
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["subdomain"] == f"brandedcorp-{unique}"
    assert data["timezone"] == "Asia/Kuala_Lumpur"
    assert data["branding"]["primary_color"] == "#FF5500"


@pytest.mark.asyncio
async def test_update_tenant_branding(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, _ = await _seed_target_tenant(admin_session)

    resp = await app_client.put(
        f"/api/v1/tenants/{target_tid}",
        json={"branding": {"primary_color": "#123456"}},
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["branding"]["primary_color"] == "#123456"


@pytest.mark.asyncio
async def test_get_tenant_exposes_subdomain_field(app_client, admin_session):
    sa_tid, sa_uid, _ = await _seed_super_admin(admin_session)
    target_tid, target_slug = await _seed_target_tenant(admin_session)

    resp = await app_client.get(
        f"/api/v1/tenants/{target_tid}",
        headers=_super_admin_auth(sa_tid, sa_uid),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "subdomain" in data
    assert "branding" in data
    assert "timezone" in data
