# Demo script — the `demo` organisation

Written 8 October 2026 and brought up to date on 10 October, for the stack as it
runs on this machine: seven core services plus the two runners and the
sample-video container, no AI workers. It replaces `DEMO-RUNBOOK.md` for the
`demo` organisation (that file describes an older `aegis` set-up and is kept as
it was).

The story below covers the screens that have sample data. The application has
more than these — assets and maintenance, risk and advice, the operations
board, workforce readings, cases, data retention — and those screens work but
are empty in this organisation.

The sample data is loaded by `scripts/ops/demo_showcase.py`. It only adds, and
it can be run again at any time: what is already there is left alone.

---

## 1. On the day

**An hour before** — today's shifts, visits, occurrence book and a guard's
finished response:

```bash
docker exec -i -e DEMO_STEP=today docker-api-1 python - < scripts/ops/demo_showcase.py
```

**Five minutes before** — the two things that must be fresh: an incident nobody
has been sent to, and a situation waiting for a decision:

```bash
docker exec -i -e DEMO_STEP=live docker-api-1 python - < scripts/ops/demo_showcase.py
```

Why the timing matters:

- An unanswered incident is marked **late** two minutes after it is raised and
  escalated after four. Left for an hour it is simply late, which demonstrates
  escalation but not the clock running.
- A situation can **settle by itself** about half an hour after its last
  event. Run `live` too early and there may be nothing waiting for a decision.
- Visits are made relative to the moment `today` runs. The one waiting for your
  approval lapses about six hours later.

Each run prints what it made, what was already there and anything it could not
do. If it prints *NOT DONE*, read the line: it gives the application's own reason.

**The clock on the screen is Singapore's.** The organisation's time zone is
Asia/Singapore, two and a half hours ahead of this laptop (IST). A time the
application shows is not the time on your taskbar.

## 2. Sign in

    https://localhost          (http://localhost:5173 redirects there)
    Organisation   demo
    Email          ops@demo.local        Priya Nair, Admin: sees every screen

The browser warns about the certificate the first time, because it is
self-signed: *Advanced*, then *Proceed*. Do this before the audience is watching.

The password is the one all demo accounts share: the value of `DEMO_PASSWORD` in
`backend/scripts/seed_demo.py`. Do not run that file — it deletes the whole
organisation.

**The platform owner signs in differently.** Organisation `seventhaivision`,
your own Super Admin email and password, and then the 6-digit code from your
authenticator app: that account has two-factor on, and the page asks for the
code once the password is accepted. It is the vendor's console — customers,
pricing, errors — and not part of this story.

Other people, same password, if you want to show what a role sees:

| Email | Who | Shows |
|---|---|---|
| `supervisor@demo.local` | David Lim, Supervisor | Reviews the book, drafts procedures, answers as a visitor's host |
| `guard2@demo.local` | Rajesh Kumar, Guard | A guard's narrower view. **Web only** — see section 5 |
| `viewer@demo.local` | Control Room Viewer | Read-only |
| `client@marinabay.demo` | Marina Bay Property Mgr | The client portal: their own site and nothing else |

## 3. The story

One site, one morning. **Jurong Logistics Hub**: a bonded warehouse, a vehicle
gate, an office block. Three people are on shift there — Rajesh Kumar and Tan
Wei Ming (guards) and David Lim (supervisor). You are Priya Nair, the operations
manager.

A white van has been waiting outside Gate 1 on more than one day. That thread
runs through the occurrence book, the watch list, the investigation and the
procedure library, so each screen leads to the next.

## 4. The route — about 25 minutes

Menu names are as they appear in the sidebar.

**1. Command Centre** *(Monitoring)* — open here. Sites, the live alert feed, the
operational picture. Do not dwell on the totals; see section 6.

**2. Situations** *(Security Intelligence)* — *the* slide for "AI recommends, a
person decides". Open **Person on foot in the vehicle lane at Gate 1**. Two alerts
from two cameras became one situation; it is assessed (risk HIGH), and it carries
suggestions headed *Suggestions — not decisions*, each with what it rests on.
Take the decision live. Say: the background process cannot act; only your
decision does, and it is recorded separately from the suggestion.

