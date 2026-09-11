# Virtual Patrolling — Gap Analysis

**Date:** 2026-09-11
**Status:** Phase 1 complete. Implementation not started.
**Rule followed:** inspect before modifying; integrate, never duplicate.

---

## 1. The finding that changes the plan

**`patrol_sessions` already exists.** So do `patrol_routes`, `patrol_checkpoints`
and `checkpoint_scans`. They belong to the **physical** guard-patrol feature — a
guard walking a route scanning QR/NFC checkpoints, with SOS.

The specification proposes `patrol_schedules`, `patrol_sessions`,
`patrol_session_cameras`, `patrol_session_questions`, `patrol_session_answers`,
`patrol_reports`, `patrol_email_queue`. **`patrol_sessions` is a direct
collision** and the rest sit confusingly close to a live feature.

**Decision: every new table takes a `virtual_patrol_` prefix.**

| Spec name | Actual name |
|---|---|
| `patrol_schedules` | `virtual_patrol_schedules` |
| `patrol_schedule_cameras` | `virtual_patrol_schedule_cameras` |
| `patrol_camera_questions` | `virtual_patrol_questions` |
| `patrol_email_recipients` | `virtual_patrol_email_recipients` |
| `patrol_sessions` | `virtual_patrol_sessions` |
| `patrol_session_cameras` | `virtual_patrol_session_cameras` |
| `patrol_session_questions` | `virtual_patrol_session_questions` |
| `patrol_session_answers` | `virtual_patrol_session_answers` |
| `patrol_reports` | `virtual_patrol_reports` |
| `patrol_email_queue` | `virtual_patrol_email_queue` |

Same reasoning for the API namespace (`/api/v1/virtual-patrol`, distinct from the
existing `/api/v1/patrols`) and permissions (`vpatrol:*`, not `patrol:*` — the
latter is taken by physical patrols and granting it would silently widen access
to a different feature).

---

## 2. Existing architecture found

| Concern | Implementation | Reuse |
|---|---|---|
| Backend | FastAPI + SQLAlchemy `text()` + asyncpg | Yes |
| Frontend | React 19 + MUI v9 + Vite, TanStack Query | Yes |
| Database | PostgreSQL 16, **FORCE ROW LEVEL SECURITY** | Yes |
| Migrations | Alembic, head `0115` | Yes |
| Auth | JWT, `get_token_payload`, tenant baked into token | Yes |
| RBAC | `require_permission("code")` against `role_permissions` | Yes |
| Multi-tenancy | RLS via `app.current_tenant` GUC, `get_db_with_tenant` | Yes |
| Tenants / Sites / Cameras / Users | `tenants`, `sites`, `cameras`, `users` | Yes — no new entities |
| Incidents | `incidents`, `incident_notes`, `incident_status_history` | Yes |
| Evidence | `evidence` (**monthly partitions**), `evidence_access_log`, `EVIDENCE_ROOT` | Yes |
| Notifications | `notification_channels`, `notification_rules`, `notification_logs` | Yes |
| Report delivery | `report_schedules`, `report_deliveries`, `scheduler_main._send_report_email` | Yes |
| Background worker | `scheduler_main.run_once()`, separate container | Yes |
| Redis | present; rate limiting, pub/sub | Yes |
| Audit | `audit_logs`, **monthly partitions, HMAC hash chain** via `services/audit.write_audit_log` | Yes |
| PDF | **reportlab 5.0.0** | Yes |
| Camera live view | `services/hls_stream.py` — ffmpeg → HLS | Yes |
| Ingestion | `ingestion_main.py` — RTSP decode loop, jpeg encode | Partly |

### Reusable components identified

- `require_permission()` — do not build authorization
- `get_db_with_tenant()` — sets the RLS GUC; do not bypass
- `services/audit.write_audit_log()` — **writes the HMAC chain**; hand-inserting
  audit rows would produce entries the verifier skips
- `services/alert_routing.resolve_push_targets` — who to notify
- `scheduler_main.run_once()` — the single registration point for periodic jobs
- reportlab usage in `routers/reports.py`, `payroll.py`, `invoicing.py`

---

## 3. Missing — database entities

All ten tables above. None exist in any form.

## 4. Missing — backend

- **Snapshot-on-demand.** This is the largest gap and the spec's §14 depends on
  it. `services/camera_service.py` is a *health/backoff tracker*, not a capture
  service — it has no snapshot function. `hls_stream.py` runs ffmpeg to produce
  HLS segments for live view. `ingestion_main.py` decodes RTSP and encodes JPEG,
  but exposes no "capture one frame now" entry point.
  **Nothing in the platform can currently be asked for a single frame.**
- Virtual patrol CRUD services and routers
- Question engine (6 types, validation, failure actions)
- Session creation with configuration snapshotting
- Scheduler job (timezone-aware, idempotent)
- Missed-patrol detection with grace period
- Email queue with retry/attempt tracking
- Excel generation

## 5. Missing — reporting

