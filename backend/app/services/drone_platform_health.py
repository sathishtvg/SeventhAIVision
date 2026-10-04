"""Is the drone service keeping up — for everybody?

The drone module's row on the platform owner's console. It follows the rule
services/platform_health.py is built on: ask the thing itself, and never show
green for a check that did not run.

TWO QUESTIONS, TWO SOURCES.

  Is the runner alive?   The runner writes a heartbeat to Redis on every pass of
                         its loop, with an expiry. No key means no runner — or a
                         runner stuck inside one pass for a minute, which for a
                         process that must act on an abort within seconds is the
                         same thing.

  Is it keeping up?      `platform_drone_health()` (migration 0130) counts, across
                         all tenants, the flights in the air, the commands the
                         runner has owed for over a minute and the report emails
                         more than fifteen minutes late. A database cannot tell a
                         dead runner from a quiet fleet, which is why the
                         heartbeat exists; a heartbeat cannot tell a busy runner
                         from one falling behind, which is why the counts do.

AN INSTALLATION THAT DOES NOT FLY IS HEALTHY. With nobody licensed, nothing in
the air and nothing owed, there is no work for a runner and its absence is not a
fault — so the console of a customer who never bought the module stays as it
was.

WHAT IS NOT HERE. A site's edge gateway being offline, a drone losing its link,
a low battery: those are the customer's emergencies, alerted to their own
operators. This row is the vendor's question only.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import text

SERVICE = "drone-patrol"

HEARTBEAT_KEY = "drone_runner:heartbeat"
#: The runner beats every pass (two seconds by default). A minute of silence is
#: thirty missed passes.
HEARTBEAT_TTL_SECONDS = 60


def heartbeat_value(now: datetime, tick_ok: bool) -> str:
    return json.dumps({"at": now.astimezone(timezone.utc).isoformat(), "tick_ok": bool(tick_ok)})


async def write_heartbeat(redis, tick_ok: bool, now: datetime | None = None) -> None:
    """Called by the runner after each pass. Expires by itself, so a runner that
    stops cannot leave a stale 'alive' behind."""
    await redis.set(HEARTBEAT_KEY, heartbeat_value(now or datetime.now(timezone.utc), tick_ok),
                    ex=HEARTBEAT_TTL_SECONDS)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def verdict(counts: dict | None, heartbeat: dict | None, *, heartbeat_asked: bool,
            now: datetime | None = None) -> dict:
    """The row, from what was measured. Pure, so every branch is tested."""
    if counts is None:
        return {"service": SERVICE, "status": "unknown",
                "detail": "the drone tables could not be asked"}

    licensed, live = int(counts["licensed"]), int(counts["live"])
    commands, emails = int(counts["commands_overdue"]), int(counts["emails_overdue"])
    row = {"service": SERVICE, "licensed": licensed, "live": live,
           "commands_overdue": commands, "emails_overdue": emails}

    if not (licensed or live or commands or emails):
        return {**row, "status": "ok", "detail": "no organisation is licensed for drone patrol"}

    load = f"{_plural(live, 'flight')} in the air, {_plural(licensed, 'organisation')} licensed"
    if not heartbeat_asked:
        return {**row, "status": "unknown",
                "detail": f"the runner's heartbeat could not be read; {load}"}
    if heartbeat is None:
        return {**row, "status": "down",
                "detail": f"the drone runner has not reported for {HEARTBEAT_TTL_SECONDS} s; {load}"}

    try:
        at = datetime.fromisoformat(heartbeat["at"])
        row["heartbeat_age_seconds"] = max(0, round(((now or datetime.now(timezone.utc)) - at).total_seconds()))
    except (KeyError, TypeError, ValueError):
        pass

    problems = []
    if not heartbeat.get("tick_ok", True):
        problems.append("its last flight pass failed")
    if commands:
        problems.append(f"{_plural(commands, 'flight command')} waiting over a minute")
    if emails:
        problems.append(f"{_plural(emails, 'report email')} more than 15 minutes late")
    if problems:
        return {**row, "status": "degraded", "detail": f"runner alive, but {'; '.join(problems)}"}
    return {**row, "status": "ok", "detail": f"runner alive; {load}"}


async def read_heartbeat() -> tuple[bool, dict | None]:
    """(could it be asked, what it said). A key that is not there was asked and
    answered: there is no runner."""
    from redis.asyncio import Redis

    url = os.environ.get("REDIS_URL")
    if not url:
        return False, None
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
    try:
        raw = await client.get(HEARTBEAT_KEY)
    except Exception:
        return False, None
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
    if raw is None:
        return True, None
    try:
        value = json.loads(raw)
        return True, value if isinstance(value, dict) else {}
    except ValueError:
        return True, {}


async def check_drone_patrol(session) -> dict:
    try:
        counts = (await session.execute(text("SELECT * FROM platform_drone_health()"))).mappings().first()
    except Exception:
        return verdict(None, None, heartbeat_asked=False)
    asked, heartbeat = await read_heartbeat()
    return verdict(dict(counts), heartbeat, heartbeat_asked=asked)
