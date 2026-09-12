# Virtual Patrolling — API reference

All routes are prefixed `/api/v1/virtual-patrol` unless shown otherwise, take a
bearer token, and are tenant-scoped by row-level security. The tenant comes from
the token and is never accepted from the client.

**Not to be confused with `/api/v1/patrols`**, which is the *physical* guard
patrol — routes, checkpoints, QR scans. Different feature, different permissions
(`patrol:*`, not `vpatrol:*`), deliberately separate namespace.

---

## Permissions

Six permissions, seeded by migration `0116`.

| Permission | Covers |
|---|---|
| `vpatrol:read` | View schedules, sessions, history |
| `vpatrol:manage` | Create and modify schedules, cameras, questions |
| `vpatrol:execute` | Carry out an assigned patrol |
| `vpatrol:report` | View and download the PDF report |
| `vpatrol:export` | Export to Excel |
| `vpatrol:email` | Configure recipients and frequency |

Role grants as seeded:

| Role | Grants |
|---|---|
| Admin (2), Manager (8) | all six |
| Supervisor (3) | `read`, `execute`, `report` |
| Operator (4), Guard (5) | `read`, `execute` |
| Everyone else | none |

Two audiences, two permissions, and the split is load-bearing: everything that
*shapes* a patrol needs `manage`, everything that *carries one out* needs
`execute`. A duty officer handed a patrol must not acquire the ability to
rewrite the questions they are about to be asked, so no execution route accepts
configuration and no configuration route is reachable with `execute` alone.

`vpatrol:execute` is permission to run **your** patrol, not anyone's. Every
execution route checks the caller against `officer_user_id` and answers 403
otherwise — without it, every guard in a tenant could answer on behalf of every
other, and the report would carry the wrong name against the evidence.

---

## Endpoints

### Schedules — `vpatrol:manage` to change, `vpatrol:read` to view

| Method | Path | Permission |
|---|---|---|
| GET | `/schedules` | `read` |
| POST | `/schedules` | `manage` |
| GET | `/schedules/{schedule_id}` | `read` |
| PUT | `/schedules/{schedule_id}` | `manage` |
| PATCH | `/schedules/{schedule_id}/status` | `manage` |
| DELETE | `/schedules/{schedule_id}` | `manage` |

`GET /schedules` accepts optional `site_id`, `enabled`, `limit`, `offset`.

**`POST /schedules`**

```json
{
  "site_id": "uuid",
  "name": "Morning Security Patrol",
  "description": null,
  "timezone": "Asia/Singapore",
  "schedule_type": "DAILY",
  "start_date": "2026-01-01",
  "end_date": null,
  "patrol_time": "07:00:00",
  "weekdays": [],
  "grace_minutes": 15,
  "enabled": true,
  "assigned_user_id": "uuid or null",
  "assigned_role_id": null,
  "email_frequency": "IMMEDIATE"
}
```

- `schedule_type` — `ONCE` | `DAILY` | `WEEKLY`
- `weekdays` — required and non-empty for `WEEKLY`; 1 = Monday … 7 = Sunday
- `email_frequency` — `IMMEDIATE` | `DAILY` | `WEEKLY` | `MONTHLY`
- `timezone` — the schedule's own zone. `patrol_time` means that time *there*.

422 with a sentence, not a constraint name, for: a weekly patrol with no
weekday, an unknown `schedule_type`, an unknown `email_frequency`.

### Cameras on a schedule

| Method | Path | Permission |
|---|---|---|
| GET | `/schedules/{schedule_id}/cameras` | `read` |
| POST | `/schedules/{schedule_id}/cameras` | `manage` |
| PUT | `/schedules/{schedule_id}/cameras/reorder` | `manage` |
| DELETE | `/schedules/{schedule_id}/cameras/{schedule_camera_id}` | `manage` |

`POST` body: `{"camera_id": "uuid", "sequence_no": 1, "timeout_seconds": null}`.
Refused if the camera belongs to another site, or is already on the schedule.

`PUT .../reorder` takes a list: `[{"schedule_camera_id": "uuid", "sequence_no": 1}, …]`.
The uniqueness constraint on `(schedule_id, sequence_no)` is `DEFERRABLE
INITIALLY DEFERRED`, so a whole reorder commits atomically instead of failing
halfway through a swap.

### Questions

| Method | Path | Permission |
|---|---|---|
| GET | `/schedule-cameras/{schedule_camera_id}/questions` | `read` |
| POST | `/schedule-cameras/{schedule_camera_id}/questions` | `manage` |
| DELETE | `/questions/{question_id}` | `manage` |

```json
{
  "question_text": "Is the camera view unobstructed?",
  "question_type": "YES_NO",
  "is_required": true,
  "sequence_no": 1,
  "options": null,
  "failure_action": "CREATE_INCIDENT"
}
```

- `failure_action` — `NONE` (record only) or `CREATE_INCIDENT`
- A choice question with no options is refused at both the API and the database.
  Discovering it as an officer, standing at the camera, mid-patrol, is not an
  acceptable way to find out.

### Email configuration

| Method | Path | Permission |
|---|---|---|
| GET | `/schedules/{schedule_id}/email-recipients` | `read` |
| POST | `/schedules/{schedule_id}/email-recipients` | `email` |
| DELETE | `/email-recipients/{recipient_id}` | `email` |
| GET | `/email-queue` | `email` |
| POST | `/email-queue/{queue_id}/resend` | `email` |

