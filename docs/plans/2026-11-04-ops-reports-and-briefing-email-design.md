# Phase 4 of the known limits: the daily briefing and the operations reports, emailed

Date: 4 November 2026. Agreed with the owner in three parts; the programme is in
`2026-11-03-known-limits-programme-and-sg-payroll-design.md`.

The known limits it closes (module 26): *no report is delivered on a schedule or as a PDF*, and *publishing tells
nobody*.

## Owner decisions

- **Both:** the daily briefing emailed when it is published, and any of the nine operations reports on a daily,
  weekly or monthly schedule.
- **Own staff only:** people of the organisation, chosen by name or by role; each gets only what they could read on
  screen. There is no field for an outside address.
- **Format:** the briefing in the email's text with a PDF; each report as CSV (as downloaded) and PDF.
- **Who sets them up:** Admin and Manager, through the existing `report:schedule`.

## 1. The briefing

- Lists of people and roles: one for a site's briefings, and one for every briefing (`briefing_recipient_lists`).
- Publishing queues an email, in the same transaction, for each person on the lists who is in use, holds
  `briefing:read`, is shown the briefing's site (a briefing for every site: only those held to no sites) and has an
  email address. Whoever is not is recorded as left out, with why.
- The email: the briefing as published (sections less those left out, the reviewer's note, who published it) in its
  text, and as a PDF. A later revision is sent again, said to be one.
- The scheduler sends what is queued within about five minutes; a failure is tried again with a growing gap, then
  stays FAILED with why (Virtual Patrolling's backoff and error wording). Each briefing shows, person by person, what
  became of its email. Each email sent is audited (`briefing.email`).

## 2. The reports

- A schedule (`ops_report_schedules`): one of the nine reports, a site or every site, daily / weekly on a day /
  monthly on a day 1-28, at an hour where the organisation is, for the day, week or month just ended, to people and
  roles. Paused, changed, or taken away (kept).
- At its time each person named gets the report made for them as they could take it out: `opsreport:export` and the
  permission its records are read under, their own sites only. Whoever may not is left out with why.
- The email says the period, the rows, and when the report was cut at 10,000; the CSV is the download's, the PDF one
  layout for all nine (a table, or a block for each row of a wide report), the first 500 rows.
- A time missed while the platform was down sends once, for the period that was due. Audited as `report.email`, with
  which report, for whom, how many rows.
- The existing scheduled reports (site summary, occurrence book, incidents) are not changed.

## 3. Testing and what changes

Tests: a briefing's emails queued, left out with why, sent with its text and PDF, audited, failed and tried again, a
revision said; a schedule set only by `report:schedule`, each person's own rows, CSV and PDF, a missed time once, a
paused schedule nothing; the timing where the organisation is; two organisations.

Migration 0178: `briefing_recipient_lists`, `ops_report_schedules`, `ops_emails` (each email, with what became of
it; the document is made when it is sent and never stored).

Existing files changed (owner approved): `routers/daily_briefings.py` (one call when a briefing is published),
`scheduler_main.py` (one more pass), `app/main.py` (the router), the operations board page and the briefing dialogs,
their API file, `SECURITY_ANALYTICS_ARCHITECTURE.md` and its checks. New: `services/ops_delivery.py`,
`services/ops_documents.py`, `routers/ops_delivery.py`, tests. Not touched: the nine reports' own code, the existing
scheduled reports, the phone.
