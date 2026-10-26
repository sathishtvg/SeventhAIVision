"""The AI module, phase 1: the Super Admin's licence and the company admin's controls.

Three gates must all be open for a feature to do anything: the licence (the
platform owner's), the admin's switch, and the person's role (the customer's).

Sections:
  A — The rules, without a database (4 tests)
  B — The Super Admin's licence (7 tests)
  C — The company admin's controls (8 tests)
  D — Row-level security, as the app's own role (1 test)
"""
from __future__ import annotations

import dataclasses
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.core.security import create_access_token
from app.services import ai_module

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SUPER_ADMIN, ADMIN, SUPERVISOR, OPERATOR, GUARD, VIEWER, CLIENT, MANAGER = 1, 2, 3, 4, 5, 6, 7, 8
NOW = datetime.now(timezone.utc)


async def _run(statements: list[tuple[str, dict]]) -> list:
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    out = []
    try:
        async with factory() as s:
            for stmt, params in statements:
                r = await s.execute(text(stmt), params)
                out.append(r.mappings().all() if r.returns_rows else [])
            await s.commit()
        return out
    finally:
        await engine.dispose()


async def _sql(stmt: str, params: dict | None = None) -> list:
    return (await _run([(stmt, params or {})]))[0]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app), base_url="http://test")


def _auth(uid, tid, role) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(uid), str(tid), role)}"}


async def _world(*, features: list[str] | None = None, enabled: bool = True,
                 expires_at: datetime | None = None) -> dict:
    """An organisation with two sites, one user per role, and (with `features`) an AI licence."""
    w: dict = {k: uuid.uuid4() for k in ("tenant", "site_a", "site_b")}
    stmts = [
        ("INSERT INTO tenants (id, name, slug) VALUES (:t,'AI Co',:s)",
         {"t": w["tenant"], "s": f"ai-{w['tenant'].hex[:10]}"}),
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Jurong')", {"i": w["site_a"], "t": w["tenant"]}),
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Tuas')", {"i": w["site_b"], "t": w["tenant"]}),
    ]
    w["users"], w["h"] = {}, {}
    for role in (ADMIN, SUPERVISOR, OPERATOR, GUARD, VIEWER, CLIENT, MANAGER):
        uid = uuid.uuid4()
        w["users"][role], w["h"][role] = uid, _auth(uid, w["tenant"], role)
        stmts.append((
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
            "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
            {"i": uid, "t": w["tenant"], "r": role, "e": f"r{role}-{uid.hex[:8]}@ai.test", "n": f"Role {role}"}))
    if features is not None:
        stmts.append((
            "INSERT INTO ai_module_licenses (tenant_id, is_enabled, licensed_at, expires_at, features) "
            "VALUES (:t, :on, now(), :e, CAST(:f AS text[]))",
            {"t": w["tenant"], "on": enabled, "e": expires_at, "f": features}))
    await _run(stmts)
    return w


async def _super_admin(*, enrolled: bool = True) -> dict:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO tenants (id, name, slug) VALUES (:t,'Platform Ops',:s)", {"t": tid, "s": f"sa-{tid.hex[:10]}"}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name, totp_enabled) "
         "VALUES (:i,:t,1,:e,'x','Platform Owner',:mfa)",
         {"i": uid, "t": tid, "e": f"sa-{uid.hex[:8]}@platform.test", "mfa": enrolled}),
    ])
    return {"tenant": tid, "user": uid, "h": _auth(uid, tid, SUPER_ADMIN)}


