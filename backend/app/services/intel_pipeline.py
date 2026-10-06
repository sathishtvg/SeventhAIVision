"""How long the layer takes, measured from its own records.

Every stage stamps what it writes: an event carries its own time and the time
it was read; a link carries the time the event was placed in a situation; a
situation, an assessment and a suggestion each carry the time they were
written. Nothing new is recorded to measure the layer — this module subtracts
what is already there.

  READ       from an event happening to the runner reading it
  PLACED     from being read to being placed in a situation
  ASSESSED   from a situation opening to its first assessment
  SUGGESTED  from that assessment to the first suggestion for it
  IN_ALL     from a situation's first event to a suggestion being ready —
             what a person at the screen waits for

THE ALERT DOES NOT WAIT FOR ANY OF THIS. It reaches the operator through the
platform's own path, first and unchanged; these are the delays of what the
layer adds afterwards.

WHAT IS LEFT OUT. An event that happened before the layer was switched on was
read late because it was there before the reader was, not because the reader
was slow: the first read reaches back an hour (services/intel_events.py). Those
events, and the situations they opened, are not measured.

A FIGURE NEEDS SOMETHING UNDER IT. The middle value and the value nineteen in
twenty came in under are given for a stage only when at least `FLOOR` were
measured; below that the stage says how many there were and gives no figure.

Reads only, and only the caller's own organisation and sites.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.sites import site_scope_clause

#: Each stage, in the order it happens, with the words a screen shows.
STAGES = (
    ("READ", "From an event happening to its being read"),
    ("PLACED", "From being read to being placed in a situation"),
    ("ASSESSED", "From a situation opening to its first assessment"),
    ("SUGGESTED", "From that assessment to the first suggestion"),
    ("IN_ALL", "From a situation's first event to a suggestion being ready"),
)
#: Fewer than this and a middle value describes the few, not the layer.
FLOOR = 5
MAX_HOURS = 168
NOTE = ("The alert itself does not wait for any of this: it reaches the operator through the platform's own path, "
        "first and unchanged. Events that happened before the layer was switched on are not measured.")


def period(hours: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The last `hours`, ending now."""
    until = now or datetime.now(timezone.utc)
    return until - timedelta(hours=hours), until


def stage(code: str, measured: int, median, p95) -> dict:
    """One stage's line. Pure. No figure from fewer than `FLOOR`."""
    enough = measured >= FLOOR and median is not None and p95 is not None
    return {
        "code": code, "label": dict(STAGES)[code], "measured": int(measured),
        "median_seconds": round(max(0.0, float(median)), 1) if enough else None,
        "p95_seconds": round(max(0.0, float(p95)), 1) if enough else None,
    }


def stages(figures: Mapping[str, tuple]) -> list[dict]:
    """Every stage in order, from {code: (measured, median, p95)}. A stage
    nothing was measured for is still listed, with nothing under it."""
    return [stage(code, *figures.get(code, (0, None, None))) for code, _ in STAGES]


def _three(row: Mapping, name: str) -> tuple:
    return int(row[f"{name}_n"] or 0), row[f"{name}_median"], row[f"{name}_p95"]


def _figures(column: str, name: str) -> str:
    return (f"count({column}) AS {name}_n, "
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {column}) AS {name}_median, "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {column}) AS {name}_p95")


async def measure(db: AsyncSession, allowed: list[str] | None, *, since: datetime, until: datetime) -> list[dict]:
    """The stages over what was read, and the situations that opened, in the
    period — for the sites the caller may see."""
    params: dict = {"since": since, "until": until}
    event_scope = site_scope_clause(allowed, "e.site_id", params)
    situation_scope = site_scope_clause(allowed, "s.site_id", params)
    events = (await db.execute(text(f"""
        WITH switched AS (SELECT min(created_at) AS at FROM security_ingest_cursors),
        measured AS (
            SELECT EXTRACT(EPOCH FROM (e.ingested_at - e.occurred_at)) AS read_s,
                   EXTRACT(EPOCH FROM (l.linked_at - e.ingested_at)) AS placed_s
              FROM security_events e
              LEFT JOIN security_situation_events l ON l.event_id = e.id
             WHERE e.ingested_at >= :since AND e.ingested_at < :until
               AND e.occurred_at >= (SELECT at FROM switched)
               {f'AND {event_scope}' if event_scope else ''}
        )
        SELECT {_figures('read_s', 'read')}, {_figures('placed_s', 'placed')} FROM measured
    """), params)).mappings().one()
    situations = (await db.execute(text(f"""
        WITH switched AS (SELECT min(created_at) AS at FROM security_ingest_cursors),
        measured AS (
            SELECT EXTRACT(EPOCH FROM (a.created_at - s.created_at)) AS assessed_s,
                   EXTRACT(EPOCH FROM (r.first_at - a.created_at)) AS suggested_s,
                   EXTRACT(EPOCH FROM (r.first_at - s.started_at)) AS in_all_s
              FROM security_situations s
              LEFT JOIN security_assessments a ON a.situation_id = s.id AND a.sequence = 1
              LEFT JOIN LATERAL (SELECT min(r.created_at) AS first_at FROM security_recommendations r
                                  WHERE r.assessment_id = a.id) r ON TRUE
             WHERE s.created_at >= :since AND s.created_at < :until
               AND s.started_at >= (SELECT at FROM switched)
               {f'AND {situation_scope}' if situation_scope else ''}
        )
        SELECT {_figures('assessed_s', 'assessed')}, {_figures('suggested_s', 'suggested')},
               {_figures('in_all_s', 'in_all')} FROM measured
    """), params)).mappings().one()
    return stages({
        "READ": _three(events, "read"), "PLACED": _three(events, "placed"),
        "ASSESSED": _three(situations, "assessed"), "SUGGESTED": _three(situations, "suggested"),
        "IN_ALL": _three(situations, "in_all"),
    })
