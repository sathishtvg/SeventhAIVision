"""Privacy zones, applied.

A privacy zone was stored and listed, and nothing applied one. What is under a
zone is now painted out of the frame before the frame is given to anything -
and the claim worth testing is the one somebody relies on without looking: that
there is no way for the unmasked picture to leave. So each of the four paths is
run here with a camera that is not real, and what came out of it is opened and
looked at.

  A — What a zone is: the rules of a polygon, and of what cannot be read as one
  B — Painting: inside a zone is the fill colour, outside is untouched
  C — A loop's zones: nothing before the first read, the last ones after a
      failed one
  D — The four paths: what the AI workers are given, the live view, a
      recording, and the image a patrol keeps
  E — HLS: a masked camera has none, and a session running for it is stopped
  F — The API: its rules, who may, and the audit lines
  G — Two organisations, read as the application's own database role
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app import ingestion_main as ingestion
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import pdpa as api
from app.routers import streams
from app.services import hls_stream, privacy_mask
from app.services import vpatrol_snapshot as snap
from shared.events import FrameJob
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _drone, _run, _sql, _world
from tests.test_investigation_search import _audit
from tests.test_vpatrol_background_jobs_rls import APP_DATABASE_URL

ZONES = "/api/v1/privacy/zones"
#: The left half of the picture.
LEFT = [{"x": 0, "y": 0}, {"x": 0.5, "y": 0}, {"x": 0.5, "y": 1}, {"x": 0, "y": 1}]
#: The bottom right corner.
CORNER = [{"x": 0.75, "y": 0.75}, {"x": 1, "y": 0.75}, {"x": 1, "y": 1}, {"x": 0.75, "y": 1}]
WIDTH, HEIGHT = 64, 48


@pytest.fixture(autouse=True)
def _nothing_remembered():
    """What is remembered of which camera is masked is this process's, not a test's."""
    privacy_mask._MASKED.clear()
    yield
    privacy_mask._MASKED.clear()
    hls_stream._sessions.clear()


def _white() -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), 255, np.uint8)


def _picture(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def _left_is_out(frame: np.ndarray) -> bool:
    """The left of the picture is black and the right is as it was. The columns
    beside the edge are left alone: JPEG smears an edge."""
    return bool(frame[:, :28].max() < 30 and frame[:, 40:].min() > 225)


def _untouched(frame: np.ndarray) -> bool:
    return bool(frame.min() > 225)


async def _camera(w: dict, name: str = "Gate 1", site: str = "site_a") -> tuple[uuid.UUID, uuid.UUID]:
    camera, stream = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO cameras (id, tenant_id, name, site_id, is_active) VALUES (:i,:t,:n,:s,TRUE)",
         {"i": camera, "t": w["tenant"], "n": name, "s": w[site]}),
        ("INSERT INTO streams (id, tenant_id, camera_id, url, status) VALUES (:i,:t,:c,:u,'online')",
         {"i": stream, "t": w["tenant"], "c": camera, "u": f"rtsp://cam/{camera.hex[:8]}"}),
    ])
    return camera, stream


async def _zone(w: dict, camera, polygon=LEFT, *, active: bool = True, name: str = "Neighbour's window") -> uuid.UUID:
    import json

    zone = uuid.uuid4()
    await _sql("INSERT INTO privacy_zones (id, tenant_id, camera_id, name, polygon, is_active) "
               "VALUES (:i,:t,:c,:n,CAST(:p AS jsonb),:a)",
               {"i": zone, "t": w["tenant"], "c": camera, "n": name,
                "p": polygon if isinstance(polygon, str) else json.dumps(polygon), "a": active})
    return zone


def _token(w: dict, role: int = ADMIN) -> str:
    return w["h"][role]["Authorization"].split()[1]


async def _until(done, *, seconds: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not done():
        assert asyncio.get_running_loop().time() < deadline, "it did not happen in time"
        await asyncio.sleep(0.02)


class _Camera:
    """Stands in for cv2.VideoCapture: a few white frames, then nothing."""
    frames = 3

    def __init__(self, *args, **kwargs):
        self.left = self.frames

    def isOpened(self):  # noqa: N802 - cv2's own name
        return True

    def set(self, *args):
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FRAME_WIDTH: WIDTH, cv2.CAP_PROP_FRAME_HEIGHT: HEIGHT}.get(prop, 0)

    def read(self):
        if self.left <= 0:
            return False, None
        self.left -= 1
        return True, _white()

    def release(self):
        pass


# ─── A. What a zone is ───────────────────────────────────────────────────────

def test_a_zone_is_three_to_sixty_four_points_inside_the_picture_that_cover_some_of_it():
    assert privacy_mask.points_of(LEFT) == ((0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0))
    assert (privacy_mask.LEAST_POINTS, privacy_mask.MOST_POINTS, privacy_mask.MOST_ZONES) == (3, 64, 20)
    ring = [{"x": 0.5 + 0.4 * np.cos(a), "y": 0.5 + 0.4 * np.sin(a)} for a in np.linspace(0, 6.2, 64)]
    assert len(privacy_mask.points_of([{"x": float(p["x"]), "y": float(p["y"])} for p in ring])) == 64
    for wrong, said in (
        ("a square", "list of points"), (LEFT[:2], "3 to 64 points"), (LEFT * 17, "3 to 64 points"),
        ([*LEFT[:3], {"x": 1.2, "y": 0.5}], "inside the picture"), ([*LEFT[:3], {"x": -0.1, "y": 0.5}], "inside the picture"),
        ([*LEFT[:3], {"x": "0.5", "y": 0.5}], "are numbers"), ([*LEFT[:3], {"x": True, "y": 0.5}], "are numbers"),
        ([*LEFT[:3], {"x": float("nan"), "y": 0.5}], "are numbers"), ([*LEFT[:3], {"x": 0.5}], "an x and a y"),
        ([*LEFT[:3], {"x": 0.5, "y": 0.5, "z": 1}], "an x and a y"), ([*LEFT[:3], [0.5, 0.5]], "an x and a y"),
        # Three points in a line, and a point three times: a zone that would be believed and mask nothing.
        ([{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.5}, {"x": 0.9, "y": 0.9}], "cover some of the picture"),
        ([{"x": 0.4, "y": 0.4}] * 3, "cover some of the picture"),
    ):
        with pytest.raises(ValueError, match=said):
            privacy_mask.points_of(wrong)


def test_a_colour_is_written_as_it_is_stored_and_anything_else_is_black():
    assert privacy_mask.colour_of("#000000") == (0, 0, 0)
    # Stored red, green, blue; painted blue, green, red.
    assert privacy_mask.colour_of("#FF8000") == (0, 128, 255)
    for wrong in ("black", "#FFF", "#GGGGGG", "", None, 0):
        assert privacy_mask.colour_of(wrong) == privacy_mask.BLACK and not privacy_mask.is_colour(wrong)
    assert privacy_mask.is_colour("#a1B2c3")


def test_a_stored_zone_that_cannot_be_read_as_a_polygon_masks_the_whole_picture():
    assert privacy_mask.zone_of(LEFT, "#000000") == privacy_mask.Zone(((0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)))
    # Stored as text is read as what it says.
    assert privacy_mask.zone_of('[{"x":0,"y":0},{"x":0.5,"y":0},{"x":0.5,"y":1}]', None).points == ((0.0, 0.0), (0.5, 0.0), (0.5, 1.0))
    for stored in ("not json", "{}", [], [{"x": 2, "y": 2}] * 3, None):
        zone = privacy_mask.zone_of(stored, "#FFFFFF")
        assert zone == privacy_mask.Zone(None, privacy_mask.BLACK), stored
        assert privacy_mask.paint(_white(), (zone,)).max() == 0, "where it was meant to be is not known"


# ─── B. Painting ─────────────────────────────────────────────────────────────

def test_inside_a_zone_is_the_fill_colour_and_outside_is_untouched():
    frame = _white()
    painted = privacy_mask.paint(frame, (privacy_mask.zone_of(LEFT, "#000000"),))
    assert painted is frame, "painted into the frame: there is no other copy of it"
    # Corners are shares of the picture: half of 64 columns, to the pixel.
    assert frame[:, :33].max() == 0 and frame[:, 33:].min() == 255
    # Two zones, each its own colour; what neither covers is as it was.
    frame = _white()
    privacy_mask.paint(frame, (privacy_mask.zone_of(LEFT, "#000000"), privacy_mask.zone_of(CORNER, "#FF0000")))
    assert frame[:, :33].max() == 0 and tuple(frame[HEIGHT - 2, WIDTH - 2]) == (0, 0, 255)
    assert frame[:30, 40:].min() == 255
    # A picture of another size is masked in the same place.
    wide = np.full((90, 160, 3), 255, np.uint8)
    privacy_mask.paint(wide, (privacy_mask.zone_of(LEFT, "#000000"),))
    assert wide[:, :80].max() == 0 and wide[:, 82:].min() == 255


def test_a_camera_with_no_zones_is_given_back_as_it_came():
    frame = _white()
    assert privacy_mask.paint(frame, ()) is frame and frame.min() == 255
    assert privacy_mask.paint(None, (privacy_mask.zone_of(LEFT, None),)) is None


def test_an_image_file_is_painted_where_it_lies(tmp_path):
    path = str(tmp_path / "frame.jpg")
    cv2.imwrite(path, _white())
    before = Path(path).read_bytes()
    assert privacy_mask.paint_file(path, ()) and Path(path).read_bytes() == before, "no zones: the file is not rewritten"
    assert privacy_mask.paint_file(path, (privacy_mask.zone_of(LEFT, "#000000"),))
    assert _left_is_out(cv2.imread(path))
    Path(path).write_bytes(b"not an image")
    assert privacy_mask.paint_file(path, (privacy_mask.zone_of(LEFT, "#000000"),)) is False


# ─── C. A loop's zones ───────────────────────────────────────────────────────

async def test_a_loop_has_nothing_to_paint_with_until_its_first_read_and_keeps_the_last_after_a_failed_one():
    now = [0.0]
    answers: list = [RuntimeError("the database is away"), (privacy_mask.zone_of(LEFT, None),),
                     RuntimeError("away again"), ()]

    async def reader(tenant, camera):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    keeper = privacy_mask.Keeper("t", "c", reader=reader, clock=lambda: now[0])
    assert not keeper.loaded and keeper.due
    with pytest.raises(privacy_mask.NotLoaded):
        keeper.paint(_white())
    # The first read fails: still nothing, and it asks again soon rather than in ten seconds.
    assert await keeper.keep_up() is False and not keeper.due
    with pytest.raises(privacy_mask.NotLoaded):
        keeper.zones
    now[0] += privacy_mask.RETRY_SECONDS
    assert keeper.due and await keeper.keep_up() is True
    assert _left_is_out(keeper.paint(_white()))
    # Nothing is read again until it is time to.
    now[0] += privacy_mask.REFRESH_SECONDS - 0.1
    assert not keeper.due and await keeper.keep_up() is True and len(answers) == 2
    # A read that fails changes nothing: the zones of the last that succeeded are kept.
    now[0] += 0.1
    assert await keeper.keep_up() is True and len(answers) == 1
    assert _left_is_out(keeper.paint(_white()))
    # And a zone that was deleted stops being painted at the next read.
    now[0] += privacy_mask.REFRESH_SECONDS
    assert await keeper.keep_up() is True and keeper.zones == () and _untouched(keeper.paint(_white()))


async def test_whether_a_camera_is_masked_is_asked_at_most_every_ten_seconds_and_is_never_guessed():
    now, asked = [0.0], []

    async def reader(tenant, camera):
        asked.append(camera)
        if camera == "broken":
            raise RuntimeError("the database is away")
        return (privacy_mask.zone_of(LEFT, None),) if camera == "masked" else ()

    kw = {"reader": reader, "clock": lambda: now[0]}
    assert await privacy_mask.is_masked("t", "masked", **kw) is True
    assert await privacy_mask.is_masked("t", "plain", **kw) is False
    assert await privacy_mask.is_masked("t", "masked", **kw) is True and asked == ["masked", "plain"]
    now[0] += privacy_mask.REFRESH_SECONDS
    assert await privacy_mask.is_masked("t", "masked", **kw) is True and asked == ["masked", "plain", "masked"]
    # A zone drawn in this process is asked about at once.
    privacy_mask.forget("plain")
    assert await privacy_mask.is_masked("t", "plain", **kw) is False and asked[-1] == "plain"
    with pytest.raises(RuntimeError):
        await privacy_mask.is_masked("t", "broken", **kw)
    assert "broken" not in privacy_mask._MASKED, "what could not be read is not remembered as an answer"


# ─── D. The four paths ───────────────────────────────────────────────────────

class _Redis:
    def __init__(self):
        self.published: list[FrameJob] = []

    async def xadd(self, stream, fields, **kwargs):
        self.published.append(FrameJob.from_redis_fields(fields))


async def _ingest(monkeypatch, w, camera, stream, redis) -> asyncio.Task:
    monkeypatch.setattr(ingestion, "_open_capture", lambda url: object())
    monkeypatch.setattr(ingestion, "_read_frame", lambda cap: (True, _white()))
    monkeypatch.setattr(ingestion, "SNAPSHOT_INTERVAL_SECONDS", 0.01)
    ingestion._frame_buffers.pop(str(camera), None)
    return asyncio.create_task(ingestion.run_camera_loop(redis, w["tenant"], camera, stream, "rtsp://cam/1", ["intrusion"]))


async def test_what_the_ai_workers_are_given_is_the_masked_picture_and_so_is_what_clips_are_cut_from(monkeypatch):
    w = await _world()
    camera, stream = await _camera(w)
    plain, plain_stream = await _camera(w, "Gate 2")
    await _zone(w, camera)
    redis = _Redis()
    tasks = [await _ingest(monkeypatch, w, camera, stream, redis), await _ingest(monkeypatch, w, plain, plain_stream, redis)]
    try:
        await _until(lambda: {j.camera_id for j in redis.published} == {camera, plain} and len(redis.published) >= 4)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for job in redis.published:
        frame = _picture(base64.b64decode(job.frame_jpeg_b64))
        assert (job.frame_width, job.frame_height) == (WIDTH, HEIGHT)
        assert _left_is_out(frame) if job.camera_id == camera else _untouched(frame), job.camera_id
    # The buffer event clips are cut from holds the same frames: there is no unmasked one to cut.
    assert all(_left_is_out(_picture(entry[0])) for entry in ingestion._frame_buffers[str(camera)])
    assert all(_untouched(_picture(entry[0])) for entry in ingestion._frame_buffers[str(plain)])


async def test_nothing_is_given_to_the_ai_workers_until_the_zones_have_been_read(monkeypatch):
    w = await _world()
    camera, stream = await _camera(w)
    await _zone(w, camera)
    real, tried = privacy_mask.read_for, []

    async def away(tenant, cam):
        tried.append(cam)
        raise RuntimeError("the database is away")

    monkeypatch.setattr(privacy_mask, "read_for", away)
    monkeypatch.setattr(privacy_mask, "RETRY_SECONDS", 0.01)
    redis = _Redis()
    task = await _ingest(monkeypatch, w, camera, stream, redis)
    try:
        await _until(lambda: len(tried) >= 4)
        assert redis.published == [] and not ingestion._frame_buffers.get(str(camera))
        # They can be read again: from then on frames are given, masked.
        monkeypatch.setattr(privacy_mask, "read_for", real)
        await _until(lambda: len(redis.published) >= 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert all(_left_is_out(_picture(base64.b64decode(j.frame_jpeg_b64))) for j in redis.published)


def _frames_of(body: bytes) -> list[np.ndarray]:
    parts = [p.split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n") for p in body.split(b"--frame") if b"\r\n\r\n" in p]
    return [_picture(p) for p in parts]


async def test_the_live_view_is_the_masked_picture_and_is_not_shown_when_the_zones_cannot_be_read(monkeypatch):
    w = await _world()
    camera, stream = await _camera(w)
    plain, plain_stream = await _camera(w, "Gate 2")
    await _zone(w, camera)
    await _zone(w, camera, CORNER, active=False, name="Switched off")
    monkeypatch.setattr(streams.cv2, "VideoCapture", _Camera)
    async with _client() as c:
        seen = await c.get(f"/api/v1/cameras/{camera}/streams/{stream}/live", params={"token": _token(w, GUARD)})
        other = await c.get(f"/api/v1/cameras/{plain}/streams/{plain_stream}/live", params={"token": _token(w, GUARD)})
        assert seen.status_code == other.status_code == 200
        frames = _frames_of(seen.content)
        assert len(frames) == _Camera.frames and all(_left_is_out(f) for f in frames)
        # A zone that is not active masks nothing: the corner is as it was.
        assert all(f[HEIGHT - 4:, WIDTH - 8:].min() > 225 for f in frames)
        assert all(_untouched(f) for f in _frames_of(other.content))

        async def away(tenant, cam):
            raise RuntimeError("the database is away")

        monkeypatch.setattr(privacy_mask, "read_for", away)
        refused = await c.get(f"/api/v1/cameras/{camera}/streams/{stream}/live", params={"token": _token(w, GUARD)})
        assert refused.status_code == 503 and "privacy zones could not be read" in refused.json()["detail"]
        # Not for a camera with no zones either: that it has none is what could not be read.
        assert (await c.get(f"/api/v1/cameras/{plain}/streams/{plain_stream}/live",
                            params={"token": _token(w, GUARD)})).status_code == 503


class _Writer:
    """Stands in for H264Writer: keeps what it was given to encode."""
    written: list[np.ndarray] = []

    def __init__(self, path, fps, size):
        self.path = path

    def write(self, frame):
        _Writer.written.append(frame.copy())

    def release(self):
        Path(self.path).write_bytes(b"mp4")


async def test_a_recording_is_of_the_masked_picture_and_nothing_is_recorded_before_the_zones_are_read(monkeypatch, tmp_path):
    w = await _world()
    camera, _ = await _camera(w)
    await _zone(w, camera)
    monkeypatch.setattr(streams.cv2, "VideoCapture", _Camera)
    monkeypatch.setattr(streams, "H264Writer", _Writer)
    _Writer.written = []
    mask = privacy_mask.Keeper(w["tenant"], camera)
    assert await mask.refresh()
    count, size = await asyncio.to_thread(streams._record_to_file, str(tmp_path / "a" / "one.mp4"), "rtsp://cam/1",
                                          asyncio.Event(), mask)
    assert (count, size) == (_Camera.frames, 3) and len(_Writer.written) == _Camera.frames
    assert all(frame[:, :33].max() == 0 and frame[:, 33:].min() == 255 for frame in _Writer.written)
    # A recorder given zones that were never read writes no frame at all.
    _Writer.written = []
    with pytest.raises(privacy_mask.NotLoaded):
        streams._record_to_file(str(tmp_path / "a" / "two.mp4"), "rtsp://cam/1", asyncio.Event(),
                                privacy_mask.Keeper(w["tenant"], camera))
    assert _Writer.written == []

    # The task that runs it waits for the zones, however long they take ...
    real, order = privacy_mask.read_for, []

    async def slow_to_come(tenant, cam):
        order.append("read")
        if order.count("read") < 3:
            raise RuntimeError("the database is away")
        return await real(tenant, cam)

    def recorded(file_path, rtsp_url, stop_event, mask):
        order.append("record")
        assert mask.loaded and len(mask.zones) == 1
        return 0, 0

    monkeypatch.setattr(privacy_mask, "read_for", slow_to_come)
    monkeypatch.setattr(privacy_mask, "RETRY_SECONDS", 0.01)
    monkeypatch.setattr(streams, "_record_to_file", recorded)
    await streams._recording_task(str(uuid.uuid4()), str(w["tenant"]), str(camera), "rtsp://cam/1",
                                  str(tmp_path / "three.mp4"), asyncio.Event())
    assert order == ["read", "read", "read", "record"]

    # ... and if it is stopped before they could be read, it has recorded nothing.
    async def away(tenant, cam):
        order.append("read")
        raise RuntimeError("the database is away")

    order.clear()
    monkeypatch.setattr(privacy_mask, "read_for", away)
    stopped = asyncio.Event()
    stopped.set()
    await streams._recording_task(str(uuid.uuid4()), str(w["tenant"]), str(camera), "rtsp://cam/1",
                                  str(tmp_path / "four.mp4"), stopped)
    assert order == ["read"]


async def _patrol_camera(w: dict, camera) -> uuid.UUID:
    session, row = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO virtual_patrol_sessions (id, tenant_id, site_id, patrol_number, schedule_name, scheduled_for, "
         " officer_user_id, status, camera_count, completed_camera_count) "
         "VALUES (:i,:t,:s,:n,'Night round', now(), :o, 'IN_PROGRESS', 1, 0)",
         {"i": session, "t": w["tenant"], "s": w["site_a"], "n": f"VP-{session.hex[:8]}", "o": w["users"][OPERATOR]}),
        ("INSERT INTO virtual_patrol_session_cameras (id, tenant_id, session_id, camera_id, sequence_no, camera_name, status) "
         "VALUES (:i,:t,:s,:c,1,'Gate 1','PENDING')", {"i": row, "t": w["tenant"], "s": session, "c": camera}),
    ])
    return row


async def _capture(w: dict, row) -> dict:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        out = await snap.capture(db, session_camera_id=str(row))
        await db.commit()
    return out


async def test_the_image_a_patrol_keeps_is_masked_before_it_is_hashed_and_is_not_kept_if_it_cannot_be(monkeypatch, tmp_path):
    w = await _world()
    camera, _ = await _camera(w)
    plain, _ = await _camera(w, "Gate 2")
    await _zone(w, camera)
    monkeypatch.setattr(snap.settings, "EVIDENCE_ROOT", str(tmp_path))

    async def the_camera_answers(source_url, out_path):
        cv2.imwrite(out_path, _white())
        return True, None

    monkeypatch.setattr(snap, "_run_ffmpeg", the_camera_answers)
    kept = await _capture(w, await _patrol_camera(w, camera))
    assert kept["ok"] is True
    stored = tmp_path / kept["path"]
    assert _left_is_out(cv2.imread(str(stored)))
    # The checksum is of the masked image: there was never another to hash.
    assert kept["checksum"] == hashlib.sha256(stored.read_bytes()).hexdigest() and kept["bytes"] == stored.stat().st_size
    as_it_came = await _capture(w, await _patrol_camera(w, plain))
    assert as_it_came["ok"] is True and _untouched(cv2.imread(str(tmp_path / as_it_came["path"])))

    # The zones cannot be read: the image is a failed capture, and the file is gone.
    async def away(session, cam):
        raise RuntimeError("the database is away")

    row = await _patrol_camera(w, camera)
    monkeypatch.setattr(privacy_mask, "read", away)
    refused = await _capture(w, row)
    assert refused == {"ok": False, "status": "SNAPSHOT_FAILED",
                       "error": "The camera's privacy zones could not be read, so no image was kept."}
    (said,) = await _sql("SELECT status, snapshot_error, snapshot_path FROM virtual_patrol_session_cameras WHERE id = :i",
                         {"i": row})
    assert said["status"] == "SNAPSHOT_FAILED" and "privacy zones" in said["snapshot_error"] and said["snapshot_path"] is None
    assert sorted(p.name for p in tmp_path.rglob("*.jpg")) == sorted(
        [Path(kept["path"]).name, Path(as_it_came["path"]).name])


# ─── E. HLS ──────────────────────────────────────────────────────────────────

async def test_a_masked_camera_has_no_hls_view_and_a_session_running_for_it_is_stopped(monkeypatch, tmp_path):
    w = await _world()
    camera, stream = await _camera(w)
    plain, plain_stream = await _camera(w, "Gate 2")
    zone = await _zone(w, camera)
    started, stopped = [], []

    async def start(stream_id, rtsp_url, wait_for_playlist=8.0):
        started.append(stream_id)
        return None

    async def stop(session):
        stopped.append(session.stream_id)

    monkeypatch.setattr(hls_stream, "ensure_session", start)
    monkeypatch.setattr(hls_stream, "_stop_session", stop)
    hls_stream._sessions[str(stream)] = hls_stream._Session(str(stream), str(tmp_path))
    hls, token = f"/api/v1/cameras/{camera}/streams/{stream}/hls", {"token": _token(w, GUARD)}
    async with _client() as c:
        refused = await c.get(f"{hls}/index.m3u8", params=token)
        assert refused.status_code == 409 and refused.json()["detail"]["masked"] is True
        assert "has a privacy zone" in refused.json()["detail"]["message"]
        assert started == [] and stopped == [str(stream)] and str(stream) not in hls_stream._sessions
        # Its segments are refused too: one made before the zone was drawn is not handed out after.
        assert (await c.get(f"{hls}/seg_00001.ts", params=token)).status_code == 409
        # A camera with no zone is as it was: the session is asked for (and here has no playlist yet).
        other = await c.get(f"/api/v1/cameras/{plain}/streams/{plain_stream}/hls/index.m3u8", params=token)
        assert other.status_code == 503 and "starting" in other.json()["detail"] and started == [str(plain_stream)]
        # Who may not see the camera at all is told that, not that it is masked.
        assert (await c.get(f"{hls}/index.m3u8", params={"token": "not-a-token"})).status_code == 401

        # The zone is deleted: the camera has its HLS view again, at once in this process.
        assert (await c.delete(f"{ZONES}/{zone}", headers=w["h"][ADMIN])).status_code == 200
        again = await c.get(f"{hls}/index.m3u8", params=token)
        assert again.status_code == 503 and "starting" in again.json()["detail"] and started[-1] == str(stream)

        # And when the zones cannot be read, HLS is refused: it is not known that the camera has none.
        async def away(tenant, cam):
            raise RuntimeError("the database is away")

        privacy_mask._MASKED.clear()
        monkeypatch.setattr(privacy_mask, "read_for", away)
        unknown = await c.get(f"/api/v1/cameras/{plain}/streams/{plain_stream}/hls/index.m3u8", params=token)
        assert unknown.status_code == 503 and "privacy zones could not be read" in unknown.json()["detail"]


# ─── F. The API ──────────────────────────────────────────────────────────────

async def test_a_zone_is_drawn_and_deleted_by_a_person_on_a_camera_they_may_see_and_each_is_on_the_record(monkeypatch, tmp_path):
    w = await _world()
    camera, stream = await _camera(w)
    far, _ = await _camera(w, "Factory B gate", site="site_b")
    stopped = []

    async def stop(session):
        stopped.append(session.stream_id)

    monkeypatch.setattr(hls_stream, "_stop_session", stop)
    hls_stream._sessions[str(stream)] = hls_stream._Session(str(stream), str(tmp_path))
    adm, sup = w["h"][ADMIN], w["h"][SUPERVISOR]
    async with _client() as c:
        drawn = await c.post(ZONES, headers=adm, json={"camera_id": str(camera), "name": "  Neighbour's window ", "polygon": LEFT})
        assert drawn.status_code == 200, drawn.text
        zone = drawn.json()
        assert (zone["name"], zone["camera_name"], zone["fill_color"], zone["is_active"]) == (
            "Neighbour's window", "Gate 1", "#000000", True)
        assert zone["polygon"] == LEFT and zone["applies_within_seconds"] == privacy_mask.REFRESH_SECONDS
        # An HLS session running for the camera is stopped as the zone is drawn.
        assert stopped == [str(stream)]

        # The list names the camera and who drew it; the masked cameras are the ones with an active zone.
        (listed,) = (await c.get(ZONES, headers=adm)).json()
        assert (listed["id"], listed["camera_name"], listed["created_by_name"]) == (zone["id"], "Gate 1", "Role 2 User")
        assert (await c.get("/api/v1/privacy/masked-cameras", headers=w["h"][GUARD])).json() == {
            "camera_ids": [str(camera)], "refresh_seconds": privacy_mask.REFRESH_SECONDS}
        assert [z["id"] for z in (await c.get(f"{ZONES}/camera/{camera}", headers=w["h"][GUARD])).json()] == [zone["id"]]

        # The rules of a polygon are the API's too, and say what is wrong.
        for body, said in (({"polygon": LEFT[:2]}, "3 to 64 points"), ({"polygon": [*LEFT[:3], {"x": 1.5, "y": 0}]}, "inside the picture"),
                           ({"polygon": [{"x": 0.2, "y": 0.2}] * 3}, "cover some of the picture"),
                           ({"fill_color": "black"}, "#RRGGBB"), ({"name": "   "}, "1 to 100 characters"),
                           ({"camera_id": "gate-1"}, "UUID")):
            got = await c.post(ZONES, headers=adm, json={"camera_id": str(camera), "name": "x", "polygon": LEFT, **body})
            assert got.status_code == 422 and said in got.text, (body, got.text)
        # A camera that is not there, and one at a site the caller is not given, are the same answer.
        assert (await c.post(ZONES, headers=adm, json={"camera_id": str(uuid.uuid4()), "polygon": LEFT})).status_code == 404
        assert (await c.post(ZONES, headers=sup, json={"camera_id": str(far), "polygon": LEFT})).status_code == 404
        mine = await c.post(ZONES, headers=sup, json={"camera_id": str(camera), "name": "Staff door", "polygon": CORNER})
        assert mine.status_code == 200
        elsewhere = (await c.post(ZONES, headers=adm, json={"camera_id": str(far), "name": "Road", "polygon": LEFT})).json()
        # Somebody held to a site reads, and deletes, only its zones.
        assert {z["camera_name"] for z in (await c.get(ZONES, headers=sup)).json()} == {"Gate 1"}
        assert (await c.get("/api/v1/privacy/masked-cameras", headers=sup)).json()["camera_ids"] == [str(camera)]
        assert sorted((await c.get("/api/v1/privacy/masked-cameras", headers=adm)).json()["camera_ids"]) == sorted(
            [str(camera), str(far)])
        assert (await c.delete(f"{ZONES}/{elsewhere['id']}", headers=sup)).status_code == 404
        # Who may not manage privacy draws and deletes nothing, and does not read the list.
        for role in (OPERATOR, GUARD, VIEWER):
            assert (await c.post(ZONES, headers=w["h"][role], json={"camera_id": str(camera), "polygon": LEFT})).status_code == 403
            assert (await c.delete(f"{ZONES}/{zone['id']}", headers=w["h"][role])).status_code == 403
            assert (await c.get(ZONES, headers=w["h"][role])).status_code == 403
        assert (await c.post(ZONES, headers=w["h"][MANAGER], json={"camera_id": str(camera), "polygon": LEFT})).status_code == 200

        # A drone's camera has none: its picture moves.
        drone = await _drone(c, w)
        on_it, _ = await _camera(w, "Drone 1 camera")
        await _sql("UPDATE drones SET camera_id = :c WHERE id = :d", {"c": on_it, "d": drone["id"]})
        flying = await c.post(ZONES, headers=adm, json={"camera_id": str(on_it), "polygon": LEFT})
        assert flying.status_code == 409 and "drone's camera" in flying.json()["detail"]

        # Deleted: gone from the list and from the masked cameras, and that is said once.
        gone = await c.delete(f"{ZONES}/{zone['id']}", headers=adm)
        assert gone.status_code == 200 and gone.json() == {"deleted": True, "id": zone["id"]}
        assert (await c.delete(f"{ZONES}/{zone['id']}", headers=adm)).status_code == 404
        assert (await c.delete(f"{ZONES}/not-an-id", headers=adm)).status_code == 422
        assert zone["id"] not in [z["id"] for z in (await c.get(ZONES, headers=adm)).json()]

    # Each drawing and each deleting is one line, with the camera, the name and who.
    created, deleted = await _audit(w, "privacy.zone.create"), await _audit(w, "privacy.zone.delete")
    assert [(str(a["user_id"]), a["detail"]["name"], a["detail"]["camera_name"]) for a in created] == [
        (str(w["users"][ADMIN]), "Neighbour's window", "Gate 1"), (str(w["users"][SUPERVISOR]), "Staff door", "Gate 1"),
        (str(w["users"][ADMIN]), "Road", "Factory B gate"), (str(w["users"][MANAGER]), "Privacy Zone", "Gate 1")]
    said = created[0]["detail"]
    assert {k: said[k] for k in ("camera_id", "camera_name", "name", "points", "is_active")} == {
        "camera_id": str(camera), "camera_name": "Gate 1", "name": "Neighbour's window", "points": 4, "is_active": True}
    # Where the zone is drawn is not written into the log: the log is read by more people than the zone is.
    assert "polygon" not in said and said["site_id"] == str(w["site_a"])
    assert str(created[0]["resource_id"]) == zone["id"] and created[0]["resource_type"] == "privacy_zone"
    assert [(str(a["user_id"]), str(a["resource_id"]), a["detail"]["camera_id"], a["detail"]["camera_name"], a["detail"]["name"])
            for a in deleted] == [(str(w["users"][ADMIN]), zone["id"], str(camera), "Gate 1", "Neighbour's window")]


async def test_a_camera_has_at_most_twenty_zones_and_an_api_key_draws_none():
    w = await _world()
    camera, _ = await _camera(w)
    for index in range(privacy_mask.MOST_ZONES - 1):
        await _zone(w, camera, CORNER, name=f"Zone {index}")
    async with _client() as c:
        assert (await c.post(ZONES, headers=w["h"][ADMIN], json={"camera_id": str(camera), "polygon": LEFT})).status_code == 200
        full = await c.post(ZONES, headers=w["h"][ADMIN], json={"camera_id": str(camera), "polygon": LEFT})
        assert full.status_code == 409 and "at most 20 privacy zones" in full.json()["detail"]
    (zone,) = await _sql("SELECT id FROM privacy_zones WHERE camera_id = :c ORDER BY created_at DESC LIMIT 1", {"c": camera})

    # What a camera shows and records is changed by somebody, by name.
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            for got in (await c.post(ZONES, json={"camera_id": str(camera), "polygon": LEFT}),
                        await c.delete(f"{ZONES}/{zone['id']}")):
                assert got.status_code == 403 and "not by an API key" in got.json()["detail"]
            # It still reads a camera's zones: that is what a worker outside the platform presents.
            assert len((await c.get(f"{ZONES}/camera/{camera}")).json()) == privacy_mask.MOST_ZONES
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    said = inspect.getsource(api._a_person)
    assert "via_api_key" in said and "support_session_id" in said
    for route in (api.create_privacy_zone, api.delete_privacy_zone):
        assert "_a_person(token)" in inspect.getsource(route) and "intel_audit.record(" in inspect.getsource(route)


# ─── G. Two organisations ────────────────────────────────────────────────────

async def test_another_organisations_camera_has_no_zones_for_the_applications_own_role():
    ours, theirs = await _world(), await _world()
    camera, _ = await _camera(ours)
    await _zone(ours, camera)
    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async def as_the_application(tenant) -> tuple:
            async with factory() as session:
                # Proved as the role the platform runs as: a superuser is shown every row whatever its tenant.
                assert (await session.execute(text(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"))).scalar() is False
                await session.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant)})
                return await privacy_mask.read(session, camera)

        assert len(await as_the_application(ours["tenant"])) == 1
        assert await as_the_application(theirs["tenant"]) == ()
    finally:
        await engine.dispose()
    # Asked for as another organisation, the camera is not found: no zone of theirs is drawn on it.
    async with _client() as c:
        assert (await c.post(ZONES, headers=theirs["h"][ADMIN], json={"camera_id": str(camera), "polygon": LEFT})).status_code == 404
        assert (await c.get(ZONES, headers=theirs["h"][ADMIN])).json() == []
        assert (await c.get("/api/v1/privacy/masked-cameras", headers=theirs["h"][ADMIN])).json()["camera_ids"] == []
