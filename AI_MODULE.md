# The AI module

An assistant for the whole app, and AI that learns from each organisation's own
work activity. Agreed with the owner on 26 October 2026; the design is
`docs/plans/2026-10-26-ai-module-design.md`. This document says what is built.

| | |
|---|---|
| Licence | Its own, per organisation, granted by the Super Admin (`ai_module_licenses`) |
| Controls | The organisation's admin, per feature (`ai_feature_settings`) |
| Migrations | `0170`, `0171`, `0172`, `0173`, `0174` |
| Web | AI Insights (`/ai-insights`), AI Settings (`/ai-settings`); Manage Licenses > AI Assistant & Learning (Super Admin) |
| Runs | The scheduler, every hour (`AI_MODULE_INTERVAL_SECONDS`, default 3600): `services/ai_runner.py` |

## What is built

| Phase | What | State |
|---|---|---|
| 1 | The licence, the company controls, the permissions | Built |
| 2 | The learning engine; late and no-show warnings; overtime and fatigue forecast; AI Insights | Built |
| 3 | False-alarm learning; device failure warnings | Built |
| 4 | Roster suggestions; best patrol times; missed-patrol warnings; guard work trends | Built |
| 5 | Incident helper; payroll check | Built |
| 6 | The assistant: free and hosted models, conversations, asking across the whole app | Built |
| 7 | The assistant's actions behind Confirm cards; what it is never offered | Built |
| 8 | Reading an IC; the guided new-user flow | Built |
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

### False-alarm learning (`false_alarm`)

From the verdicts operators already give in the last **90 days**: an alert marked `false_positive` against one
`resolved` or `acknowledged` (`dismissed` says neither and is left out). Six factors from the alerts before each one:
how often the camera's alerts were false in the 60 days before, how often that kind of alert was false here, night,
severity, how many alerts the camera raised in the hour before, and the same alert again within 10 minutes. It needs
**40** alerts with a verdict, **8** false and **8** real.

It looks at alerts still open from the last 24 hours. Its bar starts high, **60%**, **75%** or **90%** (High, Medium,
Low), because calling a real alert false is the costly mistake. **It never closes, hides or changes an alert**: the
warning says to look at the picture first, and the alert stays in the queue until an operator decides. The
operator's verdict is then written on the warning as what happened.

A camera whose alerts this month were mostly false (at least **10** decided, **70%** or more false) is put forward
for a person to review its zones or sensitivity. Nothing on the camera is changed.

### Device failure warnings (`device_failure`)

From the state changes kept for every device (`device_health_changes`) and, for cameras, stream drops
(`camera_health_events`). For each device and each day of the last **120 days**: given its history to that morning,
did it go down in the next **7 days**? Six factors: times it went down in the last 7 and 30 days, times it ran
degraded in the last 7, days since it last went down, degraded now, and a camera's stream drops in the last 7 days.
A device already down or switched off is not predicted. It needs **60** device-days, **5** that went down and **5**
that did not. It suggests a check; **no work order is raised by itself** (the maintenance module is not touched).

### Roster suggestions (`roster_fit`)

The same learning as the late and no-show warnings, kept as a model of its own so its bar and decisions stay its
own. For each shift in the next **7 days** it compares the rostered guard's chance of starting late or missing it
with that of every other guard who could take it: an active guard (roles 3, 4, 5) who has worked at that site in
the last 180 days, has no shift within the organisation's minimum rest of it, and is not on approved leave that day.
It suggests when the rostered guard's chance reaches the bar and the best other guard's is at least **20** points
lower, naming who and why. **The roster is not changed**, and neither is the auto-scheduler.

### Missed-patrol warnings (`patrol_miss_risk`)

From the last **120 days** of virtual patrols (MISSED against COMPLETED or PARTIALLY_COMPLETED) and guard tours
(missed or incomplete against completed or late). Six factors from what was known before each patrol: how often the
schedule's last 10 were missed, the same for the person it is assigned to, whether the schedule's previous patrol was
missed, night, weekend, and whether it is a guard's tour. It needs **30** finished patrols, **5** missed or failed
and **5** completed, and warns about patrols due in the next **24 hours**. The patrol itself is not changed.

### Best patrol times (`patrol_timing`)

Per site, over the last **90 days**: when incidents and doors refused, forced or tampered with happened (read through
`services/risk_patterns.py`, unchanged) and when patrols actually started, each spread over the 24 hours where the site
is. It finds the **four-hour** window holding the largest share of the site's problems and compares it with the share
of its patrols there. It suggests when the gap reaches **15**, **25** or **35** points (High, Medium, Low), with at
least **10** problems behind it. Counts over a stated period, not a forecast. No schedule is changed.

### Guard work trends (`guard_trends`)

