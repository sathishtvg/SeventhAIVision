"""Phase X-A — per-site recording policy + evidence/recording sync state.

What's actually worth testing here is the *resolution chain*, not the CRUD:
retention is how long evidence exists, so "which number wins" has to be
pinned down. In particular the NULL-vs-0 distinction — NULL means "inherit
the tenant setting", 0 means "keep nothing centrally" — is the one place a
plausible-looking `COALESCE`-free rewrite would silently start keeping
footage a local-only site was configured to discard.

Sections:
  A — RLS + schema (3 tests)
  B — Retention resolution (4 tests)
  C — Purge honours per-site retention (3 tests)
  D — record_mode gates continuous capture (4 tests)
  E — Field validation (5 tests)
  F — Permission split: read vs manage (3 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

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

SAFE_RTSP = "rtsp://203.0.113.1:554/live/cam0"  # TEST-NET-3, unreachable by design


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app

    return app


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"rpol-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": tenant_id, "name": f"RPol Test {slug}", "slug": slug},
            )
            await s.execute(
                text(
                    "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                    "VALUES (:id, :tid, :role, :email, 'hashed', 'RPol Tester')"
                ),
                {
                    "id": user_id,
                    "tid": tenant_id,
                    "role": role_id,
                    "email": f"rpol-{user_id.hex[:8]}@test.local",
                },
            )
            await s.commit()
    finally:
        await engine.dispose()
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id: uuid.UUID, name: str = "RPol Site") -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(
                text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)"),
                {"id": site_id, "tid": tenant_id, "name": name},
            )
            await s.commit()
    finally:
        await engine.dispose()
    return site_id


async def _seed_camera_and_stream(
    tenant_id: uuid.UUID, site_id: uuid.UUID | None = None, continuous: bool = True
):
    camera_id, stream_id = uuid.uuid4(), uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(
                text(
                    "INSERT INTO cameras (id, tenant_id, site_id, name) "
                    "VALUES (:id, :tid, :site, 'RPol Cam')"
                ),
                {"id": camera_id, "tid": tenant_id, "site": site_id},
            )
            await s.execute(
                text(
                    "INSERT INTO streams (id, tenant_id, camera_id, url, continuous_recording) "
                    "VALUES (:id, :tid, :cid, :url, :cont)"
                ),
                {
                    "id": stream_id,
                    "tid": tenant_id,
                    "cid": camera_id,
                    "url": SAFE_RTSP,
                    "cont": continuous,
                },
            )
            await s.commit()
    finally:
        await engine.dispose()
    return camera_id, stream_id


async def _seed_recording(
    tenant_id: uuid.UUID,
    camera_id: uuid.UUID,
    stream_id: uuid.UUID,
    site_id: uuid.UUID | None,
    *,
    days_old: int,
    file_path: str | None = None,
) -> uuid.UUID:
    rec_id = uuid.uuid4()
    started = datetime.now(timezone.utc) - timedelta(days=days_old)
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(
                text(
                    "INSERT INTO recordings (id, tenant_id, camera_id, stream_id, site_id, "
                    "  status, started_at, ended_at, file_path) "
                    "VALUES (:id, :tid, :cid, :sid, :site, 'completed', :sat, :eat, :fp)"
                ),
                {
                    "id": rec_id,
                    "tid": tenant_id,
                    "cid": camera_id,
                    "sid": stream_id,
                    "site": site_id,
                    "sat": started,
                    "eat": started + timedelta(minutes=15),
                    "fp": file_path or f"{tenant_id}/{camera_id}/{rec_id}.mp4",
                },
            )
            await s.commit()
    finally:
        await engine.dispose()
    return rec_id


async def _seed_policy(tenant_id: uuid.UUID, site_id: uuid.UUID, **cols):
    """Insert a policy row directly. Written with the admin role because the
    point of most of these tests is what the *app* role then reads back."""
    fields = {
        "record_mode": "continuous",
        "sync_mode": "central",
        "central_retention_days": None,
        "local_retention_days": None,
        "is_active": True,
        **cols,
    }
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(
                text(
                    "INSERT INTO recording_policies (tenant_id, site_id, record_mode, sync_mode, "
                    "  central_retention_days, local_retention_days, is_active) "
                    "VALUES (:tid, :sid, :record_mode, :sync_mode, "
                    "  :central_retention_days, :local_retention_days, :is_active)"
                ),
                {"tid": tenant_id, "sid": site_id, **fields},
            )
            await s.commit()
    finally:
        await engine.dispose()


async def _set_tenant_setting(tenant_id: uuid.UUID, user_id: uuid.UUID, key: str, value):
    import json

    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(
                text(
                    "INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, "
                    "  updated_by_user_id) "
                    "VALUES (:tid, :k, CAST(:v AS jsonb), :uid) "
                    "ON CONFLICT (tenant_id, setting_key) DO UPDATE "
                    "  SET setting_value = EXCLUDED.setting_value"
                ),
                {"tid": tenant_id, "k": key, "v": json.dumps(value), "uid": user_id},
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


# ─── A. RLS + schema ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rpol_rls_hides_other_tenants_policy():
    """Tenant A must not see tenant B's policy even knowing its site id."""
    from app.services.recording_policy import get_policy

    tenant_a, _, _ = await _seed_tenant_and_token()
    tenant_b, _, _ = await _seed_tenant_and_token()
    site_b = await _seed_site(tenant_b)
    await _seed_policy(tenant_b, site_b, central_retention_days=365)

    engine, session = await _app_session(tenant_a)
    try:
        leaked = await get_policy(session, str(site_b))
    finally:
        await session.close()
        await engine.dispose()
    assert leaked is None


