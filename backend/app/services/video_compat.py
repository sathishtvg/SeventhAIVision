"""Browser-playable video encoding.

Everything in this file exists because of one fact: **OpenCV cannot encode
H.264 here.** The `opencv-python` wheels ship an FFmpeg build without an H.264
encoder (it is a licensing question, not an oversight), so every H.264 fourcc
fails to open the writer:

    avc1: opened=False   H264: opened=False
    h264: opened=False   X264: opened=False

`cv2.VideoWriter` does not raise on that — it logs to stderr, hands back a
closed writer, and callers that pass `mp4v` get a perfectly valid MP4 holding
an **MPEG-4 Part 2** stream. VLC plays it. Browsers do not: Chrome, Edge,
Firefox and Safari decode H.264 / VP8 / VP9 / AV1 and nothing else in an MP4
container. That is why recorded footage downloaded fine but a `<video>` element
showed a black frame — the file was never browser-playable to begin with.

The system `ffmpeg` (7.1.5, with libx264) *is* installed in the backend image,
so frames are piped to it as rawvideo instead. Three flags matter and are not
optional:

* ``-pix_fmt yuv420p``  — yuv444p/yuvj420p are valid H.264 but are rejected by
  browser decoders. This is the single most common "encoded H.264 and it still
  won't play" cause.
* ``-movflags +faststart`` — moves the moov atom to the front of the file, so a
  `<video>` can start playing (and seeking) after the first few KB instead of
  downloading the whole recording first.
* ``-profile:v main`` — High profile is fine on desktop but stops older mobile
  hardware decoders dead. Main plays everywhere the product runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile

import numpy as np

logger = logging.getLogger(__name__)

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")

#: Video codecs every browser this product targets can decode in an MP4/WebM
#: container. Anything outside this set needs transcoding before a `<video>`
#: element will render it.
BROWSER_PLAYABLE_CODECS = frozenset({"h264", "vp8", "vp9", "av1"})

_PROBE_TIMEOUT_S = 15
_TRANSCODE_TIMEOUT_S = 900


def _encode_args(fps: float) -> list[str]:
    """Shared libx264 output arguments. Keyframe roughly every 2 seconds so
    seeking lands close to where the operator clicked; at very low frame rates
    (event clips run at 0.5 fps) this collapses to every frame, which is what
    you want for sparse footage anyway."""
    gop = max(1, round(fps * 2))
    return [
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-profile:v", "main",
        "-pix_fmt", "yuv420p",
        "-g", str(gop),
        "-movflags", "+faststart",
    ]


class H264Writer:
    """Drop-in replacement for `cv2.VideoWriter` that produces footage a
    browser can actually play.

    Mirrors the small slice of the cv2 API the callers use — ``isOpened()``,
    ``write(frame)``, ``release()`` — so swapping it in is a two-line change at
    each site. Frames are BGR24 `numpy` arrays, exactly what `cv2.imdecode` and
    `cap.read()` already hand over.
    """

    def __init__(self, path: str, fps: float, size: tuple[int, int]) -> None:
        self.path = path
        self.fps = max(fps, 0.01)
        # libx264 with yuv420p needs even dimensions; chroma is subsampled 2x1
        # in both axes and an odd edge has nowhere to go. Round down rather
        # than pad so nothing is invented at the frame border.
        w, h = size
        self.width = max(2, w - (w % 2))
        self.height = max(2, h - (h % 2))
        self._expected_shape = (self.height, self.width, 3)
        self._frame_bytes = self.width * self.height * 3
        self._proc: subprocess.Popen | None = None
        self._frames_written = 0
        self._failed = False

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        cmd = [
            FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}",
            "-framerate", f"{self.fps}",
            "-i", "-",
            *_encode_args(self.fps),
            path,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            # No ffmpeg on PATH. Recording is evidence capture — losing it
            # entirely is worse than losing browser playback — so this is loud
            # but not fatal, and the caller's isOpened() check decides.
            logger.error(
                "H264Writer could not start ffmpeg (%s); recording %s will not be written. "
                "Install ffmpeg or set FFMPEG_BIN.", exc, path,
            )
            self._failed = True

    def isOpened(self) -> bool:  # noqa: N802 — matches the cv2.VideoWriter API
        return self._proc is not None and not self._failed

    def write(self, frame: np.ndarray) -> None:
        if not self.isOpened() or frame is None:
            return
        if frame.shape != self._expected_shape:
            # A stream that changes resolution mid-recording would otherwise
            # desynchronise the raw pipe and corrupt everything after it — cv2
            # silently dropped these, we resize so the footage stays complete.
            import cv2  # local import: keeps this module importable without cv2

            frame = cv2.resize(frame, (self.width, self.height))
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)
        try:
            self._proc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            self._frames_written += 1
        except (BrokenPipeError, OSError) as exc:
            self._failed = True
            logger.error("ffmpeg pipe broke while writing %s: %s", self.path, exc)

    def release(self) -> int:
        """Close the pipe and let ffmpeg finalise the container. Returns the
        number of frames actually encoded.

        The faststart rewrite happens here, after stdin closes, so a caller
        that reads the file size must do it *after* this returns."""
        if self._proc is None:
            return 0
        # communicate() closes stdin itself — closing it here first makes its
        # internal flush raise ValueError on an already-closed file.
        try:
            _, stderr = self._proc.communicate(timeout=120)
        except (BrokenPipeError, ValueError):
            # Pipe already torn down by a failed write; still reap the process
            # so ffmpeg can finish writing whatever it did receive.
            self._proc.wait(timeout=120)
            stderr = b""
        except subprocess.TimeoutExpired:
            self._proc.kill()
            _, stderr = self._proc.communicate()
            logger.error("ffmpeg timed out finalising %s", self.path)
            self._failed = True
        if self._proc.returncode != 0:
            self._failed = True
            logger.error(
                "ffmpeg exited %s writing %s: %s",
                self._proc.returncode, self.path,
                (stderr or b"").decode("utf-8", "replace").strip()[:500],
            )
        self._proc = None
        return self._frames_written


def probe_video_codec(path: str) -> str | None:
    """Return the video stream's codec name (e.g. ``h264``, ``mpeg4``), or None
    if the file is unreadable or has no video stream."""
    try:
        out = subprocess.run(
            [
                FFPROBE_BIN, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=nw=1:nk=1",
                path,
            ],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffprobe failed on %s: %s", path, exc)
        return None
    if out.returncode != 0:
        return None
    codec = out.stdout.strip().splitlines()
    return codec[0].strip() if codec else None


def is_browser_playable(path: str) -> bool:
    """True when a `<video>` element can decode this file as-is."""
    codec = probe_video_codec(path)
    return codec is not None and codec in BROWSER_PLAYABLE_CODECS


def transcode_to_h264(src: str, dst: str) -> bool:
    """Re-encode `src` into a browser-playable H.264 MP4 at `dst`.

    Writes to a temporary file in the destination directory and only moves it
    into place on success, so an interrupted transcode can never leave a
    truncated file that later looks like a valid cached result.
    """
    if not os.path.exists(src):
        return False
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)

    fps = _probe_fps(src)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(dst) or ".")
    os.close(tmp_fd)
    try:
        result = subprocess.run(
            [
                FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
                "-i", src,
                *_encode_args(fps),
                tmp_path,
            ],
            capture_output=True, timeout=_TRANSCODE_TIMEOUT_S,
        )
        if result.returncode != 0 or os.path.getsize(tmp_path) == 0:
            logger.error(
                "Transcode of %s failed (%s): %s", src, result.returncode,
                result.stderr.decode("utf-8", "replace").strip()[:500],
            )
            return False
        shutil.move(tmp_path, dst)
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("Transcode of %s failed: %s", src, exc)
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


#: Suffix for the healed copy of a legacy recording. Kept beside the original
#: rather than replacing it: the original is the evidentiary artefact, with a
#: checksum recorded against it in some flows, so it is never mutated.
HEALED_SUFFIX = ".browser.mp4"

_heal_locks: dict[str, asyncio.Lock] = {}


def healed_path_for(path: str) -> str:
    base, _ = os.path.splitext(path)
    return base + HEALED_SUFFIX


async def ensure_browser_playable(path: str) -> str:
    """Return a path whose contents a `<video>` element can decode.

    Footage recorded before the H.264 fix is MPEG-4 Part 2 and will not play in
    any browser. Rather than requiring a manual migration — which would leave
    every existing deployment's archive silently unplayable until someone
    remembered to run it — the first play of such a file transcodes it once
    into a cached sibling and every later play serves the cache.

    Returns `path` unchanged when it is already playable, or when transcoding
    fails: serving the original is better than a 500, and the browser's own
    error surfaces in the player.
    """
    healed = healed_path_for(path)
    # Fast path: already healed on a previous play. Compare mtimes so a
    # re-recorded file at the same path invalidates a stale cache.
    try:
        if os.path.getsize(healed) > 0 and os.path.getmtime(healed) >= os.path.getmtime(path):
            return healed
    except OSError:
        pass

    lock = _heal_locks.setdefault(path, asyncio.Lock())
    async with lock:
        # Re-check under the lock: a concurrent play may have just healed it,
        # and transcoding the same file twice at once would race on the move.
        try:
            if os.path.getsize(healed) > 0 and os.path.getmtime(healed) >= os.path.getmtime(path):
                return healed
        except OSError:
            pass

        if await asyncio.to_thread(is_browser_playable, path):
            return path

        logger.info("Transcoding legacy recording to H.264 for playback: %s", path)
        ok = await asyncio.to_thread(transcode_to_h264, path, healed)
        if not ok:
            logger.warning("Could not transcode %s; serving the original", path)
            return path
        return healed


def _probe_fps(path: str) -> float:
    """Average frame rate of a file, defaulting to 10 fps when unknown — only
    used to pick a keyframe interval, so a wrong guess costs seek precision,
    never correctness."""
    try:
        out = subprocess.run(
            [
                FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate",
                "-of", "default=nw=1:nk=1", path,
            ],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
        )
        num, _, den = out.stdout.strip().partition("/")
        value = float(num) / float(den) if den and float(den) else float(num)
        return value if value > 0 else 10.0
    except (OSError, ValueError, ZeroDivisionError, subprocess.TimeoutExpired):
        return 10.0
