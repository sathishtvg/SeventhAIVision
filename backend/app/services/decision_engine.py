"""Vehicle access decision engine.

Turns "camera read plate ABC1234 at the north gate" into one of five verdicts.
This is the policy layer that sits between ANPR and the barrier — the thing
that decides whether a gate opens by itself, waits for an operator, or stays
shut and screams.

WHY THIS IS SEPARATE FROM THE LPR WORKER
    ai-worker's lpr_task already classifies plates coarsely (allow/block) to
    raise alerts, and that stays exactly as it is. But an alert is not an
    access decision: a plate can be perfectly legitimate and still not
    warrant an automatic gate opening (an expired contractor pass, a VIP
    read at 43% confidence, a visitor with no booking today). Keeping the
    policy here — in the API, against the full registry row — means the
    nuance lives in one auditable place, and the AI worker's hot path stays
    a simple classifier that needs no change.

PRECEDENCE IS ORDERED AND EXHAUSTIVE
    The rules below are evaluated top to bottom and the FIRST match wins.
    Order matters and is deliberate — a blacklisted plate is blocked even if
    its validity window has expired, and a low-confidence read never
    auto-opens no matter who the vehicle belongs to. Every branch is covered
    by a test; see tests/test_decision_engine.py.

The core is a pure function (`decide`) taking plain data, so every rule is
testable without a database, a camera, or a gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class Decision(str, Enum):
    """Verdicts, from most to least permissive."""

    AUTO_OPEN = "auto_open"            # open the barrier now, no human involved
    AUTO_ALLOW = "auto_allow"          # permitted, but this lane has no barrier to open
    REQUIRE_OPERATOR = "require_operator"  # hold; a human decides
    BLOCK = "block"                    # refuse entry, log it
    ALARM = "alarm"                    # refuse entry AND raise a critical alert


# Categories whose vehicles may open a gate unattended, assuming everything
# else checks out. Deliberately excludes 'watchlist' (the whole point of a
# watchlist is that a human looks), 'unknown', and 'blacklist'.
AUTO_OPEN_CATEGORIES = frozenset(
    {"whitelist", "vip", "staff", "emergency", "government"}
)

# Categories that are legitimate but booking-dependent: allowed only when a
# valid visit/engagement backs them up on the day.
BOOKING_CATEGORIES = frozenset({"visitor", "contractor"})

BLOCKED_CATEGORIES = frozenset({"blacklist"})

# Fallback defaults, overridden per tenant via tenant_settings. Same
# env-default/tenant-override convention used everywhere else in this codebase.
DEFAULTS: dict[str, Any] = {
    # An ANPR read below this is treated as unreliable — hold for an operator
    # rather than acting on a possible misread. Distinct from (and normally
    # higher than) lpr.confidence_threshold, which only governs whether the
    # detection is recorded at all: acting on a plate is a higher bar than
    # logging it.
    "access.auto_open_min_confidence": 0.85,
    # What to do with a plate nobody has registered. 'require_operator' is the
    # safe default for a manned command centre; a dark site with no operator
    # on shift may prefer 'block'.
    "access.unknown_plate_action": Decision.REQUIRE_OPERATOR.value,
    # Whether an expired registration is a hard refusal or an operator prompt.
    # Defaults to prompting, because the overwhelmingly common cause is an
    # admin who hasn't renewed a pass yet, not an intruder.
    "access.expired_action": Decision.REQUIRE_OPERATOR.value,
}


@dataclass(frozen=True)
class VehicleFacts:
    """Everything the policy needs, already fetched. Plain data so the rules
    are testable in isolation."""

    plate_number: str
    category: str | None = None          # None = plate not in the registry
    is_active: bool = True
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: float | None = None      # ANPR read confidence, 0..1
    has_valid_booking: bool = False       # visitor/contractor backed by a visit today
    owner_name: str | None = None
    company: str | None = None


@dataclass(frozen=True)
class DecisionOutcome:
    decision: Decision
    reason: str
    """Stable machine key for the rule that fired — logged on every
    barrier_commands row and shown to the operator, so 'why did that gate
    open' is always answerable after the fact."""
    rule: str

    @property
    def should_open_barrier(self) -> bool:
        return self.decision is Decision.AUTO_OPEN

    @property
    def is_refusal(self) -> bool:
        return self.decision in (Decision.BLOCK, Decision.ALARM)


