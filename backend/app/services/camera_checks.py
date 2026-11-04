"""A camera's frame rate, delay and missed frames, measured by a 10-second check every 15 minutes.

Phase 6 of closing the feature reference's known limits (docs/plans/2026-11-04-device-health-measured-design.md).

  figures()      what one check received, from when each frame arrived and the camera's own frame times. Pure.
  judge()        the limits a check broke, in words. Pure.
  failure()      why a check could not run, from what ffmpeg said. Pure. What ffmpeg said is never kept: it can
                 hold the camera's address and sign-in.
  answer_ms()    how long the camera takes to answer one RTSP OPTIONS request.
  listen()       one 10-second listen to a stream: ffmpeg copies the video packets and says when each arrives.
                 Nothing is decoded and no picture is kept.
  run_forever()  the camera feed service's loop: every stream once in 15 minutes, spread out, two at a time.
  limits(), latest(), for_camera()   what the screens read.

WHAT A CHECK IS. The frame rate is the frames that arrived in the 10 seconds after the first, beside the rate the
camera's own frame times give (what it says it sends). The longest stall is the longest wait between two frames,
the end of the 10 seconds included. Frames missed are the frames the camera's own times say were sent and did not
arrive. The answer time is from sending OPTIONS to the first line of the reply; the first picture is from asking for
the stream to the first whole picture. Over TCP - the way the cameras are read - a lost packet is sent again, so loss
is seen as a stall or a missed frame and is never counted as such.

WHAT IT IS NOT. It never touches the feed the AI is given. It does not judge the quality of a picture. Between two
checks nothing is seen. A drone's camera (on only in flight; a drone reports its own link), a camera switched off and
a stream whose address is not RTSP are not checked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.sites import site_scope_clause

logger = logging.getLogger(__name__)

#: Every stream is checked once in this many seconds.
EVERY_S = 15 * 60
#: How long a check listens, from the first frame.
LISTEN_S = 10.0
#: A check that has not finished by now is stopped and kept as timed out.
MOST_S = 20.0
#: How long the answer to OPTIONS is waited for.
ANSWER_WAIT_S = 5.0
#: No more checks than this run at once.
AT_ONCE = 2
#: A check older than this says how a camera was, not how it is.
STALE_AFTER_S = 45 * 60
#: Waited after the camera feed service starts, so the check does not compete with its first connections.
FIRST_WAIT_S = 60.0

RESULTS = ("OK", "NO_CONNECTION", "SIGN_IN_REFUSED", "SECOND_CONNECTION_REFUSED", "NO_VIDEO", "TIMED_OUT", "FAILED")
RESULT_TEXT = {
    "OK": "Checked",
    "NO_CONNECTION": "The check could not connect to the camera",
    "SIGN_IN_REFUSED": "The camera refused the sign-in",
    "SECOND_CONNECTION_REFUSED": "The camera refused a second connection",
    "NO_VIDEO": "The camera sent no video on this address",
    "TIMED_OUT": "The camera did not answer in time",
    "FAILED": "The check could not be made",
}
#: The limits an organisation starts with, and what each may be set to.
DEFAULTS = {"least_rate_pct": 70, "most_stall_ms": 2000, "most_missed_pct": 2.0, "most_answer_ms": 500,
            "most_first_picture_ms": 5000, "most_gap_s": 60}
RANGES = {"least_rate_pct": (10, 100), "most_stall_ms": (200, 60000), "most_missed_pct": (0, 50),
          "most_answer_ms": (10, 10000), "most_first_picture_ms": (500, 60000), "most_gap_s": (10, 3600)}
LIMIT_LABEL = {"least_rate_pct": "Frame rate below this share of what the camera says it sends (%)",
               "most_stall_ms": "A stall longer than (ms)", "most_missed_pct": "More frames missed than (%)",
               "most_answer_ms": "An answer slower than (ms)", "most_first_picture_ms": "A first picture later than (ms)",
               "most_gap_s": "A recording gap longer than (seconds)"}
#: The camera's own frame times say a frame is missing when the step between two is this many frames or more.
_MISSING_FROM = 1.5
#: A step in the camera's own times this long is a restart of its clock, not missed frames.
_CLOCK_JUMP_S = 5.0


# ─── What one check received ────────────────────────────────────────────────

def figures(arrivals: Sequence[float], stamps: Sequence[float], *, listened_s: float | None = None) -> dict:
    """The figures of one check. `arrivals` are the seconds after asking at which each frame arrived, `stamps` the
    camera's own time of each frame in seconds; both in arrival order. `listened_s` is how long it listened from the
    first frame (else to the last frame). Nothing arrived: frames 0 and nothing else."""
    if not arrivals:
        return {"frames": 0, "stated_rate": None, "received_rate": None, "longest_stall_ms": None,
                "missed_pct": None, "first_picture_ms": None, "listened_ms": None}
    first, last = arrivals[0], arrivals[-1]
    span = listened_s if listened_s is not None else last - first
    steps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    if listened_s is not None:
        steps.append(max(0.0, first + listened_s - last))      # waiting at the end is a stall too
    stamp_steps = [b - a for a, b in zip(stamps, stamps[1:]) if 0 < b - a < _CLOCK_JUMP_S]
    nominal = statistics.median(stamp_steps) if stamp_steps else None
    missed = sum(max(0, round(s / nominal) - 1) for s in stamp_steps if s >= _MISSING_FROM * nominal) if nominal else 0
    frames = len(arrivals)
    return {
        "frames": frames,
        "stated_rate": round(1 / nominal, 2) if nominal else None,
        # Frames after the first, over the time listened: a stream that stops half way reads half its rate.
        "received_rate": round((frames - 1) / span, 2) if span > 0 else None,
        "longest_stall_ms": int(round(max(steps) * 1000)) if steps else None,
        "missed_pct": round(100 * missed / (frames + missed), 2) if nominal else None,
        "first_picture_ms": int(round(first * 1000)),
        "listened_ms": int(round(span * 1000)),
    }


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def judge(figs: Mapping, limits: Mapping, answer: int | None) -> list[dict]:
    """The limits one check broke, each with what is said of it. Only a check that ran is judged."""
    out: list[dict] = []
    stated, received = _num(figs.get("stated_rate")), _num(figs.get("received_rate"))
    if stated and received is not None and received < stated * float(limits["least_rate_pct"]) / 100:
        out.append({"limit": "least_rate_pct", "text": f"frame rate {received:.1f} of {stated:.0f}"})
    stall = figs.get("longest_stall_ms")
    if stall is not None and stall > int(limits["most_stall_ms"]):
        out.append({"limit": "most_stall_ms", "text": f"a stall of {stall / 1000:.1f} s"})
    missed = _num(figs.get("missed_pct"))
    if missed is not None and missed > float(limits["most_missed_pct"]):
        out.append({"limit": "most_missed_pct", "text": f"{missed:.1f}% of frames missed"})
    if answer is not None and answer > int(limits["most_answer_ms"]):
        out.append({"limit": "most_answer_ms", "text": f"it took {answer} ms to answer"})
    first = figs.get("first_picture_ms")
    if first is not None and first > int(limits["most_first_picture_ms"]):
        out.append({"limit": "most_first_picture_ms", "text": f"the first picture took {first / 1000:.1f} s"})
    return out


_REFUSED = re.compile(r"\b(453|503)\b|not enough bandwidth|service unavailable", re.I)
_SIGN_IN = re.compile(r"\b(401|403)\b|unauthori[sz]ed|forbidden", re.I)
_NO_VIDEO = re.compile(r"\b404\b|stream not found|does not contain any stream|matches no streams|no video", re.I)
_NO_CONNECTION = re.compile(r"connection refused|no route to host|name or service not known|failed to resolve|"
                            r"network is unreachable|timed out|connection reset|host is down", re.I)


def failure(stderr: str) -> str:
    """Why a check could not run, from what ffmpeg said. The words are read here and dropped."""
    for result, pattern in (("SECOND_CONNECTION_REFUSED", _REFUSED), ("SIGN_IN_REFUSED", _SIGN_IN),
                            ("NO_VIDEO", _NO_VIDEO), ("NO_CONNECTION", _NO_CONNECTION)):
        if pattern.search(stderr or ""):
            return result
    return "FAILED"


# ─── Listening to a camera ──────────────────────────────────────────────────

def _without_sign_in(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


async def answer_ms(url: str, *, wait_s: float = ANSWER_WAIT_S) -> int | None:
    """How long the camera takes to answer an RTSP OPTIONS request, in ms; None when it does not answer as RTSP.
    The request carries no sign-in."""
    parts = urlsplit(url)
    if parts.scheme.lower() != "rtsp" or not parts.hostname:
        return None
    loop = asyncio.get_running_loop()
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(parts.hostname, parts.port or 554), wait_s)
        writer.write(f"OPTIONS {_without_sign_in(url)} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: camera-check\r\n\r\n"
                     .encode("ascii", "ignore"))
        sent = loop.time()
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), wait_s)
        return int(round((loop.time() - sent) * 1000)) if line.startswith(b"RTSP/") else None
    except (OSError, asyncio.TimeoutError, ValueError):
        return None
    finally:
        if writer is not None:
            writer.close()


def ffmpeg_args(url: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Copy the first video stream's packets and print one line as each arrives: no picture is decoded, and ffmpeg is
    told not to wait while it looks at the stream, so the time a line is read is the time its frame arrived."""
    return [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", "5000000",
            "-fflags", "nobuffer", "-analyzeduration", "0", "-probesize", "32", "-i", url,
            "-map", "0:v:0", "-c", "copy", "-flush_packets", "1", "-f", "framecrc", "pipe:1"]


