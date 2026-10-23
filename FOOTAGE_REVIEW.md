# Footage Review

Videos people upload, so that they can be searched by description, reviewed for
events and checked for possible altercations. Design and the owner's decisions:
`docs/plans/2026-10-20-footage-review-design.md`.

| Requirement | Where it is met |
|---|---|
| Semantic search — search recorded footage using natural-language descriptions | Phase 2 |
| Event review — identify and retrieve relevant events from uploaded footage | Phase 3 |
| Fight detection — detect potential physical altercations | Phase 4 |
| Cloud platform — secure manual uploads, AI processing, 30-day cloud retention | Phase 1 (uploads, retention); phase 2 on (processing) |
| Deployment — SaaS hosting or Singapore GCC, where supported | Phase 6 |

## What is built

| Phase | What | State |
|---|---|---|
| 1 | Uploads in parts, storage, the 30-day removal and the `FOOTAGE` hold, the list page, permissions | Built |
| 2 | Frame sampling, thumbnails, SigLIP 2 vectors, semantic search and its screen | Built |
| 3 | Event review: people and vehicles on the timeline | Built |
| 4 | Fight detection in two stages, and review verdicts | Built |
| 5 | "Analyse this recording" | Built |
| 6 | Helm template, the GCC package, the deployment guide and the controls sheet | Built |

A finished upload waits as **Waiting to be analysed** until the footage worker
takes it. The worker is not one of the core services: start it while footage
is being analysed or searched (below).

## Nothing here raises an alert or an incident

What is found in an uploaded video is old footage. It never raises an alert, an
incident, a notification or an SOS, and never pages anybody. It stays in
Footage Review until a person does something with it.

## Who may do what

| Permission | | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Viewer 6 | Guard 5 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|---|
| `footage:upload` | Upload a video | ✓ | ✓ | ✓ | ✓ | | | | |
| `footage:view` | Watch and search the videos | ✓ | ✓ | ✓ | ✓ | ✓ | | | |
| `footage:review` | Mark what was found as confirmed or not | ✓ | ✓ | ✓ | ✓ | | | | |
| `footage:delete` | Delete a video before its 30 days are up | ✓ | | | | | | | |

Keeping a video past its 30 days needs `evidence:hold:manage` as well as
`footage:view`. The platform owner holds none of these: it is not a customer's
command centre.

A video may name one of the organisation's sites. Somebody limited to some
sites sees the videos of those sites only, and must name one of them when
uploading. A video without a site is seen only by somebody with no site limit.
A video somebody may not see is answered as one that does not exist.

## The API

| Method | Path | Needs |
|---|---|---|
| `POST` | `/footage` | `footage:upload` |
| `GET` | `/footage/{id}/upload` | `footage:upload` |
| `PUT` | `/footage/{id}/content` | `footage:upload` |
| `POST` | `/footage/{id}/complete` | `footage:upload` |
| `DELETE` | `/footage/{id}/upload` | `footage:upload` |
| `GET` | `/footage` | `footage:view` |
| `GET` | `/footage/{id}` | `footage:view` |
| `POST` | `/footage/{id}/hold` | `footage:view`, `evidence:hold:manage` |
| `DELETE` | `/footage/{id}` | `footage:delete` |
| `POST` | `/footage/{id}/reanalyse` | `footage:review` |
| `POST` | `/footage/search` | `footage:view` |
| `GET` | `/footage/{id}/frames/{second}.jpg` | `footage:view`, from the token in the address |
| `GET` | `/footage/{id}/video` | `footage:view`, from the token in the address |
| `GET` | `/footage/{id}/findings` | `footage:view` |
| `POST` | `/footage/{id}/findings/{finding_id}/verdict` | `footage:review` |
| `POST` | `/footage/from-recording` | `footage:upload`, `recording:read` |

All under `/api/v1`. A hold on a video is lifted where every hold is lifted:
`POST /api/v1/evidence-holds/{id}/release`, with a reason. A frame and the video
take the access token in the address, as the evidence pictures do: a browser's
`<img>` and `<video>` cannot send it otherwise. Searches are limited to 60 a
minute per person.

## Uploading

