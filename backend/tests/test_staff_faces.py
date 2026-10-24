"""Staff face registration and the check-in face match (docs/plans/2026-10-23-staff-face-registration-design.md).

The owner's rules: a person registers their own face once; after that a guard's face is changed by a
supervisor or an admin, a supervisor's by an admin only; a check-in selfie that does not match never blocks
the check-in ("allow but flag"). The face model is replaced in these tests by fixed face codes.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from app.core.config import settings
from app.core.config_keys import SETTING_VALIDATORS
from app.routers import shifts as shifts_router
from app.routers import staff_faces as faces_router
from app.services import staff_face_retention
from app.services import staff_faces as faces
from tests.test_shifts import _admin_engine

SUPER, ADMIN, SUPERVISOR, OPERATOR, GUARD, VIEWER, CLIENT, MANAGER = 1, 2, 3, 4, 5, 6, 7, 8
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64  # never decoded: the face model is replaced below


def _code(i: int) -> list[float]:
    v = [0.0] * 512
    v[i] = 1.0
    return v


ALICE, BOB = _code(0), _code(1)  # two different faces: similarity 0


async def _seed(**people: int) -> dict:
    """One organisation with the people named (name=role). Returns {name: {id, token}} and the tenant id."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    ids = {name: uuid.uuid4() for name in people}
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        await s.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
                        {"id": tenant_id, "n": "Faces", "s": f"faces-{tenant_id.hex[:8]}"})
        for name, role in people.items():
            await s.execute(text(
                "INSERT INTO users (id, tenant_id, role_id, email, full_name, hashed_password) "
                "VALUES (:id, :t, :r, :e, :n, :pw)"
            ), {"id": ids[name], "t": tenant_id, "r": role, "e": f"{name}-{ids[name].hex[:6]}@test.local",
                "n": name.title(), "pw": hash_password("x")})
        await s.commit()
    await engine.dispose()
    out = {name: {"id": str(ids[name]), "token": create_access_token(str(ids[name]), str(tenant_id), role)}
           for name, role in people.items()}
    out["_tenant"] = str(tenant_id)
    return out


def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def _photo():
    return {"photo": ("face.jpg", io.BytesIO(JPEG), "image/jpeg")}


@pytest.fixture
def model(monkeypatch, tmp_path):
    """The face model: registration and check-in photos give the face code set here; liveness passes."""
    monkeypatch.setattr(settings, "ATTENDANCE_PHOTOS_ROOT", str(tmp_path))
    state = {"register": ALICE, "selfie": ALICE, "liveness": 0.98}
    monkeypatch.setattr(faces, "analyse_registration_photo_sync", lambda _b: (state["register"], b"jpeg-bytes"))

    def selfie(_b):
        if isinstance(state["selfie"], Exception):
            raise state["selfie"]
        return state["selfie"]

    monkeypatch.setattr(faces, "selfie_embedding_sync", selfie)
    monkeypatch.setattr(faces_router, "check_liveness_sync", lambda _b: state["liveness"])
    monkeypatch.setattr(shifts_router, "check_liveness_sync", lambda _b: state["liveness"])
    state["root"] = tmp_path
    return state


async def _register_self(token: str, consent: bool = True):
    async with _client(token) as c:
        return await c.post("/api/v1/faces/me", files=_photo(), data={"consent": "true" if consent else "false"})


async def _admin_db(sql: str, params: dict | None = None):
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        result = await s.execute(text(sql), params or {})
        rows = result.mappings().all() if result.returns_rows else []
        await s.commit()
    await engine.dispose()
    return rows


# ── Who may change whose face ────────────────────────────────────────────────

def test_the_owners_rules_for_who_may_change_a_face():
    assert faces.may_change(SUPERVISOR, GUARD) and faces.may_change(ADMIN, GUARD) and faces.may_change(MANAGER, GUARD)
    assert not faces.may_change(GUARD, GUARD) and not faces.may_change(OPERATOR, GUARD)
    assert faces.may_change(ADMIN, SUPERVISOR) and not faces.may_change(SUPERVISOR, SUPERVISOR)
    assert not faces.may_change(MANAGER, SUPERVISOR)
    assert faces.may_change(ADMIN, ADMIN) and not faces.may_change(SUPERVISOR, ADMIN)
    assert not faces.may_change(ADMIN, SUPER) and not faces.may_change(SUPER, GUARD)  # the platform owner: no faces
    assert not faces.may_change(ADMIN, VIEWER) and not faces.may_change(ADMIN, CLIENT)
    assert "attendance.face_match_min_similarity" in SETTING_VALIDATORS


