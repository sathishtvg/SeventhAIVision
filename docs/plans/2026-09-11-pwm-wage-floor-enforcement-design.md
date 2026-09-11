# PWM Wage Floor Enforcement — Design

**Date:** 2026-09-11
**Status:** Design agreed, not implemented
**Scope:** Progressive Wage Model minimum basic wage enforcement for security officers

---

## Why

Singapore's Progressive Wage Model sets a legally mandated minimum basic wage per
security grade. An agency paying below it is in breach and risks PLRD licence action.

The groundwork already exists and does nothing. Migration `0094` added
`users.pwm_grade`, constrained to the seven real PWM grades, and said so plainly:

> `users.pwm_grade` — the Progressive Wage Model sets a minimum basic wage per
> grade; without the grade there is nothing to check pay against

`pwm_grade` is referenced nowhere outside its own migration. It is a stored field
with no behaviour. This design finishes what that migration started.

This is the one payroll gap that can produce an **illegal** wage rather than merely
an inaccurate one. CPF completeness (graduated PR rates, the AW ceiling), SDL, FWL
and IRAS AIS are all real gaps, but they affect contribution accuracy and employer
cost — not whether the basic wage is lawful. Those come later.

## Scope

**In:** the wage floor — a versioned rate table, an effective-floor resolution,
enforcement at the point pay is set, reporting at payroll run, and a day-one
exposure report.

**Out:** auto-top-up; any change to how pay is *calculated*; CPF/SDL/FWL/AIS;
PWM for non-security roles. This feature judges a wage. It never alters one.

---

## Data model

Two tables, because the law and a company's own policy are different things.

```
pwm_wage_floors                      -- platform-owned; no tenant_id, no RLS
  grade                              -- the 7 values in ck_users_pwm_grade
  effective_from       DATE
  monthly_basic_floor  NUMERIC       -- SGD, full-time monthly basic minimum
  source               TEXT          -- which MOM/PLRD schedule this came from
  PRIMARY KEY (grade, effective_from)

tenant_pwm_floors                    -- per-tenant; RLS-protected
  tenant_id
  grade
  effective_from       DATE
  monthly_basic_floor  NUMERIC
  PRIMARY KEY (tenant_id, grade, effective_from)
```

### Why the statutory table is not tenant-scoped

PWM is national law, identical for every customer. Making it shared means:

- a **new tenant is protected on day one with nothing to seed** — no copy at tenant
  creation, no onboarding step that can be skipped
- an annual MOM increase is one `INSERT` every tenant picks up at once, with no
  per-customer drift
- a tenant cannot lower its own legal floor

It has no `tenant_id` and no RLS policy, like `permissions`. **No `SECURITY DEFINER`
function is needed** — worth stating explicitly given how many times cross-tenant
reads have bitten this codebase. It is not tenant data, so there is nothing to scope.

### Why tenants can still set a floor

Many agencies pay above the statutory minimum as policy or under a collective
agreement, and want *that* enforced. A tenant override may only ever be **higher**
than the statutory floor in force, enforced by a database constraint rather than
only in the API — a direct `UPDATE` must not be able to certify an illegal wage as
compliant.

### No `effective_to`

An end date is derivable from the next row's `effective_from`. Storing both invites
them to disagree, and a disagreement between two dates is silent.

---

## Resolving the effective floor

```
effective_floor(grade, period) =
    max( statutory(grade, period), tenant_override(grade, period) )
```

Each resolved as:

```sql
SELECT monthly_basic_floor
  FROM <table>
 WHERE grade = :grade AND effective_from <= :period_start
 ORDER BY effective_from DESC
 LIMIT 1
```

**Always as of the payroll period, never as of today.** This is the detail most
implementations get wrong. Restating March's payroll in September must judge March's
payslips against March's floor — otherwise a lawful March payslip becomes
retroactively non-compliant the moment rates rise in January.

A tenant with no override row falls through to statutory.

---

## Comparison rules

PWM floors are defined as a **monthly basic wage for full-time officers**. Other pay
bases are converted using the conventions already in `services/payroll.py`
(26 working days per month, 8 hours per day) rather than new invented constants.

| Guard | Compared against |
|---|---|
| Full-time, `monthly_salary` | monthly floor, directly |
| Full-time, `daily_rate` | floor ÷ 26 |
| Full-time, `hourly_rate` | floor ÷ 26 ÷ 8 |
| Part-time (any basis) | hourly-equivalent floor |
| `pwm_grade IS NULL` | **not assessed** |

`pwm_grade IS NULL` must render as *unassessed*, never as compliant. Silence reading
as approval is exactly how a compliance feature gives false assurance.

**Open question requiring verification:** the part-time hourly-equivalent derivation
is a legal question, not an arithmetic one. It must be checked against MOM's published
guidance before being relied on — the ÷26÷8 divisor is this codebase's internal
convention, which is not the same as MOM's definition.

---

## Enforcement points

### At setup — blocking

Refusing a below-floor wage at the moment it is entered is cheap to correct and is
the right place to be strict.

Two triggers, not one:

1. a pay field changes (`monthly_salary`, `daily_rate`, `hourly_rate`)
2. **`pwm_grade` changes**

The second is the one that gets missed. Promoting a guard from Security Officer to
Supervisor can make an *unchanged* wage non-compliant, because the floor moved rather
than the pay. A check that fires only on rate changes lets every promotion through.

Both paths call one service function, so "compliant" has a single definition rather
than two that drift apart.

### At payroll run — reporting only

Every payslip carries its assessment. The run lists below-floor payslips as
exceptions, each showing the floor it was judged against. **The run still finalises.**

This follows the module's own stated philosophy, from the `MAX_OT_HOURS_PER_MONTH`
comment:

> payroll's job is to say what happened, and refusing to pay hours somebody has
> already worked would be the wrong correction

Payroll day is also the worst possible moment to discover a data problem. A blocked
run means guards are not paid on time, which is its own violation.

---

## Surfacing

1. **Guard profile** — a badge beside pay: *Compliant / Below floor / Not assessed*,
   naming the floor applied and its effective date. The save blocks; the badge explains.
2. **Payroll run** — below-floor payslips as exceptions.
3. **Compliance report** — every guard, grade, current pay, effective floor, verdict.
   This is the day-one exposure view, and the artefact an agency shows PLRD.

The compliance report exists because blocking new saves will not find guards who are
**already** below floor when this ships. Without it, the feature protects future
mistakes and hides present ones.

---

## Seeding

The migration creates the tables and the resolution logic. **The figures come from the
published MOM/PLRD schedule**, recorded in the `source` column.

Wage floors will not be typed from memory into a compliance feature. A wrong floor is
worse than no floor: it gives false assurance while still underpaying. The same applies
to any PWM overtime-hours requirement — sourced, not assumed.

---

## Tests

- a floor rise does **not** retroactively fail last month's payroll (period resolution)
- promotion with unchanged pay **is** caught (the `pwm_grade` trigger)
- a tenant override below statutory is rejected by the **database**, not only the API
- `pwm_grade IS NULL` reads as unassessed, never compliant
- a correctly-paid part-timer is not flagged
- a payroll run containing a below-floor payslip still finalises, and still reports it

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Direction | Statutory payroll before operational roadmap | The only gap that can produce an illegal wage |
| Round scope | PWM floor only | Finishable and verifiable; rate versioning is the real work |
| Enforcement | Block at setup, report at run | Matches the module's existing philosophy |
| Pay basis | Full-time converted; part-time on hourly equivalent | Widest correct coverage |
| Ownership | Statutory baseline + optional higher tenant floor | New tenants safe on day one; agencies above minimum get their real policy enforced |
