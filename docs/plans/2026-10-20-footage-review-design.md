# Footage Review — design

Date: 2026-10-20. Agreed with the owner section by section in one session.

## The requirements

1. **Semantic search** — search recorded footage using natural-language descriptions.
2. **Event review** — identify and retrieve relevant events from uploaded video footage.
3. **Fight detection** — detect potential physical altercations from recorded footage.
4. **Cloud platform** — secure manual video uploads, AI processing and 30-day cloud retention.
5. **Deployment options** — SaaS hosting or Singapore Government Commercial Cloud (GCC), where supported.

## What existed before

| # | Existing | Gap |
|---|---|---|
| 1 | Investigation search reads a typed phrase by fixed rules into filters over recorded *events* (decision E2: no language model) | Nothing looks at the pictures themselves |
| 2 | AI runs on live camera frames only; continuous recording exists | No video upload, no analysis of a file |
| 3 | `behavior.aggression` = two people's boxes overlapping in one frame | Fires on people standing close; not a fight detector |
| 4 | Object storage (`STORAGE_BACKEND` local or s3), per-organisation recording retention (default 7 days), evidence holds | No upload pipeline, no rule for uploaded footage |
| 5 | Helm chart, AWS demo scripts (prepared, never run) | Nothing about GCC |

## Decisions (the owner's answers)

| Question | Decision |
|---|---|
| Goal | Design, then build, in phases merged to main once CI is green |
| Searching by description (E2) | **An image-text model run inside our own stack.** It turns frames and a typed description into vectors and ranks frames; it never writes text and no footage leaves the platform. E2 stays in force everywhere else. |
| Which footage | **Uploads, plus any saved camera recording someone chooses to analyse.** Nothing is analysed unless a person asks. |
| Fight detection | **The image-text model screens; the pose model confirms over a few seconds.** The result is always "possible altercation" for a person to review. |
| 30-day retention | **Deleted 30 days after upload, except what is under an evidence hold.** Each deletion is logged. |
| Cloud | **AWS Singapore first** (ap-southeast-1): SaaS there, and the same deployment packaged for an agency's GCC account on AWS. |
| Processing approach | **A separate footage pipeline** with its own worker and queue, vectors in pgvector, findings in new tables. Old footage never raises live alerts or incidents. |
| Changes to existing code | **Approved:** the evidence-hold rules gain a kind `FOOTAGE` (`backend/app/services/evidence_hold.py` and one new migration widening the allowed kinds of `evidence_holds`). |
| Test footage of fights | The owner will provide it later. Build first; measure when it arrives. |

## Models (chosen after checking online, 2026-10-20)

| Model | Licence | ImageNet 0-shot | COCO text→image R@1 | Other languages | |
|---|---|---|---|---|---|
| **SigLIP 2 B/16 224** (Google) | Apache-2.0 | 78.2 | 52.1 | XM3600 40.3 (36 languages; SigLIP 1: 22.4) | **chosen** |
| PE-Core B16 224 (Meta) | Apache-2.0 | 78.4 | 50.9 | none reported | runner-up |
| MobileCLIP2 (Apple) | apple-amlr, a research release | | | | ruled out |

SigLIP 2 handles 109 languages, so a description typed in Chinese, Malay or Tamil can be matched. That is tested before it is claimed.

No published figure exists for an image-text model detecting fights from text alone. Image-text models are strong on "what kind of scene is this" and weaker on fine-grained actions, so the pose confirmation is required, not optional. Large chat-style vision models score well on fight datasets but are 7–8-billion-parameter language models (E2, and GPUs). The planned upgrade, once labelled clips exist, is a small classifier trained on the SigLIP vectors of 2-second windows: CPU only, no new large model.

Person, vehicle and pose detection reuse the detectors the live workers already run (Ultralytics YOLO, AGPL-3.0). Ultralytics states that SaaS and cloud use needs its Enterprise licence. That applies to the whole platform, not only this module, and is **the owner's to settle before any SaaS or GCC sale.**

Every model is built into the container image. GCC networks restrict outbound internet; nothing may download at run time.

## 1. Architecture

A new module, **Footage Review**, additions only (E1).

- **API** — `backend/app/routers/footage.py` at `/api/v1/footage`: uploads, list and progress, "analyse this recording", search, events, findings and verdicts.
- **Storage** — the original under `footage/<tenant>/<video>/` through the existing `STORAGE_BACKEND` switch (local disk or S3), and one thumbnail per sampled second beside it.
- **`footage-worker`** — a new container from the AI-worker image (CPU PyTorch, the person/vehicle and pose models). It takes one video per job from its own Redis stream `footage_jobs`, never `frame_jobs`, and:
  1. probes the file (length, frame rate);
  2. samples one frame per second and writes a thumbnail;
  3. stores a SigLIP 2 vector per frame, labelled with the model version;
  4. runs the person and vehicle detector and joins seconds into events;
  5. runs the fight check;
  6. reports progress.
  It also answers an internal-only request to turn a typed description into a vector, so the API never loads the model. That endpoint is not published outside the Docker network.
