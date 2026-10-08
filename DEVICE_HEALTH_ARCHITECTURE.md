# Device Health, Assets and Maintenance — Architecture

**Phase 8 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-08, migration `0150`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase each kind of device said how it was on its own screen and in
its own words: a camera's stream was online or offline, a recorder had the
result of its last probe, a sensor had the time of its last reading, a drone
and a gateway had a heartbeat, an alarm panel had the last thing it reported.
Nothing read them together, nothing remembered how a device had been, there was
no register of what the organisation owns, and the only maintenance was a
facility defect a guard could report and a drone's service log.

```
 a DEVICE the platform knows ──► its HEALTH, read from what it reports
        │                               │  every 5 minutes; kept only when it CHANGES
        ▼                               ▼
 an ASSET in the register      down for long (if asked for), or a SCHEDULE falls due
 make, serial, vendor, warranty         │
        │                               ▼
        └──────────►  a WORK ORDER:  SUGGESTED ─► a person accepts ─► OPEN ─► IN PROGRESS ─► DONE
                                              └─► or dismisses it, with why
```

---

## 1. The rules

1. **A reading is made of what the platform is told, and of nothing else.**
   Frame rate, latency, packet loss, the quality of the picture and gaps in a
   recording are measured by nothing here. Every answer that carries a reading
   carries that list (`not_measured`), and every screen that shows a reading
   shows it.
2. **What is not known is `NOT_KNOWN`, not `OK`.** A camera with no stream, a
   recorder never probed or not probed for a day, and a sensor, drone, gateway
   or panel that has never reported are not called working.
3. **A sensor that reads a dangerous value is a working sensor.** Its health is
   whether its readings arrive, not what they say.
4. **The platform suggests; a person raises the work.** A work order the
   platform put forward is a row in a list until somebody who manages
   maintenance accepts it or dismisses it with a reason. It assigns nobody and
   tells nobody. The database holds this: a suggested order cannot be open, in
   progress or done without who accepted it.
5. **Suggestions from health are off until an organisation asks for them.** A
   schedule is itself the asking, so what a schedule puts forward needs no
   switch.
6. **A suggestion says what it was made of**: the state that was read, since
   when, and why. It says nothing about what is wrong with the device, which
   nothing here knows.
7. **An asset is a record, not a switch.** Nothing here changes a camera, a
   recorder, a sensor, a drone, a gateway or a panel. A facility defect is not
   changed by an order raised for it.
8. **Nothing is removed.** An asset is retired, a schedule is switched off, an
   order is cancelled or dismissed; each is kept. An order that is over is not
   changed — a trigger refuses it.
9. **Each change is made by a signed-in person** — not an API key, not a
   support session — and is audited.
10. **Super Admin, a guard and the client role hold none of the new
    permissions.**

---

## 2. A reading

Each device is in one of five states:

| State | Means |
|---|---|
| `OK` | What it reports says it is working |
| `DEGRADED` | It is working, and something it reports is not right |
| `DOWN` | What it reports — or its silence — says it is not working |
| `NOT_KNOWN` | Nothing says how it is |
| `OFF` | Switched off, disabled or in maintenance on purpose |

How each kind is read (`services/device_health.py`):

| Kind | Read from | `DOWN` | `DEGRADED` | `NOT_KNOWN` |
|---|---|---|---|---|
| `CAMERA` | Its streams and `camera_health_events` | Every stream is offline | A stream is degraded, or it disconnected 5 times or more in 24 hours | It has no stream |
| `NVR` | The last probe | The last probe failed | — | Never probed, or not probed for more than a day |
| `SENSOR` | When its last reading arrived | No reading for more than twice its expected interval | — | No reading has ever arrived |
| `DRONE` | Its heartbeat, the state it reports, its parts | No heartbeat in time; or it reports offline, communication lost or critical | It reports a warning; or a part reports a warning or a fault | It has never reported |
| `EDGE_GATEWAY` | When it was last seen, the state it reports | No heartbeat in time; or it reports offline | It reports degraded | Never seen; or it reports unknown |
| `ALARM_PANEL` | The last thing it reported | It reported itself offline | — | It has never been in contact |

An alarm panel says nothing while nothing happens, so a quiet panel is not read
as down: how long ago it was last in contact is given with it. A drone's
battery is given as a figure; no threshold here turns it into a state. A
recorder belongs to the organisation and not to a site, so somebody held to
particular sites is not shown one.

With each reading come the facts it was made of and **since when**: for a
camera that is down, its own record of going offline; otherwise when that
state was first read — and when that is the first thing ever kept of the
device, the answer says so (`since_is_when_first_read`), because the state may
be older than the platform's looking.

---

## 3. What is kept

Every 5 minutes the scheduler reads every device of every organisation and
keeps a row in `device_health_changes` for each whose state is not the one last
kept. The log takes no update and no delete. From it:

- the states a device has been in, newest first;
- how long it was down in the last 7 and 30 days, and in how many outages —
  counted only from the first reading that was kept, and said to be so.