async def listen(url: str, *, seconds: float = LISTEN_S, most_s: float = MOST_S, ffmpeg: str = "ffmpeg") -> dict:
    """One check of one stream: `{"result": ..., **figures}`. Stopped after `most_s` whatever happens."""
    loop = asyncio.get_running_loop()
    asked = loop.time()
    try:
        proc = await asyncio.create_subprocess_exec(*ffmpeg_args(url, ffmpeg), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
    except OSError:
        return {"result": "FAILED", **figures([], [])}
    arrivals: list[float] = []
    stamps: list[float] = []
    base = [1 / 90000]

    async def read() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            at = loop.time() - asked
            line = raw.decode("ascii", "replace").strip()
            if line.startswith("#tb 0:"):
                num, _, den = line.split(":", 1)[1].strip().partition("/")
                if num.isdigit() and den.isdigit() and int(den):
                    base[0] = int(num) / int(den)
                continue
            if not line or line.startswith("#"):
                continue
            cells = [c.strip() for c in line.split(",")]
            if len(cells) < 3 or cells[0] != "0":
                continue
            stamp = cells[2] if cells[2].lstrip("-").isdigit() else cells[1]
            if not stamp.lstrip("-").isdigit():
                continue
            arrivals.append(at)
            stamps.append(int(stamp) * base[0])
            if at - arrivals[0] >= seconds:
                return

    timed_out = False
    try:
        await asyncio.wait_for(read(), most_s)
    except asyncio.TimeoutError:
        timed_out = True
    stopped = loop.time() - asked
    stderr = ""
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:                         # it ended as it was being stopped
            pass
    try:
        _, err = await asyncio.wait_for(proc.communicate(), 5)
        stderr = (err or b"").decode("utf-8", "replace")
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
    if arrivals:
        # From the first frame for `seconds`, or until the stream ended or the check was stopped.
        return {"result": "OK", **figures(arrivals, stamps, listened_s=min(seconds, stopped - arrivals[0]))}
    return {"result": "TIMED_OUT" if timed_out else failure(stderr), **figures([], [])}


# ─── The organisation's limits ──────────────────────────────────────────────

async def limits(db: AsyncSession) -> dict:
    """The calling organisation's limits: the defaults until it changes them, and who changed them last."""
    row = (await db.execute(text("""
        SELECT s.least_rate_pct, s.most_stall_ms, s.most_missed_pct, s.most_answer_ms, s.most_first_picture_ms,
               s.most_gap_s, s.updated_at, u.full_name AS updated_by_name
          FROM camera_check_settings s LEFT JOIN users u ON u.id = s.updated_by_user_id
    """))).mappings().first()
    if row is None:
        return {**DEFAULTS, "updated_at": None, "updated_by_name": None}
    return {**dict(row), "most_missed_pct": float(row["most_missed_pct"])}


# ─── The loop in the camera feed service ────────────────────────────────────

_DUE = """
    SELECT s.id AS stream_id, s.camera_id, s.url, s.auth_config
      FROM streams s JOIN cameras c ON c.id = s.camera_id
     WHERE c.is_active = TRUE AND lower(s.url) LIKE 'rtsp://%'
       AND NOT EXISTS (SELECT 1 FROM drones d WHERE d.camera_id = c.id)
     ORDER BY s.camera_id, s.id
"""


async def due(session_factory) -> list[dict]:
    """Every stream to be checked, of every organisation, each read under its own organisation."""
    async with session_factory() as db:
        tenants = [str(r[0]) for r in await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))]
    out: list[dict] = []
    for tenant_id in tenants:
        try:
            async with session_factory() as db:
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
                rows = (await db.execute(text(_DUE))).mappings().all()
                await db.rollback()
        except Exception:
            logger.exception("camera checks: could not list the streams of tenant %s", tenant_id)
            continue
        out += [{**dict(r), "tenant_id": tenant_id} for r in rows]
    return out


