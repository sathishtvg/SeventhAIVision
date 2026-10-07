# The Security Map — Architecture

**Phase 3 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-07, migration `0145`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase the platform had a map of its sites, each with a count of
cameras and guards, and separate maps inside the drone and vehicle screens. It
knew where cameras, checkpoints, drones and incidents were and showed them
nowhere together. It did not know a site's buildings, floors, gates or doors
at all.

```
 what the platform already      ┌──────────────────────────────┐
 knows the position of    ────► │  ONE MAP, IN LAYERS          │ ──► select an incident:
                                │  each layer under its own    │     WHAT IS NEAR IT
 what an administrator    ────► │  screen's permission and     │     nearest guard first,
 draws: the PLACES of a site    │  the reader's sites          │     by last recorded position
                                └──────────────────────────────┘
```

---

## 1. The rules

1. **A layer is shown to someone who may already read what is on it.** The
   permission is the one that thing's own screen asks for. The map gives nobody
   a camera, an incident or a colleague's whereabouts they could not see
   elsewhere, and every layer is held to the reader's sites.
2. **A thing with no position is counted, not hidden.** An incident whose
   camera was never given coordinates cannot be drawn. `without_position` says
   how many there are on each layer, so that an empty patch of map is not read
   as a quiet one. A layer that was left out is named, with the reason.
3. **A guard's position is not live** (owner decision E3). It is where they
   last clocked in, scanned a checkpoint, changed an incident's status, wrote
   an occurrence-book entry or raised an emergency this shift, and it always
   says which and how long ago. One over an hour old is marked stale.
4. **The map acts on nothing.** `around` lists what is near an incident for a
   person to read. Nobody is sent anywhere by it.
5. **Places are optional** (owner decision E4). A site with none works exactly
   as it did.
6. **A place is retired, not removed.** The application's database role cannot
   delete one.

---

## 2. The layers

`backend/app/services/security_map.py`. Each is read where it already lives.

| Layer | What is drawn | Position from | Permission | State |
|---|---|---|---|---|
| `SITE` | Every active site | The site | `site:read` | `attention` when a camera is offline |
| `CAMERA` | Each active camera | The camera | `camera:read` | Its latest stream's status |
| `GUARD` | Each guard clocked in now | Their last recorded position (§3) | `shift:read` | `available`, `busy`, `emergency` |
| `INCIDENT` | Open incidents in the period | Where a guard reported from, else its camera | `incident:read` | Its severity |
| `ALERT` | Live alerts in the period | Its camera | `alert:read` | Its severity |
| `SITUATION` | Open situations in the period | Its own, else its first camera | `intel:read` | Its risk level |
| `DRONE` | Each drone | Where it last reported being | `drone:read` | Its status |
| `CHECKPOINT` | Patrol checkpoints | The checkpoint | `patrol:read` | Whether it has ever been scanned |
| `PLACE` | The places drawn for a site (§5) | The place | `sitemap:read` | Its kind — or `alert` (below) |
| `DRONE_ZONE` | Zones drawn for drone patrols | The zone | `drone:read` | Its type |

The three live layers look back 24 hours unless told, at most 168. A layer
returns at most 500 things and says when there were more.

**A door on the map.** An access point or gate may name a door the platform
already knows. For a reader holding `access:read` it then carries that door's
last event, and is in the state `alert` when that event was forced, held open
or tampered with in the last 15 minutes. For anyone else the place is on the
map and its door says nothing.

**Not drawn.** Alarm panels and sensors have no position of their own in the
platform — only a site — and are on their own screens.

---

## 3. A guard's position

`backend/app/services/guard_positions.py`, shared with the dispatch
recommendation of phase 4 so that the two cannot disagree. For each guard
clocked in now, the most recent of:

| Recorded when the guard | Read from |
|---|---|
| scanned a patrol checkpoint | `checkpoint_scans` |
| changed an incident's status | `incident_status_history` |
| wrote an occurrence-book entry | `occurrence_book_entries` |
| raised an emergency that is still live | `man_down_events` |
| clocked in | `shifts` |

— taken since the shift began, with `position_source`, `position_at`,
`position_age_s` and `stale`. A guard who recorded no position this shift is
on shift, is counted under `without_position`, and is not drawn. The phone is
not asked where it is.

---

## 4. What is near

`GET /api/v1/site-map/around?kind=INCIDENT|ALERT|SITUATION&id=`. Within the
radius (300 m unless given, at most 5000): cameras, drones, patrol checkpoints
and places, nearest first, each with its distance. And **every guard on shift
at that site**, however far, ranked: free before already sent somewhere,
nearer before farther, those with no recorded position last.