@pytest.mark.asyncio
async def test_rpol_force_rls_enabled():
    """FORCE matters specifically because svc_app owns nothing by accident —
    without it a table owner would bypass its own policy."""
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'recording_policies'"
                    )
                )
            ).first()
    finally:
        await engine.dispose()
    assert row is not None, "recording_policies table missing"
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


@pytest.mark.asyncio
async def test_rpol_one_policy_per_site():
    """Two policies for one site would make retention non-deterministic."""
    import sqlalchemy.exc

    tenant_id, _, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    await _seed_policy(tenant_id, site_id, central_retention_days=30)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await _seed_policy(tenant_id, site_id, central_retention_days=60)


# ─── B. Retention resolution ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rpol_retention_falls_back_to_tenant_setting():
    tenant_id, user_id, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    await _set_tenant_setting(tenant_id, user_id, "recording.retention_days", 21)

    from app.services.recording_policy import get_effective_retention_days

    engine, session = await _app_session(tenant_id)
    try:
        days = await get_effective_retention_days(session, str(site_id), default=7)
    finally:
        await session.close()
        await engine.dispose()
    assert days == 21


@pytest.mark.asyncio
async def test_rpol_site_policy_beats_tenant_setting():
    tenant_id, user_id, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    await _set_tenant_setting(tenant_id, user_id, "recording.retention_days", 21)
    await _seed_policy(tenant_id, site_id, central_retention_days=365)

    from app.services.recording_policy import get_effective_retention_days

    engine, session = await _app_session(tenant_id)
    try:
        days = await get_effective_retention_days(session, str(site_id), default=7)
    finally:
        await session.close()
        await engine.dispose()
    assert days == 365


@pytest.mark.asyncio
async def test_rpol_null_retention_means_inherit_not_zero():
    """A policy that sets record_mode but leaves retention NULL must still
    inherit — the row existing is not itself an override."""
    tenant_id, user_id, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    await _set_tenant_setting(tenant_id, user_id, "recording.retention_days", 21)
    await _seed_policy(tenant_id, site_id, record_mode="motion", central_retention_days=None)

    from app.services.recording_policy import get_effective_retention_days

    engine, session = await _app_session(tenant_id)
    try:
        days = await get_effective_retention_days(session, str(site_id), default=7)
    finally:
        await session.close()
        await engine.dispose()
    assert days == 21


@pytest.mark.asyncio
async def test_rpol_zero_retention_is_a_real_value():
    """0 is 'keep nothing centrally', NOT 'inherit'. This is the distinction a
    truthiness check (`or`) instead of a NULL check would destroy."""
    tenant_id, user_id, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    await _set_tenant_setting(tenant_id, user_id, "recording.retention_days", 21)
    await _seed_policy(tenant_id, site_id, central_retention_days=0)

    from app.services.recording_policy import get_effective_retention_days

    engine, session = await _app_session(tenant_id)
    try:
        days = await get_effective_retention_days(session, str(site_id), default=7)
    finally:
        await session.close()
        await engine.dispose()
    assert days == 0


