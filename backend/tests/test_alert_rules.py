"""Module 14 — tenant-configurable AI alert rules.

The thing worth pinning down here is the MERGE, not the CRUD. Every read must
return the full matrix of (module, trigger) pairs the platform knows about,
each marked as default or override, because the AI workers resolve the same
way and any divergence between the two would be a disagreement about what
severity pages the on-call rota.

The other load-bearing property: a tenant with zero rows behaves exactly as
before this feature shipped. That is what makes the migration safe to deploy
against live tenants, and it is asserted directly rather than assumed.

Sections:
  A — RLS + schema (3 tests)
  B — Effective-rule merge (5 tests)
  C — Validation (4 tests)
  D — Permission split: read vs manage (4 tests)
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

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    slug = f"arule-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": tenant_id, "name": f"ARule {slug}", "slug": slug},
            )
            await s.execute(
                text(
                    "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                    "VALUES (:id, :tid, :role, :email, 'hashed', 'ARule Tester')"
                ),
                {
                    "id": user_id,
                    "tid": tenant_id,
                    "role": role_id,
                    "email": f"arule-{user_id.hex[:8]}@test.local",
                },
            )
            await s.commit()
    finally:
        await engine.dispose()
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_rule(tenant_id: uuid.UUID, module_type: str, trigger_key: str, **cols):
    fields = {
        "severity": "low",
        "create_incident": False,
        "incident_severity": None,
        "is_enabled": True,
        **cols,
    }
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(
                text(
                    "INSERT INTO alert_rules (tenant_id, module_type, trigger_key, severity, "
                    "  create_incident, incident_severity, is_enabled) "
                    "VALUES (:tid, :m, :t, :severity, :create_incident, :incident_severity, :is_enabled)"
                ),
                {"tid": tenant_id, "m": module_type, "t": trigger_key, **fields},
            )
            await s.commit()
    finally:
        await engine.dispose()


async def _app_session(tenant_id: uuid.UUID):
    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)}
    )
    return engine, session


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _find(rules, module_type, trigger_key):
    return next(
        r for r in rules if r["module_type"] == module_type and r["trigger_key"] == trigger_key
    )


# ─── A. RLS + schema ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arule_rls_hides_other_tenants_override():
    from app.services.alert_rules import fetch_overrides

    tenant_a, _, _ = await _seed_tenant_and_token()
    tenant_b, _, _ = await _seed_tenant_and_token()
    await _seed_rule(tenant_b, "weapon", "firearm", severity="low")

    engine, session = await _app_session(tenant_a)
    try:
        leaked = await fetch_overrides(session)
    finally:
        await session.close()
        await engine.dispose()
    assert ("weapon", "firearm") not in leaked


@pytest.mark.asyncio
async def test_arule_force_rls_enabled():
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'alert_rules'"
                    )
                )
            ).first()
    finally:
        await engine.dispose()
    assert row is not None, "alert_rules table missing"
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


@pytest.mark.asyncio
async def test_arule_one_override_per_trigger():
    """Two rules for one trigger would make severity non-deterministic."""
    import sqlalchemy.exc

    tenant_id, _, _ = await _seed_tenant_and_token()
    await _seed_rule(tenant_id, "lpr", "block", severity="high")
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await _seed_rule(tenant_id, "lpr", "block", severity="low")


# ─── B. Effective-rule merge ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arule_no_overrides_yields_shipped_defaults():
    """The property that makes this migration safe on live tenants: zero rows
    means byte-identical behaviour to before the feature existed."""
    from shared.alert_rules import DEFAULT_ALERT_RULES
    from app.services.alert_rules import get_effective_rules

    tenant_id, _, _ = await _seed_tenant_and_token()
    engine, session = await _app_session(tenant_id)
    try:
        rules = await get_effective_rules(session)
    finally:
        await session.close()
        await engine.dispose()

    assert len(rules) == len(DEFAULT_ALERT_RULES)
    assert all(r["is_overridden"] is False for r in rules)
    for (module_type, trigger_key), default in DEFAULT_ALERT_RULES.items():
        row = _find(rules, module_type, trigger_key)
        assert row["severity"] == default.severity
        assert row["create_incident"] == default.create_incident
        assert row["incident_severity"] == default.incident_severity


@pytest.mark.asyncio
async def test_arule_override_wins_and_reports_the_default():
    """An override must carry the value it replaced, so the UI can show what
    'reset' would go back to without a second lookup."""
    from app.services.alert_rules import get_effective_rules

    tenant_id, _, _ = await _seed_tenant_and_token()
    await _seed_rule(
        tenant_id, "weapon", "blunt", severity="critical",
        create_incident=True, incident_severity="critical",
    )
    engine, session = await _app_session(tenant_id)
    try:
        rules = await get_effective_rules(session)
    finally:
        await session.close()
        await engine.dispose()

    row = _find(rules, "weapon", "blunt")
    assert row["severity"] == "critical"
    assert row["create_incident"] is True
    assert row["is_overridden"] is True
    # Shipped default for weapon/blunt is medium, alert-only.
    assert row["default_severity"] == "medium"
    assert row["default_create_incident"] is False


@pytest.mark.asyncio
async def test_arule_disabled_override_is_not_the_same_as_absent():
    """is_enabled=False means 'never alert here'; no row means 'use default'.
    Collapsing the two would silently re-enable a suppressed alert."""
    from app.services.alert_rules import get_effective_rules

    tenant_id, _, _ = await _seed_tenant_and_token()
    await _seed_rule(tenant_id, "face", "unrecognized", severity="info", is_enabled=False)
    engine, session = await _app_session(tenant_id)
    try:
        rules = await get_effective_rules(session)
    finally:
        await session.close()
        await engine.dispose()

    row = _find(rules, "face", "unrecognized")
    assert row["is_enabled"] is False
    assert row["is_overridden"] is True


@pytest.mark.asyncio
async def test_arule_orphaned_override_is_surfaced_not_hidden():
    """An override for a trigger the platform no longer ships is shown so an
    admin can delete it, rather than silently having no effect."""
    from app.services.alert_rules import get_effective_rules

    tenant_id, _, _ = await _seed_tenant_and_token()
    await _seed_rule(tenant_id, "retired_module", "some_trigger", severity="high")
    engine, session = await _app_session(tenant_id)
    try:
        rules = await get_effective_rules(session)
    finally:
        await session.close()
        await engine.dispose()

    row = _find(rules, "retired_module", "some_trigger")
    assert row.get("is_orphaned") is True
    assert row["default_severity"] is None


@pytest.mark.asyncio
async def test_arule_rules_are_stably_ordered():
    """The editor renders a matrix; reshuffling rows on every save would make
    it unusable."""
    from app.services.alert_rules import get_effective_rules

    tenant_id, _, _ = await _seed_tenant_and_token()
    engine, session = await _app_session(tenant_id)
    try:
        rules = await get_effective_rules(session)
    finally:
        await session.close()
        await engine.dispose()
    keys = [(r["module_type"], r["trigger_key"]) for r in rules]
    assert keys == sorted(keys)


# ─── C. Validation ───────────────────────────────────────────────────────────


def test_arule_validate_rejects_unknown_severity():
    from app.services.alert_rules import validate_rule

    with pytest.raises(ValueError, match="severity"):
        validate_rule("catastrophic", False, None)


def test_arule_validate_requires_incident_severity_when_escalating():
    from app.services.alert_rules import validate_rule

    with pytest.raises(ValueError, match="incident_severity"):
        validate_rule("high", True, None)


def test_arule_validate_rejects_incident_severity_without_escalation():
    """A stray incident_severity means the author expected an incident that
    will never be created — better to reject than silently ignore."""
    from app.services.alert_rules import validate_rule

    with pytest.raises(ValueError):
        validate_rule("high", False, "high")


def test_arule_is_known_trigger():
    from app.services.alert_rules import is_known_trigger

    assert is_known_trigger("lpr", "block") is True
    assert is_known_trigger("lpr", "not_a_trigger") is False


# ─── D. Permission split: read vs manage ─────────────────────────────────────


@pytest.mark.asyncio
async def test_arule_supervisor_can_read():
    tenant_id, _, token = await _seed_tenant_and_token(role_id=3)
    client = await _authed(token)
    try:
        r = await client.get("/api/v1/alert-rules")
    finally:
        await client.aclose()
    assert r.status_code == 200, r.text
    assert len(r.json()) == 26


@pytest.mark.asyncio
async def test_arule_supervisor_cannot_manage():
    """Severity decides who gets paged at 3am — that is an admin decision."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=3)
    client = await _authed(token)
    try:
        r = await client.put(
            "/api/v1/alert-rules/weapon/firearm",
            json={"severity": "info", "create_incident": False},
        )
    finally:
        await client.aclose()
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_arule_admin_upsert_then_reset_round_trip():
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    client = await _authed(token)
    try:
        up = await client.put(
            "/api/v1/alert-rules/behavior/loitering",
            json={"severity": "low", "create_incident": False},
        )
        assert up.status_code == 200, up.text

        rules = (await client.get("/api/v1/alert-rules")).json()
        row = _find(rules, "behavior", "loitering")
        assert row["severity"] == "low"
        assert row["is_overridden"] is True
        assert row["default_severity"] == "medium"

        gone = await client.delete("/api/v1/alert-rules/behavior/loitering")
        assert gone.status_code == 200, gone.text

        rules2 = (await client.get("/api/v1/alert-rules")).json()
        row2 = _find(rules2, "behavior", "loitering")
        assert row2["severity"] == "medium"
        assert row2["is_overridden"] is False

        # Resetting something that has no override is a 404, not a silent no-op.
        again = await client.delete("/api/v1/alert-rules/behavior/loitering")
        assert again.status_code == 404
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_arule_unknown_trigger_rejected():
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    client = await _authed(token)
    try:
        r = await client.put(
            "/api/v1/alert-rules/lpr/not_a_real_trigger", json={"severity": "low"}
        )
    finally:
        await client.aclose()
    assert r.status_code == 404, r.text