@pytest.fixture
def late_risk_ready(monkeypatch):
    """A test that needs a feature switched on makes sure late_risk counts as built,
    whatever the release (it is built from phase 2)."""
    monkeypatch.setitem(ai_module.FEATURES, "late_risk",
                        dataclasses.replace(ai_module.FEATURES["late_risk"], ready=True))


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_no_licence_an_off_one_and_an_expired_one_each_say_why():
    assert "not licensed" in ai_module.licence_problem(None)
    assert "not licensed" in ai_module.licence_problem({"is_enabled": False, "expires_at": None})
    expired = ai_module.licence_problem({"is_enabled": True, "expires_at": NOW - timedelta(days=1)})
    assert "expired" in expired
    assert ai_module.licence_problem({"is_enabled": True, "expires_at": NOW + timedelta(days=1)}) is None
    assert ai_module.licensed_features({"is_enabled": True, "expires_at": None,
                                        "features": ["late_risk", "no_such"]}) == {"late_risk"}
    assert ai_module.licensed_features({"is_enabled": False, "expires_at": None, "features": ["late_risk"]}) == set()


def test_switching_off_is_always_allowed_and_switching_on_needs_all_three():
    f = dataclasses.replace(ai_module.FEATURES["late_risk"], ready=True)
    assert ai_module.setting_problem(f, enabled=False, role_ids=[], sensitivity="MEDIUM", licensed=set()) is None
    assert "not in this organisation" in ai_module.setting_problem(
        f, enabled=True, role_ids=[3], sensitivity="MEDIUM", licensed=set())
    assert "at least one role" in ai_module.setting_problem(
        f, enabled=True, role_ids=[], sensitivity="MEDIUM", licensed={"late_risk"})
    not_built = dataclasses.replace(ai_module.FEATURES["false_alarm"], ready=False)
    assert "not available yet" in ai_module.setting_problem(
        not_built, enabled=True, role_ids=[3], sensitivity="MEDIUM", licensed={"false_alarm"})
    assert ai_module.setting_problem(f, enabled=True, role_ids=[3], sensitivity="MEDIUM",
                                     licensed={"late_risk"}) is None


def test_warnings_about_other_peoples_work_are_not_given_to_a_guard_or_the_client():
    late = ai_module.FEATURES["late_risk"]
    assert "cannot be given to role(s) 5" in ai_module.setting_problem(
        late, enabled=False, role_ids=[3, 5], sensitivity="MEDIUM", licensed=set())
    chat = ai_module.FEATURES["assistant"]
    assert 5 in chat.allowed_roles and 7 not in chat.allowed_roles and 1 not in chat.allowed_roles
    assert all(1 not in f.allowed_roles and 7 not in f.allowed_roles for f in ai_module.FEATURES.values())


def test_a_person_uses_a_feature_only_when_every_gate_is_open():
    f = dataclasses.replace(ai_module.FEATURES["late_risk"], ready=True)
    on = {"enabled": True, "role_ids": [2, 3]}
    assert ai_module.usable_by(f, on, {"late_risk"}, 3) is True
    assert ai_module.usable_by(f, on, {"late_risk"}, 4) is False, "role not chosen"
    assert ai_module.usable_by(f, on, set(), 3) is False, "not licensed"
    assert ai_module.usable_by(f, {**on, "enabled": False}, {"late_risk"}, 3) is False, "switched off"
    assert ai_module.usable_by(dataclasses.replace(f, ready=False), on, {"late_risk"}, 3) is False, "not built"


# ─── B. The Super Admin's licence ────────────────────────────────────────────

LICENCE = "/api/v1/platform/tenants/{}/ai-license"


@pytest.mark.asyncio
async def test_a_company_admin_cannot_license_the_module():
    w = await _world()
    async with _client() as c:
        r = await c.put(LICENCE.format(w["tenant"]), headers=w["h"][ADMIN], json={"is_enabled": True})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_the_super_admin_grants_it_and_the_organisation_sees_it():
    w = await _world()
    sa = await _super_admin()
    async with _client() as c:
        before = (await c.get("/api/v1/ai/status", headers=w["h"][ADMIN])).json()
        put = await c.put(LICENCE.format(w["tenant"]), headers=sa["h"], json={
            "is_enabled": True, "features": ["overtime_fatigue", "late_risk", "late_risk"],
            "assistant_provider": "LOCAL", "notes": "Pilot"})
        after = (await c.get("/api/v1/ai/status", headers=w["h"][ADMIN])).json()
    assert before["licensed"] is False and "not licensed" in before["reason"]
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["licensed"] is True and body["features"] == ["late_risk", "overtime_fatigue"]
    assert body["assistant_provider"] == "LOCAL" and body["monthly_reply_limit"] is None
    assert [f["key"] for f in body["catalogue"]] == list(ai_module.FEATURES)
    assert after["licensed"] is True
    by = {f["key"]: f for f in after["features"]}
    assert by["late_risk"]["licensed"] and not by["assistant"]["licensed"]
    logged = await _sql("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = :t AND action = 'ai.license.set'",
                        {"t": w["tenant"]})
    assert logged[0]["n"] == 1, "the grant is not in the customer's own audit log"


