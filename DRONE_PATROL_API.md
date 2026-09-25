# Drone Patrol — API

**As of:** 2026-09-25 · **Built:** Phases 3–4 — 69 operations across fleet, planning,
flying, operations and platform licensing. The endpoint tables below were
extracted from the router source, not written from memory.

**Not yet built:** telemetry and event *ingestion* from edge gateways (Phase 5);
AI events raised during a flight (Phase 6); report downloads (Phase 11). Flights
are flown by the drone runner against the simulator provider — see
`DRONE_PATROL_OPERATIONS.md`.

## Conventions

| Topic | Rule |
|---|---|
| Base path | `/api/v1` |
| Auth | `Authorization: Bearer <access token>`. Every operation also requires the permission shown |
| Licence | Every operation that creates, changes or removes something, or starts a flight, requires the tenant's Drone Patrol licence (**Gated** column) and answers **403** with a reason when it is missing or expired. Reads, event decisions and flight commands are never gated — history stays readable, an open event can always be closed, and a drone in the air can always be brought down |
| Site scoping | Users restricted to sites see only those sites' drones, routes, zones, missions, sessions and events. Anything outside answers **404**, not 403, so its existence is not revealed |
| Tenant isolation | Row-level security on every drone table; another tenant's IDs answer **404** |
| IDs | UUIDs. A malformed ID answers **422** before touching the database |
| Lists | `GET /drones`, `/drone-missions`, `/drone-patrols` and `/drone-events` are paginated (`limit` ≤ 200, `offset`) and return `{items, total, limit, offset, has_more}`. Other lists return a plain array |
| Updates | `PUT` bodies are partial: a field sent as `null` is cleared, a field left out is left alone. Names, codes and required settings cannot be cleared |
| Errors | `403` permission or licence · `404` not found or not visible · `409` conflict (duplicate name or code, in use, already decided) · `422` invalid input, with every problem stated |
| Audit | Every change and every event decision is written to the tenant's hash-chained audit log with the user and address |

## Fleet — `/api/v1/drones`

| Method | Path | Permission | Gated |
|---|---|---|:-:|
| GET | `/drones/entitlement` | `drone:read` | |
| GET | `/drones/dashboard` | `drone:read` | |
| GET | `/drones/providers/catalogue` | `drone:read` | |
| GET | `/drones/providers` | `drone:read` | |
| POST | `/drones/providers` | `drone:create` | ✓ |
| PUT | `/drones/providers/{provider_id}` | `drone:update` | ✓ |
| DELETE | `/drones/providers/{provider_id}` | `drone:delete` | ✓ |
| GET | `/drones/edge-gateways` | `drone:read` | |
| POST | `/drones/edge-gateways` | `drone:create` | ✓ |
| POST | `/drones/edge-gateways/{gateway_id}/rotate-credential` | `drone:update` | ✓ |
| PUT | `/drones/edge-gateways/{gateway_id}` | `drone:update` | ✓ |
| DELETE | `/drones/edge-gateways/{gateway_id}` | `drone:delete` | ✓ |
| GET | `/drones` | `drone:read` | |
| POST | `/drones` | `drone:create` | ✓ |
| GET | `/drones/{drone_id}` | `drone:read` | |
| PUT | `/drones/{drone_id}` | `drone:update` | ✓ |
| POST | `/drones/{drone_id}/disable` | `drone:update` | ✓ |
| POST | `/drones/{drone_id}/enable` | `drone:update` | ✓ |
| DELETE | `/drones/{drone_id}` | `drone:delete` | ✓ |
| GET | `/drones/{drone_id}/maintenance` | `drone:maintenance:read` | |
| POST | `/drones/{drone_id}/maintenance` | `drone:maintenance:manage` | ✓ |
| GET | `/drones/{drone_id}/telemetry` | `drone:read` | |

- **Entitlement** says whether the module is licensed, why not if it isn't, the
  limits and current usage. It is readable without the licence so a screen can
  explain a disabled module.
- **Limits.** A disabled drone does not count against `max_drones`; disabling
  frees a slot. A site counts once whether it has one drone or ten.
- **Providers.** Settings are validated against the provider catalogue; unknown
  settings are refused, not stored. Secret settings are encrypted and never
  returned — responses carry `has_secret` only. On update, a secret left out keeps
  its stored value. Only the simulator is catalogued until real hardware is chosen.
- **Edge gateways.** The access credential is returned **once**, on creation or
  rotation, and stored only as a SHA-256. Rotation invalidates the previous one.
  A drone may use only its own site's gateway.
- **Deleting a drone** is refused once it has flown or while an enabled mission
  uses it; disable it instead. Disabling is refused while it is on a mission.
  Enabling returns it to `OFFLINE` until its next heartbeat.
- **Maintenance** moves the drone's dates forward only: entering an older job
  late never moves `last_maintenance_at` backwards.
