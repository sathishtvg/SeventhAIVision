# Phase 6 of the known limits: a camera's frame rate, delay and recording gaps measured

Date: 4 November 2026. Agreed with the owner in seven questions and four parts; the programme is in
`2026-11-03-known-limits-programme-and-sg-payroll-design.md`.

The known limit it closes: *Device health is what devices report* - frame rate, latency, packet loss and gaps in a
recording are measured by nothing (`services/device_health.py`, `NOT_MEASURED`). The camera feed takes one picture
every two seconds and never sees the rate a camera sends at.

## Owner decisions

- **A short check every 15 minutes**, not measuring all the time (decoding every frame of 19 streams is the load that
  overloaded this box on the demo day).
- **Delay means the camera's answer time and the time to its first picture**, said on screen not to be the delay
  between the scene and the screen.
- **Gaps in the platform's own recordings only**, not the site recorders' (none is connected here).
- **A camera doing badly reads Degraded**, with the reason; nobody is sent a message.
- **Limits are defaults an organisation may change** (Admin, Manager).
- **Shown on the web, Windows and the phone.**
- **Results are kept for the organisation's device health history period**, or 90 days when none is set; the latest
  check of a stream never removed.
- **A database trigger notes each time "record all the time" is switched on or off**, so a gap while it was off is
  not counted (approved as a change to an existing table's behaviour).

## 1. The check

- Runs in the camera feed service (ingestion), which has each camera's address and sign-in, as the application's
  role. Every 15 minutes each active stream of an active camera is checked once, the streams spread across the 15
  minutes and at most two at a time.
- A check is its own connection for 10 seconds that records when each frame arrives and decodes no picture
  (`ffmpeg`, stream copy, already in the image); a small RTSP `OPTIONS` request times the camera's first answer.
- One check keeps: **frame rate** received, next to the rate the camera says it sends; the **longest stall** between
  two frames; **frames missed** - the share the timing says should have come and did not; the **answer time**; the
  **first picture** - from asking to the first whole picture; and the **result**: OK, or why it could not run
  (could not connect, sign-in refused, the camera refused a second connection, no video in the stream, timed out).
- **Frames missed stands for packet loss.** The feed uses TCP, which sends a lost packet again, so loss is never seen
  as loss - it is seen as a stall or a missed frame, and that is what is counted.
- Not done: it never touches the feed the AI uses; the quality of the picture (darkness, glare, focus) stays not
  measured; a problem that starts and ends between two checks is not seen. Drone cameras (on only in flight; a drone
  reports its own link) and switched-off cameras are not checked.

## 2. Limits, Degraded, recording gaps, keeping

- Defaults: frame rate below 70% of what the camera says it sends; a stall over 2 s; more than 2% of frames missed;
  an answer over 500 ms; a first picture after more than 5 s; a recording gap over 60 s. Changed per organisation by
  `settings:write` (Admin, Manager); each change kept with who made it.
- **Degraded from a check:** the latest check breaks a limit - "Frame rate 4 of 15 at 14:15". The next check that
  passes clears it. A check that could not run while the feed is working, or a check older than 45 minutes, does not
  make a camera Degraded; it is shown as it is.
- **Recording gaps:** every 15 minutes the scheduler looks at the pieces finished since it last looked, for each
  stream set to record all the time, and finds the time between two pieces, a piece shorter than the time it covers,
  and a piece whose file is missing (the integrity sweep's finding, reused). Each gap is kept once with its start, end
  and length; "the camera was offline then" is shown where it was, and no cause is guessed. A time recording was
  switched off is not a gap (the trigger's notes); nothing from before go-live is counted. A camera with a gap over
  the limit in the last 24 hours reads Degraded.
- **Keeping:** checks and gaps follow `retention.device_health_changes_days`, or 90 days when it is not set; the latest
  check of each stream is never removed.

## 3. Screens and rights

- **Device health page (web, Windows):** each camera's line from its latest check - "15 of 15 fps · stall 0.3 s ·
  missed 0% · answer 40 ms · first picture 1.2 s · checked 14:15"; a figure over its limit marked, an old check
  greyed. A camera opened: frame rate over 7 days against its limit, the checks that broke a limit or could not run,
  and its recording gaps. A **Check limits** button for `settings:write`. The "not measured" note says frame rate,
  delay and recording gaps are measured for cameras by a 10-second check every 15 minutes; the quality of the picture
  is not; packet loss is seen as stalls and missed frames.
- **Phone:** the camera list shows the same line; a camera's screen its latest check and its gaps in the last 24
  hours. A new APK is built only on the owner's yes.
- Rights: a camera's latest check and its gaps in the last 24 hours to whoever may see the camera (`camera:read`, site
  scope); the 7 days and the gap list to `asset:read`; the limits to `settings:write`.

## 4. When things go wrong, and testing

- Each failed check is kept with its plain reason; ffmpeg is stopped after 20 s; a restart mid-check leaves nothing
  half-written. A camera's address and sign-in never appear in a result or a log.
- Privacy: the check keeps no picture; `PRIVACY_MASKING.md` lists it as a place that connects to a camera and keeps
  no picture, and its docs test checks the sentence.
- Load: at most two checks at a time. Measured on the real run (4 November 2026, a 320x240 15 fps test stream): two
  checks took 0.6 s of CPU in 24 s, about 2.5% of one core while they ran; a camera's full-size stream copies more
  bytes but is still not decoded.
- Real run, on the test database: a steady stream read 15.1 of 15 fps, a 0.09 s stall, nothing missed - OK; a stream
  that stops 3 s in every 5 read 6.0 of 15, a 3.1 s stall, 59.6% missed - Degraded with those words. The real
  recorder with 1-minute pieces: one piece ended 16:52:29 and the next began 16:52:29 (rotation leaves no gap); after
  the camera's address went dead its pieces held 0 bytes and the pass kept one gap still going on (`EMPTY`, over the
  limit).
- Found while building: the recorder's `duration_seconds` is wall time, so "a piece shorter than its time" cannot be
  seen; a gap is any stretch with no usable piece instead (no piece, one that failed or holds nothing, a file missing).
- Tests: the figures from a list of frame times; the answer time against a stand-in camera; the gap finder with
  switched-off times left out and "offline then" marked; the Degraded rules, an old check and a failed one; the limits
  and who may change them; keeping (90 days by default, the latest never removed); checks and gaps as the
  application's role with two organisations. One real run of the real code against a test stream in a throwaway
  container, the row read back, then the stream made to stall and seen Degraded. Web: the line, the camera, the
  limits. Phone: the list and the camera screen.

Existing files to change (owner approved): `services/device_health.py` (a camera's reading takes its latest check
and its gaps; `NOT_MEASURED` and `NOTE` reworded), `app/ingestion_main.py` (starts the check loop), the scheduler (the
gap pass), `app/main.py` (the new router), the web device health page and its API file, the phone camera list and
camera screen with their API file, `PRIVACY_MASKING.md`, and the tests that pin the old "not measured" wording. Not
changed: what the switch "record all the time" does, the feed the AI is given, the recorder, sensor, drone, gateway
and alarm panel readings.
