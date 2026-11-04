
# Phase 5 of the known limits: a drone's picture blanked over privacy areas

Date: 4 November 2026. Agreed with the owner in three parts; the programme is in
`2026-11-03-known-limits-programme-and-sg-payroll-design.md`.

The known limit it closes: *Drone camera feeds are not masked* (PRIVACY_MASKING.md). A fixed camera's zone is drawn
on its picture; a drone's picture moves with the drone, so a zone on it means nothing.

## Owner decisions

- **Areas on the map, and the whole picture blanked** while the drone is at one - not a mask projected into the
  frame, which needs calibrated cameras and angles nobody has here.
- **Planning warns and allows:** a route crossing an area is saved and flown; the planner is told which areas and for
  how long the picture will be blank.

## 1. Privacy areas

- Shapes on the site map (the Drone Zones screen), drawn and deleted - never edited - by `privacy:manage`, the right
  that draws a fixed camera's zones. Each has a margin, 25 m by default (0 to 200 m), so a camera looking sideways
  near the edge is covered. Each change is audited.
- **At an area:** the drone's last reported position is inside it or within its margin. Height does not matter. A
  position not known, or older than 10 seconds, counts as at one: it fails safe.
- **Blanked while at one:** the drone's live video, the frames the AI is given and its recordings - the whole picture
  painted solid, "Picture withheld: privacy area" - at the places a fixed camera is masked, so no unblanked copy is
  made. A snapshot from the site's gateway taken at one is stored blank; a clip that overlaps a time at one is not
  kept, and a note says one was withheld and why.
- The areas are read before a drone's camera gives a frame; a later read that fails keeps the last that succeeded.

## 2. Planning, screens, the AI

- Saving a route or a mission works out the areas its path crosses (with margins) and for about how long; the planner
  is told, and it is saved. The mission's page says it too.
- The Drone Zones map shows the areas hatched grey with their margin. The live view shows the withheld picture with
  the drone's position, height and battery. A withheld snapshot or clip is said to be one on the event and in
  reports.
- The AI sees nothing over an area. An event the gateway reports from one keeps its time and place and no picture.
- Outside the platform: the drone's own controller and anything recorded on the aircraft.

## 3. Testing and what changes

Tests: blanked inside, within the margin, and with the position unknown or stale; not elsewhere; the last good read
kept; a snapshot stored blank, a clip withheld with its note; a route's warning with names and seconds, saved; only
`privacy:manage` draws; two organisations as the application's role. Web: the map, the warning, a withheld snapshot
and clip.

Migration 0179: `drone_privacy_areas`, `drone_media_withheld`.

Existing files changed (owner approved): `services/privacy_mask.py` (its keeper adds the whole picture for a drone's
camera at an area; a fixed camera's zones unchanged), the gateway's media upload (`services/drone_edge_sync.py`,
`routers/drone_edge.py`), `routers/drone_planning.py`, `routers/drone_operations.py`, `services/drone_reports.py`,
`app/main.py`, the drone screens and their API file, `PRIVACY_MASKING.md` and the drone documents with their checks.
Not touched: a fixed camera's zones, flight tracks, missions themselves, the phone. Proven on the simulator only: no
aircraft is connected.
