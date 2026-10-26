# The AI module

An assistant for the whole app, and AI that learns from each organisation's own
work activity. Agreed with the owner on 26 October 2026; the design is
`docs/plans/2026-10-26-ai-module-design.md`. This document says what is built.

| | |
|---|---|
| Licence | Its own, per organisation, granted by the Super Admin (`ai_module_licenses`) |
| Controls | The organisation's admin, per feature (`ai_feature_settings`) |
| Migrations | `0170`, `0171` |
| Web | AI Insights (`/ai-insights`), AI Settings (`/ai-settings`); Manage Licenses > AI Assistant & Learning (Super Admin) |
| Runs | The scheduler, every hour (`AI_MODULE_INTERVAL_SECONDS`, default 3600): `services/ai_runner.py` |

## What is built

| Phase | What | State |
|---|---|---|
| 1 | The licence, the company controls, the permissions | Built |
| 2 | The learning engine; late and no-show warnings; overtime and fatigue forecast; AI Insights | Built |
| 3 | False-alarm learning; device failure warnings | Not yet |
| 4 | Roster suggestions; best patrol times; missed-patrol warnings; guard work trends | Not yet |
| 5 | Incident helper; payroll check | Not yet |
| 6 | The assistant: free and hosted models, conversations, asking across the whole app | Not yet |
| 7 | The assistant's actions behind Confirm cards; what it is never offered | Not yet |
| 8 | Reading an IC; the guided new-user flow | Not yet |
| 9 | The assistant on the phone, for guards | Not yet |

## The AI suggests; a person decides

Nothing in this module acts on its own. A warning changes no roster, blocks no
check-in and touches no alert; an assistant reply saves nothing until a person
presses Confirm. The AI Security Intelligence layer, the roster auto-scheduler,
maintenance suggestions and every alert producer are unchanged.

## Three gates

A feature does something for a person only when all three are open:

1. **The licence** (the platform owner's): switched on, not expired, and the
   feature among those bought. No licence row is the same as off.
2. **The admin's switch** (the organisation's): the feature switched on. A
   feature that is not built yet can be licensed but not switched on.
3. **The person's role**: among the roles the admin chose for that feature.

Switching a feature off is always allowed, whatever the licence says. When the
licence lapses, the admin's choices are kept for when it returns.

## The licence

Set by the Super Admin in Manage Licenses > **AI Assistant & Learning**:

- on or off, and the last day it runs;
- which features were bought (the twelve below);
- the assistant's chat model: **the free built-in model** (on our own server, no
  cost per reply) or **the hosted model** (Claude, paid per reply);
- the **monthly reply limit**. The hosted model must have one; the database
  refuses a hosted licence without it (`ck_aiml_hosted_has_limit`). Raising it
  takes effect at once.

Each change is written to the organisation's own audit log as `ai.license.set`.
The platform tenant cannot be licensed.

## The features

| Key | Feature | Who it may be given to |
|---|---|---|
| `late_risk` | Late and no-show warnings | 2, 8, 3, 4 |
| `overtime_fatigue` | Overtime and fatigue forecast | 2, 8, 3, 4 |
| `false_alarm` | False-alarm learning | 2, 8, 3, 4 |
| `device_failure` | Device failure warnings | 2, 8, 3, 4 |
| `roster_fit` | Roster suggestions | 2, 8, 3, 4 |
| `patrol_timing` | Best patrol times | 2, 8, 3, 4 |
| `patrol_miss_risk` | Missed-patrol warnings | 2, 8, 3, 4 |
| `guard_trends` | Guard work trends | 2, 8, 3, 4 |
| `incident_helper` | Incident helper | 2, 8, 3, 4 |
| `payroll_check` | Payroll check | 2, 8 |
| `assistant` | AI assistant | 2, 8, 3, 4, 6, 5 |
| `ic_reading` | IC reading | 2, 8 |

Warnings about other people's work are not given to a guard (5) or a viewer (6).
Nothing is given to the client role (7) or the platform owner (1). Custom roles
are not offered yet.

## How it learns

`services/ai_learning.py`. **Logistic regression on named factors**, fitted with numpy and scipy (already in the
image). The weights are kept as plain JSON in `ai_models` (never pickled), so every warning can say which factors
raised it. Each organisation's model learns only from that organisation's rows.

- **Outcomes.** A model is refitted on what actually happened when it is more than **20 hours** old (`RETRAIN_AFTER`).
  With the admin's "keeps learning" off, the last model is kept and only scored with.
