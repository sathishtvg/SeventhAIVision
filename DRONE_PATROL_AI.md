# Drone Patrol — AI: from Detection to Security Event

**As of:** 2026-09-25 · **Phase 6.** Built and tested against detections written
exactly as the AI workers write them. **No drone video has been processed yet** —
the simulator has no camera — so how well each worker performs from the air is
unverified, and this document says so wherever it matters.

## The pipeline

```text
drone camera ─► ingestion ─► AI workers ─► detections ─┐
                                                       ├─► context ─► risk ─► verification ─► drone event ─► alert
site gateway sighting ─────────────────────────────────┘
```

Nothing in the AI stack was changed. A drone's camera is a camera
(decision D1): ingestion reads its stream, the workers process its frames and
write `detections` like any other camera's. The drone runner's AI job (every
3 s, `DRONE_RUNNER_AI_SECONDS`) reads each flying drone's new detections and
takes them through the drone pipeline. A sighting reported by a site edge gateway
goes through the same pipeline.

| Piece | Where |
|---|---|
| The rules: suitability, zones, authorisation, verification, risk | `backend/app/services/drone_ai.py` (pure) |
| Reading detections, grouping, scoring, alerting | `backend/app/services/drone_ai_pipeline.py` |
| The runner job | `drone_runner.run_ai_tick` |
| Schema | migration `0126`: `drone_observations`; grouping and verification columns on `drone_events` |

## First, what each worker can do on a moving camera

The gap analysis left this as Phase 6's first task. It was settled by reading
every worker (`ai-worker/worker/tasks/`), not by flying.

| Module | On a drone | Why |
|---|---|---|
| `lpr` | **Works** | Frame by frame. Plates are small from altitude: fly low or zoom |
| `face` | **Works** | Frame by frame. Faces are tiny from altitude: useful only low and close |
| `fire_smoke` | **Works** | Frame-by-frame classification |
| `weapon` | **Works** | Frame by frame; small objects, confidence falls with altitude |
| `ppe` | **Works** | Frame by frame, per person |
| `fall` | **Works** | Frame-by-frame pose classification |
| `intrusion` | **Needs an image zone** | Reports a person only inside a zone drawn on the camera's picture. **A full-frame restricted zone on the drone's camera turns it into the drone's person detector** |
| `crowd` | **Needs an image zone** | Counts only inside crowd zones on the camera's picture |
| `behavior` | Unreliable — ignored | Loitering and tailgating need a fixed view; running and aggression measure movement between frames, which the drone's own motion corrupts |
| `abandoned` | Unreliable — ignored | Needs objects to stay still in the picture |
| `tampering` | Unreliable — **blocks launch** | Compares frames with a reference view; on a moving camera it would raise a high alert and an incident at every change of view |

`GET /api/v1/drone-ai/modules` returns this table.

## Setting up a drone's camera

1. Create a camera for the drone at its site and link it (`drones.camera_id`).
   Its stream is the aircraft's video: the provider's live stream, or configured
   by hand.
2. Enable on the camera the modules the drone's security profiles look for.
   **Do not enable `tampering`.**
3. For people, give the camera a **full-frame restricted zone** with severity
   `low`. The intrusion worker then reports everyone in view, and raises its own
   `low` alert at most once per cooldown (see *Decision for the owner*).

Pre-flight checks all of this:

| Check | | When |
|---|---|---|
| `AI_TAMPERING` | **blocks** | the drone's camera has tampering enabled |
| `AI_CAMERA` | warns | no camera is linked: no AI runs on the flight |
| `AI_MODULES` | warns | the profile looks for modules the camera does not run |
| `AI_IMAGE_ZONES` | warns | intrusion or crowd is on without a zone on the camera's picture |
| `AI_UNRELIABLE` | warns | abandoned or behaviour is on; their detections are ignored |

## Context

For every detection the pipeline establishes:

- **Where the drone was** — the telemetry sample nearest in time (within 15 s).
  The object is somewhere in view; its own position is **not estimated**, and the
  event says so (`location_method = DRONE_POSITION`).
- **The zone** — the most serious active zone the drone was over, using the
  zones frozen with the flight; a zone out of its active hours or days does not
  count. A person-restricted zone says nothing about vehicles, and the reverse.
- **Site time** — in the tenant's timezone.
- **Authorisation** — from the worker's own watchlist verdict (plate or face on
  the allow or block list), the zone's allowed vehicle plates, and **who is on
  shift at the site** (`shifts`) against the zone's allowed users and roles.
  Only for presence (intrusion, crowd, face) and vehicles: a fall or missing PPE
  is a safety matter whoever it is.
