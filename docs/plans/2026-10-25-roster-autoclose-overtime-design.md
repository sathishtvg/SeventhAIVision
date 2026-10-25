# Roster auto-schedule without patterns; shift auto-close; overtime requests: design

Agreed with the owner on 25 October 2026. The owner reported that the roster
never creates itself (the demo organisation's Auto-Schedule for 1-31 October made
an empty draft: it has shift definitions and site headcounts but no roster
patterns), and asked that a shift close itself around 15 hours, with overtime
requested by the guard and approved by a supervisor or admin.

## 1. The roster

- Where a site has **no active roster patterns**, Auto-Schedule plans it from the
  organisation's active **shift definitions** (one day, one night) and the site's
  `day_guards_required` / `night_guards_required`, every day of the period, under
  the same rules as today (rest hours, consecutive days, leave, preferences, duty
  teams). A site with patterns is planned from them, as before.
- It never makes an **empty draft**: with nothing to plan it refuses and says why
  (no shift definitions, no active guards, every site needs 0 guards).
- A period that starts in the past is planned **from today**, and the answer says so.
- "Generate next 7 days" (each guard's own pattern repeated) is unchanged; with no
  patterns it says to add one first.

## 2. Auto-close and the reminder

- New organisation setting `attendance.auto_close_hours` (default **15**): a shift
  still open that many hours after check-in is closed by the system. A second,
  `attendance.auto_close_reminder_minutes` (default **15**), is when the guard is
  warned before. Both on the web Settings page, in a new Attendance section.
- The scheduler sweeps every 5 minutes. The reminder is a push to the guard's
  phone. A shift it closes gets `auto_closed_at`, is shown as "Closed by the
  system - no check-out" on Attendance and Shifts, and the closing is audited.

## 3. Overtime requests

- On duty, the phone's check-in card offers **Working overtime**: extra hours and a
  reason. The shift then stays open until `auto_close_hours` + those hours (the
  reminder moves with it). A guard may also ask up to 48 hours after the shift
  ends. A rejection while the shift is open brings back the normal close.
- Supervisors and admins approve or reject on the web Attendance page (and the
  desktop app), beside the correction requests; a rejection needs a reason.
  Supervisors are told by push when a request arrives; the guard is told the outcome.
- Permissions are the existing pair: `attendance:request` asks, `attendance:manage`
  decides.

## 4. Check-out location

Check-out records its location, as now. Outside the site's geofence it goes
through and is marked `checked_out_away_from_site`, as an off-site check-in is.

## 5. Pay (the owner's choice: only approved, up to the time worked)

For a shift that uses the new rule, paid time is check-in to the planned end plus
approved overtime, and approved overtime is never more than the time actually
worked past the planned end. Time past the end with no approval is shown as
"unapproved extra time", not paid. A shift closed by the system pays its planned
hours plus approved overtime.

Shifts checked in before the release keep today's rule (overtime counted
automatically): `shifts.overtime_needs_approval`, set at check-in, says which rule
a shift follows. Payroll runs and timesheets already made are not recalculated.

## 6. What is stored

- New table `overtime_requests` (shift, guard, hours asked, reason, status, who
  decided, when, why), row-level secured per organisation.
- New columns on `shifts`: `auto_closed_at`, `checked_out_away_from_site`,
  `overtime_needs_approval`, `auto_close_reminded_for` (the closing time the guard was warned about).
- New settings keys: `attendance.auto_close_hours`, `attendance.auto_close_reminder_minutes`.

## 7. Existing files this changes

`backend/app/services/roster_autoschedule.py` (posts from shift definitions),
`backend/app/routers/shifts.py` (check-out: geofence flag, overtime from
approvals, the new rule's marker at check-in), `backend/app/routers/payroll.py`
(the two hour sums), `backend/app/scheduler_main.py` (the sweep),
`backend/app/core/config_keys.py` (the settings), `backend/app/services/attendance_status.py`
(their defaults), `backend/app/main.py` (the new router), `frontend/src/pages/Roster.tsx`, `frontend/src/pages/Attendance.tsx`,
`frontend/src/pages/Settings.tsx`, `mobile/src/components/ShiftCheckInCard.tsx`.

## 8. Build order

1. The roster fix.
2. Auto-close and the reminder.
3. Overtime requests and approval.
4. Pay.
5. A new phone build and desktop 1.0.11, each on the owner's yes.