@pytest.mark.asyncio
async def test_the_hosted_model_needs_a_monthly_limit_and_the_limit_can_be_raised():
    w = await _world()
    sa = await _super_admin()
    async with _client() as c:
        no_limit = await c.put(LICENCE.format(w["tenant"]), headers=sa["h"], json={
            "is_enabled": True, "features": ["assistant"], "assistant_provider": "HOSTED"})
        first = await c.put(LICENCE.format(w["tenant"]), headers=sa["h"], json={
            "is_enabled": True, "features": ["assistant"], "assistant_provider": "HOSTED",
            "monthly_reply_limit": 500})
        raised = await c.put(LICENCE.format(w["tenant"]), headers=sa["h"], json={
            "is_enabled": True, "features": ["assistant"], "assistant_provider": "HOSTED",
            "monthly_reply_limit": 2000})
        seen = (await c.get(LICENCE.format(w["tenant"]), headers=sa["h"])).json()
    assert no_limit.status_code == 422 and "monthly reply limit" in no_limit.text
    assert first.status_code == 200 and raised.status_code == 200
    assert seen["monthly_reply_limit"] == 2000 and seen["assistant_provider"] == "HOSTED"
    # The database holds the same rule, for any writer that is not this endpoint.
    with pytest.raises(Exception, match="ck_aiml_hosted_has_limit"):
        await _sql("UPDATE ai_module_licenses SET monthly_reply_limit = NULL WHERE tenant_id = :t",
                   {"t": w["tenant"]})


@pytest.mark.asyncio
async def test_an_unknown_feature_is_refused():
    w = await _world()
    sa = await _super_admin()
    async with _client() as c:
        r = await c.put(LICENCE.format(w["tenant"]), headers=sa["h"],
                        json={"is_enabled": True, "features": ["late_risk", "mind_reading"]})
    assert r.status_code == 422 and "mind_reading" in r.text


@pytest.mark.asyncio
async def test_a_super_admin_without_2fa_cannot_grant_it():
    w = await _world()
    sa = await _super_admin(enrolled=False)
    async with _client() as c:
        r = await c.put(LICENCE.format(w["tenant"]), headers=sa["h"], json={"is_enabled": True})
    assert r.status_code == 403 and "Two-factor" in r.json()["detail"], r.text


@pytest.mark.asyncio
async def test_the_platform_tenant_does_not_use_the_module():
    sa = await _super_admin()
    plat = uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug, is_platform) VALUES (:t,'Platform',:s,TRUE)",
               {"t": plat, "s": f"plat-{plat.hex[:10]}"})
    async with _client() as c:
        r = await c.put(LICENCE.format(plat), headers=sa["h"], json={"is_enabled": True})
    assert r.status_code == 422 and "platform tenant" in r.json()["detail"], r.text


@pytest.mark.asyncio
async def test_an_expired_licence_switches_everything_off_without_touching_the_settings(late_risk_ready):
    w = await _world(features=["late_risk"])
    async with _client() as c:
        on = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN],
                         json={"enabled": True, "role_ids": [2, 3]})
        await _sql("UPDATE ai_module_licenses SET expires_at = now() - interval '1 day' WHERE tenant_id = :t",
                   {"t": w["tenant"]})
        status = (await c.get("/api/v1/ai/status", headers=w["h"][SUPERVISOR])).json()
    assert on.status_code == 200, on.text
    late = next(f for f in status["features"] if f["key"] == "late_risk")
    assert status["licensed"] is False and "expired" in status["reason"]
    assert late["enabled"] is True and late["licensed"] is False and late["mine"] is False