- **What else is happening** — other modules on this flight in the last two
  minutes; drone events in the same zone and incidents at the site in 30 days.
- **CCTV confirmation** — not yet (Phase 7).

## Grouping and verification

The workers do not track objects across a moving camera's frames, so detections
are **grouped**: same flight, same module, same zone, same plate or face, within
30 s of each other — one event. It is grouping, not tracking, and it can merge
two people seen together.

An event is **OBSERVING** until it is **VERIFIED**: seen 3 times, or for the
profile's `verify_min_seconds` (default 3 s). A weapon or fire seen at confidence
≥ 0.8 is verified on first sight. An event that goes quiet for 30 s without being
verified is closed **UNVERIFIED**.

## Risk

**AI confidence is not security risk.** Confidence (0–1) is the worker's, stored
as it came. Risk (0–100, `INFO`…`CRITICAL`) is the drone's judgement, with every
point written down in `risk_factors`.

| Factor | Points |
|---|---|
| Detection | the profile rule's base severity for the module: info 5, low 20, medium 40, high 60, critical 80 (defaults: weapon critical; fire, fall high; PPE, behaviour, abandoned medium; others low) |
| Zone | critical +25, no-entry +20, restricted / person- or vehicle-restricted +15, special inspection +5; zone severity high +5, critical +10 |
| Authorisation | blocklisted +25 · unauthorised +15 · someone allowed is on shift −5 (−10 in a normal zone; 0 in a critical zone) · on the allow list or an allowed plate −30 |
| Time | at night (22:00–06:00 site time), presence and vehicles +10 |
| Confidence | ≥ 0.90 +5 · < 0.60 −10 |
| Verification | verified +10 · seen once −10 · 5 or more detections +5 |
| Concurrent | another module on this flight in the last 2 minutes +10 |
| History | 3+ drone events here in 30 days +5 (5+: +10) · any incident at the site in 30 days +5 |

Levels start at: LOW 15 · MEDIUM 35 · HIGH 55 · CRITICAL 80.

The brief's examples, as tests: a person in a restricted zone at 02:17 with no
authorised shift is **HIGH**; a person in a normal zone by day with a guard on
duty is **LOW**.

What a tenant configures: per **profile**, the modules looked for, each module's
base severity and minimum confidence, the profile's minimum confidence and
verification time; per **zone**, type, severity, detection threshold, alert
policy, allowed people, roles and vehicles, and active hours. The weights above
are constants.

## Alerts

- Nothing unverified, nothing below MEDIUM, nothing in a zone whose alert policy
  is `NONE`. Level MEDIUM, HIGH or CRITICAL → an alert of that severity, code
  `drone.<module>`, into the existing alerts pipeline.
- **Never a second alert for what a worker already alerted.** If a worker's own
  alert on one of the event's detections is already as severe, the event links it.
  A drone alert is raised only when context makes the event more serious than the
  worker judged it — and again only if it grows more serious still.
- A sighting that would have been HIGH or CRITICAL but was never confirmed is not
  dropped: it raises a `low` "Unconfirmed — …" alert asking a person to review it.
- The zone's `INCIDENT` policy and the rule's `incident_risk_level` are for
  Phase 8, which creates incidents.

## Decision for the owner

**The AI workers alert on their own, before any drone context.** Each worker
raises its alert — and for weapons, fire, PPE and falls, an auto-incident — the
moment it detects something, under the tenant's alert rules. Those rules are per
tenant, not per camera. So on a drone's camera a weapon is alerted immediately,
an unrecognised face raises an `info` alert, and the full-frame intrusion zone
raises a `low` alert per cooldown. The drone pipeline links those rather than
repeating them, and escalates beyond them when context warrants, but it cannot
stop them without changing the workers.

To make the drone risk engine the only source of alerts for drone cameras, each
worker would skip its own alert and incident when the camera belongs to a drone
(one check per worker, in `ai-worker`). **Not done — it changes existing
functionality and needs your approval.**

## What is not verified

- Any worker on real drone video, at any altitude.
- The object's own position: only the drone's is recorded.
- Grouping as a stand-in for tracking.
- Everything above was tested with detections written in the workers' exact
  shape (`detections`, `lpr_events`, `face_events`, `alerts`), not produced by the
  models.
