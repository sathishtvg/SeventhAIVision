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
| 2 | Frame sampling, thumbnails, SigLIP 2 vectors, semantic search and its screen | Not yet |
| 3 | Event review: people and vehicles on the timeline | Not yet |
| 4 | Fight detection in two stages, and review verdicts | Not yet |
| 5 | "Analyse this recording" | Not yet |
| 6 | Helm template, the GCC package, the deployment guide and the controls sheet | Not yet |

Until phase 2 is built nothing analyses a video: a finished upload waits as
**Waiting to be analysed**.

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

All under `/api/v1`. A hold on a video is lifted where every hold is lifted:
`POST /api/v1/evidence-holds/{id}/release`, with a reason.

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
- Web: the route, the menu entry under Investigate, the page's scene, the
  built-in permissions of each role, and the label of the `FOOTAGE` kind on
  Evidence Holds.

## Left for the owner

- Test footage of fights that we have the right to use (promised later).
- The Ultralytics licence before any SaaS or GCC sale: the person, vehicle and
  pose detectors are AGPL-3.0.
- For GCC, an agency to host it.
