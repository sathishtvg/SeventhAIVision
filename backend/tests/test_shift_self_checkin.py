"""A guard checks themselves in and out from the phone; the selfie is liveness-checked.

Fixed 23 October 2026, from the owner's phone test:
  * start, end and the breaks required shift:manage, which no guard holds - a guard's check-out was refused
    with "Missing permission: shift:manage". A guard may now act on their OWN shift; anyone else still needs
    shift:manage.
  * a guard must send a selfie to check in or out (only a supervisor's override may go without one).
  * the liveness check returned the model's SPOOF logit as the "real" score, so a real face scored about -4.5.
    It now returns sigmoid(real - spoof), the model's real probability.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from app.core.config import settings
from app.routers import shifts as shifts_router
from app.services import liveness
from tests.test_shifts import _admin_engine

ADMIN, GUARD = 2, 5
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64  # never decoded: the liveness check is replaced in these tests


async def _seed() -> dict:
    """One tenant: an admin, a guard, and a second guard. Returns their tokens and ids."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    users = {"admin": (uuid.uuid4(), ADMIN), "guard": (uuid.uuid4(), GUARD), "other": (uuid.uuid4(), GUARD)}
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
                        {"id": tenant_id, "n": "Self check-in", "s": f"selfci-{tenant_id.hex[:8]}"})
        for name, (uid, role) in users.items():
            await s.execute(
                text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                     "VALUES (:id, :tid, :rid, :email, :pw)"),
                {"id": uid, "tid": tenant_id, "rid": role, "email": f"{name}-{uid.hex[:8]}@test.local",
                 "pw": hash_password("x")},
            )
        await s.commit()
    await engine.dispose()
    return {name: {"id": str(uid), "token": create_access_token(str(uid), str(tenant_id), role)}
            for name, (uid, role) in users.items()}


def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _shift_for(admin_token: str, guard_id: str) -> str:
    now = datetime.now(timezone.utc)
    async with _client(admin_token) as c:
        r = await c.post("/api/v1/shifts", json={
            "guard_user_id": guard_id,
            "scheduled_start": (now + timedelta(minutes=5)).isoformat(),
            "scheduled_end": (now + timedelta(hours=8)).isoformat(),
        })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _selfie():
    return {"photo": ("selfie.jpg", io.BytesIO(JPEG), "image/jpeg")}


@pytest.fixture
def live_face(monkeypatch, tmp_path):
    """The liveness check says 'a live face', and photos go to a scratch folder."""
    monkeypatch.setattr(settings, "ATTENDANCE_PHOTOS_ROOT", str(tmp_path))
    score = {"value": 0.97}
    monkeypatch.setattr(shifts_router, "check_liveness_sync", lambda _bytes: score["value"])
    return score


@pytest.mark.asyncio
async def test_a_guard_checks_in_takes_a_break_and_checks_out_of_their_own_shift(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    async with _client(people["guard"]["token"]) as g:
        r = await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"
        assert (await g.post(f"/api/v1/shifts/{shift_id}/break/start")).status_code == 200
        assert (await g.post(f"/api/v1/shifts/{shift_id}/break/end")).status_code == 200
        r = await g.post(f"/api/v1/shifts/{shift_id}/end", files=_selfie())
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_a_guard_cannot_check_in_or_out_without_a_selfie(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    async with _client(people["guard"]["token"]) as g:
        r = await g.post(f"/api/v1/shifts/{shift_id}/start")
        assert r.status_code == 422 and "selfie" in r.json()["detail"].lower()
        assert (await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())).status_code == 200
        r = await g.post(f"/api/v1/shifts/{shift_id}/end")
        assert r.status_code == 422 and "selfie" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_a_selfie_that_fails_the_liveness_check_does_not_start_the_shift(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    live_face["value"] = 0.12  # e.g. a photo of a photo
    async with _client(people["guard"]["token"]) as g:
        r = await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())
        assert r.status_code == 422 and "liveness" in r.json()["detail"].lower()
    async with _client(people["admin"]["token"]) as a:
        assert (await a.get(f"/api/v1/shifts/{shift_id}")).json()["status"] == "scheduled"


@pytest.mark.asyncio
async def test_a_guard_cannot_act_on_another_guards_shift(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["other"]["id"])
    async with _client(people["guard"]["token"]) as g:
        r = await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())
        assert r.status_code == 403 and "shift:manage" in r.json()["detail"]
    async with _client(people["admin"]["token"]) as a:
        assert (await a.post(f"/api/v1/shifts/{shift_id}/start")).status_code == 200
    async with _client(people["guard"]["token"]) as g:
        for path in ("break/start", "break/end"):
            assert (await g.post(f"/api/v1/shifts/{shift_id}/{path}")).status_code == 403
        assert (await g.post(f"/api/v1/shifts/{shift_id}/end", files=_selfie())).status_code == 403


@pytest.mark.asyncio
async def test_a_supervisor_still_starts_and_ends_a_guards_shift_without_a_photo(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    async with _client(people["admin"]["token"]) as a:
        assert (await a.post(f"/api/v1/shifts/{shift_id}/start")).json()["status"] == "active"
        assert (await a.post(f"/api/v1/shifts/{shift_id}/end")).json()["status"] == "completed"


def test_the_liveness_score_is_the_models_real_probability():
    """The model's two outputs are raw logits, [real, spoof]."""
    assert liveness.real_probability(np.array([[4.467, -4.466]])) > 0.999  # a real face (sample photo)
    assert liveness.real_probability(np.array([[-3.0, 2.0]])) < 0.01  # a spoof
    assert liveness.real_probability(np.array([[0.0, 0.0]])) == pytest.approx(0.5)


def test_the_face_crop_is_a_square_centred_on_the_face_even_at_the_edge():
    img = np.zeros((400, 600, 3), dtype=np.uint8)
    img[:, :300] = 200  # left half bright, so reflection at the left edge is visible
    middle = liveness._crop_face(img, np.array([250.0, 150.0, 350.0, 270.0]))
    assert middle.shape == (180, 180, 3)  # 1.5 x the longer side (120)
    edge = liveness._crop_face(img, np.array([0.0, 0.0, 100.0, 100.0]))
    assert edge.shape == (150, 150, 3)  # padded by reflection, not cut short
    assert edge[0, 0, 0] == 200
