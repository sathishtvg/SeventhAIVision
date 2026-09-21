"""Whether the guard on a shift holds what that shift requires.

THIS REPORTS, IT DOES NOT BLOCK. Deliberate: an ops manager covering a 2am
no-show must not be stopped by the roster tool, because a roster tool that
stands between them and a staffed site is a roster tool they will work around --
and then nothing is recorded at all. So a shift is always created, and the
compliance answer is produced alongside it.

WHICH MEANS THE SWEEP IS THE MECHANISM, NOT A SAFETY NET. Shifts are created
from seven different places (three in the roster router, one in shifts, one in
the roster service, plus two reassignment paths). Warning at each call site
would mean seven places to remember, and the eighth would be written next month
by someone who did not know. The sweep evaluates shifts as they stand, so it
sees every one however it got there.

COMPLIANCE IS ANSWERED AS OF THE SHIFT'S DATE, NOT TODAY. A licence valid this
afternoon may have expired by the night shift three weeks out. Asking "is this
guard certified?" gives the wrong answer for every future shift; the question is
"will this guard be certified when they actually stand at that post?"

NOT ASSESSED IS A DISTINCT ANSWER FROM COMPLIANT. A site with no requirements
recorded has not been checked -- it has not passed. Collapsing the two would let
an agency that never configured anything show a clean board, which is the exact
failure this module exists to prevent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: One finding per (shift, requirement). Ordered worst-first so a report or an
#: alert leads with the thing that actually matters.
MISSING = "MISSING"          # no such certification on file at all
EXPIRED = "EXPIRED"          # held, but not valid on the day of the shift
REVOKED = "REVOKED"          # on file and marked invalid
EXPIRING = "EXPIRING"        # valid on the day, but inside the warning window
COMPLIANT = "COMPLIANT"
NOT_ASSESSED = "NOT_ASSESSED"

SEVERITY = {MISSING: 0, REVOKED: 1, EXPIRED: 2, EXPIRING: 3,
            COMPLIANT: 4, NOT_ASSESSED: 5}

#: How far ahead the sweep looks for shifts at risk. Long enough that a licence
#: renewal (weeks, in practice) can still be started in time; short enough that
#: the board is not filled with shifts nobody has rostered properly yet.
DEFAULT_HORIZON_DAYS = 45


@dataclass(frozen=True)
class Held:
    """One certification a guard has on file."""
    certification_type: str
    expires_at: date | None
    is_valid: bool = True


@dataclass(frozen=True)
class Requirement:
    certification_type: str
    warn_days_before: int = 30
    #: None for a tenant baseline; a site id when it comes from a contract.
    site_id: str | None = None


def assess_one(requirement: Requirement, held: list[Held], *, on: date) -> str:
    """Status of a single requirement for a guard, as of the shift's date.

    `held` is every certification the guard has on file, not a pre-filtered
    list: matching happens here so the caller cannot accidentally filter with
    different rules than the ones this function documents.
    """
    matches = [h for h in held
               if h.certification_type == requirement.certification_type]
    if not matches:
        return MISSING

    # A guard may hold several of the same type -- a renewal sits alongside the
    # certificate it replaces. The best one decides, otherwise a superseded
    # record would make a properly renewed guard look lapsed.
    best = None
    for h in matches:
        status = _status_of(h, requirement, on=on)
        if best is None or SEVERITY[status] > SEVERITY[best]:
            best = status
    return best or MISSING


def _status_of(h: Held, requirement: Requirement, *, on: date) -> str:
    if not h.is_valid:
        return REVOKED
    if h.expires_at is None:
        # No expiry recorded means it does not lapse (a one-off qualification).
        # Treating a blank as expired would flag every first-aid certificate
        # somebody entered without a date.
        return COMPLIANT
    if h.expires_at < on:
        return EXPIRED
    if h.expires_at <= on + timedelta(days=requirement.warn_days_before):
        return EXPIRING
    return COMPLIANT


def assess_shift(requirements: list[Requirement], held: list[Held], *,
                 on: date) -> dict:
    """Every requirement for one shift, worst first.

    Returns NOT_ASSESSED when there is nothing to check -- a site with no
    requirements recorded has not passed, it has not been examined, and a report
    that shows those as green is worse than one that shows them as unknown.
    """
    if not requirements:
        return {"status": NOT_ASSESSED, "findings": []}

    findings = [
        {"certification_type": r.certification_type,
         "site_id": r.site_id,
         "status": assess_one(r, held, on=on)}
        for r in requirements
    ]
    findings.sort(key=lambda f: SEVERITY[f["status"]])
    return {"status": findings[0]["status"], "findings": findings}


def is_actionable(status: str) -> bool:
    """Worth telling somebody about. COMPLIANT and NOT_ASSESSED are not."""
    return status in (MISSING, REVOKED, EXPIRED, EXPIRING)


# ── Reading the pieces out of the database ───────────────────────────────────

async def requirements_for_site(db: AsyncSession, *, site_id: str | None
                                ) -> list[Requirement]:
    """The tenant baseline, plus anything this particular site adds.

    The tenant scope comes from RLS, so this is only ever the caller's own
    requirements.
    """
    rows = (await db.execute(text("""
        SELECT certification_type, warn_days_before, site_id
          FROM certification_requirements
         WHERE is_active
           AND (site_id IS NULL OR site_id = CAST(:site AS uuid))
         ORDER BY site_id NULLS FIRST, certification_type
    """), {"site": site_id})).mappings().all()

    # A site row for the same type overrides the baseline rather than adding a
    # second finding for it -- being told twice that one licence is missing is
    # noise, and the site's own warning window is the more specific intent.
    by_type: dict[str, Requirement] = {}
    for r in rows:
        by_type[r["certification_type"]] = Requirement(
            certification_type=r["certification_type"],
            warn_days_before=r["warn_days_before"],
            site_id=str(r["site_id"]) if r["site_id"] else None,
        )
    return list(by_type.values())


async def held_by(db: AsyncSession, *, user_id: str) -> list[Held]:
    rows = (await db.execute(text("""
        SELECT certification_type, expires_at, is_valid
          FROM guard_certifications
         WHERE user_id = CAST(:u AS uuid)
    """), {"u": user_id})).mappings().all()
    return [Held(certification_type=r["certification_type"],
                 expires_at=r["expires_at"], is_valid=r["is_valid"])
            for r in rows]


async def assess_shift_by_id(db: AsyncSession, shift_id: str) -> dict:
    """The compliance answer for one existing shift, for an API response."""
    shift = (await db.execute(text("""
        SELECT s.id, s.site_id, s.guard_user_id, s.scheduled_start,
               u.full_name AS guard_name, si.name AS site_name
          FROM shifts s
          LEFT JOIN users u ON u.id = s.guard_user_id
          LEFT JOIN sites si ON si.id = s.site_id
         WHERE s.id = CAST(:id AS uuid)
    """), {"id": shift_id})).mappings().first()
    if shift is None:
        raise ValueError("Shift not found")

    reqs = await requirements_for_site(db, site_id=str(shift["site_id"])
                                       if shift["site_id"] else None)
    held = await held_by(db, user_id=str(shift["guard_user_id"]))
    result = assess_shift(reqs, held, on=shift["scheduled_start"].date())
    result.update({"shift_id": str(shift["id"]),
                   "guard_name": shift["guard_name"],
                   "site_name": shift["site_name"],
                   "scheduled_start": shift["scheduled_start"]})
    return result


# ── The daily sweep ──────────────────────────────────────────────────────────

async def sweep_upcoming_shifts(db: AsyncSession, *, today: date | None = None,
                                horizon_days: int = DEFAULT_HORIZON_DAYS) -> dict:
    """Re-evaluate every upcoming shift and record what is wrong with it.

    THIS IS THE MECHANISM, not a backstop. Shifts are created from seven places
    and reassigned from two more; warning at each would mean nine places to
    remember and a tenth written next month by somebody who did not know this
    existed. Evaluating shifts as they stand sees every one however it got
    there.

    Reads per tenant with the GUC set AND tenant_id in the WHERE clause. The
    policy alone is not enough: under any connection that bypasses RLS nothing
    would filter and this loop would process every tenant's shifts once per
    tenant.
    """
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)

    tenants = (await db.execute(text(
        "SELECT id FROM tenants WHERE is_active"))).scalars().all()

    raised, resolved, assessed = 0, 0, 0

    for tenant_id in tenants:
        try:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                             {"t": str(tenant_id)})

            # Requirements first: a tenant with none configured has nothing to
            # check, and there is no point reading its roster at all.
            req_rows = (await db.execute(text("""
                SELECT certification_type, warn_days_before, site_id
                  FROM certification_requirements
                 WHERE tenant_id = CAST(:t AS uuid) AND is_active
            """), {"t": str(tenant_id)})).mappings().all()
            if not req_rows:
                await db.rollback()
                continue

            baseline = {r["certification_type"]: Requirement(
                            r["certification_type"], r["warn_days_before"], None)
                        for r in req_rows if r["site_id"] is None}
            per_site: dict[str, dict[str, Requirement]] = {}
            for r in req_rows:
                if r["site_id"] is not None:
                    per_site.setdefault(str(r["site_id"]), {})[r["certification_type"]] = \
                        Requirement(r["certification_type"], r["warn_days_before"],
                                    str(r["site_id"]))

            shifts = (await db.execute(text("""
                SELECT s.id, s.guard_user_id, s.site_id,
                       (s.scheduled_start AT TIME ZONE 'UTC')::date AS shift_date
                  FROM shifts s
                 WHERE s.tenant_id = CAST(:t AS uuid)
                   AND s.status IN ('scheduled', 'active')
                   AND (s.scheduled_start AT TIME ZONE 'UTC')::date
                       BETWEEN :a AND :b
            """), {"t": str(tenant_id), "a": today, "b": horizon})).mappings().all()
            if not shifts:
                await db.rollback()
                continue

            # Every certification held by the guards on those shifts, in one
            # read. Querying per shift would be thousands of round trips for a
            # month of roster, which is how a nightly job becomes a job nobody
            # runs.
            guard_ids = sorted({str(s["guard_user_id"]) for s in shifts})
            cert_rows = (await db.execute(text("""
                SELECT user_id, certification_type, expires_at, is_valid
                  FROM guard_certifications
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND user_id = ANY(CAST(:ids AS uuid[]))
            """), {"t": str(tenant_id), "ids": guard_ids})).mappings().all()

            held_by_guard: dict[str, list[Held]] = {}
            for c in cert_rows:
                held_by_guard.setdefault(str(c["user_id"]), []).append(
                    Held(c["certification_type"], c["expires_at"], c["is_valid"]))

            current: set[tuple[str, str]] = set()
            for s in shifts:
                site = str(s["site_id"]) if s["site_id"] else None
                reqs = dict(baseline)
                # A site's own row overrides the baseline for that type: its
                # warning window is the more specific intent, and being told
                # twice about one licence is noise.
                reqs.update(per_site.get(site, {}))
                if not reqs:
                    continue

                held = held_by_guard.get(str(s["guard_user_id"]), [])
                result = assess_shift(list(reqs.values()), held, on=s["shift_date"])
                assessed += 1

                for f in result["findings"]:
                    if not is_actionable(f["status"]):
                        continue
                    current.add((str(s["id"]), f["certification_type"]))
                    await db.execute(text("""
                        INSERT INTO shift_certification_findings
                            (tenant_id, shift_id, guard_user_id, site_id,
                             certification_type, status, shift_date)
                        VALUES (CAST(:t AS uuid), CAST(:sh AS uuid),
                                CAST(:g AS uuid), CAST(:si AS uuid), :ct, :st, :d)
                        ON CONFLICT (shift_id, certification_type) DO UPDATE
                            SET status = EXCLUDED.status,
                                shift_date = EXCLUDED.shift_date,
                                -- Reopen: the problem is present again, and a
                                -- row left resolved would hide it.
                                resolved_at = NULL
                    """), {"t": str(tenant_id), "sh": str(s["id"]),
                           "g": str(s["guard_user_id"]), "si": site,
                           "ct": f["certification_type"], "st": f["status"],
                           "d": s["shift_date"]})
                    raised += 1

            # Anything open that this pass did NOT re-raise has been fixed --
            # a renewed licence, a reassigned shift. Closing them matters as
            # much as raising them: a board that only ever grows is a board
            # people stop reading.
            still_open = (await db.execute(text("""
                SELECT id, shift_id, certification_type
                  FROM shift_certification_findings
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND resolved_at IS NULL
                   AND shift_date BETWEEN :a AND :b
            """), {"t": str(tenant_id), "a": today, "b": horizon})).mappings().all()

            gone = [r["id"] for r in still_open
                    if (str(r["shift_id"]), r["certification_type"]) not in current]
            if gone:
                await db.execute(text("""
                    UPDATE shift_certification_findings
                       SET resolved_at = now()
                     WHERE id = ANY(CAST(:ids AS uuid[]))
                """), {"ids": [str(g) for g in gone]})
                resolved += len(gone)

            await db.commit()
        except Exception:
            await db.rollback()
            # One tenant's bad data must not stop the sweep for everybody else.
            logger.exception("certification sweep failed for tenant %s", tenant_id)

    return {"assessed": assessed, "raised": raised, "resolved": resolved}