def decide(
    facts: VehicleFacts,
    *,
    today: date,
    settings: dict[str, Any] | None = None,
    barrier_available: bool = True,
) -> DecisionOutcome:
    """Pure policy. First matching rule wins; see the ordering note above.

    `barrier_available` distinguishes AUTO_OPEN from AUTO_ALLOW: the same
    permitted vehicle at a lane with no barrier bound is simply allowed and
    logged, not an actuation.
    """
    cfg = {**DEFAULTS, **(settings or {})}
    permit = Decision.AUTO_OPEN if barrier_available else Decision.AUTO_ALLOW

    # 1. Blacklist. Unconditional and first — an explicitly barred vehicle is
    #    refused regardless of validity dates, confidence or anything else,
    #    and it is the one case that also raises an alarm.
    if facts.category in BLOCKED_CATEGORIES:
        return DecisionOutcome(
            Decision.ALARM,
            f"{facts.plate_number} is blacklisted"
            + (f" ({facts.company})" if facts.company else ""),
            "blacklisted",
        )

    # 2. Not in the registry at all.
    if facts.category is None:
        action = Decision(cfg["access.unknown_plate_action"])
        return DecisionOutcome(
            action, f"{facts.plate_number} is not in the vehicle registry", "unknown_plate"
        )

    # 3. Registered but deactivated. Treated as unknown rather than blocked —
    #    deactivation is an administrative state, not an accusation.
    if not facts.is_active:
        return DecisionOutcome(
            Decision.REQUIRE_OPERATOR,
            f"{facts.plate_number} registration is deactivated",
            "inactive_registration",
        )

    # 4. Outside its validity window. Before the confidence check, because an
    #    expired pass is a fact about the vehicle, not about the read quality
    #    — reporting "expired" is more actionable to an operator than
    #    "low confidence".
    #
    #    Not-yet-valid and expired are reported distinctly: they look identical
    #    to the code but mean opposite things to the operator standing at the
    #    gate ("come back on Monday" vs. "your pass lapsed, see admin"). An
    #    earlier revision collapsed both into "expired (valid to n/a)", which
    #    was actively misleading for a pass starting next month.
    if facts.valid_from and today < facts.valid_from:
        return DecisionOutcome(
            Decision(cfg["access.expired_action"]),
            f"{facts.plate_number} registration is not valid until "
            f"{facts.valid_from.isoformat()}",
            "not_yet_valid",
        )
    if facts.valid_to and today > facts.valid_to:
        return DecisionOutcome(
            Decision(cfg["access.expired_action"]),
            f"{facts.plate_number} registration expired on {facts.valid_to.isoformat()}",
            "expired_registration",
        )

    # 5. Watchlist. Legitimate but flagged for attention — the entire purpose
    #    is that a human looks at it, so it can never auto-open.
    if facts.category == "watchlist":
        return DecisionOutcome(
            Decision.REQUIRE_OPERATOR,
            f"{facts.plate_number} is on the watchlist — operator review required",
            "watchlist_hit",
        )

    # 6. Read confidence. Applies to every remaining (permitted) category: a
    #    misread plate that happens to collide with a VIP entry must not open
    #    a gate on its own.
    threshold = float(cfg["access.auto_open_min_confidence"])
    if facts.confidence is not None and facts.confidence < threshold:
        return DecisionOutcome(
            Decision.REQUIRE_OPERATOR,
            f"plate read confidence {facts.confidence:.0%} is below the "
            f"{threshold:.0%} auto-open threshold",
            "low_confidence",
        )

    # 7. Booking-dependent categories.
    if facts.category in BOOKING_CATEGORIES:
        if facts.has_valid_booking:
            return DecisionOutcome(
                permit,
                f"{facts.plate_number} has a valid {facts.category} booking today",
                "booking_confirmed",
            )
        return DecisionOutcome(
            Decision.REQUIRE_OPERATOR,
            f"{facts.plate_number} is a registered {facts.category} with no booking today",
            "booking_missing",
        )

    # 8. Standing permission.
    if facts.category in AUTO_OPEN_CATEGORIES:
        who = facts.owner_name or facts.company or facts.plate_number
        return DecisionOutcome(
            permit, f"{who} — {facts.category} vehicle, registration valid", "standing_permission"
        )

    # 9. Fallthrough. A category exists in the schema but no rule claimed it,
    #    which means this function is out of date with the enum. Fail safe
    #    (a human decides) and say so plainly rather than guessing.
    return DecisionOutcome(
        Decision.REQUIRE_OPERATOR,
        f"no access rule is defined for category '{facts.category}'",
        "unhandled_category",
    )


