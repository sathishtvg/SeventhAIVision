# Operations Board, Daily Briefing and Reports — Architecture

**Phase 10 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-08, migrations `0152` and `0153`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase each part of the operation had its figures on its own
screen: alerts on the dashboard and in analytics, guards on shift in the
command centre, patrols in their own reports, devices in device health,
visitors at the gate, work orders in maintenance. Nothing read them together,
nothing read them for a customer's sites, and nothing put a day into words for
a person to send round.

```
 what the platform already keeps ──► THE BOARD, counted when somebody asks
 incidents · response · patrols ·      for a site, a customer's sites or every site;
 guards · devices · visitors ·         each section under its own permission;
 maintenance                           nothing stored, nothing scored
                │
                ▼
 one day's counts ──► a DRAFT: fixed sentences, each with a count in it
                          │  a person leaves sections out and adds a note
                          ▼
                     PUBLISHED ── and from then not changed;
                                  a correction is a new revision

 the records themselves ──► A REPORT: a CSV file, made when it is asked for,
                            under the records' own permission and the one to
                            take a report out; audited; stored nowhere
```

---

## 1. The rules

1. **A figure is a count of what is recorded, and says what it counts.** A
   figure ending `_now` is how things stand at the moment of asking; every
   other figure is what fell inside the period. The screen marks the first
   kind "now".
2. **A time is the middle one of those measured** — a median — with how many
   it was measured from beside it. When there were none it is nothing, not
   zero. A customer's time is never the sum or the mean of its sites': it is
   counted for those sites, or it is not given.
3. **Each section is read under its own existing permission.** Holding
   `board:read` shows the board; it does not show the incidents of somebody
   who may not read incidents. What the caller may not read is left out and
   named, with the permission it wants.
4. **Nothing is scored, and the board and a briefing name nobody.** There is
   no index, grade or rating of a site, a customer or a person; every figure
   is a number of records.
5. **Somebody held to particular sites sees those sites.** What has no site —
   an incident with no camera, a recorder — is counted only for somebody who
   is not.
6. **The board stores nothing and writes nothing.** It is counted when it is
   asked for.
7. **The platform drafts; a person publishes.** A draft is read only by
   whoever manages briefings. Nothing publishes one by itself, and publishing
   tells nobody: a published briefing is there to be read.
8. **No model writes a briefing** (owner decision E2). Each line is a fixed
   sentence with a count in it, and every section carries the figures its
   lines were made of. The same figures give the same lines.
9. **The counted lines are not edited.** A reviewer leaves a whole section
   out — and the briefing says which — and writes what they have to say in a
   note that is shown as theirs.
10. **A line says when it is true of.** `PERIOD` is what fell inside the day.
    `DRAFTING` is how things stood when the briefing was counted, because the
    platform does not keep how they stood at an earlier moment and does not
    pretend to. `WEEKS` is the advice of the weeks before, which is not a
    forecast.
11. **A published briefing is not changed.** A trigger refuses it. A
    correction is a new revision for the same day, and the earlier one stays,
    marked as replaced.
12. **Each act on a briefing is a signed-in person's** — not an API key, not a
    support session — and is audited.
13. **Super Admin, a guard and the client role hold none of the new
    permissions.**
14. **A report is the records as they are.** It is a list, not a judgement,
    made when it is asked for and stored nowhere.
15. **Taking records out is its own permission, held on top of the one they
    are read under.** `opsreport:export` alone opens no report, and a support
    session takes none out.
16. **A file is written to be opened safely and read rightly.** Text a
    spreadsheet would run as a formula is made plain text; a time says its
    time zone; a file that was cut says so.
17. **Every report taken out is audited**: who, which, for where, and how many
    records.

---

## 2. The board

Seven sections, each from rows the platform already keeps
(`services/ops_board.py`):

| Section | Read under | Its figures |
|---|---|---|
| `INCIDENTS` | `incident:read` | `opened`, `by_severity`, `resolved`, `opened_still_open`, `open_now` |
| `RESPONSE` | `response:read` | `opened`, `acknowledged`, `acknowledge_seconds`, `resolved`, `resolve_seconds`, `sent`, `arrived`, `declined`, `arrive_seconds`, `missed` |
| `PATROLS` | `patrol:read` | `tours`, `virtual`, `drone` — each `scheduled`, `done`, `partial`, `missed`, `failed`, `open`, `cancelled` |
| `GUARDS` | `shift:read` | `on_shift_now`, `due_not_started_now`, `shifts`, `worked`, `late`, `not_started` |
| `DEVICES` | `asset:read` | `devices`, `by_state` |
| `VISITORS` | `visitor:read` | `on_site_now`, `arrived`, `departed`, `refused`, `waiting_now` |
| `MAINTENANCE` | `maintenance:read` | `suggested_now`, `open_now`, `in_progress_now`, `overdue_now`, `raised`, `done` |

