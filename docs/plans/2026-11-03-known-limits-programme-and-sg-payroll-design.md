# Closing the known limits, and Phase 1: Singapore payroll (CPF and PWM)

Agreed with the owner on 3 November 2026. The feature reference (edition 23) lists, under *Known limits*, eight things
the platform does not do. The owner asked for all eight to be built.

## The programme

One programme of eight phases, in the order the owner chose ("compliance first"). Each phase is designed briefly with
the owner, built, tested and merged before the next starts.

| Phase | What | Design |
|---|---|---|
| 1 | Singapore payroll: CPF rates by date, PR graduated rates, the Additional Wage ceiling, the official PWM floors | This document |
| 2 | Retention periods for modules 17 to 28 and for evidence packages | To come |
| 3 | Cases: tell a person put on a case; escalate a case | To come |
| 4 | Operations reports and the daily briefing, scheduled and emailed | To come |
| 5 | Drone video privacy-masked | To come |
| 6 | Device health: frame rate, latency and recording gaps measured | To come |
| 7 | Evidence: video watermarked, packages digitally signed | To come |
| 8 | Tracking: a person followed across cameras; guards tracked continuously | To come |

## Phase 1: what was wrong

`services/payroll.py` worked out CPF from five age bands written into the code, capped at an Ordinary Wage ceiling of
$7,400. Checked against CPF Board's published tables on 3 November 2026:

- The OW ceiling has been **$8,000 since 1 January 2026** ($7,400 in 2025).
- The rates for **above 55 to 60** and **above 60 to 65** were older than 2025's. In 2026 they are 34% (employer 16%,
  employee 18%) and 25% (12.5% and 12.5%).
- There were **no wage bands**: up to $50 nothing; above $50 to $500 the employer only; above $500 to $750 the
  employee's share phased in.
- **CPF's rounding** was not followed: the total is rounded to the nearest dollar, the employee's share is rounded down
  to the dollar, and the employer's share is the difference.
- **First- and second-year PRs** (graduated rates) and the **Additional Wage ceiling** were not implemented at all, and
  **no PWM floor** was on file, so every guard was "not assessed".

## 1. CPF worked out as CPF Board does

**The rates are data.** A table of CPF's rate tables by the date they take effect (`cpf_rate_tables`), loaded from
CPF's published PDFs with the document named as the source:

- `CPFcontributionratesfrom1Jan2026.pdf` (from 1 January 2026) and `jan2025_contributionandallocationrates.pdf`
  (1 January to 31 December 2025);
- five tables in each: Citizens and PRs from the 3rd year (also PRs in years 1-2 on full rates after a joint
  application); PR year 1 and year 2 on Graduated rates (G/G); PR year 1 and year 2 on Full employer and Graduated
  employee rates (F/G);
- per table and age band, the employer's rate and the employee's rate.

Every published row follows one rule, which the tests hold against CPF's own figures:

| Total wages for the month (TW) | Total contribution | Employee's share |
|---|---|---|
| $50 or less | nil | nil |
| above $50 to $500 | employer rate x TW | nil |
| above $500 to $750 | employer rate x TW + 3 x employee rate x (TW - $500) | 3 x employee rate x (TW - $500) |
| above $750 | (employer + employee rate) x OW (OW capped at the ceiling) + the same rate x AW | employee rate x OW + employee rate x AW |

CPF's "Max. of $X" figures are the total and employee rates times the OW ceiling, and the tests check them.

**The month decides.** A payroll period uses the table in force for its month. A new age band applies from the first
day of the month after the 55th, 60th, 65th or 70th birthday. The graduated tables have four age bands (above 65 is
one); the others have five.

**2027.** CPF has announced higher totals for above 55 to 60 (35.5%) and above 60 to 65 (26%) from 1 January 2027, but
not yet the employer/employee split. The 2027 table is added when CPF publishes it, not before. A payroll month with
no table on file is refused with that reason, never worked out on an older table.

**Rounding:** the total to the nearest dollar (50 cents and above up), the employee's share down to the dollar, the
employer's share the difference. With OW and AW in the same month, both are worked out and summed before rounding.

**What changes for existing payroll.** `compute_cpf` and `CPF_RATE_TABLE` are replaced. A draft run is worked out again
when it is recalculated. A finalised run, its payslips and the IR8A figures made from it are never changed. Each run
records which rate table it used.

## 2. PR staff and Additional Wages

**PR staff.** Two fields on the employee: `pr_since` (date) and `cpf_rate_type`: `graduated` (the default), `fg`
(full employer, graduated employee) or `full` (full rates). The last two apply only after CPF Board approves a joint
application, and the form says so. Year 1 is the first 12 months from `pr_since`, year 2 the next 12, full rates from
the 3rd year, worked out for each payroll month.

A PR with no `pr_since`, or a citizen or PR with no date of birth, is listed in the run as "CPF not worked out" with
the reason. CPF is never guessed (until now a missing date of birth meant age 0).

**Additional Wages.** Lines on a draft payroll run, per employee: `bonus`, `aws` (13th month), `leave_encashment`,
`incentive`, `other`, with an amount and a note (`payroll_additional_wages`). The run is recalculated after a change.

