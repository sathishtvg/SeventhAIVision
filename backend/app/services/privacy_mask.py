"""Privacy zones, applied: a camera's picture with what the organisation chose not to look at painted out.

A privacy zone is a polygon drawn on a camera's picture (`privacy_zones`,
migration 0010). Until 2026-10-09 zones were stored and listed and nothing
applied them: no worker, no player and no recorder read one. This is what
applies them.

A MASK IS PAINTED INTO THE FRAME. It is not drawn over the video by a player.
What is under a zone is replaced before the frame is given to anything else, so
there is no copy of it for the AI to see, a viewer to be sent or a recording to
keep. That cannot be undone, which is the point of it.

IT IS CALLED AT THE FOUR PLACES A CAMERA'S PICTURE LEAVES THE CAMERA THROUGH
THE PLATFORM, before anything else is done with the frame:

    ingestion_main.run_camera_loop   what the AI workers are given - and so the
                                     snapshots they save and the event clips
    routers/streams._mjpeg_frames    the live view of the web and the phone
    routers/streams._record_to_file  recordings, manual and continuous
    vpatrol_snapshot.capture         the image a virtual patrol keeps

HLS copies the camera's stream without decoding it, so nothing can be painted
into it. A camera with a zone has no HLS view (routers/streams.py refuses it).

NOTHING UNMASKED LEAVES BECAUSE THE ZONES COULD NOT BE READ. A loop holds a
`Keeper`. Until its first read has succeeded it has no zones to paint with and
says so (`NotLoaded`): the loop sends, publishes and records nothing. A later
read that fails keeps the zones of the last one that succeeded.

A ZONE THAT CANNOT BE READ AS A POLYGON MASKS THE WHOLE PICTURE. The API
refuses to store one (`points_of`), so this is a row put in by hand. Where it
was meant to be is not known, and guessing would show what somebody asked not
to be shown.

TEN SECONDS. A loop reads its camera's zones again every `REFRESH_SECONDS`, so
a zone drawn or deleted takes effect within about that long, in every process,
with nothing restarted and nothing to tell.

A ZONE IS FIXED TO THE PICTURE, NOT TO THE SCENE: its corners are shares of the
picture's width and height. A camera that is turned moves out from under it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import cv2
import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: The fewest and the most corners a zone has.
LEAST_POINTS = 3
MOST_POINTS = 64
#: The most zones on one camera.
MOST_ZONES = 20
#: The least of the picture a zone covers: one that covers nothing would be believed and mask nothing.
LEAST_AREA = 0.0001
#: How often a loop reads its camera's zones again.
REFRESH_SECONDS = 10.0
#: How soon a loop that has never read them tries again.
RETRY_SECONDS = 2.0
#: Blue, green, red: OpenCV's order.
BLACK = (0, 0, 0)

_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ZONES = text("SELECT polygon, fill_color FROM privacy_zones "
              "WHERE camera_id = CAST(:c AS uuid) AND is_active = TRUE ORDER BY created_at, id")


class NotLoaded(RuntimeError):
    """A camera's zones have not been read yet, so there is nothing to paint a frame with."""


@dataclass(frozen=True)
class Zone:
    #: The corners, each a share of the picture's width and height. None when
    #: what is stored cannot be read as a polygon: the whole picture is masked.
    points: tuple[tuple[float, float], ...] | None
    #: Blue, green, red.
    colour: tuple[int, int, int] = BLACK


# ─── What a zone is ──────────────────────────────────────────────────────────

def points_of(polygon: Any) -> tuple[tuple[float, float], ...]:
    """A polygon as it is stored - a list of {"x": share, "y": share} - as
    corners. Raises ValueError, saying what is wrong, for anything else."""
    if not isinstance(polygon, (list, tuple)):
        raise ValueError("A zone is a list of points.")
    if not LEAST_POINTS <= len(polygon) <= MOST_POINTS:
        raise ValueError(f"A zone has {LEAST_POINTS} to {MOST_POINTS} points.")
    out = []
    for point in polygon:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            raise ValueError("Each point of a zone is an x and a y.")
        pair = []
        for value in (point["x"], point["y"]):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("The x and the y of a point are numbers.")
            if not 0 <= value <= 1:
                raise ValueError("Each point of a zone is inside the picture: x and y are between 0 and 1.")
            pair.append(float(value))
        out.append((pair[0], pair[1]))
    # The shoelace formula: a polygon with no area is a line, and paints nothing.
    area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(out, out[1:] + out[:1]))) / 2
    if area < LEAST_AREA:
        raise ValueError("A zone has to cover some of the picture.")
    return tuple(out)


def colour_of(fill: Any) -> tuple[int, int, int]:
    """`#RRGGBB` as blue, green, red. Anything else is black."""
    if not isinstance(fill, str) or not _COLOUR.match(fill):
        return BLACK
    return int(fill[5:7], 16), int(fill[3:5], 16), int(fill[1:3], 16)


def is_colour(fill: Any) -> bool:
    return isinstance(fill, str) and bool(_COLOUR.match(fill))