- **Database** — three new tables, each under the tenant policy:
  - `footage_videos` — uploader, source (upload or recording), place, filmed-at, status, progress, error, expiry;
  - `footage_frames` — second, thumbnail, `vector(768)`, model version; HNSW index;
  - `footage_findings` — kind, start, end, score, reasons, a person's verdict.
- **Never from old footage:** no alerts, incidents, notifications or SOS. A finding stays in Footage Review until a person adds it to an investigation or an evidence package.

## 2. Uploads, security and the 30-day rule

- Uploads in 16 MB parts through the API, resumable. One path for local disk and S3 (agency networks often block a browser sending straight to S3).
- MP4, MOV, MKV, AVI; up to 4 GB and 6 hours each (configurable per deployment). The worker opens each file with ffprobe first; a file that is not a video is refused with the reason.
- The worker has no outbound internet. Files are decoded, never run.
- Permissions: `footage:upload` (admin, manager, supervisor, operator), `footage:view` (those and viewer), `footage:review` (admin, manager, supervisor, operator), `footage:delete` (admin). Guards and clients none. Uploads, playback, downloads, searches (with their wording), verdicts and deletions are audited.
- Encryption: TLS in transit; S3 with KMS keys at rest in the cloud; disk encryption on a single server (a deployment note). For GCC the package recommends AWS's malware scanning on the bucket — configured at deployment, not built into the app.
- "Analyse this recording" copies the saved recording into Footage Review, where the 30-day rule applies to the copy. Recordings are masked when recorded, so privacy zones stay painted over in the copy, its thumbnails and its results.
- Each video shows its expiry ("deleted in 3 days"). A daily scheduler job deletes the expired original, thumbnails, vectors and findings, logs each deletion, and leaves alone anything under an evidence hold of kind `FOOTAGE`.

## 3. Processing, events and fight detection

- Optional "filmed at" and place from the uploader; a recording knows its own.
- Events: the person and vehicle detector on each sampled second; consecutive seconds joined, gaps up to 3 s allowed — "People present 00:12–00:45 (up to 3)", "Vehicle present 02:10–02:31". Anything more specific is found by semantic search.
- Fight check, two stages:
  1. each second scored against fixed fight descriptions ("people fighting", "a person punching another person", "people pushing each other") weighed against look-alikes ("people hugging", "people dancing", "a crowd walking");
  2. where 2 of 3 consecutive seconds score high, that stretch is re-read at 5 frames per second with the pose model: at least two people within reach and fast arm movement.
  A finding reads **"Possible altercation"**, with its reasons. Never "assault", never a name. A person marks it **confirmed** or **not an altercation**; verdicts are kept for tuning.
- Accuracy is measured, not assumed: thresholds set on test clips, only measured figures published.
- An unreadable video is **failed** with the reason. A job a worker drops is picked up again; after three tries it is failed. A finished video can be **re-analysed**. One video at a time per worker; more uploads, more workers. Live cameras are never slowed.

## 4. Screens (web, and the desktop app that loads it)

- Footage list: status, progress, place, filmed-at, uploader, "deleted in N days"; drag-and-drop upload with a progress bar that survives a dropped connection.
- Video page: player and timeline with events as marks; a click jumps there.
- Search, "Describe what you're looking for": one video or all, narrowed by dates or place; thumbnails ranked by match, labelled high / medium / low; neighbouring seconds of one scene grouped; "no strong matches" said plainly; a result opens the player 5 s before the moment.
- Findings: reasons shown; verdict with a note, who and when.
- "Add to investigation" and "Add to evidence package" use the existing modules; a sealed package's hold keeps the footage past 30 days.
- No phone screens in the first release.

## 5. Deployment, tests and phases

- **SaaS on AWS Singapore:** a `footage-worker` template in the Helm chart (new file; a new `footageWorker` block in `values.yaml`); workers scale with the queue; GPU nodes optional; S3 with KMS; one lifecycle rule only — abort unfinished multipart uploads after a day. No rule deletes footage: the app's purge decides, because a bucket rule would also delete held footage.
- **GCC package (prepared, never deployed by us):** the same chart with GCC values — no outbound internet, images from the agency's registry with models built in, S3 and KMS through private endpoints, no public entry unless the agency chooses — plus a deployment guide and a controls sheet mapping the app to IM8 topics. Nothing is created in AWS.
- **This machine (7.7 GB):** the footage worker runs under a Compose profile, started when needed.
- **Tests:** API (upload in parts and resume, permissions, two tenants as `svc_app`, purge with and without a hold, no alert or incident ever created); worker (a short generated video through the real pipeline once, reading the rows it wrote; unit tests for events and the two fight stages); quality (a labelled set of our own clips and descriptions: search hit-rate, fight catches and false alarms, recorded per phase); web page tests.
- **Phases**, each merged once CI is green:
  1. uploads, storage, 30-day purge and the `FOOTAGE` hold, the list page, permissions;
  2. sampling, thumbnails, SigLIP 2 indexing, semantic search and its screen;
  3. event review on the timeline;
  4. fight detection and verdicts;
  5. "analyse this recording";
  6. Helm template, GCC package, guide and controls sheet. The feature document is updated as each phase merges.

## Left for the owner

- Test footage of fights that we have the right to use (promised later).
- The Ultralytics licence before any SaaS or GCC sale.
- For GCC, an agency to host it.
