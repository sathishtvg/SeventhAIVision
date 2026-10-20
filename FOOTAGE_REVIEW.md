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
| 3 | Event review: people and vehicles on the timeline | Not yet |
| 4 | Fight detection in two stages, and review verdicts | Not yet |
| 5 | "Analyse this recording" | Not yet |
| 6 | Helm template, the GCC package, the deployment guide and the controls sheet | Not yet |

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
  takes **one frame a second**, keeps each as a thumbnail and its SigLIP 2
  vector in `footage_frames`, and — when the original is not something every
  browser plays (H.264 in MP4 or MOV) — makes a copy that is. A file that is
  not a video, or is longer than may be analysed, is *Could not be analysed*
  with the reason.
- **It answers the API's searches**: a typed description in, its vector out,
  on port **8090** of the stack's own network. That port is published nowhere,
  and the words are not logged there.
- **It writes as the application's role**, under each video's organisation.
  PostgreSQL refuses `COPY` into a table under a row-level policy, so frames are
  inserted.
- **It raises no alert, incident or notification.**

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
- Migration `0164` — what the worker found out about a video is kept on it:
  `codec`, `frames_indexed`, `index_model`, `playable`, `proxy_path`.
- Web: the routes, the menu entry under Investigate, the pages' scenes (a
  video's page arrives as playback does), the built-in permissions of each role,
  and the label of the `FOOTAGE` kind on Evidence Holds.

## Left for the owner

- Test footage of fights that we have the right to use (promised later).
- The Ultralytics licence before any SaaS or GCC sale: the person, vehicle and
  pose detectors are AGPL-3.0.
- For GCC, an agency to host it.
