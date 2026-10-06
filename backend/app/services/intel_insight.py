"""Insight: what a period looked like at a site, and a score that can be read.

  recorded rows ─► counts for a site and a period ─► a score with every
                   deduction stated ─► findings, each with what it rests on

COUNTED ON REQUEST. Nothing is stored for any of this: every number is counted,
when asked, from rows the platform and this layer already keep — situations,
events, decisions, incidents, cameras' health, patrols. So a number here can
always be traced to the rows it counts, and there is no second copy to drift.

THE SCORE IS ARITHMETIC AN OFFICER CAN CHECK. It starts at 100 and loses stated
points for stated things — unresolved incidents, cameras not sending, high-risk
situations still open, situations nobody has decided on, response times missed,
places where things keep happening, patrols missed. Each deduction is a line
with its count, its points and its sentence; the lines add up to the score, and
a test holds that. The points and caps are in `SCORE_RULES`, and a tenant can
weigh each with `intel.score_weights`. It is not a prediction and not a grade
of anybody: it is a count of what is open and what went wrong, turned into one
number so that sites can be put side by side.

NOTHING RECORDED IS NOT THE SAME AS NOTHING WRONG. A site with no cameras, no
situations and no patrols in the period scores 100 because nothing was found —
and the answer says exactly that, in `basis` and `note`, instead of letting 100
read as "secure".

FINDINGS ARE ADVISORY. Fixed rules over the same counts: where most situations
began, which hours, how many were closed as false, how long decisions took.
Each says what it rests on and what a person might consider, is marked
`is_advisory`, and changes nothing. No model, and nothing learned.

WHO IS NOT NAMED. A repeated vehicle is its number plate, which is what the
platform read. A repeated person is counted, by a watchlist entry's id, never
by a name; and nobody who was merely not identified is counted as anybody.

`score()` and `findings()` are pure. `counts_for()` only reads.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TZ = "Asia/Singapore"
DEFAULT_DAYS = 7
MAX_DAYS = 90
#: A situation nobody has decided on for this long is counted as unattended.
UNATTENDED_AFTER = timedelta(minutes=15)
#: A place counts as repeated at this many situations in the period.
REPEATED_AT = 3
#: A rule about a share needs at least this many to speak.
FLOOR = 5

#: What lowers the score: (factor, points each, the most it can take off).
SCORE_RULES = (
    ("OPEN_INCIDENTS", 5, 20),
    ("HIGH_RISK_OPEN", 5, 20),
    ("UNATTENDED", 5, 15),
    ("CAMERAS_OFFLINE", 5, 20),
    ("SLA_BREACHED", 5, 15),
    ("REPEATED_LOCATION", 5, 10),
    ("PATROLS_MISSED", 3, 9),
)
FACTORS = tuple(rule[0] for rule in SCORE_RULES)
#: Score at or above which each band starts.
BANDS = (("GOOD", 85), ("FAIR", 65), ("NEEDS_ATTENTION", 40), ("POOR", 0))
#: The band of a site nothing was recorded for. Its score is 100 and it is not
#: called good: nothing found is not the same as nothing wrong.
NOTHING_RECORDED = "NOTHING_RECORDED"

SENTENCES = {
    "OPEN_INCIDENTS": "{n} unresolved incident(s)",
    "HIGH_RISK_OPEN": "{n} open situation(s) assessed HIGH or CRITICAL",
    "UNATTENDED": "{n} situation(s) waiting more than 15 minutes for a decision",
    "CAMERAS_OFFLINE": "{n} camera(s) not sending",
    "SLA_BREACHED": "{n} incident(s) past their response time in the period",
    "REPEATED_LOCATION": "{n} place(s) where situations keep beginning",
    "PATROLS_MISSED": "{n} patrol(s) missed or failed in the period",
}


#: What each factor counts, for the table that says how the score is made.
EACH = {
    "OPEN_INCIDENTS": "each unresolved incident",
    "HIGH_RISK_OPEN": "each open situation assessed HIGH or CRITICAL",
    "UNATTENDED": "each situation waiting more than 15 minutes for a decision",
    "CAMERAS_OFFLINE": "each camera not sending",
    "SLA_BREACHED": "each incident past its response time in the period",
    "REPEATED_LOCATION": "each place where three or more situations began in the period",
    "PATROLS_MISSED": "each patrol missed or failed in the period",
}


def validate_weights(value: Any) -> None:
    """A tenant's `intel.score_weights`: an object of factor name to a
    multiplier between 0 and 3. A factor left out keeps its shipped weight."""
    if not isinstance(value, dict):
        raise ValueError("must be an object of {factor: multiplier}")
    for name, weight in value.items():
        if name not in FACTORS:
            raise ValueError(f"'{name}' is not a score factor; use {', '.join(FACTORS)}")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 3:
            raise ValueError(f"'{name}' must be a number between 0 and 3")


def band_of(value: int) -> str:
    return next(name for name, floor in BANDS if value >= floor)


# ─── Pure: the score ─────────────────────────────────────────────────────────

def score(counts: Mapping, weights: Mapping[str, float] | None = None) -> dict:
    """The site security score from one site's counts. Every deduction is
    stated; they add up to the score. Pure."""
    weights = weights or {}
    measured = {
        "OPEN_INCIDENTS": int(counts.get("incidents_open") or 0),
        "HIGH_RISK_OPEN": int(counts.get("high_risk_open") or 0),
        "UNATTENDED": int(counts.get("unattended") or 0),
        "CAMERAS_OFFLINE": int(counts.get("cameras_offline") or 0),
        "SLA_BREACHED": int(counts.get("sla_breached") or 0),
        "REPEATED_LOCATION": int(counts.get("repeated_locations") or 0),
        "PATROLS_MISSED": int(counts.get("patrols_missed") or 0),
    }
    deductions, went_well = [], []
    for factor, each, cap in SCORE_RULES:
        n = measured[factor]
        if n <= 0:
            continue
        weight = float(weights.get(factor, 1))
        points = min(round(cap * weight), round(n * each * weight))
        if points > 0:
            deductions.append({"factor": factor, "count": n, "points": -points,
                               "detail": SENTENCES[factor].format(n=n) + ".",
                               "capped": round(n * each * weight) > round(cap * weight)})
    value = max(0, 100 - sum(-d["points"] for d in deductions))

    # What went right is said, and takes nothing off and adds nothing on.
    if counts.get("cameras") and not measured["CAMERAS_OFFLINE"]:
        went_well.append(f"All {counts['cameras']} camera(s) are sending.")
    if counts.get("drone_patrols_completed"):
        went_well.append(f"{counts['drone_patrols_completed']} drone patrol(s) completed.")
    if counts.get("virtual_patrols_completed"):
        went_well.append(f"{counts['virtual_patrols_completed']} virtual patrol(s) completed.")
    if counts.get("situations") and not measured["HIGH_RISK_OPEN"] and not measured["UNATTENDED"]:
        went_well.append("No situation is waiting for a decision or open at high risk.")

    basis = {key: int(counts.get(key) or 0) for key in (
        "cameras", "situations", "incidents", "virtual_patrols", "drone_patrols")}
    note = None
    if not any(basis.values()):
        note = ("Nothing was recorded for this site in the period — no cameras, situations, incidents or patrols. "
                "The score says that nothing was found wrong, not that nothing is.")
    elif not basis["situations"]:
        note = "No situation was recorded in the period. The score rests on cameras, incidents and patrols alone."
    return {"score": value, "out_of": 100, "band": band_of(value) if any(basis.values()) else NOTHING_RECORDED,
            "deductions": deductions, "went_well": went_well, "basis": basis, "note": note}


# ─── Pure: the findings ──────────────────────────────────────────────────────

def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def busiest_hours(by_hour: Mapping[int, int], width: int = 4) -> tuple[int, int] | None:
    """(the hour a `width`-hour band starts at, how many began in it) for the
    band where most situations began, wrapping past midnight — or None."""
    total = sum(by_hour.values())
    if not total:
        return None
    best = max(range(24), key=lambda h: (sum(by_hour.get((h + k) % 24, 0) for k in range(width)), -h))
    return best, sum(by_hour.get((best + k) % 24, 0) for k in range(width))


def findings(c: Mapping, days: int) -> list[dict]:
    """What stands out in a period, by fixed rules, each with what it rests on
    and something a person might consider. Advisory, every one. Pure."""
    out: list[dict] = []

    def add(code: str, finding: str, consider: str, **rests_on) -> None:
        out.append({"code": code, "finding": finding, "consider": consider, "rests_on": rests_on,
                    "is_advisory": True, "is_decision": False})

    total = int(c.get("situations") or 0)
    places = list(c.get("locations") or [])
    if places and total >= FLOOR and _pct(places[0]["situations"], total) >= 40:
        top = places[0]
        add("CONCENTRATED_PLACE",
            f"{top['name']} is where {_pct(top['situations'], total)}% of the situations of the last {days} day(s) "
            f"began ({top['situations']} of {total}).",
            "Review what that camera covers and the rule that raises its alerts, and whether the place needs "
            "more patrols.", place=top["name"], situations=top["situations"], of=total)
    hours = busiest_hours({int(h): int(n) for h, n in (c.get("by_hour") or {}).items()})
    if hours and total >= FLOOR and _pct(hours[1], total) >= 50:
        start, n = hours
        add("CONCENTRATED_HOURS",
            f"{_pct(n, total)}% of the situations began between {start:02d}:00 and {(start + 4) % 24:02d}:00 "
            f"({n} of {total}).",
            "More patrols, or another pair of eyes, in those hours.",
            from_hour=start, to_hour=(start + 4) % 24, situations=n, of=total, timezone=c.get("timezone"))
    closed, false = int(c.get("closed") or 0), int(c.get("false_positive") or 0)
    if closed >= FLOOR and _pct(false, closed) >= 50:
        add("MOSTLY_FALSE", f"{false} of the {closed} situations closed in the period were closed as false positives "
                            f"({_pct(false, closed)}%).",
            "Have the cameras and rules that raised them checked: an alert that is usually wrong teaches people "
            "to stop looking.", false_positive=false, closed=closed)
    waited = c.get("median_seconds_to_decide")
    if waited is not None and int(c.get("decided") or 0) >= FLOOR and waited > 600:
        add("SLOW_DECISIONS", f"Half of the situations waited more than {round(waited / 60)} minutes for their first "
                              f"decision.",
            "Check who is watching in the hours they began, and whether the decision policy leaves the right "
            "people able to decide.", median_seconds=int(waited), decided=int(c["decided"]))
    offline = int(c.get("cameras_offline") or 0)
    if offline:
        add("CAMERAS_OFFLINE", f"{offline} of {int(c.get('cameras') or 0)} camera(s) are not sending.",
            "Have them checked: what they cover is not being watched.", offline=offline,
            names=list(c.get("offline_names") or [])[:5])
    missed = int(c.get("patrols_missed") or 0)
    if missed:
        add("PATROLS_MISSED", f"{missed} patrol(s) were missed or failed in the period.",
            "Find out why they did not run: a schedule nobody keeps is not coverage.", missed=missed)
    for v in list(c.get("vehicles") or [])[:3]:
        if v["situations"] >= REPEATED_AT:
            add("REPEATED_VEHICLE", f"Number plate {v['plate']} was part of {v['situations']} situations.",
                "A watchlist entry for it — allowed or blocked — so that the next read says which.",
                plate=v["plate"], situations=v["situations"])
    overrides, decided = int(c.get("overrides") or 0), int(c.get("decisions") or 0)
    if decided >= FLOOR and _pct(overrides, decided) >= 40:
        add("OFTEN_OVERRIDDEN", f"{overrides} of {decided} decisions went against what was suggested "
                                f"({_pct(overrides, decided)}%).",
            "Read the reasons given: they say what the suggestions are missing at this site.",
            overrides=overrides, decisions=decided)
    return out


# ─── Reading ─────────────────────────────────────────────────────────────────

def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def weights(db: AsyncSession) -> dict:
    """The tenant's own score weights, or none."""
    value = _loads((await db.execute(text(
        "SELECT setting_value FROM tenant_settings WHERE setting_key = 'intel.score_weights'"))).scalar_one_or_none())
    return value if isinstance(value, dict) else {}


