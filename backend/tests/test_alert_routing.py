"""Gap 82 — Site+shift-aware alert routing (isolated-tenant)

Tests app/services/alert_routing.py, the site_ids filter in
notifications/dispatch.py, per-user push-token registration in users.py,
and site_ids on the notification-rules CRUD.

Routing semantics under test:
  resolve_push_targets(session, camera_id) →
    - camera missing / camera has no site           → None (tenant-wide fallback)
    - site found: active-shift guards at that site
      ∪ site-assigned users with roles 3/4/5        → list of user_ids
    - site found but zero targets                   → None (fallback)
    - client (role 7) assigned to site              → NOT targeted

Sections:
  A — resolve_push_targets (6 tests)
  B — Rule site_ids matching in _load_matching_rules (3 tests)
  C — Per-user push token registration API (2 tests)
  D — Rules CRUD site_ids round-trip (2 tests)
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

APP_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://svc_app:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"rte-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Routing Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Routing Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"rte-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_extra_user(tenant_id: uuid.UUID, role_id: int) -> uuid.UUID:
    user_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Routed User', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"rte-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return user_id


async def _seed_site(tenant_id: uuid.UUID, name: str) -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)"),
            {"id": site_id, "tid": tenant_id, "name": name},
        )
        await s.commit()
    await engine.dispose()
    return site_id


async def _seed_camera(tenant_id: uuid.UUID, site_id: uuid.UUID | None) -> uuid.UUID:
    camera_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:id, :tid, :sid, 'Route Cam')"),
            {"id": camera_id, "tid": tenant_id, "sid": site_id},
        )
        await s.commit()
    await engine.dispose()
    return camera_id


async def _seed_active_shift(tenant_id: uuid.UUID, site_id: uuid.UUID,
                             guard_user_id: uuid.UUID) -> uuid.UUID:
    shift_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, "
                "scheduled_start, scheduled_end, actual_start, status) "
                "VALUES (:id, :tid, :sid, :gid, now() - interval '1 hour', "
                "now() + interval '7 hours', now() - interval '1 hour', 'active')"
            ),
            {"id": shift_id, "tid": tenant_id, "sid": site_id, "gid": guard_user_id},
        )
        await s.commit()
    await engine.dispose()
    return shift_id


async def _assign_site(tenant_id: uuid.UUID, user_id: uuid.UUID, site_id: uuid.UUID):
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:uid, :sid, :tid)"),
            {"uid": user_id, "sid": site_id, "tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()


async def _resolve_targets(tenant_id: uuid.UUID, camera_id) -> list[str] | None:
    """Run resolve_push_targets under the app role with the tenant GUC set."""
    from app.services.alert_routing import resolve_push_targets

    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        result = await resolve_push_targets(s, str(camera_id) if camera_id else None)
    await engine.dispose()
    return result


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. resolve_push_targets ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rte_active_shift_guard_targeted():
    """Guard on active shift at the alert's site is a push target."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    guard_id = await _seed_extra_user(tenant_id, role_id=5)
    site_id = await _seed_site(tenant_id, "Routed Site")
    camera_id = await _seed_camera(tenant_id, site_id)
    await _seed_active_shift(tenant_id, site_id, guard_id)
    targets = await _resolve_targets(tenant_id, camera_id)
    assert targets == [str(guard_id)]


@pytest.mark.asyncio
async def test_rte_site_assigned_operational_user_targeted():
    """Supervisor assigned to the site (no shift) is also a push target."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    sup_id = await _seed_extra_user(tenant_id, role_id=3)
    site_id = await _seed_site(tenant_id, "Sup Site")
    camera_id = await _seed_camera(tenant_id, site_id)
    await _assign_site(tenant_id, sup_id, site_id)
    targets = await _resolve_targets(tenant_id, camera_id)
    assert targets == [str(sup_id)]


@pytest.mark.asyncio
async def test_rte_shift_guard_and_assigned_user_deduplicated():
    """A guard both on shift AND site-assigned appears once (UNION dedup)."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    guard_id = await _seed_extra_user(tenant_id, role_id=5)
    site_id = await _seed_site(tenant_id, "Dedup Site")
    camera_id = await _seed_camera(tenant_id, site_id)
    await _seed_active_shift(tenant_id, site_id, guard_id)
    await _assign_site(tenant_id, guard_id, site_id)
    targets = await _resolve_targets(tenant_id, camera_id)
    assert targets == [str(guard_id)]