`POST /email-recipients` takes `?email=` as a query parameter.

`GET /email-queue` accepts `?status=` (`PENDING`/`PROCESSING`/`SENT`/`FAILED`),
`limit`, `offset`. `?status=FAILED` answers "did anything not go out?", which is
otherwise unanswerable without database access.

**`POST /email-queue/{id}/resend`** puts a failed report or digest back in the
queue, clearing the error and restoring the full retry budget. This is the only
recovery path for a lost digest, and it exists because
`uq_vpeq_digest_period` allows exactly one digest per window: once the five
attempts (about five hours) are spent, no replacement can be queued, so a mail
outage over a weekend would otherwise cost a client their weekly summary
permanently.

Refuses anything that is not `FAILED` with **409**, naming the current status.
A `PENDING` row is already going to be tried, and a second copy of an email that
did arrive is a different decision — not a side effect of a button labelled
"resend". An unknown id is **404**, because a typo and a wrong state are
different problems.

### Execution — `vpatrol:execute`, and only your own patrol

| Method | Path |
|---|---|
| GET | `/my-patrols` |
| POST | `/sessions/{session_id}/start` |
| GET | `/sessions/{session_id}/current-camera` |
| POST | `/sessions/{session_id}/cameras/{session_camera_id}/snapshot` |
| POST | `/sessions/{session_id}/cameras/{session_camera_id}/answers` |
| POST | `/sessions/{session_id}/cameras/{session_camera_id}/complete` |
| POST | `/sessions/{session_id}/complete` |

**`GET /current-camera`** returns the next camera needing attention, with its
frozen questions:

```json
{
  "camera": {
    "id": "uuid", "sequence_no": 1, "camera_name": "Car Park Ramp",
    "status": "PENDING", "snapshot_path": null, "snapshot_taken_at": null,
    "snapshot_error": null, "officer_notes": null, "location": null
  },
  "questions": [
    {"id": "uuid", "question_text": "…", "question_type": "YES_NO",
     "is_required": true, "sequence_no": 1, "options": null,
     "failure_action": "CREATE_INCIDENT", "answer_text": null, "answer_json": null}
  ]
}
```

`{"camera": null, "message": "Every camera on this patrol is done."}` once
finished — that `null` is the client's only loop exit.

**`POST .../snapshot`** captures a frame over RTSP, right then. It answers **200
with `{"ok": false, …}` for an unreachable camera**, not a 5xx: an offline
camera is an expected outcome the officer must see and may retry, and a 500
would read as the application being broken. Capture takes roughly 9–15 seconds
against a real camera.

**`POST .../answers`**

```json
{"answers": [{"session_question_id": "uuid", "answer": "NO"}],
 "officer_notes": "optional"}
```

Returns `{"saved": 2, "incidents_raised": ["uuid"]}`. Answering twice does not
raise a second incident.

**`POST /sessions/{id}/complete`** returns
`{"session_id": "…", "status": "COMPLETED", "reports_stored": ["PDF","XLSX"],
"report_email_queued": true}`. Completion writes both reports to storage and
queues the email; a report that cannot be rendered is logged and does **not**
fail the completion — an officer who walked every camera must not lose the
record because reportlab failed.

### History and reports

| Method | Path | Permission |
|---|---|---|
| GET | `/sessions` | `read` |
| GET | `/sessions/{session_id}` | `read` |
| GET | `/sessions/{session_id}/report/pdf` | `report` |
| GET | `/sessions/{session_id}/report/excel` | `export` |
| GET | `/sessions/{session_id}/cameras/{session_camera_id}/snapshot` | *inline* — see below |

`GET /sessions` accepts `site_id`, `status`, `limit`, `offset`.

**The snapshot image endpoint is the exception to the pattern.** An `<img>` tag
cannot send an `Authorization` header, so the token rides in `?token=` — the
same approach as evidence images and payslip PDFs. Because the usual dependency
cannot run, the endpoint checks **inside the handler**:

1. the token decodes, or 401;
2. the role holds `vpatrol:read` (looked up in `role_permissions`), or 403;
3. the row is read with the tenant from the token, so another tenant's id
   returns 404;
4. the caller's site assignments are applied — a supervisor restricted to other
   sites gets **404, not 403**, because 403 would confirm the row exists and
   tell them a particular camera was patrolled at a particular time.

The stored path is never returned to a client. A storage path handed to a
browser is an invitation to walk the directory.

### Command Centre

`GET /api/v1/command-centre/virtual-patrol` — `vpatrol:read`, site-scoped.
Returns `{"summary": {...}, "in_progress": [...], "exceptions": [...]}`. Only
failed answers reach `exceptions`; every answered question would bury the one
that matters.

---

## Notes for callers

**Optional filters are cast on both sides.** Used once bare and once cast,
Postgres cannot infer a single type for a parameter and answers
`AmbiguousParameterError`, which surfaces as a 500 on a page's first request.
Every optional filter is written `(CAST(:p AS uuid) IS NULL OR col = CAST(:p AS uuid))`.

**Configuration is frozen at session creation.** The officer's routes read the
session tables, never the schedule. A mid-patrol edit cannot change what the
officer is being asked, and history survives the deletion of the schedule that
produced it.

**Validation is repeated server-side even though the browser does it.** A patrol
is evidence, and anyone with curl can skip a browser. A required question
answered only in the UI is a required question that was never asked.