async def keep(db: AsyncSession, stream: Mapping, answer: int | None, heard: Mapping, now: datetime) -> dict:
    """Keep one check, judged by the organisation's limits as they are now. The caller has set the organisation and
    commits."""
    result = heard["result"]
    figs = {k: v for k, v in heard.items() if k != "result"}
    breaks = judge(figs, await limits(db), answer) if result == "OK" else []
    row = (await db.execute(text("""
        INSERT INTO camera_checks (tenant_id, camera_id, stream_id, checked_at, result, listened_ms, frames,
                                   stated_rate, received_rate, longest_stall_ms, missed_pct, answer_ms,
                                   first_picture_ms, breaks)
        VALUES (current_setting('app.current_tenant')::uuid, :camera, :stream, :at, :result, :listened, :frames,
                :stated, :received, :stall, :missed, :answer, :first, CAST(:breaks AS jsonb))
        RETURNING id
    """), {"camera": str(stream["camera_id"]), "stream": str(stream["stream_id"]), "at": now, "result": result,
           "listened": figs.get("listened_ms"), "frames": figs.get("frames") if result == "OK" else None,
           "stated": figs.get("stated_rate"), "received": figs.get("received_rate"),
           "stall": figs.get("longest_stall_ms"), "missed": figs.get("missed_pct"), "answer": answer,
           "first": figs.get("first_picture_ms"), "breaks": json.dumps(breaks)})).first()
    return {"id": row[0], "result": result, "breaks": breaks}


