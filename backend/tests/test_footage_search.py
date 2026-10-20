"""Footage Review, phase 2: searching by description, the frames, playing a video, analysing again.

  A — Moments: neighbouring seconds are one result, and how strong it is is said in words
  B — Search through the API: ranked, scoped, audited with its wording
  C — When the worker cannot be reached
  D — A frame and the video, for the browser, with the token in the address
  E — Analysing a video again

The words' vector is the footage worker's to make; here it is stood in for, and
the frames' vectors are written straight into the database, so what is tested
is what the API does with them. The worker itself is tested in
ai-worker/tests/test_footage_worker.py, and the two together once, for real.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.core.config import settings
from app.services import footage
from app.services import footage_search as finder
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/footage"
MODEL = "test-model@1"
DIM = 768


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


#: The real one, before the tests stand in for it.
REAL_EMBED = finder.embed

#: The scale of the model, as the worker sends it (ai-worker/worker/footage/model.py).
SCALE, BIAS = math.exp(4.724453449249268), -16.771724700927734


def _at(likeness: float, toward: int = 0, away: int = 2) -> np.ndarray:
    """A unit vector whose likeness to axis `toward` is exactly `likeness`."""
    v = np.zeros(DIM)
    v[toward], v[away] = likeness, math.sqrt(1 - likeness ** 2)
    return v


#: What is searched for, something else, and the background most frames look like: far from both.
TARGET = _unit(np.eye(DIM)[0])
OTHER = _unit(np.eye(DIM)[1])
BACKGROUND = _unit(np.eye(DIM)[2])


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v.tolist()) + "]"


def _words(vector: np.ndarray) -> finder.Words:
    return finder.Words(vector.tolist(), MODEL, SCALE, BIAS)


@pytest.fixture(autouse=True)
def _stand_ins(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "EVIDENCE_ROOT", str(tmp_path))

    async def embed(words: str, **_):
        return _words(TARGET if "red" in words else OTHER)
    monkeypatch.setattr(finder, "embed", embed)
    return tmp_path


async def _video(w: dict, *, site: str | None = "site_a", title: str = "Lobby", status: str = "READY",
                 model: str = MODEL, frames: dict[int, np.ndarray] | None = None, seconds: int = 60,
                 filmed_at: datetime | None = None, playable: str | None = "ORIGINAL", root=None) -> str:
    vid = uuid.uuid4()
    path = footage.original_path(w["tenant"], vid, ".mp4")
    stmts = [("""
        INSERT INTO footage_videos (id, tenant_id, title, original_filename, size_bytes, received_bytes, part_size,
                                    storage_path, site_id, status, expires_at, index_model, frames_indexed,
                                    duration_seconds, filmed_at, playable, content_type, uploaded_by_user_id, error)
        VALUES (:i, :t, :title, 'x.mp4', 10, 10, 8, :p, :s, :st, now() + interval '30 days', :m, :n, :d, :f, :pl,
                'video/mp4', :u, :err)
    """, {"i": vid, "t": w["tenant"], "title": title, "p": path, "s": w[site] if site else None, "st": status,
          "m": model, "n": seconds, "d": seconds, "f": filmed_at, "pl": playable, "u": w["users"][ADMIN],
          "err": "This file has no picture in it." if status == "FAILED" else None})]
    given = frames or {}
    for second in range(seconds):
        stmts.append(("INSERT INTO footage_frames (tenant_id, video_id, second, thumb_path, embedding, model_version) "
                      "VALUES (:t, :v, :s, :p, CAST(:e AS vector), :m)",
                      {"t": w["tenant"], "v": vid, "s": second, "p": footage.frame_path(path, second),
                       "e": _vec(given.get(second, BACKGROUND)), "m": model}))
    await _run(stmts)
    if root is not None:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        for second in range(seconds):
            frame = root / footage.frame_path(path, second)
            frame.parent.mkdir(parents=True, exist_ok=True)
            frame.write_bytes(b"\xff\xd8\xff\xe0" + bytes([second % 256]) * 16)
    return str(vid)


# ─── A. Moments ──────────────────────────────────────────────────────────────

async def test_neighbouring_seconds_are_one_moment_shown_by_its_best_frame():
    found = finder.moments([("a", 10, 0.30), ("a", 11, 0.34), ("a", 14, 0.31), ("a", 18, 0.29), ("b", 11, 0.20),
                            ("a", 40, 0.33)])
    assert [(m.video_id, m.start, m.end, m.best, m.likeness) for m in found] == [
        ("a", 10, 14, 11, 0.34), ("a", 40, 40, 40, 0.33), ("a", 18, 18, 18, 0.29), ("b", 11, 11, 11, 0.20)]


async def test_how_likely_a_match_is_is_the_probability_of_the_model_said_in_words():
    words = _words(TARGET)
    assert math.isclose(words.probability(-BIAS / SCALE), 0.5)
    assert math.isclose(words.probability(words.likeness_for(0.1)), 0.1)
    for likeness, label in ((0.20, "HIGH"), (0.135, "MEDIUM"), (0.115, "LOW")):
        assert finder.label(finder.Moment("a", 1, 1, 1, likeness), words).label == label, likeness
    assert words.probability(-1.0) == 0.0
    assert finder.STRENGTH_WORDS == {"HIGH": "Strong match", "MEDIUM": "Possible match", "LOW": "Weak match"}


# ─── B. Search through the API ───────────────────────────────────────────────

async def test_a_search_finds_the_moment_that_looks_like_the_words_and_is_audited_with_them():
    w = await _world()
    filmed = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
    lobby = await _video(w, title="Lobby", frames={20: _at(0.30), 21: _at(0.32), 22: _at(0.28), 45: OTHER},
                         filmed_at=filmed)
    await _video(w, title="Gate", frames={5: _at(0.135), 40: _at(0.115)})
    async with _client() as c:
        r = await c.post(f"{BASE}/search", headers=w["h"][OPERATOR], json={"text": "  a man in a  red jacket "})
    assert r.status_code == 200, r.text
    body = r.json()
    top = body["moments"][0]
    assert (top["video_id"], top["title"], top["start"], top["end"], top["label"]) == (lobby, "Lobby", 20, 22, "HIGH")
    assert top["best"] == 21 and top["label_words"] == "Strong match" and top["match"] > 0.99
    labels = [(m["title"], m["start"], m["label"]) for m in body["moments"]]
    assert labels == [("Lobby", 20, "HIGH"), ("Gate", 5, "MEDIUM"), ("Gate", 40, "LOW")], "the background is not offered"
    assert datetime.fromisoformat(top["at"]) == filmed + timedelta(seconds=top["best"])
    assert body["strong"] is True and body["videos_searched"] == 2 and body["frames_searched"] == 120
    assert body["model_version"] == MODEL and body["text"] == "a man in a red jacket"
    asked = await _audit(w, "footage.search")
    assert [a["detail"]["text"] for a in asked] == ["a man in a red jacket"]
    assert asked[0]["detail"]["moments"] == len(body["moments"])


async def test_a_search_with_nothing_like_it_says_so():
    w = await _world()
    await _video(w, title="Quiet corridor")
    async with _client() as c:
        body = (await c.post(f"{BASE}/search", headers=w["h"][VIEWER], json={"text": "a red bicycle"})).json()
    assert (body["strong"], body["moments"]) == (False, []), "nothing like it: nothing is offered"


async def test_a_search_sees_only_ready_videos_of_the_right_model_the_person_may_see():
    w, x = await _world(), await _world()
    at_a = await _video(w, title="At A", frames={3: TARGET})
    await _video(w, title="At B", site="site_b", frames={3: TARGET})
    await _video(w, title="Waiting", status="QUEUED", frames={3: TARGET})
    await _video(w, title="Old model", model="older@0", frames={3: TARGET})
    await _video(x, title="Elsewhere", frames={3: TARGET})
    async with _client() as c:
        limited = (await c.post(f"{BASE}/search", headers=w["h"][SUPERVISOR], json={"text": "red"})).json()
        everyone = (await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={"text": "red"})).json()
        one = (await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={"text": "red", "video_id": at_a})).json()
        theirs = await c.post(f"{BASE}/search", headers=x["h"][ADMIN], json={"text": "red", "video_id": at_a})
        guard = await c.post(f"{BASE}/search", headers=w["h"][GUARD], json={"text": "red"})
    assert {m["title"] for m in limited["moments"]} == {"At A"}
    assert {m["title"] for m in everyone["moments"]} == {"At A", "At B"}
    assert everyone["not_searched"] == 1, "the video indexed by another model is said, not searched"
    assert {m["video_id"] for m in one["moments"]} == {at_a}
    assert theirs.status_code == 404 and guard.status_code == 403


async def test_a_period_searches_only_videos_filmed_in_it():
    w = await _world()
    day = datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc)
    await _video(w, title="Morning", frames={3: TARGET}, filmed_at=day + timedelta(hours=9), seconds=60)
    await _video(w, title="Night", frames={3: TARGET}, filmed_at=day + timedelta(hours=22), seconds=60)
    await _video(w, title="Unknown time", frames={3: TARGET})
    async with _client() as c:
        body = (await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={
            "text": "red", "filmed_from": (day + timedelta(hours=8)).isoformat(),
            "filmed_to": (day + timedelta(hours=12)).isoformat()})).json()
        naive = await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={"text": "red", "filmed_from": "2026-10-03T08:00:00"})
        empty = await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={"text": "   "})
    assert {m["title"] for m in body["moments"]} == {"Morning"}
    assert naive.status_code == 422 and empty.status_code == 422


# ─── C. When the worker cannot be reached ────────────────────────────────────

async def test_a_search_says_so_when_the_footage_worker_is_not_running(monkeypatch):
    w = await _world()
    await _video(w)

    async def down(words: str, **_):
        raise finder.SearchUnavailable("Search is not available: the footage worker is not running.")
    monkeypatch.setattr(finder, "embed", down)
    async with _client() as c:
        r = await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={"text": "red"})
    assert r.status_code == 503 and "footage worker" in r.json()["detail"]


async def test_the_real_embed_says_the_worker_is_not_running_when_it_cannot_be_reached(monkeypatch):
    monkeypatch.setattr(finder, "WORKER_URL", "http://127.0.0.1:9")
    with pytest.raises(finder.SearchUnavailable):
        await REAL_EMBED("a red jacket", timeout=2)


# ─── D. A frame and the video, for the browser ───────────────────────────────

def _token(w: dict, role: int) -> str:
    return w["h"][role]["Authorization"].removeprefix("Bearer ")


async def test_a_frame_and_the_video_are_served_to_who_may_see_them(_stand_ins):
    w, x = await _world(), await _world()
    vid = await _video(w, seconds=3, root=_stand_ins)
    at_b = await _video(w, site="site_b", seconds=3, root=_stand_ins)
    async with _client() as c:
        frame = await c.get(f"{BASE}/{vid}/frames/2.jpg", params={"token": _token(w, VIEWER)})
        missing = await c.get(f"{BASE}/{vid}/frames/9.jpg", params={"token": _token(w, VIEWER)})
        guard = await c.get(f"{BASE}/{vid}/frames/2.jpg", params={"token": _token(w, GUARD)})
        other_site = await c.get(f"{BASE}/{at_b}/frames/0.jpg", params={"token": _token(w, SUPERVISOR)})
        stranger = await c.get(f"{BASE}/{vid}/frames/0.jpg", params={"token": _token(x, ADMIN)})
        bad = await c.get(f"{BASE}/{vid}/frames/0.jpg", params={"token": "nonsense"})
        played = await c.get(f"{BASE}/{vid}/video", params={"token": _token(w, OPERATOR)})
        part = await c.get(f"{BASE}/{vid}/video", params={"token": _token(w, OPERATOR)},
                           headers={"Range": "bytes=10-19"})
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
    assert frame.content == b"\xff\xd8\xff\xe0" + bytes([2]) * 16
    assert missing.status_code == 404 and guard.status_code == 403 and other_site.status_code == 404
    assert stranger.status_code == 404 and bad.status_code == 401
    assert played.status_code == 200 and played.content.startswith(b"\x00\x00\x00\x18ftyp")
    assert part.status_code == 206 and len(part.content) == 10
    assert len(await _audit(w, "footage.play")) == 1, "opening is audited; the player's later parts are not"


async def test_a_video_no_browser_can_play_is_said_to_be_so(_stand_ins):
    w = await _world()
    vid = await _video(w, seconds=1, playable="NONE", root=_stand_ins)
    waiting = await _video(w, seconds=1, status="QUEUED", playable=None, root=_stand_ins)
    async with _client() as c:
        none = await c.get(f"{BASE}/{vid}/video", params={"token": _token(w, ADMIN)})
        early = await c.get(f"{BASE}/{waiting}/video", params={"token": _token(w, ADMIN)})
    assert none.status_code == 409 and "cannot be played" in none.json()["detail"]
    assert early.status_code == 409


# ─── E. Analysing a video again ──────────────────────────────────────────────

async def test_a_ready_or_failed_video_is_analysed_again_by_who_may_review():
    w = await _world()
    ready = await _video(w, seconds=1)
    failed = await _video(w, seconds=1, status="FAILED")
    await _sql("UPDATE footage_videos SET attempts = 3 WHERE id = :i", {"i": failed})
    waiting = await _video(w, seconds=1, status="QUEUED")
    async with _client() as c:
        again = await c.post(f"{BASE}/{ready}/reanalyse", headers=w["h"][OPERATOR])
        retry = await c.post(f"{BASE}/{failed}/reanalyse", headers=w["h"][OPERATOR])
        not_yet = await c.post(f"{BASE}/{waiting}/reanalyse", headers=w["h"][OPERATOR])
        viewer = await c.post(f"{BASE}/{ready}/reanalyse", headers=w["h"][VIEWER])
    assert again.status_code == 200 and again.json()["status"] == "QUEUED"
    assert retry.status_code == 200 and (retry.json()["error"], retry.json()["progress"]) == (None, 0)
    rows = await _sql("SELECT attempts FROM footage_videos WHERE id = :i", {"i": failed})
    assert rows[0]["attempts"] == 0
    assert not_yet.status_code == 409 and viewer.status_code == 403
    assert len(await _audit(w, "footage.reanalyse")) == 2


async def test_frames_go_with_their_video():
    w = await _world()
    vid = await _video(w, seconds=5)
    await _sql("DELETE FROM footage_videos WHERE id = :i", {"i": vid})
    left = await _sql("SELECT count(*) AS n FROM footage_frames WHERE video_id = :i", {"i": vid})
    assert left[0]["n"] == 0
