"""Footage Review, phase 1: a video arrives in parts, is kept 30 days, held past them, or deleted.

  A — An upload arrives in parts, in order, and carries on after it stops
  B — What is refused at the start
  C — Who may do what
  D — Sites and organisations
  E — Holding and deleting
  F — The 30 days: the daily removal
  G — Nothing here raises an alert or an incident

Every request goes through the real app over ASGI, as svc_app with RLS enforced.
The removal runs on a superuser session, as the scheduler runs it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services import footage, footage_retention
from tests.test_drone_api import (ADMIN, ADMIN_DATABASE_URL, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client,
                                  _run, _sql, _world)
from tests.test_investigation_search import _audit

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/footage"
#: Small parts, so a test file of a few dozen bytes arrives in several.
PART = 8


@pytest.fixture(autouse=True)
def _small_parts_on_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(footage, "PART_SIZE", PART)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "EVIDENCE_ROOT", str(tmp_path))
    return tmp_path


def _file(n: int = 21) -> bytes:
    return bytes(range(65, 65 + n))


async def _start(c, w, who=ADMIN, *, data: bytes | None = None, **extra):
    data = _file() if data is None else data
    body = {"filename": "gate camera.mp4", "size_bytes": len(data), "site_id": str(w["site_a"]), **extra}
    r = await c.post(BASE, headers=w["h"][who], json=body)
    return r


async def _upload(c, w, who=ADMIN, *, data: bytes | None = None, finish: bool = True, **extra) -> dict:
    data = _file() if data is None else data
    r = await _start(c, w, who, data=data, **extra)
    assert r.status_code == 201, r.text
    video = r.json()
    for offset in range(0, len(data), PART):
        p = await c.put(f"{BASE}/{video['id']}/content", params={"offset": offset}, headers=w["h"][who],
                        content=data[offset:offset + PART])
        assert p.status_code == 200, p.text
    if finish:
        done = await c.post(f"{BASE}/{video['id']}/complete", headers=w["h"][who])
        assert done.status_code == 200, done.text
        return done.json()
    return video


def _on_disk(root: Path, w: dict, video_id: str) -> Path:
    return root / footage.storage_dir(w["tenant"], video_id)


# ─── A. An upload arrives in parts, in order, and carries on after it stops ──

async def test_a_video_arrives_in_parts_and_is_queued_whole(_small_parts_on_disk):
    w = await _world()
    data = _file(21)
    async with _client() as c:
        r = await _start(c, w, OPERATOR, data=data, title="  Lobby, night of the 3rd ", place="Lobby",
                         filmed_at="2026-10-03T22:15:00+08:00")
        assert r.status_code == 201, r.text
        v = r.json()
        assert (v["status"], v["received_bytes"], v["part_size"], v["title"], v["place"]) == (
            "UPLOADING", 0, PART, "Lobby, night of the 3rd", "Lobby")
        assert v["rules"]["retention_days"] == 30 and ".mp4" in v["rules"]["extensions"]
        assert v["deleted_in_days"] == 30 and v["held"] is False
        # The first two parts, then the connection drops.
        for offset in (0, 8):
            p = await c.put(f"{BASE}/{v['id']}/content", params={"offset": offset}, headers=w["h"][OPERATOR],
                            content=data[offset:offset + PART])
            assert p.status_code == 200 and p.json()["complete"] is False, p.text
        # Sent again from the start: refused, with where to carry on from.
        again = await c.put(f"{BASE}/{v['id']}/content", params={"offset": 0}, headers=w["h"][OPERATOR],
                            content=data[:PART])
        assert again.status_code == 409 and again.json()["detail"]["received_bytes"] == 16, again.text
        state = await c.get(f"{BASE}/{v['id']}/upload", headers=w["h"][OPERATOR])
        assert state.json()["received_bytes"] == 16
        # Finishing early is refused.
        early = await c.post(f"{BASE}/{v['id']}/complete", headers=w["h"][OPERATOR])
        assert early.status_code == 409, early.text
        # The last part is what is left, and no more.
        long = await c.put(f"{BASE}/{v['id']}/content", params={"offset": 16}, headers=w["h"][OPERATOR],
                           content=data[16:] + b"xx")
        assert long.status_code == 422, long.text
        last = await c.put(f"{BASE}/{v['id']}/content", params={"offset": 16}, headers=w["h"][OPERATOR],
                           content=data[16:])
        assert last.json() == {"id": v["id"], "received_bytes": 21, "size_bytes": 21, "complete": True}
        done = await c.post(f"{BASE}/{v['id']}/complete", headers=w["h"][OPERATOR])
        assert done.status_code == 200, done.text
        assert (done.json()["status"], done.json()["status_label"]) == ("QUEUED", "Waiting to be analysed")
        twice = await c.post(f"{BASE}/{v['id']}/complete", headers=w["h"][OPERATOR])
        assert twice.status_code == 409
    stored = _on_disk(_small_parts_on_disk, w, v["id"]) / "original.mp4"
    assert stored.read_bytes() == data
    assert [a["detail"]["filename"] for a in await _audit(w, "footage.upload.start")] == ["gate camera.mp4"]
    assert [str(a["resource_id"]) for a in await _audit(w, "footage.upload.complete")] == [v["id"]]


async def test_a_part_cut_short_by_a_crash_leaves_nothing_behind_it(_small_parts_on_disk):
    w = await _world()
    data = _file(20)
    async with _client() as c:
        v = await _upload(c, w, data=data, finish=False)
    stored = _on_disk(_small_parts_on_disk, w, v["id"]) / "original.mp4"
    assert stored.read_bytes() == data
    # Rewind the server to the second part, as if its update had never been saved, and send it again.
    await _sql("UPDATE footage_videos SET received_bytes = 8 WHERE id = :i", {"i": v["id"]})
    async with _client() as c:
        p = await c.put(f"{BASE}/{v['id']}/content", params={"offset": 8}, headers=w["h"][ADMIN],
                        content=data[8:16])
        assert p.status_code == 200, p.text
    assert stored.read_bytes() == data[:16], "what was past the part is cut away"


# ─── B. What is refused at the start ─────────────────────────────────────────

async def test_what_is_not_a_video_or_too_big_or_unplaced_is_refused(monkeypatch):
    w = await _world()
    monkeypatch.setattr(footage, "MAX_BYTES", 100)
    async with _client() as c:
        exe = await c.post(BASE, headers=w["h"][ADMIN], json={"filename": "tool.exe", "size_bytes": 10})
        big = await c.post(BASE, headers=w["h"][ADMIN], json={"filename": "a.mov", "size_bytes": 101})
        empty = await c.post(BASE, headers=w["h"][ADMIN], json={"filename": "a.mov", "size_bytes": 0})
        future = await c.post(BASE, headers=w["h"][ADMIN], json={
            "filename": "a.mkv", "size_bytes": 10, "filmed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
        naive = await c.post(BASE, headers=w["h"][ADMIN], json={
            "filename": "a.avi", "size_bytes": 10, "filmed_at": "2026-10-01T10:00:00"})
        extra = await c.post(BASE, headers=w["h"][ADMIN], json={"filename": "a.mp4", "size_bytes": 10, "owner": "x"})
        # Somebody limited to site A must name a site, and may name only theirs.
        none = await c.post(BASE, headers=w["h"][SUPERVISOR], json={"filename": "a.mp4", "size_bytes": 10})
        other = await c.post(BASE, headers=w["h"][SUPERVISOR], json={
            "filename": "a.mp4", "size_bytes": 10, "site_id": str(w["site_b"])})
        mine = await c.post(BASE, headers=w["h"][SUPERVISOR], json={
            "filename": "A.MP4", "size_bytes": 10, "site_id": str(w["site_a"])})
        # Somebody with no site limit may leave the site out.
        unplaced = await c.post(BASE, headers=w["h"][ADMIN], json={"filename": "a.mp4", "size_bytes": 10})
    assert exe.status_code == 422 and "MP4, MOV, MKV and AVI" in exe.json()["detail"]
    assert big.status_code == 413 and empty.status_code == 422 and extra.status_code == 422
    assert future.status_code == 422 and "future" in future.json()["detail"]
    assert naive.status_code == 422 and "time zone" in naive.json()["detail"]
    assert none.status_code == 422 and "your sites" in none.json()["detail"]
    assert other.status_code == 404
    assert mine.status_code == 201 and mine.json()["title"] == "A" and mine.json()["content_type"] == "video/mp4"
    assert unplaced.status_code == 201 and unplaced.json()["site_id"] is None


# ─── C. Who may do what ──────────────────────────────────────────────────────

async def test_roles():
    w = await _world()
    async with _client() as c:
        v = await _upload(c, w, OPERATOR)
        started = {role: (await _start(c, w, role)).status_code for role in (ADMIN, OPERATOR, MANAGER, VIEWER, GUARD)}
        listed = {role: (await c.get(BASE, headers=w["h"][role])).status_code for role in (ADMIN, VIEWER, GUARD)}
        mine = (await c.get(BASE, headers=w["h"][OPERATOR])).json()
        viewer = (await c.get(BASE, headers=w["h"][VIEWER])).json()
        # Only the person who began an upload adds to it.
        other = (await _start(c, w, ADMIN)).json()
        theirs = await c.put(f"{BASE}/{other['id']}/content", params={"offset": 0}, headers=w["h"][OPERATOR],
                             content=_file()[:PART])
        delete = {role: (await c.request("DELETE", f"{BASE}/{v['id']}", headers=w["h"][role],
                                         json={"reason": "Not needed any more"})).status_code
                  for role in (OPERATOR, MANAGER, VIEWER)}
        hold = {role: (await c.post(f"{BASE}/{v['id']}/hold", headers=w["h"][role],
                                    json={"reason": "Asked for by the police"})).status_code
                for role in (OPERATOR, VIEWER)}
    assert started == {ADMIN: 201, OPERATOR: 201, MANAGER: 201, VIEWER: 403, GUARD: 403}
    assert listed == {ADMIN: 200, VIEWER: 200, GUARD: 403}
    assert mine["may"] == {"upload": True, "review": True, "delete": False, "hold": False}
    assert viewer["may"] == {"upload": False, "review": False, "delete": False, "hold": False}
    assert theirs.status_code == 403
    assert delete == {OPERATOR: 403, MANAGER: 403, VIEWER: 403}
    assert hold == {OPERATOR: 403, VIEWER: 403}


# ─── D. Sites and organisations ──────────────────────────────────────────────

async def test_a_site_limit_and_another_organisation_see_nothing_they_may_not():
    w, x = await _world(), await _world()
    async with _client() as c:
        a = await _upload(c, w, ADMIN, title="At A")
        b = await _upload(c, w, ADMIN, title="At B", site_id=str(w["site_b"]))
        nowhere = await _upload(c, w, ADMIN, title="No site", site_id=None)
        limited = (await c.get(BASE, headers=w["h"][SUPERVISOR])).json()
        everyone = (await c.get(BASE, headers=w["h"][ADMIN])).json()
        b_for_a = await c.get(f"{BASE}/{b['id']}", headers=w["h"][SUPERVISOR])
        stranger = await c.get(f"{BASE}/{a['id']}", headers=x["h"][ADMIN])
        strangers_list = (await c.get(BASE, headers=x["h"][ADMIN])).json()
        found = (await c.get(BASE, headers=w["h"][ADMIN], params={"q": "at b"})).json()
    assert [v["title"] for v in limited["items"]] == ["At A"]
    assert {v["title"] for v in everyone["items"]} == {"At A", "At B", "No site"} and everyone["total"] == 3
    assert b_for_a.status_code == 404 and stranger.status_code == 404
    assert strangers_list["total"] == 0 and nowhere["site_id"] is None
    assert [v["title"] for v in found["items"]] == ["At B"]


# ─── E. Holding and deleting ─────────────────────────────────────────────────

async def test_a_held_video_is_not_deleted_until_the_hold_is_lifted(_small_parts_on_disk):
    w = await _world()
    async with _client() as c:
        v = await _upload(c, w, OPERATOR)
        placed = await c.post(f"{BASE}/{v['id']}/hold", headers=w["h"][MANAGER],
                              json={"reason": "Asked for by the police"})
        assert placed.status_code == 201, placed.text
        again = await c.post(f"{BASE}/{v['id']}/hold", headers=w["h"][ADMIN], json={"reason": "Twice over"})
        read = (await c.get(f"{BASE}/{v['id']}", headers=w["h"][OPERATOR])).json()
        refused = await c.request("DELETE", f"{BASE}/{v['id']}", headers=w["h"][ADMIN],
                                  json={"reason": "Not needed any more"})
        lifted = await c.post(f"/api/v1/evidence-holds/{placed.json()['id']}/release", headers=w["h"][ADMIN],
                              json={"reason": "The police have their copy"})
        no_reason = await c.request("DELETE", f"{BASE}/{v['id']}", headers=w["h"][ADMIN], json={"reason": " "})
        deleted = await c.request("DELETE", f"{BASE}/{v['id']}", headers=w["h"][ADMIN],
                                  json={"reason": "Not needed any more"})
        gone = await c.get(f"{BASE}/{v['id']}", headers=w["h"][ADMIN])
    assert again.status_code == 409
    assert (read["held"], read["hold_reason"], read["deleted_in_days"]) == (True, "Asked for by the police", None)
    assert refused.status_code == 409 and "hold" in refused.json()["detail"]
    assert lifted.status_code == 200, lifted.text
    assert no_reason.status_code == 422
    assert deleted.status_code == 200 and gone.status_code == 404
    assert not _on_disk(_small_parts_on_disk, w, v["id"]).exists()
    steps = await _sql("SELECT step, kind FROM evidence_custody_events WHERE tenant_id = :t ORDER BY occurred_at",
                       {"t": w["tenant"]})
    assert [(s["step"], s["kind"]) for s in steps] == [("LOCKED", "FOOTAGE"), ("UNLOCKED", "FOOTAGE")]
    assert [a["detail"]["reason"] for a in await _audit(w, "footage.delete")] == ["Not needed any more"]


async def test_an_upload_is_cancelled_by_its_uploader_or_by_who_may_delete(_small_parts_on_disk):
    w = await _world()
    async with _client() as c:
        mine = await _upload(c, w, OPERATOR, finish=False)
        theirs = await _upload(c, w, MANAGER, finish=False)
        finished = await _upload(c, w, OPERATOR)
        own = await c.delete(f"{BASE}/{mine['id']}/upload", headers=w["h"][OPERATOR])
        not_own = await c.delete(f"{BASE}/{theirs['id']}/upload", headers=w["h"][OPERATOR])
        by_admin = await c.delete(f"{BASE}/{theirs['id']}/upload", headers=w["h"][ADMIN])
        too_late = await c.delete(f"{BASE}/{finished['id']}/upload", headers=w["h"][OPERATOR])
    assert own.status_code == 200 and not_own.status_code == 403 and by_admin.status_code == 200
    assert too_late.status_code == 409
    assert not _on_disk(_small_parts_on_disk, w, mine["id"]).exists()
    assert len(await _audit(w, "footage.upload.cancel")) == 2


# ─── F. The 30 days: the daily removal ───────────────────────────────────────

async def _retention(now: datetime | None = None) -> dict:
    engine = create_async_engine(ADMIN_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as db:
            return await footage_retention.run(db, now)
    finally:
        await engine.dispose()


async def test_after_30_days_a_video_goes_unless_held_and_an_abandoned_upload_after_a_day(_small_parts_on_disk):
    w = await _world()
    async with _client() as c:
        expired = await _upload(c, w, title="Expired")
        held = await _upload(c, w, title="Held")
        fresh = await _upload(c, w, title="Fresh")
        abandoned = await _upload(c, w, title="Abandoned", finish=False)
        working = await _upload(c, w, title="Still uploading", finish=False)
        hold = await c.post(f"{BASE}/{held['id']}/hold", headers=w["h"][ADMIN], json={"reason": "A claim"})
        assert hold.status_code == 201
    await _run([
        ("UPDATE footage_videos SET created_at = now() - interval '31 days', expires_at = now() - interval '1 day' "
         "WHERE id = ANY(CAST(:ids AS uuid[]))", {"ids": [expired["id"], held["id"]]}),
        ("UPDATE footage_videos SET updated_at = now() - interval '25 hours' WHERE id = :i", {"i": abandoned["id"]}),
    ])
    out = await _retention()
    assert out["failed"] == 0 and out["removed"] >= 2
    left = {r["title"] for r in await _sql("SELECT title FROM footage_videos WHERE tenant_id = :t", {"t": w["tenant"]})}
    assert left == {"Held", "Fresh", "Still uploading"}
    root = _small_parts_on_disk
    assert not _on_disk(root, w, expired["id"]).exists() and not _on_disk(root, w, abandoned["id"]).exists()
    assert _on_disk(root, w, held["id"]).exists() and _on_disk(root, w, working["id"]).exists()
    removed = {a["detail"]["title"]: a for a in await _audit(w, "footage.removed")}
    assert set(removed) == {"Expired", "Abandoned"}
    assert (removed["Expired"]["detail"]["why"], removed["Abandoned"]["detail"]["why"]) == ("EXPIRED", "ABANDONED")
    assert removed["Expired"]["user_id"] is None
    assert datetime.fromisoformat(fresh["expires_at"]) > datetime.now(timezone.utc) + timedelta(days=29)


# ─── G. Nothing here raises an alert or an incident ──────────────────────────

async def test_nothing_in_footage_review_raises_an_alert_or_an_incident():
    w = await _world()
    async with _client() as c:
        v = await _upload(c, w)
        await c.post(f"{BASE}/{v['id']}/hold", headers=w["h"][ADMIN], json={"reason": "A claim"})
    for table in ("alerts", "incidents"):
        rows = await _sql(f"SELECT count(*) AS n FROM {table} WHERE tenant_id = :t", {"t": w["tenant"]})
        assert rows[0]["n"] == 0, table


async def test_the_audit_never_holds_the_file_and_a_new_upload_is_announced_or_waits():
    w = await _world()
    async with _client() as c:
        done = await _upload(c, w)
    # No Redis in the test app, or a Redis: either way the video waits, QUEUED.
    assert done["status"] == "QUEUED" and isinstance(done["announced"], bool)
    for a in await _audit(w, "footage.upload.start"):
        assert set(a["detail"]) >= {"title", "filename", "size_bytes"} and "content" not in a["detail"]
    assert uuid.UUID(done["id"])