Each guard is compared with themselves, never ranked against others: punctuality (shifts started on time) and patrol
completion (checkpoints scanned on their tours), the last **4 weeks** against the **8 weeks** before. Only a clear drop
is raised: at least **15** points, with at least **8** counted in both periods, and unlikely to be chance by a
two-proportion test (z of at least 1.64, 1.96 or 2.58 by sensitivity). **It is not a score**: nothing about the guard
is changed or kept against them, nothing per guard is stored in the model, and the guard is not told by the platform.
Response times are not measured yet.

### Incident helper (`incident_helper`)

From the organisation's own incidents of the last **365 days**: the words of their titles and descriptions (word
weights, TF-IDF), the severity they were given, and the last note written on them (what was done). A new incident is
compared with each past one by those weights; the most similar are shown with what was done, and their severities,
weighted by how similar they are, suggest a severity. It needs **20** incidents with words. Hourly, it speaks about
incidents opened in the last **24 hours** whose recorded severity differs from the suggested one when **50%**, **60%**
or **75%** of the similar incidents' weight agrees (High, Medium, Low). It also answers on request, for words not yet
saved (`POST /ai/incident-helper`), for the incident form and the assistant. **No incident is changed.**

### Payroll check (`payroll_check`)

Each guard's own payslips in finalised payroll runs (the last **12**): the usual gross pay, regular hours, overtime
hours and pay per hour worked (the median), and how much that guard's pay normally varies (the median absolute
deviation). A payslip in a **draft** run is put forward when a figure is **2.5**, **3.5** or **4.5** deviations from the
guard's own normal (High, Medium, Low), with at least **4** finalised payslips behind it. Seen only by the roles chosen
(Admin and Manager can be). **Nothing in the payroll run or the payslip is changed**; once the run is finalised, the
warning closes.

### Deciding

On AI Insights a supervisor accepts or dismisses an open warning, with a note. Only an open warning can be decided,
once; the decision is written to the audit log as `ai.insight.decide`. A decided warning is never reopened by a later
run. Accepting changes nothing by itself. A supervisor limited to some sites sees only their sites' warnings.

## The assistant

`services/ai_chat.py`, `services/ai_functions.py`, `routers/ai_assistant.py`; the web's button and panel are
`components/ai/AssistantPanel.tsx`, on every page for the people it is switched on for.

### The two models

The Super Admin chooses per organisation, in the licence:

| | Free built-in (`LOCAL`) | Hosted (`HOSTED`) |
|---|---|---|
| What | An open model served by Ollama on our own server | Claude, through Anthropic's official Python SDK (`anthropic`, 1.x) |
| Set by | `OLLAMA_URL` (default `http://ollama:11434`), `AI_LOCAL_MODEL` (default `qwen2.5:7b-instruct`) | `ANTHROPIC_API_KEY`, `AI_HOSTED_MODEL` (default `claude-opus-5-5`), `AI_HOSTED_EFFORT` (default `medium`) |
| Cost | The server's | Per reply; the monthly limit applies |
| Refusals | Said in words | Re-run server-side on Anthropic's recommended model (`fallbacks: "default"`) |

Without the model running (no Ollama, or no key), the assistant says so in words; nothing else is affected.

### Reaching the whole app

Two tools rather than 1,100: **find_functions** searches the app's own function list (built from its API description
at start-up, about 1,100 functions) and offers only those the person's role holds; **use_function** calls one **inside
the server, with the person's own sign-in**, so permissions, row-level security, site limits, rate limits and the
audit log apply as when they click. Functions that answer with a file, a picture or a video are not offered.

**Never offered**: Super Admin functions, sign-in and passwords, two-factor sign-in, organisations (including deleting
one), licences, roles and permissions, API keys, the AI module's own settings, finalising payroll, erasing a person's
data, and deleting evidence, footage or recordings; changing someone's role is refused even through an allowed
function. A test holds each rule to a real route (`NEVER_OFFERED` in `services/ai_functions.py`).

### Changes: the Confirm card

`services/ai_actions.py`, `ai_pending_actions`. A change the model asks for is **prepared, not made**: the person sees
a card with exactly what will be sent, and nothing happens until they press **Confirm**. Then it is sent with their
sign-in at that moment, through the same in-process call, and written to the audit log as `ai.assistant.action`
(confirmed by that person). Cancel drops it. A card not decided within **30 minutes** expires.

- Missing required details are not guessed: the model is told which, and asks for them one at a time.
- Checked again at Confirm: the never-offered list, the person's permissions then, that the card is theirs, and that
  the reply which prepared it has finished (otherwise the note of what happened would be inserted before that reply,
  and the history must stay append-only).
- **Passwords never pass through the model.** One it sends is dropped; a function that needs one gets a temporary
  password made by the app, shown once to the person who confirms, and removed from the stored card.
- What became of a card (confirmed, refused by the app, cancelled, expired) is added to the conversation as a note,
  so the model knows on the next turn.
- Creating someone with a role is allowed; **changing** a person's role (a `role_id` in a PUT or PATCH) is not.
- After a card is decided the assistant waits; **Carry on** under the conversation tells it to go on to the next
  question.

