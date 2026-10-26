# The AI module

An assistant for the whole app, and AI that learns from each organisation's own
work activity. Agreed with the owner on 26 October 2026; the design is
`docs/plans/2026-10-26-ai-module-design.md`. This document says what is built.

| | |
|---|---|
| Licence | Its own, per organisation, granted by the Super Admin (`ai_module_licenses`) |
| Controls | The organisation's admin, per feature (`ai_feature_settings`) |
| Migrations | `0170` |
| Web | AI Settings (`/ai-settings`); Manage Licenses > AI Assistant & Learning (Super Admin) |

## What is built

| Phase | What | State |
|---|---|---|
| 1 | The licence, the company controls, the permissions | Built |
| 2 | The learning engine; late and no-show warnings; overtime and fatigue forecast; AI Insights | Not yet |
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
| `GET` | `/platform/tenants/{tenant_id}/ai-license` | `license:manage` |
| `PUT` | `/platform/tenants/{tenant_id}/ai-license` | `license:manage` |

## Changes to what existed

Additions only:

- `backend/app/main.py`: the two routers registered.
- `frontend/src/App.tsx`, `components/layout/Sidebar.tsx`, `motion/scenes.ts`: the AI Settings page, its menu entry and its scene.
- `frontend/src/pages/Tenants.tsx`: the AI Assistant & Learning tab in Manage Licenses; the tab bar scrolls, now
  that four tabs share the drawer's width.
- `frontend/src/hooks/usePermission.ts`: the four permissions.
- `frontend/src/motion/conversion.test.ts`: the page count (128 pages, 120 that fetch).
- `PAGE_AWARE_ANIMATION_MATRIX.md`: written again.
