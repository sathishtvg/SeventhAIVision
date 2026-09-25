# Drone Patrol — Provider Integration

**As of:** 2026-09-25 · **Installed providers:** the simulator only. No physical
drone, manufacturer SDK or flight controller has been integrated, and nothing in
this system claims otherwise.

This is how to connect a real kind of aircraft. Nothing above the provider layer —
the runner, pre-flight, sessions, the API, the screens — needs to change.

## What a provider is

A provider is two things, registered under the same key:

| Piece | Where | Says |
|---|---|---|
| Catalogue entry | `services/drone_provider_registry.py` → `PROVIDERS` | Its name, whether it is simulated, and the settings an administrator enters (types, bounds, which are secret) |
| Adapter class | `services/drone_providers/` → `ADAPTERS` | How to actually talk to the aircraft |

A test fails if a key is in one and not the other: a provider an administrator can
configure but nothing can fly, or the reverse, is a trap.

## The interface

`services/drone_providers/base.py` → `DroneProvider`:

| Method | Capability | Must |
|---|---|---|
| `connect()` / `disconnect()` | — | Open and release any session the vendor needs (default: nothing) |
| `get_status(drone, now)` | `HEALTH` | Return `DroneHealth`: battery, GPS, link, camera, storage, position, and a status hint (`READY`, `CHARGING`…). This is the heartbeat |
| `get_position(drone, now)` | `POSITION` | Latitude, longitude, altitude |
| `start_mission(flight, now)` | `MISSION` | Upload the plan and launch; return the first `FlightUpdate` and the vendor's mission reference |
| `get_telemetry(flight, now)` | `TELEMETRY` | Everything since the last call: samples, events, phase, and the outcome once landed |
| `pause_mission` / `resume_mission` | `PAUSE` / `RESUME` | Hold position / continue |
| `abort_mission` | `ABORT` | End the mission and invoke the aircraft's safe behaviour |
| `return_to_home` | `RETURN_TO_HOME` | Fly back to the launch point and land |
| `get_live_stream(drone)` | `LIVE_STREAM` | The stream URL, for the camera row that represents the drone (decision D1) |
| `get_recording(flight)` | `RECORDING` | A reference to the flight's recording |
| `capture_snapshot(drone, flight)` | `SNAPSHOT` | JPEG bytes |

**Declare only what the aircraft really does.** `capabilities` is enforced twice: the
API refuses a command the provider lacks with a 409 before queuing it, and the base
class raises `CapabilityNotSupported` if one is called anyway. Never implement a
capability by approximating it — a "pause" that is really a slow return is worse
than no pause.

## The contract for a flight

- **`FlightContext`** carries the session id, the drone, the `MissionPlan` (from the
  session's frozen configuration, never the live tables) and `provider_state`.
- **`provider_state`** is yours. Put whatever you need between calls in it —
  the vendor's mission id, the last telemetry cursor. The runner stores it on the
  session after every call and hands it back next time, so a runner restart resumes
  the flight. It must be JSON-serialisable.
- **`FlightUpdate.phase`** is one of `LAUNCHING`, `ACTIVE`, `PAUSED`, `RETURNING`,
  `LANDED`. **`outcome`** is set once, on landing: `COMPLETED`, `ABORTED` (an
  operator ended it) or `FAILED` (it could not finish). Set `failure_code` and a
  human `failure_reason` whenever something went wrong — including on a
  `COMPLETED` flight, where it raises a lower-severity alert.
- **Events** (`FlightEvent.kind`): `WAYPOINT_REACHED`, `WAYPOINT_DEPARTED`,
  `COMMS_LOST`, `COMMS_RESTORED`, `LOW_BATTERY`, `FAULT`, `RETURN_STARTED`, `PAUSED`,
  `RESUMED`, `LANDED`. Waypoint events move the session's waypoint progress;
  `COMMS_LOST` raises the lost-link alert.
- **Telemetry timestamps** must be the aircraft's, strictly increasing per drone.
  `(drone_id, recorded_at)` is unique, so a re-sent sample is dropped, not
  duplicated.
- **Every method is given `now`.** Don't read a clock inside the adapter.

## Rules that are not negotiable

1. **Safety behaviour belongs to the aircraft.** On lost link, low battery or a
   fault, the platform records and reports; it does not try to out-think the
   autopilot. Map the vendor's own return-to-home and landing behaviour to the
   phases and events above.
2. **Timeouts.** The runner wraps every call in a 10-second timeout
   (`PROVIDER_TIMEOUT_S`). An adapter that blocks longer has that tick treated as
   no contact. Ten minutes of no contact closes the session as `FAILED` and
   alerts.
3. **Secrets never travel with config.** Mark credential fields `secret=True` in
   the catalogue: they are encrypted at rest, never returned by the API, and arrive
   in the adapter as `self.secrets`.
4. **Provider code runs in the drone runner, never in a web request.** For
   aircraft reached through a site edge gateway, the adapter belongs in the edge
   service (Phase 5), with the runner relaying commands.
5. **Label simulated behaviour.** Anything not driven by a real aircraft must say
   so — `simulated: True` in the catalogue.

## Testing a new provider

Model the tests on `backend/tests/test_drone_simulator.py`: a full flight, pause and
resume, abort, return home, each failure path, and a restart (one long gap must
fly the same mission as many short ticks). Then run
`backend/tests/test_drone_runner.py` with the new provider configured — it drives
the whole session lifecycle through the database.

## What only hardware can complete

A physical aircraft; the manufacturer's SDK or cloud API with credentials; its
flight controller (and dock, for autonomous launch); the video stream it actually
emits; site edge hardware and network; and the operator's aviation permits and
site-specific flight configuration. In Singapore unmanned aircraft operations are
regulated by CAAS, and permits may be required depending on the aircraft and the
operation — confirm requirements with CAAS before any real flight.
