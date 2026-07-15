"""0071 — Daily-rate ("day basis") pay support

Adds a third pay-rate option for guards paid a flat rate per day/shift
worked rather than hourly or monthly (common for part-time/relief
security guards). Mirrors how hourly_rate/monthly_salary were added
directly to `users` in migration 0067, rather than a side table — a
handful of scalar fields, same precedent.

payslips.days_worked records the distinct calendar dates with a completed
shift in the payroll period. It is computed and stored for EVERY guard
(not just daily-rate ones) — it's a genuinely meaningful attendance
metric regardless of pay basis, so the payslip PDF/UI show it
unconditionally rather than only for daily_rate guards.

Precedence in services/payroll.py::compute_payslip is unchanged in kind,
extended in degree: monthly_salary wins, then daily_rate * days_worked,
then hourly_rate * regular_hours. Overtime pay remains gated purely on
hourly_rate being set (unchanged) — a daily-rate-only guard's overtime
hours are tracked but unpaid, exactly mirroring the existing
monthly_salary-only behavior. This is a deliberate v1 simplification: a
per-day worker has no natural "hourly equivalent" to multiply an OT
premium against without inventing a shift-length baseline, so day-rate
OT is intentionally not implemented, not an oversight.
"""
from __future__ import annotations

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN daily_rate NUMERIC(8,2)")
    op.execute("ALTER TABLE payslips ADD COLUMN days_worked NUMERIC(5,2) NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE payslips DROP COLUMN IF EXISTS days_worked")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS daily_rate")