- **Telemetry** takes `start`/`end` (default: the last hour), at most 24 hours
  per request, up to 5,000 samples in time order.

## Planning — zones, profiles, routes, missions, schedules

| Method | Path | Permission | Gated |
|---|---|---|:-:|
| GET | `/drone-zones` | `drone:read` | |
| POST | `/drone-zones` | `drone:mission:create` | ✓ |
| GET | `/drone-zones/{zone_id}` | `drone:read` | |
| PUT | `/drone-zones/{zone_id}` | `drone:mission:update` | ✓ |
| DELETE | `/drone-zones/{zone_id}` | `drone:mission:update` | ✓ |
| GET | `/drone-security-profiles` | `drone:read` | |
| POST | `/drone-security-profiles` | `drone:mission:create` | ✓ |
| GET | `/drone-security-profiles/{profile_id}` | `drone:read` | |
| PUT | `/drone-security-profiles/{profile_id}` | `drone:mission:update` | ✓ |
| DELETE | `/drone-security-profiles/{profile_id}` | `drone:mission:update` | ✓ |
| GET | `/drone-routes` | `drone:read` | |
| POST | `/drone-routes` | `drone:mission:create` | ✓ |
| GET | `/drone-routes/{route_id}` | `drone:read` | |
| PUT | `/drone-routes/{route_id}` | `drone:mission:update` | ✓ |
| PUT | `/drone-routes/{route_id}/waypoints` | `drone:mission:update` | ✓ |
| DELETE | `/drone-routes/{route_id}` | `drone:mission:update` | ✓ |
| GET | `/drone-missions` | `drone:read` | |
| POST | `/drone-missions` | `drone:mission:create` | ✓ |
| GET | `/drone-missions/{mission_id}` | `drone:read` | |
| PUT | `/drone-missions/{mission_id}` | `drone:mission:update` | ✓ |
| PATCH | `/drone-missions/{mission_id}/enabled?enabled=` | `drone:mission:update` | ✓ |
| DELETE | `/drone-missions/{mission_id}` | `drone:mission:update` | ✓ |
| GET | `/drone-missions/{mission_id}/schedules` | `drone:read` | |
| POST | `/drone-missions/{mission_id}/schedules` | `drone:mission:update` | ✓ |
| PUT | `/drone-schedules/{schedule_id}` | `drone:mission:update` | ✓ |
| DELETE | `/drone-schedules/{schedule_id}` | `drone:mission:update` | ✓ |
| GET | `/drone-schedules/{schedule_id}/preview?count=` | `drone:read` | |

- **One site.** A mission, its route, its drone and every zone a waypoint names
  must be on the same site; anything else is refused with a reason. A route's and
  a mission's site cannot change after creation.
- **Zones** are map shapes: `POLYGON` (3+ points), `RECTANGLE` (two opposite
  corners or four corners — stored as four) or `CIRCLE` (centre and radius, at
  most 50 km). Points are `{lat, lng}` or `[lat, lng]`. An active window with
  `active_from` later than `active_to` runs overnight. Vehicle plates are stored
  upper-case without spaces, as a plate reader reports them.
- **Profiles** hold one rule per AI module, and only modules that exist:
  `lpr`, `face`, `intrusion`, `ppe`, `crowd`, `fire_smoke`, `weapon`, `behavior`,
  `tampering`, `abandoned`, `fall`. `rules` on update replaces the whole set.
- **Routes.** The base point defaults to the site's coordinates. Waypoints are
  numbered by list order; `PUT …/waypoints` replaces the whole path. The route
  detail includes `summary.length_m` and `summary.outside_site_geofence` — the
  sequence numbers of waypoints beyond the site's fence. That is a **warning**,
  not a refusal. A route an enabled mission uses cannot be deleted.
- **Missions** show `next_run` (soonest launch across enabled schedules) and the
  most recent session. A mission that is flying cannot be deleted; past sessions
  keep its name.
- **Schedules** are `ONCE`, `DAILY`, `WEEKLY` (the weekday of `start_date`),
  `SELECTED_DAYS` (`weekdays`, 0 = Monday) or `SPECIFIC_DATE` (`specific_dates`),
  computed in the schedule's own IANA `timezone` (default `Asia/Singapore`).
  Every problem with a schedule is reported at once. `next_runs` give each launch
  in UTC and in local time.

## Flying — pre-flight and manual runs

| Method | Path | Permission | Gated |
|---|---|---|:-:|
| GET | `/drone-missions/{mission_id}/preflight` | `drone:read` | |
| POST | `/drone-missions/{mission_id}/run` | `drone:mission:execute` | ✓ |

- **Pre-flight** runs every check a launch runs and creates nothing. It returns
  `passed`, `blocking` and `warnings` (each check with a `code` and a reason a
  person can act on) and the flight `estimate`: duration, distance and the
  battery it needs.
