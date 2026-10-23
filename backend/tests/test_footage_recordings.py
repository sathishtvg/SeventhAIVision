"""Footage Review, phase 5: a saved recording, copied in to be analysed.

The recording's file is written into a temporary recordings root; the copy is
made after the answer (a background task, which the test client waits for).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.services import footage
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/footage"
BYTES = b"\x00\x00\x00\x18ftypisom" + bytes(range(256)) * 4


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "recordings").mkdir()
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "EVIDENCE_ROOT", str(tmp_path / "evidence"))
    monkeypatch.setattr(footage, "RECORDINGS_ROOT", str(tmp_path / "recordings"))
    return tmp_path


async def _recording(w: dict, root, *, site: str = "site_a", status: str = "completed", write: bool = True) -> str:
    camera, stream, rec = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rel = f"{w['tenant']}/{camera}/{rec}.mp4"
    started = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
    await _run([
        ("INSERT INTO cameras (id, tenant_id, name, site_id) VALUES (:i, :t, 'Gate 1', :s)",
         {"i": camera, "t": w["tenant"], "s": w[site]}),
        ("INSERT INTO streams (id, tenant_id, camera_id, url) VALUES (:i, :t, :c, 'rtsp://example.invalid/1')",
         {"i": stream, "t": w["tenant"], "c": camera}),
        ("INSERT INTO recordings (id, tenant_id, camera_id, stream_id, site_id, started_at, ended_at, status, file_path, "
         "duration_seconds) VALUES (:i, :t, :c, :st, :s, :a, :b, :status, :p, 600)",
         {"i": rec, "t": w["tenant"], "c": camera, "st": stream, "s": w[site], "a": started,
          "b": started + timedelta(minutes=10), "status": status, "p": rel}),
    ])
    if write:
        (root / "recordings" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "recordings" / rel).write_bytes(BYTES)
    return str(rec)


async def test_a_finished_recording_is_copied_in_and_waits_to_be_analysed(_roots):
    w = await _world()
    rec = await _recording(w, _roots)
    async with _client() as c:
        r = await c.post(f"{BASE}/from-recording", headers=w["h"][OPERATOR], json={"recording_id": rec})
        assert r.status_code == 202, r.text
        v = r.json()
        again = await c.post(f"{BASE}/from-recording", headers=w["h"][OPERATOR], json={"recording_id": rec})
        after = (await c.get(f"{BASE}/{v['id']}", headers=w["h"][OPERATOR])).json()
        parts = await c.put(f"{BASE}/{v['id']}/content", params={"offset": 0}, headers=w["h"][OPERATOR], content=b"x")
    assert (v["source"], v["title"], v["place"], v["site_id"]) == ("RECORDING", "Gate 1 recording", "Gate 1", str(w["site_a"]))
    assert datetime.fromisoformat(v["filmed_at"]) == datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
    assert after["status"] == "QUEUED" and after["received_bytes"] == len(BYTES) and after["recording_id"] == rec
    copied = _roots / "evidence" / footage.original_path(w["tenant"], v["id"], ".mp4")
    assert copied.read_bytes() == BYTES, "a copy, byte for byte"
    assert (_roots / "recordings").rglob("*.mp4").__next__().exists(), "the recording itself is untouched"
    assert again.status_code == 409 and again.json()["detail"]["video_id"] == v["id"]
    assert parts.status_code == 409, "a copy takes no parts"
    assert [a["detail"]["recording_id"] for a in await _audit(w, "footage.from_recording")] == [rec]


async def test_only_who_may_upload_and_read_recordings_and_only_a_finished_one_they_may_see(_roots):
    w, x = await _world(), await _world()
    rec = await _recording(w, _roots)
    at_b = await _recording(w, _roots, site="site_b")
    running = await _recording(w, _roots, status="recording")
    gone = await _recording(w, _roots, write=False)
    async with _client() as c:
        roles = {role: (await c.post(f"{BASE}/from-recording", headers=w["h"][role], json={"recording_id": rec})).status_code
                 for role in (VIEWER, GUARD)}
        limited = await c.post(f"{BASE}/from-recording", headers=w["h"][SUPERVISOR], json={"recording_id": at_b})
        stranger = await c.post(f"{BASE}/from-recording", headers=x["h"][ADMIN], json={"recording_id": rec})
        unfinished = await c.post(f"{BASE}/from-recording", headers=w["h"][MANAGER], json={"recording_id": running})
        missing = await c.post(f"{BASE}/from-recording", headers=w["h"][MANAGER], json={"recording_id": gone})
    assert roles == {VIEWER: 403, GUARD: 403}
    assert (limited.status_code, stranger.status_code) == (404, 404)
    assert unfinished.status_code == 409 and "finished" in unfinished.json()["detail"]
    assert missing.status_code == 409 and "no longer kept" in missing.json()["detail"]


async def test_a_copy_that_fails_says_so_and_the_copy_outlives_its_recording(_roots, monkeypatch):
    w = await _world()
    rec = await _recording(w, _roots)

    def broken(source, path):
        raise OSError("disk full")
    monkeypatch.setattr(footage, "copy_in", broken)
    async with _client() as c:
        v = (await c.post(f"{BASE}/from-recording", headers=w["h"][ADMIN], json={"recording_id": rec})).json()
        after = (await c.get(f"{BASE}/{v['id']}", headers=w["h"][ADMIN])).json()
    assert (after["status"], after["error"]) == ("FAILED", "The recording could not be copied into Footage Review.")
    # A failed copy does not stand in the way of trying again; a recording removed by its own retention leaves its copy.
    monkeypatch.undo()
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "EVIDENCE_ROOT", str(_roots / "evidence"))
    monkeypatch.setattr(footage, "RECORDINGS_ROOT", str(_roots / "recordings"))
    async with _client() as c:
        retry = await c.post(f"{BASE}/from-recording", headers=w["h"][ADMIN], json={"recording_id": rec})
    assert retry.status_code == 202
    await _sql("DELETE FROM recordings WHERE id = :r", {"r": rec})
    left = await _sql("SELECT recording_id, status FROM footage_videos WHERE id = :i", {"i": retry.json()["id"]})
    assert left == [{"recording_id": None, "status": "QUEUED"}]