**3. Response Desk** *(Monitoring)* — two incidents.

- *Person down on the warehouse floor* — nobody sent. Click **Who to send**: Tan
  Wei Ming and David Lim are free; Rajesh Kumar is ranked last because he is
  already on a job. Each score is shown with its parts. The procedure *Person
  down or medical emergency* is beside it, word for word. Send somebody.
- *Intrusion: person in the restricted lane at Gate 1* — **Open** it: sent,
  accepted, on the way, arrived, and what the guard found. That is what the
  guard's phone writes.

Each incident carries its clocks — acknowledge, arrive, resolve — and the
filters above the list count what is late or unanswered. **Response settings**
shows the times per severity and the three escalation policies. Say: the clocks
tell people; they never reassign or close anything.

**4. Security Map** *(Monitoring)* — choose Jurong Logistics Hub. Guards on shift
with the age of their last position, the incidents, the places of the site:
Warehouse A, the office block, two gates, the assembly point. Select the
incident to list what is near it. Say: guards are not tracked; this is the last
position each one recorded.

**5. Occurrence Book** *(Guard Operations)* — this morning's entries. Point at:

- the *unusual activity* entry about the van — the supervisor marked it **to be
  followed up**, with what to do;
- the fence-light entry — **corrected by a later entry**; the first is still
  there exactly as written;
- the tab **Instructions in force** — two, one of them read by the guard;
- the tab **Shift summaries** — Tan Wei Ming's is confirmed; Rajesh Kumar's is a
  draft. Open the draft: fixed sentences that count and quote, written from the
  records. Say: no language model wrote this, and it cannot be changed once
  confirmed.

**6. Search Records** *(Investigate)* — type `plate SKD2468A last 3 days`. Six
reads. The screen shows which words became which filter. Then type
`white van at Jurong yesterday` to show it saying what it did **not** understand
rather than guessing. Open the plate's **trail**: Jurong at night, Marina Bay
Tower the next afternoon, Jurong again this morning — two sites.

**7. Investigations** *(Investigate)* — *White van waiting outside Gate 1*. The
plate reads and the guard's own entry are filed in it, in the order they
happened, with a note of what was concluded.

**8. Evidence Packages** *(Investigate)* — one **sealed** package
(`EVP-…-0002`) and one empty draft. Open the sealed one: the manifest, the
checksum, *intact*, the hold that stops it being purged, and the chain of
custody — including the fact that you have just opened it.

**9. SOP Library** *(Guard Operations)* — five procedures in force, one
*awaiting approval*. In *Find the procedure on something* type
`van waiting outside the gate`: the passage comes back word for word with its
procedure, version and who approved it. Then open **Suspicious or abandoned
object** and approve it live: David Lim drafted it, so he could not approve it
himself — you can.

**10. Visitor Authorisations** *(People & Vehicles)* —

- *Waiting for your answer*: **Marcus Tan** (you are his host) and the **Coolair
  Engineering** work permit (no host named). Approve one.
- The table: Farah Binte Ismail **Valid**, Wong Kah Wai **Declined** with the
  reason. Open Farah: approved by her host, to be escorted, ID seen (the kind of
  document, never its number), authorised for Warehouse A.
- *Door events to look at*: her badge was used at the **Office block side
  door**, outside what she was authorised for. Say: this is something for a
  person to look at, not a finding. Record that it was in order.