- **Blocking checks:** licence, mission enabled, site active, drone assigned,
  enabled and available (not in flight, not in maintenance), communication
  (heard from within its heartbeat timeout), battery (at least the mission
  minimum **and** the estimated need plus a 20% reserve), GPS, camera, storage,
  maintenance due, provider configured and able to fly missions, edge gateway
  online (if the drone uses one), route active with waypoints, estimated duration
  within the mission's limit, security profile active. **Warnings** — do not
  block: storage low, waypoints outside the site's geofence, no security profile.
- **Run** answers **201** either way. The session is `READY` (the runner launches
  it within seconds, re-checking with the freshest health first) or `BLOCKED`
  with every reason, so the attempt is on the record. **409** if the drone is
  already committed to another flight.
- A session freezes the mission, route, waypoints, drone, profile and zones as
  they were at the moment it was created. Editing the mission afterwards changes
  the next flight, never this one.

## Operations — sessions and events

| Method | Path | Permission | Gated |
|---|---|---|:-:|
| GET | `/drone-patrols` | `drone:read` | |
| GET | `/drone-patrols/{session_id}` | `drone:read` | |
| GET | `/drone-patrols/{session_id}/track` | `drone:read` | |
| POST | `/drone-patrols/{session_id}/pause` | `drone:operate` | |
| POST | `/drone-patrols/{session_id}/resume` | `drone:operate` | |
| POST | `/drone-patrols/{session_id}/abort` | `drone:mission:abort` | |
| POST | `/drone-patrols/{session_id}/return-to-home` | `drone:mission:abort` | |
| POST | `/drone-patrols/{session_id}/cancel` | `drone:mission:abort` | |
| GET | `/drone-patrols/{session_id}/commands` | `drone:read` | |
| GET | `/drone-events` | `drone:event:read` | |
| GET | `/drone-events/{event_id}` | `drone:event:read` | |
| POST | `/drone-events/{event_id}/acknowledge` | `drone:event:acknowledge` | |
| POST | `/drone-events/{event_id}/investigate` | `drone:event:investigate` | |
| POST | `/drone-events/{event_id}/escalate` | `drone:event:investigate` | |
| POST | `/drone-events/{event_id}/resolve` | `drone:event:investigate` | |
| POST | `/drone-events/{event_id}/false-positive` | `drone:event:investigate` | |

- **Flight commands** are queued, not carried out in the request: they answer
  **202** with `{command, queued, session_status}` and the drone runner carries
  them out within about two seconds, recording the outcome on the command
  (`DONE`, `REJECTED` or `FAILED`, with a `result`). The body is an optional
  `reason`. **409** when the session's state doesn't allow the command (pause
  needs `ACTIVE`, resume needs `PAUSED`, cancel only before launch, nothing once
  it has ended) or when the drone's provider cannot do it. Before launch, abort
  and return-to-home simply cancel. The same command pressed twice — or by two
  operators at once — is one command: the second answers `queued: false` with
  the first. **Never licence-gated.**
- **Commands** lists every command for the session in the order given, with who asked.
- **Track** returns the flown path for replay, thinned evenly to at most 2,000
  points, always keeping the first and last sample.
- **Event detail** includes its media (without storage paths — media is served,
  not exposed), the correlated fixed cameras with distances, and the alert and
  incident it fed.
- **Event decisions.** An event moves `NEW → ACKNOWLEDGED → INVESTIGATING /
  ESCALATED → RESOLVED`, or to `FALSE_POSITIVE` from any open state. Investigate
  and escalate also acknowledge, if nobody had. A closed event (`RESOLVED`,
  `FALSE_POSITIVE`) cannot be decided again, and a second acknowledgement is
  refused: the **409** names who acted first. The check is repeated inside the
  update, so two operators cannot both win. A false positive requires a reason.
- Filters: sessions by site, drone, mission, status and `from`/`to`; events by
  site, session, drone, status, `open_only`, `risk_level` and `from`/`to`.

## Platform — licensing (Super Admin)

| Method | Path | Permission | Gated |
|---|---|---|:-:|
| GET | `/platform/tenants/{tenant_id}/drone-license` | `license:manage` | |
| PUT | `/platform/tenants/{tenant_id}/drone-license` | `license:manage` | |

`license:manage` is held by Super Admin only and is MFA-gated: a platform owner
not enrolled in 2FA is refused. The body is `is_enabled`, `expires_at`,
`max_drones`, `max_missions`, `max_sites` (null = unlimited) and `notes`. An
enabled licence cannot already have expired. The platform tenant itself is
refused — the platform owner licenses the module; it does not run patrols. The
change is written to the **customer's** audit log.

The entitlement lives in `drone_module_licenses`, not `tenant_module_licenses`;
see `DRONE_PATROL_GAP_ANALYSIS.md` §19.1 for why.