### Reading an IC (`ic_reading`)

`services/ai_ic.py`, `ai_ic_drafts`; the web's dialog is `components/ai/IcScanDialog.tsx`, opened from the
assistant's panel (the card icon) for people who may create staff (`user:create`) and whose role the admin chose.

1. The person takes or chooses a photo of the front of the NRIC or FIN card, and the back for the address.
2. **Read on our own server** by RapidOCR (`rapidocr-onnxruntime`, Apache-2.0, its models inside the package, nothing
   downloaded when it runs). **The photos are never kept**: read in memory and dropped, never written to disk or the
   database, never sent to any model, hosted or not.
3. What is read: NRIC/FIN, name, date of birth, sex, race or nationality, country of birth, and the address. The
   number is checked against its **check letter** (S, T, F and G series); a number that fails is shown, marked, for the
   person to look again. The reader tends to drop spaces between words; they are put back where letters stand clearly
   apart, and **the person checks and corrects every field** before anything is used.
4. The checked details are given to the assistant **with the NRIC/FIN shortened to its last four characters**; it asks
   for the rest one question at a time (site, pay, preferred shift and so on) and prepares the new staff member as a
   Confirm card. The full number is held on our server in a draft (the text only) for **60 minutes**, belongs to the
   person who scanned the card, and is filled into the card only when they press Confirm; the draft is then deleted.
   One not used by then is deleted by the hourly pass. The data-retention statement names this period
   (`IC_READINGS`).

### Conversations, privacy and limits

- The reply streams (`status`, `delta`, `done`), so it starts at once. At most **8** rounds of tool use per reply.
- Conversations are kept in our database (`ai_conversations`, `ai_messages`) for as long as the company exists, and go
  with the person's account if it is deleted. A person reads only their own.
- Each turn is stored exactly as the model produced it and replayed unchanged: the history is append-only, as the
  hosted model requires. After the Super Admin switches the model, a conversation starts its context again.
- Sent to the hosted model, NRIC/FIN, passport and bank numbers are shortened to their last four characters. An IC's
  photos are not kept anywhere, and its full number never reaches either model (above).
- Each reply counts towards the organisation's **monthly limit** (its own calendar month). At the limit the assistant
  says so. A person may send at most **20** messages a minute.
- It replies in the person's account language (English, Malay, Tamil or Chinese).

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
| `POST` | `/ai/incident-helper` | `ai:insights:read` |
| `POST` | `/ai/assistant/chat` | `ai:assistant:use` |
| `GET` | `/ai/assistant/conversations` | `ai:assistant:use` |
| `GET` | `/ai/assistant/conversations/{conversation_id}` | `ai:assistant:use` |
| `GET` | `/ai/assistant/usage` | `ai:assistant:use` |
| `GET` | `/ai/assistant/actions` | `ai:assistant:use` |
| `POST` | `/ai/assistant/actions/{action_id}/confirm` | `ai:assistant:use` |
| `POST` | `/ai/assistant/actions/{action_id}/cancel` | `ai:assistant:use` |
| `POST` | `/ai/assistant/ic` | `ai:assistant:use`, and may create staff |
| `PUT` | `/ai/assistant/ic/{draft_id}` | `ai:assistant:use`, and may create staff |
| `GET` | `/platform/tenants/{tenant_id}/ai-license` | `license:manage` |
| `PUT` | `/platform/tenants/{tenant_id}/ai-license` | `license:manage` |

## Changes to what existed

Additions only:

- `backend/app/main.py`: the four routers registered.
- `backend/pyproject.toml`, `backend/requirements-lock.txt`: `anthropic>=1.17,<2` (Anthropic's official SDK, for
  the hosted model) and `rapidocr-onnxruntime>=1.4,<2` (the IC reader), and what they bring.
- `frontend/src/components/layout/AppShell.tsx`: the assistant's button.
- `backend/app/scheduler_main.py`: the hourly pass (`AI_MODULE_INTERVAL`, one block).
- `frontend/src/App.tsx`, `components/layout/Sidebar.tsx`, `motion/scenes.ts`: the AI Insights and AI Settings pages,
  their menu entries and their scenes.
- `backend/app/services/retention_statement.py`, `ENTERPRISE_SECURITY_HARDENING.md`: what is read from an IC is a
  ninth kind of record with a period (`IC_READINGS`, an hour); `frontend/src/api/dataGovernance.ts` reads a period in
  hours.
- `frontend/src/pages/Tenants.tsx`: the AI Assistant & Learning tab in Manage Licenses; the tab bar scrolls, now
  that four tabs share the drawer's width.
- `frontend/src/hooks/usePermission.ts`: the four permissions.
- `frontend/src/motion/conversion.test.ts`: the page count (129 pages, 121 that fetch).
- `PAGE_AWARE_ANIMATION_MATRIX.md`: written again.