async def zone_for(db: AsyncSession, site_id) -> str:
    name = (await db.execute(text(
        "SELECT COALESCE((SELECT p.timezone FROM security_site_profiles p WHERE p.site_id = CAST(:s AS uuid)), "
        "                (SELECT t.timezone FROM tenants t WHERE t.id = current_setting('app.current_tenant')::uuid))"),
        {"s": str(site_id) if site_id else None})).scalar()
    try:
        return ZoneInfo(name or DEFAULT_TZ).key
    except Exception:  # noqa: BLE001 — an unknown zone name must not stop a count
        return DEFAULT_TZ


def _scope(site_ids: list | None, column: str, params: dict) -> str:
    """A filter keeping to the given sites, or to every site when None."""
    if site_ids is None:
        return "TRUE"
    params["sites"] = [str(s) for s in site_ids]
    return f"{column} = ANY(CAST(:sites AS uuid[]))"


async def score_counts(db: AsyncSession, site_ids: list | None, since: datetime, now: datetime) -> dict[str, dict]:
    """What the score rests on, for each site: {site id: counts}. Grouped, so
    that scoring every site is a handful of statements, not a handful a site."""
    out: dict[str, dict] = {}

    def at(site) -> dict:
        return out.setdefault(str(site), {})

    p: dict = {"since": since, "now": now, "stale": now - UNATTENDED_AFTER}
    for r in (await db.execute(text(f"""
        SELECT s.site_id,
               count(*) FILTER (WHERE s.started_at >= :since) AS situations,
               count(*) FILTER (WHERE s.closed_at IS NULL AND s.risk_level IN ('HIGH', 'CRITICAL')) AS high_risk_open,
               count(*) FILTER (WHERE s.closed_at IS NULL AND s.decision_status = 'AWAITING'
                                  AND s.started_at < :stale) AS unattended
          FROM security_situations s
         WHERE s.site_id IS NOT NULL AND {_scope(site_ids, 's.site_id', p)}
           AND (s.started_at >= :since OR s.closed_at IS NULL)
         GROUP BY s.site_id
    """), p)).mappings().all():
        at(r["site_id"]).update(situations=r["situations"], high_risk_open=r["high_risk_open"],
                                unattended=r["unattended"])

    p = {"since": since}
    for r in (await db.execute(text(f"""
        SELECT x.site_id, count(*) AS places FROM (
            SELECT s.site_id, COALESCE(s.primary_camera_id::text, s.location_label) AS place
              FROM security_situations s
             WHERE s.site_id IS NOT NULL AND s.started_at >= :since AND {_scope(site_ids, 's.site_id', p)}
               AND COALESCE(s.primary_camera_id::text, s.location_label) IS NOT NULL
             GROUP BY s.site_id, 2 HAVING count(*) >= {REPEATED_AT}) x
         GROUP BY x.site_id
    """), p)).mappings().all():
        at(r["site_id"])["repeated_locations"] = r["places"]

    p = {"since": since}
    for r in (await db.execute(text(f"""
        SELECT c.site_id,
               count(*) FILTER (WHERE i.created_at >= :since) AS incidents,
               count(*) FILTER (WHERE i.status NOT IN ('resolved', 'closed')) AS incidents_open,
               count(*) FILTER (WHERE i.created_at >= :since AND i.sla_breached) AS sla_breached
          FROM incidents i JOIN cameras c ON c.id = i.camera_id
         WHERE c.site_id IS NOT NULL AND {_scope(site_ids, 'c.site_id', p)}
           AND (i.created_at >= :since OR i.status NOT IN ('resolved', 'closed'))
         GROUP BY c.site_id
    """), p)).mappings().all():
        at(r["site_id"]).update(incidents=r["incidents"], incidents_open=r["incidents_open"],
                                sla_breached=r["sla_breached"])

    p = {}
    for r in (await db.execute(text(f"""
        SELECT c.site_id, count(*) AS cameras,
               count(*) FILTER (WHERE (SELECT h.event_type FROM camera_health_events h WHERE h.camera_id = c.id
                                        ORDER BY h.occurred_at DESC, h.id DESC LIMIT 1) = 'stream_disconnected')
                   AS cameras_offline
          FROM cameras c
         WHERE c.is_active AND c.site_id IS NOT NULL AND {_scope(site_ids, 'c.site_id', p)}
         GROUP BY c.site_id
    """), p)).mappings().all():
        at(r["site_id"]).update(cameras=r["cameras"], cameras_offline=r["cameras_offline"])

    p = {"since": since, "now": now}
    for r in (await db.execute(text(f"""
        SELECT v.site_id, count(*) AS virtual_patrols,
               count(*) FILTER (WHERE v.status IN ('COMPLETED', 'PARTIALLY_COMPLETED')) AS completed,
               count(*) FILTER (WHERE v.status IN ('MISSED', 'FAILED')) AS missed
          FROM virtual_patrol_sessions v
         WHERE v.site_id IS NOT NULL AND v.scheduled_for >= :since AND v.scheduled_for <= :now
           AND {_scope(site_ids, 'v.site_id', p)}
         GROUP BY v.site_id
    """), p)).mappings().all():
        at(r["site_id"]).update(virtual_patrols=r["virtual_patrols"], virtual_patrols_completed=r["completed"],
                                patrols_missed=r["missed"])

    p = {"since": since}
    for r in (await db.execute(text(f"""
        SELECT d.site_id, count(*) AS drone_patrols,
               count(*) FILTER (WHERE d.status = 'COMPLETED') AS completed,
               count(*) FILTER (WHERE d.status IN ('MISSED', 'FAILED', 'BLOCKED')) AS missed
          FROM drone_patrol_sessions d
         WHERE d.site_id IS NOT NULL AND d.created_at >= :since AND {_scope(site_ids, 'd.site_id', p)}
         GROUP BY d.site_id
    """), p)).mappings().all():
        site = at(r["site_id"])
        site.update(drone_patrols=r["drone_patrols"], drone_patrols_completed=r["completed"])
        site["patrols_missed"] = int(site.get("patrols_missed") or 0) + int(r["missed"] or 0)
    return out


