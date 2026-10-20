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


# ─── Reading a video ─────────────────────────────────────────────────────────

@needs_ffmpeg
def test_a_video_is_read_and_sampled_once_a_second(tmp_path):
    from worker.footage import video

    clip = _make_video(tmp_path / "clip.mp4", seconds=5)
    info = video.probe(str(clip))
    assert (round(info.duration), info.width, info.height, info.codec) == (5, 320, 240, "h264")
    assert info.playable and info.thumb_size() == (320, 240)
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
        assert pipeline.process(conn, embedder, tenants[0], videos[0]) == "READY"
        assert pipeline.process(conn, embedder, tenants[1], videos[1]) == "READY"
        assert pipeline.process(conn, embedder, tenants[0], videos[0]) == "SKIPPED", "not claimed twice"
        rows = _admin("SELECT id, status, progress, frames_indexed, index_model, playable, proxy_path, codec, sha256 "
                      "FROM footage_videos WHERE id = ANY(%s) ORDER BY codec", (videos,))
        h264, mpeg4 = rows
        assert (h264[1], h264[2], h264[3], h264[4], h264[5], h264[6]) == ("READY", 100, 4, model.VERSION, "ORIGINAL", None)
        assert (mpeg4[5], mpeg4[7]) == ("PROXY", "mpeg4") and (tmp_path / mpeg4[6]).exists()
        assert len(h264[8]) == 64
        frames = _admin("SELECT video_id, second, thumb_path, vector_dims(embedding), model_version FROM footage_frames "
                        "WHERE video_id = ANY(%s) ORDER BY video_id, second", (videos,))
        assert [(f[1], f[3], f[4]) for f in frames if f[0] == videos[0]] == [(s, 768, model.VERSION) for s in range(4)]
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
