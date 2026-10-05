# Drone Patrol — Operations

**As of:** 2026-09-25 · Covers Phases 4–5: the drone runner, the flight lifecycle,
commands, alerts, site edge gateways and what to do when something goes wrong.
Only the simulator provider exists; every flight described here is simulated until
hardware is connected. The edge gateway itself is described in
`DRONE_PATROL_EDGE.md`.

## The drone runner

A worker process of its own (`drone-runner` in `docker/docker-compose.yml`, entry
point `app.drone_runner_main`), built from the backend image like the scheduler.
It is separate so that a slow provider can never delay the scheduler's man-down
escalation, and so an abort is carried out in seconds rather than at the
scheduler's once-a-minute tick.

| Job | Default cadence | Environment variable |
|---|---|---|
| Commands, launches and live flights | every 2 s | `DRONE_RUNNER_TICK_SECONDS` |
| Drone health polls, then the lost-link sweep | every 15 s | `DRONE_RUNNER_HEALTH_SECONDS` |
| Sessions owed by schedules | every 30 s | `DRONE_RUNNER_SCHEDULE_SECONDS` |
| Flights' new AI detections into drone events; closing unconfirmed sightings; CCTV correlation | every 3 s | `DRONE_RUNNER_AI_SECONDS` |
| Finished flights' reports stored; report emails queued and sent | every 60 s, beside the loop | `DRONE_RUNNER_REPORT_SECONDS` |
| Footage and flight tracks past their period deleted | every 6 h, beside the loop, not at start-up | `DRONE_RUNNER_RETENTION_SECONDS` (`0` never deletes) |

```bash
docker compose -f docker/docker-compose.yml up -d drone-runner
```

On the 7.7 GB development machine it is light (no ML imports), but it is not one
of the core seven services — start it when working on drones. It logs only when
something happens: commands carried out, sessions created, drones lost or
recovered.

It is safe to run more than one: sessions and commands are locked with
`SKIP LOCKED`, so two runners never fly the same session. It is safe to restart:
flight state lives on the session, and a flight resumes where it was.

After every pass it writes a heartbeat to Redis (`drone_runner:heartbeat`, expiring
after 60 seconds) saying when, and whether the pass ran clean. That is what the
platform owner's console reads; a Redis that will not take it is logged and never
delays a flight.

**Drones behind a site edge gateway are not flown by the runner.** Their gateway
claims and flies their sessions and collects their commands. The runner only
watches: an edge session nobody claims within 10 minutes is recorded `MISSED`
(`EDGE_NOT_CLAIMED`); one whose gateway is silent for 60 minutes is closed
`FAILED` (`EDGE_UNREACHABLE`) — corrected if the gateway later reports the real
flight. Its health tick also marks a silent gateway `OFFLINE` and deletes sync
receipts older than a week.

## A flight, start to finish

```mermaid
stateDiagram-v2
    [*] --> READY: pre-flight passed
    [*] --> BLOCKED: pre-flight failed
    [*] --> MISSED: scheduled, not started in grace
    READY --> CANCELLED: cancel / abort
    READY --> LAUNCHING: runner re-checks, launches
    LAUNCHING --> ACTIVE
    ACTIVE --> PAUSED: pause
    PAUSED --> ACTIVE: resume
    ACTIVE --> RETURNING: abort / return home / low battery / lost link
    PAUSED --> RETURNING: abort / return home
    RETURNING --> COMPLETED
    RETURNING --> ABORTED
    RETURNING --> FAILED
```

- **Manual run** — `POST /drone-missions/{id}/run`. Pre-flight runs at once; the
  session is `READY` or `BLOCKED` with every reason.
- **Scheduled run** — the runner creates the session when it is due, judged by
  pre-flight at that moment. A run from before the schedule or its mission was last
  changed is never owed. A run the runner could not start within the schedule's
  grace period is recorded `MISSED`.
- **Launch** — the runner re-checks pre-flight with the freshest health against
  the session's **frozen** route, then launches. A drone can be in at most one
  flight; the database enforces it.