- **In parts, in order.** The browser says the file's name and size and is
  given an id and a part size of **16 MB**. Each part is sent at the offset the
  server has reached; a part sent at another offset is refused with the offset
  the server has, so an upload that stopped carries on where it was — on the
  same page, or later with the same file chosen again ("Carry on").
- **Only the person who began an upload** adds to it, finishes it or cancels
  it; somebody who may delete videos may cancel anybody's.
- **Accepted:** MP4, MOV, MKV and AVI, up to **4 GB** and **6 hours** each
  (`FOOTAGE_MAX_BYTES`, `FOOTAGE_MAX_SECONDS`).
- **One path for disk and S3.** The installation's `STORAGE_BACKEND` decides:
  on disk the parts are written into one file under `EVIDENCE_ROOT`; in S3 each
  is one part of a multipart upload. A browser never writes to S3 itself.
- **The web proxy** lets the upload paths take a part of 16 MB
  (`client_max_body_size 17m` on `/api/v1/footage/`); every other API request
  keeps the proxy's default limit.
- **The queue is a doorbell, the table is the truth.** A finished upload is
  marked *Waiting to be analysed* and announced on the Redis stream
  `footage_jobs`. If the announcement is lost the video is still waiting, and
  the worker finds it.
- **Audited:** an upload begun, finished and cancelled; a hold; a deletion with
  its reason; a removal by the 30-day job with why.

## The footage worker

A container of its own, `footage-worker` (`docker/footage-worker.Dockerfile`,
code in `ai-worker/worker/footage/`). ONNX Runtime and ffmpeg, no PyTorch, so
it can run beside the live stack on a small machine; it is not one of the core
services and is started when needed:

    docker compose -f docker/docker-compose.yml --profile footage up -d --no-deps footage-worker

