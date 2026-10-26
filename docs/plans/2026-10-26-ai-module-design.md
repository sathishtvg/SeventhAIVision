# The AI module: an assistant for the whole app, and learning from work activity

Agreed with the owner on 26 October 2026. The owner asked for an AI assistant
inside the whole application that replies at once and does the work by asking
for what it needs, with every decision left to a person. The owner also asked
for AI that is built in and free, learns from the company's own work activity
and improves itself from what people decide. Both are sold as one add-on
licence that the Super Admin grants and each company's admin controls.

This is a new module. Existing features stay exactly as they are. Section 12
lists the existing files that are touched, and only by additions.

## 1. The owner's decisions

| Question | Decision |
|---|---|
| Where the chat model runs | The Super Admin chooses per company: the **free built-in** model on our own server, or the **hosted** model (Claude, paid). It can be changed at any time. |
| How it is sold | An add-on licence, off until the Super Admin turns it on for a company, like Drone Patrol. |
| Who controls it inside a company | The company's admin: each feature on or off, which roles and sites, how sensitive warnings are, and whether it learns. |
| How much of the app the assistant covers | **Everything, automatically**: every function of the app, with the person's own permissions, every change behind a Confirm card. |
| Guards | Use it on the phone for their own work, **typing** (no voice), in the language on their account (English, Malay, Tamil, Chinese). |
| Actions it may never do | Deleting a company, finalising payroll, deleting evidence, changing roles or permissions, turning off two-factor sign-in. These stay manual. |
| IC photos | Read on our own server, **deleted straight after reading**, never kept and never sent to a hosted model. Without a photo, the assistant asks for each detail one by one. |
| How long conversations are kept | In our own database, for as long as the company exists. |
| Monthly limit | Set by the Super Admin per company, and raised whenever needed. |
| What is built first | The learning features that improve themselves from people's decisions. The assistant follows. |
| Server | This PC is for building and testing only. The free chat model runs on a proper server later (16 GB of memory or more, ideally a graphics card). |

## 2. The rule above all others

**The AI suggests or prepares; a person decides.** No warning changes a roster,
no prediction blocks a check-in, and no assistant reply saves anything until a
person presses Confirm. A suggestion, the decision on it and the action that
follows are separate records.

This is the same rule as the AI Security Intelligence layer. That layer stays
rule-based and does not learn (its owner decisions D3 and D4 stand). The new
module learns, but only inside its own tables, and never changes the layer, the
roster auto-scheduler, maintenance suggestions or any alert producer.

## 3. Names and boundaries

- Tables are `ai_*`. None is called `security_*`.
- Permissions are `ai:*`. API under `/api/v1/ai` (company) and
  `/api/v1/platform/tenants/{id}/ai-license` (Super Admin).
- No path contains "drone" (the drone security test walks such paths).
- Every table has row-level security by `tenant_id`, like every other tenant table.
- A company's models are trained only on that company's rows. Nothing is
  shared or pooled between companies.

## 4. The licence (Super Admin)

`ai_module_licenses`, one row per company, its own table for the same reason as
`drone_module_licenses`: the shared `tenant_module_licenses` treats a company with
no rows as licensed for everything, so one row there would change other modules.

| Field | Meaning |
|---|---|
| `is_enabled`, `expires_at` | On or off, and until when. No row, off, or expired: the module is off. |
| `features` | Which features the company bought (keys from section 6). |
| `assistant_provider` | `LOCAL` (free built-in) or `HOSTED` (Claude). |
| `monthly_reply_limit` | Assistant replies per calendar month. Required when `HOSTED`; may be empty (no limit) for `LOCAL`. Raising it takes effect at once. |
| `notes`, `licensed_by_user_id`, `licensed_at` | As the drone licence. |

Only the Super Admin changes it (`license:manage`, already MFA-gated). The
platform tenant cannot be licensed. Each change is written to the company's own
audit log. When a licence lapses, the company keeps reading what already
happened (past suggestions, past conversations); nothing new is produced.

## 5. Company controls (the company's admin)

`ai_feature_settings`, one row per company and feature, changed with the new
permission `ai:configure` (Admin 2, Manager 8):

- **enabled**: off until the admin turns it on, and only for licensed features.
- **roles**: which roles see it (for the assistant: who may use it).
- **sites**: all sites, or a chosen list.
- **sensitivity**: Low, Medium or High: how sure the model must be before it
  raises a warning.
