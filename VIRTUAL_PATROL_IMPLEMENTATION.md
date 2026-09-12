# Virtual Patrolling — implementation

A scheduled, camera-by-camera inspection carried out from a screen. A schedule
names a site, an ordered list of cameras and a question set. When its time
arrives the scheduler creates a session; the assigned officer works through the
cameras, capturing a **real** snapshot at each and answering its questions; a
failed answer can raise an incident; the finished patrol produces a PDF and an
Excel workbook, stores both, and emails them.

Integrated into the existing application rather than parallel to it: sites,
cameras, streams, users, roles, permissions, incidents and the Command Centre
are all the ones already there.

Migration head: **`0117`**. Module namespace: `vpatrol:*`, `virtual_patrol_*`.

---

## Where the code is

| Concern | File |
|---|---|
| Schema | `backend/alembic/versions/0116_virtual_patrol.py`, `0117_vpatrol_digest_periods.py` |
| HTTP | `backend/app/routers/virtual_patrol.py` (28 endpoints) |
| Command Centre panel | `backend/app/routers/command_centre.py` |
| Session lifecycle | `backend/app/services/virtual_patrol.py` |
| RTSP capture | `backend/app/services/vpatrol_snapshot.py` |
| PDF / Excel | `backend/app/services/vpatrol_reports.py` |
| Digest workbook | `backend/app/services/vpatrol_digest.py` |
| Due detection, missed sweep | `backend/app/services/vpatrol_scheduler.py` |
| Email queue, digests | `backend/app/services/vpatrol_email.py` |
| Worker wiring | `backend/app/scheduler_main.py` |
| Admin screen | `frontend/src/pages/VirtualPatrol.tsx` |
| Officer screen | `frontend/src/pages/PatrolExecution.tsx` |
| API client | `frontend/src/api/virtualPatrol.ts` |
| End-to-end check | `scripts/ops/vpatrol_e2e.py` |

Ten tables, all with RLS **enabled and forced**, so even the table owner is
subject to the policies. Each carries both a `USING` and a `WITH CHECK` clause:
`USING` alone lets a tenant insert rows into another tenant's account that it
then cannot see itself.

---

## The decisions that matter

### Configuration is frozen into the session

`create_session()` copies the cameras and the questions into
`virtual_patrol_session_cameras` and `virtual_patrol_session_questions`. The
officer's routes read only those.

Two reasons. A supervisor editing a question mid-patrol must not change what the
officer is being asked halfway through. And a patrol from March must still read
as it did in March after the schedule is rewritten in June — deleting a schedule
leaves its history intact.

### One execution, one session — enforced by the database

The scheduler does not check whether a session exists. It tries to insert one
and treats the unique violation on `(schedule_id, scheduled_for)` as "already
handled". Two workers, a restart mid-run, or a retry after a timeout all lose a
read-then-write race; the constraint is the coordination, not an `if`.

The same shape is used for digests: `uq_vpeq_digest_period` on
`(schedule_id, frequency, period_start)` where `session_id IS NULL`. Partial,
because immediate reports are one per session and many per schedule.

### Time is computed in the schedule's own zone

A patrol set for 07:00 means 07:00 where the site is. Reading the server clock
works perfectly until the stack is deployed elsewhere, and then every patrol
shifts by hours — or by one hour, twice a year, in any zone with daylight
saving. Sites have no timezone column, so the schedule carries its own.

`latest_due()` checks today **and** yesterday in local time: at 00:30 the
occurrence that matters is usually last night's, and a scheduler that looked
only at today would skip every late-evening patrol, every night.

The lookback is deliberately short (6 hours). Enabling a schedule that started
in January must not manufacture two hundred patrols nobody could have carried
out.

### A missed patrol is recorded, not ignored

Past its grace period with nothing started, a session becomes `MISSED`. Silence
would let a site go uninspected for a week with nothing to show that it had.
Only `SCHEDULED` sessions are swept — one that was started and abandoned is a
different fact, and flattening the two would hide the officer who stopped.

### Snapshots are pulled directly, and failure is a state

