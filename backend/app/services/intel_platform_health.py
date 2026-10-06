"""Is the intelligence runner keeping up — for everybody?

The security intelligence layer's row on the platform owner's console. It is
built the way the drone module's is (services/drone_platform_health.py), on the
rule services/platform_health.py states: ask the thing itself, and never show
green for a check that did not run.

TWO QUESTIONS, TWO SOURCES.

  Is the runner alive?   The runner writes a heartbeat to Redis after every
                         pass, with an expiry (services/intel_runner.py). No key
                         means no runner.

  Is it keeping up?      `platform_intel_health()` (migration 0141) counts,
                         across every organisation with the layer on, the events
                         still not placed in a situation a minute after they
                         were read, the situations still not assessed a minute
                         after they changed, and the sources whose last read
                         failed. A heartbeat cannot tell a busy runner from one
                         falling behind; the counts can.

AN INSTALLATION THAT HAS NOT SWITCHED IT ON IS HEALTHY. The layer is off until
an organisation's administrator turns it on. With nobody using it there is no
work for a runner and its absence is not a fault — so the console of an
installation that never switched it on reads as it did before.

WHAT IS NOT HERE, AND MUST NEVER BE. Which organisation, which site, what was
assessed, what was suggested, what anybody decided. The platform owner is not a
customer's security operator: this row is counts, a status and a sentence, and
the function behind it returns nothing else.

A STOPPED RUNNER STOPS ONLY THIS LAYER. Alerts, pushes, incidents and video do
not pass through it. The row says so, because "down" on a vendor's console is
read by somebody deciding how fast to get out of bed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from app.services import intel_runner

SERVICE = "security-intelligence"

#: What the function returns, and — with service, status, detail and the
#: heartbeat's age — everything this row may ever carry.
COUNTS = ("enabled", "events_waiting", "situations_waiting", "sources_failing")
MAY_CARRY = frozenset(COUNTS) | {"service", "status", "detail", "heartbeat_age_seconds"}

STILL_WORKS = "alerts, incidents and video do not depend on it"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def verdict(counts: dict | None, heartbeat: dict | None, *, heartbeat_asked: bool,
            now: datetime | None = None) -> dict:
    """The row, from what was measured. Pure, so every branch is tested."""
    if counts is None:
        return {"service": SERVICE, "status": "unknown",
                "detail": "the security intelligence tables could not be asked"}

    row = {"service": SERVICE, **{name: int(counts[name]) for name in COUNTS}}
    enabled = row["enabled"]
    if not enabled:
        return {**row, "status": "ok", "detail": "no organisation has switched security intelligence on"}

    load = f"{_plural(enabled, 'organisation')} with it on"
    if not heartbeat_asked:
        return {**row, "status": "unknown",
                "detail": f"the runner's heartbeat could not be read; {load}"}
    if heartbeat is None:
        return {**row, "status": "down",
                "detail": f"the intelligence runner has not reported for {intel_runner.HEARTBEAT_TTL_SECONDS} s; "
                          f"{load}. Nothing new is being assessed or suggested; {STILL_WORKS}"}

    try:
        at = datetime.fromisoformat(heartbeat["at"])
        row["heartbeat_age_seconds"] = max(0, round(((now or datetime.now(timezone.utc)) - at).total_seconds()))
    except (KeyError, TypeError, ValueError):
        pass

    problems = []
    if not heartbeat.get("ok", True):
        problems.append("its last pass had a failure")
    if row["events_waiting"]:
        problems.append(f"{_plural(row['events_waiting'], 'event')} waiting over a minute to be placed")
    if row["situations_waiting"]:
        problems.append(f"{_plural(row['situations_waiting'], 'situation')} waiting over a minute to be assessed")
    if row["sources_failing"]:
        problems.append(f"{_plural(row['sources_failing'], 'source')} could not be read")
    if problems:
        return {**row, "status": "degraded", "detail": f"runner alive, but {'; '.join(problems)}"}
    return {**row, "status": "ok", "detail": f"runner alive; {load}"}


async def check_security_intelligence(session) -> dict:
    """Asked inside a savepoint: a failure here is this row's `unknown`, and
    leaves the console's session usable for whatever reads it next."""
    try:
        async with session.begin_nested():
            counts = (await session.execute(text("SELECT * FROM platform_intel_health()"))).mappings().first()
    except Exception:  # noqa: BLE001 — not being able to ask is an answer of its own
        return verdict(None, None, heartbeat_asked=False)
    asked, heartbeat = await intel_runner.ask_heartbeat()
    return verdict(dict(counts), heartbeat, heartbeat_asked=asked)
