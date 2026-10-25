"""Saved drafts can be found again, and a draft says how many people the planner had.

Owner's report, 25 October 2026: a roster draft was shown only right after it was made; after a reload it could not
be found, reviewed or discarded, so an empty draft made before the fix sat there unseen. GET /roster/batches lists
them. Each organisation sees only its own (filtered by tenant in the query, not left to row security alone, because
tests connect as a user that bypasses it).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app  # noqa: F401 - the import every backend test makes first
from tests.test_roster_from_shift_definitions import _auto, _org, _today


async def _get(org: dict, path: str):
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {org['token']}"
        return await c.get(path)


async def _delete(org: dict, path: str):
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {org['token']}"
        return await c.delete(path)


@pytest.mark.asyncio
async def test_drafts_are_listed_newest_first_with_their_posts_and_discarded_ones_are_not():
    org = await _org(guards=2, day_needed=2, night_needed=1)
    other = await _org(guards=1)
    start = _today() + timedelta(days=1)
    first = (await _auto(org, start, start)).json()
    second = (await _auto(org, start, start + timedelta(days=1))).json()
    discarded = (await _auto(org, start, start)).json()
    assert (await _delete(org, f"/api/v1/roster/batches/{discarded['id']}")).status_code == 200
    theirs = (await _auto(other, start, start)).json()

    r = await _get(org, "/api/v1/roster/batches")
    assert r.status_code == 200, r.text
    listed = r.json()
    assert [b["id"] for b in listed] == [second["id"], first["id"]], "drafts only, newest first"
    by_id = {b["id"]: b for b in listed}
    assert by_id[second["id"]]["posts"] == len(second["draft_shifts"]) == 6
    assert by_id[second["id"]]["filled"] == sum(s["guard_user_id"] is not None for s in second["draft_shifts"])
    assert by_id[first["id"]]["site_name"] is None, "every site"
    assert theirs["id"] not in by_id, "another organisation's draft is never listed"

    r = await _get(org, "/api/v1/roster/batches?status=discarded")
    assert [b["id"] for b in r.json()] == [discarded["id"]]
    assert (await _get(org, "/api/v1/roster/batches?status=nonsense")).status_code == 422


@pytest.mark.asyncio
async def test_a_draft_records_how_many_people_could_be_rostered():
    org = await _org(guards=2, day_needed=2, night_needed=2)
    start = _today() + timedelta(days=1)
    batch = (await _auto(org, start, start)).json()
    summary = batch["rules_summary"]
    assert summary["people_available"] == 2
    assert summary["unfilled_slots"] >= 2, "four posts a day, two people, one shift each a day"