@pytest.mark.asyncio
async def test_rte_no_site_camera_returns_none():
    """Camera without a site → None (tenant-wide fallback)."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    camera_id = await _seed_camera(tenant_id, None)
    targets = await _resolve_targets(tenant_id, camera_id)
    assert targets is None


@pytest.mark.asyncio
async def test_rte_site_with_no_guards_returns_none():
    """Site exists but nobody on shift / assigned → None (fallback)."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id, "Empty Site")
    camera_id = await _seed_camera(tenant_id, site_id)
    targets = await _resolve_targets(tenant_id, camera_id)
    assert targets is None


@pytest.mark.asyncio
async def test_rte_client_assigned_to_site_not_targeted():
    """Client (role 7) and viewer (role 6) assigned to the site are NOT
    push targets — only operational roles 3/4/5 are routed."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    client_id = await _seed_extra_user(tenant_id, role_id=7)
    viewer_id = await _seed_extra_user(tenant_id, role_id=6)
    site_id = await _seed_site(tenant_id, "Client Site")
    camera_id = await _seed_camera(tenant_id, site_id)
    await _assign_site(tenant_id, client_id, site_id)
    await _assign_site(tenant_id, viewer_id, site_id)
    targets = await _resolve_targets(tenant_id, camera_id)
    assert targets is None


# ─── B. Rule site_ids matching ────────────────────────────────────────────────

async def _load_rules_for(tenant_id: uuid.UUID, alert_site_id: str,
                          severity: str = "high") -> list:
    from app.notifications.dispatch import _load_matching_rules

    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        rules = await _load_matching_rules(
            s, str(tenant_id),
            event_type="alert_created",
            event_severity=severity,
            alert_module="intrusion",
            alert_code="intrusion.zone_breach",
            alert_site_id=alert_site_id,
        )
    await engine.dispose()
    return rules


async def _seed_channel_and_rule(tenant_id: uuid.UUID, site_ids: list[str]) -> uuid.UUID:
    """Insert an active webhook channel + rule with the given site_ids filter."""
    import json as _json

    rule_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO notification_channels (id, tenant_id, channel_type, name, config) "
                "VALUES (:id, :tid, 'webhook', 'Rule Test Channel', "
                "CAST('{\"url\": \"http://example.invalid/hook\"}' AS jsonb))"
            ),
            {"id": channel_id, "tid": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO notification_rules "
                "(id, tenant_id, channel_id, min_severity, module_types, alert_codes, "
                " trigger_events, site_ids, is_active) "
                "VALUES (:id, :tid, :cid, 'medium', CAST('[]' AS jsonb), CAST('[]' AS jsonb), "
                "CAST('[]' AS jsonb), CAST(:sids AS jsonb), TRUE)"
            ),
            {"id": rule_id, "tid": tenant_id, "cid": channel_id,
             "sids": _json.dumps(site_ids)},
        )
        await s.commit()
    await engine.dispose()
    return rule_id


@pytest.mark.asyncio
async def test_rte_rule_empty_site_ids_matches_any_site():
    """Rule with site_ids=[] matches alerts from any site (and no site)."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    await _seed_channel_and_rule(tenant_id, site_ids=[])
    assert len(await _load_rules_for(tenant_id, alert_site_id=str(uuid.uuid4()))) == 1
    assert len(await _load_rules_for(tenant_id, alert_site_id="")) == 1


@pytest.mark.asyncio
async def test_rte_rule_site_ids_matches_only_listed_site():
    """Rule scoped to Site A matches Site A alerts, not Site B or site-less."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    site_a = str(uuid.uuid4())
    site_b = str(uuid.uuid4())
    await _seed_channel_and_rule(tenant_id, site_ids=[site_a])
    assert len(await _load_rules_for(tenant_id, alert_site_id=site_a)) == 1
    assert len(await _load_rules_for(tenant_id, alert_site_id=site_b)) == 0
    assert len(await _load_rules_for(tenant_id, alert_site_id="")) == 0


@pytest.mark.asyncio
async def test_rte_rule_severity_still_enforced_with_site_ids():
    """site_ids filter composes with min_severity (medium rule, low alert → no match)."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    site_a = str(uuid.uuid4())
    await _seed_channel_and_rule(tenant_id, site_ids=[site_a])
    assert len(await _load_rules_for(tenant_id, alert_site_id=site_a, severity="low")) == 0