# ─── C. Purge honours per-site retention ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rpol_purge_spares_site_with_longer_retention(tmp_path):
    """A 30-day-old recording at a 365-day site survives a purge whose tenant
    fallback (7) would otherwise have deleted it."""
    from app.services.continuous_recording import purge_expired_recordings

    tenant_id, _, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id, site_id)
    await _seed_policy(tenant_id, site_id, central_retention_days=365)
    rec = await _seed_recording(tenant_id, camera_id, stream_id, site_id, days_old=30)

    engine, session = await _app_session(tenant_id)
    try:
        purged = await purge_expired_recordings(session, str(tmp_path), retention_days=7)
        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM recordings WHERE id = :id"), {"id": rec}
            )
        ).scalar()
    finally:
        await session.close()
        await engine.dispose()
    assert purged == 0
    assert remaining == 1


@pytest.mark.asyncio
async def test_rpol_purge_deletes_site_with_shorter_retention(tmp_path):
    """Mirror image: a 3-day-old recording at a 1-day site is purged even
    though the tenant fallback (7) would have kept it."""
    from app.services.continuous_recording import purge_expired_recordings

    tenant_id, _, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id, site_id)
    await _seed_policy(tenant_id, site_id, central_retention_days=1)
    rel = f"{tenant_id}/{camera_id}/short.mp4"
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"fake mp4")
    rec = await _seed_recording(
        tenant_id, camera_id, stream_id, site_id, days_old=3, file_path=rel
    )

    engine, session = await _app_session(tenant_id)
    try:
        purged = await purge_expired_recordings(session, str(tmp_path), retention_days=7)
        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM recordings WHERE id = :id"), {"id": rec}
            )
        ).scalar()
    finally:
        await session.close()
        await engine.dispose()
    assert purged == 1
    assert remaining == 0
    assert not full.exists(), "file must be deleted alongside the row"


@pytest.mark.asyncio
async def test_rpol_purge_unaffected_for_recording_with_no_site(tmp_path):
    """site_id IS NULL still resolves to the tenant fallback — the pre-X-A
    behaviour, which the new LEFT JOIN must not have changed."""
    from app.services.continuous_recording import purge_expired_recordings

    tenant_id, _, _ = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id, None)
    rec = await _seed_recording(tenant_id, camera_id, stream_id, None, days_old=30)

    engine, session = await _app_session(tenant_id)
    try:
        purged = await purge_expired_recordings(session, str(tmp_path), retention_days=7)
        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM recordings WHERE id = :id"), {"id": rec}
            )
        ).scalar()
    finally:
        await session.close()
        await engine.dispose()
    assert purged == 1
    assert remaining == 0


# ─── D. record_mode gates continuous capture ─────────────────────────────────


async def _needing_recording(tenant_id: uuid.UUID) -> list[str]:
    from app.services.continuous_recording import find_streams_needing_recording

    engine, session = await _app_session(tenant_id)
    try:
        rows = await find_streams_needing_recording(session)
    finally:
        await session.close()
        await engine.dispose()
    return [str(r["stream_id"]) for r in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "ai_event"])
async def test_rpol_record_mode_excludes_stream(mode):
    tenant_id, _, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    _, stream_id = await _seed_camera_and_stream(tenant_id, site_id, continuous=True)
    await _seed_policy(tenant_id, site_id, record_mode=mode)
    assert str(stream_id) not in await _needing_recording(tenant_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["continuous", "motion", "scheduled"])
async def test_rpol_record_mode_still_captures(mode):
    """'motion'/'scheduled' deliberately still capture: neither gate is
    implemented yet, and over-capturing is recoverable where under-capturing
    loses footage permanently."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    _, stream_id = await _seed_camera_and_stream(tenant_id, site_id, continuous=True)
    await _seed_policy(tenant_id, site_id, record_mode=mode)
    assert str(stream_id) in await _needing_recording(tenant_id)


@pytest.mark.asyncio
async def test_rpol_inactive_policy_does_not_gate():
    """is_active=FALSE resolves identically to 'no policy' everywhere."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    _, stream_id = await _seed_camera_and_stream(tenant_id, site_id, continuous=True)
    await _seed_policy(tenant_id, site_id, record_mode="off", is_active=False)
    assert str(stream_id) in await _needing_recording(tenant_id)