An outage shorter than the 5 minutes between two looks can pass unrecorded,
and the answer says that too.

---

## 4. The asset register

An asset (`asset_register`) has a code it is cited by — `AST-0001`, numbered
per organisation — a kind, a name, a site or none, and what a register keeps:
make, model, serial number, where it is, vendor, the day it was installed, the
day its warranty ends, a status and notes.

An asset may be **the device it is**: a camera, recorder, sensor, drone,
gateway or alarm panel the platform knows. Then it has that device's health. A
device is one asset (a unique index per kind), and a camera in the register is
the asset of a camera (`ck_asset_device_kind`). An asset of any other kind — a
server, an access controller, a UPS, network equipment — has no reading, and
is shown as having none rather than as working.

Devices the platform already knows are put into the register in one step
(`POST /register-devices`), each as it is known: its name, its site, and its
make, model and serial number where the device has them. The rest is for a
person to fill in.

Its code and its kind do not change. It is retired with a reason and may be
restored; it is never removed, and deleting the device leaves the asset.

The table is `asset_register` and not `security_assets`, as the plan had it:
tables named `security_…` are the intelligence layer's own, and that layer's
tests say what the application may do to every table so named.

---

## 5. Work orders

A work order (`maintenance_work_orders`) has a number — `WO-0001` — a kind of
work (`CORRECTIVE`, `PREVENTIVE`, `INSPECTION`), a priority, and where it came
from:

| Origin | Raised |
|---|---|
| `PERSON` | By hand |
| `DEFECT` | By hand, for a facility defect. The defect is not changed |
| `HEALTH` | Put forward by the platform: a device read as down for long |
| `SCHEDULE` | Put forward by the platform: a schedule falling due |

| State | Means | Then |
|---|---|---|
| `SUGGESTED` | Put forward by the platform. Not work | `OPEN` when accepted, `DISMISSED` with a reason |
| `OPEN` | Work to be done | `IN_PROGRESS`, `DONE`, or `CANCELLED` with a reason |
| `IN_PROGRESS` | Being done | `DONE`, or `CANCELLED` with a reason |
| `DONE` | Done, with what was done | — |
| `CANCELLED` | Not to be done, with why | — |
| `DISMISSED` | A suggestion set aside, with why | — |

Raising, accepting, dismissing, changing and cancelling are for somebody who
manages maintenance. Starting and completing are for whoever has the order, or
somebody who manages maintenance. An order is given to one of the
organisation's people who can read work orders, or to a vendor named in words.
Completing says what was done, and may say what parts were used and for how
many minutes the thing was out of use — as whoever did the work states it. An
order completed without having been started is started and completed at the
same moment.

**From health.** When an organisation has switched it on
(`maintenance.suggest_from_health`), a device read as `DOWN` for
`maintenance.suggest_after_hours` or more (4 unless set; 1 to 168) is put
forward as a corrective order. Each outage is put forward once — the same
outage is not put forward again whatever became of the first, and a device
that comes back and goes again is another outage. A device whose going down
was not recorded is counted from when it was first read as down, and the
suggestion says "since at least".

**From a schedule.** A schedule (`maintenance_schedules`) says something is
done every so many days, on an asset or at a site. So many days before each
date (`lead_days`) the work is put forward, once for that date. When the order
is done, the schedule runs again from that day. When it is dismissed or
cancelled, the schedule moves on to its next date and the order keeps why — a
schedule moved by hand meanwhile is left where the person put it. A schedule
that is switched off, or is for a retired asset, puts nothing forward.

---

## 6. API

Under `/api/v1/security-assets`, all needing `asset:read`:

| | | Also needs |
|---|---|---|
| `GET` | `/health` | |
| `GET` | `/health/{kind}/{id}` | |
| `GET` | `/` | |
| `POST` | `/` | `asset:manage` |
| `GET` | `/unregistered` | `asset:manage` |
| `POST` | `/register-devices` | `asset:manage` |
| `GET` | `/{id}` | |
| `PATCH` | `/{id}` | `asset:manage` |
| `POST` | `/{id}/retire` | `asset:manage` |
| `POST` | `/{id}/restore` | `asset:manage` |

Under `/api/v1/maintenance`, all needing `maintenance:read`:

| | | Also needs |
|---|---|---|
| `GET` | `/work-orders` | |
| `POST` | `/work-orders` | `maintenance:manage` |
| `GET` | `/options` | `maintenance:manage` |
| `GET` | `/work-orders/{id}` | |
| `PATCH` | `/work-orders/{id}` | `maintenance:manage` |
| `POST` | `/work-orders/{id}/accept` | `maintenance:manage` |
| `POST` | `/work-orders/{id}/dismiss` | `maintenance:manage` |
| `POST` | `/work-orders/{id}/start` | |
| `POST` | `/work-orders/{id}/complete` | |
| `POST` | `/work-orders/{id}/cancel` | `maintenance:manage` |
| `GET` | `/schedules` | |
| `POST` | `/schedules` | `maintenance:manage` |
| `PATCH` | `/schedules/{id}` | `maintenance:manage` |
| `GET` | `/settings` | |
| `PUT` | `/settings` | `maintenance:manage` |