async def check_one(session_factory, stream: Mapping, build_url: Callable[[str, dict | None], str], *,
                    listen_fn: Callable[[str], Awaitable[dict]] = listen,
                    answer_fn: Callable[[str], Awaitable[int | None]] = answer_ms) -> dict | None:
    """Check one stream and keep what was found. Nothing half-made is kept; the address is never logged."""
    url = build_url(stream["url"], dict(stream["auth_config"]) if stream.get("auth_config") else None)
    answer = await answer_fn(url)
    heard = await listen_fn(url)
    try:
        async with session_factory() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(stream["tenant_id"])})
            kept = await keep(db, stream, answer, heard, datetime.now(timezone.utc))
            await db.commit()
    except Exception:
        logger.exception("camera check: could not keep the check of camera %s", stream["camera_id"])
        return None
    if kept["result"] != "OK" or kept["breaks"]:
        logger.info("camera check: camera %s %s %s", stream["camera_id"], kept["result"],
                    "; ".join(b["text"] for b in kept["breaks"]))
    return kept


async def run_cycle(session_factory, build_url, *, every_s: float = EVERY_S, at_once: int = AT_ONCE,
                    check=check_one) -> int:
    """One round: every stream once, spread evenly across `every_s`, no more than `at_once` at a time."""
    streams = await due(session_factory)
    if not streams:
        return 0
    loop = asyncio.get_running_loop()
    start, step = loop.time(), every_s / len(streams)
    gate = asyncio.Semaphore(at_once)

    async def one(stream: Mapping) -> None:
        async with gate:
            try:
                await check(session_factory, stream, build_url)
            except Exception:
                logger.exception("camera check failed for camera %s", stream["camera_id"])

    tasks = []
    for n, stream in enumerate(streams):
        wait = start + n * step - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        tasks.append(asyncio.create_task(one(stream)))
    await asyncio.gather(*tasks)
    return len(streams)


async def run_forever(session_factory, build_url) -> None:
    """The camera feed service's check loop. One round's trouble is its own; the next round runs."""
    await asyncio.sleep(FIRST_WAIT_S)
    loop = asyncio.get_running_loop()
    while True:
        began = loop.time()
        try:
            await run_cycle(session_factory, build_url)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("camera checks: a round failed")
        await asyncio.sleep(max(5.0, EVERY_S - (loop.time() - began)))


# ─── What the screens read ──────────────────────────────────────────────────

_CHECK_COLUMNS = """k.checked_at, k.result, k.listened_ms, k.frames, k.stated_rate, k.received_rate,
                    k.longest_stall_ms, k.missed_pct, k.answer_ms, k.first_picture_ms, k.breaks"""


def shown(check: Mapping | None, now: datetime) -> dict | None:
    """A check as a screen shows it: its figures, what its result means, and whether it is too old to go by."""
    if check is None or check.get("checked_at") is None:
        return None
    out = {k: check.get(k) for k in ("checked_at", "result", "listened_ms", "frames", "longest_stall_ms",
                                     "answer_ms", "first_picture_ms")}
    for k in ("stated_rate", "received_rate", "missed_pct"):
        out[k] = _num(check.get(k))
    breaks = check.get("breaks") or []
    out["breaks"] = json.loads(breaks) if isinstance(breaks, str) else list(breaks)
    out["result_text"] = RESULT_TEXT[out["result"]]
    out["stale"] = (now - check["checked_at"]).total_seconds() > STALE_AFTER_S
    return out