def zone_of(polygon: Any, fill: Any) -> Zone:
    """A stored zone. One that cannot be read as a polygon masks the whole picture."""
    if isinstance(polygon, (str, bytes)):
        try:
            polygon = json.loads(polygon)
        except ValueError:
            polygon = None
    try:
        return Zone(points_of(polygon), colour_of(fill))
    except ValueError:
        return Zone(None, BLACK)


# ─── Painting ────────────────────────────────────────────────────────────────

def paint(frame, zones):
    """Paint the zones into the frame, in place, and give it back. A frame with
    no zones is given back as it came."""
    if frame is None or not zones:
        return frame
    height, width = frame.shape[:2]
    for zone in zones:
        if zone.points is None:
            frame[:] = 0
            return frame
        corners = np.array([[round(x * (width - 1)), round(y * (height - 1))] for x, y in zone.points], dtype=np.int32)
        cv2.fillPoly(frame, [corners], zone.colour)
    return frame


def paint_file(path: str, zones) -> bool:
    """Paint the zones into an image file where it lies. False when the file
    cannot be read or written as an image: the caller does not keep it."""
    if not zones:
        return True
    frame = cv2.imread(path, cv2.IMREAD_COLOR)
    if frame is None:
        return False
    return bool(cv2.imwrite(path, paint(frame, zones), [cv2.IMWRITE_JPEG_QUALITY, 95]))


# ─── Reading a camera's zones ────────────────────────────────────────────────

async def read(session: AsyncSession, camera_id) -> tuple[Zone, ...]:
    """The camera's active zones, oldest first. The session is already the
    organisation's: under row level security another organisation's camera has
    no zones here."""
    rows = (await session.execute(_ZONES, {"c": str(camera_id)})).all()
    return tuple(zone_of(row[0], row[1]) for row in rows)


async def read_for(tenant_id, camera_id) -> tuple[Zone, ...]:
    """The same, for a loop that has no session of its own."""
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})
        return await read(session, camera_id)


Reader = Callable[[Any, Any], Awaitable[tuple[Zone, ...]]]


class Keeper:
    """One camera's zones, as a loop last read them."""

    def __init__(self, tenant_id, camera_id, *, reader: Reader | None = None, every: float = REFRESH_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.tenant_id, self.camera_id = tenant_id, camera_id
        self._reader, self._every, self._clock = reader, every, clock
        self._zones: tuple[Zone, ...] | None = None
        self._tried_at: float | None = None

    @property
    def loaded(self) -> bool:
        return self._zones is not None

    @property
    def zones(self) -> tuple[Zone, ...]:
        if self._zones is None:
            raise NotLoaded(f"The privacy zones of camera {self.camera_id} have not been read.")
        return self._zones

    @property
    def due(self) -> bool:
        if self._tried_at is None:
            return True
        wait = self._every if self.loaded else min(self._every, RETRY_SECONDS)
        return self._clock() - self._tried_at >= wait

    async def refresh(self) -> bool:
        """Read the zones now. A read that fails changes nothing: the zones of
        the last that succeeded are kept, and a loop that never had any still
        has none. Returns whether there are zones to paint with."""
        self._tried_at = self._clock()
        try:
            self._zones = await (self._reader or read_for)(self.tenant_id, self.camera_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("privacy zones of camera %s could not be read (%s): %s", self.camera_id, type(exc).__name__,
                           "the last ones read are kept" if self.loaded else "nothing of it is shown until they are")
        return self.loaded

    async def keep_up(self) -> bool:
        """Read again when it is time to. Returns whether there are zones to paint with."""
        if self.due:
            await self.refresh()
        return self.loaded

    def paint(self, frame):
        """The frame with this camera's zones painted in. Refuses (`NotLoaded`)
        before the first read: an unmasked frame is never the fallback."""
        return paint(frame, self.zones)

    async def keep(self, stop: asyncio.Event | None = None) -> None:
        """Keep up until stopped - for a loop that runs in a thread and cannot
        ask for itself (the recorder)."""
        while stop is None or not stop.is_set():
            await self.keep_up()
            await asyncio.sleep(min(self._every, RETRY_SECONDS))


# ─── Whether a camera has a zone, for a route that is asked often ────────────

_MASKED: dict[str, tuple[float, bool]] = {}


async def is_masked(tenant_id, camera_id, *, reader: Reader | None = None,
                    clock: Callable[[], float] = time.monotonic) -> bool:
    """Whether the camera has an active zone, as read within the last ten
    seconds. Raises when it cannot be read: the caller refuses, it does not guess."""
    key, now = str(camera_id), clock()
    known = _MASKED.get(key)
    if known is not None and now - known[0] < REFRESH_SECONDS:
        return known[1]
    masked = bool(await (reader or read_for)(tenant_id, camera_id))
    _MASKED[key] = (now, masked)
    return masked


def forget(camera_id) -> None:
    """A zone of the camera was drawn or deleted in this process: ask again."""
    _MASKED.pop(str(camera_id), None)
