# GPS fleet tracking and vehicle management — design

Agreed with the owner on 14 October 2026, one part at a time, from the brief
*Seventh AI Vision — GPS Fleet Tracking and Vehicle Management Upgrade*. This
upgrades the existing GPS module (`/api/v1/gps`, the web page `/gps`, the
phone's GPS screen); it does not add a second one.

## 1. What was there (inspection, 14 October 2026)

- Two vehicle lists exist and stay separate: the **fleet** (`vehicles`,
  migration 0024) and the **plate register** for cameras and barriers
  (`watchlist_entries`, 0076). This work is about the fleet.
- The vehicle form is the GPS page's *Add Vehicle* dialog: name, type, plate,
  make, model, colour. "Custom" is a vehicle type; vehicles have no custom
  fields (only visitor forms do).
- **Why an administrator could not delete a vehicle:** the API has
  `DELETE /gps/vehicles/{id}` (a soft delete, `gps:write`: Admin, Supervisor,
  Manager), but the web page has no edit or delete control and the web client
  has no call for either; the phone is read-only. The endpoint answers "ok"
  for a vehicle that does not exist and writes no audit line. Super Admin has
  no `gps:*` permission, by design.
- A hard delete would cascade: `vehicle_journeys` and `geofence_events` are
  `ON DELETE CASCADE`; `vehicle_positions` has no foreign key and would be
  left orphaned.
- Positions can only be sent with a person's sign-in token (`gps:ingest`);
  there is no device credential, provider or adapter. The development
  database holds one vehicle and no position.
- `current_status` is set to moving/idle by each position and never returns
  to offline (`IDLE_TIMEOUT_MIN` is unused). `recorded_at` is the server's
  time, not the device's.
- The web map is a grid of dots, although Leaflet is installed and used by
  four other screens with a configurable tile source (`VITE_MAP_TILE_URL`).
- Journeys start and end on a speed threshold per position, with wall-clock
  averages; no stops, playback or export. Positions are partitioned monthly
  and kept for ever; they are outside the retention module.
- Geofences are polygons only; every vehicle is checked against every active
  geofence of the organisation on each position, against only the previous
  position: boundary jitter produces an alert per wobble and overspeed one per
  position. No dwell rule, recipients or offline/stale alert.
- GPS alerts already reach the command centre (`alerts`, module `gps`) and the
  AI layer reads them as `SENSOR`.
- The GPS router ignores the per-user site limit (`user_sites`,
  `site_scope_clause`) that other modules apply.
- Tests: `test_gps.py` (21) and `test_gps_features.py` (6).

## 2. How positions arrive (part 1)

- **Traccar** (open source, 200+ tracker protocols) runs as a container in
  our stack under a Compose profile, so it does not start with the seven core
  services on the 7.7 GB development PC. Its data is a separate `traccar`
  database in the existing Postgres. Only port **5055** (OsmAnd: the free
  *Traccar Client* phone app, for testing) is opened for trackers; its web UI
  is bound to localhost.
- Traccar **forwards each position as JSON** (`forward.type=json`,
  `forward.url`, `forward.header`, retries on) to a new API endpoint. The
  platform secret in the header is checked in constant time; anything else is
  refused.
- One Traccar serves every organisation. The device's `uniqueId` is looked up
  by a narrow `SECURITY DEFINER` function that answers only "which vehicle and
  organisation own this device"; the rest runs under that organisation's RLS.
  A device ID belongs to one vehicle on the whole platform. Unknown devices are
  counted and logged, never stored.
- The device's **fix time** is used (`fixTime`), speed converted from knots to
  km/h, ignition, accuracy, heading and satellites kept where sent. Impossible
  coordinates, 0,0, a fix more than a few minutes in the future and a
  duplicate are rejected with a recorded reason. A late older position is
  stored in its place and never moves the vehicle's last known position back.
- A **provider adapter** interface: check credentials, list devices, accept a
  forwarded position, fetch a time range. Traccar is the first adapter; an
  organisation may add its own Traccar or another provider, with credentials
  encrypted by `app.core.crypto.encrypt_secret` and never sent to a browser.
- Whether Traccar's API accepts a program's sign-in reliably is tested against
  a real Traccar before the vehicle form registers devices in it; otherwise a
  device is added once in Traccar and only linked here.

## 3. Status, vehicles and archive (part 2)

- **Tracking status** from the last fix time, worked out when read:
  *Not configured* (tracking off or no device), *Online* (< 5 min), *Stale*
  (5–30 min), *Offline* (> 30 min or never). Both limits are organisation
  settings. Moving/idle only while Online. A once-a-minute job records changes,
  raises offline/stale alerts and keeps `current_status` honest for the phone.
- **Pages** (upgrading `/gps`): fleet dashboard; vehicle list with search,
  filters, server-side sort and paging; vehicle details with Details, GPS
  device, Journeys, Events and History tabs.
- **Form:** registration number (required for new vehicles, unique per
  organisation by normalised plate), name, make, model, colour, type, site,
  department, driver (a user, or a name and phone), service status (in
  service, maintenance, out of service), notes, organisation-defined **custom
  fields** (text, number, date, choice, yes/no) like visitor forms. A
  migration that finds duplicate plates stops and lists them.
- **Delete becomes Archive:** confirmation names plate and name and asks a
  reason; the vehicle leaves lists and the map, its device is released, its
  history stays, it can be restored; every create, edit, archive, restore and
  device change is audited. The existing endpoint archives with an audit line
  and answers 404 for no vehicle.
- **No permanent-delete button.** Retention removes an archived vehicle and
  its history only after an organisation-set period (at least 30 days), with
  no open alert, incident, case or hold on it.
- The cascades from `vehicle_journeys` and `geofence_events` to `vehicles`
  become `RESTRICT` (approved by the owner: a change to existing schema).
- Site limits apply; a vehicle without a site is seen only by people with no
  site limit.

## 4. Map, journeys and playback (part 3)

- The existing Leaflet and tile setting. OpenStreetMap's public tiles are for
  development and demos only (usage policy); production sets its own tile
  source — documented, no paid dependency chosen.
- Markers by type, plate label, status colour, heading arrow; Stale faint;
  Offline hollow at its last known place with its age; Not configured only in
  the list. Search, filters, detail panel, Focus.
- **Live updates** carry only which vehicles moved (the WebSocket fan-out is
  organisation-wide, so coordinates on it would show a site-limited person
  other sites' vehicles); screens fetch through their site-filtered request at
  most every 2 s, and every 30 s if the socket is down.
- **Journeys** are worked out from stored positions, and only from two or
  more: a stop is staying within ~50 m for 3 min or ignition off; distance is
  summed fix to fix, leaving out fixes that need an impossible speed or are
  too inaccurate, and saying how many. The old live journey writer is replaced;
  its rows are kept.
- **Playback** for up to 7 days: the route (thinned for drawing, never for
  distance), stops with dwell, time slider, play at 1×/10×/60×.
- **Export** of trips, stops and positions as CSV, under its own permission,
  audited.
- One position per vehicle per fix time (unique index including the partition
  key). Retention by organisation setting, removed per organisation.

## 5. Geofences and alerts (part 4)

- Circle (25 m – 50 km) or polygon (3–200 points, not self-crossing), using
  the attendance geofence maths. Organisation, optional site; "start from the
  site's boundary" copies it. Applies to the site's vehicles or chosen
  **vehicle groups** (new). Enable, disable, archive.
- Rules: entry, exit, inside longer than N min, outside longer than N min,
  overspeed inside; each with severity and recipients (users or roles, through
  their existing notification settings).
- **Jitter protection:** stored inside/outside state per vehicle and geofence;
  a change needs the fix past the boundary by max(15 m, its accuracy) and the
  new side held for 2 fixes or 30 s; the same alert for the same pair not
  again within 2 min; older positions never change state; an exit after a
  silent gap is recorded as "between X and Y". Overspeed needs reported speed,
  two fixes over, one alert per episode. Dwell is checked by the minute job,
  once per stay.
- Alerts in the existing queue (acknowledge, assign, notes, incident,
  dismiss). Existing codes kept; new ones for dwell, offline, stale. Offline
  alerts on by default for tracked in-service vehicles; stale off; one per
  episode, a note on recovery.
- **A geofence never opens a barrier, admits anybody or confirms who is
  driving** — in code, documents and a test.

## 6. Permissions, evidence, testing, order (part 5)

| Permission | Roles |
|---|---|
| `gps:read` (existing) vehicles and map | as today |
| `gps:write` (existing) register and edit | as today |
| `fleet:device:manage` | Admin, Manager |
| `fleet:vehicle:archive` | Admin, Manager |
| `fleet:geofence:manage` | Admin, Supervisor, Manager |
| `fleet:history:read` | Admin, Supervisor, Operator, Viewer, Manager |
| `fleet:history:export` | Admin, Manager |
| `fleet:alerts:manage` | Admin, Manager |

Super Admin and Client get none. Every endpoint checks the permission and the
site limit.

**Evidence, not identity:** a site geofence entry or exit shows, within ±2 min
(a setting), plate reads of this vehicle's plate (normalised) at the site's
cameras, barrier commands for it, door-access events and open incidents at the
site — each "plate matches" or "near in time only", with the gap and
confidence; never "confirmed"; "none found" said. Worked out when viewed.

**Tests:** every item of the brief's list; the forwarding endpoint and the
minute job tested as `svc_app` with two organisations; Traccar messages
recorded once from a real container and replayed. No simulator in the
product; real test data from the Traccar phone app.

**Order** (each merged after CI, the feature reference updated):
1. Vehicles: edit, archive, audit, plate rule, site limits, custom fields, groups.
2. Traccar container, adapter, forwarding endpoint, device setup, status, minute job.
3. Live map and dashboard.
4. Journeys, playback, export, retention.
5. Geofences, rules, alerts.
6. Camera and plate evidence.

**Settings:** `TRACCAR_FORWARD_SECRET`; Traccar API credentials if program
sign-in works; the existing `VITE_MAP_TILE_URL` / `VITE_MAP_TILE_ATTRIBUTION`
for production. Setup in `docs/FLEET_GPS_PROVIDERS.md`.