async def site_scores(db: AsyncSession, allowed: list[str] | None, days: int = DEFAULT_DAYS,
                      now: datetime | None = None) -> list[dict]:
    """Every site the caller may see, each with its score and what the score
    rests on, the lowest first: where to look is the first thing read."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    params: dict = {}
    where = "TRUE" if allowed is None else "s.id = ANY(CAST(:ids AS uuid[]))"
    if allowed is not None:
        params["ids"] = [str(s) for s in allowed]
    sites = (await db.execute(text(f"SELECT s.id, s.name FROM sites s WHERE {where} ORDER BY s.name"),
                              params)).mappings().all()
    counted = await score_counts(db, [s["id"] for s in sites] if allowed is not None else None, since, now)
    tenant_weights = await weights(db)
    out = [{"site_id": s["id"], "site_name": s["name"], **score(counted.get(str(s["id"]), {}), tenant_weights)}
           for s in sites]
    out.sort(key=lambda s: (s["score"], s["site_name"]))
    return out


async def counts_for(db: AsyncSession, site_ids: list | None, since: datetime, now: datetime, zone: str) -> dict:
    """The fuller picture of a period for the given sites (or every site):
    what was reported, how it stood, how people answered, and what repeats."""
    c: dict = {"timezone": zone}
    scored: dict = {}
    for site in (await score_counts(db, site_ids, since, now)).values():
        for key, value in site.items():
            scored[key] = int(scored.get(key) or 0) + int(value or 0)
    c.update(scored)

    p: dict = {"since": since}
    sit = _scope(site_ids, "s.site_id", p)
    row = (await db.execute(text(f"""
        SELECT count(*) AS situations,
               count(*) FILTER (WHERE s.risk_level = 'CRITICAL') AS critical,
               count(*) FILTER (WHERE s.risk_level = 'HIGH') AS high,
               count(*) FILTER (WHERE s.risk_level = 'MEDIUM') AS medium,
               count(*) FILTER (WHERE s.risk_level IN ('LOW', 'INFO')) AS low,
               count(*) FILTER (WHERE s.risk_level IS NULL) AS not_assessed,
               count(*) FILTER (WHERE s.closed_at IS NOT NULL) AS closed,
               count(*) FILTER (WHERE s.decision_status = 'FALSE_POSITIVE') AS false_positive,
               count(*) FILTER (WHERE s.decision_status = 'RESOLVED') AS resolved,
               count(*) FILTER (WHERE s.closed_at IS NULL) AS still_open,
               COALESCE(sum(s.event_count), 0) AS events, COALESCE(sum(s.duplicate_count), 0) AS repeats_folded
          FROM security_situations s WHERE s.started_at >= :since AND {sit}
    """), p)).mappings().one()
    c.update({k: int(v or 0) for k, v in row.items()})
    c["by_risk"] = {"CRITICAL": c.pop("critical"), "HIGH": c.pop("high"), "MEDIUM": c.pop("medium"),
                    "LOW": c.pop("low"), "NOT_ASSESSED": c.pop("not_assessed")}

    p = {"since": since}
    c["by_source"] = {r["source_type"]: int(r["n"]) for r in (await db.execute(text(f"""
        SELECT e.source_type, count(*) AS n FROM security_events e
         WHERE e.occurred_at >= :since AND {_scope(site_ids, 'e.site_id', p)}
         GROUP BY e.source_type ORDER BY n DESC
    """), p)).mappings().all()}

    p = {"since": since, "zone": zone}
    c["by_hour"] = {int(r["hour"]): int(r["n"]) for r in (await db.execute(text(f"""
        SELECT EXTRACT(HOUR FROM s.started_at AT TIME ZONE CAST(:zone AS text))::int AS hour, count(*) AS n
          FROM security_situations s WHERE s.started_at >= :since AND {_scope(site_ids, 's.site_id', p)}
         GROUP BY 1
    """), p)).mappings().all()}

    p = {"since": since}
    c["locations"] = [{"name": r["name"], "camera_id": r["camera_id"], "situations": int(r["n"])}
                      for r in (await db.execute(text(f"""
        SELECT COALESCE(cam.name, s.location_label) AS name, s.primary_camera_id AS camera_id, count(*) AS n
          FROM security_situations s LEFT JOIN cameras cam ON cam.id = s.primary_camera_id
         WHERE s.started_at >= :since AND {_scope(site_ids, 's.site_id', p)}
           AND COALESCE(cam.name, s.location_label) IS NOT NULL
         GROUP BY 1, 2 ORDER BY n DESC, 1 LIMIT 5
    """), p)).mappings().all()]

    # Identities the platform read, in more than one situation. A plate is what
    # was read; a person is a watchlist entry's id, never a name.
    p = {"since": since}
    repeated = (await db.execute(text(f"""
        SELECT e.subject_kind, e.subject_ref, count(DISTINCT l.situation_id) AS n
          FROM security_events e JOIN security_situation_events l ON l.event_id = e.id
         WHERE e.occurred_at >= :since AND e.subject_ref IS NOT NULL AND e.subject_kind IN ('VEHICLE', 'PERSON')
           AND {_scope(site_ids, 'e.site_id', p)}
         GROUP BY e.subject_kind, e.subject_ref HAVING count(DISTINCT l.situation_id) >= 2
         ORDER BY n DESC, e.subject_ref LIMIT 20
    """), p)).mappings().all()
    c["vehicles"] = [{"plate": r["subject_ref"], "situations": int(r["n"])} for r in repeated
                     if r["subject_kind"] == "VEHICLE"][:5]
    c["persons"] = [{"watchlist_entry_id": r["subject_ref"], "situations": int(r["n"])} for r in repeated
                    if r["subject_kind"] == "PERSON"][:5]

    # How people answered: every decision, and how long the first one took.
    p = {"since": since}
    rows = (await db.execute(text(f"""
        SELECT d.basis, EXTRACT(EPOCH FROM (d.decided_at - s.started_at)) AS waited,
               row_number() OVER (PARTITION BY d.situation_id ORDER BY d.decided_at, d.id) AS nth
          FROM security_decisions d JOIN security_situations s ON s.id = d.situation_id
         WHERE s.started_at >= :since AND {_scope(site_ids, 's.site_id', p)}
    """), p)).mappings().all()
    firsts = [float(r["waited"]) for r in rows if r["nth"] == 1 and r["waited"] is not None]
    c["decisions"] = len(rows)
    c["decided"] = len(firsts)
    c["overrides"] = sum(1 for r in rows if r["basis"] == "OVERRIDE")
    c["followed"] = sum(1 for r in rows if r["basis"] == "FOLLOWED")
    c["median_seconds_to_decide"] = round(median(firsts)) if firsts else None

    # How long a dispatched guard took, by the incident's own record.
    p = {"since": since}
    took = [float(r["took"]) for r in (await db.execute(text(f"""
        SELECT EXTRACT(EPOCH FROM (i.guard_arrived_at - i.dispatched_at)) AS took
          FROM incidents i JOIN cameras c ON c.id = i.camera_id
         WHERE i.dispatched_at >= :since AND i.guard_arrived_at IS NOT NULL AND {_scope(site_ids, 'c.site_id', p)}
    """), p)).mappings().all() if r["took"] is not None and r["took"] >= 0]
    c["guard_arrivals"] = len(took)
    c["median_seconds_to_arrive"] = round(median(took)) if took else None

    p = {}
    c["offline_names"] = [r["name"] for r in (await db.execute(text(f"""
        SELECT c.name FROM cameras c
         WHERE c.is_active AND {_scope(site_ids, 'c.site_id', p)}
           AND (SELECT h.event_type FROM camera_health_events h WHERE h.camera_id = c.id
                 ORDER BY h.occurred_at DESC, h.id DESC LIMIT 1) = 'stream_disconnected'
         ORDER BY c.name LIMIT 10
    """), p)).mappings().all()]
    return c


async def insight(db: AsyncSession, site: Mapping | None, allowed: list[str] | None, days: int = DEFAULT_DAYS,
                  now: datetime | None = None) -> dict:
    """The daily intelligence for one site, or for every site the caller may
    see: the counts, the findings, and — for one site — its score."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    site_ids = [site["id"]] if site is not None else (None if allowed is None else list(allowed))
    zone = await zone_for(db, site["id"] if site is not None else None)
    c = await counts_for(db, site_ids, since, now, zone)
    out = {"period": {"days": days, "from": since, "to": now, "timezone": zone},
           "site": {"id": site["id"], "name": site["name"]} if site is not None else None,
           "counts": c, "findings": findings(c, days), "is_advisory": True, "score": None}
    if site is not None:
        out["score"] = score(c, await weights(db))
    return out
