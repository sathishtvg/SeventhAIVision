# Client Demo Runbook

Written for the "aegis" demo tenant on a single modest host. The constraint that
shapes everything here: **this stack does not fit all eleven AI workers in 7.7 GB.**
Pick what the demo needs, run only that, and the machine stays up.

---

## 1. Bring up the demo set

```bash
cd docker
docker compose up -d postgres redis api frontend mediamtx ingestion ai-worker-intrusion
```

Seven services, not nine. What each is for:

| Service | Why it is in the demo set |
|---|---|
| `postgres`, `redis` | Data and the frame stream. Nothing works without them. |
| `api` | The whole backend. |
| `frontend` | Serves the built UI on **http://localhost:5173**. |
| `mediamtx` | Loops sample video over RTSP so cameras have a live picture. |
| `ingestion` | Pulls that RTSP and publishes frames to Redis. |
| `ai-worker-intrusion` | Consumes frames and would raise detections. **Do not count on one appearing during the demo — see §3a.** |

Deliberately left out: `scheduler` (background jobs, invisible in a demo) and the
other ten AI workers.

**Wait for the API before opening the browser.** Cold start loads the face and
liveness models, which takes well over a minute:

```bash
docker compose ps api
```

Proceed when it reads `healthy`, not `health: starting`.

---

## 2. Sign in

    http://localhost:5173
    Organisation   aegis
    Email          ops@aegis.demo
    Password       Demo1234!

Role 2 (Admin) — sees every screen including the two admin screens.

A guard-side login exists for the mobile/duty story: `guard1@aegis.demo`, same password.

---

## 3. A demo path that holds together

Roughly fifteen minutes, ordered so each screen sets up the next.

**Command Centre** — open here. Site cards, KPI row, live alert feed. Click a
card to show it drills through rather than being decoration.

**Live Wall** — the money shot. Add cameras, then **Full Screen**: the app chrome
disappears and the wall goes edge to edge. Worth saying out loud that this is a
real control-room mode, not a browser fullscreen. If a second monitor is
available, use the pop-out button and drag the window across.

**Detections** — the newest work. Every module tab has a **Proof** column with a
thumbnail; click one for the full frame. The point to make: every detection
carries its own picture, so an operator reviewing an event is never reading a row
of text and guessing.

**Attendance** — the guard-workforce half. Site-grouped guards, status that flips
green on check-in, a contact panel listing exactly who needs chasing.

**Roster** → **Auto-Schedule** — pick a 30-day window and run it. Show the draft
with its warnings (coverage shortfalls, missing supervisor) before publishing.
The warnings are the selling point: it flags rather than silently producing a bad
roster.

**Payroll** → new run — hours pulled from real attendance, CPF computed by age
band and work-pass type.

**Alert Rules** — severity and auto-incident per module, editable, with reset to
default. Say that a change reaches the detection workers in about thirty seconds
with no redeploy.

**Device Protocols** — closes the "will it work with our hardware" question.
Note the honest **unverified** badges rather than glossing over them.

---

## 3a. Do not build the demo around a live detection

Measured, not assumed: with the worker healthy and steadily consuming frames,
**no detection fired in ten minutes.** Camera streams, module flags and zone
configuration were all verified correct, so the cause is the sample footage —
either nobody crosses those zone polygons in the loop, or the polygons were
drawn against different framing.

The consequence for a demo: **Live Wall shows live video, not live detections.**
Live video is impressive on its own. Do not stand in front of a client waiting
for a box to appear.

Show the detection capability on the **Detections** page instead. It holds
~39,700 historical detections, every one with a screenshot thumbnail, and it
demonstrates the feature completely without depending on anything firing on cue.
This is the stronger demo regardless — you control the pace instead of waiting.

If a live detection genuinely matters for the pitch, fix it on the content side
beforehand: supply footage with people crossing those areas, or redraw a zone
over where movement actually happens in the current loop. Then confirm with:

```sql
SELECT count(*) FROM detections WHERE detected_at > now() - INTERVAL '10 minutes';
```

Non-zero before the client arrives, or leave it out.

---

## 4. Say these before you are asked

Volunteering these lands better than being caught by them.

- **Hardware drivers.** Six barrier protocols are implemented; only the simulator
  has been run against real equipment. The screen labels the rest unverified.
  Do not promise a live boom gate.
- **Not every module is running.** Ten of eleven are switched off for memory, not
  because they are unfinished. Any of them can be enabled on adequate hardware.
- **Plate proof needs LPR running.** See below.
- **Mobile selfie check-in does not work yet.** The anti-spoof model
  (`backend/models/liveness_minifasnet.onnx`) was never sourced — see that
  folder's README. Any check-in that submits a photo returns
  `503 Liveness model not found`. Everything around it is built and tested:
  the mock-GPS block, face detection, photo storage, and the web/desktop
  admin check-in override (which sends no photo and works normally). Do not
  demo a guard checking in from the phone until the model file is in place.
- **Payroll CPF** covers standard rates. Graduated first/second-year PR rates are
  not implemented.

---

## 5. If the demo includes licence plates

The plate-crop proof is real but needs the LPR worker running *and* vehicle
footage. Swap it in for intrusion rather than adding to it:

```bash
docker compose stop ai-worker-intrusion
docker compose up -d ai-worker-lpr
```

Then drive a plate through the feed and confirm a row appears under
**Detections → LPR** with a crop in the Proof column *before* the client is
watching. Without that check, the tab shows one old row with no image — which
reads as a broken feature rather than an unused one.

---

## 6. If something goes wrong mid-demo

**Everything hangs / pages stop loading.** Almost certainly memory. Fastest
recovery is to drop the AI worker — the wall keeps its video, only new detections
stop:

```bash
docker compose stop ai-worker-intrusion
```

**Database errors after a Docker restart.** Postgres and Redis have repeatedly
failed to come back with the rest of the stack. Check first:

```bash
docker compose ps postgres redis
docker compose up -d postgres redis
```

**Docker itself wedges** (HTTP 500 from the engine). Recovery takes a couple of
minutes, so do not attempt it in front of a client — stop, and reschedule:

```powershell
Get-Process -Name 'Docker Desktop','com.docker.backend' | Stop-Process -Force
wsl --shutdown
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

---

## 7. Rehearse on the machine you will present from

This is a requirement, not caution. During one working session this host lost its
Docker engine four times, twice taking the database with it, and once ran out of
memory so completely that it could no longer start a process.

Rehearse the full path end to end on the actual demo machine. If it stumbles
there, present from better hardware — 16 GB or more, which also lets you run
several AI modules at once and makes the product look like what it is.