Three parts have a permission of their own on top of their section's: virtual
patrols (`vpatrol:read`), drone patrols (`drone:read`) and visits waiting for a
decision (`visitorauth:read`). A part that is not read is given as nothing, not
as zero.

How each is counted:

- **Incidents** belong to the site of their camera. `opened` and `resolved`
  are by when each happened; an incident is open until it is `resolved` or
  `closed`.
- **Response** is of the incidents opened in the period: how many had
  something done with them and the middle time until then, how many were
  resolved and the middle time until then. "Something done" is what the
  response clocks reckon it (`services/response_sla.py`): the first of a
  dispatch, a change of status, a guard being sent, or its resolution. Of the
  guards sent in the period: how many arrived, how many declined, and the
  middle time from being sent to arriving. `missed` is each response clock
  recorded as missed — which happens only once an organisation has switched
  the clocks on, so the answer also says since when they have been on
  (`clocks_on_since`), and the screen does not call clocks that are off "none
  missed".
- **Patrols** are those that fell due in the period: a guard's tour, a virtual
  patrol, a drone patrol. A drone patrol that was blocked or aborted is counted
  as failed. A share is taken only of those that are over.
- **Guards** are shifts: those being worked now, those due now and not
  started, and of the shifts due to begin in the period how many were worked,
  started late, or never started. No guard is named or counted alone.
- **Devices** are the health reading of phase 8 for the same sites, as it is
  now (`services/device_health.py`).
- **Visitors** are arrivals, departures and refusals logged at the gate in the
  period, who is on site now, and visits waiting for a decision.
- **Maintenance** is work orders: suggestions waiting for a person, orders in
  hand and overdue now, and those raised and completed in the period. A
  suggestion is not counted as raised until a person accepts it.

A period is the last 1, 7 or 30 days, ending now.

**Sites and customers.** `GET /sites` gives the same figures for each site the
caller may see — every site in use, and any other with something counted —
what is at no site apart, and the sites together. A site's customer is
`sites.client_id`; each customer's sites are summed (`add()`), without their
times. Asked for by customer (`client_id`), the board is counted for that
customer's sites, and then its times are real ones.

**What stands out.** For somebody who may read advice (`advice:read`), the
board says how many pieces of phase 9's advice stand for the same sites over
the last 4 weeks, and links to them. It does not repeat them.

---

## 3. The daily briefing

A briefing is for one calendar day, where the site is, for one site or for
every site together. A day that is not over is counted so far. It may be
drafted for a day in the last 31 days.

| State | Means | Then |
|---|---|---|
| `DRAFT` | Counted, and being reviewed. Read only by whoever manages briefings | `PUBLISHED` or `DISCARDED` |
| `PUBLISHED` | Read by whoever may read briefings. Not changed | — |
| `DISCARDED` | A draft set aside. Kept, with its number | — |

**Drafting** (`services/daily_briefing.py`) counts the board for that day, as
the drafter may read it, and writes each section as fixed sentences:

> 4 incidents were opened: 1 critical, 2 high and 1 medium.
> Somebody acted on 3 of the 4 opened; on half of them within 4 minutes.
> Guard tours: 1 done of 2 that are over; 1 missed.
> Of 3 devices, 1 read as working; 1 read as down and 1 with no reading.

A last section, **What stands out**, carries up to 3 pieces of phase 9's advice
word for word, each with what it rests on, under a note that they are counts of
the 4 weeks before and not a forecast. A section the drafter may not read is
not in the draft, and the briefing names it.

**Reviewing.** The reviewer may leave any section out (`left_out`) and write a
note (`note`). They may count the draft again; the note stays. They cannot
change a line: there is no field for one, and the request refuses any.

**Publishing** makes it readable and final. A briefing with every section left
out and no note is not published. Once published it is read whole by whoever
may read briefings — including a section they could not read on the board:
publishing it is the decision to share its counts. What was left out is not
shown to anybody, the reviewer included; that it was left out is.

**Correcting.** Drafting a day that already has a published briefing makes the
next revision. Until it is published the earlier one stands; after, the earlier
one is kept as it was and marked as replaced (`replaced_by`). A day has one
draft at a time. A draft set aside keeps its number.

**Who reads which.** A briefing for one site is read by whoever may see that
site. One for every site together is drafted and read only by somebody who is
not held to particular sites.

---

## 4. Reports