@pytest.mark.asyncio
async def test_rpol_stream_with_no_site_unaffected_by_policies():
    tenant_id, _, _ = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    await _seed_policy(tenant_id, site_id, record_mode="off")
    _, stream_id = await _seed_camera_and_stream(tenant_id, None, continuous=True)
    assert str(stream_id) in await _needing_recording(tenant_id)


# ─── E. Field validation ─────────────────────────────────────────────────────


def test_rpol_validate_rejects_unknown_mode():
    from app.services.recording_policy import validate_policy_fields

    with pytest.raises(ValueError, match="record_mode"):
        validate_policy_fields({"record_mode": "sometimes"})


def test_rpol_validate_rejects_unknown_compression():
    from app.services.recording_policy import validate_policy_fields

    with pytest.raises(ValueError, match="compression"):
        validate_policy_fields({"compression": "av1"})


def test_rpol_validate_requires_both_window_bounds():
    from datetime import time

    from app.services.recording_policy import validate_policy_fields

    with pytest.raises(ValueError, match="together"):
        validate_policy_fields({"sync_window_start": time(22, 0)})


def test_rpol_validate_allows_overnight_window():
    """22:00 -> 06:00 crosses midnight and is the most common quiet-hours
    window there is; start > end must not be treated as an error."""
    from datetime import time

    from app.services.recording_policy import validate_policy_fields

    validate_policy_fields(
        {"sync_window_start": time(22, 0), "sync_window_end": time(6, 0)}
    )


def test_rpol_validate_rejects_zero_length_window():
    from datetime import time

    from app.services.recording_policy import validate_policy_fields

    with pytest.raises(ValueError, match="identical"):
        validate_policy_fields(
            {"sync_window_start": time(2, 0), "sync_window_end": time(2, 0)}
        )


# ─── F. Permission split: read vs manage ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rpol_supervisor_can_read():
    """Role 3 sees the policy — a supervisor should know what retention their
    site is under."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=3)
    site_id = await _seed_site(tenant_id)
    await _seed_policy(tenant_id, site_id, central_retention_days=45)
    client = await _authed(token)
    try:
        r = await client.get(f"/api/v1/recording-policies/sites/{site_id}")
    finally:
        await client.aclose()
    assert r.status_code == 200, r.text
    assert r.json()["central_retention_days"] == 45


@pytest.mark.asyncio
async def test_rpol_supervisor_cannot_manage():
    """...but must not be able to shorten it. Retention is evidence."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=3)
    site_id = await _seed_site(tenant_id)
    client = await _authed(token)
    try:
        r = await client.put(
            f"/api/v1/recording-policies/sites/{site_id}",
            json={"record_mode": "off", "central_retention_days": 1},
        )
    finally:
        await client.aclose()
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_rpol_admin_upsert_then_delete_reverts_to_inherit():
    tenant_id, user_id, token = await _seed_tenant_and_token(role_id=2)
    site_id = await _seed_site(tenant_id)
    await _set_tenant_setting(tenant_id, user_id, "recording.retention_days", 21)
    client = await _authed(token)
    try:
        created = await client.put(
            f"/api/v1/recording-policies/sites/{site_id}",
            json={"record_mode": "continuous", "central_retention_days": 180},
        )
        assert created.status_code == 200, created.text

        eff = await client.get(f"/api/v1/recording-policies/sites/{site_id}/effective")
        assert eff.json()["central_retention_days"] == 180
        assert eff.json()["central_retention_inherited"] is False

        gone = await client.delete(f"/api/v1/recording-policies/sites/{site_id}")
        assert gone.status_code == 200, gone.text

        eff2 = await client.get(f"/api/v1/recording-policies/sites/{site_id}/effective")
        assert eff2.json()["central_retention_days"] == 21
        assert eff2.json()["central_retention_inherited"] is True
    finally:
        await client.aclose()
