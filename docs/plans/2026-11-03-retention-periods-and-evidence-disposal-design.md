# Phase 2 of the known limits: retention periods for modules 17 to 28, and evidence packages removed by a person

Date: 3 November 2026. Agreed with the owner in four parts; the programme is in
`2026-11-03-known-limits-programme-and-sg-payroll-design.md`.

The known limit it closes: *Most of modules 17 to 28 has no retention period. Four kinds may be given one by the
organisation and are kept until then; evidence packages have none.*

## Owner decisions

- **Scope: history only.** Records of what happened get an optional period. Settings in use stay until a person
  removes them: SOPs, site places, escalation policies, the asset register, maintenance schedules. Occurrence-book
  reviews and corrections go only with their entry, never on their own (the book is a statutory record, corrected by
  further entries).
- **Evidence packages: a person removes one**, from a "due for removal" list. The daily job never does.
- **Who removes: Admin and Manager**, through a new right of its own, `evidence:package:dispose`.
- **Custody: the chain stays tied to a disposal record.** The package row is deleted; the custody log gains a link to
  the disposal record and a "Disposed" step, so its entries are never left pointing at nothing.

## 1. Seven kinds of history, each with an optional period

The rules of the four kinds that have periods today (`services/record_retention.py`) apply unchanged: nothing is
removed until the organisation sets a period; a period is at least 30 days; the scheduler's daily pass removes what
is over and older, and writes one audit line per organisation.

| Kind | Clock starts | Always kept |
|---|---|---|
| Guard responses to an incident, with their steps, and the incident's escalations | when the incident was resolved | while the incident is open, or is in an evidence package |
| Shift handover summaries | the end of the shift they cover | drafts |
| Standing instructions that have ended, with who read them | when closed or expired | instructions in force, and their reads |
| Device health changes | when observed | each device's latest change (its current state) |
| Maintenance work orders done, cancelled or dismissed | when completed or closed | open and in-progress orders; a suggestion for an outage still going on, or for the date its schedule still stands at (else it would be suggested again) |
| Answers to risk advice | when answered | |
| Daily briefings, published or discarded | the briefing date, or when discarded | the current draft |

They are set on the existing Data Retention screen, and the retention statement lists them with their periods.

## 2. Evidence packages

- **The period** is set on the Data Retention screen like the others (at least 30 days). A sealed package counts from
  when it was sealed, a draft from its last change. With none set, packages are kept as today.
- **Due for removal** (a tab on the Evidence Packages screen, for `evidence:package:dispose`): past the period, nothing
  in it on hold, the investigation it was made from closed (or none), the incident it was made for resolved (or none),
  and no open case linking it.
- **Removing:** one at a time, with a reason, after a confirmation. The package and its item list go.
- **What stays:** a disposal record kept for good (`evidence_package_disposals`: number, title, the fingerprint of
  the sealed contents, when sealed, how many items, what it was made for, who removed it, when, why and under what
  period); the chain of custody, its entries linked to that record, ending with a "Disposed" step; an audit line.
- **Not removed:** the pictures and video it pointed to. They follow their own periods.
- **How:** the application inserts the disposal record; a database step that runs with the record (not the
  application) checks the package is due, refuses otherwise, links the custody entries, writes the "Disposed" entry
  and removes the package. The rule that a sealed package cannot be changed is not edited, and the application's role
  still cannot change or delete a package itself.

## 3. Testing

- Each kind: kept with no period; with one, only what is over and older goes; everything in "always kept" survives;
  two organisations, one with a period: the other's records are untouched.
- Packages: not due while held, while its investigation is open, its incident unresolved, a case linking it is open,
  or before the period; the list only for Admin and Manager; a reason required; the record, the last custody entry
  and the audit line left; as the application's role, a sealed package still cannot be changed or deleted, and
  another organisation can neither see nor remove one; the pictures and video are untouched.
- The retention statement and the test that every deletion is stated; web tests for the tab and the screen.

## 4. What changes

Migration 0176: `evidence_package_disposals`, its checked removal step, the custody log's link column and "Disposed"
step (owner approved), the new right for Admin and Manager.

Existing files changed (owner approved): `services/record_retention.py` (the kinds, and a kind that removes from a
second table), `services/retention_statement.py`, `core/config_keys.py`, `app/main.py` (one router), the web Evidence
Packages page, `api/evidencePackages.ts`, `hooks/usePermission.ts`, the Data Retention page, and the tests that pin
the retention lists (`test_data_governance.py`, `test_record_retention.py`).

Deploying needs the scheduler restarted to run the new kinds; with no period set it removes nothing, counted first.
