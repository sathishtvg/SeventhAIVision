"""Reading a video: what it is, one frame a second, and a copy a browser can play.

Everything is done by ffprobe and ffmpeg, run as separate processes with fixed
arguments; the file is only ever read. A source is a path on disk or, where
the files are in S3, an address that is good for a few minutes.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterator

import numpy as np

#: Width of the frame kept for each second: big enough to see, small enough to keep thousands.
THUMB_WIDTH = 320
#: Width each second is read at: what the person and vehicle detector is given.
SAMPLE_WIDTH = 640
#: Containers and the one codec every browser plays without help.
PLAYABLE_CONTAINERS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
PLAYABLE_CODECS = {"h264"}


class NotAVideo(ValueError):
    """What the person is told when the file cannot be analysed."""


@dataclass(frozen=True)
class Probe:
    duration: float
    width: int
    height: int
    frame_rate: float | None
    codec: str
    container: str
    pixel_format: str | None

    @property
    def playable(self) -> bool:
        """A browser plays the original as it is."""
        containers = set(self.container.split(","))
        return bool(containers & PLAYABLE_CONTAINERS) and self.codec in PLAYABLE_CODECS and \
            self.pixel_format in (None, "yuv420p", "yuvj420p")

    def size_at(self, most: int) -> tuple[int, int]:
        width = max(2, min(most, self.width) // 2 * 2)
        height = max(2, round(width * self.height / self.width / 2) * 2)
        return width, height

    def thumb_size(self) -> tuple[int, int]:
        return self.size_at(THUMB_WIDTH)

    def sample_size(self) -> tuple[int, int]:
        return self.size_at(SAMPLE_WIDTH)


def _rate(text: str | None) -> float | None:
    try:
        rate = Fraction(text) if text else None
    except (ValueError, ZeroDivisionError):
        return None
    return float(rate) if rate else None


def probe(source: str, timeout: int = 120) -> Probe:
    """What the file is. Refuses a file with no picture in it, or none that can be read."""
    try:
        answer = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", source],
            capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise NotAVideo("The file took too long to open.") from exc
    if answer.returncode != 0:
        raise NotAVideo("This file is not a video the platform can read.")
    info = json.loads(answer.stdout or b"{}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic") != 1), None)
    if video is None:
        raise NotAVideo("This file has no picture in it.")
    duration = float(video.get("duration") or info.get("format", {}).get("duration") or 0)
    if duration <= 0 or not video.get("width") or not video.get("height"):
        raise NotAVideo("This file is not a video the platform can read.")
    return Probe(duration=duration, width=int(video["width"]), height=int(video["height"]),
                 frame_rate=_rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")),
                 codec=str(video.get("codec_name") or "unknown"),
                 container=str(info.get("format", {}).get("format_name") or "unknown"),
                 pixel_format=video.get("pix_fmt"))


def frames_per_second(source: str, info: Probe, every: float = 1.0) -> Iterator[tuple[int, np.ndarray]]:
    """(second, frame) for every `every` seconds, as RGB at most SAMPLE_WIDTH wide. The n-th frame is second n."""
    width, height = info.sample_size()
    size = width * height * 3
    process = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", source, "-an", "-sn", "-dn",
         "-vf", f"fps={1 / every},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        second = 0
        while True:
            chunk = process.stdout.read(size)
            if len(chunk) < size:
                break
            yield second, np.frombuffer(chunk, dtype=np.uint8).reshape(height, width, 3)
            second += 1
    finally:
        process.stdout.close()
        process.kill()
        process.wait()


def proxy(source: str, target: str, timeout: int) -> None:
    """A copy every browser plays: H.264, at most 1280 wide, with the start of the file first."""
    answer = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source, "-map", "0:v:0", "-map", "0:a:0?",
         "-vf", "scale='min(1280,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", target],
        capture_output=True, timeout=timeout, check=False)
    if answer.returncode != 0:
        raise RuntimeError(f"ffmpeg could not make a playable copy: {answer.stderr[-400:]!r}")