- **Outcome** — `COMPLETED` (every waypoint patrolled), `ABORTED` (an operator
  ended it) or `FAILED` (it could not finish). A fault after every waypoint was
  patrolled still ends `COMPLETED`, with the fault recorded and alerted.

## Commands

`pause`, `resume` (`drone:operate`), `abort`, `return-to-home`, `cancel`
(`drone:mission:abort`) on `/drone-patrols/{id}/…`. Each is queued and carried out
by the runner within about two seconds; `GET /drone-patrols/{id}/commands` shows
who asked, when, and what happened.

- Commands are refused at once when the session's state doesn't allow them, or
  the drone's provider can't do them.
- Before launch, abort and return-to-home simply cancel.
- Pressing the same command twice — or two operators at once — is one command.
- **Flight commands are never licence-gated.** A licence that lapses mid-flight
  cannot stop anyone bringing a drone down; only starting a new flight is gated.

## Alerts it raises

All go into the existing `alerts` table as module `drone_patrol` and are announced
as ordinary `alert_created` events, so notification rules, push, webhooks and
every alert feed handle them with no change. Each is raised once per flight (or
once per outage).

| Code | Severity | When |
|---|---|---|
| `drone.preflight_blocked` | medium | A scheduled run, or a ready run re-checked at launch, fails pre-flight |
| `drone.mission_missed` | medium | A scheduled run was not started within its grace period |
| `drone.comms_lost` | high | A flight reports a lost link, or an idle drone goes silent past its heartbeat timeout |
| `drone.mission_failed` | high | A flight ends `FAILED`, cannot launch, or has been out of contact for 10 minutes |
| `drone.flight_fault` | medium | A flight completed its patrol but reported a fault |
| `drone.command_failed` | **critical** | An abort or return-to-home could not be delivered — take manual control |
| `drone.gateway_offline` | high | A site edge gateway was silent past its heartbeat timeout. One per outage, for the site — its drones are not alerted one by one |
| `drone.<module>` (e.g. `drone.intrusion`, `drone.lpr`) | medium / high / critical | A verified drone event whose risk earned an alert that no worker alert already covers (`DRONE_PATROL_AI.md`) |
| `drone.<module>`, titled "Unconfirmed — …" | low | A sighting that would have been high or critical risk but was never confirmed — review it |

Realtime events for screens, on `tenant_events:<tenant>`: `drone_session_updated`,
`drone_telemetry` (latest position each tick), `drone_status_changed`,
`drone_command_processed`, `drone_event_created`, `drone_event_updated` (risk or
verification changed), `drone_event_cctv_updated` (related cameras or
corroboration changed), and from edge gateways `drone_gateway_status_changed`,
`drone_sync_completed`, `drone_event_created`, `drone_media_recorded`,
`drone_media_synced`.

## From event to guard

1. A drone event is **verified** and its risk scored (`DRONE_PATROL_AI.md`). A
   drone alert reaches the command centre carrying the card: site, area, drone,
   mission, site time, AI confidence, risk.
2. An **incident** opens in the platform's incident list — automatically at the
   profile's incident level (HIGH by default) or in an `INCIDENT` zone, or
   when an officer opens one (`POST /drone-events/{id}/incident`).
3. **Verify with drone**, if the drone is still overhead: it holds for up to two
   minutes while the AI keeps looking; the incident gets a note with what it saw.
4. **Dispatch** the nearest free guard (`POST /drone-events/{id}/dispatch`), or
   one you name — the platform's own dispatch, SLA and mobile flow.
5. The guard's acknowledgement, arrival and resolution run in the existing
   incident flow. When the incident is resolved or closed, the drone event is
   resolved too.

## The screens

In the web app's sidebar, **Drone Patrol**: the fleet (`/drones`), missions,
patrols and reports, events, and zones and AI profiles; every drone screen has
the same tab bar. The screen for each step:

| To | Go to |
|---|---|
| Register a drone, its provider, gateway and camera | Drone Fleet → Register drone; the Providers and Edge gateways tabs |
| Draw a route, choose the profile and recording policy, schedule it, check pre-flight | Drone Missions → New mission, or open one |
| Draw zones and set what each AI module is worth | Drone Zones → Security zones / AI security profiles |
| Watch a flight and pause, bring home or abort it | Drone Patrols → the flight while it is live |
| Replay a flight | Drone Patrols → the flight after it has landed |
| Investigate, open the incident, send a guard, ask for a second look | Drone Events → the event |
| Export the flights for a period | Drone Patrols → filters → Export CSV |

A drone with no camera linked flies and reports telemetry, but its live view says
so and the AI has nothing to read: link its camera on the fleet screen.

**On the phone.** A drone alert arrives like any alert (a push when it is high or
critical, the Alerts tab, the "Drone" filter). Opening it offers **View the drone event**; More → Drone
Events lists them all. From the event an officer sees the snapshot and clip,
opens the location in the phone's maps app, acknowledges, escalates, opens the
incident and dispatches a guard; the incident itself is updated on the existing
Incidents screens. A guard's phone shows drone events and can open an incident,
but not acknowledge or dispatch — that follows the roles in migration `0123`.

**On the Windows app.** The same screens as the web, after the app is rebuilt
from this version.

## Reports and their emails

A flight is reported **five minutes after it ends** — long enough for the fixed
cameras' corroboration and an officer's first action to be in it. The PDF and the
workbook are written under `drone/<tenant>/reports/<date>/<session>/` in the
evidence store and recorded with their checksums. Any flight's report can also be
downloaded at any time from its page; that copy is built fresh.

Emails are set up under **Patrols & reports → Report recipients**: an address, what
it covers (all sites, one site, one mission) and how often.

| Frequency | Sent | Attachment |
|---|---|---|
| After each flight | About five minutes after landing — including flights that were blocked, missed, failed or aborted | That flight's PDF |
| Daily | After midnight, for yesterday | A workbook: summary, a row per flight, a row per event |
| Weekly | Monday, for Monday–Sunday | The same |
| Monthly | The 1st, for last month | The same |

Days are the organisation's own (its timezone, set on the tenant). A period with
no flights sends nothing. Someone added today is not sent the report of a flight that
had already ended; a summary covers its whole period, whenever they were added.

**Email deliveries** shows what became of each one. A failure is retried by
itself after 5 minutes, then 15, an hour and four hours; after five attempts it
stays **Failed** with the mail server's reason until someone presses **Send
again**. Mail uses the platform's `SMTP_*` settings, on the `drone-runner`
service — if that service is not running, reports are neither stored nor sent,
and catch up (for flights of the last seven days) when it starts.

## Reading the analytics

**Drone Analytics** in the sidebar, for the last 7, 30 or 90 days and one site or
all of them.

- **Mission success** is flights completed out of those that should have flown to
  the end. A cancelled flight is not counted against it; a blocked, missed, failed
  or aborted one is — and the reasons are listed.
- **Suspicious** means assessed MEDIUM risk or above and not marked a false
  positive. **False-positive rate** and **incident conversion** are shares of all
  events in the period.
- **The risk map** ranks areas by an analytical score: weighted events per week.
  It shows where the drones have been finding things. It is not a forecast, and a
  quiet area is not a safe one — it may simply not be patrolled.
