"""How long each kind of record is kept: one reading of every period in force.

Before this, the periods were each where the job that applies them reads them:
the organisation's settings, a site's recording policy, the installation's
configuration, a constant in a module. Nothing said them together, and nothing
said of the records no job removes that none does.

THIS READS; IT SETS NOTHING. A period is changed where it has always been
changed. What is here is what the jobs read, read the same way: each value
comes from the job's own function or its own constant, so that this cannot say
one number while a job applies another.

WHERE A PERIOD COMES FROM is said with it, because they are not equally sure:

    SITE_POLICY     a site's recording policy               certain
    TENANT_SETTING  the organisation's own setting          certain
    FIXED           a constant in the code                  certain
    INSTALLATION    the installation's configuration, as THIS service reads it

The last is the fallback when the organisation has set nothing. The job that
applies it runs in a service of its own; if the installation gives that
service a different value, the job's is the one in force. The statement says
so beside every period that falls back.

NO PERIOD IS A STATEMENT TOO. Most of what the platform keeps is removed by no
job: it stays until a person removes it on its own screen, a data-subject
erasure is carried out, or the organisation is removed. Of what the enterprise
expansion added, four kinds may be given a period by the organisation
(services/record_retention.py) and are kept until one is set; the rest has no
period and no way to set one. Each of its tables is listed with whether it
names a person.

IT SAYS WHAT IS CONFIGURED, NOT WHAT THE LAW REQUIRES. How long a recording or
a visitor's record ought to be kept is the organisation's to decide and is
written nowhere in the code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.dependencies.sites import is_site_allowed
from app.services import evidence_hold, record_retention
from app.services.subject_records import names_people

#: Where a period was read from.
SOURCES = ("SITE_POLICY", "TENANT_SETTING", "INSTALLATION", "FIXED")
SOURCE_WORDS = {
    "SITE_POLICY": "This site's recording policy",
    "TENANT_SETTING": "The organisation's setting",
    "INSTALLATION": "The installation's default, as this service reads it",
    "FIXED": "Fixed in the code",
}
#: Said beside every period that falls back to the installation's default.
INSTALLATION_NOTE = ("The job that applies this period runs in a service of its own. If the installation gives that "
                     "service a different default, the job's is the one in force.")
NOT_LAW = ("This says what is configured, not what the law requires. How long a record ought to be kept is the "
           "organisation's to decide.")
EVERYTHING_ELSE = ("Every other record is removed by no job. It is kept until a person removes it on its own screen, "
                   "a data-subject erasure is carried out, or the organisation is removed.")


@dataclass(frozen=True)
class Period:
    """A kind of record that a job removes once it is old enough."""
    key: str
    label: str
    tables: tuple[str, ...]
    counted_from: str
    removed_by: str
    how: str
    #: The kinds of hold that stop it, or None when no hold applies to it.
    hold: tuple[str, ...] | None
    #: What is kept past its period whatever its age, besides what is under a hold.
    kept_past: str | None
    #: The organisation's setting that sets it, or None when it has none.
    setting_key: str | None
    names_people: bool


PERIODS: tuple[Period, ...] = (
    Period("EVIDENCE", "Pictures and clips kept as evidence", ("evidence",),
           "when it was captured", "The scheduler, once a day", "The file is deleted, then its record",
           evidence_hold.FRAMES_AND_CLIPS, None, "evidence.retention_days", True),
    Period("RECORDINGS", "Continuous recordings", ("recordings",),
           "when the recording began", "The recording supervisor, about once an hour",
           "The file is deleted, then its record. Only a recording that is finished is removed",
           evidence_hold.RECORDINGS, None, "recording.retention_days", True),
    Period("DRONE_FOOTAGE", "Drone footage", ("drone_event_media",),
           "when it was captured", "The drone runner, every few hours", "The file is deleted, then its record",
           evidence_hold.DRONE_MEDIA,
           "Footage of an event that became an incident, of an event that was confirmed and is still open, and "
           "of a flight still in progress.", "evidence.retention_days", True),
    Period("DRONE_TRACKS", "Drone flight tracks", ("drone_telemetry",),
           "when the sample was recorded", "The drone runner, every few hours",
           "The second-by-second track is deleted. The flight's own record stays", None, None,
           "drone.telemetry_retention_days", False),
    Period("DRONE_RECEIPTS", "Receipts of what a site gateway sent", ("drone_sync_receipts",),
           "when it was received", "The drone runner, as it works", "The receipt is deleted", None, None, None, False),
    Period("AUDIT", "The audit log", ("audit_logs",),
           "when the entry was written", "The scheduler, once a day",
           "Moved out of the log into a file, a month at a time, once every entry of that month is old enough. "
           "Not deleted", None, None, None, True),
)

#: What a job deletes that is not a matter of age: it is replaced as it is worked out again.
RECOMPUTED = {"drone_event_cameras": "Which cameras saw a drone event, replaced each time it is worked out"}


@dataclass(frozen=True)
class Kept:
    """Records no job removes."""
    key: str
    label: str
    tables: tuple[str, ...]
    #: Tables whose rows a person's own step may take away, with what the step is.
    taken_away: dict[str, str] | None = None


#: What the enterprise expansion added (migrations 0143 to 0155) that has no period and no way to set one.
#: The four kinds that may be given one are record_retention.KINDS, and are not repeated here.
#: Whether a table names a person is read from the columns that refer to one (services/subject_records.py).
KEPT: tuple[Kept, ...] = (
    Kept("EVIDENCE_PACKAGES", "Evidence packages, their holds and their custody",
         ("evidence_packages", "evidence_package_items", "evidence_holds", "evidence_custody_events"),
         {"evidence_package_items": "An item is taken out of a package that has not been sealed"}),
    Kept("SITE_MAP", "The places of a site", ("site_places",)),
    Kept("RESPONSE", "Guards sent to incidents, their steps, the escalation policies and what was escalated",
         ("incident_responses", "incident_response_steps", "escalation_policies", "incident_escalations")),
    Kept("OCCURRENCE_BOOK", "Reviews and corrections of occurrence book entries, standing instructions and who read "
         "them, and shift summaries",
         ("occurrence_entry_reviews", "occurrence_entry_corrections", "site_instructions", "site_instruction_reads",
          "shift_handover_summaries")),
    Kept("SOP", "Procedures, their versions and passages", ("sop_documents", "sop_versions", "sop_passages",
                                                             "sop_incident_types"),
         {"sop_incident_types": "The kinds of incident a procedure is for are replaced when they are set again"}),
    Kept("ASSETS", "The asset register, what devices reported of their health, maintenance schedules and work orders",
         ("asset_register", "device_health_changes", "maintenance_schedules", "maintenance_work_orders")),
    Kept("ADVICE_ANSWERS", "Answers to risk advice", ("risk_advice_answers",)),
    Kept("BRIEFINGS", "Daily briefings", ("daily_briefings",)),
)

#: What else takes a row away from one of the kinds that may be given a period, besides the period.
ALSO_TAKEN = {
    "VISITOR_AUTHORIZATIONS": {
        "visitor_authorization_places": "The places a visit is for are replaced when they are set again",
        "visitor_authorizations": "An authorisation goes with its visitor when a data-subject erasure removes "
                                  "the visitor",
    },
}
MAY_BE_SET = ("These are kept until the organisation sets a period for them. Once one is set, what is over and older "
              "than it is removed for good, a day at a time.")

#: What a data-subject erasure, carried out by a person, removes or blanks. It is the existing step and is unchanged.
ERASURE = ("A face on the watchlist, and the match kept on what the cameras saw of it",
           "A number plate on the watchlist; the plate is blanked on what the cameras read of it",
           "A visitor. Their visits stay, without the visitor; the authorisations of their visits go with them",
           "Named pictures and clips")


def _days(value, default: int, least: int = 1) -> tuple[int, bool]:
    """(the days in force, whether the organisation's setting gave them)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        return default, False
    return value, True


async def read(db: AsyncSession, allowed: list[str] | None) -> dict:
    """The statement for the caller's organisation, as the jobs would read it now."""
    # Imported here: each drags in the job it belongs to, which only this reading needs.
    from app.services import continuous_recording, drone_retention, drone_runner

    rows = (await db.execute(text("""
        SELECT setting_key, setting_value, updated_at FROM tenant_settings
         WHERE setting_key = ANY(CAST(:keys AS text[]))
    """), {"keys": sorted({p.setting_key for p in PERIODS if p.setting_key})})).mappings().all()
    said = {r["setting_key"]: r for r in rows}

    def setting(key: str, default: int) -> dict:
        row = said.get(key)
        days, own = _days(row["setting_value"] if row else None, default)
        return {"amount": days, "unit": "days", "source": "TENANT_SETTING" if own else "INSTALLATION",
                "set_at": row["updated_at"] if own else None}

    evidence = setting("evidence.retention_days", settings.EVIDENCE_RETENTION_DAYS)
    recording = setting("recording.retention_days", continuous_recording.DEFAULT_RETENTION_DAYS)
    tracks = setting("drone.telemetry_retention_days", drone_retention.DEFAULT_TELEMETRY_DAYS)
    in_force = {
        "EVIDENCE": evidence, "RECORDINGS": recording, "DRONE_FOOTAGE": dict(evidence), "DRONE_TRACKS": tracks,
        "DRONE_RECEIPTS": {"amount": drone_runner.RECEIPT_RETENTION.days, "unit": "days", "source": "FIXED",
                           "set_at": None},
        "AUDIT": {"amount": settings.AUDIT_RETENTION_YEARS, "unit": "years", "source": "INSTALLATION",
                  "set_at": None},
    }

    holds = {r["kind"]: r["held"] for r in (await db.execute(text("""
        SELECT kind, count(*) AS held FROM evidence_holds
         WHERE released_at IS NULL AND (CAST(:every AS boolean) OR site_id = ANY(CAST(:sites AS uuid[])))
         GROUP BY kind
    """), {"every": allowed is None, "sites": list(allowed or [])})).mappings()}

    periods = []
    for p in PERIODS:
        period = in_force[p.key]
        periods.append({
            "key": p.key, "label": p.label, "tables": list(p.tables), "names_people": p.names_people,
            "period": {**period, "source_words": SOURCE_WORDS[period["source"]],
                       "note": INSTALLATION_NOTE if period["source"] == "INSTALLATION" else None},
            "setting_key": p.setting_key, "counted_from": p.counted_from, "removed_by": p.removed_by, "how": p.how,
            "hold_stops_it": p.hold is not None,
            "held_now": sum(holds.get(kind, 0) for kind in p.hold) if p.hold else None,
            "kept_past": p.kept_past,
            "per_organisation": p.key != "AUDIT",
        })

    # A site's own period for its recordings, where its policy sets one.
    site_rows = (await db.execute(text("""
        SELECT s.id, s.name, s.is_active, p.central_retention_days, p.local_retention_days, p.updated_at
          FROM sites s LEFT JOIN recording_policies p ON p.site_id = s.id AND p.is_active = TRUE
         ORDER BY s.name, s.id
    """))).mappings().all()
    sites = []
    for s in site_rows:
        if not is_site_allowed(allowed, s["id"]):
            continue
        own = s["central_retention_days"] is not None
        sites.append({
            "site_id": str(s["id"]), "site_name": s["name"], "in_use": s["is_active"],
            "recording_days": s["central_retention_days"] if own else recording["amount"],
            "source": "SITE_POLICY" if own else recording["source"],
            "source_words": SOURCE_WORDS["SITE_POLICY" if own else recording["source"]],
            # Nothing is kept centrally for a site whose policy says none is.
            "keeps_none": own and s["central_retention_days"] == 0,
            "at_the_site_days": s["local_retention_days"],
            "set_at": s["updated_at"] if own else None,
        })

    # The four kinds the organisation may give a period: each kept until one is set.
    chosen = await record_retention.periods(db)
    optional = [{
        "key": k.key, "label": k.label, "setting_key": k.setting_key,
        "tables": [{"name": t, "names_people": names_people(t)} for t in (k.table, *k.parts)],
        "days": chosen[k.key]["days"], "set_at": chosen[k.key]["set_at"],
        "counted_from": k.counted_from, "removes": k.removes, "kept_whatever": k.kept_whatever,
        "removed_by": record_retention.REMOVED_BY, "taken_away": ALSO_TAKEN.get(k.key, {}),
    } for k in record_retention.KINDS]

    return {
        "read_at": datetime.now(timezone.utc),
        "periods": periods,
        "sites": sites,
        "optional": optional,
        "optional_note": MAY_BE_SET,
        "least_days": record_retention.LEAST_DAYS,
        "holds": {"in_force": sum(holds.values()), "by_kind": holds,
                  "words": "A hold keeps one picture, clip, recording or piece of drone footage past its period "
                           "until a person releases it, with a reason."},
        "kept": [{"key": k.key, "label": k.label,
                  "tables": [{"name": t, "names_people": names_people(t)} for t in k.tables],
                  "taken_away": k.taken_away or {}} for k in KEPT],
        "everything_else": EVERYTHING_ELSE,
        "erasure": list(ERASURE),
        "not_law": NOT_LAW,
        "sources": dict(SOURCE_WORDS),
    }