- **Excel is not available at all.** Exports today are CSV via `csv.DictWriter`
  (`routers/exports.py`). There is no `openpyxl`, `xlsxwriter` or `pandas` in
  `requirements.txt`. §24 requires a four-sheet workbook, so this needs a **new
  dependency** — the only new runtime dependency this feature requires.
- PDF: reportlab is present and used; the virtual patrol report is new document
  construction, not new capability.

## 6. Missing — email automation

`report_schedules` / `report_deliveries` plus `_send_report_email` exist and are
the right pattern, but they are bound to the existing scheduled-report feature.
§21 needs a queue with `PENDING/PROCESSING/SENT/FAILED`, attempts, last error and
retry — which `report_deliveries` does not model.

## 7. Missing — frontend

Eleven screens (§32). None exist. The Payroll page's tab pattern and `GlassCard`
/ `PageHeader` / `PermissionGuard` conventions apply directly.

## 8. Missing — permissions

`vpatrol:read`, `vpatrol:manage`, `vpatrol:execute`, `vpatrol:report`,
`vpatrol:export`, `vpatrol:email`. Seeded via the established
`INSERT INTO permissions (code, description, category)` pattern, then granted to
roles. **Duty Officers get `vpatrol:execute` only** — never `vpatrol:manage`
(§28).

## 9. Missing — tests

Everything in §43. Existing conventions: `backend/tests/`, pytest + asyncio,
module-level `from app.main import app` (or the first test absorbs a 120s ML
import), run via `docker exec docker-api-1 python -m pytest`.

---

## 10. Integration points

| Integration | How |
|---|---|
| Sites / Cameras | FK to existing `sites.id`, `cameras.id`. Camera list filtered by site. |
| Incidents | `CREATE_INCIDENT` failure action writes through the existing incident tables, referencing session, camera, question, answer, snapshot. |
| Evidence | Snapshots stored under `EVIDENCE_ROOT` following the existing storage-path convention, served through the authorized evidence route — never a raw path. |
| Audit | `write_audit_log()` for every §29 action, so entries join the existing hash chain. |
| Scheduler | New functions called from `run_once()` alongside the existing jobs. |
| Command Centre | Read-only surfacing of active/missed/exception patrols (§30). |
| RLS | Every tenant-owned table gets `ENABLE` + `FORCE ROW LEVEL SECURITY` and a `tenant_isolation_*` policy matching the `man_down_events` pattern. |

---

## 11. Risks

1. **Snapshot capture is unbuilt and is the feature's evidentiary core.** If the
   camera is offline the patrol must record `SNAPSHOT_FAILED`, never silently
   complete (§40). Capturing via a one-shot ffmpeg pull from the camera's RTSP
   URL is the lowest-risk option: it reuses the URL and credential decryption
   already in `streams`/`hls_stream`, and does not disturb the ingestion loop.
2. **Memory.** This box has 7.7 GB with ~0.5 GB free running only the core seven
   services. ffmpeg snapshot processes are short-lived but real. Concurrency must
   be bounded.
3. **Scheduler idempotency.** Multiple workers or a restart must not double-create
   a session. Enforced by a **unique constraint on
   `(schedule_id, scheduled_for)`** — database-level, not application-level.
4. **Configuration snapshotting (§4).** The hard rule. A running patrol must not
   see edits to its schedule. Enforced by copying cameras and questions into
   session tables at creation and never joining back to configuration at read
   time.
5. **Naming collision** with physical patrols — mitigated by the prefix above.
6. **New dependency** (`openpyxl`) needs to land in `requirements.txt` and the
   Docker image, which on this machine means a slow rebuild.
7. **Timezones.** Schedules run in the site's timezone; storage is UTC. Recurrence
   must be computed in the schedule's zone or DST will shift every patrol.

---

## 12. Implementation plan

| Phase | Deliverable |
|---|---|
| 1 ✅ | Inspection + this document |
| 2 | Migration `0116`: 10 tables, FKs, indexes, constraints, RLS policies, permissions seed |
| 3 | Services: `virtual_patrol.py` (session lifecycle, snapshotting config), `vpatrol_snapshot.py` (ffmpeg one-shot), `vpatrol_reports.py` |
| 4 | Routers: schedules, cameras, questions, email settings, officer execution, reports |
| 5 | Admin UI: dashboard, schedule list/create/edit, camera sequence, question builder, email config |
| 6 | Duty Officer execution screen |
| 7 | Snapshot integration + failure states |
| 8 | Incident integration on failure actions |
| 9 | PDF (reportlab) + Excel (openpyxl) |
| 10 | Scheduler: due detection, idempotent creation, missed-patrol sweep |
| 11 | Email queue + retry + aggregate reports |
| 12 | Command Centre surfacing |
| 13 | Tests: RLS, RBAC, scheduler, execution, reports, email, incidents |
| 14 | End-to-end verification against §52 |

**Scope note.** This is a large module — ten tables, ~25 endpoints, eleven
screens, two report formats, a scheduler and an email queue. It will be delivered
across multiple working sessions, phase by phase, each verified before the next.
Anything reported as done will have been run.