- The year's AW ceiling = **$102,000 - the employee's Ordinary Wages subject to CPF this calendar year**: finalised
  runs so far, this month, and the remaining months at this month's capped OW.
- CPF on AW is paid only up to what remains of the ceiling.
- The payslip shows OW, AW, the AW subject to CPF, and the ceiling used.
- A **year-end ceiling check** lists each employee's year against the ceiling and marks any over- or under-
  contribution, for example after a pay change late in the year. It reports and changes nothing.
- IR8A includes the bonus and the CPF on it.
- Adding, changing and removing a line is audited. No line can be changed once the run is finalised.

## 3. The official PWM floors

MOM's published schedule for the security sector, 2024 to 2028, loaded with the MOM page as the source. As built:
the outsourced full-time floors go into the existing `pwm_wage_floors` (the table was made for them; a row already
there is left as it is), the other three schedules into a new table beside it (`pwm_wage_schedules`); the companies'
own floors are untouched:

| Arrangement | Hours | Judged on | 2026: SO / SSO / SS / SSS |
|---|---|---|---|
| Outsourced | full-time (35-44 h a week) | monthly basic wage | $3,090 / $3,390 / $3,690 / $3,990 |
| Outsourced | part-time | hourly basic wage | $11.80 / $12.95 / $14.10 / $15.25 |
| In-house | full-time | monthly gross wage (basic + allowances + incentives, no overtime) | $2,475 / $2,675 / $2,905 / left to the market |
| In-house | part-time | the full-time gross pro-rated on a 44-hour week, as MOM says | |

One new employee field, `employment_arrangement`: `outsourced` (default) or `in_house`. In a payroll run each guard
is judged against the floor for their arrangement, hours and grade in that month; in-house guards on the run's gross
wage without overtime. A grade with no MOM floor (Chief Security Officer, the older site-supervisor grades, an in-house
Senior Security Supervisor) stays **not assessed**, never compliant. The PWM report says which schedule each guard was
judged against. A company's own higher floor still applies to outsourced full-time basic wage.

## 4. Creating an employee, the screens, and testing

**Creating an employee (web)** asks **Residency** first:

- **Singaporean**: date of birth and monthly salary are required (CPF needs them);
- **PR**: the same, and `pr_since` and the CPF rate type;
- **Foreigner**: work pass type (EP, S Pass, Work Permit) and expiry; "No CPF for foreign employees".

It uses the existing `work_pass_type` (`citizen`, `pr`, `ep`, `sp`, `wp`). As the salary and date of birth are entered
the form shows the employee's and the employer's CPF, as percentages and dollars, the total, the take-home before other
deductions, and the table it used. The figures come from the server, from the same function payroll uses, so the form
and the payslip cannot disagree. The same preview is on the edit screen. A scanned IC fills in the residency: an S or T
number is a citizen or PR, F, G or M a foreigner. (As built: the IC scan belongs to the AI assistant, not the Users
page; it tells the assistant what the number says, and the assistant asks citizen or PR, as the card's colour is not
read.)

**Payroll screens:** additional-pay lines on a draft run and **Recalculate**; payslips with OW, AW and the ceiling; the
year-end ceiling check; the PWM report with arrangement and part-time.

**Storage:** migration 0175. New tables `cpf_rate_tables`, `cpf_wage_ceilings`, `pwm_wage_schedules`,
`payroll_additional_wages`; new employee columns `pr_since`, `cpf_rate_type`, `employment_arrangement`; new payslip
columns for the OW, the AW, what CPF was paid on, the ceiling left and the table used; the official outsourced floors
added to `pwm_wage_floors`. Nothing removed.

**Existing files changed (the owner agreed to correct the CPF calculation):** `services/payroll.py`,
`routers/payroll.py` (runs, payslips, IR8A, the PWM check), `services/pwm.py`, `routers/users.py`, the web Users page
and the payroll page; as built also `services/ai_ic.py` and `routers/ai_assistant.py` (what the IC number says),
and `tests/test_pwm_wage_floors.py`, whose fixtures used grades and dates that now hold MOM's official floors.

**Tests:** every published row of both years' tables, at each band edge ($50, $500, $750, the OW ceiling) and each
age band, including the month of a birthday; CPF's "Max." figures; a payroll run for a citizen, a 1st-year PR, a
58-year-old, a part-timer and an in-house guard; the AW ceiling across two runs; finalised runs unchanged; the form's
preview equal to the payslip.

## Sources

- CPF Board, *CPF Contribution Rate Table from 1 January 2026* (`CPFcontributionratesfrom1Jan2026.pdf`), and
  *from 1 January to 31 December 2025* (`jan2025_contributionandallocationrates.pdf`), cpf.gov.sg.
- CPF Board, *What is the Additional Wage (AW) ceiling?*, cpf.gov.sg: $102,000 - total OW subject to CPF for the year.
- MOM, *Progressive Wage Model for the security sector*, mom.gov.sg/employment-practices/progressive-wage-model/
  security-sector (2024-2028 schedules).
