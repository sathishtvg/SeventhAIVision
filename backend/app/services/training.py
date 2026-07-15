"""Shared training-completion helpers (ShiftSecure Phase 6).

compute_expiry is used by both the pre-existing manual "log completion"
path (training.py::create_record) and the new auto-graded quiz-submission
path, so a course's validity_months always computes an identical
expires_at regardless of which path wrote the record.
"""
from __future__ import annotations

import calendar
from datetime import date


def compute_expiry(completed_date: date, validity_months: int | None) -> date | None:
    """None if the course has no expiry. Clamps day-of-month overflow the
    same way Postgres's `date + INTERVAL 'N months'` does (e.g. Jan 31 +
    1 month -> Feb 28/29), extracted from create_record's original inline
    SQL-based computation into a pure function."""
    if not validity_months:
        return None
    month_index = completed_date.month - 1 + validity_months
    year = completed_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(completed_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
