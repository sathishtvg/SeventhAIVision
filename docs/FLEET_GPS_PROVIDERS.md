# GPS fleet tracking: providers and setup

How a fleet vehicle's positions reach Seventh AI Vision, what has to be set up,
and what is and is not done. The design is
`docs/plans/2026-10-14-gps-fleet-tracking-design.md`.

## How a position arrives

```
tracker or phone app ──► Traccar ──(JSON, X-Fleet-Secret)──► POST /api/v1/fleet/ingest/traccar ──► the vehicle
```

- **Traccar** (open source, https://www.traccar.org) receives the trackers'
  own protocols - more than 200 of them - and forwards every position to
  Seventh AI as JSON, with a secret header and retries if the server is
  briefly unreachable.
- The position carries the **device ID** (Traccar's `uniqueId`: a tracker's
  IMEI, or the ID the phone app shows). That ID decides which vehicle, and
  which organisation, the position belongs to. A device ID is one vehicle's on
  the whole platform. A position from a device nobody has linked is counted
  and not stored.
- The **device's own fix time** is kept, not the time the server heard of it.
  Speed arrives in knots and is kept in km/h. One position is kept per
  vehicle and fix time; a late, older one is stored in its place and does not
  move the vehicle back.
- Refused, with the reason counted on the vehicle's GPS device page: no
  device ID, coordinates out of range, exactly 0,0, no fix time, a fix more
  than five minutes in the future, a fix Traccar marks as not valid, a
  position sent twice, tracking switched off, an archived vehicle.

Checked on 15 October 2026 with Traccar 6.16.0: a vehicle given a device on
its GPS device page, the device registered in Traccar by Seventh AI, one
position sent the way the Traccar Client phone app sends it, and the vehicle
read back Online at that place and time.

## Supported providers

| Provider | How it reports | What Seventh AI needs |
|---|---|---|
| The platform's Traccar (`traccar` service in the Docker stack) | forwards every position to `/api/v1/fleet/ingest/traccar` | `TRACCAR_FORWARD_SECRET`; for registering devices and testing them, `TRACCAR_URL` and a token or user and password |
| An organisation's own Traccar (Fleet → Providers) | forwards to `/api/v1/fleet/ingest/traccar/<connection id>` with the connection's own secret | its address, and a token or user and password, kept encrypted |

Adding another provider means writing one module with the five steps in
`backend/app/services/fleet_providers/__init__.py` (test, device,
register_device, parse_forward, fetch_range) and naming it there. None exists
yet beyond Traccar.

## Setting up the platform's Traccar

1. Choose a long random forwarding secret and put it in `.env` as
   `TRACCAR_FORWARD_SECRET`. The same value goes to the API and, through
   Compose, to Traccar's `FORWARD_HEADER`. Set `TRACCAR_DB_PASSWORD` too.
2. Start it - it is not one of the core services, so on a small machine run it
   only when vehicles are tracked:

   ```
   docker compose --profile fleet up -d --no-deps traccar-db traccar
   ```

   `traccar-db` makes Traccar's own database (`traccar`) in the platform's
   Postgres once. Traccar's heap is capped at 256 MB (the container at 640 MB).
3. Open Traccar's own screen on the server itself, http://127.0.0.1:8082, and
   make its administrator: on an empty Traccar the first account made is the
   administrator. Keep that password in your password manager.
4. So that Seventh AI can register devices in Traccar and test them, give the
   API a Traccar sign-in: `TRACCAR_URL=http://traccar:8082` and either
   `TRACCAR_API_TOKEN` (made in Traccar under the account's settings) or
   `TRACCAR_API_USER` and `TRACCAR_API_PASSWORD`. Without them, devices are
   added in Traccar's own screen and only linked in Seventh AI.
5. Restart the API so it reads the new settings.

Then, for each vehicle, on its **GPS device** tab: type the device ID, switch
tracking on, and tick "Register it in Traccar".

## Testing with a phone (no tracker needed)

1. Install **Traccar Client** (Android or iPhone) on a phone.
2. In it, set the server address to `http://<this server's address>:5055`
   and note the device ID it shows.
3. Give that ID to a test vehicle on its GPS device tab, with tracking on.
4. Start the app's service. The vehicle turns Online when the first position
   arrives.

The phone must reach port 5055 on the server: on Windows, the firewall has to
allow it. That setting is the server owner's to change.

## When a vehicle is Online, Stale or Offline

Worked out from the time of the last fix whenever it is read:

| Status | When |
|---|---|
| Not configured | tracking is off, or no device is linked |
| Online | the last fix is newer than 5 minutes |
| Stale | older than that, newer than 30 minutes |
| Offline | older than that, or nothing has ever arrived |

Each organisation can change the two limits (Fleet → Providers, "When a
vehicle counts as stale or offline"). A once-a-minute job records each change
once. A vehicle is never shown as live without a recent fix.

## The live map

Fleet → Live map (`/gps/map`) shows each vehicle that has reported at its last
fix: Online solid, Stale faint, Offline a hollow ring at its last known place
with its age. A vehicle that has never reported is listed and not drawn. The
status is worked out against the clock on the screen too, so a vehicle that
stops reporting goes faint without anything being fetched.

The live connection tells the screens only which vehicles moved, never where:
it reaches everybody in the organisation, and somebody limited to sites must
not learn another site's vehicle's position from it. The screen then asks for
those vehicles through its own site-limited request (`GET /api/v1/fleet/map?ids=`),
at most every two seconds; with the connection down it asks for everything
every 30 seconds.

**Map tiles.** The map uses the platform's tile setting, the same as every other
map: `VITE_MAP_TILE_URL` and `VITE_MAP_TILE_ATTRIBUTION` when the web app is
built. Left unset it uses OpenStreetMap's public tile server, which is fine for
development and demonstrations, but whose usage policy does not allow heavy use
such as a fleet map open all day in a control room. For production set a tile
service of your own: a paid one with a free allowance (MapTiler, Stadia Maps,
Thunderforest and others, each with its own key, limits and attribution) or a
tile server inside your own network. None is chosen or paid for here.

## Journeys and playback

Fleet → Journeys (`/gps/history`), or Journeys on a vehicle's page, shows one
vehicle over a range of at most seven days: its route, its trips and stops, the
distance driven and the time spent driving and stopped. Nothing is stored for
this: the trips and stops are worked out from the kept positions each time the
page asks (`GET /api/v1/fleet/vehicles/{id}/journeys?start=&end=`), so a
change to the rules below applies to the past as well.

- A position the device itself reports as worse than 100 m accurate, or one
  that would mean moving faster than 250 km/h from the one before, is left out,
  and the page says how many were left out and why.
- A stop is three minutes or more within 50 m, or the ignition off. A trip is
  the driving between stops.
- No position for more than 15 minutes ends a trip, and the page says so. The
  distance across such a gap is not counted, because nobody knows the way the
  vehicle went.
- A long route is drawn with at most 5,000 points (the shape is kept); the
  trips, stops and distance always use every kept position.

**Playback** moves a marker along the route at 1, 10 or 60 times real
speed. The marker jumps from fix to fix: it is always at a place the vehicle
reported, never at a place worked out between two.

**Export.** Whoever has `fleet:history:export` (Administrator and Manager
to begin with) can download the trips, the stops or the kept positions as a
CSV file (`GET /api/v1/fleet/vehicles/{id}/journeys.csv?kind=trips|stops|positions`).
Every export is a line in the audit log (`fleet.history.exported`) with the
range and the number of rows. Reading journeys needs `fleet:history:read`, and
somebody limited to sites sees only those sites' vehicles.

## Geofences and alerts

Fleet → Geofences (`/gps/geofences`) draws the places the vehicles are watched
against: a circle of 25 m to 50 km, a polygon of 3 to 200 corners whose edges
do not cross, or a site's own boundary as it is set on the site. A geofence
belongs to the organisation or to one of its sites, and applies to the vehicle
groups it names, or else to every vehicle of its site.

**A fix that wobbles on the line changes nothing.** Each vehicle's side of each
geofence is kept. A fix counts for a side only when it is past the boundary by
at least 15 m, or by the accuracy the device reported when that is worse. The
side changes when a second fix agrees, or when 30 seconds pass with no fix
saying otherwise; the change is dated at the first fix on the new side. A
position sent late never changes the side. When nothing was heard for more
than 15 minutes across a change, it is recorded as having happened between the
last fix on the old side and the first on the new. The first fix after a
geofence is drawn says which side a vehicle is on and raises nothing.

**Rules**, one of each kind per geofence, each with its severity and the people
or roles told: enters, leaves, inside longer than N minutes, outside longer than
N minutes, faster than N km/h inside. Every entry and exit is recorded; an alert
is raised only where a rule asks, and the same alert for the same vehicle and
geofence is not raised again within 2 minutes. Overspeed uses the speed the
device reported, over the limit at two fixes running, once an episode. A stay
is counted up to the last fix heard, by the minute job, once a stay.

**Alerts go to the platform's one alert queue** (module GPS), to be
acknowledged, assigned, noted, made an incident or dismissed there. The codes
the older position endpoint raised are kept (`gps.entry`, `gps.exit`,
`gps.speed_violation`); `gps.dwell_inside`, `gps.dwell_outside`, `gps.offline`
and `gps.stale` are new. The organisation's notification rules apply to them as
to any alert, and a high or critical one is pushed to the phones as every high
alert is. A rule's own people and roles are pushed to as well when the alert is
below high, but only those who may read vehicles and whose sites include the
vehicle's.

**When a vehicle stops reporting**: a tracked vehicle in service raises one
alert when it counts as offline (on until switched off) and, if switched on,
one when it counts as stale. When it reports again a note saying so is added to
those alerts; they are not closed for anybody. Set under Fleet → Providers by
whoever may set the limits.

Changing a geofence's shape, site or groups, or switching it off, makes every
vehicle's next fix say again which side it is on, without an alert. A geofence
is archived with a reason, never deleted; its events stay readable. Somebody
limited to sites draws and changes their own sites' geofences only, and sees
their sites' vehicles' events only.

**A geofence never opens a barrier, admits anybody or confirms who is
driving.** It records where a vehicle's tracker said the vehicle was and
raises an alert for a person to look at.

## How long positions are kept

Positions are kept until the organisation sets a period, under Fleet →
Providers. There are two periods, each at least 30 days:

- **Fleet vehicle positions**, counted from the fix time. The journeys worked
  out from them go with them; the vehicle stays.
- **Archived fleet vehicles**, counted from when the vehicle was archived.
  The vehicle goes with its positions, journeys, geofence events, status
  history and group memberships. A vehicle that an open or acknowledged GPS
  alert, or an incident from one that is not closed, refers to is kept
  whatever its age. The alerts and incidents themselves are not touched.

Setting a period removes nothing at once: the scheduler removes once a day.
When records are already older than the period asked for, the first answer
says how many, and the period is set only when it is asked for again. A period
is set by somebody who may change the organisation's settings and is not held
to particular sites. The fleet's periods appear in the organisation's retention
statement with the others.

## Secrets

- The forwarding secret, a connection's token or password, and the platform's
  Traccar sign-in are never sent to a browser, written in a log, or kept in the
  browser. A connection's password or token is encrypted with the platform's
  key; its forwarding secret is kept only as a hash and shown once, when it is
  made or replaced.
- The device register is read and written only through four database
  functions; the application's own database role cannot read it.
- A provider's address may not be this server itself or a cloud metadata
  address.

## Not done yet

- Only Traccar. No commercial provider's API is connected.
- The Seventh AI phone app does not report its own position as a tracker;
  for a phone, use Traccar Client.
- Plate reads, barrier commands and door events near a geofence entry or exit
  are not shown beside it yet. That is phase 6 of the design.
- The older position endpoint (`POST /api/v1/gps/vehicles/{id}/positions`,
  signed in as a user) still writes its own rows to `vehicle_journeys` by its
  old speed rule. No screen reads them: Journeys are worked out from the
  positions instead.
- Traccar's own screen and API are reachable from the server only; to manage
  it from elsewhere, put it behind the platform's TLS proxy first.