When the thing itself has no position the answer says `located: false`: the
guards on shift at its site are still listed, without a distance.

It is a reading. Dispatch remains the existing dispatch, done by a person.

---

## 5. The places of a site

`site_places`: the site, a kind, a name, and a point, an outline, or — for a
floor — the building it is a level of.

| Kind | Typically |
|---|---|
| `BUILDING` | An outline. Floors, gates and access points can be part of one |
| `FLOOR` | A level of a building; needs no shape of its own |
| `GATE`, `ACCESS_POINT` | A point; may name a door |
| `EMERGENCY_POINT`, `ASSEMBLY_POINT` | A point |
| `ZONE`, `PARKING`, `OTHER` | An outline or a point |

A place is part of a building or of nothing. A door is at one place. Two
active places of one kind at a site are not called the same thing. An outline
is three to 200 points. What a place is, and which site it is at, do not
change: it is retired and another drawn.

---

## 6. API

All under `/api/v1/site-map` and all need `sitemap:read`.

| | | Also needs |
|---|---|---|
| `GET` | `/layers` | |
| `GET` | `/features` | |
| `GET` | `/around` | |
| `GET` | `/places` | |
| `POST` | `/places` | `sitemap:manage` |
| `PATCH` | `/places/{id}` | `sitemap:manage` |
| `POST` | `/places/{id}/retire` | `sitemap:manage` |
| `POST` | `/places/{id}/restore` | `sitemap:manage` |

There is no `DELETE`. Reading the map writes nothing, and is not audited. A
change to a place is made by a signed-in person — not an API key, not a
support session — and is audited: `sitemap.place.create`,
`sitemap.place.update`, `sitemap.place.retire`, `sitemap.place.restore`.

**Permissions** (migration `0145`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Viewer 6 | Guard 5 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `sitemap:read` | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – |
| `sitemap:manage` | ✓ | ✓ | – | – | – | – | – | – |

A guard does not hold `sitemap:read`: the map shows where colleagues last
recorded their position.

---

## 7. Screens

- **Security Map** (`/security-map`), under Monitoring — the map; a chip per
  layer, turned on and off; the important marks named on the map itself, a
  guard's with the age of the position; things at one spot gathered into one
  mark with a count; under the map, what could not be drawn and why. Selecting
  an incident, an alert or a situation opens what is near it.
- **Site Places** (`/site-places`), under Sites & Devices — a site's places,
  drawing one on a map, changing, retiring and restoring.

The existing Site Map (`/map`) is unchanged. The tiles are the same
configurable ones (`VITE_MAP_TILE_URL`), so an installation without internet
points at a tile server of its own. The phone app is not changed.

---

## 8. Files

| | |
|---|---|
| `backend/alembic/versions/0145_site_places.py` | One table, its policy and grants, two permissions |
| `backend/app/services/guard_positions.py` | Where each guard on shift last recorded being |
| `backend/app/services/security_map.py` | The layers, and what is near |
| `backend/app/routers/site_map.py` | The API |
| `frontend/src/api/siteMap.ts` | The typed client |
| `frontend/src/pages/securityMap/` | The two screens |
| `frontend/src/components/securityMap/` | Their shared wording and colours |

Existing files changed, by additions only: `backend/app/main.py` (the router
is registered), `frontend/src/App.tsx` (two routes),
`frontend/src/components/layout/Sidebar.tsx` (two menu entries),
`frontend/src/hooks/usePermission.ts` (the two permissions).

---

## 9. Tests

| | |
|---|---|
| `backend/tests/test_security_map.py` | Outlines; the last recorded position and who is nearest; every layer through the API; what is counted for want of a position; a guard's position and its age; what is near; who may see which layer, at which sites, in which organisation; places drawn, changed, retired; what the database refuses |
| `backend/tests/test_security_map_docs.py` | That this document says what the code does |
| `frontend/src/pages/securityMap/securityMap.test.tsx` | The two screens |

---

## 10. What this does not do

- **It does not track anybody.** No position is reported by a phone. A guard
  who has recorded nothing since clocking in is shown where they clocked in,
  marked stale after an hour.
- **It is not an indoor map.** A floor is a level of a building and has no
  drawing of its own; there are no floor plans.
- **It does not route.** Distances are straight lines between two positions,
  not a walking route.
- **It does not place alarm panels or sensors**, which have no position.
- **It does not dispatch**, suggest a dispatch, or change anything it shows.
- **It has been exercised on test data, and looked at with made-up data.** On
  the development database nine of eleven active cameras have a position and
  some fourteen hundred incidents are open at a handful of them — which is why
  things at one spot are gathered into one mark with a count, and why the live
  layers look back a day unless asked. It has not been run on a real site.