# ─── C. The company admin's controls ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_an_admin_or_manager_changes_the_controls():
    w = await _world(features=["late_risk"])
    sa = await _super_admin()
    async with _client() as c:
        codes = {role: (await c.get("/api/v1/ai/settings", headers=w["h"][role])).status_code
                 for role in (ADMIN, MANAGER, SUPERVISOR, OPERATOR, GUARD, VIEWER, CLIENT)}
        platform = await c.get("/api/v1/ai/settings", headers=sa["h"])
        guard_put = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][GUARD], json={"enabled": False})
    assert codes == {ADMIN: 200, MANAGER: 200, SUPERVISOR: 403, OPERATOR: 403, GUARD: 403, VIEWER: 403, CLIENT: 403}
    assert platform.status_code == 403, "the platform owner sees no customer's AI controls"
    assert guard_put.status_code == 403


@pytest.mark.asyncio
async def test_a_feature_not_in_the_licence_cannot_be_switched_on_but_can_be_switched_off(late_risk_ready):
    w = await _world(features=["overtime_fatigue"])
    async with _client() as c:
        on = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN],
                         json={"enabled": True, "role_ids": [3]})
        off = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN],
                          json={"enabled": False, "role_ids": [3]})
        missing = await c.put("/api/v1/ai/settings/mind_reading", headers=w["h"][ADMIN], json={"enabled": False})
    assert on.status_code == 422 and "not in this organisation's AI licence" in on.json()["detail"]
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_a_feature_not_built_yet_cannot_be_switched_on():
    w = await _world(features=["ic_reading"])
    async with _client() as c:
        r = await c.put("/api/v1/ai/settings/ic_reading", headers=w["h"][ADMIN],
                        json={"enabled": True, "role_ids": [2]})
    assert r.status_code == 422 and "not available yet" in r.json()["detail"]


@pytest.mark.asyncio
async def test_switched_on_for_chosen_roles_it_is_theirs_and_nobody_elses(late_risk_ready):
    w = await _world(features=["late_risk"])
    async with _client() as c:
        put = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN], json={
            "enabled": True, "role_ids": [3, 2, 3], "sensitivity": "HIGH", "learning_enabled": False})
        mine = {role: next(f for f in (await c.get("/api/v1/ai/status", headers=w["h"][role])).json()["features"]
                           if f["key"] == "late_risk")["mine"]
                for role in (ADMIN, SUPERVISOR, OPERATOR, MANAGER)}
        settings = (await c.get("/api/v1/ai/settings", headers=w["h"][MANAGER])).json()
    assert put.status_code == 200, put.text
    assert put.json()["role_ids"] == [2, 3] and put.json()["sensitivity"] == "HIGH"
    assert put.json()["learning_enabled"] is False and put.json()["site_ids"] is None
    assert mine == {ADMIN: True, SUPERVISOR: True, OPERATOR: False, MANAGER: False}
    late = next(f for f in settings["features"] if f["key"] == "late_risk")
    assert late["enabled"] and late["licensed"] and late["allowed_roles"] == [2, 8, 3, 4]
    logged = await _sql("SELECT detail FROM audit_logs WHERE tenant_id = :t AND action = 'ai.setting.set'",
                        {"t": w["tenant"]})
    assert len(logged) == 1


@pytest.mark.asyncio
async def test_roles_a_feature_may_not_have_are_refused(late_risk_ready):
    w = await _world(features=["late_risk", "assistant"])
    async with _client() as c:
        guard = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN],
                            json={"enabled": True, "role_ids": [3, 5]})
        client = await c.put("/api/v1/ai/settings/assistant", headers=w["h"][ADMIN],
                             json={"enabled": False, "role_ids": [2, 7]})
    assert guard.status_code == 422 and "role(s) 5" in guard.json()["detail"]
    assert client.status_code == 422 and "role(s) 7" in client.json()["detail"]