# ─── C. Per-user push token registration ─────────────────────────────────────
# ASGITransport doesn't run the app lifespan, so app.state.redis is unset —
# inject a real client for the duration of each test.

class _AppRedis:
    def __init__(self):
        self._prev = None
        self.client = None

    async def __aenter__(self):
        import redis.asyncio as aioredis

        app = _app()
        self._prev = getattr(app.state, "redis", None)
        self.client = aioredis.from_url(
            os.environ.get("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
        app.state.redis = self.client
        return self.client

    async def __aexit__(self, *exc):
        _app().state.redis = self._prev
        await self.client.aclose()


@pytest.mark.asyncio
async def test_rte_push_token_registered_per_user_and_tenant():
    """POST /users/me/push-token writes both the tenant-wide and per-user sets."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    push_token = f"ExponentPushToken[test-{uuid.uuid4().hex[:8]}]"
    async with _AppRedis() as rc:
        async with await _authed(token) as c:
            r = await c.post("/api/v1/users/me/push-token", json={"token": push_token})
        assert r.status_code == 200
        assert r.json()["registered"] is True
        try:
            assert push_token in await rc.smembers(f"push_tokens:{tenant_id}")
            assert push_token in await rc.smembers(f"push_tokens:{tenant_id}:{user_id}")
        finally:
            await rc.srem(f"push_tokens:{tenant_id}", push_token)
            await rc.delete(f"push_tokens:{tenant_id}:{user_id}")


@pytest.mark.asyncio
async def test_rte_push_token_unregister_clears_both_sets():
    """DELETE /users/me/push-token removes the token from both sets."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    push_token = f"ExponentPushToken[gone-{uuid.uuid4().hex[:8]}]"
    async with _AppRedis() as rc:
        async with await _authed(token) as c:
            reg = await c.post("/api/v1/users/me/push-token", json={"token": push_token})
            assert reg.status_code == 200
            # Confirm it actually landed before testing removal
            assert push_token in await rc.smembers(f"push_tokens:{tenant_id}:{user_id}")
            r = await c.request(
                "DELETE", "/api/v1/users/me/push-token",
                content=f'{{"token": "{push_token}"}}',
                headers={"Content-Type": "application/json"},
            )
        assert r.status_code == 200
        assert push_token not in await rc.smembers(f"push_tokens:{tenant_id}")
        assert push_token not in await rc.smembers(f"push_tokens:{tenant_id}:{user_id}")


# ─── D. Rules CRUD site_ids round-trip ────────────────────────────────────────

async def _create_channel(c: AsyncClient) -> str:
    r = await c.post("/api/v1/notifications/channels", json={
        "channel_type": "webhook",
        "name": "Site Rule Channel",
        "config": {"url": "http://example.invalid/hook"},
    })
    assert r.status_code == 201, f"channel create failed: {r.text}"
    return r.json()["id"]


@pytest.mark.asyncio
async def test_rte_rule_create_with_site_ids_round_trip():
    """POST /notifications/rules stores site_ids; list returns them."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = str(await _seed_site(tenant_id, "Rule Site"))
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        r = await c.post("/api/v1/notifications/rules", json={
            "channel_id": channel_id,
            "min_severity": "high",
            "site_ids": [site_id],
        })
        assert r.status_code == 201, r.text
        assert r.json()["site_ids"] == [site_id]
        r_list = await c.get("/api/v1/notifications/rules")
    rows = r_list.json()
    row = next(rw for rw in rows if rw["id"] == r.json()["id"])
    assert row["site_ids"] == [site_id]


@pytest.mark.asyncio
async def test_rte_rule_update_site_ids():
    """PUT /notifications/rules/{id} replaces site_ids."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_a = str(await _seed_site(tenant_id, "RU A"))
    site_b = str(await _seed_site(tenant_id, "RU B"))
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        created = (await c.post("/api/v1/notifications/rules", json={
            "channel_id": channel_id,
            "site_ids": [site_a],
        })).json()
        r = await c.put(f"/api/v1/notifications/rules/{created['id']}",
                        json={"site_ids": [site_b]})
    assert r.status_code == 200
    assert r.json()["site_ids"] == [site_b]