There is no `DELETE` under either. Audited: `asset.create`, `asset.update`,
`asset.retire`, `asset.restore`, `asset.register_devices`,
`maintenance.order.raise`, `maintenance.order.accept`,
`maintenance.order.dismiss`, `maintenance.order.update`,
`maintenance.order.start`, `maintenance.order.complete`,
`maintenance.order.cancel`, `maintenance.schedule.create`,
`maintenance.schedule.update`, `maintenance.settings`.

Somebody held to particular sites reads and keeps what is at those sites, and
reads an order they were given wherever it is. The switch for suggestions from
health is for the whole organisation, so it is set by somebody who is not held
to particular sites.

**Permissions** (migration `0150`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `asset:read` | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – |
| `asset:manage` | ✓ | ✓ | ✓ | – | – | – | – | – |
| `maintenance:read` | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – |
| `maintenance:manage` | ✓ | ✓ | ✓ | – | – | – | – | – |

---

## 7. Screens

- **Assets & Maintenance** (`/assets-maintenance`), under Sites & Devices, in
  four parts — **Device health**: every device with its state, why and since
  when, how many are in each state, and what is not measured; one device with
  what was kept of it and how long it was down. **Asset register**: the
  register, narrowed by words, site, kind, status and warranty; add an asset;
  register known devices; one asset with its health or that it has none, and
  the work on it; change, retire, restore. **Work orders**: what is put forward
  first; whether health puts orders forward; raise, accept, dismiss, give to
  somebody, start, complete, cancel. **Schedules**: what is done every so many
  days; add; switch off.

The phone is not changed in this phase. The existing camera, recorder, sensor,
drone, alarm panel, equipment (`/equipment`) and defect (`/defects`) screens
are unchanged.

---

## 8. Files

| | |
|---|---|
| `backend/alembic/versions/0150_security_assets.py` | Four tables, their policies and grants, one trigger, four permissions |
| `backend/app/services/device_health.py` | A reading per kind of device; every device of an organisation; what is kept; time down |
| `backend/app/services/maintenance.py` | What may follow what; what is put forward; the scheduler's pass |
| `backend/app/routers/security_assets.py` | Health and the register |
| `backend/app/routers/maintenance.py` | Work orders, schedules, the switch |
| `frontend/src/api/securityAssets.ts` | The typed client for health and the register |
| `frontend/src/api/maintenance.ts` | The typed client for maintenance |
| `frontend/src/pages/assets/` | The screen |
| `frontend/src/components/assets/` | Its dialogs and shared wording |

Existing files changed, by additions only: `backend/app/main.py` (the two
routers are registered), `backend/app/core/config_keys.py` (the two settings),
`backend/app/scheduler_main.py` (the pass, every 5 minutes),
`frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the four permissions).

No existing table is altered. The new tables refer to `cameras`,
`nvr_connections`, `iot_sensors`, `drones`, `drone_edge_gateways`,
`alarm_panels`, `site_places`, `facility_defects`, `sites` and `users`.

---

## 9. Tests

| | |
|---|---|
| `backend/tests/test_security_assets.py` | A reading for each kind of device; every device and who sees which; what is kept and time down; the register; registering known devices; what the application's role and the database refuse |
| `backend/tests/test_maintenance.py` | What may follow what; raising; doing the work; what a schedule and health put forward; the lists and the switch; that an order that is over is held still |
| `backend/tests/test_device_health_docs.py` | That this document says what the code does |
| `frontend/src/pages/assets/assetsMaintenance.test.tsx` | The screen and its dialogs |

---

## 10. What this does not do

- **It measures nothing new.** Frame rate, latency, packet loss, the quality of
  a picture and gaps in a recording were not measured before this phase and are
  not measured now. The gap analysis listed them as missing; they still are.
- **It does not probe anything.** A recorder is read from its last probe,
  which a person runs; one not probed for a day is `NOT_KNOWN`.
- **It does not see an outage shorter than five minutes**, and knows nothing of
  a device from before the first reading it kept. Time down is not an
  availability figure for a contract.
- **It does not say why a device is down.** No cause is inferred, and a camera
  that keeps disconnecting is not put forward as an order — only shown as
  degraded.
- **It does not raise, assign or tell.** A suggestion sends no notification,
  and accepting one tells nobody either: whoever is given an order finds it in
  their list.
- **It does not change an asset when work is done.** An asset under repair is
  put back in service by a person.
- **It has no parts store, no costs, no vendor contracts and no maintenance
  SLA.** Parts are a line of text on a completed order; a vendor is a name.
- **It does not read drone maintenance logs or guard kit** (`equipment_items`),
  which stay where they were, and it does not turn a facility defect into an
  order by itself.
- **The phone is not part of it.** A technician works an order on the web.
- **It has run on the development organisation's cameras** — whose streams are
  all offline on a development machine — **and on test data for every other
  kind of device**, of which that organisation has none.
