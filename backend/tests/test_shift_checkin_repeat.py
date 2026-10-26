"""A check-in or check-out sent again by the same guard soon after it went through is answered "done".

Owner's phone test, 26 October 2026: the first check-in was saved (the selfie matched), but the phone's connection
dropped before the answer came back, so the phone said "Failed to submit check-in". Every retry was then refused as
"Shift not found or not in scheduled status" - the guard was checked in and could not find out. Now the same guard
repeating the same step within REPEAT_WINDOW_MINUTES gets the shift as it is, marked already_done; nothing is written
again. Anyone else, a step that never happened, or a repeat long after, is refused as before.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from tests.test_shift_self_checkin import _client, _seed, _selfie, _shift_for, live_face  # noqa: F401 - fixture
from tests.test_shifts import _admin_engine


async def _sql(sql: str, params: dict):
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        await s.execute(text(sql), params)
        await s.commit()
    await engine.dispose()


async def _row(shift_id: str):
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        row = (await s.execute(text(
            "SELECT status, actual_start, actual_end, check_in_photo_path FROM shifts WHERE id = :id"
        ), {"id": shift_id})).first()
    await engine.dispose()
    return row


@pytest.mark.asyncio
async def test_a_repeated_check_in_is_answered_done_and_changes_nothing(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    async with _client(people["guard"]["token"]) as g:
        first = await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())
        assert first.status_code == 200, first.text
        before = await _row(shift_id)
        again = await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())
    assert again.status_code == 200, again.text
    assert again.json()["already_done"] is True and again.json()["status"] == "active"
    assert "already_done" not in first.json()
    after = await _row(shift_id)
    assert after.actual_start == before.actual_start, "the first check-in's time stands"
    assert after.check_in_photo_path == before.check_in_photo_path


@pytest.mark.asyncio
async def test_a_repeat_from_someone_else_or_long_after_is_still_refused(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    async with _client(people["guard"]["token"]) as g:
        assert (await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())).status_code == 200
    async with _client(people["other"]["token"]) as o:
        r = await o.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())
        assert r.status_code == 404, "not their shift: no answer about it"
    await _sql("UPDATE shifts SET actual_start = now() - interval '11 minutes' WHERE id = :id", {"id": shift_id})
    async with _client(people["guard"]["token"]) as g:
        r = await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())
    assert r.status_code == 404, "eleven minutes on, a check-in to an active shift is a mistake, not a retry"


@pytest.mark.asyncio
async def test_two_check_ins_sent_at_once_both_come_back_done_and_one_is_saved(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    async with _client(people["guard"]["token"]) as g1, _client(people["guard"]["token"]) as g2:
        a, b = await asyncio.gather(
            g1.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie()),
            g2.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie()),
        )
    assert (a.status_code, b.status_code) == (200, 200), (a.text, b.text)
    assert sorted(bool(r.json().get("already_done")) for r in (a, b)) == [False, True]
    assert (await _row(shift_id)).status == "active"


@pytest.mark.asyncio
async def test_a_repeated_check_out_is_answered_done_but_not_after_the_system_closed_the_shift(live_face):
    people = await _seed()
    shift_id = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    async with _client(people["guard"]["token"]) as g:
        assert (await g.post(f"/api/v1/shifts/{shift_id}/start", files=_selfie())).status_code == 200
        assert (await g.post(f"/api/v1/shifts/{shift_id}/end", files=_selfie())).status_code == 200
        ended = (await _row(shift_id)).actual_end
        again = await g.post(f"/api/v1/shifts/{shift_id}/end", files=_selfie())
        assert again.status_code == 200, again.text
        assert again.json()["already_done"] is True and again.json()["status"] == "completed"
        assert (await _row(shift_id)).actual_end == ended

    closed = await _shift_for(people["admin"]["token"], people["guard"]["id"])
    await _sql("UPDATE shifts SET status = 'completed', actual_start = now() - interval '15 hours', "
               "actual_end = now(), auto_closed_at = now() WHERE id = :id", {"id": closed})
    async with _client(people["guard"]["token"]) as g:
        r = await g.post(f"/api/v1/shifts/{closed}/end", files=_selfie())
    assert r.status_code == 404, "the guard did not check out of a shift the system closed"
