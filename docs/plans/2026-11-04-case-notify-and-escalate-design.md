# Phase 3 of the known limits: people told when put on a case, and cases that escalate

Date: 4 November 2026. Agreed with the owner in three parts; the programme is in
`2026-11-03-known-limits-programme-and-sg-payroll-design.md`.

The known limit it closes: *Nobody is told when they are put on a case, and nothing escalates one.*

## Owner decisions

- **How people are told:** on screen, by phone push, and through the organisation's notification rules (email or
  SMS) - the three ways guard responses already use.
- **What escalates:** an overdue task, a case gone quiet, and a close request waiting too long. (A high-priority case
  opened does not tell Managers by itself.)
- **Who next:** Managers and Admins who can see the case's site.
- **Timings:** defaults, which the organisation may change.

## 1. Being told when put on a case

- **When:** somebody becomes a case's lead (when it is opened, or the lead changes), is added as an investigator, or
  is given a task when it is made. Nobody is told of a change they made themselves, and nobody who cannot see the
  case's site is told.
- **How:** a pop-up on their open web and Windows screens (event `case_assigned`, carrying who it is for; nobody else
  sees it); a push to their phones with the same words; and the organisation's notification rules, for a rule that
  asks for `case_assigned`. The phone has no case screens: the push is a heads-up.
- **Never** changes the case. Telling is done after the change is saved; a telling that fails is logged and the
  change stands.

## 2. Escalations

A pass every 15 minutes (the scheduler), each organisation in its own transaction, as the application's role.

| Trigger | First | Then |
|---|---|---|
| A task past its due time, still open | its assignee and the case lead | after the overdue wait (24 h): Managers and Admins |
| An open case with nothing added for its quiet days (high 3, normal 7, low 14) | the lead | after 2 more quiet days: Managers and Admins |
| A close request not decided within the close wait (2 days) | those who may approve (case:manage), never whoever asked | - |

- *Nothing added*: the case's last change, history entry, task, link, person named or investigator change. The next
  one starts the quiet count again.
- **Timings** are the organisation's (`case_escalation_settings`), changed by whoever may change its settings
  (`settings:write`): quiet days 1-90 for each priority, the overdue wait 1-168 hours, the close wait 1-30 days.
- **Not backdated:** a step is due from the later of when its moment came and when the organisation's escalation
  began (its settings row, made on the first pass).
- **Recorded once** in `case_escalations` (case, task, trigger, step, the moment it counts from, when, how many people
  it was addressed to; a step addressed to nobody shows as that). Told as `case_escalated`, the same three ways.
  Each case shows its escalations; each is audited.
- **Never** reassigns, closes or reprioritises a case.

## 3. Testing and what changes

Tests: the four moments tell the right person and never the one who acted, nor somebody who cannot see the site; a
failed push leaves the change saved. Each trigger at its time, first and second step, once; a note restarts the quiet
count; a finished task stops; a close request never goes to whoever asked; not backdated; changed timings apply and
only `settings:write` changes them; run as the application's role with two organisations. Web: the pop-up only for
the person named; the timings dialog; a case's escalations.

Migration 0177: `case_escalation_settings`, `case_escalations`.

Existing files changed (owner approved): `routers/cases.py` (one call after each of the four moments),
`scheduler_main.py` (one more pass), `app/main.py` (the router), `hooks/useRealtimeEvents.ts`, the Cases page and case
dialog, `api/cases.ts`, `SECURITY_CASE_MANAGEMENT.md` and its test. New: `services/case_notify.py`,
`services/case_escalation.py`, `routers/case_escalation.py`, tests.

Deploying needs the api, the web console and the scheduler; what the scheduler would delete on starting is counted
first.