# ── Database-facing wrapper ───────────────────────────────────────────────

async def load_vehicle_facts(
    db: AsyncSession, plate_number: str, *, confidence: float | None = None
) -> VehicleFacts:
    """Fetch the registry row for a plate, plus whether a booking backs it.

    Ordered so an explicit blacklisting always wins if a plate somehow carries
    more than one active registration — the same defensive ordering the LPR
    worker's own lookup uses.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT category, is_active, valid_from, valid_to, owner_name, company
                FROM watchlist_entries
                WHERE plate_number = :plate AND is_active = TRUE
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY (category = 'blacklist') DESC
                LIMIT 1
                """
            ),
            {"plate": plate_number},
        )
    ).mappings().first()

    if row is None:
        return VehicleFacts(plate_number=plate_number, category=None, confidence=confidence)

    has_booking = False
    if row["category"] in BOOKING_CATEGORIES:
        # Column names and the "still an open booking" predicate mirror
        # routers/visitors.py exactly (it uses the same NOT IN ('departed',
        # 'cancelled') test); the partial index idx_visitors_plate on
        # (tenant_id, vehicle_plate) already covers this lookup.
        has_booking = bool(
            (
                await db.execute(
                    text(
                        """
                        SELECT 1 FROM visitors
                        WHERE vehicle_plate = :plate
                          AND is_active = TRUE
                          AND status NOT IN ('departed', 'cancelled')
                          AND (expected_from  IS NULL OR expected_from::date  <= CURRENT_DATE)
                          AND (expected_until IS NULL OR expected_until::date >= CURRENT_DATE)
                        LIMIT 1
                        """
                    ),
                    {"plate": plate_number},
                )
            ).first()
        )

    return VehicleFacts(
        plate_number=plate_number,
        category=row["category"],
        is_active=row["is_active"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        confidence=confidence,
        has_valid_booking=has_booking,
        owner_name=row["owner_name"],
        company=row["company"],
    )


async def load_settings(db: AsyncSession) -> dict[str, Any]:
    """Tenant overrides for the access.* keys, falling back to DEFAULTS.
    Same inline-query + Python-default pattern used by every other
    tenant-tunable value in this codebase."""
    rows = (
        await db.execute(
            text(
                "SELECT setting_key, setting_value FROM tenant_settings "
                "WHERE setting_key LIKE 'access.%'"
            )
        )
    ).mappings().all()
    return {r["setting_key"]: r["setting_value"] for r in rows}


async def evaluate_vehicle(
    db: AsyncSession,
    plate_number: str,
    *,
    confidence: float | None = None,
    barrier_available: bool = True,
    today: date | None = None,
) -> tuple[DecisionOutcome, VehicleFacts]:
    """Full evaluation against the database. Returns the verdict and the facts
    it was based on, so the caller can log both — an access decision that
    can't be explained afterwards is not auditable."""
    facts = await load_vehicle_facts(db, plate_number, confidence=confidence)
    settings = await load_settings(db)
    outcome = decide(
        facts,
        today=today or date.today(),
        settings=settings,
        barrier_available=barrier_available,
    )
    return outcome, facts