- **Recommendations** are suggestions the system generates from fixed thresholds
  (three suspicious night events in an area, intrusion three times at one spot on
  two days, under 80% of a mission's flights completing, and so on). Each says
  what it saw. They are prompts to look, not instructions.

Marking false positives is what keeps all of this honest: an event nobody
reviewed counts as real.

## What the platform owner sees

The platform console's health panel has a **drone-patrol** row. It answers the
vendor's question — is the drone service keeping up for everybody? — and nothing
about any one customer: a site's gateway being offline or a drone losing its link
is that customer's alert, not this row.

| Status | Meaning | Do |
|---|---|---|
| `ok` — "no organisation is licensed for drone patrol" | Nobody uses the module and nothing is owed. A runner is not needed | Nothing |
| `ok` — "runner alive; N flights in the air…" | Working | Nothing |
| `degraded` — "its last flight pass failed" | The runner is up but a pass raised an error | Read the runner's log; the failure is there with its traceback |
| `degraded` — "N flight commands waiting over a minute" | The runner is not getting to commands it owes — a stuck provider call, or too much work for one runner | Read the log; a second runner can be started safely |
| `degraded` — "N report emails more than 15 minutes late" | The report job is stuck or the mail server is slow | Check the mail server; the Deliveries tab shows each email's reason |
| `down` — "has not reported for 60 s" | The runner is stopped, or stuck inside one pass | `docker compose ps drone-runner`; start it. Flights behind a site gateway carry on under their gateway |
| `unknown` | Redis or the database could not be asked | Fix that first; the rows above it will say which |

Unexpected errors in drone operations appear in the console's error list like any
other, with the request id that is also in the API's log.

## Who took what

Every snapshot or clip handed over, and every report exported, is in the
organisation's audit log (**Audit Logs** screen, or `GET /api/v1/audit`):

| Action | Resource | Detail |
|---|---|---|
| `drone.media.access` | The media file | Kind, event, flight, the file's SHA-256 |
| `drone.report.export` | The flight, or none for a period summary | Format, size, the document's SHA-256; for a summary its scope and dates |

The checksum of an exported report is of the exact bytes that person was given:
a document produced later can be matched to the download that made it. Emails are
not in this list — they are in the Deliveries tab, with who they went to.

## How long footage and tracks are kept

| What | Kept for | Changed by |
|---|---|---|
| Snapshots and clips | The organisation's evidence retention — 90 days unless it has set otherwise | The tenant setting `evidence.retention_days`, the same one fixed-camera evidence follows |
| Flight tracks (the second-by-second telemetry) | A year | The tenant setting `drone.telemetry_retention_days` (`PUT /api/v1/settings/drone.telemetry_retention_days`); the installation's default is `DRONE_TELEMETRY_RETENTION_DAYS` |

Footage is **kept, whatever its age**, while it belongs to an event that became
an incident, to a confirmed event that is still open, or to a flight in progress.
What is deleted is what nobody acted on: footage of events that were resolved,
marked false or never confirmed, and routine footage attached to no event.
Deleting cannot be undone — resolve or escalate an event deliberately.

Never deleted: events, incidents, and the stored report PDFs and workbooks.

After a track is deleted the flight is still listed with its times, distance and
events; its replay and its report show the planned route without the flown line.

A period of `0` is read as "not set", never as "delete everything". A file that
cannot be deleted keeps its record and is tried again next time. Footage still
held only at a site is the gateway's to prune, not the centre's.

Each purge that deletes anything writes one `drone.retention.purge` entry to the
organisation's audit log: how many files and track samples, how many bytes, and
the periods that applied.

## Releasing the desktop app with the drone screens

The desktop app bundles the web build when it is built, so the drone screens
reach desktop users only in a release built after them. **1.0.2** is the first:
bump `version` in `desktop/package.json` and its two entries in the lockfile
(never `appId` or the MSI upgrade code), then

```bash
cd desktop && npm run dist
```

On a machine whose application-control policy blocks the MSI validation step,
`npm run dist -- -c.msi.warningsAsErrors=false`. The installers are unsigned
until a code-signing certificate is supplied (`CSC_LINK`, `CSC_KEY_PASSWORD`,
`WIN_PUBLISHER_NAME`); Windows will warn on first run.

## When something goes wrong

| Symptom | Likely cause | Do |
|---|---|---|
| A finished flight shows no distance | It was flown before the fix in Phase 14, or it never left the ground | Older flights keep an empty distance; their track is still stored |
| A screen says "Too many requests" | It asked one route more than 300 times in a minute (1,200 for video and pictures) | Wait the seconds it says. If a normal screen does this, the limit is too low for it: see *The Default API Rate Limit* in `docs/UPGRADE.md` |
| An old flight's replay shows no track | Its telemetry is past the organisation's track period | Nothing: the flight's record and report remain. Lengthen `drone.telemetry_retention_days` for the future |
| An old event has lost its snapshot | It was resolved, marked false or never confirmed, and is past the evidence retention period | Nothing can bring it back. Footage of incidents and of confirmed open events is never deleted |
| "Too many requests in a short time" on a report download | One person took 30 reports in a minute | Wait a minute. For many flights at once, use the period summary workbook |
| Platform console shows `drone-patrol` `down` | The runner is not running | See *What the platform owner sees* |
| Mission `BLOCKED` | A pre-flight check failed | Read `blocked_reason` or `preflight_result`; every failing check is listed. `GET /drone-missions/{id}/preflight` re-checks without creating anything |
| Drone `COMMUNICATION_LOST` | No heartbeat within `heartbeat_timeout_seconds` | Check the aircraft's power and link. It recovers by itself the next time it answers |
| Scheduled runs never appear | The runner isn't running, or the tenant's licence has lapsed | `docker compose ps drone-runner`; `GET /drones/entitlement` |
| Session stuck `ACTIVE` | The provider stopped answering | It closes itself as `FAILED` after 10 minutes of silence, and alerts |
| `drone.command_failed` | The abort or return-to-home didn't reach the drone | Use the manufacturer's own controller. The command's `result` has the provider's error |
| `drone.gateway_offline` | The site's link, power or gateway host is down | Flights in the air continue under the gateway and catch up when it returns. Check the site's network and the `drone-edge` service |
| Gateway `DEGRADED` | Clock more than 30 s out, backlog older than 5 min, or under 10% storage | `health.problems` on the gateway says which. Fix the clock (NTP) first — every time it reports depends on it |
| Edge session `MISSED` / `EDGE_NOT_CLAIMED` | The gateway was offline, or its drone was busy, when the run was due | Gateways do not start new flights without the centre. Check the gateway, then run the mission again |
| Items refused in a sync | Something the gateway sent can never be accepted | `GET /drones/edge-gateways/{id}/sync-receipts` lists each with the reason; the gateway keeps them in its local `rejected` table |
| No drone events from a flight | No camera linked, the camera does not run the profile's modules, or intrusion has no zone on the camera's picture | Pre-flight's `AI_*` warnings say which. People need a full-frame restricted zone on the drone's camera |
| Mission `BLOCKED` by `AI_TAMPERING` | The drone's camera has tampering detection on | Turn tampering off for that camera: on a moving camera it alerts at every change of view |
| An event lists no related CCTV | No fixed camera at the site has a position within 150 m, or every surveyed one faces away | Give cameras their positions; survey coverage (`PUT /drones/camera-coverage/{camera_id}`), then `POST /drone-events/{id}/correlate` |
| A camera that clearly sees the spot is called only "nearby" | Its coverage has not been surveyed | Record its heading, field of view and range, or draw its coverage polygon |
| No incident opened for a serious event | Not verified yet, below the profile rule's incident level (HIGH by default), or in a zone whose policy is `NONE` | Open one by hand, or set the rule's incident level / the zone's policy to `INCIDENT` |
| A drone incident shows no site in the incident list | The drone has no camera linked: incidents take their site from their camera | Link the drone's camera |
| Dispatch says no guard is free | Nobody is on shift at the site, or everyone on shift is on an open incident | Name a guard to dispatch anyway |
| Verify with drone refused | The flight is not active, the drone has moved on (over 75 m), the provider cannot pause and resume, battery under 30%, or an abort is pending | The reason says which; none of these can be overridden from here |
| An event stays `OBSERVING` | Seen fewer than 3 times, for less than the profile's verification time | After 30 s of quiet it closes `UNVERIFIED`; if it looked high risk, a low "Unconfirmed" alert asks for review |

## Simulator settings

The simulator provider takes `speed_factor` (1 = real time; 10 flies a 10-minute
route in 1 minute) and `failure_rate` (0–1: the share of flights that fail on
purpose). Which flights fail, how and when is decided by the session id, so a
failure can be replayed exactly.