`vpatrol_snapshot.capture()` runs a one-shot ffmpeg against the camera's stream
URL. Only `rtsp`, `rtsps`, `http` and `https` are accepted; credentials are
scrubbed from anything logged; concurrency is capped so a large patrol cannot
saturate the host.

An unreachable camera returns `{"ok": false, …}` with **200**, and the officer
sees it and may retry. A 5xx would read as the application being broken rather
than the camera being down.

> ffmpeg 7 removed `-stimeout`; the flag is `-timeout`, in microseconds. The
> wrong one fails *every* capture and presents as every camera being offline.
> There is a test that runs the real command and asserts a *connection* error
> rather than an argument error.

### Reports are stored, not only streamed

Completion renders both formats, writes them under the same
`tenant/site/date` tree as the snapshots, and records a row in
`virtual_patrol_reports` (idempotent per `(session, format)`).

A report that exists only while someone is clicking Download is not evidence.
An agency asked months later to show a site was inspected needs a record that
the report existed, in that format, at that path, at that time. Keeping it
beside the snapshots means evidence and report are retained and purged
together.

Storing is **non-fatal**: an unrenderable report is logged and the patrol still
completes. An officer who walked every camera must not lose the record because
a rendering library failed.

### Email: queued, retried, and never sent from a request

Completing a patrol enqueues a row and returns. An officer standing at the last
camera should not wait on an SMTP handshake, and a mail server that is down must
not make a finished patrol look broken.

The worker claims a row into `PROCESSING` **in its own committed transaction**
before sending, so two workers cannot both take it and a crash mid-send leaves
it `PROCESSING` rather than `PENDING`. One email possibly sent twice is
recoverable; the same email sent by two workers every minute forever is not.

Failures record the reason, back off (1, 5, 15, 60, 240 minutes) and stop after
five attempts — staying `FAILED` with the reason attached, never deleted. An
email nobody can prove was never sent is worse than one plainly marked failed.

**Digests** (`DAILY`/`WEEKLY`/`MONTHLY`) cover a *closed* window computed in the
schedule's zone: yesterday, last Monday–Sunday, or the previous calendar month.
They carry a workbook with a row per patrol plus the findings — not a
concatenation of per-patrol PDFs, since a month of daily patrols is thirty
documents and a hundred megabytes of snapshots that no mail server will accept.
A window with no patrols queues nothing: an agency that receives "0 patrols"
every Monday stops reading Monday's email, and then misses the week something
did go wrong.

### A lost digest can be recovered, deliberately

Retries span about five hours across five attempts, then the row rests at
`FAILED`. For a digest that would be terminal: the uniqueness index allows one
digest per window, so no replacement can ever be queued for it. A mail outage
over a weekend would silently cost a client their weekly summary, with a
`FAILED` row nobody looks at as the only trace.

So `GET /email-queue?status=FAILED` surfaces them and
`POST /email-queue/{id}/resend` puts one back with its error cleared and its
full retry budget restored. `FAILED` only — a `PENDING` row is already going to
be tried, and re-sending a `SENT` one is a different decision that should not
be a side effect of a button labelled "resend".

### Exceptions become incidents in the system that already exists

A failed answer on a question with `failure_action = CREATE_INCIDENT` writes to
the existing `incidents` table. Idempotent — answering twice does not raise two
incidents. No parallel incident concept was introduced.

---

## The RLS trap, twice

Worth stating plainly because it cost real time and would cost it again.

`get_db_with_tenant` sets `app.current_tenant` with `set_config(..., true)`,
which is `SET LOCAL` — scoped to the transaction. Every policy casts it to
`uuid`. So:

1. **Any statement after a commit** runs with the GUC empty, and the cast of
   `''` to uuid *raises* rather than returning no rows. In `process_queue`,
   `_claim()` commits — so the `UPDATE … SET status='SENT'` after a *successful*
   send threw, fell into the failure branch, and marked a delivered report
   `FAILED` for retry. The same report would have gone out five times. The GUC
   is now re-set after the claim.

2. **A cross-tenant worker starts with nothing set at all**, so its opening
   `SELECT` raises and the whole job dies. All three patrol jobs failed every
   two-minute cycle this way, and no test saw it.

