"""Load statutory PWM wage floors from the published MOM/PLRD schedule.

    docker cp scripts/ops/pwm_rates.csv docker-api-1:/tmp/pwm_rates.csv
    docker cp scripts/ops/seed_pwm_rates.py docker-api-1:/tmp/seed_pwm_rates.py
    docker exec -it docker-api-1 python /tmp/seed_pwm_rates.py /tmp/pwm_rates.csv

WHY THIS IS A CSV AND NOT A MIGRATION FULL OF NUMBERS.

The figures are law, they change on a published annual schedule, and nobody
should be reading them out of Python. Putting them in a file you fill in from
the official table means the number in the database can be traced to a document,
which is the whole point of the `source` column: when somebody asks in two years
why a guard was flagged, the answer names a schedule rather than a commit.

It also means an annual increase is a new CSV row and one command, not a code
change, a review and a deploy.

NOTHING IS GUESSED. The script refuses a row with no source, refuses a grade
outside the seven, and refuses a non-positive amount. A wrong floor is worse
than no floor: with no rate on file every guard reads "not assessed", which is
visibly incomplete, whereas a wrong rate reads as a clean bill of health.

Re-running is safe. A row for a grade and effective date that already exists is
updated, so correcting a typo is the same command again.
"""
from __future__ import annotations

import asyncio
import csv
import os
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation

import asyncpg

GRADES = {
    "security_officer",
    "senior_security_officer",
    "security_supervisor",
    "senior_security_supervisor",
    "security_site_supervisor",
    "senior_security_site_supervisor",
    "chief_security_officer",
}

REQUIRED = ("grade", "effective_from", "monthly_basic_floor", "source")


def _rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        # Comment lines are stripped BEFORE the header is read. The template
        # leads with an explanation of where the figures come from, and
        # DictReader would otherwise take the first '#' line as the header and
        # report every column as missing.
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        reader = csv.DictReader(lines)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"CSV is missing column(s): {', '.join(missing)}")
        rows, problems = [], []
        for n, raw in enumerate(reader, start=2):  # line 1 is the header
            grade = (raw["grade"] or "").strip()
            src = (raw["source"] or "").strip()
            if not grade or grade.startswith("#"):
                continue
            if grade not in GRADES:
                problems.append(f"line {n}: unknown grade {grade!r}")
                continue
            if not src:
                problems.append(f"line {n}: no source cited — refusing to load a "
                                f"wage floor nobody can trace")
                continue
            try:
                amount = Decimal((raw["monthly_basic_floor"] or "").strip())
            except (InvalidOperation, TypeError):
                problems.append(f"line {n}: {raw['monthly_basic_floor']!r} is not a number")
                continue
            if amount <= 0:
                problems.append(f"line {n}: floor must be greater than zero")
                continue
            try:
                eff = date.fromisoformat((raw["effective_from"] or "").strip())
            except ValueError:
                problems.append(f"line {n}: effective_from must be YYYY-MM-DD")
                continue
            rows.append({"grade": grade, "effective_from": eff,
                         "monthly_basic_floor": amount, "source": src})
    if problems:
        print("Refusing to load. Fix these first:")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    if not rows:
        raise SystemExit("No usable rows in that CSV — nothing to load.")
    return rows


async def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    rows = _rows(sys.argv[1])

    raw = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(re.sub(r"^postgresql\+\w+://", "postgresql://", raw))
    try:
        async with conn.transaction():
            for r in rows:
                await conn.execute(
                    "INSERT INTO pwm_wage_floors "
                    "  (grade, effective_from, monthly_basic_floor, source) "
                    "VALUES ($1, $2, $3, $4) "
                    "ON CONFLICT (grade, effective_from) DO UPDATE "
                    "  SET monthly_basic_floor = EXCLUDED.monthly_basic_floor, "
                    "      source = EXCLUDED.source",
                    r["grade"], r["effective_from"], r["monthly_basic_floor"],
                    r["source"],
                )
        print(f"Loaded {len(rows)} wage floor(s).")
        for r in rows:
            print(f"  {r['effective_from']}  {r['grade']:<34} "
                  f"{r['monthly_basic_floor']:>10}  ({r['source']})")
        print("\nRun the compliance report to see the effect:")
        print("  GET /api/v1/payroll/pwm-compliance")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