- **learning**: on by default. Off keeps the last trained model and stops
  retraining.

Other new permissions: `ai:insights:read` (see warnings and suggestions: 2, 3, 4,
8), `ai:insights:decide` (accept or dismiss: 2, 3, 8), `ai:assistant:use` (2, 3,
4, 5, 6, 8). The Super Admin (1) holds none of the company ones, as with faces
(migration 0102). Clients (7) get no AI access.

## 6. The learning features

Each learns from rows the platform already keeps, nightly, per company.

| Key | What it warns or suggests | Learns from | Phase |
|---|---|---|---|
| `late_risk` | Shifts in the next 36 hours likely to start late or be missed, so a standby can be lined up | `shifts` (late, late minutes, no-shows), guard and site history, hours worked, rest since last shift | 2 |
| `overtime_fatigue` | Guards heading over their monthly overtime or working too long without rest | `shifts`, `overtime_requests`, roster ahead | 2 |
| `false_alarm` | Alerts likely to be false alarms, and cameras/hours worth a sensitivity review | `alerts` marked `false_positive` against real ones | 3 |
| `device_failure` | Cameras and devices likely to go down soon, as a suggested maintenance job | `device_health_changes`, `camera_health_events` | 3 |
| `roster_fit` | A fit score for guard, site and shift combinations, beside the auto-schedule (which is not changed) | lateness, incidents and patrol results per guard and site | 4 |
| `patrol_timing` | When problems happen at a site, and patrol times to match | incidents, alerts, door events by hour and day | 4 |
| `patrol_miss_risk` | Patrols likely to be missed or fail | virtual, guard-tour and drone patrol outcomes | 4 |
| `guard_trends` | Each guard's punctuality, patrol completion and response times over time, as trends for supervisors, never as a score that acts on its own | shifts, patrols, alerts | 4 |
| `incident_helper` | A suggested category and severity for a new incident, and similar past incidents with what was done | `incidents` text and outcome | 5 |
| `payroll_check` | Pay lines far from a guard's own normal before payroll is finalised | `payslips`, hours, rates | 5 |

### How it learns and improves itself

