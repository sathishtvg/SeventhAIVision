"""The auto-scheduler's pure decision rules.

These had no coverage at all, which is how `min_guards_per_shift` sat unused
for so long: the number existed, the UI never set it, and nothing asserted
that the scheduler placed anyone against it.

Only the pure functions are exercised here — no database, no event loop. The
assignment loop itself needs a tenant, patterns, guards and leave blocks, and
belongs in an integration test; what is tested here is every rule that decides
*how many* posts exist and *which* guard ranks highest for one, which is where
the logic actually lives.
"""
from __future__ import annotations

from datetime import date, time

from app.services.roster_autoschedule import (
    infer_shift_type,
    is_guard_on_leave,
    posts_for_group,
    score_candidate,
)


# ── A. Shift type inference ─────────────────────────────────────────────────

def test_ras_day_starts_at_five():
    """05:00 is the first day start; 04:59 is still night."""
    assert infer_shift_type(time(5, 0)) == "day"
    assert infer_shift_type(time(4, 59)) == "night"


def test_ras_night_starts_at_seventeen():
    """16:59 is the last day start; 17:00 is night."""
    assert infer_shift_type(time(16, 59)) == "day"
    assert infer_shift_type(time(17, 0)) == "night"


def test_ras_typical_singapore_shifts():
    """The two shifts nearly every site in the estate actually runs."""
    assert infer_shift_type(time(8, 0)) == "day"
    assert infer_shift_type(time(20, 0)) == "night"


# ── B. How many posts a shift type has ──────────────────────────────────────

def _slot(name: str) -> dict:
    return {"pattern": name}


def test_ras_posts_match_required_not_pattern_count():
    """Three officers due across one pattern is three posts on that pattern.

    The bug this replaces: the scheduler placed exactly one guard per pattern
    and raised a coverage warning for the rest, so a site needing three got
    one and a complaint.
    """
    posts = posts_for_group([_slot("08:00")], 3)
    assert len(posts) == 3
    assert all(p["pattern"] == "08:00" for p in posts)


def test_ras_posts_deal_round_robin_across_patterns():
    """Three due over a morning and an afternoon pattern: 2 and 1, not 3 and 0."""
    posts = posts_for_group([_slot("08:00"), _slot("14:00")], 3)
    assert [p["pattern"] for p in posts] == ["08:00", "14:00", "08:00"]


def test_ras_more_patterns_than_required_leaves_the_rest_empty():
    """A site cut back to one officer staffs its first pattern only."""
    posts = posts_for_group([_slot("08:00"), _slot("14:00"), _slot("16:00")], 1)
    assert [p["pattern"] for p in posts] == ["08:00"]


def test_ras_zero_required_schedules_nothing():
    """A site that runs no night shift generates no night shifts.

    Zero is a real configuration, not a missing value, which is why the column
    allows it and the check constraint only forbids negatives.
    """
    assert posts_for_group([_slot("20:00")], 0) == []


def test_ras_no_patterns_schedules_nothing():
    """Strength without a pattern to hang it on cannot produce a shift."""
    assert posts_for_group([], 4) == []


# ── C. Ranking within a post ────────────────────────────────────────────────

def _history(day_count: int = 0, night_count: int = 0, last_assigned: int | None = None) -> dict:
    return {
        "day_count": day_count,
        "night_count": night_count,
        "last_assigned_day_index": last_assigned,
        "last_shift_end": None,
        "consecutive_days": 0,
        "last_day_index_worked": None,
    }


def test_ras_preferred_shift_outranks_no_preference():
    """Rule 4 — the guard who asked for nights wins a night post.

    This is the rule the guard-creation page now writes to, so it is the one
    that has to hold for that field to mean anything.
    """
    history = {"wants_night": _history(), "no_preference": _history()}
    prefs = {"wants_night": {"preferred_shift_type": "night"}}

    wants = score_candidate("wants_night", "night", prefs, history, day_index=0)
    neutral = score_candidate("no_preference", "night", prefs, history, day_index=0)
    assert wants > neutral


def test_ras_preference_does_not_apply_to_the_other_shift():
    """A night preference is no advantage when filling a day post."""
    history = {"wants_night": _history(), "no_preference": _history()}
    prefs = {"wants_night": {"preferred_shift_type": "night"}}

    wants = score_candidate("wants_night", "day", prefs, history, day_index=0)
    neutral = score_candidate("no_preference", "day", prefs, history, day_index=0)
    assert wants == neutral


def test_ras_day_night_balance_favours_the_skewed_guard():
    """Rule 3 — someone who has worked only days ranks higher for a night."""
    history = {"all_days": _history(day_count=6), "already_nights": _history(night_count=6)}

    day_heavy = score_candidate("all_days", "night", {}, history, day_index=0)
    night_heavy = score_candidate("already_nights", "night", {}, history, day_index=0)
    assert day_heavy > night_heavy


def test_ras_off_day_stagger_favours_the_longest_rested():
    """Rule 8 — the guard idle longest in this batch ranks higher."""
    history = {"rested": _history(last_assigned=0), "worked_yesterday": _history(last_assigned=4)}

    rested = score_candidate("rested", "day", {}, history, day_index=5)
    recent = score_candidate("worked_yesterday", "day", {}, history, day_index=5)
    assert rested > recent


# ── D. Leave ────────────────────────────────────────────────────────────────

def test_ras_leave_block_covers_its_boundaries():
    """Rule 5 — leave is inclusive of both end dates."""
    blocks = [{
        "guard_user_id": "g1",
        "start_date": date(2026, 9, 10),
        "end_date": date(2026, 9, 12),
    }]
    assert is_guard_on_leave("g1", date(2026, 9, 10), blocks) is True
    assert is_guard_on_leave("g1", date(2026, 9, 12), blocks) is True
    assert is_guard_on_leave("g1", date(2026, 9, 13), blocks) is False


def test_ras_leave_is_per_guard():
    """One guard's leave does not excuse another."""
    blocks = [{
        "guard_user_id": "g1",
        "start_date": date(2026, 9, 10),
        "end_date": date(2026, 9, 12),
    }]
    assert is_guard_on_leave("g2", date(2026, 9, 11), blocks) is False
