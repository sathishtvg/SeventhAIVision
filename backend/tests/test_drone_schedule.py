"""When a drone mission flies — the pure schedule logic, pinned to known instants.

No database and no clock: every function takes the moment it is asked about, so
each case below states exactly when "now" is and what must come next.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

from app.services import drone_schedule as ds


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _s(schedule_type: str, *, tz: str = "Asia/Singapore", start: date = date(2026, 9, 1),
       at: time = time(23, 0), end: date | None = None, weekdays=(), dates=()) -> ds.Schedule:
    return ds.Schedule(schedule_type=schedule_type, timezone=tz, start_date=start,
                       launch_time=at, end_date=end, weekdays=tuple(weekdays),
                       specific_dates=tuple(dates))


# ─── Time zones ──────────────────────────────────────────────────────────────

def test_23_00_in_singapore_is_15_00_utc():
    nxt = ds.next_occurrences(_s("DAILY"), _utc(2026, 9, 25, 10, 0), 1)
    assert nxt == [_utc(2026, 9, 25, 15, 0)]


def test_a_launch_at_exactly_now_is_not_next():
    """Strictly after: the scheduler asking at 15:00:00 must not be told 15:00:00
    is still to come, or it would launch the same flight twice."""
    nxt = ds.next_occurrences(_s("DAILY"), _utc(2026, 9, 25, 15, 0), 1)
    assert nxt == [_utc(2026, 9, 26, 15, 0)]


def test_a_launch_later_on_a_local_day_that_is_behind_utc():
    """01:30 UTC on the 26th is still 21:30 on the 25th in New York; tonight's
    22:00 launch is 30 minutes away, not tomorrow's."""
    s = _s("DAILY", tz="America/New_York", at=time(22, 0))
    nxt = ds.next_occurrences(s, _utc(2026, 9, 26, 1, 30), 1)
    assert nxt == [_utc(2026, 9, 26, 2, 0)]


def test_local_launch_time_holds_across_daylight_saving():
    """07:00 in New York is 11:00 UTC before 1 Nov 2026 and 12:00 after. A
    scheduler on the server clock would drift an hour; this must not."""
    s = _s("DAILY", tz="America/New_York", at=time(7, 0), start=date(2026, 10, 30))
    runs = ds.next_occurrences(s, _utc(2026, 10, 30, 0, 0), 4)
    assert [r.hour for r in runs] == [11, 11, 12, 12]
    assert all(r.astimezone(ds.ZoneInfo("America/New_York")).hour == 7 for r in runs)


# ─── Schedule types ──────────────────────────────────────────────────────────

def test_once_runs_once():
    s = _s("ONCE", start=date(2026, 10, 1))
    assert ds.next_occurrences(s, _utc(2026, 9, 25), 5) == [_utc(2026, 10, 1, 15, 0)]
    assert ds.next_occurrences(s, _utc(2026, 10, 2), 5) == []


def test_weekly_runs_on_the_start_dates_weekday():
    s = _s("WEEKLY", start=date(2026, 9, 28))          # a Monday
    runs = ds.next_occurrences(s, _utc(2026, 9, 25), 3)
    assert [r.astimezone(ds.ZoneInfo("Asia/Singapore")).weekday() for r in runs] == [0, 0, 0]
    assert runs[0] == _utc(2026, 9, 28, 15, 0)


def test_selected_days_runs_only_on_those_days():
    s = _s("SELECTED_DAYS", weekdays=(0, 2, 4))       # Mon, Wed, Fri
    runs = ds.next_occurrences(s, _utc(2026, 9, 27), 6)  # a Sunday
    days = [r.astimezone(ds.ZoneInfo("Asia/Singapore")).weekday() for r in runs]
    assert days == [0, 2, 4, 0, 2, 4]


def test_specific_dates_run_on_those_dates_and_then_stop():
    s = _s("SPECIFIC_DATE", dates=(date(2026, 10, 5), date(2026, 12, 24)))
    runs = ds.next_occurrences(s, _utc(2026, 9, 25), 5)
    assert runs == [_utc(2026, 10, 5, 15, 0), _utc(2026, 12, 24, 15, 0)]


def test_the_end_date_is_the_last_day():
    s = _s("DAILY", end=date(2026, 9, 27))
    runs = ds.next_occurrences(s, _utc(2026, 9, 25), 10)
    assert runs == [_utc(2026, 9, 25, 15, 0), _utc(2026, 9, 26, 15, 0), _utc(2026, 9, 27, 15, 0)]


def test_nothing_runs_before_the_start_date():
    s = _s("DAILY", start=date(2026, 11, 1))
    assert ds.next_occurrences(s, _utc(2026, 9, 25), 1) == [_utc(2026, 11, 1, 15, 0)]


# ─── What a scheduler tick owes ──────────────────────────────────────────────

def test_due_between_is_half_open_so_ticks_never_double_count():
    s = _s("DAILY")
    launch = _utc(2026, 9, 25, 15, 0)
    assert ds.due_between(s, _utc(2026, 9, 25, 14, 59), launch) == [launch]
    assert ds.due_between(s, launch, _utc(2026, 9, 25, 15, 1)) == []


def test_due_between_catches_every_launch_in_a_long_gap():
    """A scheduler that was down for three days owes three launches — which ones
    are still worth flying is the caller's lookback rule, not this function's."""
    got = ds.due_between(_s("DAILY"), _utc(2026, 9, 24, 16, 0), _utc(2026, 9, 27, 16, 0))
    assert got == [_utc(2026, 9, 25, 15, 0), _utc(2026, 9, 26, 15, 0), _utc(2026, 9, 27, 15, 0)]


# ─── Validation ──────────────────────────────────────────────────────────────

def test_a_valid_schedule_has_no_problems():
    assert ds.problems(_s("DAILY")) == []


def test_every_problem_is_reported_at_once():
    s = _s("SELECTED_DAYS", tz="Mars/Olympus_Mons", start=date(2026, 9, 10), end=date(2026, 9, 1))
    found = " ".join(ds.problems(s))
    assert "Unknown timezone" in found
    assert "end date is before the start date" in found
    assert "at least one day" in found


def test_weekdays_are_zero_to_six():
    assert ds.problems(_s("SELECTED_DAYS", weekdays=(7,)))


def test_specific_dates_outside_the_range_are_called_out():
    s = _s("SPECIFIC_DATE", start=date(2026, 10, 1), end=date(2026, 10, 31),
           dates=(date(2026, 9, 15), date(2026, 11, 2)))
    found = " ".join(ds.problems(s))
    assert "before the start date" in found and "after the end date" in found


def test_an_invalid_schedule_never_produces_runs():
    assert ds.next_occurrences(_s("SELECTED_DAYS"), _utc(2026, 9, 25), 5) == []