- **The model.** Logistic regression on named factors (for example "late 4 of
  the last 10 shifts", "night shift", "11 hours since last shift"), fitted with
  numpy and scipy, which the image already has. The trained weights are stored
  as JSON in `ai_models`: nothing is pickled, and every warning can say which
  factors raised it. The incident helper uses word counts (TF-IDF) the same way.
- **Two loops.** First, outcomes: each night the model is refitted on what
  actually happened (did the guard arrive late?). Second, people's decisions:
  when supervisors dismiss a kind of warning that did not help, the feature
  raises its own threshold for that company; when they accept warnings that
  came true, it keeps or lowers it. Both are recorded, so the threshold in force
  always has a reason.
- **Still learning.** A feature shows "still learning" until it has enough
  history (for example 30 finished shifts with at least 5 late ones). Until
  then it raises nothing rather than guessing.
- **Every suggestion shows** how sure it is, the top factors, and how much
  history it rests on. `ai_suggestions` keeps one row per subject (one shift,
  one alert) with its status: open, accepted, dismissed, expired; and later the
  outcome.

## 7. The assistant

### Models

- **Free built-in (`LOCAL`)**: an open model under the Apache-2.0 licence (for
  example Qwen2.5 7B Instruct), served by Ollama on our own server, set in
  configuration. On this PC only a very small model is used, for wiring tests.
- **Hosted (`HOSTED`)**: Claude through Anthropic's API, model set in
  configuration. Running it through AWS in Singapore is a later option.
- Both sit behind one interface, so the Super Admin's switch changes nothing else.
  Tests use a fake model.

### Asking and doing across the whole app

The assistant reaches the app through two tools rather than 1,100:

1. **find functions**: searches the app's own function list (built from the
   API's description at start-up) by meaning and keywords.
2. **use a function**: calls it **inside the server, with the person's own
   sign-in**. Permissions, row-level security, rate limits and the audit log
   apply exactly as when the person clicks in the app. The assistant cannot see
   or do anything the person could not.

Reading runs at once. **Any change** becomes a Confirm card (`ai_pending_actions`)
showing exactly what will be sent. It runs only when the person presses
Confirm, with the sign-in of the request that confirms it, and is written to the
audit log as done through the assistant and confirmed by that person.

**Never offered** (section 1), held by a list of path patterns and a test that
each pattern still matches a real route: deleting a company, finalising payroll,
deleting evidence, roles and permissions, turning off two-factor sign-in. Also
never offered: every Super Admin (`/platform`) function, sign-in and password
functions, and licences.

### Privacy

- NRIC/FIN and bank account numbers are masked (last four characters) in
  anything sent to the hosted model. The person still sees the full values,
  because the screen draws them from our own data.
- Conversations (`ai_conversations`, `ai_messages`) stay in our database for as
  long as the company exists. A PDPA erasure of a person must include their
  conversations; this is checked when phase 6 is built.

### Replies and limits

- The reply is streamed, so it starts at once.
- Each assistant reply counts towards the month's limit. At the limit, the
  assistant says so; every normal screen keeps working.
- Replies are in the person's account language. Office staff use it on the web
  and desktop; guards in the phone app.

## 8. Reading an IC

- The photos are read by RapidOCR, a free text reader that runs on
  `onnxruntime`, which the image already has. Nothing leaves our server, whichever
  chat model the company uses.
- The photos are held in memory only, never written to disk or the database,
  and dropped as soon as they are read.
- The NRIC/FIN is checked against its check letter. Name, date of birth, sex,
  nationality and address are filled into the new-user Confirm card; anything the
  reader could not read is asked one by one.
- Without photos, the assistant asks for each detail one by one, then the site,
  pay and preferred shift, then shows the Confirm card.

## 9. Guards on the phone

A new Assistant screen in the phone app: typing only, in the guard's language,
limited to what a guard may already do (their shifts, overtime requests,
incidents, check-in help). The same Confirm cards.

## 10. This PC and the real server

| | This PC (7.7 GB) | Production server |
|---|---|---|
| Learning features | Run in the existing scheduler; light | Same |
| IC reader | Runs in the api container | Same |
| Free chat model | A tiny model, only when testing the wiring, started on request | Ollama with a 7B to 14B model; 16 GB or more, ideally a graphics card |
| Hosted chat model | Works once an API key is set | Same |

Ollama is a Compose service under its own profile (`ai`), so it never starts
with the core services on this PC. Downloading its image and a model needs the
owner's yes first.

## 11. Phases

Each phase: migration (if any), backend, web and phone where it applies, tests
(row-level security tested as `svc_app` with two companies), CI green, merged to
main, and the feature reference PDF updated when an AI capability is added.

1. **Licence and controls.** `ai_module_licenses`, `ai_feature_settings`,
   permissions; Super Admin panel on the Tenants page; company AI Settings page.
2. **Learning engine**, plus `late_risk` and `overtime_fatigue`; AI Insights page
   with accept and dismiss; nightly training in the scheduler.
3. `false_alarm`, `device_failure`.
4. `roster_fit`, `patrol_timing`, `patrol_miss_risk`, `guard_trends`.
5. `incident_helper`, `payroll_check`.
6. **Assistant**: the model interface (local and hosted), conversations,
   streaming, asking across the whole app; chat panel on the web and desktop.
7. **Assistant actions**: Confirm cards, the never-offered list, audit.
8. **IC reading** and the guided new-user flow.
9. **Phone assistant** for guards.
10. Final check, documentation, the feature reference PDF.

## 12. Existing files touched (additions only)

- `backend/app/main.py`: registering the new routers.
- `backend/app/scheduler_main.py`: one more nightly job (training and suggestions).
- `frontend` menu, routes and motion scenes: entries for the new pages; the
  Tenants page gets the AI licence panel beside the drone one.
- `docker/docker-compose.yml`: the `ollama` service under profile `ai`.
- `backend/requirements*`: `rapidocr-onnxruntime` (phase 8) and the Anthropic
  client (phase 6).
- Phone navigation: the Assistant screen entry (phase 9).

Anything beyond these needs the owner's say first.

## 13. Not in this design

Voice; learning pooled across companies; any action without a person's
Confirm; the assistant changing its own permissions or licence; client (role 7)
access; running the hosted model through AWS Bedrock (later option).