# ── Registering one's own face ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_guard_registers_their_own_face_once_with_consent(model):
    p = await _seed(guard=GUARD)
    async with _client(p["guard"]["token"]) as g:
        before = (await g.get("/api/v1/faces/me")).json()
        assert before["registered"] is False and before["may_register"] is True
    r = await _register_self(p["guard"]["token"])
    assert r.status_code == 201, r.text
    assert r.json()["registered"] is True and r.json()["source"] == "SELFIE" and r.json()["may_register"] is False
    again = await _register_self(p["guard"]["token"])
    assert again.status_code == 409 and "supervisor" in again.json()["detail"]

    consent = await _admin_db(
        "SELECT consent_type, consent_method, consented FROM pdpa_consents WHERE data_subject_id = :u",
        {"u": p["guard"]["id"]})
    assert [dict(c) for c in consent] == [{"consent_type": "face_recognition", "consent_method": "digital",
                                           "consented": True}]
    audit = await _admin_db("SELECT action FROM audit_logs WHERE tenant_id = CAST(:t AS uuid)", {"t": p["_tenant"]})
    assert [a["action"] for a in audit] == ["face.registered"]
    saved = await _admin_db("SELECT photo_path FROM staff_faces WHERE user_id = CAST(:u AS uuid)", {"u": p["guard"]["id"]})
    assert (model["root"] / saved[0]["photo_path"]).read_bytes() == b"jpeg-bytes"


@pytest.mark.asyncio
async def test_nothing_is_saved_without_consent_or_with_a_failed_liveness_check(model):
    p = await _seed(guard=GUARD)
    r = await _register_self(p["guard"]["token"], consent=False)
    assert r.status_code == 422 and "agreement" in r.json()["detail"]
    model["liveness"] = 0.1
    r = await _register_self(p["guard"]["token"])
    assert r.status_code == 422 and "liveness" in r.json()["detail"].lower()
    assert await _admin_db("SELECT 1 FROM staff_faces WHERE user_id = CAST(:u AS uuid)", {"u": p["guard"]["id"]}) == []
    assert await _admin_db("SELECT 1 FROM pdpa_consents WHERE data_subject_id = :u", {"u": p["guard"]["id"]}) == []


@pytest.mark.asyncio
async def test_a_viewer_has_no_face_to_register(model):
    p = await _seed(viewer=VIEWER)
    assert (await _register_self(p["viewer"]["token"])).status_code == 403


# ── Changing someone else's face ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_supervisor_changes_a_guards_face_but_not_another_supervisors(model):
    p = await _seed(admin=ADMIN, sup=SUPERVISOR, sup2=SUPERVISOR, guard=GUARD, guard2=GUARD)
    await _register_self(p["guard"]["token"])
    async with _client(p["guard2"]["token"]) as g2:
        assert (await g2.put(f"/api/v1/faces/{p['guard']['id']}", files=_photo(), data={"consent": "true"})).status_code == 403
    async with _client(p["sup"]["token"]) as s:
        listed = {row["user_id"] for row in (await s.get("/api/v1/faces")).json()}
        assert listed == {p["guard"]["id"], p["guard2"]["id"]}
        no_tick = await s.put(f"/api/v1/faces/{p['guard']['id']}", files=_photo(), data={"consent": "false"})
        assert no_tick.status_code == 422
        model["register"] = BOB
        r = await s.put(f"/api/v1/faces/{p['guard']['id']}", files=_photo(), data={"consent": "true"})
        assert r.status_code == 200, r.text
        assert r.json()["source"] == "UPLOAD" and r.json()["registered_by_name"] == "Sup"
        r = await s.put(f"/api/v1/faces/{p['sup2']['id']}", files=_photo(), data={"consent": "true"})
        assert r.status_code == 403 and "an admin" in r.json()["detail"]
        assert (await s.delete(f"/api/v1/faces/{p['guard']['id']}")).status_code == 200
        assert (await s.get(f"/api/v1/faces/{p['guard']['id']}")).json()["registered"] is False
    async with _client(p["admin"]["token"]) as a:
        assert (await a.put(f"/api/v1/faces/{p['sup2']['id']}", files=_photo(), data={"consent": "true"})).status_code == 200
    audit = await _admin_db("SELECT action FROM audit_logs WHERE tenant_id = CAST(:t AS uuid) ORDER BY created_at",
                            {"t": p["_tenant"]})
    assert [a["action"] for a in audit] == ["face.registered", "face.replaced", "face.deleted", "face.registered"]


@pytest.mark.asyncio
async def test_another_organisation_cannot_see_or_change_a_face(model):
    a = await _seed(guard=GUARD)
    b = await _seed(admin=ADMIN)
    await _register_self(a["guard"]["token"])
    async with _client(b["admin"]["token"]) as other:
        assert (await other.get(f"/api/v1/faces/{a['guard']['id']}")).status_code == 404
        assert (await other.delete(f"/api/v1/faces/{a['guard']['id']}")).status_code == 404
        assert a["guard"]["id"] not in {row["user_id"] for row in (await other.get("/api/v1/faces")).json()}


@pytest.mark.asyncio
async def test_the_photo_is_shown_to_the_person_and_to_who_may_change_it(model):
    p = await _seed(sup=SUPERVISOR, guard=GUARD, guard2=GUARD)
    await _register_self(p["guard"]["token"])
    url = f"/api/v1/faces/{p['guard']['id']}/photo"
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        assert (await c.get(url, params={"token": p["guard"]["token"]})).status_code == 200
        assert (await c.get(url, params={"token": p["sup"]["token"]})).status_code == 200
        assert (await c.get(url, params={"token": p["guard2"]["token"]})).status_code == 403