A report is records the platform already keeps, taken out as a CSV file
(`services/ops_reports.py`). Nine of them, each a list and none a judgement:

| Report | Read under | A row is |
|---|---|---|
| `board-sites` | `board:read` | A site, with the board's figures for the period. A figure of a section the reader may not read is blank |
| `response` | `response:read`, `incident:read` | An incident opened in the period: when somebody first acted on it, when it was resolved, the guards sent, the clocks missed |
| `device-health` | `asset:read` | A device, as it is read now and why |
| `maintenance` | `maintenance:read` | A work order raised or completed in the period, or still waiting or in hand |
| `visitors` | `visitorauth:read` | A visitor's or a contractor's authorisation asked for in the period |
| `access` | `access:read` | A door event in the period, with the door and the credential used |
| `risk` | `advice:read` | A piece of advice that stands over the last 4 weeks, with how much history it rests on and the latest answer to it |
| `evidence` | `evidence:package:read` | An evidence package made or sealed in the period, with its checksum and how many items, custody steps and holds it has |
| `investigations` | `investigation:read` | An investigation opened or closed in the period, or still open |

- **Two permissions, both needed.** `opsreport:export` to take any report out,
  and the permission the report's own records are read under. The list of
  reports says, for each, whether the caller may have it and why not.
- **A period** is the last 1, 7, 30 or 90 days. `device-health` is of how
  things are now and `risk` is of the last 4 weeks whatever is asked: neither
  takes a period.
- **The sites** are one site, one customer's sites, or every site the caller
  may see. Somebody held to particular sites is given those sites' records
  and nothing of what has no site.
- **What a spreadsheet would run is made text.** A value somebody typed — a
  visitor's name, a title, a reason — that begins with `=`, `+`, `-`, `@`, a
  tab or a return is written with an apostrophe before it.
- **A time is written where the organisation is**, and the heading of its
  column names the time zone. The file begins with a byte-order mark, so that
  a name in any script opens as it was written.
- **A file holds at most 10,000 records.** Past that its last line says it was
  cut, in words, and so do the response's headers.
- **Nothing is stored of a report** but the line in the audit log: who took
  which report, for where, for what period, and how many records
  (`report.export`).
- **A support session takes none out.** It reads a customer's account to
  answer a question; it does not carry the customer's records away. An API
  key may.

---

## 5. API

Under `/api/v1/operations-board`, all needing `board:read`:

| | | Also needs |
|---|---|---|
| `GET` | `/` | |
| `GET` | `/sites` | |

Under `/api/v1/daily-briefings`, all needing `briefing:read`:

| | | Also needs |
|---|---|---|
| `GET` | `/` | |
| `POST` | `/` | `briefing:manage` |
| `GET` | `/{id}` | |
| `PATCH` | `/{id}` | `briefing:manage` |
| `POST` | `/{id}/recount` | `briefing:manage` |
| `POST` | `/{id}/publish` | `briefing:manage` |
| `POST` | `/{id}/discard` | `briefing:manage` |

Under `/api/v1/operations-reports`, all needing `opsreport:export`:

| | | Also needs |
|---|---|---|
| `GET` | `/` | |
| `GET` | `/{key}` | |

`GET /{key}` also needs what that report's records are read under, which is
not the same for each and is checked when it is asked for.

There is no `PUT` or `DELETE` under any, and nothing but `GET` under the board
and the reports. Audited: `briefing.draft`, `briefing.review`,
`briefing.recount`, `briefing.publish`, `briefing.discard`, `report.export`.
Reading the board is not audited: it reads counts.