- **One video at a time.** It takes a video from `footage_jobs`, and every
  minute asks the database which videos are waiting (`footage_waiting()`, the
  one question asked across organisations, answered as the database's owner).
  A video a stopped worker was holding goes back in the queue; after three tries
  it is *Could not be analysed*.
- **What it does to a video:** reads what it is (ffprobe), takes its SHA-256,
  takes **one frame a second**, keeps each as a thumbnail with its SigLIP 2
  vector and how many people and vehicles are in it (`footage_frames`), joins
  those seconds into events (`footage_findings`), and — when the original is
  not something every browser plays (H.264 in MP4 or MOV) — makes a copy that
  is. A file that is not a video, or is longer than may be analysed, is *Could
  not be analysed* with the reason.
- **It answers the API's searches**: a typed description in, its vector out,
  on port **8090** of the stack's own network. That port is published nowhere,
  and the words are not logged there.
- **It writes as the application's role**, under each video's organisation.
  PostgreSQL refuses `COPY` into a table under a row-level policy, so frames are
  inserted.
- **It raises no alert, incident or notification.**

## The person and vehicle detector

The same weights the live AI workers run: YOLOv8s (`ai-worker/yolov8s.pt`,
Ultralytics, **AGPL-3.0** — see *Left for the owner*). It is converted to ONNX
when the worker's image is built, in a stage of its own that holds PyTorch; the
worker itself has none. A machine that already has the AI workers' image builds
it with nothing downloaded:

    docker build --build-arg DETECTOR_EXPORT_BASE=docker-ai-worker-behavior:latest -f docker/footage-worker.Dockerfile .

Each sampled second is read 640 pixels wide and shown to the detector, which
counts **people** (COCO person) and **vehicles** (bicycle, car, motorcycle, bus,
truck) with a score of at least 0.35.

## Events

- **Seconds in which some were seen are one event** when no more than **3
  seconds** without any lie between them — a person walking behind a pillar is
  one event, not three. An event says when it begins and ends, how many were
  seen at once at most, and which second shows the most (its picture).
- **A count is how many were seen in one frame, never who.** Somebody who passes
  in less than a second may be missed, and a count is not how many different
  people came by.
- **On a video's page** the events are marks on a timeline — a row for people,
  one for vehicles — and a list in the order they happen, each with its
  picture; choosing one jumps the player there.
- On Ultralytics' own street photo (a bus and the people beside it) the
  detector counted the people and the bus, through the real worker on
  20 October 2026.

## Possible altercations

Two stages, and the result is never more than **"Possible altercation"** —
never "assault", never a name — for a person to mark **confirmed** or **not an
altercation**.

1. **A screen, every sampled second.** The frame's SigLIP 2 vector is compared
   with five fixed descriptions of fighting ("people fighting", "a person
   punching another person", "people pushing each other", ...) and seven of what
   looks like it and is not ("people hugging", "people dancing", "a crowd of
   people walking", "people playing sports", ...). The share of the fighting
   descriptions among all twelve, by the model's own logits, is how much more
   the frame looks like a fight than like its look-alikes. A second is flagged
   when that share is at least **0.5** and at least **two people** were counted
   in it; where **2 of 3** consecutive seconds are flagged, that stretch and a
   second either side is looked at again.
2. **The bodies, five frames a second.** The pose model (YOLOv8s-pose, the same
   family and licence as the detector, converted in the same build stage) finds
   each person's body points. A moment of struggle is two people within reach of
   each other (centres nearer than **1.2** times their mean height) one of whom
   moved a wrist faster than **1.5** body heights a second — a punch or a
   shove; an arm swinging while walking is well under it. The stretch is a
   possible altercation when there are at least **3** such moments.

A finding keeps its reasons: what it looked like, how strongly, and what the
pose model saw. The video's page shows it on the timeline in red, with **Give
a verdict**; the list says how many in a video are waiting for one.

**A verdict changes nothing else.** It raises no alert and no incident. It is
kept with who gave it and when, may be changed, and every change is audited
with what it replaced — verdicts are what the thresholds will be tuned against.

**How well it works is not yet measured.** No footage of fights that the
platform has the right to use has been tested yet (the owner will provide it).
Every number above is a first setting; the measured catch rate and false-alarm
rate are what will be published, and nothing before them.

## The model

SigLIP 2 base (Google, **Apache-2.0**), as converted to ONNX and quantised to
int8 by onnx-community: `onnx-community/siglip2-base-patch16-224-ONNX`, revision
`ba1f3b0`, built into the worker's image with every file checked against its
published size and SHA-256 (`worker/footage/fetch_model.py`). Nothing is
fetched when the worker runs: a GCC network gives a container no internet.

A frame and a sentence each become 768 numbers; the nearer they point, the more
the frame looks like the words. The model writes no text and decides nothing:
it ranks. A sentence may be written in any of the languages the model was
trained on (109); how well each works is to be measured before it is claimed.

Each frame records which model made its vector, and a search compares only
vectors of one model: a video analysed by an earlier model is not searched until
it is analysed again, and the search says how many were left out.

## Searching by description

- **The words go to the footage worker, nothing else does.** No footage leaves
  the platform.
- **How likely a match is, is the model's own measure**: SigLIP was trained to
  give each picture-and-sentence pair a probability, from two numbers it learned
  (`logit_scale` and `logit_bias`, read from Google's weights). A frame is judged
  on its own, so what fills half a video is found as readily as what appears
  once. **Strong match** at 0.5 or more, **Possible match** at 0.1, **Weak
  match** at 0.01; below that a frame is not offered. These are first settings,
  to be checked against labelled clips and changed when they are measured.
- **Neighbouring seconds are one moment**, shown by its best frame.
- **A search with no strong or possible match says so**, and shows any weak
  ones as weak, for a person to judge.
- **Every search is audited with its wording.** A search for "a woman in a red
  dress" is a search for a person.
- On a 15-second test video of a red scene, a blue scene and a test pattern,
  run through the real worker on 20 October 2026, each description found its
  own scene (the plain colours as weak matches, the test pattern as a strong
  one) and "a dog on a beach" found nothing.

## Playing a video

The video's page plays the original when a browser can, otherwise the copy the
worker made; a video neither will play says so, and its frames can still be
searched. Opened from a search, the player starts a few seconds before the
moment. Opening a video is audited; the player's later requests for other parts
of the same file are not.

## A saved recording

**Analyse a saved recording** on the list offers the finished recordings the
person may see (it needs `footage:upload` and `recording:read`). The one chosen
is copied into Footage Review, byte for byte, after the answer is sent; until
the copy is whole it is shown as *Copying the recording*, and then it is
analysed like an upload. A copy that cannot be made says so.

- **The 30 days are the copy's.** A camera's recordings are usually kept for
  days, not weeks; the recording keeps its own retention, and when it is
  removed the copy stays until its own 30 days are up.
- **It is masked.** Recordings are masked when they are recorded
  (`PRIVACY_MASKING.md`), so the copy, its frames and its search results are
  of the masked picture.
- **One copy at a time.** A recording already in Footage Review is not copied
  again (unless its copy failed); the answer names the video it is in.
- It is filmed at the recording's start, at the camera's site, and named after
  the camera.

## Keeping and removal

- A video is kept **30 days** from when its upload began, shown on the list as
  "Deleted in N days".
- Once a day the scheduler removes every video past its 30 days — the file,
  everything kept beside it, then its record — and writes why to the
  organisation's audit log (`services/footage_retention.py`).
- An upload nobody has added to for **a day** is removed the same way.
- **A video under an evidence hold of kind `FOOTAGE` is not removed**, however
  old, until the hold is lifted. A held video cannot be deleted by hand either.
- The retention statement (Data Retention) lists the period as
  `FOOTAGE_REVIEW`: 30 days, fixed in the code.

## Deploying it

`docs/FOOTAGE_REVIEW_DEPLOYMENT.md`: SaaS on AWS Singapore (Part A), an agency's
GCC account (Part B, with `helm/seventh-ai-vision/values-gcc.yaml`), a controls
sheet against the topics of a government security review (Part C), and what is
the owner's before it runs on a real bucket (Part D). Prepared, never run;
nothing has been created in any cloud account.

The chart gains the footage worker (its deployment, service, network policy and
upload ingress) and `FOOTAGE_WORKER_URL` in its config map; it renders with the
default values and with the GCC values.

## Changes to what existed

All approved in the design. Each is an addition:

- `backend/app/services/evidence_hold.py` — the kind `FOOTAGE`.
- Migration `0163` — the allowed kinds of `evidence_holds` and
  `evidence_custody_events` gain `FOOTAGE`; every existing row satisfies the
  wider list.
- `backend/app/services/retention_statement.py` and its test — the period
  `FOOTAGE_REVIEW`.
- `backend/app/scheduler_main.py` — the daily removal, after the fleet's.
- `backend/app/main.py` — the router.
- `docker/nginx.conf` — the upload location.
- `docker/docker-compose.yml` — the `footage-worker` service, under the profile
  `footage`.
- `helm/seventh-ai-vision/values.yaml` — the `footageWorker` block and its image;
  `templates/configmap.yaml` — `FOOTAGE_WORKER_URL`.
- Migration `0164` — what the worker found out about a video is kept on it:
  `codec`, `frames_indexed`, `index_model`, `playable`, `proxy_path`.
- `PRIVACY_MASKING.md` — a paragraph saying a copied recording is masked, and an
  upload has no zone.
- Migration `0166` — `footage_videos.source` may be `RECORDING`, with the
  recording it was copied from (`recording_id`, set to NULL when the recording
  is removed).
- Migration `0165` — `footage_frames` gains the counts of people and vehicles;
  `footage_findings` is new. Both tables are this module's own. The application
  may change only a verdict on a finding, never the finding.
- Web: the routes, the menu entry under Investigate, the pages' scenes (a
  video's page arrives as playback does), the built-in permissions of each role,
  and the label of the `FOOTAGE` kind on Evidence Holds.

## Left for the owner

- Test footage of fights that we have the right to use (promised later): the
  thresholds of both stages are set on it, and its catch and false-alarm rates
  are what may be claimed.
- The Ultralytics licence before any SaaS or GCC sale: the person, vehicle and
  pose detectors are AGPL-3.0.
- For GCC, an agency to host it.
- Two changes to existing code before a real S3 bucket can be used (the chart's
  S3 keys when MinIO is off; the API's S3 client and a pod's IAM role) —
  `docs/FOOTAGE_REVIEW_DEPLOYMENT.md`, Part D.