@pytest.mark.asyncio
async def test_sites_must_be_the_organisations_own(late_risk_ready):
    w = await _world(features=["late_risk"])
    other = await _world()
    async with _client() as c:
        foreign = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN], json={
            "enabled": True, "role_ids": [3], "site_ids": [str(w["site_a"]), str(other["site_a"])]})
        none = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN],
                           json={"enabled": True, "role_ids": [3], "site_ids": []})
        own = await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN], json={
            "enabled": True, "role_ids": [3], "site_ids": [str(w["site_b"]), str(w["site_a"])]})
    assert foreign.status_code == 422 and "not in this organisation" in foreign.json()["detail"]
    assert none.status_code == 422
    assert own.status_code == 200 and own.json()["site_ids"] == sorted([str(w["site_a"]), str(w["site_b"])])


@pytest.mark.asyncio
async def test_an_unlicensed_organisation_is_told_why_and_nothing_is_anyones():
    w = await _world()
    async with _client() as c:
        status = await c.get("/api/v1/ai/status", headers=w["h"][GUARD])
    assert status.status_code == 200
    body = status.json()
    assert body["licensed"] is False and "Ask your platform administrator" in body["reason"]
    assert body["assistant_provider"] is None
    assert not any(f["mine"] or f["licensed"] for f in body["features"])


@pytest.mark.asyncio
async def test_a_licence_switched_off_keeps_the_admins_choices_for_when_it_returns(late_risk_ready):
    w = await _world(features=["late_risk"])
    async with _client() as c:
        await c.put("/api/v1/ai/settings/late_risk", headers=w["h"][ADMIN], json={"enabled": True, "role_ids": [3]})
        await _sql("UPDATE ai_module_licenses SET is_enabled = FALSE WHERE tenant_id = :t", {"t": w["tenant"]})
        off = next(f for f in (await c.get("/api/v1/ai/status", headers=w["h"][SUPERVISOR])).json()["features"]
                   if f["key"] == "late_risk")
        await _sql("UPDATE ai_module_licenses SET is_enabled = TRUE WHERE tenant_id = :t", {"t": w["tenant"]})
        back = next(f for f in (await c.get("/api/v1/ai/status", headers=w["h"][SUPERVISOR])).json()["features"]
                    if f["key"] == "late_risk")
    assert off["mine"] is False and off["enabled"] is True
    assert back["mine"] is True


# ─── D. Row-level security ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_each_organisation_sees_only_its_own_licence_and_settings():
    """As svc_app, the role the app runs as: the postgres test role bypasses RLS
    and would pass this whatever the policies said."""
    a, b = await _world(features=["late_risk"]), await _world(features=["assistant"])
    for w in (a, b):
        await _sql("INSERT INTO ai_feature_settings (tenant_id, feature_key, role_ids) "
                   "VALUES (:t, 'late_risk', '{3}')", {"t": w["tenant"]})
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            restricted = (await s.execute(text(
                "SELECT NOT (rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname = current_user"))).scalar()
            assert restricted, "connected as a role that bypasses RLS: proves nothing"
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(b["tenant"])})
            licences = (await s.execute(text("SELECT tenant_id, features FROM ai_module_licenses"))).all()
            settings = (await s.execute(text("SELECT tenant_id FROM ai_feature_settings"))).all()
            with pytest.raises(Exception):
                await s.execute(text(
                    "INSERT INTO ai_feature_settings (tenant_id, feature_key) VALUES (:t, 'assistant')"),
                    {"t": str(a["tenant"])})
    finally:
        await engine.dispose()
    assert [(str(t), list(f)) for t, f in licences] == [(str(b["tenant"]), ["assistant"])]
    assert [str(t) for (t,) in settings] == [str(b["tenant"])]