**Permissions** (migrations `0152`, `0153`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `board:read` | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – |
| `briefing:read` | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – |
| `briefing:manage` | ✓ | ✓ | ✓ | – | – | – | – | – |
| `opsreport:export` | ✓ | ✓ | ✓ | – | – | – | – | – |

---

## 6. Screens

- **Operations Board** (`/operations-board`), under Monitoring, in four
  parts — **Board**: for a site, a customer or every site and a period, each
  section the reader may read as counts, with what is as things stand marked
  "now", each time given as a middle time, what is not shown named, and how
  much advice stands. **Sites and customers**: one row for each site with one
  figure from each section, what is at no site, the sites together, and each
  customer's sites summed; a site's row opens the board for it.
  **Daily briefing**: the briefings, newest day first; drafting one; reading
  it with what each line is true of; leaving sections out, the reviewer's
  note, counting again, publishing after being told it is final, setting a
  draft aside, and drafting a correction. **Reports**: each report with what
  a row of it is, its columns and what it is read under; taking one out for a
  site and a period; a report the reader may not have, with the reason.

The phone is not changed in this phase. The existing dashboard (`/`), command
centre (`/command-centre`), analytics (`/analytics`) and reports (`/reports`)
are unchanged.

---

## 7. Files

| | |
|---|---|
| `backend/alembic/versions/0152_daily_briefings.py` | One table, its policy, grants and trigger, three permissions |
| `backend/alembic/versions/0153_operations_reports.py` | One permission. No table |
| `backend/app/services/ops_board.py` | The sections, how each is counted, adding. Reads only |
| `backend/app/services/daily_briefing.py` | The day, the words, the draft. Reads nothing and writes nothing |
| `backend/app/routers/operations_board.py` | The board, for a scope and site by site |
| `backend/app/routers/daily_briefings.py` | Drafting, reviewing, publishing, correcting |
| `backend/app/services/ops_reports.py` | The reports, their rows, a value and a file. Reads only |
| `backend/app/routers/operations_reports.py` | The list of reports, and one as a file |
| `frontend/src/api/operationsBoard.ts` | The typed client for the board and the briefing |
| `frontend/src/api/operationsReports.ts` | The typed client for the reports |
| `frontend/src/pages/board/` | The screen |
| `frontend/src/components/board/` | The briefing's dialogs, the reports and the shared wording |

Existing files changed, by additions only: `backend/app/main.py` (the three
routers are registered), `frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the four permissions).

No existing table is altered. The new table refers to `sites` and `users`. The
board reads `incidents`, `cameras`, `incident_status_history`,
`incident_responses`, `incident_escalations`, `tour_occurrences`,
`tour_schedules`, `patrol_routes`, `virtual_patrol_sessions`,
`drone_patrol_sessions`, `shifts`, `visitor_logs`, `visitors`,
`visitor_authorizations` and `maintenance_work_orders`, and writes to none of
them. Devices come through phase 8's reading and the clocks' switch through
phase 4's; a site's customer is read from `billing_clients`. The reports read
their own records and write to none of them either.

The table is `daily_briefings` and not `security_briefings`, as the plan had
it: tables named `security_…` are the intelligence layer's own.

---

## 8. Tests

| | |
|---|---|
| `backend/tests/test_operations_board.py` | Adding and sharing; every section counted from the database for a day and a week, a site, every site and no site; who is shown what; sites and customers; that the board only reads |
| `backend/tests/test_daily_briefings.py` | The day and the words; the draft line by line; drafting, reviewing, publishing, correcting, setting aside; who may draft and read which; what the application's role and the database refuse |
| `backend/tests/test_operations_reports.py` | A value, a heading and a file; the list and who may take one out; each report from the database; whose records a file holds; that taking one out is audited and changes nothing |
| `backend/tests/test_security_analytics_docs.py` | That this document says what the code does |
| `frontend/src/pages/board/operationsBoard.test.tsx` | The screen: its words, the board, the table of sites, the briefing and its dialogs |
| `frontend/src/pages/board/operationsReports.test.tsx` | The reports: the list, taking one out, a report that is not offered, a file that was cut |

---

## 9. What this does not do

- **It scores nothing.** No site, customer or guard is given an index, a
  grade or a rank, and no figure is a judgement.
- **The board and a briefing name nobody.** Guards are counted as shifts and
  visitors as arrivals. A reading per guard is phase 11's, and is not this. A
  report holds its records as they are, names included — which is why taking
  one out has a permission of its own and is audited.
- **It keeps no history of the board.** How things stood yesterday at noon is
  not known: a briefing's "when drafted" lines are exactly that.
- **It does not allow for what is not recorded.** A site with no patrols
  scheduled has none missed; a site whose clocks are off has none missed
  either, and the screen says the clocks are off.
- **It drafts nothing by itself.** A briefing is drafted when a person asks.
  No scheduler writes one each morning.
- **It sends nothing.** Publishing a briefing notifies nobody, emails nothing
  and makes no file. It is read on the screen.
- **It does not let a line be rewritten.** What a reviewer wants said
  differently goes in the note.
- **It delivers no report on a schedule.** A report is asked for by a person
  and handed to them. The existing scheduled reports are unchanged and do not
  carry these.
- **A report is a CSV file.** There is no PDF of one, no workbook and no
  chart.
- **It has no report per guard.** That is phase 11's.
- **The client role's portal is unchanged**, and a customer's own people are
  not given this board.
- **The phone is not part of it.**
- **It has run on the development organisation's records** for incidents,
  response, virtual patrols, shifts, cameras and visitors. That organisation
  has no guard tours, drone patrols or work orders in the last month, so those
  have run on test data only.