**If there is time** — *Virtual Patrol* (22 past patrols with their reports),
*Roster* then *Auto-Schedule* (run a month and show the draft's warnings),
*Attendance*, *Payroll*, *Live Wall* (live video from the cameras).

## 5. Do not

- **Do not sign in as a guard on the phone app or the emulator.** A guard's
  phone that is lying still raises a real man-down alert. The guard's side of a
  response is already on the Response Desk (stop 3).
- **Do not open the screens that have no sample data** unless an empty screen
  is what you want to show: Assets & Maintenance, Cases, the Operations board
  and briefing, Workforce, Risk and advice, Data retention.
- **Do not open Drone Patrol.** The organisation has no drone licence and no
  drone was set up for this demo.
- **Do not wait for a live detection.** No AI worker is running on this machine.
  *Detections* holds about 39,900 past detections, each with its picture: show
  those.
- **Do not run tests or rebuild containers during the demo.** Screens that
  normally answer in a second take five to ten while the machine is busy.

## 5a. Keeping it fast — learned the hard way on 8 October

This machine has 8 cores and gives Docker 3.8 GB. The application answers in a
fraction of a second until something else takes the processor; then every screen
takes seconds and sign-in takes ten.

- **Continuous recording stays off.** Recording three cameras re-encodes their
  video inside the API and takes about five cores. It was switched off on Lobby
  Entrance, Loading Bay and Car Park Ramp (Cameras, the stream's *continuous
  recording* setting, to turn it back on). The 10,800 past recordings are still
  there to show.
- **The sample-video container now encodes three pictures, not nine**
  (`docker/mediamtx/mediamtx.yml`): about one core. All nine cameras are live.
- **No tests, no image builds, no container restarts while presenting.** The API
  needs about two minutes to come back after a restart, and sign-in fails
  during it.
- **Ten wrong passwords lock an account for thirty minutes.** If Chrome
  autofills an old password, clear the field and type it. To clear a lock:

  ```bash
  docker exec docker-postgres-1 psql -U postgres -d seventh_ai_vision -c "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE email = 'ops@demo.local'"
  ```

- **A quick check before you start** — every line should say `Up`, and the API
  `healthy`:

  ```bash
  docker ps --format "table {{.Names}}\t{{.Status}}"
  ```

## 6. Say these before you are asked

- **The big numbers on the dashboards are old.** About 1,400 open incidents and
  8,600 open alerts have been left unattended since August on this development
  system. They are not part of the story.
- **Some names in the lists are test leftovers**: the users *Cert UI Verify* and
  *Test Guard Verify* (who is on the daily roster), the cameras *No Stream Cam*
  and *Unreachable Test Cam*.
- **Camera pins stack on the map.** Every camera of a site carries the site's
  own coordinates.
- **Nothing here uses a language model.** A typed phrase is parsed by fixed
  rules, a summary is a template, a procedure is found by its words.
- **The procedures are samples.** Each begins by saying so.
- **The drone has only flown in a simulator**, and access control has no real
  hardware behind it here: the door events in stop 10 were put in by the loader.
- **The phone app needs a new build** before guards have the newest screens.

## 7. If something goes wrong

- **A screen is slow or times out** — something else is using the machine. Wait
  and reload; do not click again repeatedly.
- **Nothing waits for a decision in Situations** — it settled. Run the `live`
  step again and reload after half a minute.
- **"Who to send" finds nobody** — no shift is running. Run the `today` step: it
  starts the rostered shifts, or makes one for the demo outside 07:00 to 19:00
  Singapore time.
- **You are signed out mid-demo** — sign in again; nothing is lost.
- **Docker or the database stops** — see `DEMO-RUNBOOK.md` section 6. Do not
  attempt it in front of an audience.

## 8. What the loader put in, so that nothing surprises you

| | |
|---|---|
| Response | The three clocks **switched on** for this organisation (they were off), times for four severities, three escalation policies |
| Places | Eleven places at Jurong Logistics Hub and three at Marina Bay Tower; two doors |
| Procedures | Five in force and one awaiting approval, drafted by David Lim and approved by Priya Nair |
| Vehicles | Plate `SKD2468A` on the watch list, with six reads over three days. The plate cannot be a real registration: its last letter fails the checksum |
| Contractors | Coolair Engineering Pte Ltd, vetted, with a work permit |
| Shifts | Today's rostered shifts of the three demo staff **started**, as an on-time check-in would |
| Visitors | Four visits with their authorisations; one checked in with badge `V-017` and three door events |
| Occurrence book | Seven entries, two reviews, a correction, two instructions, two shift summaries |
| Incidents | One answered by a guard, one unanswered. An older unanswered one made by the loader is resolved when a new one is made |
| Alerts | Two, marked in their text as samples, which the intelligence layer relates into a situation |
| Investigation | One, with the plate reads and the guard's entry |
| Evidence | One sealed package (one frame, under a hold) and one empty draft |

No visitor has an ID number, and no procedure, alert or plate is a real one.