And the correction to the first fix: **the tenant must be in the `WHERE` clause
as well as in the GUC.** Leaning on the policy to filter is correct only for a
connection RLS applies to. Under any `BYPASSRLS` role — `postgres`, a
superuser, the `admin_session()` path already used in `scheduler_main` — nothing
filters, and a per-tenant loop collects one copy of every row *per tenant*. On a
database with 2,269 tenants that became 2,275 sends of one email.

---

## Tests

**141 tests across twelve files**, plus a 33-step end-to-end script.

| File | Covers |
|---|---|
| `test_virtual_patrol_schema.py` | Idempotency, constraints, history survival |
| `test_virtual_patrol_service.py` | Answer validation, exceptions, progress, status |
| `test_virtual_patrol_api.py` | Endpoints, RBAC basics, incident raising |
| `test_vpatrol_snapshot.py` | URL schemes, credential scrubbing, real ffmpeg args |
| `test_vpatrol_reports.py` | PDF and Excel contents |
| `test_vpatrol_scheduler.py` | Due detection, timezones, idempotency, missed sweep |
| `test_vpatrol_email.py` | Queue, claiming, backoff, giving up visibly |
| `test_vpatrol_digests.py` | Window boundaries, queued-once, digest contents |
| `test_vpatrol_email_resend.py` | Recovering a failed report or digest |
| `test_vpatrol_command_centre.py` | Board contents and site scoping |
| `test_vpatrol_rls_isolation.py` | All ten tables, read and write, as `svc_app` |
| `test_vpatrol_rbac_matrix.py` | Six permissions × five roles, two layers |

### Two things about the test suite you need to know

**Almost every test connects as `postgres`, which has `BYPASSRLS`.** Under that
role RLS is not evaluated, so those tests cannot see a tenant-GUC bug at all.
`test_vpatrol_rls_isolation.py` and `test_vpatrol_background_jobs_rls.py`
connect as `svc_app` and assert `rolbypassrls` is false *before* asserting
anything else — without that check the test silently degrades into "postgres can
see everything" and passes.

The two layers are complementary, not redundant. The `svc_app` tests see RLS
bugs; the `postgres` tests see bugs RLS was *masking* — the 2,275-sends defect
above was caught by the postgres tests while the `svc_app` tests passed
throughout.

**CI runs the end-to-end script.** `scripts/ops/vpatrol_e2e.py` walks all 33
steps of §52 and the digest path against a live stack — real login, real scheduler, real RTSP
capture, real reports — and it is the only thing that exercises the full flow.
The backend job starts `mediamtx`, waits for `cam1` to publish, and runs it.

No real camera is needed: mediamtx generates the streams from the video files
committed under `docker/mediamtx/videos`, and the script seeds its own tenant,
site and cameras against them, then deletes the tenant on the way out. It is
hermetic — it runs against a database that has only had its migrations applied.
Pointed at a database that *does* have the named site, it uses that instead and
leaves the session and report behind as evidence.

**Snapshot capture is retried up to three times**, because a real capture really
does fail sometimes — a busy stream, a slow handshake, a camera mid-keyframe.
The application is right to refuse to complete a camera whose snapshot failed,
and an officer in that position presses Retake. Without the retry an ordinary
transient turns into a red build, which is how a CI step stops being trusted;
this was caught by the script failing that way on its own second run.

Locally:

```bash
docker exec docker-api-1 python /tmp/vpatrol_e2e.py
```

Every defect listed in this document was found by running something, not by
reading it. That is why this is a CI step and not a runbook entry.

---

## Known limitations

- **Snapshot capture takes 9–15 seconds.** The officer screen shows a
  "Capturing…" state, but offers no progress beyond that, and a slow camera
  simply feels slow.
- **SMTP is unconfigured in development**, so delivery is verified only to the
  point of the connection attempt. The queue, claim, backoff and give-up paths
  are all tested.
- **PWM wage figures are not seeded.** `scripts/ops/pwm_rates.csv` has blank
  amounts on purpose; floor enforcement returns `NOT_ASSESSED` until real
  gazetted figures are entered. Unrelated to patrolling, but outstanding.
- **Report backfill.** Patrols completed before `store_reports` existed have no
  stored report row. They still render on demand.