- **People's decisions.** The bar a warning must reach starts at the admin's sensitivity: **25%** (High), **40%**
  (Medium) or **60%** (Low). After at least **8** decided warnings, the last **20** move it: most dismissed raises it
  (by up to 20 points), mostly accepted and come true lowers it (by up to 10). The bar and the reason for it are shown
  on AI Insights and kept with each model.
- **Still learning.** A feature raises nothing until it has enough history, and says what it still needs.
- **Measured honestly.** Fitted on the older four fifths of the history and measured on the newest fifth, which it
  did not see.

### Late and no-show warnings (`late_risk`)

From every finished shift of the last **180 days**: late (`shifts.is_late`) or missed (a `no_show` violation, or a
shift whose time passed with nobody checking in). Eight factors, each worked out from what was known before the shift:
the guard's lateness in their last 10 shifts, how many shifts they have on record, the site's lateness in the 60 days
before, night, early start, weekend, hours rostered in the 7 days before, and rest since the previous shift. A rate
with little history is pulled towards the organisation's own. It needs **30** finished shifts, **5** late or missed
and **5** on time. It warns about shifts in the next **36 hours**; the warning expires when the shift starts, and what
happened is written on it afterwards.

### Overtime and fatigue forecast (`overtime_fatigue`)

The existing forecast (`services/overtime_projection.py`, unchanged) adds what a guard has accrued this month to what
the roster still holds. This feature learns how far past a shift's end each guard usually works (the last **90 days**,
pulled towards the organisation's own with five shifts' weight) and adds that for every shift still to come. It warns
when the expected total reaches **80%**, **90%** or **100%** of the **72-hour** monthly limit (High, Medium, Low). It
needs **20** completed shifts.

Fatigue is said as a rule, not a prediction: in the next 7 days, a shift with less rest before it than
`roster.min_rest_hours` (default 11), or more working days in a row than `roster.max_consecutive_days` (default 6).

### Deciding

On AI Insights a supervisor accepts or dismisses an open warning, with a note. Only an open warning can be decided,
once; the decision is written to the audit log as `ai.insight.decide`. A decided warning is never reopened by a later
run. Accepting changes nothing by itself. A supervisor limited to some sites sees only their sites' warnings.

## The admin's controls

On AI Settings, for each licensed feature: on or off; which roles use it; every
site or a chosen list; for a learning feature, how sure it must be before it
warns (Low, Medium, High) and whether it keeps learning. Each change is written to
the audit log as `ai.setting.set`.

## Who may do what

| Permission | | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Viewer 6 | Guard 5 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|---|
| `ai:configure` | Change the AI controls | ✓ | ✓ | | | | | | |
| `ai:insights:read` | See the warnings and suggestions | ✓ | ✓ | ✓ | ✓ | | | | |
| `ai:insights:decide` | Accept or dismiss them | ✓ | ✓ | ✓ | | | | | |
| `ai:assistant:use` | Talk to the assistant | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | | |

The licence itself needs `license:manage`, the Super Admin's alone, and two-factor
sign-in.

## The API

| Method | Path | Needs |
|---|---|---|
| `GET` | `/ai/status` | signed in |
| `GET` | `/ai/settings` | `ai:configure` |
| `PUT` | `/ai/settings/{feature_key}` | `ai:configure` |
| `GET` | `/ai/insights` | `ai:insights:read` |
| `POST` | `/ai/insights/{suggestion_id}/decide` | `ai:insights:decide` |
| `GET` | `/ai/learning` | `ai:insights:read` |
| `POST` | `/ai/learning/{feature_key}/run` | `ai:configure` |
| `GET` | `/platform/tenants/{tenant_id}/ai-license` | `license:manage` |
| `PUT` | `/platform/tenants/{tenant_id}/ai-license` | `license:manage` |

## Changes to what existed

Additions only:

- `backend/app/main.py`: the three routers registered.
- `backend/app/scheduler_main.py`: the hourly pass (`AI_MODULE_INTERVAL`, one block).
- `frontend/src/App.tsx`, `components/layout/Sidebar.tsx`, `motion/scenes.ts`: the AI Insights and AI Settings pages,
  their menu entries and their scenes.
- `frontend/src/pages/Tenants.tsx`: the AI Assistant & Learning tab in Manage Licenses; the tab bar scrolls, now
  that four tabs share the drawer's width.
- `frontend/src/hooks/usePermission.ts`: the four permissions.
- `frontend/src/motion/conversion.test.ts`: the page count (129 pages, 121 that fetch).
- `PAGE_AWARE_ANIMATION_MATRIX.md`: written again.