def not_checked(row: Mapping) -> str | None:
    """Why a stream is not checked, or None when it is."""
    if row.get("is_drone"):
        return "A drone's camera is not checked: a drone reports its own link."
    if not row.get("is_active"):
        return "Switched off in the platform."
    if not str(row.get("url") or "").lower().startswith("rtsp://"):
        return "Only an RTSP address is checked."
    return None


async def latest(db: AsyncSession, now: datetime, *, allowed: Sequence[str] | None = None, site_id: Any = None,
                 camera_id: Any = None) -> list[dict]:
    """Each camera the caller may see, its streams with their latest check, and its recording gaps in the last 24
    hours. Cameras in name order."""
    params: dict = {"day_ago": now - timedelta(hours=24)}
    where = []
    scope = site_scope_clause(allowed, "c.site_id", params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append("c.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    if camera_id is not None:
        where.append("c.id = CAST(:camera AS uuid)")
        params["camera"] = str(camera_id)
    rows = (await db.execute(text(f"""
        SELECT c.id AS camera_id, c.name AS camera_name, c.site_id, si.name AS site_name, c.is_active,
               EXISTS (SELECT 1 FROM drones d WHERE d.camera_id = c.id) AS is_drone,
               s.id AS stream_id, s.protocol, s.url, s.status AS stream_status, s.continuous_recording,
               {_CHECK_COLUMNS}
          FROM cameras c LEFT JOIN sites si ON si.id = c.site_id
          LEFT JOIN streams s ON s.camera_id = c.id
          LEFT JOIN LATERAL (SELECT * FROM camera_checks k WHERE k.stream_id = s.id
                              ORDER BY k.checked_at DESC LIMIT 1) k ON TRUE
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY c.name, c.id, s.created_at, s.id
    """), params)).mappings().all()
    gaps = {str(r["camera_id"]): dict(r) for r in (await db.execute(text("""
        SELECT camera_id, count(*) AS gaps, max(seconds) AS longest_s, bool_or(ended_at IS NULL) AS open
          FROM recording_gaps WHERE over_limit AND (ended_at IS NULL OR ended_at >= :day_ago)
         GROUP BY camera_id
    """), {"day_ago": params["day_ago"]})).mappings()}
    cameras: dict[str, dict] = {}
    for r in rows:
        key = str(r["camera_id"])
        cam = cameras.setdefault(key, {
            "camera_id": r["camera_id"], "camera_name": r["camera_name"], "site_id": r["site_id"],
            "site_name": r["site_name"], "streams": [],
            "gaps_24h": {"gaps": 0, "longest_s": None, "open": False, **{k: v for k, v in
                         (gaps.get(key) or {}).items() if k != "camera_id"}}})
        if r["stream_id"] is None:
            continue
        cam["streams"].append({"stream_id": r["stream_id"], "protocol": r["protocol"],
                               "stream_status": r["stream_status"], "records_all_the_time": r["continuous_recording"],
                               "not_checked": not_checked(r), "check": shown(r, now)})
    return list(cameras.values())


async def for_camera(db: AsyncSession, camera_id: Any, days: int, now: datetime) -> dict:
    """A camera's checks and its recording gaps over the last `days` days, oldest check first, newest gap first."""
    since = now - timedelta(days=days)
    checks = (await db.execute(text(f"""
        SELECT k.stream_id, {_CHECK_COLUMNS} FROM camera_checks k
         WHERE k.camera_id = CAST(:c AS uuid) AND k.checked_at >= :since ORDER BY k.checked_at, k.id
    """), {"c": str(camera_id), "since": since})).mappings().all()
    gaps = (await db.execute(text("""
        SELECT stream_id, kind, started_at, ended_at, seconds, camera_was_offline, limit_s, over_limit
          FROM recording_gaps WHERE camera_id = CAST(:c AS uuid) AND COALESCE(ended_at, :now) >= :since
         ORDER BY started_at DESC LIMIT 500
    """), {"c": str(camera_id), "since": since, "now": now})).mappings().all()
    from app.services.recording_gaps import KIND_TEXT
    return {"checks": [{"stream_id": c["stream_id"], **shown(c, now)} for c in checks],
            "gaps": [{**dict(g), "kind_text": KIND_TEXT[g["kind"]]} for g in gaps]}
