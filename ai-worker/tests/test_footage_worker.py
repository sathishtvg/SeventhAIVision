"""The footage worker: the model's inputs, its vectors, reading a video, and one video all the way through.

Runs in the footage worker's own image, where the model and ffmpeg are
(docker/footage-worker.Dockerfile); elsewhere the tests that need them skip.
The pipeline test also needs the test database:

    TEST_DATABASE_URL_SYNC        the application's role (svc_app), so the tenant policy applies
    ADMIN_TEST_DATABASE_URL_SYNC  the superuser, for making and removing what the test needs
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import numpy as np
import pytest

MODEL_DIR = Path(os.environ.get("FOOTAGE_MODEL_DIR", "/opt/models/siglip2"))
HAS_MODEL = (MODEL_DIR / "onnx" / "vision_model_quantized.onnx").exists()
DETECTOR = Path(os.environ.get("FOOTAGE_DETECTOR", "/opt/models/yolov8s/yolov8s.onnx"))
HAS_DETECTOR = DETECTOR.exists()
needs_detector = pytest.mark.skipif(not HAS_DETECTOR, reason="the detector is in the footage worker's image only")
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_model = pytest.mark.skipif(not HAS_MODEL, reason="the model is in the footage worker's image only")
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is in the footage worker's image")


@pytest.fixture(scope="module")
def embedder():
    from worker.footage.model import Embedder

    return Embedder(MODEL_DIR, threads=2)


def _make_video(path: Path, seconds: int = 5, codec: str = "libx264") -> Path:
    extra = ["-pix_fmt", "yuv420p"] if codec == "libx264" else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
                    "-c:v", codec, *extra, str(path)], check=True)
    return path


# ─── The model's inputs ──────────────────────────────────────────────────────

@needs_model
def test_a_sentence_is_lower_case_ends_with_end_of_text_and_is_padded_to_64(embedder):
    ids = embedder.token_ids("A Man IN a Red Jacket")
    assert ids.shape == (1, 64) and ids.dtype == np.int64
    assert (embedder.token_ids("a man in a red jacket") == ids).all(), "lower case"
    row = ids[0].tolist()
    eos = row.index(embedder._eos)
    assert 0 < eos < 63 and set(row[eos + 1:]) == {embedder._pad}
    long = embedder.token_ids("word " * 200)[0].tolist()
    assert len(long) == 64 and long[63] == embedder._eos, "a long sentence is cut, and still ends"


@needs_model
def test_a_frame_is_224_square_and_between_minus_one_and_one(embedder):
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:, :, 0] = 255
    pixels = embedder.pixels([frame, frame])
    assert pixels.shape == (2, 3, 224, 224) and pixels.dtype == np.float32
    assert pixels[0, 0].min() == pytest.approx(1.0) and pixels[0, 1].max() == pytest.approx(-1.0)


# ─── The model's vectors ─────────────────────────────────────────────────────

@needs_model
def test_vectors_are_768_long_unit_and_a_colour_is_nearest_its_name(embedder):
    red, blue = np.zeros((224, 224, 3), np.uint8), np.zeros((224, 224, 3), np.uint8)
    red[..., 0], blue[..., 2] = 220, 220
    images = embedder.embed_images([red, blue])
    words = np.stack([embedder.embed_text("a red picture"), embedder.embed_text("a blue picture")])
    assert images.shape == (2, 768) and words.shape == (2, 768)
    assert np.allclose(np.linalg.norm(images, axis=1), 1, atol=1e-4)
    assert np.allclose(np.linalg.norm(words, axis=1), 1, atol=1e-4)
    likeness = images @ words.T
    assert likeness[0, 0] > likeness[0, 1] and likeness[1, 1] > likeness[1, 0], likeness


@needs_model
def test_the_model_is_the_pinned_one(embedder):
    from worker.footage import fetch_model, model

    assert model.VERSION == "siglip2-base-patch16-224-int8@ba1f3b0"
    for name, (size, _sha) in fetch_model.FILES.items():
        assert (MODEL_DIR / name).stat().st_size == size, name


# ─── People and vehicles ─────────────────────────────────────────────────────

def test_seconds_with_someone_in_them_are_joined_into_events_across_short_gaps():
    from worker.footage import events

    counts = {3: (1, 0.9), 4: (2, 0.8), 7: (1, 0.95), 8: (0, 0.0), 20: (1, 0.6), 30: (3, 0.7)}
    found = events.stretches("PEOPLE", counts)
    assert [(e.start, e.end, e.most, e.best, e.seconds) for e in found] == [
        (3, 7, 2, 4, 3), (20, 20, 1, 20, 1), (30, 30, 3, 30, 1)]
    assert found[0].reasons == {"seen_in_seconds": 3, "most_at_once": 2}
    assert events.stretches("VEHICLES", {1: (0, 0.0)}) == []


def test_overlapping_boxes_of_one_thing_are_kept_once():
    from worker.footage import detect

    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    assert detect.keep_best(boxes, np.array([0.6, 0.9, 0.5])) == [1, 2]


def test_the_answer_of_the_detector_is_read_into_people_and_vehicles_in_the_frame_s_own_pixels():
    from worker.footage import detect

    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    _, scale, px, py = detect.letterbox(frame)
    assert (scale, px, py) == (1.0, 0.0, 140.0)
    out = np.zeros((1, 84, 8400), dtype=np.float32)
    # A person at (100, 200) 40 x 80 in the frame, twice (overlapping); a car; a dog (not counted); a faint person.
    for i, (cx, cy, w, h, cls, score) in enumerate([(100, 200, 40, 80, 0, 0.9), (102, 201, 40, 80, 0, 0.7),
                                                    (400, 250, 120, 60, 2, 0.8), (300, 300, 30, 30, 16, 0.95),
                                                    (500, 100, 20, 40, 0, 0.2)]):
        out[0, :4, i] = (cx, cy + py, w, h)
        out[0, 4 + cls, i] = score
    found = detect.decode(out, scale, px, py)
    assert sorted((b.kind, round(b.score, 2)) for b in found) == [("PERSON", 0.9), ("VEHICLE", 0.8)]
    person = next(b for b in found if b.kind == "PERSON")
    assert (person.x1, person.y1, person.x2, person.y2) == (80.0, 160.0, 120.0, 240.0)


@needs_detector
def test_the_detector_counts_the_people_and_the_bus_in_a_street_photo():
    from PIL import Image

    from worker.footage import detect

    photo = Path(__file__).parent / "fixtures" / "bus.jpg"
    if not photo.exists():
        pytest.skip("the street photo is not here")
    people, vehicles, p_score, v_score = detect.Detector(DETECTOR).count(np.asarray(Image.open(photo).convert("RGB")))
    assert people >= 3 and vehicles >= 1 and p_score > 0.5 and v_score > 0.5, (people, vehicles)


# ─── Reading a video ─────────────────────────────────────────────────────────

@needs_ffmpeg
def test_a_video_is_read_and_sampled_once_a_second(tmp_path):
    from worker.footage import video

    clip = _make_video(tmp_path / "clip.mp4", seconds=5)
    info = video.probe(str(clip))
    assert (round(info.duration), info.width, info.height, info.codec) == (5, 320, 240, "h264")
    assert info.playable and info.thumb_size() == (320, 240) and info.sample_size() == (320, 240)
    frames = list(video.frames_per_second(str(clip), info))
    assert [s for s, _ in frames] == [0, 1, 2, 3, 4]
    assert frames[0][1].shape == (240, 320, 3) and frames[0][1].dtype == np.uint8


@needs_ffmpeg
def test_what_is_not_a_video_is_refused_with_a_reason(tmp_path):
    from worker.footage import video

    text = tmp_path / "notes.mp4"
    text.write_text("not a video at all")
    with pytest.raises(video.NotAVideo, match="not a video"):
        video.probe(str(text))


@needs_ffmpeg
def test_a_video_no_browser_plays_is_given_a_copy_that_one_does(tmp_path):
    from worker.footage import video

    clip = _make_video(tmp_path / "clip.avi", seconds=2, codec="mpeg4")
    info = video.probe(str(clip))
    assert info.codec == "mpeg4" and not info.playable
    video.proxy(str(clip), str(tmp_path / "playable.mp4"), timeout=120)
    copy = video.probe(str(tmp_path / "playable.mp4"))
    assert copy.codec == "h264" and copy.playable


# ─── One video, all the way through, as the application's role ───────────────

APP_URL = os.environ.get("TEST_DATABASE_URL_SYNC", "")
ADMIN_URL = os.environ.get("ADMIN_TEST_DATABASE_URL_SYNC", "")
needs_db = pytest.mark.skipif(not (APP_URL and ADMIN_URL), reason="needs the test database")


def _admin(sql: str, params: tuple = ()):
    import psycopg

    with psycopg.connect(ADMIN_URL.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


@needs_model
@needs_ffmpeg
@needs_db
def test_a_video_goes_from_queued_to_ready_with_a_frame_and_a_vector_a_second(tmp_path, monkeypatch, embedder):
    import psycopg

    from worker.footage import db, model, pipeline, store

    monkeypatch.setattr(store, "BACKEND", "local")
    monkeypatch.setattr(store, "ROOT", tmp_path)
    tenants = [uuid.uuid4(), uuid.uuid4()]
    videos = [uuid.uuid4(), uuid.uuid4()]
    try:
        for t in tenants:
            _admin("INSERT INTO tenants (id, name, slug) VALUES (%s, 'Footage worker test', %s)", (t, f"fw-{t.hex[:10]}"))
        for t, v, ext, codec in ((tenants[0], videos[0], ".mp4", "libx264"), (tenants[1], videos[1], ".avi", "mpeg4")):
            rel = f"footage/{t}/{v}/original{ext}"
            (tmp_path / rel).parent.mkdir(parents=True)
            _make_video(tmp_path / rel, seconds=4, codec=codec)
            _admin("""
                INSERT INTO footage_videos (id, tenant_id, title, original_filename, size_bytes, received_bytes,
                                            part_size, storage_path, status, expires_at)
                VALUES (%s, %s, 'Test', %s, 1, 1, 8, %s, 'QUEUED', now() + interval '30 days')
            """, (v, t, f"original{ext}", rel))
        conn = psycopg.connect(APP_URL.replace("postgresql+psycopg://", "postgresql://"), autocommit=True)
        assert not conn.execute("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]
        waiting = {row[1] for row in db.waiting(conn)}
        assert set(videos) <= waiting, "the owner's function finds every organisation's waiting videos"
        detector = screen = poser = None
        if HAS_DETECTOR:
            from worker.footage import detect

            detector = detect.Detector(DETECTOR)
        if os.path.exists(os.environ.get("FOOTAGE_POSE", "/opt/models/yolov8s-pose/yolov8s-pose.onnx")):
            from worker.footage import fight, pose

            screen = fight.Screen(embedder.embed_text, model.LOGIT_SCALE, model.LOGIT_BIAS)
            poser = pose.PoseDetector()
        assert pipeline.process(conn, embedder, tenants[0], videos[0], detector, screen, poser) == "READY"
        assert pipeline.process(conn, embedder, tenants[1], videos[1], detector, screen, poser) == "READY"
        assert pipeline.process(conn, embedder, tenants[0], videos[0], detector, screen, poser) == "SKIPPED",             "not claimed twice"
        rows = _admin("SELECT id, status, progress, frames_indexed, index_model, playable, proxy_path, codec, sha256 "
                      "FROM footage_videos WHERE id = ANY(%s) ORDER BY codec", (videos,))
        h264, mpeg4 = rows
        assert (h264[1], h264[2], h264[3], h264[4], h264[5], h264[6]) == ("READY", 100, 4, model.VERSION, "ORIGINAL", None)
        assert (mpeg4[5], mpeg4[7]) == ("PROXY", "mpeg4") and (tmp_path / mpeg4[6]).exists()
        assert len(h264[8]) == 64
        frames = _admin("SELECT video_id, second, thumb_path, vector_dims(embedding), model_version, people, vehicles "
                        "FROM footage_frames WHERE video_id = ANY(%s) ORDER BY video_id, second", (videos,))
        assert [(f[1], f[3], f[4]) for f in frames if f[0] == videos[0]] == [(s, 768, model.VERSION) for s in range(4)]
        if detector is not None:
            assert {(f[5], f[6]) for f in frames} == {(0, 0)}, "a test pattern has nobody in it"
            assert _admin("SELECT count(*) FROM footage_findings WHERE video_id = ANY(%s)", (videos,))[0][0] == 0
        assert all((tmp_path / f[2]).exists() for f in frames)
        # Each frame is its own organisation's.
        tenants_of = {row[0]: row[1] for row in _admin(
            "SELECT video_id, tenant_id FROM footage_frames WHERE video_id = ANY(%s)", (videos,))}
        assert tenants_of == {videos[0]: tenants[0], videos[1]: tenants[1]}
        # Nothing raised an alert or an incident.
        assert _admin("SELECT count(*) FROM alerts WHERE tenant_id = ANY(%s)", (tenants,))[0][0] == 0
        assert _admin("SELECT count(*) FROM incidents WHERE tenant_id = ANY(%s)", (tenants,))[0][0] == 0
        conn.close()
    finally:
        _admin("DELETE FROM footage_videos WHERE id = ANY(%s)", (videos,))
        _admin("DELETE FROM tenants WHERE id = ANY(%s)", (tenants,))


@needs_model
@needs_ffmpeg
@needs_db
def test_a_file_that_is_not_a_video_fails_with_its_reason(tmp_path, monkeypatch, embedder):
    import psycopg

    from worker.footage import pipeline, store

    monkeypatch.setattr(store, "BACKEND", "local")
    monkeypatch.setattr(store, "ROOT", tmp_path)
    t, v = uuid.uuid4(), uuid.uuid4()
    rel = f"footage/{t}/{v}/original.mp4"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(json.dumps({"not": "a video"}))
    try:
        _admin("INSERT INTO tenants (id, name, slug) VALUES (%s, 'Footage worker test', %s)", (t, f"fw-{t.hex[:10]}"))
        _admin("""
            INSERT INTO footage_videos (id, tenant_id, title, original_filename, size_bytes, received_bytes, part_size,
                                        storage_path, status, expires_at)
            VALUES (%s, %s, 'Junk', 'original.mp4', 1, 1, 8, %s, 'QUEUED', now() + interval '30 days')
        """, (v, t, rel))
        with psycopg.connect(APP_URL.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as conn:
            assert pipeline.process(conn, embedder, t, v) == "FAILED"
        status, error = _admin("SELECT status, error FROM footage_videos WHERE id = %s", (v,))[0]
        assert status == "FAILED" and "not a video" in error
    finally:
        _admin("DELETE FROM footage_videos WHERE id = %s", (v,))
        _admin("DELETE FROM tenants WHERE id = %s", (t,))


# ─── Possible altercations ───────────────────────────────────────────────────

POSE = Path(os.environ.get("FOOTAGE_POSE", "/opt/models/yolov8s-pose/yolov8s-pose.onnx"))
needs_pose = pytest.mark.skipif(not POSE.exists(), reason="the pose model is in the footage worker's image only")


def _body(x: float, y: float = 100.0, wrist: tuple[float, float] | None = None, height: float = 100.0):
    from worker.footage.pose import Body

    points = np.zeros((17, 3), dtype=np.float32)
    if wrist is not None:
        points[9] = (wrist[0], wrist[1], 0.9)
    return Body(0.9, np.array([x - 25, y - height / 2, x + 25, y + height / 2], dtype=np.float32), points)


def test_a_frame_is_screened_by_how_much_more_it_looks_like_a_fight_than_its_look_alikes():
    from worker.footage import fight

    axes = {t: np.eye(32)[i] for i, t in enumerate(fight.FIGHTING + fight.LOOK_ALIKES)}
    screen = fight.Screen(lambda t: axes[t], logit_scale=100.0, logit_bias=-10.0)
    punching = axes["a person punching another person"]
    hugging = axes["people hugging"]
    shares, described = screen.shares(np.stack([punching, hugging, 0.5 * punching + 0.5 * hugging]))
    assert shares[0] > 0.99 and shares[1] < 0.01 and 0.4 < shares[2] < 0.6
    assert fight.FIGHTING[described[0]] == "a person punching another person"


def test_two_of_three_seconds_make_a_window_and_a_second_either_side_is_looked_at():
    from worker.footage import fight

    per_second = {s: (0.1, 3, 0) for s in range(40)}
    per_second.update({10: (0.7, 2, 1), 11: (0.9, 3, 2), 13: (0.6, 2, 0), 20: (0.95, 2, 0), 30: (0.8, 1, 0),
                       31: (0.8, 1, 0)})
    found = fight.windows(per_second)
    assert [(w.start, w.end, w.best, w.seconds) for w in found] == [(9, 12, 11, [10, 11])]
    assert found[0].described == fight.FIGHTING[2] and found[0].share == pytest.approx(0.9)
    assert fight.windows({s: (0.1, 3, 0) for s in range(10)}) == [], "a lone second, or one person, is not enough"


def test_bodies_within_reach_and_a_fast_arm_are_a_struggle_and_walking_is_not():
    from worker.footage import pose

    a0, b0 = _body(100, wrist=(110, 90)), _body(150, wrist=(140, 90))
    a1, b1 = _body(102, wrist=(160, 80)), _body(151, wrist=(141, 90))
    assert pose.within_reach([a1, b1]) == [(0, 1)]
    assert pose.follow([a0, b0], [b1, a1]) == {0: 1, 1: 0}
    assert pose.arm_speed(a0, a1, 0.2) == pytest.approx(np.hypot(50, 10) / 100 / 0.2)
    assert pose.struggle([a0, b0], [a1, b1], 0.2), "a wrist moving 2.5 body heights a second, within reach"
    slow = [_body(102, wrist=(115, 90)), _body(151, wrist=(141, 90))]
    assert not pose.struggle([a0, b0], slow, 0.2), "an arm swinging while walking"
    far = [_body(100, wrist=(110, 90)), _body(600, wrist=(600, 90))]
    far_fast = [_body(102, wrist=(160, 80)), _body(601, wrist=(600, 90))]
    assert not pose.struggle(far, far_fast, 0.2), "a fast arm with nobody within reach"


def test_a_window_is_a_possible_altercation_only_with_enough_moments_of_struggle():
    from worker.footage import fight

    still = [_body(100, wrist=(110, 90)), _body(150, wrist=(140, 90))]
    swung = [_body(100, wrist=(165, 80)), _body(150, wrist=(140, 90))]
    struggling = [still, swung, still, swung, still, swung, still]
    possible, why = fight.confirmed(struggling)
    assert possible and why["moments_with_fast_arms_within_reach"] == 6 and why["frames_looked_at"] == 7
    quiet = [still] * 7
    assert fight.confirmed(quiet) == (False, {"frames_looked_at": 7, "frames_with_people_within_reach": 6,
                                              "moments_with_fast_arms_within_reach": 0})


def test_the_answer_of_the_pose_model_is_read_into_bodies_in_the_frame_s_own_pixels():
    from worker.footage import pose

    out = np.zeros((1, 56, 8400), dtype=np.float32)
    out[0, :5, 0] = (100, 340, 40, 80, 0.9)      # a person at (100, 200) once the 140-pixel band is taken off
    out[0, 5 + 3 * 9: 5 + 3 * 9 + 3, 0] = (110, 330, 0.8)
    out[0, :5, 1] = (300, 300, 40, 80, 0.2)      # too faint
    bodies = pose.decode(out, 1.0, 0.0, 140.0)
    assert len(bodies) == 1 and tuple(bodies[0].box) == (80.0, 160.0, 120.0, 240.0)
    assert tuple(bodies[0].points[9]) == pytest.approx((110.0, 190.0, 0.8))


@needs_pose
def test_the_pose_model_finds_the_bodies_in_a_street_photo():
    from PIL import Image

    from worker.footage import pose

    photo = Path(__file__).parent / "fixtures" / "bus.jpg"
    if not photo.exists():
        pytest.skip("the street photo is not here")
    bodies = pose.PoseDetector(POSE).bodies(np.asarray(Image.open(photo).convert("RGB")))
    assert len(bodies) >= 3 and all((b.points[:, 2] >= pose.POINT).sum() >= 8 for b in bodies[:3])


@needs_ffmpeg
def test_a_flagged_stretch_is_read_again_five_frames_a_second_and_kept_when_the_bodies_struggle(tmp_path):
    from worker.footage import fight, pipeline, video

    clip = _make_video(tmp_path / "clip.mp4", seconds=8)
    info = video.probe(str(clip))
    still = [_body(100, wrist=(110, 90)), _body(150, wrist=(140, 90))]
    swung = [_body(100, wrist=(165, 80)), _body(150, wrist=(140, 90))]

    class Poser:
        def __init__(self, struggling: bool):
            self.calls, self.struggling = 0, struggling

        def bodies(self, frame):
            assert frame.shape == (240, 320, 3)
            self.calls += 1
            return (swung if self.calls % 2 else still) if self.struggling else still

    flagged = {s: (0.1, 3, 0) for s in range(8)}
    flagged.update({3: (0.8, 2, 1), 4: (0.9, 2, 1)})
    struggling = Poser(True)
    found = pipeline.altercations(str(clip), info, flagged, struggling)
    assert struggling.calls == 20, "seconds 2 to 5 inclusive, five frames each"
    assert [(f.kind, f.start, f.end, f.best, f.most) for f in found] == [("ALTERCATION", 2, 5, 4, None)]
    assert found[0].reasons["looked_like"] == fight.FIGHTING[1] and found[0].reasons["moments_with_fast_arms_within_reach"] >= 3
    assert pipeline.altercations(str(clip), info, flagged, Poser(False)) == [], "screened, but the bodies do not struggle"