# ── The check-in ─────────────────────────────────────────────────────────────

async def _check_in(admin_token: str, guard: dict) -> tuple[str, dict]:
    now = datetime.now(timezone.utc)
    async with _client(admin_token) as a:
        shift_id = (await a.post("/api/v1/shifts", json={
            "guard_user_id": guard["id"], "scheduled_start": (now + timedelta(minutes=5)).isoformat(),
            "scheduled_end": (now + timedelta(hours=8)).isoformat(),
        })).json()["id"]
    async with _client(guard["token"]) as g:
        r = await g.post(f"/api/v1/shifts/{shift_id}/start", files={"photo": ("s.jpg", io.BytesIO(JPEG), "image/jpeg")})
        assert r.status_code == 200, r.text  # allow but flag: whatever the face check finds
        checks = (await g.get("/api/v1/faces/me/checks", params={"shift_id": shift_id})).json()
    assert len(checks) == 1 and checks[0]["which"] == "check_in"
    return shift_id, checks[0]


@pytest.mark.asyncio
async def test_a_check_in_records_matched_mismatch_no_face_and_not_checked_and_never_blocks(model):
    p = await _seed(admin=ADMIN, sup=SUPERVISOR, guard=GUARD)
    _, check = await _check_in(p["admin"]["token"], p["guard"])
    assert check["outcome"] == "NO_FACE"

    await _register_self(p["guard"]["token"])  # ALICE on file
    _, check = await _check_in(p["admin"]["token"], p["guard"])
    assert check["outcome"] == "MATCHED" and check["similarity"] == pytest.approx(1.0)
    assert check["min_similarity"] == pytest.approx(faces.MATCH_MIN_SIMILARITY_DEFAULT)

    model["selfie"] = BOB  # somebody else
    mismatch_shift, check = await _check_in(p["admin"]["token"], p["guard"])
    assert check["outcome"] == "MISMATCH" and check["similarity"] == pytest.approx(0.0)

    model["selfie"] = RuntimeError("Face recognition model unavailable")
    _, check = await _check_in(p["admin"]["token"], p["guard"])
    assert check["outcome"] == "NOT_CHECKED" and "unavailable" in check["reason"]

    async with _client(p["sup"]["token"]) as s:
        review = (await s.get("/api/v1/faces/checks", params={"outcome": "review"})).json()
        assert sorted(r["outcome"] for r in review) == ["MISMATCH", "NOT_CHECKED", "NO_FACE"]
        assert mismatch_shift in {r["shift_id"] for r in review}
        assert len((await s.get("/api/v1/faces/checks")).json()) == 4
    async with _client(p["guard"]["token"]) as g:
        assert (await g.get("/api/v1/faces/checks")).status_code == 403


@pytest.mark.asyncio
async def test_a_supervisors_override_without_a_photo_has_no_face_check(model):
    p = await _seed(admin=ADMIN, guard=GUARD)
    now = datetime.now(timezone.utc)
    async with _client(p["admin"]["token"]) as a:
        shift_id = (await a.post("/api/v1/shifts", json={
            "guard_user_id": p["guard"]["id"], "scheduled_start": (now + timedelta(minutes=5)).isoformat(),
            "scheduled_end": (now + timedelta(hours=8)).isoformat(),
        })).json()["id"]
        assert (await a.post(f"/api/v1/shifts/{shift_id}/start")).status_code == 200
    assert await _admin_db("SELECT 1 FROM shift_face_checks WHERE shift_id = CAST(:s AS uuid)", {"s": shift_id}) == []


# ── The nightly removal ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_deactivated_persons_face_is_removed_at_night_and_the_removal_audited(model):
    p = await _seed(guard=GUARD, guard2=GUARD)
    await _register_self(p["guard"]["token"])
    await _register_self(p["guard2"]["token"])
    path = (await _admin_db("SELECT photo_path FROM staff_faces WHERE user_id = CAST(:u AS uuid)",
                            {"u": p["guard"]["id"]}))[0]["photo_path"]
    assert (model["root"] / path).exists()
    await _admin_db("UPDATE users SET is_active = FALSE WHERE id = CAST(:u AS uuid)", {"u": p["guard"]["id"]})

    engine = _admin_engine()  # the scheduler's superuser session
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        out = await staff_face_retention.run(s)
    await engine.dispose()
    assert out["removed"] >= 1 and out["failed"] == 0
    left = {r["user_id"] for r in await _admin_db(
        "SELECT user_id::text FROM staff_faces WHERE tenant_id = CAST(:t AS uuid)", {"t": p["_tenant"]})}
    assert left == {p["guard2"]["id"]}
    assert not (model["root"] / path).exists()
    assert not (model["root"] / path).parent.exists(), "the person's empty folder goes with the photo"
    assert (model["root"] / "faces" / p["_tenant"]).exists(), "the organisation's folder stays while guard2's face is in it"
    removed = await _admin_db("SELECT detail FROM audit_logs WHERE tenant_id = CAST(:t AS uuid) AND action = 'face.removed'",
                              {"t": p["_tenant"]})
    assert len(removed) == 1 and staff_face_retention.WHY in str(removed[0]["detail"])
