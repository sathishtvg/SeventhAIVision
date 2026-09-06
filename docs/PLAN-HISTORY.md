# Plan History — recovered planning record

> **What this is.** Every implementation plan written for this project during
> the Claude Code sessions of 13 Jun – 17 Jul 2026, recovered on 2026-09-05
> from the session transcripts after the project folder was renamed from
> `D:\Virtual Patrolling` to `D:\Claude Project\Virtual Patrolling`.
>
> **What it is not.** It is a record of what was *planned*, in the order it was
> planned — not a specification of what the system does now. It was last
> revised **17 Jul 2026** while work continued to **14 Aug 2026**, so the final
> month is not represented here. Where this document and the code disagree,
> the code is right. For current behaviour read `docs/architecture.md` and
> the source.
>
> The plan grew by accretion: each round of work appended a new section rather
> than editing earlier ones. Twenty-six revisions were captured in total; this
> is the last and most complete, and it contains all the earlier ones.
>
> Checked for credentials before committing — none present.

## Rounds, in order

- Seventh AI Vision — Phase 10 (Enterprise UX Upgrade) Implementation Plan
- Seventh AI Vision — Phase 1 Implementation Plan (Multi-Module AI Backend + Real-Time Platform)
- Round: Command Centre, Live Wall, Zone Drawing, Admin Hierarchy (Web + Desktop)
- Round: Live Wall — True Operator Control Room
- Round: ShiftSecure Integration — Roadmap + Phase 1 (Employee Data Model)
- Round: ShiftSecure Phase 2A — Attendance Hardening + Live Monitor
- Round: ShiftSecure Phase 2B — Roster Rebuild (Employee×Day Grid + AI Auto-Scheduler)
- Round: Manager Role + 30-Day Roster + Editable Published Shifts + Urgent Reassign + Timezone Fix
- Round: Selfie Check-In/Out Photos + Liveness + Anti-Mock-GPS + Site Geofence Config
- Round: Violations Module
- Round: Leave Management Module
- Round: Payroll / CPF / IR8A Module
- Round: SOP Training Rebuild — Real Quiz Engine
- Round: Client Billing / Invoicing — Final ShiftSecure Item
- Round: Mobile Parity — Live Wall, Zone Drawing, My Violations/Leave, Command Centre Taps
- Round: Client Portal Invoice Viewing
- Round: Daily-Rate Pay, Guards Grid, Unified Guard Creation
- Round: Live Attendance Redesign + Cross-Role Action Center (Duty Guidance)
- Round: Live Wall Multi-Screen Control Room + Real Pop-Out Windows

---

# Seventh AI Vision — Phase 10 (Enterprise UX Upgrade) Implementation Plan

---

## Context

Phases 1–9 delivered the full backend AI platform (8 modules, multi-tenant RBAC, real-time WebSocket push, notifications, analytics, pgvector face matching, export, tenant management, mobile app). The user now requests a major enterprise UX and infrastructure uplift:

1. **Sites hierarchy** — cameras grouped under sites, multiple simultaneous live streams, stream recording
2. **Camera stream validation** — test RTSP URL + credentials before saving
3. **Multi-site operator UX** — filter alerts/incidents by site and by which analytics are active
4. **Super-admin module licensing** — per-tenant control of which AI modules are purchased/enabled
5. **Windows desktop application** — Electron wrapper of the existing React frontend, packaged as .exe
6. **Dedicated enhanced dashboard** — rich site health overview, camera grid, live feed, analytics sparklines

**Existing foundation (no changes needed):**
- DB: PostgreSQL 16 + RLS (cameras, streams, alerts, incidents, detections — all tenant-scoped)
- Cameras have `location` string and lat/lon but no site grouping layer — `site_id` must be added
- Streams table: `url`, `protocol`, `status` — no credential storage — must be added
- Alert/incident APIs have no `site_id` filter or `site_name` in response — must be joined through camera
- Tenant settings exist but no per-module license gates — new `tenant_module_licenses` table needed
- MJPEG live streaming works for single stream — multi-view is purely a frontend layout change + a new recordings feature

---

## Part A — Database Migration 0006

**File:** `backend/alembic/versions/0006_sites_licensing_recordings.py`

### A1. `sites` table
```sql
CREATE TABLE sites (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        VARCHAR(255) NOT NULL,
  address     TEXT,
  description TEXT,
  latitude    DOUBLE PRECISION,
  longitude   DOUBLE PRECISION,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sites_tenant_id ON sites(tenant_id);
```
RLS: full `ENABLE / FORCE / USING / WITH CHECK` pattern (same as every other tenant table).

### A2. Add `site_id` to cameras
```sql
ALTER TABLE cameras ADD COLUMN site_id UUID REFERENCES sites(id) ON DELETE SET NULL;
CREATE INDEX idx_cameras_site_id ON cameras(site_id);
```
Nullable — existing cameras without a site still work.

### A3. Add `auth_config` to streams (credential storage)
```sql
ALTER TABLE streams ADD COLUMN auth_config JSONB NOT NULL DEFAULT '{}';
-- schema: { "username": "...", "password": "..." }
-- password stored as-is in dev; in prod, PGP-encrypted with pgcrypto (noted as upgrade path)
```

### A4. `tenant_module_licenses` table
```sql
CREATE TABLE tenant_module_licenses (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  module_type             VARCHAR(30) NOT NULL,
  is_enabled              BOOLEAN NOT NULL DEFAULT TRUE,
  max_cameras             INTEGER,            -- NULL = unlimited
  licensed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at              TIMESTAMPTZ,        -- NULL = perpetual
  licensed_by_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
  notes                   TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, module_type)
);
CREATE INDEX idx_tenant_module_licenses_tenant ON tenant_module_licenses(tenant_id);
```
RLS applied. No `WITH CHECK` restriction on tenant_id — super admin writes cross-tenant via `get_raw_db()` (same pattern as `tenants.py` router).

### A5. `recordings` table
```sql
CREATE TABLE recordings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  camera_id       UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
  stream_id       UUID NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
  site_id         UUID REFERENCES sites(id) ON DELETE SET NULL,
  status          VARCHAR(20) NOT NULL DEFAULT 'recording',  -- recording | completed | failed
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at        TIMESTAMPTZ,
  file_path       VARCHAR(500),
  file_size_bytes BIGINT,
  duration_seconds INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_recordings_tenant_camera ON recordings(tenant_id, camera_id, started_at DESC);
```
RLS applied.

### A6. New permissions seed
```sql
INSERT INTO permissions (code, description, category) VALUES
  ('site:manage',      'Create / edit / delete sites',          'site'),
  ('recording:create', 'Start / stop stream recordings',         'recording'),
  ('recording:read',   'View recording list and download',        'recording'),
  ('license:manage',   'Manage per-tenant module licenses',       'license');
```
Grant matrix additions:
- `site:manage` → super_admin, admin, supervisor
- `recording:create` → super_admin, admin, supervisor, operator
- `recording:read` → super_admin, admin, supervisor, operator, security_guard, viewer
- `license:manage` → super_admin only

---

## Part B — Backend: Sites API

**New file:** `backend/app/routers/sites.py`  
**Prefix:** `/api/v1/sites`  
**Uses:** `get_db_with_tenant` (RLS-scoped session)

| Method | Path | Permission | Body / Params |
|--------|------|------------|----------------|
| GET | `/` | `site:manage` | query: `is_active=true` |
| POST | `/` | `site:manage` | `name`, `address`, `description`, `latitude`, `longitude` |
| GET | `/{site_id}` | `site:manage` | — |
| PUT | `/{site_id}` | `site:manage` | partial update same fields |
| DELETE | `/{site_id}` | `site:manage` | soft-delete (`is_active=FALSE`) |
| GET | `/{site_id}/cameras` | `camera:read` | cameras belonging to site |

Register in `backend/app/main.py`.

---

## Part C — Backend: Camera stream validation endpoint

**New endpoint in `backend/app/routers/cameras.py`:**

```python
POST /api/v1/cameras/validate-stream
# Permission: camera:create (or camera:update — any camera writer)
# Body: { url: str, username: str | None, password: str | None }
# Returns: { valid: bool, resolution_w: int | None, resolution_h: int | None,
#            fps: float | None, latency_ms: int | None, error: str | None }
```

**Implementation:**
- Build full RTSP URL: if username+password, prepend `rtsp://user:pass@...` (or embed in URL per RFC 3986)
- `cv2.VideoCapture(full_url)` with 5-second timeout (use `CAP_PROP_OPEN_TIMEOUT_MSEC`)
- `cap.read()` once → measure latency, extract `CAP_PROP_FRAME_WIDTH`, `CAP_PROP_FRAME_HEIGHT`, `CAP_PROP_FPS`
- Release immediately after test
- All CV2 calls wrapped in `asyncio.to_thread()` (non-blocking)
- Return structured result — never raise 500 on connection failure, always return `{ valid: false, error: "..." }`

**Update `POST /api/v1/cameras/{id}/streams`:** Accept optional `username` and `password` in body; store as `auth_config = {"username": ..., "password": ...}` in streams row.

**Update ingestion service (`backend/app/ingestion_main.py`):** When opening RTSP URLs, read `auth_config` from the stream row and embed credentials in URL before passing to `cv2.VideoCapture`.

**Update live streaming (`backend/app/routers/streams.py`):** Same credential embedding for the MJPEG proxy endpoint.

---

## Part D — Backend: Recording API

**New endpoints in `backend/app/routers/streams.py`:**

```
POST /{camera_id}/streams/{stream_id}/recordings/start
  Permission: recording:create
  → inserts recordings row (status='recording'), starts background task writing JPEG frames to MP4
  → returns { recording_id, started_at }

POST /{camera_id}/streams/{stream_id}/recordings/{recording_id}/stop
  Permission: recording:create
  → sets status='completed', ended_at, file_size, duration

GET /{camera_id}/streams/{stream_id}/recordings
  Permission: recording:read
  → list recordings for stream

GET /recordings/{recording_id}/download
  Permission: recording:read
  → FileResponse for the MP4 file
```

**Recording background task:**
- `asyncio.create_task(_record_stream(recording_id, stream_url, auth_config, file_path))`
- Uses `cv2.VideoCapture` → `cv2.VideoWriter` (mp4v codec, target 10 FPS for storage efficiency)
- Stored under `RECORDINGS_ROOT` env var (new Docker volume `recordings_data`)
- Path convention: `{tenant_id}/{camera_id}/{recording_id}.mp4`
- Active recording tasks tracked in `app.state.active_recordings: dict[uuid, asyncio.Task]`

---

## Part E — Backend: Tenant Module Licensing API

**New file:** `backend/app/routers/licenses.py`  
**Prefix:** `/api/v1/licenses`  
**Uses:** `get_raw_db()` (no RLS — super admin reads/writes across tenants)  
**All routes require:** `license:manage` (super_admin only)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{tenant_id}` | List all licenses for a tenant (all 8 modules shown, enabled or not) |
| PUT | `/{tenant_id}/{module_type}` | Enable / disable module, set max_cameras, expires_at |
| DELETE | `/{tenant_id}/{module_type}` | Revoke license (sets is_enabled=false) |

**License check enforcement:**
1. `POST /api/v1/cameras` and `PUT /api/v1/cameras/{id}`: before saving `ai_modules_enabled`, query `tenant_module_licenses` and filter to only licensed+enabled modules. Return `422` with detail listing unlicensed modules if any are requested.
2. AI workers: add `_check_tenant_license(tenant_id, module_type)` helper in `ai-worker/worker/common/tenant_settings_cache.py` (same 30s TTL cache). Worker skips frame if module not licensed.

**Seed initial licenses:** On tenant creation (in `tenants.py` router `POST /`), auto-insert license rows for all 8 modules with `is_enabled=FALSE` as default. Super admin then enables what the company purchased.

Register in `main.py`.

---

## Part F — Backend: Alert & Incident API site enrichment

**Update `backend/app/routers/alerts.py`:**
- `GET /api/v1/alerts`: add optional `?site_id=<uuid>` filter (JOIN cameras ON alerts.camera_id = cameras.id WHERE cameras.site_id = :site_id)
- Response: add `site_id`, `site_name` to each alert row (LEFT JOIN sites)

**Update `backend/app/routers/incidents.py`:**
- `GET /api/v1/incidents`: add optional `?site_id=<uuid>` filter (same JOIN pattern)
- Response: add `site_id`, `site_name`

**Update `backend/app/routers/analytics.py`:**
- `GET /api/v1/analytics/summary`: add optional `?site_id=<uuid>` filter throughout all sub-queries

---

## Part G — Frontend: Sites management page

**New file:** `frontend/src/pages/Sites.tsx`  
**New file:** `frontend/src/api/sites.ts`  
**Route:** `/sites`  
**Permission gate:** `site:manage`

UI: Table of sites with columns: Name, Address, Camera count, Status, Actions (Edit / Deactivate).  
Row expand or drawer: shows cameras belonging to site with quick links.  
"Add Site" dialog: name (required), address, description, lat/lon fields.

**Update `frontend/src/pages/Cameras.tsx`:**
- Add "Site" dropdown in Camera Create/Edit dialog (lists available sites, "No site" = null)
- Show site name as a subtitle under camera name in camera cards
- Add site filter at top of page: `All Sites | Site A | Site B …`

**Update `frontend/src/components/layout/Sidebar.tsx`:** Add Sites link under Camera section, visible to `site:manage` roles.

---

## Part H — Frontend: Stream validation UX

**Update `frontend/src/pages/Cameras.tsx` (StreamDialog component):**
- Add "Username" and "Password" TextInput fields in the dialog (optional)
- Add **"Test Connection"** button that calls `POST /api/v1/cameras/validate-stream`
- Show a result chip: ✓ Connected — 1920×1080 @ 25fps | ✗ Failed — Connection refused
- **Save button disabled until test passes** (or user explicitly overrides with a "Save anyway" link)
- On save: pass `username` and `password` along with URL to `POST /{camera_id}/streams`

---

## Part I — Frontend: Multi-view live streaming page (LiveWall)

**New file:** `frontend/src/pages/LiveWall.tsx`  
**New file:** `frontend/src/api/recordings.ts`  
**Route:** `/live`  
**Permission gate:** `camera:read`

**Layout controls (top bar):**
- Grid selector: 1×1, 2×2, 3×3, 4×4 (1, 4, 9, 16 cells)
- Site filter dropdown
- "Add Camera to Wall" button → camera picker dialog (search by name, shows site)

**Each cell:**
- Live `<img src="/api/v1/cameras/{id}/streams/{sid}/live?token=...">` (same MJPEG pattern)
- Camera name + site overlay (top-left)
- Status badge
- Record button: starts/stops recording via API, shows red REC indicator with elapsed time
- Full-screen button (single cell expands)
- Close (X) button removes from wall

**Wall state:** Stored in `localStorage` as `[{ camera_id, stream_id }]` — persists on refresh.

**Recording list:** Small "Recordings" drawer accessible from sidebar, lists past recordings with download links.

**Sidebar update:** Add "Live Wall" nav item with camera icon, visible to `camera:read`.

---

## Part J — Frontend: Enhanced Alerts & Incidents with site filter

**Update `frontend/src/pages/Alerts.tsx`:**
- Add Site filter chip-group above the table (alongside existing status filters): "All Sites | Site A | Site B …"
- Add "Module" filter chip-group: "All | LPR | Face | Intrusion | PPE | …"
- Add Site name column to the table
- API call passes `?site_id=...` and `?module_type=...` when filters are active

**Update `frontend/src/pages/Incidents.tsx`:**
- Same site + module filter chips above the incident table
- Add Site name column
- Incident drawer: show originating site name prominently

**Update `frontend/src/api/alerts.ts` and `incidents.ts`:**  
Add `site_id?: string` and `module_type?: string` optional params to list functions.

---

## Part K — Frontend: Super-admin licensing page

**Update `frontend/src/pages/Tenants.tsx`:**
- Add a "Manage Licenses" button per tenant row → opens a full-page drawer or dialog
- Shows 8 module toggles (lpr, face, intrusion, ppe, crowd, fire_smoke, weapon, behavior)
- Each toggle: Enable/Disable switch + optional "Max cameras" input + optional "Expires" date picker
- Calls `PUT /api/v1/licenses/{tenant_id}/{module_type}` on change
- License status shown as colored badge on tenant row (e.g., "5/8 modules" — how many active)

**New file:** `frontend/src/api/licenses.ts`

---

## Part L — Frontend: Enhanced Dashboard

**Rewrite `frontend/src/pages/Dashboard.tsx`** — keep glassmorphism theme, restructure layout:

**Section 1 — Site Selector (top bar):**
- Dropdown showing all sites; selection persists in component state
- "All Sites" option = tenant-wide view

**Section 2 — KPI Row (5 cards):**
- Open Alerts | Open Incidents | Cameras Online | Detections Today | Active Recordings  
  (adds recordings count vs existing 4)

**Section 3 — Camera Status Grid:**
- Compact grid of camera tiles (max 20 visible, scroll for more)
- Each tile: camera name, site, status dot (green/amber/red), last-frame timestamp
- Click → opens mini live view popover

**Section 4 — Live Events Feed (right column):**
- Real-time WebSocket events list (last 30) — alert_created, incident_created, camera_status_changed
- Severity badge + site name + module chip per event
- Already fed by existing `useRealtimeEvents` hook; just improve layout

**Section 5 — Analytics Sparklines (middle, 4 mini charts):**
- Detections 7d (per module stacked bar using CSS/inline SVG — no chart library)
- Alerts by severity (horizontal mini bar)
- Incident resolution time trend
- Top 3 cameras by alert count (rank list)

**Section 6 — System Health (bottom strip):**
- Status indicators for: API, PostgreSQL, Redis, ingestion, each AI worker (8)
- Uses `GET /api/v1/system/version` response + existing Prometheus data
- New backend endpoint: `GET /api/v1/system/health` returns per-service status

---

## Part M — Windows Desktop Application (Electron)

**New directory:** `desktop/`

### Directory structure
```
desktop/
├── electron/
│   ├── main.js          # Electron main process
│   ├── preload.js       # contextBridge API
│   └── tray.js          # System tray setup
├── package.json         # electron + electron-builder deps
├── electron-builder.yml # Windows .exe / NSIS installer config
└── README.md
```

### How it works
Electron loads the **existing React frontend** (built static files from `frontend/dist/`) inside a `BrowserWindow`. No code duplication — 100% reuse.

**First-run wizard (`main.js`):**
- On first launch, checks `electron-store` for saved `serverUrl`
- If not set, shows a frameless "Configure Server" window (custom HTML form)
- User enters `http://<server-ip>:8000` → saved to electron-store
- Subsequent launches load `<serverUrl>` in main BrowserWindow

**System tray (`tray.js`):**
- Tray icon with badge showing unread alert count (updated via IPC from renderer)
- Right-click menu: Open, Reconnect, Quit
- Click tray icon → restore window

**IPC bridge (`preload.js`):**
```javascript
contextBridge.exposeInMainWorld('electronAPI', {
  getServerUrl: () => ipcRenderer.invoke('get-server-url'),
  setServerUrl: (url) => ipcRenderer.invoke('set-server-url', url),
  showNotification: (title, body) => ipcRenderer.invoke('show-notification', { title, body }),
  setBadgeCount: (n) => ipcRenderer.invoke('set-badge-count', n),
})
```

**Native notifications:** `useRealtimeEvents` hook in React detects `window.electronAPI` and calls `showNotification()` for critical/high severity alerts instead of (or in addition to) MUI toasts.

**Auto-start (optional):** `app.setLoginItemSettings({ openAtLogin: true })` toggle in Settings page when running inside Electron.

### Build config (`electron-builder.yml`)
```yaml
appId: ai.seventh.vision.desktop
productName: 7th AI Vision
win:
  target: [nsis, portable]
  icon: assets/icon.ico
nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
  installerIcon: assets/icon.ico
```

### `package.json` scripts
```json
{
  "build:frontend": "cd ../frontend && npm run build",
  "copy:dist":      "xcopy /E /Y ..\\frontend\\dist .\\dist",
  "start":          "electron electron/main.js",
  "dist":           "npm run build:frontend && npm run copy:dist && electron-builder --win"
}
```

**Output:** `desktop/dist/7th AI Vision Setup 1.0.0.exe` (NSIS installer) + `7th AI Vision 1.0.0.exe` (portable)

---

## Part N — Permission updates (`usePermission.ts`)

Add new permission codes to the frontend matrix (mirrors new DB seed):
- `site:manage`: roles 1 (super_admin), 2 (admin), 3 (supervisor)
- `recording:create`: roles 1, 2, 3, 4 (operator)
- `recording:read`: roles 1–6 (everyone)
- `license:manage`: role 1 (super_admin) only

---

## Build Sequence

| Step | Deliverable | Critical files |
|------|-------------|----------------|
| 1 | DB migration 0006 | `backend/alembic/versions/0006_sites_licensing_recordings.py` |
| 2 | Sites backend API | `backend/app/routers/sites.py`, register in `main.py` |
| 3 | Stream validation endpoint | `backend/app/routers/cameras.py` (new endpoint + `auth_config` in create stream) |
| 4 | Ingestion + streaming credential support | `backend/app/ingestion_main.py`, `backend/app/routers/streams.py` |
| 5 | Recording backend | `backend/app/routers/streams.py` (start/stop/list), new Docker volume `recordings_data` |
| 6 | Licensing backend | `backend/app/routers/licenses.py`, camera router license check, worker license check |
| 7 | Alert/incident site enrichment | `backend/app/routers/alerts.py`, `incidents.py`, `analytics.py` |
| 8 | Frontend permission updates | `frontend/src/hooks/usePermission.ts` |
| 9 | Sites management page | `frontend/src/pages/Sites.tsx`, `frontend/src/api/sites.ts`, Sidebar |
| 10 | Stream validation UX + credential fields | `frontend/src/pages/Cameras.tsx` (StreamDialog) |
| 11 | LiveWall multi-view page | `frontend/src/pages/LiveWall.tsx`, `frontend/src/api/recordings.ts`, Sidebar |
| 12 | Alert/incident site filter | `frontend/src/pages/Alerts.tsx`, `Incidents.tsx`, `api/alerts.ts`, `api/incidents.ts` |
| 13 | Super-admin licensing UI | `frontend/src/pages/Tenants.tsx`, `frontend/src/api/licenses.ts` |
| 14 | Enhanced dashboard | `frontend/src/pages/Dashboard.tsx` (full rewrite) + `GET /api/v1/system/health` |
| 15 | Windows Electron app | `desktop/` directory, electron main/preload/tray, electron-builder config |
| 16 | Mobile app sync | `mobile/src/` — add sites filter to alerts/incidents screens, add live-wall single-view |

---

## Verification

- `docker compose up -d` → `GET /api/v1/sites` returns 200, `GET /api/v1/licenses/{tenant_id}` returns 8 module rows
- Create site → create camera under site → stream URL with credentials → "Test Connection" returns resolution + fps
- Start recording → wait 10s → stop → `GET /api/v1/.../recordings` shows completed row with file_size > 0
- Disable a module license for tenant → attempt to enable that module on a camera → 422 response
- Alerts page: filter by site → only cameras in that site's alerts appear
- LiveWall page: add 4 cameras, set 2×2 grid → 4 MJPEG streams render simultaneously
- Dashboard: site selector → KPI cards update with site-filtered counts
- Electron: `npm run dist` in `desktop/` → generates `.exe`, launch → first-run wizard prompts server URL → main window loads React app → tray icon appears

# Seventh AI Vision — Phase 1 Implementation Plan (Multi-Module AI Backend + Real-Time Platform)

## Context

The user provided the full locked scope for "Seventh AI Vision" — an enterprise AI video surveillance/security SaaS platform (face recognition, LPR, intrusion/PPE/crowd/fire/weapon/behavior detection, multi-tenant RBAC, web + mobile clients, GPU-accelerated workers, Docker→Kubernetes path). The working directory (`D:\Virtual Patrolling`) is **empty** — no code, no git repo.

This plan went through three rounds of scoping. **Round 1** locked: monorepo, backend-first Phase 1, Postgres Row-Level Security for tenant isolation (shared schema + `tenant_id`), LPR as the first AI module. **Round 2** added: real-time push to multiple simultaneously-connected clients, two more AI modules in this same Phase 1 (not deferred), a glassmorphism UI direction for the future web dashboard, and three enterprise gaps to close now (observability, rate limiting, admin-editable configuration). **Round 3** considered and explicitly rejected a MySQL-now/Postgres-later switch (it would have required rebuilding tenant isolation as app-level filtering now, then rebuilding it again as RLS during a future migration — Postgres stays), locked a speed/accuracy trade-off for the detection models, and locked the testing strategy. **Round 4** asked for a 7-10 year usable lifespan with a "patchable" update story, which surfaced gaps that are cheap to build now but very expensive to retrofit once years of production data exist: unbounded growth on the highest-volume tables with no partitioning/retention plan, no API versioning or upgrade/rollback process, hardcoded English alert text that blocks future localization, and no JWT signing-key rotation path. Decisions from all four rounds:

| Decision | Choice |
|---|---|
| Repo structure | **Monorepo** |
| Phase 1 breadth | **Backend-first** — no web/mobile UI yet (placeholders only), but now a *full real-time backend platform*, not just a CRUD API |
| Multi-tenancy | **Shared schema + `tenant_id`**, enforced via **Postgres Row-Level Security** |
| AI modules in Phase 1 | **Three, built together**: License Plate Recognition, Face Recognition, Intrusion Detection — each a complete pipeline (detect → match/check → alert → conditional auto-incident → evidence → audit), each independently scalable |
| Multi-client connectivity | **Real-time WebSocket push** (not live video relay) — alerts/incidents/camera-status pushed instantly to every connected dashboard/mobile client via Redis Pub/Sub fan-out |
| UI style | **Glassmorphism**, locked for the Phase 2 web dashboard only; Phase 1 stays API-only (verified via Swagger `/docs` and the smoke test) |
| Enterprise gaps to close now | **Observability** (Prometheus/Grafana), **API rate limiting** (brute-force/abuse protection), **admin-editable runtime configuration** (no-redeploy tuning) |
| Database | **PostgreSQL stays** — MySQL-now/Postgres-later explicitly considered and rejected (see Round 3 corrections below) |
| Detection model sizing | **Balanced accuracy upgrade**: YOLOv8n → **YOLOv8s** for LPR plate detection and intrusion person detection; insightface `buffalo_l` unchanged for face (already a strong, accurate pack) |
| Test strategy | **Real, runnable backend test cases now** (TDD-style — test written before each component's implementation); frontend test cases deferred to Phase 2 when frontend code actually exists |
| Long-term operability (Round 4) | High-volume tables (`detections` + per-module event tables + `evidence`) **partitioned by month from day one**; `audit_logs` partitioned too but archived, never silently dropped; API versioned at `/api/v1`; JWT signing keys rotatable; alerts/incidents carry a machine-readable `alert_code`+`message_params` alongside the human-readable text; semantic-versioned releases with a documented upgrade/rollback runbook |

Goal: a real, runnable, GPU-ready (CPU-fallback) backend platform proving three independent AI pipelines end-to-end against seeded test data, with operators able to receive live pushes the instant something happens — before any other AI module or client (web/mobile) is built. The `detections` schema, Redis Streams pipeline, and per-module worker pattern are generic so the next AI module (PPE, crowd, fire/smoke, weapon, behavior) is a repeat of an already-proven pattern, not a new architecture.

**Verified externally during design:**
- `onvif-zeep-async` — real, actively-maintained async ONVIF client (Python 3.10+).
- Stock YOLOv8/COCO has no "license plate" class (fine-tuned weight required for LPR) but **does** have a `person` class (no custom training needed for intrusion detection).
- `insightface`'s `buffalo_l` model pack is real: RetinaFace-10GF detection + ResNet50@WebFace600K (ArcFace) recognition, ~326MB, ~450 FPS reference throughput.

**Corrections made while synthesizing this plan** (it was designed across several sub-agent passes that didn't always agree):
1. **Postgres driver split**: `api` and `ingestion` are async (SQLAlchemy + `asyncpg`); all AI workers run synchronous consumer loops (`psycopg[binary]`). Every service's `DATABASE_URL` below matches its actual session type — earlier drafts had this inconsistent.
2. **Missing `ingestion` container**: the camera-ingestion loop needs its own container (so a decode crash can't take the API down) — added explicitly to Docker Compose.
3. **GPU reservation blocks default to commented-out** on all AI worker services — the dev host has no GPU; `resolve_device()` must fall back to CPU out of the box. Earlier drafts left these uncommented.
4. **`tenant_settings` RLS policy** corrected to match the same full pattern as every other tenant table (`FORCE ROW LEVEL SECURITY` + `WITH CHECK` + the missing-OK `current_setting(..., true)` form) — a draft had a shorthand version that was inconsistent with the rest of the schema.
5. **Evidence snapshot helper unified** to one signature (`save_evidence_snapshot(frame, tenant_id, detection_id)`) shared by all three AI modules, instead of three diverging signatures from independent design passes.
6. **Dockerfile/compose paths standardized**: all worker images build from `docker/ai-worker.Dockerfile` (context `..`), matching the existing `docker/backend.Dockerfile` convention — a draft used a different in-folder path.
7. **`ingestion` and AI workers are not HTTP apps**, so they expose Prometheus metrics via `prometheus_client.start_http_server(8001)`, not FastAPI routes — only `api` uses the Instrumentator/`/metrics` route approach.
8. **MySQL-now/Postgres-later rejected** (Round 3): kept on Postgres rather than building tenant isolation twice (app-level filtering for MySQL, then RLS again for the future migration) and rather than losing native array columns for face embeddings (MySQL has no array type).
9. **Model variant bumped one tier** (Round 3): every `yolov8n*` reference below is now `yolov8s*` (plate detector fine-tune and the stock person detector) for materially better accuracy at an acceptable CPU-inference cost; `device.py`'s CUDA/CPU abstraction is unaffected by this — it's purely a weight-file/model-size choice.

---

## Phase 1 Scope Boundaries

**In scope:** monorepo skeleton; FastAPI backend with JWT auth + RBAC; Postgres schema with RLS-enforced tenant isolation **and monthly partitioning on every high-volume table**; camera ingestion (RTSP via OpenCV, ONVIF discovery) publishing frames to Redis Streams; **three** AI worker pipelines (LPR, Face Recognition, Intrusion Detection), each with its own consumer group, model stack, and alert/incident rules; **real-time WebSocket push** of alerts/incidents/camera-status to every connected client via Redis Pub/Sub; evidence snapshot storage on local disk with checksums and a daily retention/archival job; structured (`alert_code`+`message_params`) plus human-readable alert/incident text; audit logging with long-retention archival (never silently dropped); **Prometheus + Grafana observability**; **Redis-backed API rate limiting**; **admin-editable per-tenant configuration** (AI thresholds, cooldowns, retention — no redeploy needed); versioned (`/api/v1`) REST API with a `/system/version` endpoint and rotatable JWT signing keys; Docker Compose orchestration (CPU-only by default, GPU passthrough as a one-line uncomment per worker); a documented semantic-versioned upgrade/rollback runbook; real, runnable backend test suites (pytest) written test-first for every component; a PowerShell smoke test proving all three pipelines end-to-end plus the real-time push and the operability pieces, without real camera hardware.

**Out of scope (Phase 2+):** web dashboard (glassmorphism theme locked in for then), mobile app (placeholder folders + READMEs only), remaining AI modules (PPE, crowd, fire/smoke, weapon, behavior — same proven pattern, just not built yet), live video streaming relay (WebRTC/HLS — explicitly NOT what "multiple clients" meant this round), object storage (MinIO/S3), Kubernetes, MFA/SSO/LDAP, notification channels (email/push/SMS), reporting/analytics, per-tenant billing-style quotas (distinct from the abuse-protection rate limits built now).

---

## 1. Monorepo Folder Structure

```text
seventh-ai-vision/
├── backend/
│   ├── app/
│   │   ├── main.py                       # FastAPI app factory; lifespan starts Redis Pub/Sub listener
│   │   ├── ingestion_main.py             # standalone camera-ingestion process entrypoint
│   │   ├── scheduler_main.py             # daily partition maintenance + evidence/audit retention job (§16)
│   │   ├── core/
│   │   │   ├── config.py                 # Pydantic Settings, env vars
│   │   │   ├── security.py               # bcrypt hash, JWT encode/decode, multi-key (kid-based) signing/verification for rotation (§16)
│   │   │   ├── metrics.py                # custom Prometheus counters/gauges (ws connections, frame jobs)
│   │   │   ├── limiter.py                # slowapi Limiter instance (Redis-backed)
│   │   │   ├── config_keys.py            # tenant_settings key registry + validators (shared w/ workers' intent)
│   │   │   ├── logging.py
│   │   │   └── constants.py
│   │   ├── db/{base.py, session.py, rls.py}
│   │   ├── dependencies/{auth.py, tenant.py, permissions.py}
│   │   ├── realtime/
│   │   │   ├── connection_manager.py     # per-tenant WebSocket connection tracking
│   │   │   ├── redis_listener.py         # Redis Pub/Sub -> WebSocket bridge (background task)
│   │   │   └── router.py                 # /ws/live endpoint
│   │   ├── models/                       # tenant, user, rbac, camera, detection, lpr, face, intrusion, alert, incident, evidence, audit, tenant_settings
│   │   ├── schemas/                       # mirrors models/, plus realtime.py (RealtimeEvent envelope)
│   │   ├── routers/                       # auth, users, roles, cameras, detections, lpr, face, intrusion, alerts, incidents, evidence, audit, settings, system (GET /api/v1/system/version, §16)
│   │   ├── services/                      # auth, camera, alert, incident, evidence (storage abstraction), audit
│   │   └── middleware/audit_middleware.py
│   ├── alembic/versions/0001_initial_schema.py   # full DDL + RLS for every table below
│   ├── tests/                             # conftest.py (tenant/db fixtures) + test_auth, test_rls, test_rbac, test_cameras, test_settings, test_realtime, test_rate_limit, test_evidence, test_audit (see §13)
│   ├── pyproject.toml
│   └── Dockerfile
│
├── ai-worker/                              # ONE image, run as 3 separate Compose services (WORKER_MODULE env var picks the task)
│   ├── worker/
│   │   ├── main.py                       # WORKER_MODULE dispatcher -> consumer loop for lpr|face|intrusion
│   │   ├── settings.py
│   │   ├── device.py                     # resolve_device() — sole CUDA/CPU decision point (all 3 modules route through this)
│   │   ├── db_writer.py                  # get_tenant_session() — sync RLS-correct session, shared by all 3
│   │   ├── dedup.py                      # Redis-backed BreachTracker (intrusion de-dup; reusable pattern for future modules)
│   │   ├── common/
│   │   │   ├── metrics.py                # shared Prometheus metrics (frames_consumed/processed/failed, latency, lag)
│   │   │   └── tenant_settings_cache.py  # 30s TTL cache reading backend's tenant_settings table
│   │   ├── models/
│   │   │   ├── lpr_model.py              # YOLOv8 plate detector + PaddleOCR
│   │   │   ├── face_model.py             # insightface FaceAnalysis (buffalo_l)
│   │   │   └── intrusion_model.py        # stock YOLOv8s (person class) + shapely zone check
│   │   └── tasks/
│   │       ├── lpr_task.py
│   │       ├── face_task.py
│   │       └── intrusion_task.py
│   ├── tests/                             # test_device, test_dedup, test_lpr_task, test_face_task, test_intrusion_task, test_worker_resilience (model calls mocked; see §13)
│   ├── models/yolov8s-lpr.pt             # sourced/trained plate-detection weight (see §6a note)
│   ├── pyproject.toml
│   └── Dockerfile
│
├── shared/shared/{enums.py, events.py, constants.py, schemas/realtime.py}
│
├── frontend/README.md                     # "Phase 2 — React + TypeScript + MUI, glassmorphism theme"
├── mobile/README.md                       # "Phase 2 — React Native (Expo)"
│
├── docker/
│   ├── docker-compose.yml
│   ├── backend.Dockerfile                 # shared by api + ingestion + scheduler
│   ├── ai-worker.Dockerfile               # shared by all 3 ai-worker-* services
│   ├── postgres.Dockerfile                # postgres:16 (Debian) + pg_partman installed (§16)
│   └── postgres/{init-rls.sql, init-partman.sql}
│
├── observability/
│   ├── prometheus.yml
│   └── grafana/provisioning/              # datasource + dashboard auto-provisioning
│
├── scripts/{smoke-test.ps1, push_test_frame.py}
├── docs/{architecture.md, schema.md, rbac-permissions.md, UPGRADE.md}   # UPGRADE.md: versioned release/rollback runbook, §16
├── .env.example
└── README.md
```

---

## 2. Database Schema (PostgreSQL + Row-Level Security)

Global catalogues (no `tenant_id`, no RLS): `roles`, `permissions`, `role_permissions`. Every other table is tenant-scoped and RLS-protected with the **same policy triplet** on every one of them:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_<table> ON <table>
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

(`true` = missing-OK → an unscoped session matches zero rows, fail-closed. Applied via `backend/alembic/versions/0001_initial_schema.py`, hand-authored so schema and policy can't drift apart. The app's Postgres role, `app_user`, must not be superuser/`BYPASSRLS` or `FORCE` is moot.)

### Core tables (unchanged from Round 1)

```sql
CREATE TABLE tenants (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name VARCHAR(255) NOT NULL, slug VARCHAR(100) NOT NULL UNIQUE, is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE roles (id SMALLINT PRIMARY KEY, code VARCHAR(50) NOT NULL UNIQUE, name VARCHAR(100) NOT NULL, description TEXT);  -- super_admin, admin, supervisor, operator, security_guard, viewer
CREATE TABLE permissions (id SERIAL PRIMARY KEY, code VARCHAR(100) NOT NULL UNIQUE, description TEXT, category VARCHAR(50));
CREATE TABLE role_permissions (role_id SMALLINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE, permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE, PRIMARY KEY (role_id, permission_id));
-- New Phase-1-Round-2 permission codes added to this catalogue: 'settings:read', 'settings:write' (granted to admin/super_admin only)

CREATE TABLE users (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, role_id SMALLINT NOT NULL REFERENCES roles(id), email VARCHAR(255) NOT NULL, hashed_password VARCHAR(255) NOT NULL, full_name VARCHAR(255), is_active BOOLEAN NOT NULL DEFAULT TRUE, last_login_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (tenant_id, email));
CREATE INDEX idx_users_tenant_id ON users(tenant_id);

CREATE TABLE refresh_tokens (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE, token_hash VARCHAR(255) NOT NULL, expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX idx_refresh_tokens_tenant_id ON refresh_tokens(tenant_id);

CREATE TABLE cameras (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, name VARCHAR(255) NOT NULL, location VARCHAR(255), latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, ai_modules_enabled JSONB NOT NULL DEFAULT '[]', is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX idx_cameras_tenant_id ON cameras(tenant_id);
CREATE INDEX idx_cameras_ai_modules_enabled ON cameras USING GIN (ai_modules_enabled);  -- e.g. ["lpr","face","intrusion"]

CREATE TABLE streams (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE, protocol VARCHAR(20) NOT NULL DEFAULT 'rtsp', url VARCHAR(500) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'offline', last_frame_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX idx_streams_tenant_id ON streams(tenant_id);

CREATE TABLE camera_health_events (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE, event_type VARCHAR(50) NOT NULL, detail TEXT, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX idx_camera_health_camera_id_time ON camera_health_events(camera_id, occurred_at DESC);

-- Generic parent for ALL AI modules, present and future.
-- PARTITIONED BY RANGE (monthly) on detected_at — this table is the highest-volume
-- one in the system (one row per processed detection, forever) and converting an
-- unpartitioned multi-year table to partitioned later requires a full rebuild under
-- load; doing it from day one costs nothing since there's no data yet. Declarative
-- partitioning requires detected_at in the PK.
CREATE TABLE detections (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    module_type VARCHAR(30) NOT NULL,
    confidence NUMERIC(5,4),
    bounding_box JSONB,
    raw_metadata JSONB,        -- convention: always include {"model_version": "yolov8s-lpr-2026.06"} so historical rows stay interpretable after a model upgrade
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, detected_at)
) PARTITION BY RANGE (detected_at);
CREATE INDEX idx_detections_tenant_module_time ON detections(tenant_id, module_type, detected_at DESC);
CREATE INDEX idx_detections_camera_id_time ON detections(camera_id, detected_at DESC);
-- Registered with pg_partman (see §16) for monthly partitions, 3 premade ahead, dropped per tenant evidence.retention_days-equivalent policy.

-- alerts/incidents/incident_notes stay UNPARTITIONED: volume is orders of magnitude
-- lower (one row per *matched* detection, not per processed frame) — partitioning
-- overhead isn't justified, and keeping them simple lets detection_id stay a plain
-- indexed column rather than a composite FK into a partitioned parent (see note below).
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    detection_id UUID,          -- intentionally NOT a foreign key — see note below
    camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    module_type VARCHAR(30) NOT NULL,
    severity VARCHAR(10) NOT NULL DEFAULT 'medium',
    alert_code VARCHAR(100),    -- stable machine key, e.g. 'lpr.blocklist_hit', 'face.unrecognized', 'intrusion.zone_breach' — render any future language from this + message_params instead of re-parsing English text
    message_params JSONB,       -- e.g. {"plate": "SGB1234X", "confidence": 0.91} — structured data backing alert_code
    title VARCHAR(255) NOT NULL,
    message TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    acknowledged_by_user_id UUID REFERENCES users(id),
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_alerts_tenant_status_time ON alerts(tenant_id, status, created_at DESC);
CREATE INDEX idx_alerts_detection_id ON alerts(detection_id);

CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    alert_id UUID REFERENCES alerts(id) ON DELETE SET NULL,
    camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    alert_code VARCHAR(100),    -- mirrors the originating alert's code, same i18n-readiness purpose
    message_params JSONB,
    severity VARCHAR(10) NOT NULL DEFAULT 'medium',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    is_auto_created BOOLEAN NOT NULL DEFAULT FALSE,
    assigned_to_user_id UUID REFERENCES users(id),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_incidents_tenant_status_time ON incidents(tenant_id, status, created_at DESC);

CREATE TABLE incident_notes (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, author_user_id UUID REFERENCES users(id), note TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());

-- PARTITIONED like detections (same monthly boundaries, by captured_at) — evidence
-- volume tracks detection volume 1:1-ish and is exactly the kind of table that must
-- not become an unbounded pile of old JPEGs with no purge mechanism over a decade.
CREATE TABLE evidence (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    detection_id UUID,          -- intentionally NOT a foreign key, same reasoning as alerts.detection_id
    incident_id UUID,           -- intentionally NOT a foreign key, for the same reason
    media_type VARCHAR(10) NOT NULL DEFAULT 'image',
    storage_path VARCHAR(500) NOT NULL,
    checksum_sha256 VARCHAR(64),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, captured_at)
) PARTITION BY RANGE (captured_at);
CREATE INDEX idx_evidence_detection_id ON evidence(detection_id);
CREATE INDEX idx_evidence_incident_id ON evidence(incident_id);

-- PARTITIONED by created_at, same monthly scheme — but see §16: audit partitions are
-- ARCHIVED (detached + exported), never DROPPED outright, per the original scope's
-- "immutable audit trails" / compliance requirement. Partitioning here is for query
-- performance on an append-forever table, not for disposal.
CREATE TABLE audit_logs (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id UUID,
    ip_address VARCHAR(45),
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX idx_audit_logs_tenant_time ON audit_logs(tenant_id, created_at DESC);
```

**Why `alerts.detection_id`/`evidence.detection_id`/`evidence.incident_id` dropped their `REFERENCES` constraint**: once `detections` has a composite primary key `(id, detected_at)` (required for partitioning), a real foreign key from an unpartitioned table would need to carry `detected_at` too just to satisfy Postgres's constraint rules — adding a denormalized timestamp to every referencing table for a referential-integrity guarantee that's already structurally guaranteed in practice (the AI worker writes `detections` and `alerts`/`evidence` in the same transaction; there is no code path that creates one without the other). The column stays indexed and is still how every join/lookup works — only the database-enforced constraint is gone. This is a deliberate, common trade-off for "many low-volume tables referencing one partitioned firehose table," not an oversight.

### LPR module tables

**Partitioning note (applies to `lpr_events`, `face_events`, `intrusion_events` below)**: each is 1:1 with `detections` and must be partitioned identically (same monthly boundaries, on the same denormalized timestamp) so Postgres can do efficient partition-wise joins back to `detections`, and so a `detections` partition can be dropped/archived together with its children. The FK to `detections` becomes a composite `(detection_id, detected_at) REFERENCES detections(id, detected_at)`, which requires denormalizing `detected_at` onto each child row (set once at insert time from the same value used for the parent row — both written in the same transaction, so they can never drift).

```sql
CREATE TABLE lpr_events (
    detection_id UUID NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,    -- denormalized from detections.detected_at; required for the partitioned FK
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    plate_number VARCHAR(20) NOT NULL,
    plate_confidence NUMERIC(5,4),
    direction VARCHAR(10),
    vehicle_type VARCHAR(30),
    vehicle_color VARCHAR(30),
    watchlist_match VARCHAR(10),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (detection_id, detected_at),
    FOREIGN KEY (detection_id, detected_at) REFERENCES detections(id, detected_at) ON DELETE CASCADE
) PARTITION BY RANGE (detected_at);
CREATE INDEX idx_lpr_events_plate_number ON lpr_events(tenant_id, plate_number);

CREATE TABLE watchlist_entries (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, plate_number VARCHAR(20) NOT NULL, list_type VARCHAR(10) NOT NULL CHECK (list_type IN ('allow','block')), reason TEXT, added_by_user_id UUID REFERENCES users(id), is_active BOOLEAN NOT NULL DEFAULT TRUE, expires_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (tenant_id, plate_number, list_type));
CREATE INDEX idx_watchlist_plate_lookup ON watchlist_entries(tenant_id, plate_number) WHERE is_active = TRUE;
```

### Face Recognition module tables (new this round)

```sql
CREATE TABLE face_watchlist_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    person_name TEXT NOT NULL,
    embedding FLOAT4[] NOT NULL,            -- 512-d ArcFace vector; plain array, NOT pgvector (locked as a future upgrade)
    list_type VARCHAR(10) NOT NULL CHECK (list_type IN ('allow','block')),  -- allow=VIP/known-safe, block=blacklist
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_face_watchlist_tenant_active ON face_watchlist_entries(tenant_id) WHERE is_active = TRUE;

CREATE TABLE face_events (
    detection_id UUID NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,    -- denormalized from detections.detected_at; same partitioning pattern as lpr_events
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    embedding FLOAT4[] NOT NULL,
    matched_watchlist_id UUID REFERENCES face_watchlist_entries(id),
    match_confidence NUMERIC(5,4),           -- cosine similarity; NULL if no match
    watchlist_match VARCHAR(10) CHECK (watchlist_match IN ('allow','block')),  -- NULL = unrecognized face
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (detection_id, detected_at),
    FOREIGN KEY (detection_id, detected_at) REFERENCES detections(id, detected_at) ON DELETE CASCADE
) PARTITION BY RANGE (detected_at);
CREATE INDEX idx_face_events_tenant_camera ON face_events(tenant_id, camera_id);
```

`FLOAT4[]` chosen over JSONB: embeddings are fixed-length (512) numeric vectors — a native array is ~4x more compact and casts straight to `numpy.array(row, dtype=np.float32)` with no JSON parsing.

### Intrusion Detection module tables (new this round)

```sql
CREATE TABLE restricted_zones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    polygon JSONB NOT NULL,                  -- [{"x":0.1,"y":0.2}, ...] normalized 0..1 coords
    severity VARCHAR(10) NOT NULL DEFAULT 'medium' CHECK (severity IN ('low','medium','high','critical')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_restricted_zones_camera_active ON restricted_zones(camera_id) WHERE is_active = TRUE;

CREATE TABLE intrusion_events (
    detection_id UUID NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,    -- denormalized from detections.detected_at; same partitioning pattern as lpr_events
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    zone_id UUID NOT NULL REFERENCES restricted_zones(id),
    person_bbox JSONB NOT NULL,
    dwell_time_seconds NUMERIC(8,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (detection_id, detected_at),
    FOREIGN KEY (detection_id, detected_at) REFERENCES detections(id, detected_at) ON DELETE CASCADE
) PARTITION BY RANGE (detected_at);
CREATE INDEX idx_intrusion_events_tenant_zone ON intrusion_events(tenant_id, zone_id);
```

### Admin-editable configuration table (new this round)

```sql
CREATE TABLE tenant_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value JSONB NOT NULL,
    updated_by_user_id UUID NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, setting_key)
);
```

**Phase 1 setting keys** (admin-editable via `/api/v1/settings`, env var = fallback default when no row exists):

| `setting_key` | Shape | Validation | Env fallback |
|---|---|---|---|
| `lpr.confidence_threshold` | number | `0.0–1.0` | `LPR_CONFIDENCE_THRESHOLD=0.55` |
| `face.match_threshold` | number | `0.0–1.0` | `FACE_MATCH_THRESHOLD=0.6` |
| `intrusion.breach_cooldown_seconds` | int | `≥0` | `INTRUSION_BREACH_COOLDOWN_SECONDS=60` |
| `evidence.retention_days` | int | `≥1` | `EVIDENCE_RETENTION_DAYS=90` |

**RLS for every table above** (`users`, `refresh_tokens`, `streams`, `camera_health_events`, `detections`, `alerts`, `incidents`, `incident_notes`, `evidence`, `audit_logs`, `lpr_events`, `watchlist_entries`, `face_watchlist_entries`, `face_events`, `restricted_zones`, `intrusion_events`, `tenant_settings`) uses the exact policy triplet shown at the top of §2 — including `tenant_settings`, corrected to the full pattern (a draft had a shorthand version missing `FORCE`/`WITH CHECK`/the missing-OK form).

---

## 3. Tenant Isolation Wiring (unchanged from Round 1 — the enforcement chokepoint)

```python
# backend/app/dependencies/tenant.py
async def get_db_with_tenant(token: TokenPayload = Depends(get_token_payload)) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT set_config('app.current_tenant', :tenant_id, true)"), {"tenant_id": token.tenant_id})
        try:
            yield session
        finally:
            await session.rollback()
```

Every router depends on this, never a raw session — RLS filters automatically, so a query can't accidentally skip tenant scoping. AI workers use the sync equivalent, `ai-worker/worker/db_writer.py::get_tenant_session()`, before every write.

---

## 4. Auth / RBAC

Unchanged from Round 1: `passlib[bcrypt]` hashing, `python-jose[cryptography]` JWT (15 min access / 7 day refresh, tenant_id+role_id in claims), `require_permission(code)` FastAPI dependency checking `role_permissions`. **New this round**: `settings:read`/`settings:write` permission codes, granted only to `admin`/`super_admin` roles, gating the new `/api/v1/settings` router (§8).

---

## 5. Camera Ingestion Service (unchanged from Round 1)

`opencv-python` (RTSP via FFmpeg backend) + `onvif-zeep-async` (verified real/maintained). Runs as its own async process (`backend/app/ingestion_main.py`), one `asyncio.to_thread`-wrapped task per camera. Health monitoring with exponential backoff reconnect, `camera_health_events` on status transitions. Fixed 2s snapshot cadence. Frames pushed as base64 JPEG inline in a `FrameJob` (`shared/shared/events.py`) via `XADD frame_jobs`.

---

## 6. AI Worker Architecture — Three Independent Pipelines

**Design**: one `ai-worker` Docker image, run as **three separate Compose services** (`ai-worker-lpr`, `ai-worker-face`, `ai-worker-intrusion`), each with its own Redis Streams consumer group independently reading the *same* `frame_jobs` stream, skipping any frame where its module isn't in that frame's `ai_modules_enabled`. This is the "AI worker separation / GPU worker pools" principle from the locked product scope, applied now instead of deferred — each module scales independently later without an architecture change.

```python
# ai-worker/worker/main.py
TASK_MODULES = {"lpr": "worker.tasks.lpr_task", "face": "worker.tasks.face_task", "intrusion": "worker.tasks.intrusion_task"}
CONSUMER_GROUPS = {"lpr": "lpr_workers", "face": "face_workers", "intrusion": "intrusion_workers"}

def main():
    module_name = os.environ["WORKER_MODULE"]  # lpr | face | intrusion
    task_mod = importlib.import_module(TASK_MODULES[module_name])
    run_consumer_loop(stream="frame_jobs", group=CONSUMER_GROUPS[module_name], process_fn=task_mod.PROCESS_FN)
```

```python
# ai-worker/worker/device.py — the ONLY CUDA/CPU decision point, every model load in every module routes through this
import os, torch
def resolve_device() -> str:
    override = os.environ.get("DEVICE")  # optional forced override, e.g. for testing
    if override in ("cuda", "cpu"):
        return override
    return "cuda" if torch.cuda.is_available() else "cpu"
```

Crash recovery is identical across all three: a message is `XACK`'d only after its DB transaction commits; unacked messages sit in the consumer group's PEL; each worker's loop runs `claim_stale_messages()` (`XPENDING` → `XCLAIM` for entries idle >30s) every iteration, including right after a restart — this is what `test_worker_resilience.py` (§13) and the smoke test's crash-recovery step (§15.9) exercise.

```python
# ai-worker/worker/storage.py — shared by all 3 modules (unifies 3 diverging draft signatures into 1)
def save_evidence_snapshot(frame, tenant_id: UUID, detection_id: UUID) -> tuple[str, str]:
    """Returns (relative_storage_path, sha256_checksum). Path: {tenant_id}/{yyyy}/{mm}/{dd}/{detection_id}.jpg"""
```

### 6a. LPR Pipeline

| Component | Choice | Why |
|---|---|---|
| Plate detection | Fine-tuned **YOLOv8s** (`yolov8s-lpr.pt`) | Stock COCO has no "license plate" class (confirmed by web search); the `s` tier over `n` trades modest CPU latency for materially better small-object (plate) detection accuracy — the Round 3 balanced-accuracy decision. |
| OCR | **PaddleOCR** (rec-only, `det=False`) | Handles dense plate alphanumerics better than EasyOCR; smaller footprint than Tesseract (tuned for prose, not skewed crops). |

**Weight sourcing (real prerequisite):** source a community fine-tuned weight (Roboflow Universe / Kaggle) or fine-tune `yolov8s.pt` ~20-50 epochs on an open LP dataset. Place at `ai-worker/models/yolov8s-lpr.pt` — build-sequence step, not produced by this plan.

Pipeline: decode frame → YOLOv8 plate detect (`conf=0.4`) → crop → PaddleOCR → confidence ≥ `lpr.confidence_threshold` (tenant setting, §8) → `detections` row → `lpr_events` row → tenant-scoped `watchlist_entries` lookup → `watchlist_match` → alert/incident per §7 → evidence snapshot → audit log → publish realtime event (§9) → `XACK`.

### 6b. Face Recognition Pipeline

| Component | Choice | Why |
|---|---|---|
| Detection + embedding | **`insightface`**, model pack **`buffalo_l`** (RetinaFace-10GF + ArcFace/ResNet50@WebFace600K) | One package, one model load, one dependency for both detection and embedding — confirmed real via web search. |
| Runtime | `onnxruntime-gpu` / `onnxruntime` selected via `resolve_device()` | Matches the existing CUDA/CPU abstraction. |
| Similarity | Cosine similarity via numpy (no pgvector — locked as future) | `face_watchlist_entries.embedding` is `FLOAT4[]`; brute-force compare is fine at Phase 1 watchlist scale. |

```python
# ai-worker/worker/tasks/face_task.py (core logic)
MATCH_THRESHOLD = get_tenant_setting(conn, tenant_id, "face.match_threshold")  # tenant-configurable, §8

def process_face_job(job, conn):
    if "face" not in job.ai_modules_enabled: return
    faces = face_app.get(decode_jpeg_b64(job.frame_jpeg_b64))
    if not faces: return
    set_tenant_context(conn, job.tenant_id)
    watchlist = fetch_active_face_watchlist(conn, job.tenant_id)
    for face in faces:
        matched, score = best_cosine_match(face.normed_embedding, watchlist, MATCH_THRESHOLD)
        detection_id = insert_detection(conn, module_type="face", confidence=face.det_score, bounding_box=face.bbox, ...)
        insert_face_event(conn, detection_id, embedding=face.normed_embedding, matched_watchlist_id=matched, match_confidence=score, watchlist_match=matched.list_type if matched else None)
        # alert/incident per §7 table; evidence snapshot; audit log; publish realtime event
    conn.commit()
```

**Unknown-face handling**: unlike LPR's "silent on no-match," an unrecognized face **does** get a low-severity `info` alert (no incident) — the product scope explicitly names "Unknown face detection" as a feature; an unrecognized person on a monitored site is itself the signal, unlike an unmatched plate (most plates aren't list-worthy).

### 6c. Intrusion Detection Pipeline

| Component | Choice | Why |
|---|---|---|
| Person detection | Stock **YOLOv8s** (`yolov8s.pt`, COCO `person` class, id 0) | No custom training needed — confirmed via web search that COCO already includes `person`; bumped from `n`→`s` (Round 3) for better detection accuracy at range/in clutter, still CPU-feasible since it's a stock weight needing no fine-tuning. |
| Zone check | **`shapely`** (`Point.within(Polygon)`) | Standard, GEOS-backed point-in-polygon, avoids hand-rolled ray-casting. |
| De-duplication | Redis-backed `BreachTracker`, sliding TTL cooldown | A person standing in a zone across many frames must not spam one alert per frame. |

```python
# ai-worker/worker/dedup.py
class BreachTracker:
    """SETNX on `intrusion:active_breach:{tenant}:{camera}:{zone}` with TTL = breach_cooldown_seconds
    (tenant-configurable, §8). First call -> new breach (alert fires). Calls within the TTL window
    extend it (sliding) and return is_new=False (no repeat alert) but compute running dwell_time.
    Redis-backed (not in-memory) so it stays correct if a worker module is ever scaled to N>1 replicas."""
```

Pipeline: decode frame → YOLOv8 person detect → foot-point (bbox bottom-center, normalized) → point-in-polygon against tenant's active `restricted_zones` for that camera → `BreachTracker.register()` → only on a **new** breach: `detections` + `intrusion_events` rows → alert (severity = zone's configured severity) → auto-incident **only for `high`/`critical`** zones (mirrors LPR/face: low-severity tiers alert but don't flood the incident queue) → evidence → audit log → publish realtime event → `XACK`.

---

## 7. Alert + Incident Rules (unified across all three modules)

| Module | Match condition | Alert? | Severity | Incident? |
|---|---|---|---|---|
| LPR | `watchlist_match='block'` | Yes | `critical` | **Yes**, `is_auto_created=true` |
| LPR | `watchlist_match='allow'` | Yes | `low` | No |
| LPR | unmatched | No | — | No (entry/exit logging via `lpr_events` query, not alert noise) |
| Face | `watchlist_match='block'` | Yes | `high` | **Yes** |
| Face | `watchlist_match='allow'` (VIP) | Yes | `low` | No |
| Face | unrecognized | Yes | `info` | No |
| Intrusion | breach in `low`/`medium` zone | Yes | zone's severity | No |
| Intrusion | breach in `high`/`critical` zone | Yes | zone's severity | **Yes** |

---

## 8. Admin-Editable Runtime Configuration

Directly answers "easy configuration" — AI thresholds/cooldowns/retention are no longer redeploy-only.

```python
# backend/app/routers/settings.py — Admin/Super Admin only
@router.put("/api/v1/settings/{setting_key}", dependencies=[Depends(require_permission("settings:write"))])
async def upsert_setting(setting_key: str, body: TenantSettingUpsert, db=Depends(get_db_with_tenant), user=Depends(get_current_user)):
    validator = SETTING_VALIDATORS.get(setting_key)        # backend/app/core/config_keys.py
    if validator is None: raise HTTPException(400, f"Unknown setting_key: {setting_key}")
    validator(body.setting_value)                           # e.g. 0.0<=v<=1.0 for thresholds
    return await settings_crud.upsert(db, setting_key, body.setting_value, user.id)
```

AI workers read settings through a 30-second in-process TTL cache (lazy refresh, no background poller — idle tenants cost nothing):

```python
# ai-worker/worker/common/tenant_settings_cache.py
def get_tenant_setting(db_conn, tenant_id, setting_key):
    # cache hit (< 30s old) -> return immediately
    # else: SELECT setting_value FROM tenant_settings WHERE tenant_id=... AND setting_key=...
    #       row found -> cache + return it; no row -> fall back to the env-var default, cache that too
```

30s staleness is acceptable — these are tuning knobs, not security controls. If instant propagation is ever needed, the same Redis Pub/Sub channel from §9 could carry a `setting_changed` invalidation message; deferred since unnecessary now.

---

## 9. Real-Time Push to Multiple Clients (WebSocket)

**What "multiple clients connecting" means here**: many dashboard/mobile clients stay connected and receive alerts/incidents/camera-status the instant they happen, via push — not polling, and not a live video relay (explicitly out of scope this round).

**Transport & auth**: `GET /ws/live?token=<jwt>` — query-param token, not a post-connect auth message. Browsers' native WebSocket API can't set custom `Authorization` headers at handshake time, and resolving auth *before* `accept()` means unauthorized clients are rejected at the upgrade layer without consuming a connection slot (a post-connect scheme would need a pending-auth timeout and an unauth'd-socket DoS surface). Reuses the **exact same** `decode_access_token` used by every REST dependency — no parallel auth scheme. Caveat: tokens appear in access/proxy logs; mitigated by the existing short access-token TTL (15 min).

**Fan-out**: AI workers are separate OS processes from `api` and can't hold WebSocket connections directly. They `PUBLISH` a small JSON event to Redis Pub/Sub channel `tenant_events:{tenant_id}` (same connection already used for Streams — zero new infra) immediately after committing an `alerts`/`incidents` row. Every `api` replica runs a background `redis_pubsub_listener` task (`PSUBSCRIBE tenant_events:*`) that forwards matching messages to that replica's own locally-connected clients via an in-process `ConnectionManager`.

```python
# backend/app/realtime/connection_manager.py
class ConnectionManager:
    """dict[tenant_id -> set[WebSocket]], one instance per API process (app.state).
    No cross-replica connection state — correctness across replicas comes entirely
    from every replica independently subscribing to the same Redis Pub/Sub channels,
    so a tenant's operators connected to different replicas all still get the push."""
    async def connect(self, tenant_id, ws): ...      # await ws.accept(), then track
    async def disconnect(self, tenant_id, ws): ...
    async def broadcast_to_tenant(self, tenant_id, message: str): ...  # prunes dead sockets on send failure
```

```python
# backend/app/realtime/redis_listener.py — background task started in FastAPI's lifespan
async def redis_pubsub_listener(redis_client):
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("tenant_events:*")
    async for message in pubsub.listen():
        if message["type"] != "pmessage": continue
        tenant_id = UUID(message["channel"].removeprefix("tenant_events:"))
        await manager.broadcast_to_tenant(tenant_id, message["data"])
```

```python
# shared/shared/schemas/realtime.py — the contract; workers publish a dict matching this shape
class RealtimeEvent(BaseModel):
    event_type: Literal["alert_created", "incident_created", "camera_status_changed"]
    tenant_id: UUID
    payload: dict[str, Any]
    occurred_at: datetime
```

Each AI worker's pipeline calls `redis_client.publish(f"tenant_events:{tenant_id}", event.model_dump_json())` right after its alert/incident commit (§6a/6b/6c) — the same Redis connection each worker already holds for Streams.

**Why this is correct at multi-replica scale**: Redis Pub/Sub delivers every message to every subscriber, so every `api` replica gets every event and forwards it only to the sockets it personally holds — no replica needs to know about another's connections. **Why a missed push is acceptable**: the alert row is already durable in Postgres before the publish; a momentarily-disconnected client's reconnect/initial-load REST query is the source of truth, the socket push is a convenience.

---

## 10. Observability (Prometheus + Grafana)

- **`api`**: `prometheus-fastapi-instrumentator` → `/metrics` (default HTTP metrics) + custom `ws_connections_active` (Gauge, per tenant) and `ws_events_forwarded_total` (Counter) in `backend/app/core/metrics.py`.
- **`ingestion`** and all three **`ai-worker-*`**: not HTTP apps → each runs `prometheus_client.start_http_server(8001)` once at startup. Shared metrics module (`ai-worker/worker/common/metrics.py`): `frames_consumed_total`, `frames_processed_total`, `frames_failed_total`, `processing_latency_seconds` (histogram), `detections_written_total`, `alerts_created_total` — all labeled by `module_type`; `ingestion` exposes `frame_jobs_published_total` the same way.
- **Consumer lag**: `XINFO GROUPS frame_jobs` → `lag` field directly (Redis 7+ — confirmed, the compose `redis:7-alpine` image qualifies, no need for the `XPENDING`+`XLEN` approximation).
- **Compose additions**: `prometheus` (scrapes `api:8000`, `ingestion:8001`, `ai-worker-lpr:8001`, `ai-worker-face:8001`, `ai-worker-intrusion:8001`) + `grafana` (host port `3001`, pre-provisioned datasource). `postgres_exporter`/`redis_exporter` deliberately deferred — one-line addition later, not needed for Phase 1's first dashboard.

---

## 11. API Rate Limiting & Abuse Protection

`slowapi` (Redis-backed via the same `REDIS_URL` — no new infra), wired in `backend/app/core/limiter.py`:

| Scope | Limit | Key |
|---|---|---|
| `/api/v1/auth/login`, `/auth/refresh` | `5/minute` | per IP — brute-force protection before any user/tenant is known |
| Everything else (app-wide default) | `100/minute` | per IP |

Keyed on IP for both tiers (not per-tenant/user) — IP is what an attacker controls, so it's what actually stops credential-stuffing. A per-tenant *quota* (billing-style, distinct from abuse protection) is explicitly out of scope for Phase 1.

---

## 12. Docker Compose

```yaml
# docker/docker-compose.yml
version: "3.9"
services:
  postgres:
    build: { context: .., dockerfile: docker/postgres.Dockerfile }   # postgres:16 (Debian, not Alpine) + pg_partman — see §16
    environment: { POSTGRES_DB: "${POSTGRES_DB:-seventh_ai_vision}", POSTGRES_USER: "${POSTGRES_USER:-svc_app}", POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-change_me_dev_only}" }
    volumes: ["postgres_data:/var/lib/postgresql/data", "./postgres/init-rls.sql:/docker-entrypoint-initdb.d/init-rls.sql:ro", "./postgres/init-partman.sql:/docker-entrypoint-initdb.d/init-partman.sql:ro"]
    ports: ["5432:5432"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-svc_app}"], interval: 5s, timeout: 5s, retries: 10 }

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck: { test: ["CMD", "redis-cli", "ping"], interval: 5s, timeout: 5s, retries: 10 }

  api:
    build: { context: .., dockerfile: docker/backend.Dockerfile }
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    depends_on: { postgres: { condition: service_healthy }, redis: { condition: service_healthy } }
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-svc_app}:${POSTGRES_PASSWORD:-change_me_dev_only}@postgres:5432/${POSTGRES_DB:-seventh_ai_vision}
      REDIS_URL: redis://redis:6379/0
      JWT_SECRET_KEY: ${JWT_SECRET_KEY:-change_me_dev_only}
      EVIDENCE_ROOT: /data/evidence
    volumes: ["evidence_data:/data/evidence"]
    ports: ["8000:8000"]
    healthcheck: { test: ["CMD", "curl", "-f", "http://localhost:8000/health"], interval: 10s, timeout: 5s, retries: 5, start_period: 15s }

  # Separate container so an RTSP decode crash never takes the API down. Async -> asyncpg, like `api`.
  ingestion:
    build: { context: .., dockerfile: docker/backend.Dockerfile }
    command: ["python", "-m", "app.ingestion_main"]
    depends_on: { postgres: { condition: service_healthy }, redis: { condition: service_healthy } }
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-svc_app}:${POSTGRES_PASSWORD:-change_me_dev_only}@postgres:5432/${POSTGRES_DB:-seventh_ai_vision}
      REDIS_URL: redis://redis:6379/0
    healthcheck: { test: ["CMD", "python", "-c", "import redis,os; redis.from_url(os.environ['REDIS_URL']).ping()"], interval: 15s, timeout: 5s, retries: 5, start_period: 15s }

  ai-worker-lpr: &ai_worker_base
    build: { context: .., dockerfile: docker/ai-worker.Dockerfile }
    depends_on: { postgres: { condition: service_healthy }, redis: { condition: service_healthy } }
    environment:
      WORKER_MODULE: lpr
      # Sync psycopg driver -- consumer loop is synchronous, unlike api/ingestion above.
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-svc_app}:${POSTGRES_PASSWORD:-change_me_dev_only}@postgres:5432/${POSTGRES_DB:-seventh_ai_vision}
      REDIS_URL: redis://redis:6379/0
      EVIDENCE_ROOT: /data/evidence
    volumes: ["evidence_data:/data/evidence"]
    # --- GPU passthrough: uncomment per-worker on a host with NVIDIA Container Toolkit ---
    # deploy: { resources: { reservations: { devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }] } } }
    healthcheck: { test: ["CMD", "python", "-c", "import redis,os; redis.from_url(os.environ['REDIS_URL']).ping()"], interval: 15s, timeout: 5s, retries: 5, start_period: 20s }

  ai-worker-face:
    <<: *ai_worker_base
    environment: { WORKER_MODULE: face, DATABASE_URL: "postgresql+psycopg://${POSTGRES_USER:-svc_app}:${POSTGRES_PASSWORD:-change_me_dev_only}@postgres:5432/${POSTGRES_DB:-seventh_ai_vision}", REDIS_URL: "redis://redis:6379/0", EVIDENCE_ROOT: /data/evidence }

  ai-worker-intrusion:
    <<: *ai_worker_base
    environment: { WORKER_MODULE: intrusion, DATABASE_URL: "postgresql+psycopg://${POSTGRES_USER:-svc_app}:${POSTGRES_PASSWORD:-change_me_dev_only}@postgres:5432/${POSTGRES_DB:-seventh_ai_vision}", REDIS_URL: "redis://redis:6379/0", EVIDENCE_ROOT: /data/evidence }

  # Daily maintenance: pre-create future partitions, purge/archive old ones per
  # retention policy, delete the evidence files a dropped partition orphans. See §16.
  scheduler:
    build: { context: .., dockerfile: docker/backend.Dockerfile }
    command: ["python", "-m", "app.scheduler_main"]
    depends_on: { postgres: { condition: service_healthy } }
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-svc_app}:${POSTGRES_PASSWORD:-change_me_dev_only}@postgres:5432/${POSTGRES_DB:-seventh_ai_vision}
      EVIDENCE_ROOT: /data/evidence
    volumes: ["evidence_data:/data/evidence"]
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:v2.55.1
    volumes: ["../observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro", "prometheus_data:/prometheus"]
    ports: ["9090:9090"]

  grafana:
    image: grafana/grafana:11.4.0
    environment: { GF_SECURITY_ADMIN_PASSWORD: "${GRAFANA_ADMIN_PASSWORD:-admin}" }
    volumes: ["grafana_data:/var/lib/grafana", "../observability/grafana/provisioning:/etc/grafana/provisioning:ro"]
    ports: ["3001:3000"]
    depends_on: [prometheus]

volumes: { postgres_data: {}, evidence_data: {}, prometheus_data: {}, grafana_data: {} }
```

All three `ai-worker-*` services and `ingestion` use the corrected `postgresql+asyncpg`/`postgresql+psycopg` split per service (see corrections list). GPU blocks are commented out on every worker by default — the dev host has no GPU; `resolve_device()` falls back to CPU automatically.

`docker/ai-worker.Dockerfile`: Python 3.11-slim + `libgl1`/`ffmpeg` (OpenCV) + `ultralytics`, `paddleocr`, `paddlepaddle`, `insightface`, `onnxruntime`, `shapely`, `torch` (CPU wheel by default). `docker/backend.Dockerfile` (shared by `api`+`ingestion`): same base + `libgl1`/`ffmpeg` for the RTSP capture loop, FastAPI/SQLAlchemy/asyncpg stack.

`observability/prometheus.yml`:
```yaml
global: { scrape_interval: 15s }
scrape_configs:
  - job_name: api
    static_configs: [{ targets: ["api:8000"] }]
  - job_name: ingestion
    static_configs: [{ targets: ["ingestion:8001"] }]
  - job_name: ai-worker-lpr
    static_configs: [{ targets: ["ai-worker-lpr:8001"] }]
  - job_name: ai-worker-face
    static_configs: [{ targets: ["ai-worker-face:8001"] }]
  - job_name: ai-worker-intrusion
    static_configs: [{ targets: ["ai-worker-intrusion:8001"] }]
```

---

## 13. Test Strategy & Test Cases (Backend, Test-First)

**Principle**: per the user's explicit build-order request, every component below gets its test written first (red), then the implementation follows until it passes (green). Frontend test cases are deferred to Phase 2 — there's no frontend code yet to meaningfully test against.

**Tooling**: `pytest` + `pytest-asyncio` (the backend is async); FastAPI's `TestClient`/`httpx.AsyncClient`, including its `websocket_connect` context manager, for API+WebSocket tests; a real `seventh_ai_vision_test` Postgres database (RLS is enforced by Postgres itself, so it cannot be meaningfully tested against a mock), migrated once per test session via Alembic; a real test Redis (separate DB index, flushed between tests) since Streams/Pub-Sub semantics aren't meaningfully fakeable. AI model inference (YOLOv8/PaddleOCR/insightface) is mocked at the pipeline boundary in worker tests — these assert *your* pipeline logic (DB writes, matching, alert/incident rules, dedup), not the third-party model's accuracy.

**Isolation pattern**: each test runs inside its own DB transaction (begin → `SET LOCAL app.current_tenant` → test queries → rollback at teardown) — RLS's transaction-scoped GUC gives perfect per-test isolation with zero manual cleanup. Cross-tenant tests open a second tenant context within the same test (re-issuing `SET LOCAL`) to assert tenant A can never see tenant B's rows.

### Backend API tests (`backend/tests/`)

| File | Test case | Pass condition |
|---|---|---|
| `test_auth.py` | `test_login_valid_credentials` | 200, returns access+refresh token pair |
| | `test_login_wrong_password` / `test_login_nonexistent_email` | both 401 with an identical response shape (no user enumeration) |
| | `test_access_token_claims` | decoded token contains `sub`, `tenant_id`, `role_id`, correct `exp` |
| | `test_expired_token_rejected` | 401 on a token with `exp` in the past |
| | `test_refresh_rotates_token` | old refresh token hash marked revoked after use; reuse fails |
| `test_rbac.py` | `test_permission_denied_returns_403` | a `viewer`-role user hitting `camera:create` gets 403 |
| | `test_permission_granted_succeeds` | an `admin`-role user with `camera:create` succeeds |
| | `test_role_seed_matches_six_roles` | `roles` contains exactly super_admin/admin/supervisor/operator/security_guard/viewer |
| `test_rls.py` | `test_cross_tenant_camera_read_blocked` | tenant A's session lists 0 of tenant B's cameras, even with a known ID |
| | `test_cross_tenant_alert_incident_evidence_blocked` | same, for `alerts`/`incidents`/`evidence` |
| | `test_unscoped_session_sees_zero_rows` | a session with `app.current_tenant` unset returns 0 rows from any tenant table (fail-closed) |
| | `test_insert_mismatched_tenant_rejected` | inserting a row whose `tenant_id` differs from the session GUC raises (`WITH CHECK` fires) |
| | `test_app_db_role_is_not_superuser` | `app_user` has neither `BYPASSRLS` nor superuser — confirms `FORCE ROW LEVEL SECURITY` is actually load-bearing |
| `test_cameras.py` | `test_create_camera_requires_permission` | 403 without `camera:create` |
| | `test_stream_health_online_to_degraded` | 3 consecutive simulated failures flips `streams.status` to `degraded`, writes exactly one `camera_health_events` row |
| | `test_stream_health_degraded_to_offline` | 10 consecutive failures flips to `offline` |
| | `test_reconnect_backoff_caps_at_60s` | backoff sequence 1,2,4,8,16,32,60,60,... never exceeds 60 |
| `test_settings.py` | `test_non_admin_cannot_write_settings` | 403 for `operator` role |
| | `test_invalid_threshold_rejected` | `PUT lpr.confidence_threshold` with `1.5` → 422 |
| | `test_unknown_key_rejected` | `PUT` an unregistered `setting_key` → 400 |
| | `test_missing_row_falls_back_to_env_default` | no `tenant_settings` row → effective value equals the env default |
| `test_realtime.py` | `test_ws_rejects_invalid_token` | connection closed with a policy-violation code before `accept()` |
| | `test_ws_receives_published_event` | a `redis_client.publish("tenant_events:{id}", ...)` results in the connected test client receiving that exact message |
| | `test_ws_tenant_isolation` | tenant A's socket never receives a message published on tenant B's channel |
| | `test_disconnect_pruned_from_manager` | after disconnect, `ConnectionManager.connection_count(tenant_id)` returns to 0 |
| `test_rate_limit.py` | `test_sixth_login_in_one_minute_429` | the 6th rapid login attempt from one IP returns 429 |
| | `test_limit_resets_after_window` | after the window elapses, the next attempt succeeds again |
| | `test_general_endpoint_default_limit` | a non-auth endpoint allows up to 100/minute per IP before 429 |
| `test_evidence.py` | `test_snapshot_checksum_matches_file` | `evidence.checksum_sha256` equals an independently computed SHA-256 of the file at `storage_path` |
| | `test_storage_path_convention` | path matches `{tenant_id}/{yyyy}/{mm}/{dd}/{detection_id}.jpg` |
| `test_audit.py` | `test_audit_row_per_detection_processed` | every committed `detections` row has a matching `audit_logs` row |
| | `test_system_actor_has_null_user_id` | AI-worker-originated audit rows have `user_id IS NULL` |

### AI worker pipeline tests (`ai-worker/tests/`, model calls mocked)

| File | Test case | Pass condition |
|---|---|---|
| `test_device.py` | `test_cuda_used_when_available` / `test_cpu_fallback` / `test_env_override_wins` | `resolve_device()` matches a mocked `torch.cuda.is_available()`; explicit `DEVICE` env var wins over both |
| `test_lpr_task.py` | `test_blocklist_plate_critical_alert_and_incident` | mocked detector+OCR returns a blocklisted plate → `critical` alert, `is_auto_created=true` incident |
| | `test_allowlist_plate_low_alert_no_incident` | allowlisted plate → `low` alert, zero incidents |
| | `test_unmatched_plate_no_alert` | plate on neither list → only `lpr_events`, zero alerts/incidents |
| | `test_below_threshold_writes_nothing` | mocked confidence below `lpr.confidence_threshold` → zero `detections` rows |
| | `test_tenant_setting_overrides_env_default` | a `tenant_settings` row changes the effective threshold used |
| `test_face_task.py` | `test_blocklist_face_high_alert_and_incident` | mocked embedding matches a blocklisted entry above threshold → `high` alert + incident |
| | `test_allowlist_vip_low_alert_no_incident` | matches an allowlisted entry → `low` alert, no incident |
| | `test_unrecognized_face_info_alert_no_incident` | no match above threshold → `info` alert, still no incident (deliberate LPR-vs-face asymmetry) |
| | `test_cosine_similarity_pure_function` | known vector pairs produce expected similarity scores — no DB/model needed |
| `test_intrusion_task.py` | `test_breach_high_zone_alert_and_incident` | foot-point inside a `high`-severity zone → alert + incident |
| | `test_breach_low_zone_alert_no_incident` | `low`/`medium` zone → alert only |
| | `test_person_outside_any_zone_no_detection` | foot-point outside every zone → zero `detections` rows |
| | `test_point_in_polygon_pure_function` | `shapely` zone check against known in/out fixture points — no DB/model needed |
| `test_dedup.py` | `test_first_breach_is_new` | first `BreachTracker.register()` call for a camera+zone returns `is_new=True` |
| | `test_repeat_within_cooldown_suppressed` | a second call inside the TTL window returns `is_new=False` |
| | `test_cooldown_expiry_allows_new_breach` | after the TTL elapses, a new call returns `is_new=True` again |
| | `test_sliding_window_extends_on_continued_presence` | repeated in-window calls keep pushing the Redis key's TTL forward |
| `test_worker_resilience.py` | `test_message_unacked_on_processing_exception` | a forced exception during processing leaves the message in the consumer group's PEL |
| | `test_stale_message_reclaimed` | a message idle past `IDLE_CLAIM_MS` is claimed by `claim_stale_messages()` under a new consumer name |
| | `test_each_module_group_independent_position` | `lpr_workers`/`face_workers`/`intrusion_workers` each track their own position on the same stream without interfering |

This is intentionally exhaustive for Phase 1's actual surface area: every row of the §7 alert/incident decision table has a corresponding test, every RLS guarantee has a corresponding test, and every concurrency-sensitive mechanism (dedup, crash recovery) has a corresponding test. New AI modules in later phases repeat this same table shape.

---

## 14. Build Sequence (Test-First)

For each step: **write the listed test file(s) first — they fail, since nothing exists yet — then implement until they pass.**

1. `docker/postgres.Dockerfile` + `docker/postgres/init-rls.sql` + `init-partman.sql` + `docker-compose.yml` — bring up `postgres`(with pg_partman)+`redis`+a `seventh_ai_vision_test` database, confirm healthchecks. (Infra only, no tests yet.)
2. Tests: `test_rls.py`, written against the schema about to exist. Implementation: `backend/app/models/` + `0001_initial_schema.py` — full DDL + RLS + partitioning for every table in §2, register each partitioned table with pg_partman per §16. Migrate dev and test databases. `test_rls.py` goes green.
3. Tests: `test_auth.py`, `test_rbac.py`. Implementation: `backend/app/core/`, `dependencies/`, JWT auth, `get_db_with_tenant`, `require_permission`. Seed `roles`/`permissions`/`role_permissions` (including `settings:read`/`write`) via `init-rls.sql`.
4. `shared/shared/` — `enums.py`, `events.py` (`FrameJob`), `schemas/realtime.py` (`RealtimeEvent`). (Shared types, exercised indirectly by later tests.)
5. Tests: `test_cameras.py`. Implementation: `backend/app/services/camera_service.py` + `ingestion_main.py` — capture loop, health monitoring, `XADD` only (no AI yet). Manual `redis-cli XADD`/`XLEN` check alongside the automated tests.
6. Tests: `test_device.py`, `test_dedup.py`. Implementation: `ai-worker/worker/device.py`, `db_writer.py`, `main.py` (`WORKER_MODULE` dispatch), `common/metrics.py`, `common/tenant_settings_cache.py`, `storage.py`, `dedup.py` — shared worker infra used by all 3 modules.
7. **LPR**: tests `test_lpr_task.py` first (model calls mocked) → source/place `yolov8s-lpr.pt` → `models/lpr_model.py` → `tasks/lpr_task.py` until green.
8. **Intrusion**: tests `test_intrusion_task.py` first → `models/intrusion_model.py` (stock `yolov8s.pt`, no sourcing needed) → `tasks/intrusion_task.py` until green.
9. **Face**: tests `test_face_task.py` first → `models/face_model.py` (insightface `buffalo_l`) → `tasks/face_task.py` until green.
10. Tests: `test_worker_resilience.py`. Implementation: confirm `claim_stale_messages()`/`XCLAIM`/`XPENDING` behavior generically across all 3 consumer groups.
11. Tests: `test_realtime.py`. Implementation: `backend/app/realtime/` (`ConnectionManager`, `redis_listener.py`, `/ws/live`); add `redis_client.publish(...)` to the end of each of the 3 pipelines from steps 7-9.
12. Tests: `test_settings.py`. Implementation: `backend/app/routers/settings.py` + `core/config_keys.py`; confirm workers pick up a changed threshold within the 30s cache TTL.
13. `backend/app/core/metrics.py` + Instrumentator wiring in `api`; `ai-worker/worker/common/metrics.py` + `start_http_server(8001)` in all 3 workers + `ingestion`. (Observed via Prometheus, not pytest.)
14. Tests: `test_rate_limit.py`. Implementation: `backend/app/core/limiter.py` — `slowapi`, `5/minute` on login, `100/minute` app-wide default.
15. Tests: `test_evidence.py`, `test_audit.py`. Implementation: `backend/app/routers/` read endpoints for `alerts`/`incidents`/`lpr_events`/`face_events`/`intrusion_events`; CRUD for `watchlist_entries`/`face_watchlist_entries`/`restricted_zones`.
16. `docker/backend.Dockerfile` + `docker/ai-worker.Dockerfile` — containerize; wire full `docker-compose.yml` (§12) including `prometheus`+`grafana`+`scheduler`.
17. `observability/prometheus.yml` + Grafana provisioning — confirm all 5 scrape targets show `UP`.
18. `backend/app/scheduler_main.py` + `routers/system.py` (`GET /api/v1/system/version`) + multi-key JWT verification in `core/security.py` (§16) — the long-term-operability pieces. Confirm `scheduler` runs `partman.run_maintenance_proc()` without error against the empty/near-empty Phase 1 dataset.
19. Full backend test suite green (`pytest backend/ ai-worker/`), then `scripts/smoke-test.ps1` (§15) — the end-to-end gate against the real Docker Compose stack, distinct from the mocked unit/integration suite above.
20. Crash-recovery drill — kill any one `ai-worker-*` mid-job in the live stack, confirm `XPENDING`→`XCLAIM` self-heals (already covered by `test_worker_resilience.py`, re-run here against real containers, not just the test harness).

---

## 15. Verification — End-to-End Smoke Test

`scripts/smoke-test.ps1`, run after `docker compose up -d` and all healthchecks pass:

1. **Seed**: a test tenant; a blocklist + allowlist plate (`watchlist_entries`); a blocklist + allowlist face embedding fixture (`face_watchlist_entries`); one `restricted_zones` polygon on a fixture camera.
2. **Drive frames**: `scripts/push_test_frame.py` XADDs fixture frames (plate fixtures, face fixtures, a person-in-zone fixture) directly to `frame_jobs` — no camera hardware needed.
3. **LPR assertions**: blocklist plate → `alerts.severity='critical'` + `incidents.is_auto_created=true` + evidence + audit row. Allowlist plate → alert (`low`) but **no** incident. Unmatched plate → only `lpr_events` row, zero alerts/incidents.
4. **Face assertions**: blocklist face → `alerts.severity='high'` + incident. Allowlist (VIP) face → alert (`low`), no incident. Unrecognized face → alert (`info`), no incident — confirms the deliberate LPR-vs-face asymmetry on unmatched results.
5. **Intrusion assertions**: person-in-zone fixture on a `high`/`critical` zone → alert + incident. Same fixture replayed within 60s (the cooldown) → confirm **no second alert** (de-dup via `BreachTracker`) but a second `intrusion_events` row is NOT written either (per the design, suppressed before any insert) — assert exactly one full row-set for the episode.
6. **Realtime push assertion**: open a test WebSocket client against `/ws/live?token=<seeded-tenant-jwt>` before step 2 runs; assert it receives an `alert_created` message matching the blocklist-plate alert's `alert_id` within a few seconds — proves the Redis Pub/Sub → `ConnectionManager` bridge actually delivers, not just that rows land in Postgres.
7. **Admin config assertion**: `PUT /api/v1/settings/lpr.confidence_threshold` to an extreme value (e.g. `0.99`) → redrive a previously-passing plate fixture → assert it now falls below threshold and produces **no** `detections` row → proves the worker actually reads `tenant_settings`, not just the env var.
8. **Rate limit assertion**: hammer `/api/v1/auth/login` 6 times in a minute with bad credentials → assert the 6th returns `429`.
9. **Crash-recovery** (per existing Round-1 design, now generalized): kill `ai-worker-lpr` mid-job before ACK → `XPENDING frame_jobs lpr_workers` shows ≥1 pending → restart → confirm the worker's own `claim_stale_messages()` (not the script) reclaims it within ~30s → `lpr_events` count increments, proving reprocessing, not silent data loss.
10. **Version endpoint assertion**: `GET /api/v1/system/version` → 200 with non-empty `version`/`git_sha` fields — proves the release-versioning mechanism (§16.6) is actually wired, not just documented.
11. **Partition maintenance assertion**: query `partman.show_partitions('public.detections')` (or equivalent) → assert at least the current month plus 3 premade future months exist — proves pg_partman is actually registered and running (§16.1), not just present in the init SQL.

Script exits non-zero on any failed assertion — a repeatable CI-style gate.

---

## 16. Long-Term Operability (7-10 Year Design)

Addresses the explicit "usable for 7-10 years, patchable" requirement. Each item is either baked into the DDL/code above already (cross-referenced) or is a small, self-contained addition — none of this is speculative over-engineering; each is a specific, named gap that is cheap to close now and expensive (sometimes a full-table-rebuild-under-load expensive) to close after years of production data exist.

**1. Partitioning + retention/archival (mechanism for the DDL in §2).** `docker/postgres.Dockerfile` builds on `postgres:16` (Debian-based, not `-alpine` — `pg_partman` has readily available Debian/PGDG packages; Alpine's musl libc makes extension builds more friction than they're worth here):
```dockerfile
FROM postgres:16
RUN apt-get update && apt-get install -y postgresql-16-partman && rm -rf /var/lib/apt/lists/*
```
`docker/postgres/init-partman.sql` (runs once at container init, alongside the existing `init-rls.sql`):
```sql
CREATE EXTENSION IF NOT EXISTS pg_partman;
SELECT partman.create_parent(p_parent_table => 'public.detections', p_control => 'detected_at', p_interval => 'monthly', p_premake => 3);
SELECT partman.create_parent(p_parent_table => 'public.lpr_events', p_control => 'detected_at', p_interval => 'monthly', p_premake => 3);
SELECT partman.create_parent(p_parent_table => 'public.face_events', p_control => 'detected_at', p_interval => 'monthly', p_premake => 3);
SELECT partman.create_parent(p_parent_table => 'public.intrusion_events', p_control => 'detected_at', p_interval => 'monthly', p_premake => 3);
SELECT partman.create_parent(p_parent_table => 'public.evidence', p_control => 'captured_at', p_interval => 'monthly', p_premake => 3);
SELECT partman.create_parent(p_parent_table => 'public.audit_logs', p_control => 'created_at', p_interval => 'monthly', p_premake => 3);
```
The new `scheduler` service (§12) runs `backend/app/scheduler_main.py` once daily:
- For `detections`/`lpr_events`/`face_events`/`intrusion_events`/`evidence`: query each active tenant's `evidence.retention_days` setting (§8, default 90), then call `partman.run_maintenance_proc()` and let pg_partman **drop** partitions older than the longest active tenant retention. Before the drop, a pass over `evidence` rows in the expiring partition deletes the corresponding files under `/data/evidence/...` first (DB row and on-disk file are purged together — a DB-only drop would leak disk space, a file-only delete would leave dangling rows).
- For `audit_logs`: **never drop** — pg_partman is configured to **detach** (not drop) partitions past a separately configured, much longer compliance retention (e.g. 7 years, itself a `tenant_settings` key: `audit.retention_years`), then `COPY` the detached partition to a compressed export file under an `archive/` volume before dropping the detached copy. This matches the original scope's "immutable audit trails" / "exportable logs" compliance requirement — audit history is archived, never silently destroyed.

**2. Model version tracking.** Already in §2's `detections.raw_metadata` convention (`{"model_version": "yolov8s-lpr-2026.06"}`, set by every AI worker pipeline on every write). Costs nothing extra (the column already exists as a JSONB catch-all) but means a model upgrade in year 4 doesn't make years 1-3's detections uninterpretable, and enables per-tenant canary rollout of a new model version via the existing `tenant_settings` mechanism (e.g. `lpr.model_version` overriding the default for one tenant before a fleet-wide upgrade).

**3. Structured, localizable alerts.** Already in §2 (`alerts.alert_code`/`message_params`, mirrored on `incidents`). The AI worker pipelines (§6a/6b/6c) set both fields alongside the existing pre-rendered English `title`/`message` (which stays as the Phase 1 display string). Phase 2's web/mobile clients can render `alert_code`+`message_params` through a translation table without ever touching historical English strings — retrofitting this onto years of `message TEXT` rows later would mean parsing free-text English to recover structured meaning, which is unreliable; capturing it structurally from day one is two extra nullable columns.

**4. JWT signing-key rotation.** `backend/app/core/security.py` looks up the verification key by the token's `kid` header against a small map of currently-valid keys, rather than a single hardcoded secret:
```python
JWT_SIGNING_KEYS = {"2026-06": settings.JWT_SECRET_KEY_CURRENT, "2026-01": settings.JWT_SECRET_KEY_PREVIOUS}  # previous stays valid only during a rotation window
JWT_ACTIVE_KID = "2026-06"  # new tokens are always signed with this one

def decode_access_token(token: str):
    kid = jwt.get_unverified_header(token)["kid"]
    key = JWT_SIGNING_KEYS.get(kid)
    if key is None:
        raise InvalidTokenError
    return jwt.decode(token, key, algorithms=[settings.JWT_ALGORITHM])
```
Rotating a compromised or aging key is then: add the new key+`kid`, flip `JWT_ACTIVE_KID`, keep the old one valid until its longest-lived outstanding token (≤15 min access, ≤7 day refresh) expires, then remove it. Without this, rotating the single static secret today would invalidate every session instantly — an operational non-starter, and the kind of thing that's a one-function design decision now versus a forced incident-response scramble later.

**5. API versioning policy.** Every route is already under `/api/v1/...` (§4, §8, §11 examples) — this is now a **locked policy**, not an accidental prefix: breaking changes ship as `/api/v2/...` alongside the still-running `/api/v1/...` (not a replacement), v1 is supported for a documented deprecation window after v2 ships, and the WebSocket path/Redis event envelope (§9) carry the same versioning discipline if their message shape ever needs a breaking change (`RealtimeEvent` gains a `schema_version` field for this — cheap now, would require client-side guessing later). `GET /api/v1/system/version` returns `{"version": "1.0.0", "git_sha": "...", "build_date": "..."}` — lets support staff and a future admin UI confirm what's actually deployed at a site.

**6. Release/upgrade/rollback runbook.** The whole platform is released under one semantic version (`MAJOR.MINOR.PATCH`) even though it's multiple images — simpler for an on-prem Windows operator to reason about "we're running 1.4.2" than tracking `api`/`ai-worker`/etc. independently. Images are tagged per release (`seventh-ai-vision/api:1.4.2`, never `:latest` in a real deployment). `docs/UPGRADE.md` documents the procedure: back up the Postgres volume → pull the new image tags → `alembic upgrade head` → `docker compose up -d` → run `scripts/smoke-test.ps1` (§15) as the go/no-go gate. Rollback is the same procedure with the previous tags plus a restore from the pre-upgrade backup if a migration's `downgrade()` isn't sufficient on its own — every Alembic revision from `0001_initial_schema.py` onward must implement a working `downgrade()`, not just `upgrade()`, specifically so rollback is real and not aspirational.

**7. Dependency hygiene.** Both `pyproject.toml` files (backend, ai-worker) use locked/pinned versions (`poetry.lock` or `uv.lock`, not loose ranges) for reproducible builds across a decade of rebuilds. Periodic vulnerability scanning (e.g. `pip-audit`, or the Endor Labs scan already available as a skill in this environment) is an operational practice to schedule, not a Phase 1 code deliverable — noted here so it isn't silently forgotten.

**8. Deliberately deferred, with reasons (not gaps, judgment calls):**
- *Formal AI-module plugin registry* (each module self-registering its migration/consumer-group/permissions instead of following the §6 pattern by hand) — premature with only 3 worked examples; revisit once a 4th-5th module's real variance is known, not before.
- *Secrets manager (Vault/Key Vault) integration* — `.env`-based config is appropriate for Windows-first Docker Compose now; swapping the config-loading layer for a vault client later is a contained change (`core/config.py` is already the single place secrets are read from), not a structural one.
- *Full i18n/translation infrastructure* — item 3 above lays the structural groundwork (`alert_code`+`message_params`); the actual translation tables/UI are Phase 2 frontend work, not meaningful to build before any frontend exists.

---

## 17. Phase 2 — Web Dashboard

**Status: PLANNING** (Phase 1 complete — 74/74 tests passing, smoke test 14/14)

Phase 2 delivers the glassmorphism React + TypeScript + MUI web dashboard that consumes the Phase 1 backend exactly as built. No backend architecture changes; only two additive backend endpoints needed before the frontend can be feature-complete.

---

### 17.1 Backend Additions (before frontend)

**Two new routes only — no schema changes.**

#### User management router (`backend/app/routers/users.py`)

The backend has a `users` table and RBAC enforcement but no HTTP CRUD for users yet. Add:

| Method | Path | Permission | Notes |
|--------|------|------------|-------|
| `GET` | `/api/v1/users` | `user:read` | List all tenant users |
| `POST` | `/api/v1/users` | `user:create` | Create user with email+password+role_id+full_name |
| `GET` | `/api/v1/users/{user_id}` | `user:read` | Single user |
| `PUT` | `/api/v1/users/{user_id}` | `user:update` | Update full_name / role_id / is_active |
| `DELETE` | `/api/v1/users/{user_id}` | `user:delete` | Soft-deactivate (set `is_active=FALSE`) |

Password is set directly on creation (no email invite in Phase 2). Hash via `backend/app/core/security.py::hash_password()` — already exists. Response schema excludes `hashed_password`.

#### Evidence file serving (`backend/app/routers/evidence.py` — add one route)

```python
@router.get("/{evidence_id}/file")
async def download_evidence_file(evidence_id: UUID, ...):
    # Fetch evidence row (RLS-isolated), resolve EVIDENCE_ROOT / storage_path, stream with FileResponse
```

Returns `FileResponse` for `image/jpeg`. No new dependencies — FastAPI's `FileResponse` is already available.

**Tests to write first** (backend-test-first pattern from Phase 1):
- `test_user_crud_round_trip` — create, read, update, deactivate
- `test_non_admin_cannot_create_users` — 403 for operator role
- `test_evidence_file_download` — 200 with correct bytes for known evidence row

---

### 17.2 Frontend Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Build | **Vite 5 + React 18 + TypeScript** | Fast HMR, zero backend coupling, static build served by nginx |
| UI | **MUI v5** | Comprehensive component library; theme override API for glassmorphism |
| Routing | **React Router v6** | File-not-module, declarative nested routes |
| Data fetching | **TanStack Query v5** | Per-resource cache + automatic background refetch; WS events → `queryClient.invalidateQueries()` |
| State | **Zustand** | Auth store + notification queue; lightweight, no Provider boilerplate |
| HTTP | **axios** | Interceptor for token refresh on 401; typed response transforms |
| Icons | **MUI Icons** | Already in the MUI package, no extra dep |

---

### 17.3 Glassmorphism Theme (`frontend/src/theme/glassmorphism.ts`)

```typescript
// Key overrides applied to MUI's createTheme()
palette: {
  mode: 'dark',
  background: { default: '#080818', paper: 'rgba(255,255,255,0.05)' },
  primary:   { main: '#6C63FF' },   // violet
  secondary: { main: '#00D9C0' },   // teal
  error:     { main: '#FF4560' },
  warning:   { main: '#FF9800' },
  success:   { main: '#00E396' },
}
components: {
  MuiPaper: {
    styleOverrides: {
      root: { backdropFilter: 'blur(16px)', border: '1px solid rgba(255,255,255,0.1)', backgroundImage: 'none' }
    }
  },
  MuiCard: { same as Paper },
  MuiAppBar: { styleOverrides: { root: { backdropFilter: 'blur(20px)', background: 'rgba(8,8,24,0.8)' } } },
  MuiDrawer: { styleOverrides: { paper: { backdropFilter: 'blur(20px)', background: 'rgba(8,8,24,0.85)' } } },
}
```

Body background: `linear-gradient(135deg, #080818 0%, #1A0828 50%, #081828 100%)` fixed via `CssBaseline` global override.

---

### 17.4 Folder Structure

```
frontend/
├── index.html
├── vite.config.ts            # path alias @/ → src/
├── tsconfig.json
├── package.json
├── src/
│   ├── main.tsx              # createRoot, QueryClientProvider, ThemeProvider
│   ├── App.tsx               # <RouterProvider> with route tree
│   ├── theme/
│   │   └── glassmorphism.ts
│   ├── api/                  # One file per resource — typed axios calls
│   │   ├── client.ts         # axios instance, base URL from VITE_API_BASE_URL
│   │   ├── auth.ts           # login(), refresh()
│   │   ├── users.ts
│   │   ├── cameras.ts
│   │   ├── alerts.ts
│   │   ├── incidents.ts
│   │   ├── detections.ts     # listDetections(), listLprEvents(), listFaceEvents(), listIntrusionEvents()
│   │   ├── evidence.ts       # listEvidence(), evidenceFileUrl(id)
│   │   ├── watchlist.ts
│   │   ├── zones.ts
│   │   ├── settings.ts
│   │   └── audit.ts
│   ├── store/
│   │   ├── auth.ts           # Zustand: { accessToken, user, login, logout, refresh }
│   │   └── notifications.ts  # Zustand: { toasts, unreadCount, push, dismiss }
│   ├── hooks/
│   │   ├── usePermission.ts  # usePermission(code) → boolean from role_id
│   │   ├── useWebSocket.ts   # auto-reconnect WS; calls onMessage callback
│   │   └── useRealtimeEvents.ts  # parses RealtimeEvent, invalidates queries, pushes toasts
│   ├── components/
│   │   ├── layout/
│   │   │   ├── AppShell.tsx  # Sidebar + TopBar + <Outlet/>
│   │   │   ├── Sidebar.tsx   # MUI Drawer, nav items filtered by permission
│   │   │   └── TopBar.tsx    # AppBar: title, notification bell badge, avatar menu
│   │   ├── common/
│   │   │   ├── GlassCard.tsx         # MUI Card with blur/border preset
│   │   │   ├── SeverityChip.tsx      # Color-coded: info/low/medium/high/critical
│   │   │   ├── StatusChip.tsx        # open/acknowledged/resolved/closed
│   │   │   ├── PermissionGuard.tsx   # renders children only if usePermission(code)
│   │   │   ├── PageHeader.tsx
│   │   │   └── EmptyState.tsx
│   │   ├── alerts/
│   │   │   ├── AlertList.tsx
│   │   │   └── AlertRow.tsx          # inline Acknowledge button
│   │   ├── incidents/
│   │   │   ├── IncidentList.tsx
│   │   │   └── IncidentDrawer.tsx    # slide-out: notes timeline, assign, resolve
│   │   └── users/
│   │       ├── UserTable.tsx
│   │       └── UserFormDialog.tsx    # create / edit dialog
│   ├── pages/
│   │   ├── Login.tsx         # Centered glass card: tenant_slug + email + password
│   │   ├── Dashboard.tsx     # KPI cards + recent alerts + live event feed
│   │   ├── Alerts.tsx        # Filterable alert list with live badge
│   │   ├── Incidents.tsx     # Incident list + IncidentDrawer
│   │   ├── Cameras.tsx       # Camera grid with status
│   │   ├── Detections.tsx    # Tabs: LPR Events / Face Events / Intrusion Events
│   │   ├── Watchlists.tsx    # Tabs: Plates / Faces
│   │   ├── Zones.tsx
│   │   ├── Evidence.tsx      # Image gallery using /evidence/{id}/file
│   │   ├── Audit.tsx
│   │   ├── Settings.tsx      # admin only — 4 setting knobs
│   │   └── Users.tsx         # admin only — user table + invite dialog
│   └── types/
│       ├── api.ts            # All response/request TypeScript interfaces
│       └── realtime.ts       # RealtimeEvent, RealtimeEventType
└── Dockerfile                # node:20-alpine build → nginx:alpine serve
```

---

### 17.5 Authentication Flow

**Storage**: access token in memory (Zustand), refresh token in `localStorage` (`seventh_ai_refresh_token`). No `httpOnly` cookie because there's no BFF — acceptable for a self-hosted deployment.

**Login flow**: `POST /api/v1/auth/login` → decode JWT claims (`jwtDecode`) → store in Zustand.

**Axios interceptor** (`api/client.ts`):
```typescript
// On 401: call refresh(), retry original request once; on second 401 or refresh failure → logout()
instance.interceptors.response.use(null, async (error) => {
  if (error.response?.status === 401 && !error.config._retry) {
    error.config._retry = true
    await authStore.refresh()   // updates accessToken in store
    error.config.headers.Authorization = `Bearer ${authStore.getState().accessToken}`
    return instance(error.config)
  }
  return Promise.reject(error)
})
```

**Route guard**: `<PrivateRoute>` checks `authStore.accessToken`; redirects to `/login` if absent.

---

### 17.6 RBAC in the Frontend

```typescript
// src/hooks/usePermission.ts
// Permission matrix mirrors the backend seed data exactly
const ROLE_PERMISSIONS: Record<number, string[]> = {
  1: [/* super_admin: all 24 codes */],
  2: [/* admin: all except role:manage */],
  3: [/* supervisor */],
  4: [/* operator */],
  5: [/* security_guard */],
  6: [/* viewer: *:read only */],
}

export function usePermission(code: string): boolean {
  const roleId = useAuthStore(s => s.user?.roleId)
  return roleId != null && (ROLE_PERMISSIONS[roleId]?.includes(code) ?? false)
}
```

`<PermissionGuard permission="alert:acknowledge">` renders `null` for roles without that code — same gate the backend enforces, preventing orphaned buttons before the 403 even fires.

Sidebar nav items are filtered by the same mechanism: `Cameras` shown only when `camera:read`, `Users` only when `user:read`, etc.

---

### 17.7 Real-Time WebSocket

```typescript
// src/hooks/useWebSocket.ts
// Reads accessToken from authStore; rebuilds URL when token rotates
// Exponential backoff reconnect: 1s, 2s, 4s, 8s, max 30s
// Exposes: status ('connecting'|'open'|'closed'), lastMessage
```

```typescript
// src/hooks/useRealtimeEvents.ts
// Parses lastMessage as RealtimeEvent
// alert_created    → queryClient.invalidateQueries(['alerts'])
//                  → notificationsStore.push({ severity, title, id })
// incident_created → queryClient.invalidateQueries(['incidents'])
//                  → notificationsStore.push(...)
// camera_status_changed → queryClient.invalidateQueries(['cameras'])
// TopBar renders a badge with notificationsStore.unreadCount
```

---

### 17.8 Key Pages

| Page | Route | Permission gate | Notes |
|------|-------|-----------------|-------|
| Login | `/login` | none | Tenant slug + email + password |
| Dashboard | `/` | any | 4 KPI cards (open alerts, open incidents, cameras online, detections today) + live event feed |
| Alerts | `/alerts` | `alert:read` | Status filter chips, live badge, acknowledge inline |
| Incidents | `/incidents` | `incident:read` | Status filter, slide-out drawer for detail/notes/resolve |
| Cameras | `/cameras` | `camera:read` | Card grid, status chip (online/degraded/offline) |
| Detections | `/detections` | `detection:read` | 3 tabs: LPR / Face / Intrusion |
| Watchlists | `/watchlists` | `watchlist:manage` | 2 tabs: Plates / Faces |
| Zones | `/zones` | `zone:manage` | CRUD table |
| Evidence | `/evidence` | `evidence:read` | Image gallery, click → full preview |
| Audit | `/audit` | `audit:read` | Paginated log table |
| Settings | `/settings` | `settings:read` | 4 knobs with save (write gated by `settings:write`) |
| Users | `/users` | `user:read` | Table + invite dialog (create gated by `user:create`) |

---

### 17.9 Docker Addition

```yaml
# docker/docker-compose.yml addition
frontend:
  build:
    context: ..
    dockerfile: docker/frontend.Dockerfile
  environment:
    - VITE_API_BASE_URL=http://localhost:8000
  ports: ["5173:80"]
  depends_on: [api]
```

`docker/frontend.Dockerfile`:
```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_API_BASE_URL=http://localhost:8000
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

`docker/nginx.conf` routes all non-asset requests to `index.html` (SPA fallback).

---

### 17.10 Build Sequence

For each step: backend additions are test-first; frontend steps are dev-server verified (start Vite, walk the golden path in the browser).

1. **Backend: user CRUD** — write `test_user_crud_round_trip` + `test_non_admin_cannot_create_users` → implement `backend/app/routers/users.py` → suite green
2. **Backend: evidence file endpoint** — write `test_evidence_file_download` → add `GET /{id}/file` to `backend/app/routers/evidence.py` → suite green
3. **Frontend scaffold** — `npm create vite@latest frontend -- --template react-ts`, install MUI + axios + TanStack Query + Zustand + react-router-dom + jwt-decode, configure `vite.config.ts` path aliases, apply glassmorphism theme
4. **Auth** — `api/auth.ts`, `store/auth.ts`, axios client with interceptor, Login page, PrivateRoute, `usePermission` hook
5. **App shell** — `AppShell`, `Sidebar` (nav filtered by permissions), `TopBar` (notification bell)
6. **Dashboard** — KPI cards via `useQuery`, static layout
7. **WebSocket** — `useWebSocket` + `useRealtimeEvents`; live badge on TopBar and Alerts page confirms push works
8. **Alerts page** — list + acknowledge; real-time invalidation
9. **Incidents page** — list + `IncidentDrawer` (notes, assign, resolve)
10. **Cameras page** — grid with status chips
11. **Detections page** — tabbed LPR / Face / Intrusion event tables
12. **Watchlists page** — plate CRUD + face CRUD
13. **Zones page** — CRUD table
14. **Evidence page** — gallery using `/evidence/{id}/file`
15. **Audit page** — paginated table
16. **Settings page** — 4 setting knobs
17. **Users page** — table + invite dialog
18. **Docker** — `frontend.Dockerfile` + `nginx.conf` + add service to `docker-compose.yml`; `docker compose up` → navigate to `http://localhost:5173` and run through smoke-test golden path manually

---

### 17.11 Verification

1. `docker compose up -d` (all Phase 1 services + new `frontend`)
2. Navigate to `http://localhost:5173` → Login page loads with glassmorphism card
3. Login with seeded test tenant credentials → redirect to Dashboard
4. Dashboard KPI cards show counts; Alerts page loads list
5. Open a second browser tab → trigger a test alert via `scripts/push_test_frame.py` → both tabs receive the WebSocket push within 2s (badge increments, list auto-refreshes)
6. Acknowledge an alert → status chip changes to `acknowledged`, no page reload
7. Open an incident → IncidentDrawer → add a note → note appears in timeline
8. Navigate to `/users` as admin → invite a new user → new row appears
9. Login as viewer role → `/users` route redirects or shows empty (no `user:read` permission) — RBAC enforced in UI
10. Navigate to `http://localhost:5173/settings` as viewer → Settings page renders read-only (Save button absent — `settings:write` guard)

---

# Round: Command Centre, Live Wall, Zone Drawing, Admin Hierarchy (Web + Desktop)

## Context

After the client-demo work (seeded "aegis" tenant, live RTSP loop, real-time
intrusion detections), the user walked through the actual UI and flagged four
concrete gaps that make the product feel unfinished for a live security
operations demo:

1. **Command Centre** — every widget (site cards, KPI cards, the alert feed)
   is purely decorative today; nothing is clickable, and alerts can't be
   acted on from the page an operator would actually be watching.
2. **Live Wall** — full-screen/kiosk mode already exists (Gap 83) but alerts
   only flash the affected cell's border; there's no persistent "what just
   happened" strip visible while watching full-screen, and each cell's
   record/fullscreen/remove controls are always-on clutter instead of
   appearing on hover.
3. **Analytics zones** — confirmed by reading the code: **restricted zones
   have no create UI at all today** (`Zones.tsx` never imports `createZone`;
   only crowd zones have a create dialog, and it's a raw JSON-array
   textarea). Operators can't draw a restricted zone without hand-crafting
   normalized `{x,y}` coordinates via direct API calls. The ask is a
   click-to-draw editor on the live feed, plus letting a drawn zone name
   which AI module(s) (intrusion, and/or behavior — the only two workers
   that currently query `restricted_zones`) it gates.
4. **Admin/Users page** — Aegis is the customer buying this product; its
   Super Admin is the top of *their* org. The Users table already labels
   roles with `Chip`s but has no visual sense of hierarchy — the ask is a
   dedicated hierarchy view with Super Admin visibly at the top.

**Scope decision (confirmed with user):** Web first — the Windows desktop
app is just an Electron `BrowserWindow` loading the same built frontend
(`desktop/electron/main.js`), so every web fix here ships to desktop for
free the next time `desktop/` is rebuilt (`npm run dist`, already a known,
working step from earlier this session). Mobile equivalents (no Command
Centre grid, no multi-camera wall, no zone drawing, no admin screen exist
today per a prior audit) are explicitly deferred to a separate round.

**Zone module scope (confirmed with user):** a drawn zone gets an
`applies_to_modules` picker (Intrusion / Behavior) rather than being
hardwired to intrusion only — this mirrors the fact that `intrusion_task.py`
and `behavior_task.py` already run the *identical* `SELECT id, polygon,
severity FROM restricted_zones WHERE tenant_id=%s AND camera_id=%s AND
is_active=TRUE` query independently (confirmed by reading both files) — the
column is a natural, small extension of an already-shared pattern, not a new
architecture.

**Admin hierarchy scope (confirmed with user):** add a dedicated
hierarchy/org-structure view rather than just re-styling the existing table
row — role numbering is already the hierarchy (`role_id` 1=Super Admin …
7=Client, per `BUILTIN_LABELS`/`ROLE_LABELS` duplicated in `Roles.tsx` and
`Users.tsx`), so this is a presentation layer on data that already exists.

---

## 1. Command Centre — clickable widgets + alert response popup

**File:** `frontend/src/pages/CommandCentre.tsx` (currently: `SiteCard`,
`KpiCard`, `AlertFeed` items are all static `Paper`/`Box` — `AlertFeed`
items explicitly set `cursor: 'default'` and have no `onClick`).

- **`SiteCard`** (line 102): add `onClick` navigating to `/alerts?site_id=…`
  (the Alerts page already supports a `site_id` query filter per its
  existing site-filter chips — reuse, don't rebuild). Add `cursor: pointer`
  and a hover elevation bump consistent with the existing `&:hover`
  box-shadow rule already on the card.
- **`KpiCard`** (line 58): make each card clickable to the page it
  summarizes — Cameras Online/Offline → `/cameras`, Active Alerts/Critical →
  `/alerts`, Guards on Duty → `/roster`. Add `onClick` + `cursor: pointer`.
- **`AlertFeed` item** (line 242): clicking an alert opens a new
  `AlertResponseDialog` (new component) showing full alert detail (title,
  module, site/camera, severity, timestamp) with **Acknowledge** and **Mark
  False Positive** actions wired to the already-existing endpoints
  `POST /api/v1/alerts/{id}/acknowledge` and
  `POST /api/v1/alerts/{id}/false-positive` (backend/app/routers/alerts.py
  lines 107, 134 — both already permission-gated on `alert:acknowledge`,
  already have frontend API wrappers per the pattern used in `Alerts.tsx`,
  reuse those wrappers rather than adding new ones). On success, invalidate
  `['cc-overview']` so the Command Centre's own feed refreshes.
- This is the "immediate pop up" ask: a `Dialog` opens synchronously on
  click, not a route navigation — matches "respond with immediate pop up."

## 2. Live Wall — full-screen alert strip + hover-only cell controls

**File:** `frontend/src/pages/LiveWall.tsx`.

- **Bottom alert strip:** add a slim, persistent `Box` docked to the bottom
  of the wall (visible in and out of kiosk mode) that lists the last ~5
  alerts from the existing `lastMessage` WebSocket stream the page already
  subscribes to (`useWebSocket()`, already parsed in the `alert_created`
  `useEffect` at line 354 for the auto-pop/flash behavior — extend that same
  handler to also push into a small rolling `recentAlerts` state array,
  rather than adding a second WebSocket subscription). Each strip entry:
  severity dot, title, camera/site, relative time, click-to-jump (scrolls/
  highlights the corresponding cell if it's on the wall). This complements
  the existing per-cell flash (Gap 83), it doesn't replace it — the flash
  says *which* camera, the strip says *what just happened across the wall*.
- **Hover-only per-cell controls:** `LiveCell`'s bottom control bar (record/
  fullscreen/remove, lines 250–292) currently renders unconditionally.
  Wrap it in a `sx={{ opacity: 0, transition: 'opacity 0.15s', '&:hover, &:focus-within': { opacity: 1 } }}`
  pattern on the parent cell `Box` (standard CSS hover-reveal, no JS state
  needed) so controls appear only on mouse hover — keep the top overlay
  (camera name, alert chip, site chip, recording indicator) always visible
  since those are status, not controls.
- **Full-screen monitoring:** kiosk mode already exists (`toggleKiosk`,
  line 400, uses Electron's `window.electronAPI.setKiosk` when running in
  the desktop app, falls back to the browser Fullscreen API otherwise) —
  verify/confirm this is what "should be full screen monitoring" means by
  making the kiosk button more prominent (e.g. move it out of the small
  icon cluster into a clearly-labeled "Full Screen" button next to "Add
  Camera") rather than building a second, redundant full-screen mechanism.

## 3. Interactive zone-drawing editor + `applies_to_modules`

Adopts the design from a dedicated design pass (already completed, full
detail in the referenced files below) with two confirmed defaults: **(a)**
client-side canvas frame capture, no new backend snapshot endpoint, and
**(b)** create-only in this pass — dragging an existing zone's saved polygon
to edit it is a natural v2, not built now.

### Backend
- **New migration** `backend/alembic/versions/0058_zone_applies_to_modules.py`:
  `ALTER TABLE restricted_zones ADD COLUMN applies_to_modules jsonb NOT NULL DEFAULT '["intrusion"]'`.
- **`backend/app/routers/zones.py`**: add `applies_to_modules: list[str] = ["intrusion"]`
  to `ZoneCreate` (validate against `{"intrusion", "behavior"}`, mirroring
  the existing `validate_severity` field_validator pattern at line 56);
  include the column in `_ZONE_SELECT` (line 35) and the `INSERT` in
  `create_zone` (line 108).
- **`ai-worker/worker/tasks/intrusion_task.py`** (line 70) and
  **`ai-worker/worker/tasks/behavior_task.py`** (line 70): both already run
  `SELECT id, polygon, severity FROM restricted_zones WHERE tenant_id=%s AND
  camera_id=%s AND is_active=TRUE` — add `applies_to_modules` to the SELECT
  list and filter in Python (`if "intrusion" not in row.applies_to_modules:
  continue` / `"behavior"` respectively) rather than a JSONB `@>` SQL
  operator, to keep the change a small, symmetric diff across both files.

### Frontend (per the dedicated design — build in this order)
1. **`frontend/src/lib/videoCoords.ts`** (new, pure functions, no DOM/React)
   — `computeContainRect(containerW, containerH, mediaW, mediaH)` solves the
   `objectFit: contain` letterboxing math; `screenToNormalized`/
   `normalizedToScreen` wrap it for click↔normalized-coordinate conversion.
   Write this first and unit-test the aspect-ratio math in isolation
   (e.g. an 800×450 container with 640×480 media) before touching any
   component — it's the one part of this feature that's easy to get subtly
   wrong.
2. **`frontend/src/components/common/ZoneDrawOverlay.tsx`** (new) —
   interactive SVG layer, same `viewBox="0 0 1000 1000"` and
   `preserveAspectRatio="none"` convention as the existing read-only
   `DetectionOverlay.tsx` (reference, not modified), but `pointerEvents:
   'auto'` with Pointer Events (`pointerdown`/`pointermove`/`pointerup` +
   `setPointerCapture`) for click-to-place vertices, a dashed "rubber band"
   preview segment, click-first-vertex-to-close, and post-close per-vertex
   drag. Fully controlled by its parent (no internal vertex state).
3. **`frontend/src/components/common/ZonePolygonEditor.tsx`** (new) —
   owns vertex/drag/closed state; renders the camera's live MJPEG feed
   (reuse the exact `liveUrl` construction from `LiveWall.tsx`'s `LiveCell`)
   with a "Capture Frame" button that freezes one still via
   `canvas.drawImage()` + `toDataURL()` (avoids drawing on a constantly-
   refreshing image — precision matters more than liveness for a mostly-
   permanent zone shape); composes `ZoneDrawOverlay` on top plus a toolbar
   (Capture / Undo / Finish / Clear / Retake).
4. **`frontend/src/pages/Zones.tsx`** — add `RestrictedZoneDialog` (new),
   mirroring the existing `CrowdZoneDialog` shell exactly (camera `Select`
   sourced from `getCameras()`, name `TextField`, severity `Select`) but
   swapping the JSON textarea for `ZonePolygonEditor`, plus a checkbox
   group for `applies_to_modules` (Intrusion / Behavior). Add a "+ Add
   Zone" button above `RestrictedZonesTable` — mirror `CrowdZonesTable`'s
   existing button pattern exactly (currently restricted zones have no
   create entry point at all). `createZone` in `frontend/src/api/zones.ts`
   already accepts the right shape; only needs `applies_to_modules` added
   to its payload type once the backend column lands.

No new npm dependencies — plain SVG, native Pointer Events, and the
browser's built-in Canvas 2D API are sufficient.

## 4. Admin hierarchy view

**File:** `frontend/src/pages/Users.tsx`.

- Add a compact "Organization Hierarchy" `GlassCard` section above the
  existing user table (reuse the tiered-card visual language already
  established by `CommandCentre.tsx`'s `KpiCard`/`SiteCard` — small
  gradient-bordered cards, not a new charting library). Render one row per
  role tier present in the tenant (Super Admin → Admin → Supervisor →
  Operator → Security Guard → Viewer → Client, using the same
  `ROLE_LABELS` map already defined at line 24), each showing the count of
  users at that tier and, for Super Admin specifically, the actual name(s)
  (there's normally exactly one) pulled from the existing `users` query
  already loaded by the page — no new API call needed, just a
  `useMemo` grouping the already-fetched list by `role_id`.
- Give the Super Admin tier a visually distinct treatment (accent border/
  icon, e.g. reusing `SecurityIcon` or a crown-style icon already available
  in `@mui/icons-material`) so it reads as "head of everyone" at a glance.
- The existing flat table below is unchanged in function — this is an
  additive section, not a replacement, so no regression risk to existing
  edit/deactivate/session actions.

---

## Verification

1. **Command Centre**: click a `SiteCard` → navigates to `/alerts` filtered
   to that site; click a KPI card → navigates to the matching page; click an
   alert in the feed → dialog opens with Acknowledge/Mark False Positive,
   both actions succeed and the feed refreshes without a page reload.
2. **Live Wall**: trigger a live alert (the demo's intrusion loop already
   does this every ~60–100s) → it appears in the new bottom strip within a
   few seconds of the WebSocket push, independent of whether that camera is
   currently on the wall; hover a cell → record/fullscreen/remove controls
   fade in; move mouse away → they fade out; click the (now more prominent)
   Full Screen button → wall goes kiosk/fullscreen, Esc exits.
3. **Zone drawing**: open Zones → Restricted Zones tab → "+ Add Zone" (new
   button) → pick camera 1 (the demo's live cam) → Capture Frame → click 4+
   points on the frozen still → click first vertex to close → polygon fills
   with severity color → check Intrusion + Behavior → Create → new row
   appears in the table with correct point count; verify in Postgres that
   `applies_to_modules` was stored correctly and that a subsequent
   intrusion-zone breach against that camera still fires (confirms the
   worker-side filter didn't break the existing, already-working pipeline).
4. **Admin hierarchy**: Users page shows a new hierarchy card above the
   table with Super Admin visually distinct and correctly counted against
   the seeded "aegis" tenant's 7 users.
5. **Desktop**: after `cd desktop && npm run dist`, launch the rebuilt exe,
   confirm all four fixes above render identically inside the Electron
   window (no desktop-specific code needed — same bundled frontend).
11. `docker compose build frontend` completes without error

---

# Round: Live Wall — True Operator Control Room

## Context

The user shared a screenshot of Live Wall in its current "Full Screen"
state and pointed out it isn't actually a control-room view — the full
sidebar (Command Centre, Dashboard, Alerts, … Audit Logs) and top bar are
still visible, because "Full Screen" today only calls the browser/Electron
fullscreen API (`document.documentElement.requestFullscreen()` /
`window.electronAPI.setKiosk()` in `LiveWallPage.toggleKiosk`,
`frontend/src/pages/LiveWall.tsx:420`) — that fullscreens the *window*, not
the app's own chrome, which is rendered unconditionally by
`AppShell.tsx` around every route's `<Outlet/>`.

Beyond that framing bug, three real capability gaps came out of reading the
actual operator workflow the user described — confirmed by reading the
current code, not assumed:

1. **No analytics switcher on the wall itself.** `showOverlay` in
   `LiveWall.tsx` is a single global on/off "AI" button
   (`OVERLAY_KEY` in localStorage) — it can't show "only LPR" or "only
   intrusion," and it has no idea which AI modules this tenant actually
   licenses. `DetectionOverlay.tsx` draws *every* zone and *every*
   detection it's handed, with no module filter.
2. **No way to draw a zone from the wall.** Zone drawing
   (`ZonePolygonEditor` + `RestrictedZoneDialog`, built earlier this
   project) only exists on the separate `/zones` page — an operator who
   spots a problem while watching a feed has to leave monitoring, go to
   Zones, and re-find the right camera from a dropdown.
3. **No real alert response on the wall.** Clicking a "Recent Alerts" strip
   entry today just re-triggers the cell's border flash
   (`LiveWall.tsx:652`) — no acknowledge, no resolve, no way to loop in a
   supervisor. Command Centre has a real `AlertResponseDialog`
   (`CommandCentre.tsx:343`) but it's a page-local component with only
   Acknowledge / Mark False Positive, and Live Wall doesn't use it at all.

Verified while reading the backend that most of the *primitives* for #3
already exist and just aren't exposed together: `POST /alerts/{id}/assign`
and `POST /alerts/{id}/notes` (`backend/app/routers/alerts.py:389,447`),
and — critically — `resolve_alert_site_id` /
`resolve_push_targets` in `backend/app/services/alert_routing.py` already
compute "which on-duty/site-assigned staff should know about this camera"
for Gap 82's push routing. Escalation reuses that exact query instead of
inventing new routing logic.

Also verified: `GET /api/v1/licenses/{tenant_id}` requires
`license:manage` (super-admin-only, `backend/app/routers/licenses.py:44`)
— an operator role cannot call it, so a new, minimal, any-authenticated-
user endpoint is needed for "which modules does *my* tenant have."

## 1. Real focus/kiosk mode (hide app chrome, not just the window)

**New:** `frontend/src/store/focusMode.ts` — tiny Zustand store,
`{ isFocusMode: boolean; setFocusMode(v: boolean): void }`.

**`frontend/src/components/layout/AppShell.tsx`**: read `isFocusMode`;
when true, skip rendering `<Sidebar/>` and `<TopBar/>` and let `<Outlet/>`
take the full viewport. Add a small always-visible floating exit affordance
(a single icon button, top-right, `position: fixed`) that calls
`setFocusMode(false)` — the operator must always have a visible way out,
not just Esc.

**`LiveWall.tsx`**: `toggleKiosk` calls `setFocusMode(!kiosk)` alongside
the existing fullscreen-API call (both stay — fullscreen for true
edge-to-edge monitor use, focus-mode for hiding app chrome even when not
OS-fullscreen, e.g. a operator who just wants the wall maximized in a
normal window). Keep the existing `fullscreenchange` listener syncing
`kiosk` state; mirror it into `setFocusMode(false)` on exit.

## 2. Tenant-licensed analytics switcher, on the wall

**Backend** (`backend/app/routers/licenses.py`): add
`GET /api/v1/licenses/me/enabled-modules`, **no special permission** (just
`get_db_with_tenant`, any authenticated user) — returns
`{"modules": string[]}`. Mirrors the existing "zero rows configured = don't
cripple the tenant" convention already used by `_check_module_licenses` in
`backend/app/routers/cameras.py:191`: if the tenant has no
`tenant_module_licenses` rows at all, return every module in
`ALL_AI_MODULES`; otherwise return only `module_type` where
`is_enabled = TRUE`.

Also add `applies_to_modules` to the zones `SELECT` in the existing
`GET /{camera_id}/overlay` endpoint (`cameras.py:162`) — it's already a
real column (migration 0058) but this endpoint never selected it, so the
overlay response has no way to say which module a zone belongs to.

**Frontend**: `getMyEnabledModules()` in `frontend/src/api/licenses.ts`.
Replace `LiveWall.tsx`'s single "AI" `Button` with a `Stack` of `Chip`s
(same pattern as the existing status/site/module filter chips in
`Alerts.tsx:235`) built from the intersection of `ALL_AI_MODULES` and the
licensed list; selection is a `Set<string>` persisted to localStorage
(new `OVERLAY_MODULES_KEY`, replacing the old boolean `OVERLAY_KEY`).
Passed down as `activeModules: string[]` through `LiveCell` to
`DetectionOverlay`.

**`DetectionOverlay.tsx`**: new prop `moduleFilter?: string[]`. When
provided, filter `data.detections` to `moduleFilter.includes(d.module_type)`
and filter `data.zones` to
`z.applies_to_modules.some(m => moduleFilter.includes(m))`. `enabled`
stays as-is (`moduleFilter.length > 0` becomes the new "is overlay on at
all" condition, computed in `LiveCell`).

## 3. Inline zone drawing from a wall cell

**Extract** the existing `RestrictedZoneDialog` out of `Zones.tsx` (currently
a page-local function, `Zones.tsx:160`) into
`frontend/src/components/common/RestrictedZoneDialog.tsx`, unchanged except
one new optional prop: `initialCameraId?: string`. When set, the camera
`Select` is pre-filled and disabled (the dialog opened *from* that camera,
so re-picking makes no sense) instead of defaulting to empty. `Zones.tsx`
imports it from the new location — zero behavior change for that page.

**`LiveWall.tsx`**: add a "Draw Zone" `IconButton` (a shape/polygon icon)
to `LiveCell`'s existing hover-controls row (next to record/fullscreen/
remove, `LiveWall.tsx:262-294`), opening `RestrictedZoneDialog` with
`initialCameraId={cell.camera_id}`. On successful create, invalidate
`['camera-overlay', cell.camera_id]` so the new zone appears on that cell's
`DetectionOverlay` immediately, without a page refresh.

## 4. Real alert response: Acknowledge / Resolve / Escalate

**Backend** (`backend/app/routers/alerts.py`):
- `POST /{alert_id}/dismiss` — singular counterpart to the existing
  `bulk-dismiss` (`alerts.py:250`), same semantics
  (`status IN ('open','acknowledged') → 'dismissed'`). Surfaced to the
  operator as **"Resolve"** — reusing the existing `dismissed` status value
  rather than adding a new one, so nothing else that reads `alerts.status`
  (Alerts.tsx filters, mobile, analytics) needs to change.
- `GET /{alert_id}/escalation-targets` — looks up the alert's `camera_id`,
  calls `resolve_alert_site_id` (`alert_routing.py:29`) to get the site,
  then runs the same shift/site-assignment query `resolve_push_targets`
  uses (`alert_routing.py:56-66`) but joined to return
  `{user_id, full_name, role_id}[]` instead of just ids. If that's empty
  (no site, or nobody rostered), fall back to all tenant users with
  `role_id IN (2,3)` (admin/supervisor) — an escalation picker must never
  come back empty.

**Frontend**: extract `CommandCentre.tsx`'s local `AlertResponseDialog`
(`CommandCentre.tsx:343`) into
`frontend/src/components/common/AlertResponseDialog.tsx`. Its alert-shape
prop is already generic enough (`id/title/severity/module_type/site_name/
camera_name/created_at`) to serve both call sites unchanged. Add two
actions alongside the existing Acknowledge / Mark False Positive:
- **Resolve** → calls the new dismiss endpoint.
- **Escalate** → reveals an inline list fetched from
  `escalation-targets`; picking a person calls the existing
  `assignAlert(alertId, userId)` (already in `frontend/src/api/alerts.ts`,
  used today by Command Centre's `AlertFeed`) and then
  `addAlertNote(alertId, "Escalated to {name} ({role})")` (existing
  `POST /notes`) so the escalation leaves an audit trail on the alert
  timeline — no new backend concept, just composing the two endpoints that
  already exist.

Both call sites take an `onResolved` callback for their own query
invalidation: `CommandCentre.tsx` keeps invalidating `['cc-overview']`;
`LiveWall.tsx` invalidates the wall's local `recentAlerts` entry (drop it
from the strip) and `['camera-overlay', cameraId]`. Wire the dialog into
`LiveWall.tsx` from two places: clicking a cell's alert `Chip`
(`LiveWall.tsx:230-237`) and clicking a "Recent Alerts" strip entry
(`LiveWall.tsx:649-672`, replacing the current flash-only `onClick`).

## Scope guardrails (explicitly not doing)

- No DB migration — `dismissed` status, `applies_to_modules`, and
  `tenant_module_licenses` all already exist.
- Escalation reuses `assign` + `notes`, not a new schema concept; incident-
  level `dispatch.py` (SLA/arrival tracking) is untouched — that's a
  heavier workflow for confirmed incidents, orthogonal to a quick
  alert-level ping.
- Per-camera analytics filtering (different modules shown per cell) is not
  built — one wall-wide filter, matching how the existing "AI" toggle
  already works and how the user described it ("same screen I can switch
  analytic," singular). Easy to extend to per-cell later if asked.
- Mobile app is untouched this round — this is specifically the web/desktop
  Live Wall operator screen from the shared screenshot.

## Build sequence

1. Backend: `GET /licenses/me/enabled-modules`, `applies_to_modules` in the
   overlay SELECT, `POST /alerts/{id}/dismiss`,
   `GET /alerts/{id}/escalation-targets`.
2. Frontend plumbing: `focusMode.ts` store, `AppShell.tsx` conditional
   chrome, `licenses.ts` API addition, `alerts.ts` additions if needed.
3. Extract `RestrictedZoneDialog.tsx` and `AlertResponseDialog.tsx` to
   `components/common/`, update `Zones.tsx` and `CommandCentre.tsx` imports
   (behavior-neutral refactor — verify both pages still work identically
   before adding anything new to the extracted components).
4. Add the new actions/props to the extracted `AlertResponseDialog`.
5. Redesign `LiveWall.tsx`: focus-mode wiring, module-filter chip row
   replacing the "AI" button, "Draw Zone" cell button, alert dialog wiring
   on cell chips + recent-alerts strip.
6. `DetectionOverlay.tsx` module filtering.
7. `npx tsc --noEmit` clean, then live-verify per below, then
   `docker compose build frontend` + `docker compose up -d frontend`.

## Verification

1. Open Live Wall → Full Screen → sidebar and top bar disappear entirely,
   only the wall + toolbar + a small exit affordance remain; click exit (or
   Esc) → chrome returns.
2. Analytics chip row shows only this tenant's licensed modules (cross-check
   against `/licenses/{tenant_id}` as super-admin on `/tenants`); toggling
   a module on/off changes which detection boxes/zone outlines render on
   every wall cell within one overlay refresh (~2s).
3. Click "Draw Zone" on a live cell → dialog opens with that camera already
   selected and locked → capture frame → draw polygon → create → zone
   outline appears on that same cell without a page reload.
4. Trigger a demo alert → click its chip on the cell (or the recent-alerts
   strip entry) → dialog opens → Escalate → picker shows on-duty/site staff
   (not an empty list) → pick one → alert's `assigned_to_user_id` updates
   and a note is recorded → Resolve on a different alert → status becomes
   `dismissed` and it drops off the strip.
5. Regression: Command Centre's alert dialog still does Acknowledge/Mark
   False Positive correctly after the extraction (same behavior as before,
   plus the two new actions available there too).
6. `docker compose build frontend` clean; spot-check the rebuilt container
   for #1–#4.

---

# Round: ShiftSecure Integration — Roadmap + Phase 1 (Employee Data Model)

## Context

The user has a separate, already-completed guard workforce management
product ("ShiftSecure" — Laravel 10 web + Flutter mobile, live at
gkbrothers.com.sg) covering 20 web modules (Employees, Sites, Shifts,
Roster with AI auto-scheduling, Attendance, Approvals, Leave, Payroll/CPF/
IR8A, Violations, Incidents, Guard Tour QR+NFC, SOP Training, Notifications,
Reports, Billing, Settings, Super Admin, Client Portal) plus 14 mobile
modules. The instruction: fold ShiftSecure's feature set into Seventh AI
Vision under the Seventh AI Vision name — wherever the two overlap or
ShiftSecure is more complete, ShiftSecure's spec is the target; wherever
Seventh AI Vision has no equivalent, build it fresh in its actual stack
(FastAPI/Postgres/React/RN — not Laravel/Flutter, so this is a from-scratch
build against ShiftSecure's spec, not a code port).

**Gap analysis (three parallel research passes, each with file:line
evidence, confirmed against the live code — not assumed):**

| Already covers spec (reuse/extend) | Partial (needs real extension) | Hard gap (net-new) |
|---|---|---|
| Super Admin (`Tenants.tsx`/`tenants.py`, suspend-blocks-login confirmed real in `auth_service.py:88-94`) | Roster (`shifts.py`, `Roster.tsx` — has recurring patterns + site×day grid, missing employee×day grid, shift types, draft/publish, 8-rule AI scheduler) | **Employee fields** — no NRIC/FIN, work pass, DOB, nationality, bank details, employment type on `users` |
| Client Portal (`ClientPortal.tsx`, role 7) | Attendance (`shifts` table has unused lat/lon columns — no geofence check, no breaks, no late/OT detection, no correction requests) | **Payroll** (CPF, OT pay, payslip) — zero code |
| Roles/permissions (data-driven, custom roles already supported — Gap 91) | Guard Tour NFC (backend models it; `PatrolScanScreen.tsx` only scans QR) | **IR8A** — zero code |
| Guard Tour QR + checkpoints (`patrols.py`) | Document expiry (exists for contractors in `contractors.py`, no employee equivalent, no check-in blocking) | **Leave Management** — zero code |
| SOS/Panic (`shifts.py:660`, `patrols.py:310` — two duplicate paths, needs consolidation) | | **Violations** — zero code, nothing plays this role today |
| Incidents (richer status workflow than spec) | | **SOP Training w/ quizzes+certs** — existing `Training.tsx`/`training.py` is a manual log/cert-tracker, no quiz engine |
| Push infra (Expo, site+shift routing) — only 6 of 19 event types wired | | **Client Billing/Invoicing** (guard-hours × rate) — existing `billing.py` is Stripe SaaS subscription billing, a different concept entirely |
| | | **Shift/attendance settings** — `tenant_settings` today is 100% AI-detection thresholds |

**Confirmed build order** (dependency-driven, user-selected): Employee data
model → Attendance + Roster hardening → Violations → Leave Management →
Payroll/CPF/IR8A → SOP Training rebuild → Client Billing/Invoicing, with
smaller items (NFC on mobile, SOS consolidation, remaining notification
types, shift/attendance company settings) threaded into the phase that
produces the data they need. Each phase after this one will get its own
detailed plan section when reached — payroll/leave in particular touch
compliance-sensitive calculations that deserve their own focused design
pass rather than being speculatively detailed now.

This section covers **Phase 1 only**: the employee data model. Every later
phase reads from it (payroll needs work-pass type for CPF eligibility,
attendance needs it for document-expiry check-in blocks, violations/leave
need employment_type), so it goes first regardless of which later phase is
prioritized next.

**User note carried forward to Phase 2 (Attendance + Roster hardening):**
the live attendance monitor by site — one of the confirmed-missing pieces
of ShiftSecure's Attendance module (no equivalent view exists today; the
closest thing is the general Live Wall camera grid, which is video, not a
guard-checked-in/out roster view) — must ship as a first-class, visually
polished screen, not a bare table. When Phase 2 is designed in detail: (1)
build it using the same visual language already established this session —
`GlassCard`, staggered `fadeUpSx` entrance, `useCountUp` on headline counts
(both in `frontend/src/lib/motion.ts`), live status transitions animated
rather than snapping; (2) pull live check-in/out state over the existing
WebSocket channel (`useRealtimeEvents`/Redis pub-sub) the same way Live
Wall and Command Centre already do, not polling; (3) run it through the
`ui-ux-pro-max` skill (already invoked once this session for this exact
theme — glassmorphism enterprise security SaaS dashboard) before finalizing
layout/color/motion choices, consistent with how Command Centre and Live
Wall were built. Noted here now so it isn't dropped when Phase 2 scope gets
finalized — not built in this Phase 1 pass, which touches only the
employee data model.

## Phase 1 — Employee Data Model

### Backend

**New migration** `backend/alembic/versions/0060_employee_profile.py`
(down_revision `0059`):

```sql
ALTER TABLE users
  ADD COLUMN nric_fin VARCHAR(20),
  ADD COLUMN date_of_birth DATE,
  ADD COLUMN nationality VARCHAR(100),
  ADD COLUMN phone VARCHAR(30),
  ADD COLUMN address TEXT,
  ADD COLUMN work_pass_type VARCHAR(20)
      CHECK (work_pass_type IN ('citizen','pr','ep','sp','wp')),
  ADD COLUMN work_pass_expiry DATE,
  ADD COLUMN employment_type VARCHAR(20)
      CHECK (employment_type IN ('full_time','part_time','contract')),
  ADD COLUMN designation VARCHAR(100),
  ADD COLUMN department VARCHAR(100),
  ADD COLUMN date_joined DATE,
  ADD COLUMN bank_name VARCHAR(100),
  ADD COLUMN bank_account_number VARCHAR(50),
  ADD COLUMN emergency_contact_name VARCHAR(255),
  ADD COLUMN emergency_contact_phone VARCHAR(30);
```

All nullable — existing users remain valid; fields are filled in
incrementally by admin/HR, matching ShiftSecure's 4-tab form UX. `users` is
already in `RLS_TABLES` (migration `0001`) with the standard tenant-
isolation policy applied, so no RLS work is needed for these columns.

```sql
CREATE TABLE employee_documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_type       VARCHAR(30) NOT NULL
        CHECK (document_type IN ('passport','work_pass','certification','other')),
    document_number     VARCHAR(100),
    issuing_body        VARCHAR(255),
    issue_date          DATE,
    expiry_date         DATE,
    storage_path        VARCHAR(500),
    notes               TEXT,
    created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_employee_documents_tenant_user ON employee_documents(tenant_id, user_id);
CREATE INDEX idx_employee_documents_expiry ON employee_documents(expiry_date) WHERE expiry_date IS NOT NULL;
```

Standard RLS triplet (same pattern as `user_sites` in migration `0051`,
the most recent precedent for a new tenant-scoped child table).

**`backend/app/routers/users.py`** (extend, don't restructure — same
raw-SQL-via-`text()` style already used throughout this file):
- Add all 15 new fields as optional to `UserCreate` and `UserUpdate`
  (Pydantic `Literal["citizen","pr","ep","sp","wp"] | None` for
  `work_pass_type`, `Literal["full_time","part_time","contract"] | None`
  for `employment_type` — tighter validation than the DB CHECK alone).
  `update_user`'s existing dynamic `SET` clause (line 109:
  `{k: v for k, v in body.model_dump(...) if v is not None}`) picks up the
  new fields with zero other changes — this is why create stays minimal
  (email/password/role only) and the rest gets filled in via `PUT`,
  matching ShiftSecure's incremental 4-tab form.
- Extend the `SELECT` column lists in `list_users`/`get_user` to include
  the new columns.
- New sub-resource endpoints, mirroring the existing `/{user_id}/sites`
  pattern (lines 147-195) exactly:
  - `GET /{user_id}/documents` — list, permission `user:read`
  - `POST /{user_id}/documents` — `multipart/form-data` (file +
    `document_type`/`document_number`/`issuing_body`/`issue_date`/
    `expiry_date`/`notes`), permission `user:update`. Saves the file under
    `EMPLOYEE_DOCS_ROOT/{tenant_id}/{user_id}/{document_id}.{ext}` (new
    settings key alongside the existing `EVIDENCE_ROOT` pattern already
    used for camera evidence storage) and inserts the row.
  - `PUT /{user_id}/documents/{doc_id}` — update metadata (not the file),
    permission `user:update`.
  - `DELETE /{user_id}/documents/{doc_id}` — permission `user:delete`.
- **Not built in this phase**: check-in blocking on expired documents —
  that logic lives in the shift-start endpoint (`shifts.py`), which is
  Phase 2's territory. This phase only makes the expiry data queryable.

### Frontend

**`frontend/src/types/api.ts`**: extend the `User` interface with the 15
new optional fields; add an `EmployeeDocument` interface.

**`frontend/src/api/users.ts`**: extend `createUser`/`updateUser` param
types with the new optional fields (already exports `getUsers, createUser,
updateUser, deactivateUser, getUserSites, setUserSites` — add
`getEmployeeDocuments`, `uploadEmployeeDocument`, `updateEmployeeDocument`,
`deleteEmployeeDocument` alongside the existing `getUserSites`/
`setUserSites` pair).

**`frontend/src/pages/Users.tsx`** — `UserFormDialog` (lines 42-114)
currently a single flat form (email/password/full name/role only). Convert
to a `Tabs`-based dialog, matching ShiftSecure's 4-tab structure:
1. **Personal** — full name, email, phone, DOB, nationality, NRIC/FIN,
   address, emergency contact name/phone.
2. **Employment** — role (existing `Select`, unchanged), employment type,
   designation, department, date joined.
3. **Work Pass & Documents** — work pass type + expiry fields, plus an
   inline `employee_documents` list (matches the existing `List`/
   `ListItem` pattern already used elsewhere on this page for site
   assignments, lines ~170-210) with an "Add Document" button opening a
   small upload dialog, and an expiry-status `Chip` (valid/expiring-soon/
   expired) per document computed client-side from `expiry_date`.
4. **Bank Details** — bank name, account number.

All new fields optional and only shown on Edit (mirrors the existing
`{!isEdit && (...)}` pattern for email/password) — Create stays the
current minimal email/password/role flow; the rest gets filled in via Edit
immediately after, consistent with the backend split above.

### Build sequence

1. Migration `0060_employee_profile.py` — run `alembic upgrade head` inside
   the API container, confirm `\d users` shows the 15 new columns and
   `employee_documents` exists with RLS enabled.
2. `backend/app/routers/users.py` — extend schemas/SELECTs, add the 4
   document sub-resource endpoints, add `EMPLOYEE_DOCS_ROOT` to
   `backend/app/core/config.py` + `docker-compose.yml` (new volume, same
   shape as the existing `evidence_data` volume).
3. `frontend/src/types/api.ts` + `frontend/src/api/users.ts` — type and API
   additions.
4. `frontend/src/pages/Users.tsx` — tabbed `UserFormDialog` rewrite.
5. `npx tsc --noEmit` clean, then `docker compose build api frontend` +
   restart those two containers.

### Verification

1. `docker exec docker-api-1 python -m alembic upgrade head` (PowerShell,
   per the established test-runner convention) succeeds; migration head is
   `0060`.
2. As an admin on the seeded "aegis" tenant: open Users → Edit an existing
   guard → all 4 tabs render → fill NRIC, DOB, work pass (type=`wp`,
   expiry a future date), employment type, bank details → Save → reopen →
   all values persisted correctly.
3. Work Pass tab → Add Document → upload a small test file as
   `document_type=work_pass` with an expiry date → appears in the list
   with a correct status chip; set an expiry date in the past on a second
   upload → chip shows "Expired".
4. `GET /api/v1/users/{id}/documents` (via Swagger `/docs` or curl) returns
   the uploaded rows with correct `storage_path`.
5. Regression: existing Create-user flow (email/password/role only) and
   the existing Site-assignment tab both still work unchanged after the
   dialog restructure.
6. `docker compose build api frontend` clean.

---

# Round: ShiftSecure Phase 2A — Attendance Hardening + Live Monitor

## Context

Continuing the confirmed build order after Phase 1 (employee data model). This round splits the originally-scoped "Attendance + Roster hardening" phase in two: **2A (this round) = Attendance** — geofence check-in/out, breaks, late/overtime detection, correction requests, and the live attendance monitor UI the user explicitly asked for. **2B (separate, later round) = Roster rebuild** — employee×day grid, shift types, draft/publish, the 8-rule AI auto-scheduler. Splitting keeps each round reviewable; the auto-scheduler in particular is a substantial algorithm that deserves its own focused design pass rather than being bundled in. Attendance doesn't block on the roster rebuild — it operates on `shifts` rows that already exist today (one-off or pattern-generated), so there's no ordering problem.

**Confirmed current state (read directly from source, not re-derived from memory):**
- `shifts` table (migration `0008`) already has `check_in_lat/lon`, `check_out_lat/lon` columns, but `start_shift`/`end_shift` in `backend/app/routers/shifts.py:304-358` never read or write them — no request body at all today, just a shift_id path param.
- `sites` table (`backend/app/routers/sites.py`) has `latitude`/`longitude` but no geofence radius column.
- Zero break tracking, zero late/overtime computation, zero correction-request table anywhere.
- Mobile `ShiftScreen.tsx` (`mobile/src/screens/ShiftScreen.tsx:47-57`) calls `startShift(id)`/`endShift(id)` with no location capture — `expo-location` isn't even installed in `mobile/package.json`. This is the guard-facing check-in surface (the web `GuardOps.tsx` shift tab is the ops/admin override surface and doesn't need geofence capture).
- Realtime push pattern confirmed consistent across the whole backend: every router does its own inline `redis.publish(f"tenant_events:{tenant_id}", json.dumps({...}))` (no shared helper exists — `shifts.py:733`'s SOS handler is the closest precedent and is what this round's new events will mirror). Frontend consumption is a `switch (event.event_type)` in `frontend/src/hooks/useRealtimeEvents.ts:50` calling `queryClient.invalidateQueries`.
- Tenant-setting-with-fallback pattern confirmed: every call site does its own inline `SELECT setting_value FROM tenant_settings WHERE setting_key=:k` + Python-side hardcoded default when the row is missing (e.g. `backend/app/services/continuous_recording.py:84`) — no shared "get effective setting" helper exists either.

## Backend

**Migration `0061_attendance_hardening.py`:**
```sql
ALTER TABLE sites ADD COLUMN geofence_radius_meters INTEGER;  -- NULL = use tenant default

ALTER TABLE shifts
  ADD COLUMN is_within_geofence BOOLEAN,   -- NULL = no location data submitted
  ADD COLUMN is_late BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN late_minutes INTEGER,
  ADD COLUMN overtime_minutes INTEGER;

CREATE TABLE shift_breaks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    shift_id    UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    break_start TIMESTAMPTZ NOT NULL DEFAULT now(),
    break_end   TIMESTAMPTZ,              -- NULL = break in progress
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- standard RLS triplet + idx_shift_breaks_shift(shift_id)

CREATE TABLE attendance_corrections (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    shift_id              UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    guard_user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_check_in    TIMESTAMPTZ,
    requested_check_out   TIMESTAMPTZ,
    reason                TEXT NOT NULL,
    status                VARCHAR(20) NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending','approved','rejected')),
    reviewed_by_user_id   UUID REFERENCES users(id),
    reviewed_at           TIMESTAMPTZ,
    review_notes          TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- standard RLS triplet + idx_attendance_corrections_shift/status

INSERT INTO permissions (code, description, category) VALUES
  ('attendance:read',    'View live attendance monitor and corrections', 'guard'),
  ('attendance:request', 'Request an attendance correction',              'guard'),
  ('attendance:manage',  'Approve / reject attendance corrections',       'guard');
-- attendance:read -> roles 1-6 (mirrors shift:read); attendance:request -> roles 1,2,3,4,5
-- (mirrors shift:manage grantees + operator/security_guard); attendance:manage -> roles 1,2,3
-- (mirrors shift:manage exactly)
```

**`backend/app/core/config_keys.py`**: add 3 keys to `SETTING_VALIDATORS` — `attendance.geofence_radius_meters` (`_positive_int_validator`, effective default 200 when no tenant row and no per-site override), `attendance.late_grace_minutes` (`_positive_int_validator`, default 10), `attendance.overtime_threshold_minutes` (`_positive_int_validator`, default 15).

**New `backend/app/services/geofence.py`** — one pure function, `haversine_meters(lat1, lon1, lat2, lon2) -> float`, standard great-circle distance formula. Kept standalone (not inlined in `shifts.py`) so it's independently testable and reusable if Phase 3 (Violations) later needs the same check for auto-generating a "failed geofence" violation.

**`backend/app/routers/shifts.py` changes** (extend in place, same file/style — it already owns the shift lifecycle):
- `start_shift`: add `latitude: float | None = None, longitude: float | None = None` as query params (matching the file's existing convention — `end_shift` already takes `handover_notes` as a bare query param, not a `Body()` model). Logic: fetch the shift's `scheduled_start` + `site_id`; if lat/lon provided and the site has coordinates, compute `is_within_geofence` via `haversine_meters` against `site.geofence_radius_meters` (falling back to the `attendance.geofence_radius_meters` tenant setting via the same inline-query pattern used elsewhere); compute `is_late`/`late_minutes` from `now() - scheduled_start` vs the `attendance.late_grace_minutes` setting. **Geofence failure does not block check-in** — it's recorded, not enforced, matching that "blocks check-in" was specifically a document-expiry behavior in the original spec, not a geofence one; a failed-geofence *violation* record is Phase 3's job once the Violations table exists. `UPDATE shifts SET ... check_in_lat/lon, is_within_geofence, is_late, late_minutes` alongside the existing `status='active', actual_start=now()`. Publish a `attendance_status_changed` realtime event after commit (same inline `redis.publish` pattern as the SOS handler).
- `end_shift`: add the same `latitude`/`longitude` query params; compute `overtime_minutes` from `now() - scheduled_end` vs `attendance.overtime_threshold_minutes`; before the main UPDATE, auto-close any dangling open break (`UPDATE shift_breaks SET break_end = now() WHERE shift_id = :id AND break_end IS NULL`) as a safety net for a guard who forgot to end their break; publish `attendance_status_changed`.
- Two new endpoints, `POST /{shift_id}/break/start` and `POST /{shift_id}/break/end` (same permission `shift:manage` as start/end — breaks are part of the shift lifecycle): start inserts into `shift_breaks` (rejects with 409 if the shift isn't `active` or already has an open break); end sets `break_end = now()` on the open row (404 if none). Both publish `attendance_status_changed`.
- `list_shifts`: add `EXISTS(SELECT 1 FROM shift_breaks b WHERE b.shift_id = sh.id AND b.break_end IS NULL) AS on_break` to the SELECT so the mobile "My Shifts" screen can show break state and toggle its button correctly — this is the same endpoint mobile's `getShifts()` already calls.

**New `backend/app/routers/attendance.py`** (prefix `/api/v1/attendance`):
- `GET /live?site_id=` (`attendance:read`) — today's shifts (`scheduled_start::date = CURRENT_DATE`) joined to guard/site names plus the `on_break` EXISTS-subquery, with a computed status label per row (`not_started` / `late` / `checked_in` / `on_break` / `checked_out`) and a `summary` object with counts per status — this is what feeds the live monitor's KPI row and list.
- `POST /corrections` (`attendance:request`) — body `{shift_id, requested_check_in?, requested_check_out?, reason}`; a guard (roles 4,5) may only file against their own `shift.guard_user_id`, admin/supervisor (1,2,3) may file on behalf of anyone.
- `GET /corrections?status=` (`attendance:read`) — guards (4,5) see only their own rows; roles 1,2,3 see all.
- `PUT /corrections/{id}/approve` and `PUT /corrections/{id}/reject` (`attendance:manage`) — approve applies the requested times onto the `shifts` row (`actual_start`/`actual_end` via `COALESCE`) in the same transaction as marking the correction approved; reject just records `review_notes`. Both publish `attendance_status_changed` so the live monitor and any open corrections list refresh immediately.

Register `attendance.py` in `main.py` alongside the other routers.

## Frontend

**`frontend/src/api/attendance.ts`** (new) — `getLiveAttendance(siteId?)`, `startBreak(shiftId)`, `endBreak(shiftId)`, `requestCorrection(data)`, `listCorrections(status?)`, `approveCorrection(id)`, `rejectCorrection(id, notes?)`.

**`frontend/src/api/guards.ts`**: `startShift`/`endShift` gain optional `latitude`/`longitude` params (purely additive — `GuardOps.tsx`'s existing calls stay valid unchanged).

**`frontend/src/hooks/useRealtimeEvents.ts`**: add a `case 'attendance_status_changed':` invalidating `['attendance-live']` (and `['corrections']` when a correction event) — same one-line-per-case pattern already used for `alert_created`/`camera_status_changed`.

**New `frontend/src/pages/Attendance.tsx`** — the page the user specifically asked to be visually polished, reusing this session's established motion/glass toolkit rather than a bare table:
- `PageHeader` title "Attendance", subtitle "Live check-in/out monitoring across all sites".
- KPI row: 4 `GlassCard`s (Checked In / On Break / Late / Not Started) with staggered `fadeUpSx` entrance and `useCountUp` on the numbers (both from `frontend/src/lib/motion.ts`, already built and used on Dashboard/CommandCentre/Analytics).
- Site filter `Select` (reuses `getSites`).
- Live list grouped by site (same `Map<site, rows>` grouping pattern already used in `Roster.tsx`'s coverage grid): guard name, scheduled time, a status `Chip` whose color transitions on change (`sx={{ transition: 'background-color 0.2s' }}`) rather than snapping, a late badge when `late_minutes > 0`.
- Data comes from `useQuery(['attendance-live', siteId], () => getLiveAttendance(siteId))`; real-time freshness comes from the WS invalidation above, not polling.
- "Corrections" section below, gated `PermissionGuard permission="attendance:manage"` for Approve/Reject, listing pending requests with the guard name, shift time, requested change, and reason.
- Run the color/spacing/motion choices through the `ui-ux-pro-max` skill before finalizing, per the earlier note.

**`frontend/src/App.tsx`**: add `<Route path="attendance" element={<AttendancePage />} />`. **Sidebar**: "Attendance" nav item near Roster under Monitoring, gated on `attendance:read`. **`usePermission.ts`**: add the 3 new permission codes to `ALL_PERMISSIONS`/role matrix mirroring the DB grant matrix above.

## Mobile

- Add `expo-location` to `mobile/package.json` (not currently installed anywhere in the app — confirmed via grep).
- `mobile/src/screens/ShiftScreen.tsx`: on the "Start" press, call `Location.requestForegroundPermissionsAsync()` then `getCurrentPositionAsync()` and pass `{latitude, longitude}` into `startShift`; same for "End". Add "Start Break"/"End Break" buttons alongside the existing Patrol/DOB Log/Handover/End row when `s.status === 'active'`, toggled by the new `on_break` flag now present on `getShifts()`'s response. Location failure (permission denied, GPS off) must not block the check-in — call `startShift`/`endShift` without coordinates in that case, same as the web ops override path.
- `mobile/src/api/patrols.ts`: `startShift`/`endShift` gain the optional lat/lon params; add `startBreak`/`endBreak` wrappers.

## Verification

1. Migration `0061` applies cleanly (`docker compose up -d api` picks it up via the existing `alembic upgrade head` startup command); `\d shift_breaks`, `\d attendance_corrections` show RLS enabled.
2. As a guard on the seeded "aegis" tenant: start a shift with coordinates inside the site's radius → `is_within_geofence=true`, `is_late` correctly reflects grace period; start another shift late → `is_late=true` with a sane `late_minutes`.
3. Start Break → live monitor shows `on_break`; End Break → reverts to `checked_in`; End Shift with an open break → break auto-closes, `overtime_minutes` computed correctly for a shift ended past its scheduled end + threshold.
4. File a correction request as a guard for someone else's shift → 403; for own shift → 201; approve as admin → shift's `actual_start`/`actual_end` updated accordingly.
5. Open `/attendance` in two browser tabs; start/end a shift in one → the other tab's KPI counts and list update within ~1s via WebSocket, no manual refresh.
6. `npx tsc --noEmit` clean (frontend), mobile `npx tsc --noEmit` clean.
7. `docker compose build api frontend` clean; live-verify per the preview workflow the same way Phase 1 was verified (frontend-dev on port 5174 against the real Docker api/postgres, `aegis`/`ops@aegis.demo`/`Demo1234!`).

---

# Round: ShiftSecure Phase 2B — Roster Rebuild (Employee×Day Grid + AI Auto-Scheduler)

## Context

Closes the roster half of the phase that was split after Phase 1 ("the auto-scheduler in particular is a substantial algorithm that deserves its own focused design pass"). ShiftSecure's spec asks for: monthly employee×day grid, shift types (day/night/split), manual + bulk assign, an 8-rule AI auto-scheduler, and a draft→publish workflow where publishing notifies guards.

**Confirmed current state (read directly from source):**
- `shifts` has no `shift_type` column; `shift_patterns` (migration `0055`) is guard×site×days-of-week×start_time×duration — no shift-type concept either.
- Zero existing concept anywhere of: leave/unavailability, guard shift preferences, or per-site minimum coverage (confirmed via grep — no matches for any of these terms in `backend/`).
- `class ShiftUpdate(BaseModel)` is defined at `shifts.py:62` but **no endpoint uses it** — there is currently no way to edit a shift's guard/time/notes once created, at all. This is a pre-existing gap this round closes as a side effect (needed for "manual assign" on already-published shifts).
- "Supervisor" = `role_id 3` (confirmed against `ROLE_LABELS` used throughout the frontend).
- Existing `Roster.tsx` coverage grid is site×day (rows=sites, cols=next 7 dates); this round adds an employee×day view alongside it, not a replacement — the site×day view stays useful for "who's covering this site."

**Key design decision — draft/publish via a separate staging table, not a flag on `shifts`:** rather than adding a `publish_status` column to the live `shifts` table (which would require adding `AND publish_status='published'` to every existing read path — `list_shifts`, `roster_coverage`, `attendance/live` — with real risk of missing one and leaking an unpublished draft shift to a guard's mobile app or the live monitor), draft output lives in new `roster_batches`/`roster_draft_shifts` tables that are structurally invisible to every existing consumer. Publishing is an explicit `INSERT INTO shifts SELECT ... FROM roster_draft_shifts`. This means **zero changes to any already-shipped, already-relied-upon shift-reading endpoint** — the existing pattern-based `generate_roster_shifts` flow (Gap 86) and one-off `create_shift` continue exactly as they are today, unaffected by this round.

**Key design decision — `guard_leave_blocks` is intentionally minimal, not Phase 4's Leave Management:** the confirmed build order puts Leave Management *after* Roster, but rule 5 ("respect leave") needs *some* signal of guard unavailability to be meaningful now. Rather than stub the rule out, this round adds a minimal date-range unavailability table — `{guard_user_id, start_date, end_date, reason}`, no types/entitlements/approval workflow. Phase 4 becomes the writer of a richer superset later; the scheduler's read contract (`is guard X unavailable on date Y`) doesn't need to change when that happens.

**Algorithm scope decision:** a full CSP/ILP solver is out of scope — every prior model/algorithm choice in this project favored pragmatic, testable, "balanced" engineering over theoretical optimality (e.g. the YOLOv8s tier decision, greedy dedup/cooldown patterns). The scheduler is a **greedy, day-by-day, slot-by-slot assignment with hard exclusions + score-based ranking**, not a solver. Coverage shortfalls and missing-supervisor cases become **warnings on the draft for a human to fix**, not blocking errors — matching the "flag, don't block" precedent already set for geofence failures in Phase 2A.

## Backend

**Migration `0062_roster_rebuild.py`:**
```sql
ALTER TABLE shifts ADD COLUMN shift_type VARCHAR(10) CHECK (shift_type IN ('day','night','split'));
-- Auto-inferred at generation time (start hour 5-16 -> day, else night); 'split' only ever
-- set explicitly (two shift rows, same guard/site/date, both tagged 'split').

ALTER TABLE sites ADD COLUMN min_guards_per_shift INTEGER;  -- NULL = no minimum enforced

CREATE TABLE guard_leave_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    guard_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL CHECK (end_date >= start_date),
    reason TEXT,
    created_by_user_id UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- standard RLS triplet + idx_guard_leave_blocks_guard(guard_user_id, start_date, end_date)

CREATE TABLE guard_shift_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    guard_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE UNIQUE,
    preferred_shift_type VARCHAR(10) CHECK (preferred_shift_type IN ('day','night')),
    preferred_off_days INTEGER[],  -- 0=Mon..6=Sun
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- standard RLS triplet

CREATE TABLE roster_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id UUID REFERENCES sites(id) ON DELETE SET NULL,  -- NULL = all sites
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','discarded')),
    rules_summary JSONB,  -- {"coverage_shortfalls": 2, "missing_supervisor_days": 1, "unfilled_slots": 3}
    generated_by_user_id UUID REFERENCES users(id),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by_user_id UUID REFERENCES users(id),
    published_at TIMESTAMPTZ
);
-- standard RLS triplet

CREATE TABLE roster_draft_shifts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    batch_id UUID NOT NULL REFERENCES roster_batches(id) ON DELETE CASCADE,
    guard_user_id UUID REFERENCES users(id),  -- NULL = unfilled slot
    site_id UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    scheduled_start TIMESTAMPTZ NOT NULL,
    scheduled_end TIMESTAMPTZ NOT NULL,
    shift_type VARCHAR(10) NOT NULL CHECK (shift_type IN ('day','night','split')),
    warnings JSONB NOT NULL DEFAULT '[]'  -- e.g. ["no_supervisor_present","below_min_coverage"]
);
-- standard RLS triplet + idx_roster_draft_shifts_batch(batch_id)

INSERT INTO permissions (code, description, category) VALUES
  ('roster:autoschedule', 'Run AI auto-scheduler and manage roster drafts', 'guard')
ON CONFLICT (code) DO NOTHING;
-- roles 1,2,3 (mirrors shift:manage grantees) get roster:autoschedule

-- New tenant_settings keys (config_keys.py): roster.max_consecutive_days (default 6),
-- roster.min_rest_hours (default 11)
```

**New `backend/app/services/roster_autoschedule.py`** — the algorithm, kept as pure/testable functions separate from the router (mirrors the `geofence.py`/`roster.py` service-module pattern already established):
- `infer_shift_type(start_time) -> 'day'|'night'` — pure function.
- `is_guard_on_leave(guard_id, date, leave_blocks) -> bool` — pure function over pre-fetched leave rows (no DB access itself, keeps it unit-testable like `dedup.py`'s `BreachTracker` pattern from the AI-worker side).
- `score_candidate(guard, slot, assignment_history) -> float` — pure scoring function implementing rules 3/4/8 (day-night balance, preference match, off-day stagger) as additive bonuses.
- `generate_draft(db, tenant_id, site_id, period_start, period_end) -> batch_id` — orchestrates: for each day × each site's active `shift_patterns` slots, hard-exclude candidates violating rules 1/2/5 (min rest via last-assigned-shift lookup, max consecutive days via a rolling window, leave via `is_guard_on_leave`), score the rest, assign the top-scoring candidate, record warnings for rule 6 (coverage) and rule 7 (supervisor presence, checked per site+day after all that day's slots are assigned) into each `roster_draft_shifts.warnings` and rolled up into `roster_batches.rules_summary`.

**`backend/app/routers/roster.py`** (new file — the existing pattern/coverage endpoints in `shifts.py` stay where they are; this file owns everything new):
- `POST /api/v1/roster/auto-schedule` (`roster:autoschedule`) — body `{site_id?, period_start, period_end}`, calls `generate_draft`, returns the batch + draft shifts + warnings.
- `GET /api/v1/roster/batches/{id}` (`roster:autoschedule`) — batch detail for the review UI.
- `PUT /api/v1/roster/draft-shifts/{id}` (`roster:autoschedule`) — manual edit of one draft slot (reassign guard / adjust time) before publish — this is "manual assign."
- `POST /api/v1/roster/batches/{id}/publish` (`roster:autoschedule`) — validates `status='draft'`, inserts all draft rows (with non-NULL `guard_user_id`) into `shifts` as `status='scheduled'`, marks the batch `published`, sends an Expo push to every affected guard (reuses the existing `push_tokens:{tenant_id}:{user_id}` Redis-set send path already used elsewhere — same mechanism, new trigger point) and publishes a `roster_published` realtime event.
- `DELETE /api/v1/roster/batches/{id}` (`roster:autoschedule`) — discard a draft.
- `GET/PUT /api/v1/roster/leave-blocks`, `GET/PUT /api/v1/roster/preferences/{guard_user_id}` — small CRUD for the two new input tables (`shift:manage` for leave-blocks writes/admin view; a guard can read/write their own preferences under `shift:read`).

**`backend/app/routers/shifts.py`**: add `PUT /{shift_id}` (`shift:manage`) using the already-defined-but-unused `ShiftUpdate` model — dynamic `SET` clause, same pattern as `update_shift_pattern`. This is "manual assign" for already-published shifts (the draft-stage equivalent is the `roster.py` endpoint above).

Register `roster.py` in `main.py`.

## Frontend

**`frontend/src/api/roster.ts`** (extend existing file) — add `autoSchedule`, `getBatch`, `updateDraftShift`, `publishBatch`, `discardBatch`, `getLeaveBlocks`/`setLeaveBlock`, `getPreferences`/`setPreferences`, and `updateShift` (the new `shifts.py` PUT).

**`frontend/src/pages/Roster.tsx`** — additive, not a rewrite:
- New "View by: Site | Employee" toggle above the existing coverage grid. Employee view: rows = guards (from `getUsers` filtered to roles 4/5, same `GUARD_ROLES` set already defined in this file), columns = the same `nextDates` window already computed, cells show that guard's shift (site name + shift-type color dot) or an "Off" state — reuses the exact same `Map`-grouping/`Chip` rendering pattern already in the file, just keyed by guard instead of site.
- New "Auto-Schedule" button (gated `roster:autoschedule`) opens a dialog: site picker (or "All Sites"), period-start/end date pickers → calls `autoSchedule` → on success, navigates to a new draft-review panel.
- Draft review panel: lists `roster_draft_shifts` for the batch, grouped by day, each row showing guard/site/time/shift-type and a warning `Chip` (amber) when `warnings` is non-empty; unfilled slots (`guard_user_id === null`) shown distinctly (red "Unfilled"); each row has an inline guard-reassign `Select` calling `updateDraftShift`; batch-level summary banner from `rules_summary` (e.g. "2 coverage shortfalls, 1 day missing a supervisor"). "Publish" and "Discard" buttons at the bottom.
- Small "Leave & Preferences" section (or a lightweight dialog reachable per-guard) for the two new CRUD endpoints — kept minimal, matching the tables' minimal schema.

**`frontend/src/hooks/useRealtimeEvents.ts`**: add `case 'roster_published':` invalidating `['shift-patterns']`/`['roster-coverage']`/`['my-shifts']` (mirrors the mobile query key so a guard's app refreshes the moment their roster is published).

**`frontend/src/hooks/usePermission.ts`**: add `roster:autoschedule` to `ALL_PERMISSIONS` and to role 3's (supervisor's) explicit list (roles 1/2 inherit via `ALL_PERMISSIONS`).

## Verification

1. Migration `0062` applies cleanly; `\d roster_batches`, `\d roster_draft_shifts`, `\d guard_leave_blocks`, `\d guard_shift_preferences` show RLS enabled.
2. Add a leave block for a guard covering tomorrow → run auto-schedule for a period including tomorrow → that guard is never assigned tomorrow (rule 5), confirmed via the draft's rows.
3. Run auto-schedule twice back-to-back for an overlapping period → assignments respect rule 1 (no slot starts <11h after that guard's prior assigned slot ends) and rule 2 (no guard assigned 7 consecutive days) — verified by inspecting `roster_draft_shifts` ordering per guard.
4. A site with `min_guards_per_shift=2` but only 1 eligible guard → resulting slot warning includes `below_min_coverage`; a day with zero role-3 guards assigned at a site → `no_supervisor_present` warning appears in `rules_summary`.
5. Manually reassign a draft slot via `PUT /roster/draft-shifts/{id}` → reflected on next `GET /roster/batches/{id}`.
6. Publish a batch → rows appear in `shifts` with `status='scheduled'` and the correct `shift_type`; a WS client subscribed to `roster_published` receives the event; discard a different draft batch → its rows never reach `shifts`.
7. `PUT /api/v1/shifts/{id}` on an already-published shift changes its guard/time — confirms the previously-dead `ShiftUpdate` model now works.
8. Employee×day grid in `Roster.tsx` shows the same guards/shifts the site×day grid shows, just transposed — spot-check against `GET /roster/coverage`.
9. `npx tsc --noEmit` clean; `docker compose build api frontend` clean; live-verify via the same frontend-dev-on-5174-against-real-Docker workflow used in Phases 1 and 2A.

---

# Round: Manager Role + 30-Day Roster + Editable Published Shifts + Urgent Reassign + Timezone Fix

## Context

User feedback on the shipped Roster/Attendance work, four distinct asks bundled together — split into what belongs together vs. what's genuinely separate. This round covers everything RBAC/roster-related: a new global "Manager" role, extending the roster's coverage-warning system to require both Supervisor and Manager presence over a (now 30-day-default) period, wiring the already-built-but-never-exposed `PUT /shifts/{id}` into an actual UI control so admins can edit a published roster, a quick-reassign path for guards who take urgent/same-day leave, and a real timezone bug in the live attendance monitor. **Check-in/out selfie photos are a separate round** — confirmed via research that no photo-capture flow exists anywhere in the mobile app today (only QR scanning); it's a self-contained mobile-camera+storage+display feature with no dependency on anything in this round, so it doesn't belong bundled in.

**Confirmed via research (not assumed):**
- Backend permission enforcement (`backend/app/dependencies/permissions.py`) resolves purely through the `role_permissions` table — no hardcoded role_id logic anywhere in the enforcement path, so adding role_id `8` is safe by construction.
- Gap 91's custom-role endpoint (`backend/app/routers/roles.py`) is **not** the right mechanism for Manager — custom roles are tenant-scoped (`tenant_id` set) and numbered from a sequence starting at 100 (`0057_custom_roles.py`). A global built-in role needs a migration inserting directly into `roles`, same as roles 1-7 were seeded (`0001_initial_schema.py`, `0012_client_portal_role.py`).
- Frontend has **four independent, already-slightly-inconsistent** role-label maps with no shared source: `TopBar.tsx:27`, `Sidebar.tsx:54`, `Users.tsx:30`, and `Roles.tsx:24` (called `BUILTIN_LABELS` there). All four need the new role added by hand.
- **Two real bugs found that a new role_id=8 would silently trip**, both pre-existing and worth fixing now since this change is exactly what exposes them: `frontend/src/pages/Compliance.tsx:199` filters tour-assignable guards via `role_id >= 4` (a raw range check) when the file already has a correct explicit `GUARD_ROLES = new Set([3,4,5])` at line 30 that just isn't used at this call site — Manager(8) would get miscategorized as an assignable low-level guard. `mobile/src/screens/EmergencyScreen.tsx:186` gates emergency-broadcast send rights via `user.roleId <= 3` — Manager(8) would be wrongly *denied* despite being senior to Operator/Guard.
- `tenants.timezone` (`VARCHAR(50)`, default `'Asia/Singapore'`, added in `0022_platform_products.py`) is already used correctly elsewhere — `backend/app/services/roster.py`'s `generate_roster_shifts` does `JOIN tenants t ON t.id = p.tenant_id` + `AT TIME ZONE COALESCE(t.timezone, 'UTC')`. `attendance.py`'s `GET /live` has no such join at all and filters `WHERE sh.scheduled_start::date = CURRENT_DATE` using the Postgres server's own timezone — this is the actual bug behind "day end reset automatically to next day": for a Singapore tenant, "today" currently flips at 08:00 local time (UTC midnight), not local midnight.

## Backend

**Migration `0063_manager_role_and_roster_extras.py`:**
```sql
INSERT INTO roles (id, code, name, description) VALUES
  (8, 'manager', 'Manager', 'Operational manager — between Admin and Supervisor in seniority')
ON CONFLICT (id) DO NOTHING;

-- Manager's permission set = a clone of Admin's (role 2) existing grants. This is the
-- practical meaning of "between Admin and Supervisor": Admin's grant set already excludes
-- the two super-admin-only permissions (tenant:manage, license:manage — see usePermission.ts's
-- existing ALL_PERMISSIONS.filter), so cloning it gives Manager everything Supervisor has
-- and more, without re-deriving a permission list by hand.
INSERT INTO role_permissions (role_id, permission_id)
SELECT 8, permission_id FROM role_permissions WHERE role_id = 2
ON CONFLICT DO NOTHING;
```
No new tables — this migration is pure seed data plus the two lines below.

**`backend/app/services/roster_autoschedule.py`:**
- `SUPERVISOR_ROLE_IDS = (1, 2, 3)` → `(1, 2, 3, 8)` (a Manager present also satisfies "a supervisor-or-above is present" — Manager is senior to Supervisor).
- New `MANAGER_ROLE_IDS = (1, 2, 8)`.
- In `generate_draft`'s per-site-per-day rule 6/7 pass, add a parallel check: `if not any(gid in manager_ids for gid in assigned): warnings.append("no_manager_present")` (mirrors the existing supervisor check exactly, same `assigned_today_by_site` data already computed — no new queries needed, `guard_rows` already carries `role_id`).
- `rules_summary` gains `missing_manager_days`, computed the same way as `missing_supervisor_days`.
- **Not changed**: the automatic-fill candidate pool (`GUARD_ROLE_IDS = (3, 4, 5)`) — the algorithm still won't *auto-assign* a Manager onto a patrol slot (that's not a realistic manager duty); Manager presence is tracked from whatever's actually scheduled (auto-filled or manually assigned per the new edit UI below), not force-generated.

**`backend/app/routers/attendance.py`** — fix the timezone bug in `get_live_attendance`: add `JOIN tenants t ON t.id = sh.tenant_id` and change the date filter to `WHERE (sh.scheduled_start AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date = (now() AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date` — the exact idiom already proven in `services/roster.py`.

**`backend/app/routers/roster.py` — leave blocks become reassignment-aware:**
`create_leave_block` currently just inserts the row. Extend it to also look up any **published** shifts (`shifts` table, not drafts) for that guard whose `scheduled_start::date` falls inside `[start_date, end_date]` and `status = 'scheduled'`, and return them alongside the created leave block: `{..leave block fields.., "affected_shifts": [{id, site_name, scheduled_start, scheduled_end}, ...]}`. This is the "urgent offs" ask — the moment an admin logs a same-day/urgent leave, the response tells the frontend exactly which already-published shifts now need a new guard, so it can prompt for reassignment immediately using the shift-edit control below instead of the admin having to go hunting through the coverage grid.

## Frontend — Manager role wiring

Add `8: 'Manager'` to all four label maps: `TopBar.tsx`, `Sidebar.tsx`, `Users.tsx`, `Roles.tsx`'s `BUILTIN_LABELS`. Fix `Compliance.tsx:199` to use the already-defined `GUARD_ROLES` set instead of `role_id >= 4`. Fix `mobile/src/screens/EmergencyScreen.tsx:186`'s `canSend` check from `user.roleId <= 3` to an explicit set including Manager (`[1,2,3,8].includes(user.roleId)`). In `Roster.tsx`, widen the **manual-assignment** guard pickers (pattern-creation dialog, draft-shift reassign `Select`, leave/preferences guard pickers — all currently `GUARD_ROLES = new Set([3,4,5])`) to also include `8`, so an admin *can* manually put a Manager on a shift for oversight even though auto-schedule won't do it on its own. `WARNING_LABELS` gains `no_manager_present: 'No Manager'`, and the batch summary banner surfaces `missing_manager_days` alongside the existing three counts.

## Frontend — 30-day default + editable published shifts + urgent reassign

**`AutoScheduleDialog`** (`Roster.tsx`): change the default `periodEnd` computation from `+6` days to `+29` days (30-day window), matching "30 days roster needed."

**New `EditShiftDialog` component** (`Roster.tsx`) — the piece that makes "admin can able to change roster schedule" real: a small dialog taking a `shift: CoverageShift` prop, a guard `Select` (using the widened guard-role list above) and start/end time fields, calling the already-existing `updateShift(id, data)` API function from `roster.ts` (added in the previous round, never wired to any control until now). Wire it in two places:
1. **Click any shift chip in the coverage grid** (both Site and Employee views) → opens `EditShiftDialog` for that shift. This alone satisfies "admin can change roster schedule" for the general case.
2. **Urgent-reassign flow**: `LeavePreferencesCard`'s `addLeave` mutation's `onSuccess` now receives `affected_shifts` from the extended `createLeaveBlock` response (per the backend change above) — if non-empty, immediately open `EditShiftDialog` pre-loaded with the first affected shift (with a small "N more shifts also affected" note if there's more than one), so the admin can reassign right away instead of hunting for it afterward.

## Verification

1. Migration `0063` applies cleanly; `SELECT * FROM roles WHERE id=8` shows `manager`; `SELECT COUNT(*) FROM role_permissions WHERE role_id=8` equals the same count as `role_id=2`.
2. Log in as a Manager-role test user (create one via Users.tsx, role=Manager) → confirm they land with Admin-equivalent access (can reach Roster/Attendance/Settings), not blocked.
3. `Compliance.tsx`'s tour-assignment picker no longer lists a Manager as an assignable "guard"; `EmergencyScreen.tsx` lets a Manager-role mobile user send an emergency broadcast.
4. Auto-schedule a 30-day period (confirm the dialog's default dates now span 30 days) on a roster with no Manager ever assigned to any site → `rules_summary.missing_manager_days` is non-zero and per-slot `no_manager_present` warnings appear; manually assign a Manager to one shift via the new `EditShiftDialog` on a published day → re-running/inspecting shows that day no longer warns.
5. Click a shift chip in the coverage grid (Site view, then Employee view) → `EditShiftDialog` opens with correct pre-filled data → reassign to a different guard → grid updates, DB `shifts` row confirmed changed via direct query.
6. Add a leave block for a guard who already has a **published** shift within that date range → `affected_shifts` comes back non-empty → `EditShiftDialog` auto-opens pre-loaded with that shift → reassign → confirm the leave-taking guard is no longer on that shift.
7. Timezone: with the seeded "aegis" tenant (`Asia/Singapore`), confirm `GET /attendance/live` at a wall-clock time where UTC-date and Singapore-date differ (e.g. any time between 00:00-08:00 SGT) returns shifts for the *Singapore* calendar day, not the UTC one — spot-check against a shift's `scheduled_start` converted manually.
8. `npx tsc --noEmit` clean (frontend + mobile for the EmergencyScreen fix); `docker compose build api frontend` clean; live-verify via the established frontend-dev-on-5174 workflow.

---

# Round: Selfie Check-In/Out Photos + Liveness + Anti-Mock-GPS + Site Geofence Config

## Context

The previously-deferred "check-in/out selfie photo" round, now specified in full by the user: photos must reach the server on both check-in and check-out, the selfie must pass a liveness check (reject a photo held up to the camera), a mock/fake GPS location must be rejected outright (not just flagged), and every site needs its geofence fully configurable — confirmed via direct code reading that this last part is a **real, pre-existing gap**: `sites.py`'s `SiteCreate`/`SiteUpdate`/`list_sites`/`get_site` never reference `geofence_radius_meters` at all, even though the column has existed since migration `0061` — an admin currently has no way to set it per site; every site silently falls back to the tenant-wide `attendance.geofence_radius_meters` default (200m).

**Confirmed via research, not assumed:**
- `backend/pyproject.toml` already depends on `insightface>=0.7` and `onnxruntime>=1.17` **inside the API container itself** (not just the AI worker image) — `backend/app/routers/watchlist.py` already has a working, thread-safe, lazy-loaded `insightface.app.FaceAnalysis(name="buffalo_l")` singleton (`_load_face_app()`) used synchronously inside the `/watchlist/faces/enroll` request (via `asyncio.to_thread`) to extract a face embedding from an uploaded photo. This is the exact "run a face model synchronously inside an API request" pattern liveness needs — no new heavy dependency required, just a small ONNX anti-spoof classifier layered on top of the already-loaded face detector.
- `expo-location` (already installed, `~17.0.1`) returns a `mocked` boolean directly on the position object on Android — no extra native package needed. No equivalent exists on iOS (Apple doesn't expose this in production builds without a jailbreak), so iOS is documented as a known, honest platform limitation rather than faked.
- `mobile/src/screens/ShiftScreen.tsx`'s `tryGetCoords()` already does best-effort GPS capture for start/end; `expo-camera` (`~15.0.16`) is already installed and used for QR scanning (`CameraView`) in `VisitorsScreen.tsx`/`PatrolScanScreen.tsx` — `takePictureAsync` (still-photo capture) has zero existing precedent anywhere in the app.
- `backend/app/routers/shifts.py`'s `start_shift`/`end_shift` currently take `latitude`/`longitude` as plain query params (not multipart) — a photo can be added as an additional `UploadFile` parameter on the same endpoints without breaking the query-param contract, since FastAPI reads query params from the URL independently of the request body's content-type.
- Storage convention to mirror: `EMPLOYEE_DOCS_ROOT`/`EVIDENCE_ROOT` — a settings key + Docker volume + `{tenant_id}/{entity_id}/...` path-on-disk convention, DB stores only the relative path.
- Anti-spoof model choice: `face-antispoof-onnx` (MiniFASNetV2-SE, ~600KB, already ONNX-exported) — fits directly on top of the existing `onnxruntime` dependency with no PyTorch needed. Like the original Phase 1 LPR plate-detector weight, **this is an external asset that must be manually sourced and placed** (`backend/models/liveness_minifasnet.onnx`) — not something written by this plan.

**Enforcement semantics (the key design decision):**
- **Mock GPS → hard block.** Mobile reads `mocked` from `expo-location`'s result and refuses to even open the camera/submit if true, with a clear "Fake GPS detected" alert. The server independently re-checks the client-reported flag and rejects with `403` before any DB write — defense in depth, since a modified client could lie, but this is the best signal available without a full remote-attestation system (out of scope). This matches "should be not allow" literally — unlike geofence, which stays a **flag, not a block** (a legitimate guard can be just outside an imprecise radius; that established Phase 2A judgment is unchanged).
- **Liveness → hard block.** New tenant setting `attendance.liveness_min_score` (0.0–1.0, same `_range_validator` shape as `lpr.confidence_threshold`). Score below threshold → `422`, mobile shows a retake prompt (not a dead end).
- **Photo → now required**, not optional, on both check-in and check-out — matches "should be comes to server" and makes the liveness check meaningful (an optional photo would make the whole feature toggleable-away). Mobile is the only client, so there's no external API back-compat concern.
- **Site geofence → configuration, not new blocking behavior.** The ask here is that every site has geofence *configurable* and *visible*, not that geofence becomes stricter. Sites list gets a "geofence not configured" indicator (missing lat/lon or radius) to prompt admins to complete it — non-disruptive, since a site can legitimately exist before its coordinates are surveyed.

## Backend

**Migration `0064_checkin_photos_liveness.py`:**
```sql
ALTER TABLE shifts
  ADD COLUMN check_in_photo_path text,
  ADD COLUMN check_out_photo_path text,
  ADD COLUMN check_in_liveness_score numeric(5,4),
  ADD COLUMN check_out_liveness_score numeric(5,4),
  ADD COLUMN check_in_is_mock_location boolean,
  ADD COLUMN check_out_is_mock_location boolean;
```
No new tables, no RLS changes needed (new columns on an already-RLS-covered table).

**`backend/app/core/config.py` / `docker-compose.yml`:** new `ATTENDANCE_PHOTOS_ROOT` setting + Docker volume, identical shape to `EMPLOYEE_DOCS_ROOT`. Path convention: `{ATTENDANCE_PHOTOS_ROOT}/{tenant_id}/{shift_id}/{check_in|check_out}.jpg`.

**`backend/app/core/config_keys.py`:** add `"attendance.liveness_min_score": _range_validator(0.0, 1.0)` (default `0.7` when unset, mirroring the existing `_ATTENDANCE_DEFAULTS` dict pattern in `shifts.py`).

**Refactor `backend/app/services/face.py` (new, extracted from `watchlist.py`):** move `_face_app_lock`/`_load_face_app()` here unchanged (it's a generic "load the shared face model" singleton, not enrollment-specific) — `watchlist.py` imports it instead of defining it locally. This is a minimal, justified extraction: liveness becomes the second caller of the exact same singleton, which is this codebase's established threshold for sharing over duplicating (see `GUARD_ROLES`/`WARNING_LABELS` module-level pattern elsewhere — duplicated at 1 caller, shared once a real 2nd caller exists).

**New `backend/app/services/liveness.py`:**
```python
def check_liveness_sync(image_bytes: bytes) -> float:
    """Detect the largest face (reusing services/face.py's singleton), crop
    with padding, run the MiniFASNetV2-SE ONNX classifier, return a 0.0-1.0
    'real face' confidence score. Raises ValueError if no face is found —
    same error-shape convention as face.py's embedding extraction."""
```
Loads `backend/models/liveness_minifasnet.onnx` via a second lazy-loaded `onnxruntime.InferenceSession` singleton (same lock pattern as `_load_face_app`); reads the model's actual expected input shape from `session.get_inputs()[0].shape` rather than hardcoding it, so whichever exact quantized variant gets sourced just works.

**`backend/app/routers/shifts.py` — `start_shift`/`end_shift` extended:**
- New params: `photo: UploadFile = File(...)` (required), `is_mock_location: bool = False` (query, mobile-reported).
- Flow, in order: (1) `is_mock_location=True` → `403` immediately, no DB writes, no liveness/photo work wasted. (2) Validate `photo.content_type` is JPEG/PNG (mirrors `enroll_face`'s check). (3) `await asyncio.to_thread(check_liveness_sync, image_bytes)` — same async-wrapping pattern as `enroll_face`. Score below `attendance.liveness_min_score` → `422` with a clear message, nothing written. (4) On pass: write the photo to disk, then include `check_in_photo_path`/`check_in_liveness_score`/`check_in_is_mock_location` (or the `check_out_*` equivalents) in the existing `UPDATE shifts SET ...` alongside the current lat/lon/late/geofence logic — no separate query needed, same transaction.
- New `GET /shifts/{shift_id}/photo/{which}` (`which: Literal["check_in","check_out"]`, permission `attendance:read`) — resolves the stored path under `ATTENDANCE_PHOTOS_ROOT`, `FileResponse`, `404` if the row or on-disk file is missing. Mirrors `evidence.py`'s file-serving endpoint exactly.

**`backend/app/routers/sites.py` — close the real gap:**
- `SiteCreate`/`SiteUpdate` gain `geofence_radius_meters: int | None = None`.
- `list_sites`/`get_site`/`create_site`/`update_site` SELECT/INSERT/UPDATE column lists all include it (same mechanical pattern as the existing lat/lon fields right next to it — no new logic, just the column that was silently missing).

## Frontend — web

**`frontend/src/pages/Sites.tsx`:** add a "Geofence Radius (m)" numeric field to the create/edit dialog next to the existing lat/lon fields. Sites table gains a small warning `Chip` ("Geofence not configured") on any row missing lat, lon, or radius — computed client-side from the already-fetched row, no new API call.

**`frontend/src/pages/Attendance.tsx`:** each guard row in the site-grouped live list gains a small circular photo thumbnail (check-in on the left, check-out on the right once present) sourced from the new `GET /shifts/{id}/photo/{which}` endpoint; click to enlarge in a simple `Dialog`. This is the direct answer to "check in and check out photos should be comes to server" — the live monitor is where an admin actually looks at them.

**`frontend/src/api/attendance.ts`** and **`frontend/src/api/sites.ts`**: extend types (`LiveAttendanceShift` gains the new photo/liveness/mock fields; `Site` gains `geofence_radius_meters`) and the create/update call signatures.

## Frontend — mobile

**New `mobile/src/screens/CheckInPhotoScreen.tsx`** (or an in-place modal on `ShiftScreen.tsx` — decided during implementation based on nav-stack shape): front-facing `CameraView` + `takePictureAsync`, capture/retake buttons, mirrors the existing QR-scanner screens' visual style. Flow:
1. Guard taps Start/End on `ShiftScreen.tsx`.
2. Call `tryGetCoords()` (already exists) — if `pos.coords.mocked === true` (Android only; `undefined` on iOS treated as `false`), show a blocking alert ("Fake GPS detected — disable mock location apps to check in") and stop, never opening the camera.
3. Otherwise open the camera screen, guard takes the selfie, confirms.
4. Upload photo (multipart) + lat/lon + `is_mock_location` to the extended `startShift`/`endShift` calls.
5. On `422` (liveness failed): show a retake prompt, loop back to step 3. On `403` (mock location, server-side catch): same blocking alert as step 2.

**`mobile/src/api/patrols.ts`:** `startShift`/`endShift` gain a `photoUri: string` param (required) and `isMockLocation: boolean` param, building a `FormData` multipart request instead of the current plain query-param call.

## Verification

1. Migration `0064` applies cleanly; `\d shifts` shows the 6 new columns.
2. Backend model prerequisite: `backend/models/liveness_minifasnet.onnx` sourced and present before any liveness test — confirm `services/liveness.py`'s lazy singleton loads it without error on first call.
3. As a guard: start a shift with a real selfie → `200`, `check_in_photo_path` set, `check_in_liveness_score` above threshold; retry holding a photo of a photo up to the camera → `422`, no shift-state change.
4. Toggle a mock-location app on a test Android device (or send `is_mock_location=true` directly via curl against the live endpoint) → `403` on both the client-side pre-check and the server-side independent check.
5. `GET /shifts/{id}/photo/check_in` returns the correct JPEG; `Attendance.tsx` shows the thumbnail in the live list.
6. Sites page: create a site with no lat/lon → "Geofence not configured" chip appears; fill in lat/lon/radius → chip disappears; confirm `geofence_radius_meters` round-trips correctly via `GET /sites/{id}`.
7. `npx tsc --noEmit` clean (frontend + mobile); `docker compose build api frontend` clean; live-verify via the established frontend-dev-on-5174 workflow plus a real mobile check-in against the live API.

---

# Round: Violations Module

## Context

Next item in the confirmed ShiftSecure build order (Employee → Attendance/Roster
→ **Violations** → Leave Management → Payroll/CPF/IR8A → SOP Training →
Billing). Confirmed via grep across `backend/app` for
`no_show|demerit|violation_type|violations_table` that this is a genuine
zero-code gap — nothing today auto-flags a no-show/late/geofence-failure
guard or gives a supervisor a points-based view of repeat problems.

**What already exists that this round builds on (confirmed by direct
reading, not assumed):** `shifts` already computes and stores `is_late`,
`late_minutes`, `is_within_geofence`, `overtime_minutes` at check-in/out
time (`backend/app/routers/shifts.py:438-533`, from Phase 2A) — the
late/geofence auto-detection signals already exist, they're just not
surfaced as violations anywhere. `scheduler_main.py::check_visitor_overstays`
(lines 211-289) is the established template for a new tenant-iterating
scheduled job: `set_config('app.current_tenant', ...)` per tenant → a
dedup-safe query → INSERT → `redis.publish(f"tenant_events:{tenant_id}",
...)` → per-tenant commit. `shifts.py` already has a small per-router
realtime-publish helper (`_publish_attendance_event`, line 107) — the new
router follows the exact same shape. `compliance.py`'s
`get_compliance_dashboard` (line 68) is the closest existing analog for a
per-guard scoring view, but it scores patrol-tour compliance, not
attendance/conduct — Violations is a parallel, independent scoring axis,
not an extension of it.

**Scope decisions (pragmatic, matching this session's established
"balanced, no premature configurability" pattern — e.g. the roster
scheduler was built as greedy-with-warnings, not a CSP solver):**
- **Point values are a fixed Python constant**, not a new tenant-configurable
  setting. `config_keys.py`'s `SETTING_VALIDATORS` only validates scalars
  (range/int/bool); a per-type points dict doesn't fit that shape without
  inventing a new validator kind for a single feature. Easy to make
  configurable later if asked.
- **No escalation/suspension workflow.** This phase surfaces points and lets
  a supervisor acknowledge/dispute/waive a violation — actually acting on
  a high point total (warning letters, termination) is an HR process
  outside this system's scope, same boundary Payroll/IR8A will respect
  later.
- **Auto-detection covers no-show, late check-in, geofence failure, early
  departure.** Manual entries (uniform, conduct, etc.) cover everything
  else via a free-text admin-logged violation — no attempt to enumerate
  every possible manual violation "type."
- **Mobile:** not touched this round. This is primarily a
  supervisor/admin review tool; a guard's own violations are visible via
  the existing web-only pattern (mirrors how Roster/Violations-adjacent
  admin tools have stayed web/desktop-only). A "My Violations" mobile view
  is a cheap, natural future add, not built now.

## Backend

**Migration `0065_violations.py`** (down_revision `0064`):
```sql
CREATE TABLE violations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    guard_user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shift_id            UUID REFERENCES shifts(id) ON DELETE SET NULL,
    site_id             UUID REFERENCES sites(id) ON DELETE SET NULL,
    violation_type      VARCHAR(30) NOT NULL
        CHECK (violation_type IN ('no_show','late_checkin','geofence_failure','early_departure','manual')),
    description         TEXT,
    points              INTEGER NOT NULL DEFAULT 0,
    status              VARCHAR(20) NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','acknowledged','disputed','waived')),
    is_auto_generated   BOOLEAN NOT NULL DEFAULT FALSE,
    reported_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,  -- NULL for auto-generated
    reviewed_by_user_id UUID REFERENCES users(id),
    reviewed_at         TIMESTAMPTZ,
    review_notes        TEXT,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- standard RLS triplet (ENABLE/FORCE/USING/WITH CHECK on tenant_id, same as every other tenant table)
CREATE INDEX idx_violations_tenant_guard ON violations(tenant_id, guard_user_id);
CREATE INDEX idx_violations_tenant_status ON violations(tenant_id, status);
CREATE INDEX idx_violations_shift ON violations(shift_id);

INSERT INTO permissions (code, description, category) VALUES
  ('violation:read',   'View violations and guard points summary', 'guard'),
  ('violation:manage', 'Log manual violations and review/waive existing ones', 'guard')
ON CONFLICT (code) DO NOTHING;
-- Grants mirror existing patterns exactly:
-- violation:read   -> roles 1,2,3,4,5,6,8 (mirrors attendance:read's broad grant — everyone sees at least their own)
-- violation:manage -> roles 1,2,3,4,8 (mirrors shift:manage's exact grantee set)
```

**New `backend/app/services/violations.py`** — shared logic used by both the
inline shift-lifecycle hooks and the new scheduled job, so the INSERT +
realtime-publish shape lives in exactly one place (mirrors the
`geofence.py`/`roster_autoschedule.py` service-module convention already
established):
```python
VIOLATION_POINTS = {
    "no_show": 10,
    "late_checkin": 3,
    "geofence_failure": 2,
    "early_departure": 5,
    "manual": 0,  # admin sets custom points explicitly on manual entries
}

async def create_violation(
    db, tenant_id, guard_user_id, violation_type, *,
    shift_id=None, site_id=None, description=None, points=None,
    is_auto_generated=False, reported_by_user_id=None,
) -> str | None:
    """Inserts a violations row. Auto-generated types are deduped against an
    existing open violation of the same type for the same shift (prevents a
    guard's single late check-in from producing duplicate rows if this is
    ever called from more than one path). Returns the new violation_id, or
    None if deduped."""
```

**`backend/app/routers/shifts.py` — inline auto-detection hooks:**
- In `start_shift`, right after the existing `is_late`/`late_minutes` and
  `is_within_geofence` computation (lines ~438-453, same transaction,
  before the `UPDATE shifts` commit): if `is_late` → `create_violation(...,
  "late_checkin", points=VIOLATION_POINTS["late_checkin"],
  is_auto_generated=True)`. If `is_within_geofence is False` → same for
  `"geofence_failure"`. Both fire-and-forget style (log, don't raise, on
  any error — a violation-logging failure must never block a real
  check-in, same principle as `_publish_attendance_event`'s try/except).
- In `end_shift`, after the existing `overtime_minutes` computation
  (~line 512-517): if the shift ended more than
  `attendance.late_grace_minutes` (reuse the existing tenant setting, no
  new one needed) *before* `scheduled_end` → `"early_departure"`.
- Both call the existing `_publish_attendance_event`-style helper, extended
  with a new `event_type="violation_created"` publish right after a
  successful `create_violation` call.

**New `backend/app/scheduler_main.py::check_no_show_shifts`** — mirrors
`check_visitor_overstays` exactly (same tenant-iteration/dedup/publish/
commit shape): for shifts where `status = 'scheduled'` and
`scheduled_start < now() - INTERVAL 'X minutes'` (threshold = the existing
`attendance.late_grace_minutes` setting, reused again rather than adding a
5th tenant-setting for a 4th purpose) and no existing violation of type
`no_show` already exists for that `shift_id` → `create_violation(...,
"no_show", points=VIOLATION_POINTS["no_show"], is_auto_generated=True)`.
Registered in the scheduler's existing job-iteration loop alongside
`check_visitor_overstays`/`check_camera_offline_alerts`.

**New `backend/app/routers/violations.py`** (prefix `/api/v1/violations`,
registered in `main.py`):
- `GET /` (`violation:read`) — list, filters: `guard_user_id`, `site_id`,
  `violation_type`, `status`. Guards (roles 4,5) are restricted to
  `WHERE guard_user_id = :current_user_id` regardless of filters passed —
  same restriction shape already used for attendance corrections
  (`attendance.py`'s guard-scoping on `GET /corrections`).
- `POST /` (`violation:manage`) — manual entry: `{guard_user_id,
  violation_type: "manual", description, points, shift_id?, site_id?}`.
  Sets `reported_by_user_id = current_user.id`, `is_auto_generated=False`.
- `PUT /{id}/review` (`violation:manage`) — body `{status: "acknowledged"|
  "disputed"|"waived", review_notes?}`, sets `reviewed_by_user_id`,
  `reviewed_at`. Waiving does not delete the row (audit trail stays
  intact) — it just zeroes its contribution to the summary endpoint below
  by excluding `status='waived'` rows from the points sum.
- `GET /summary` (`violation:read`) — per-guard point totals over a
  rolling window (default 90 days, `?days=` override), excluding waived
  rows: `guard_user_id, guard_name, total_points, violation_count,
  last_violation_at`, ordered by `total_points DESC`. This is what feeds
  the frontend's leaderboard/KPI cards. Guards (4,5) get only their own row
  back (list of one).

## Frontend

**`frontend/src/api/violations.ts`** (new) — `listViolations(filters)`,
`createViolation(data)`, `reviewViolation(id, status, notes?)`,
`getViolationsSummary(days?)`.

**`frontend/src/hooks/useRealtimeEvents.ts`**: add
`case 'violation_created': queryClient.invalidateQueries({ queryKey:
['violations'] }); queryClient.invalidateQueries({ queryKey:
['violations-summary'] }); break` — same one-case-per-event-type pattern
already used for `attendance_status_changed`/`roster_published`.

**`frontend/src/hooks/usePermission.ts`**: add `'violation:read',
'violation:manage'` to `ALL_PERMISSIONS`; roles 1/2/8 inherit automatically
via the existing `ALL_PERMISSIONS`/`ALL_PERMISSIONS.filter(...)` grants;
add `'violation:read', 'violation:manage'` to role 3's and role 4's
explicit lists (mirroring where `shift:manage` already appears in each);
add `'violation:read'` only to role 5's and role 6's explicit lists.

**New `frontend/src/pages/Violations.tsx`** — reuses this session's
established motion/glass toolkit exactly as `Attendance.tsx` did:
- `PageHeader` title "Violations", subtitle "Attendance and conduct
  tracking across all guards".
- KPI row: `GlassCard`s with `useCountUp` (Open Violations / This Month's
  Points Issued / Guards Flagged / Auto-Detected vs Manual ratio) — same
  staggered `fadeUpSx` entrance as `Attendance.tsx`'s KPI row.
- Points leaderboard: a compact table/list from `GET /summary`, guard name
  + total points + a severity-tinted `Chip` (e.g. green under 10, amber
  10-25, red 25+ — thresholds are a display-only convenience, not a new
  backend concept).
- Violations list: filterable table (site, guard, type, status), each row
  showing a `SeverityChip`-style type badge, auto vs manual indicator,
  points, status. Click opens a review dialog (Acknowledge / Dispute /
  Waive + notes) — `PermissionGuard permission="violation:manage"` gates
  the action buttons, matching the existing `attendance:manage` gating
  pattern on `Attendance.tsx`'s corrections section.
- "Log Violation" button (gated `violation:manage`) opens a dialog: guard
  picker (`getUsers` filtered to `GUARD_ROLES`, same set already defined
  in `Compliance.tsx`/`Roster.tsx`), type (defaults to "manual"), points,
  description, optional site/shift.
- Data via `useQuery(['violations', filters], ...)` and
  `useQuery(['violations-summary'], ...)`; freshness from the WS
  invalidation above, not polling — same convention as `Attendance.tsx`.

**`frontend/src/App.tsx`**: `<Route path="violations"
element={<ViolationsPage />} />`. **`Sidebar.tsx`**: new "Violations" nav
item near Attendance, gated `violation:read`, reusing an appropriate
existing MUI icon (e.g. `WarningAmberIcon`, not a new asset).

## Verification

1. Migration `0065` applies cleanly; `\d violations` shows RLS enabled;
   `SELECT * FROM permissions WHERE code LIKE 'violation:%'` shows both
   codes with correct grants.
2. Start a shift late (past `attendance.late_grace_minutes`) → a
   `late_checkin` violation appears with the correct point value;
   check in outside a site's geofence radius → a `geofence_failure`
   violation appears; end a shift well before `scheduled_end` → an
   `early_departure` violation appears.
3. Leave a `scheduled` shift unstarted past the no-show threshold →
   confirm the scheduler job (manually invoke or wait for its interval)
   creates exactly one `no_show` violation, and re-running the job doesn't
   duplicate it.
4. Log a manual violation as a supervisor → appears in the list with
   `is_auto_generated=false` and the correct `reported_by_user_id`; a
   security_guard attempting `POST /violations` → `403`.
5. `GET /summary` totals correctly exclude a `waived` violation's points
   but still list it (non-deleted) in the full violations list.
6. Open Violations page in two browser tabs; trigger an auto-violation in
   one (e.g. a late check-in) → the other tab's KPI counts and list update
   within ~1s via WebSocket.
7. As a security_guard user, `GET /violations` and the Violations page
   show only that guard's own rows.
8. `npx tsc --noEmit` clean; `docker compose build api frontend` clean;
   live-verify via the established frontend-dev-on-5174-against-real-Docker
   workflow (aegis / ops@aegis.demo / Demo1234!).

---

# Round: Leave Management Module

## Context

Next item in the confirmed ShiftSecure build order (Employee → Attendance/
Roster → Violations → **Leave Management** → Payroll/CPF/IR8A → SOP
Training → Billing). Confirmed via direct reading that `guard_leave_blocks`
(added in migration `0062`, `backend/app/routers/roster.py:204-269`) is —
exactly as documented in its own design note — a deliberately minimal
date-range table (`guard_user_id, start_date, end_date, reason`) with **no
leave type, no entitlement/balance, no guard-submitted request, no
approval workflow**. Today any admin/supervisor directly creates a block
via `POST /roster/leave-blocks`; there is no guard-facing way to request
leave at all. This is the real gap Leave Management closes.

**What already exists that this round builds on:**
- `roster_autoschedule.py::is_guard_on_leave` (line 38) and
  `generate_draft` (line 119) read `guard_leave_blocks(guard_user_id,
  start_date, end_date)` as a flat availability signal — this read
  contract stays **unchanged**. Per `0062`'s own design note ("Phase 4
  becomes the writer of a richer superset later"), this round makes
  `guard_leave_blocks` a *derived* table: rows are written automatically
  when a leave request is approved (and removed if later cancelled),
  rather than created directly by an admin bypassing the request/approval
  flow. The scheduler needs zero changes.
- `attendance.py`'s correction-request workflow
  (`backend/app/routers/attendance.py`) is the closest existing analog —
  guard submits, admin/supervisor approves/rejects — and this round's
  permission grants (`leave:read`/`leave:request`/`leave:manage`) mirror
  `attendance:read`/`attendance:request`/`attendance:manage`'s exact role
  grants from migration `0061` line-for-line.
- `tenants.py::create_tenant` (line 59) already calls
  `seed_tenant_licenses(db, tenant_id)` right before `commit()` — this
  round adds one more call in the same spot, `seed_default_leave_types`,
  mirroring `licenses.py::seed_tenant_licenses`'s exact
  `INSERT ... ON CONFLICT DO NOTHING` shape.
- `users.py`'s `employee_documents` sub-resource (multipart upload,
  `EMPLOYEE_DOCS_ROOT`-based storage, decoupled from the main
  create-user flow) is the template for optional medical-certificate
  attachments on a leave request — reused as a separate follow-up
  endpoint, not baked into the request-creation body.

**Scope decisions (mirrors this session's established "balanced, no
premature complexity" pattern):**
- **Balance is computed on read, not maintained as a running total** —
  same principle as Violations' points summary (`SUM` over
  `leave_requests` at query time, never a stored/incrementable counter),
  so a later cancellation never needs a backfill.
- **No hard block on over-allocation.** A request can be submitted or
  approved even if it would exceed the guard's remaining balance — the
  balance is surfaced to the approver as context, not enforced as a gate.
  Matches the "flag, don't block" precedent from geofence/roster coverage.
- **`requires_document` is advisory, not enforced.** Since the document
  attaches via a separate follow-up call (mirroring `employee_documents`),
  there's no single request-creation instant to gate on file presence;
  the UI surfaces a reminder instead.
- **No cross-page dialog reuse for affected-shift reassignment.** Approval
  returns `affected_shifts` (mirroring `create_leave_block`'s existing
  behavior), and the approval dialog lists them as a warning banner
  pointing the admin to Roster — it does not open `Roster.tsx`'s local
  `EditShiftDialog` inline, since that component isn't exported as a
  shared one and extracting it is out of scope for this round.
- **Balances view requires picking a guard** (defaults to self for
  guards) rather than an eagerly-loaded all-guards×all-types grid — avoids
  a wide cross-join on page load for tenants with many guards/types.

## Backend

**Migration `0066_leave_management.py`** (down_revision `0065`):
```sql
CREATE TABLE leave_types (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                 VARCHAR(100) NOT NULL,
    default_annual_days  INTEGER NOT NULL DEFAULT 0,
    requires_document    BOOLEAN NOT NULL DEFAULT FALSE,
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);
-- standard RLS triplet

CREATE TABLE leave_balances (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    guard_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    leave_type_id  UUID NOT NULL REFERENCES leave_types(id) ON DELETE CASCADE,
    year           INTEGER NOT NULL,
    entitled_days  INTEGER NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guard_user_id, leave_type_id, year)
);
-- standard RLS triplet; a missing row for a guard+type+year falls back to
-- leave_types.default_annual_days as the effective entitlement — same
-- env-var-fallback convention used throughout (tenant_settings, etc.)

CREATE TABLE leave_requests (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    guard_user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    leave_type_id        UUID NOT NULL REFERENCES leave_types(id) ON DELETE RESTRICT,
    start_date           DATE NOT NULL,
    end_date             DATE NOT NULL CHECK (end_date >= start_date),
    days_count           INTEGER NOT NULL,
    reason               TEXT,
    document_path        TEXT,
    status               VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','cancelled')),
    reviewed_by_user_id  UUID REFERENCES users(id),
    reviewed_at          TIMESTAMPTZ,
    review_notes         TEXT,
    created_by_user_id   UUID REFERENCES users(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- standard RLS triplet + idx_leave_requests_tenant_guard(tenant_id, guard_user_id),
-- idx_leave_requests_tenant_status(tenant_id, status)

ALTER TABLE guard_leave_blocks
    ADD COLUMN leave_request_id UUID REFERENCES leave_requests(id) ON DELETE CASCADE;
-- links an auto-synced block back to the approved request that created it;
-- NULL for any pre-existing directly-created blocks (unaffected, still valid)

INSERT INTO permissions (code, description, category) VALUES
  ('leave:read',    'View leave requests and balances',            'guard'),
  ('leave:request', 'Submit or cancel a leave request',             'guard'),
  ('leave:manage',  'Approve/reject requests, manage types+balances','guard')
ON CONFLICT (code) DO NOTHING;

-- Grants mirror attendance:read/request/manage from migration 0061 exactly,
-- plus an explicit role-8 (manager) grant (0063's clone only captured
-- admin's grants *at that time*, not permissions added afterward — same
-- correction applied for violation:read/manage in migration 0065).
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.id IN (1, 2, 3, 8) AND p.code IN ('leave:read', 'leave:manage', 'leave:request')
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.id IN (4, 5) AND p.code IN ('leave:read', 'leave:request')
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.id = 6 AND p.code = 'leave:read'
ON CONFLICT DO NOTHING;

-- Seed 4 default leave types for every existing tenant (new tenants get
-- these via seed_default_leave_types() at creation time, see below).
INSERT INTO leave_types (tenant_id, name, default_annual_days, requires_document)
SELECT t.id, v.name, v.days, v.doc
FROM tenants t
CROSS JOIN (VALUES
    ('Annual Leave', 14, FALSE),
    ('Medical Leave', 14, TRUE),
    ('Compassionate Leave', 3, FALSE),
    ('Unpaid Leave', 0, FALSE)
) AS v(name, days, doc)
ON CONFLICT (tenant_id, name) DO NOTHING;
```

**`backend/app/routers/tenants.py`**: add
`await seed_default_leave_types(db, tenant_id)` in `create_tenant` right
next to the existing `await seed_tenant_licenses(db, tenant_id)` call
(line 92), same transaction, same GUC already set for the RLS `WITH CHECK`
to pass.

**New `backend/app/services/leave.py`**:
```python
def compute_days_count(start_date: date, end_date: date) -> int:
    """Inclusive calendar-day count — same simplicity level as
    guard_leave_blocks' existing date-range convention; no weekend/holiday
    awareness anywhere in this codebase yet, so this doesn't add any."""
    return (end_date - start_date).days + 1

async def seed_default_leave_types(db: AsyncSession, tenant_id) -> None:
    """Mirrors licenses.py::seed_tenant_licenses exactly — INSERT ... ON
    CONFLICT DO NOTHING for the same 4 default types the migration seeds
    for existing tenants."""
```

**New `backend/app/routers/leave.py`** (prefix `/api/v1/leave`, registered
in `main.py`):

*Leave types* (admin-configurable, mirrors `zones.py`'s CRUD shape):
- `GET /types` (`leave:read`) — active types.
- `POST /types` / `PUT /types/{id}` / `DELETE /types/{id}` (`leave:manage`,
  delete = soft, `is_active=FALSE`).

*Requests:*
- `GET /requests` (`leave:read`) — filters `guard_user_id`, `leave_type_id`,
  `status`; guards (roles 4,5) forced to their own rows, same self-scoping
  shape as `violations.py::list_violations`.
- `POST /requests` (`leave:request`) — body `{guard_user_id, leave_type_id,
  start_date, end_date, reason?}`. A guard may only target their own
  `guard_user_id` (roles 1,2,3,8 may file on behalf of anyone, mirrors
  `attendance.py::request_correction`'s exact check). Rejects with `409`
  if an overlapping non-cancelled/non-rejected request already exists for
  that guard. Computes `days_count` via `compute_days_count`. Returns the
  created row plus the guard's current balance for that type/year
  (`entitled`, `used`, `remaining`) so the UI can show it immediately.
- `POST /requests/{id}/document` (`leave:request`, multipart, self-owned
  or `leave:manage`) — mirrors `users.py`'s `employee_documents` upload
  exactly: saves under `EMPLOYEE_DOCS_ROOT/{tenant_id}/{guard_user_id}/leave/{request_id}.{ext}`,
  updates `document_path`.
- `PUT /requests/{id}/approve` (`leave:manage`) — sets `status='approved'`,
  `reviewed_by_user_id`, `reviewed_at`; in the same transaction, `INSERT
  INTO guard_leave_blocks (tenant_id, guard_user_id, start_date, end_date,
  reason, created_by_user_id, leave_request_id) VALUES (...)` (the sync
  step that makes the roster scheduler see it, zero scheduler changes
  needed); looks up overlapping **published** shifts the same way
  `create_leave_block` already does (`roster.py:243-254` — same query,
  reused) and returns them as `affected_shifts`. Publishes
  `leave_status_changed`.
- `PUT /requests/{id}/reject` (`leave:manage`) — body `{review_notes?}`,
  sets `status='rejected'`. No `guard_leave_blocks` row ever existed, so
  nothing to clean up. Publishes `leave_status_changed`.
- `PUT /requests/{id}/cancel` (`leave:request` + self-check, or
  `leave:manage`) — allowed on `pending` or `approved` requests (plans
  change after approval too); sets `status='cancelled'`; if it was
  `approved`, also `DELETE FROM guard_leave_blocks WHERE leave_request_id
  = :id` in the same transaction. Publishes `leave_status_changed`.

*Balances:*
- `GET /balances?guard_user_id=&year=` (`leave:read`) — guards (4,5) forced
  to self; defaults `year` to the current year. For each active
  `leave_type`, returns `{leave_type_id, name, entitled_days, used_days,
  remaining_days}` — `entitled_days` from `leave_balances` if a row
  exists else `leave_types.default_annual_days`; `used_days` = `SUM
  (days_count) FROM leave_requests WHERE guard_user_id=... AND
  leave_type_id=... AND status='approved' AND
  EXTRACT(YEAR FROM start_date)=:year`.
- `PUT /balances` (`leave:manage`) — upsert `{guard_user_id,
  leave_type_id, year, entitled_days}` (e.g. pro-rating a new hire's first
  year) — `INSERT ... ON CONFLICT (guard_user_id, leave_type_id, year) DO
  UPDATE`.

## Frontend

**`frontend/src/api/leave.ts`** (new) — `getLeaveTypes`,
`createLeaveType`, `updateLeaveType`, `deleteLeaveType`, `listLeaveRequests(filters)`,
`createLeaveRequest(data)`, `uploadLeaveDocument(id, file)`,
`approveLeaveRequest(id)`, `rejectLeaveRequest(id, notes?)`,
`cancelLeaveRequest(id)`, `getLeaveBalances(guardUserId?, year?)`,
`setLeaveBalance(data)`.

**`frontend/src/hooks/useRealtimeEvents.ts`**: add `case
'leave_status_changed': queryClient.invalidateQueries({ queryKey:
['leave-requests'] }); queryClient.invalidateQueries({ queryKey:
['leave-balances'] }); break`.

**`frontend/src/hooks/usePermission.ts`**: add `'leave:read',
'leave:request', 'leave:manage'` to `ALL_PERMISSIONS` (roles 1/2/8
inherit); add all three to role 3's explicit list; add `'leave:read',
'leave:request'` (no manage) to roles 4 and 5's explicit lists; add
`'leave:read'` to role 6's — exact mirror of where `attendance:*` already
appears in each list.

**New `frontend/src/pages/Leave.tsx`** — tabbed page (same tabbed-page
precedent as `Watchlists.tsx`'s Plates/Faces tabs), reusing the
established `GlassCard`/`fadeUpSx`/`useCountUp`/`PageHeader`/
`PermissionGuard` toolkit:
- KPI row: Pending Requests / Approved This Month / On Leave Today /
  Guards Near Limit (guards whose `remaining_days < 2` on any type).
- **Tab: Requests** (default) — filterable list (guard, type, status);
  "Request Leave" button opens a dialog (guard picker widened to
  `GUARD_ROLES` for admins, locked to self for guards; leave-type
  `Select` showing `entitled/used/remaining` inline once picked; date
  range; reason; a follow-up "Attach document" step after creation when
  the picked type's `requires_document` is true — advisory, not blocking).
  Clicking a row opens a review dialog: Approve / Reject / Cancel, an
  `affected_shifts` warning banner on approval when non-empty ("N
  published shifts need reassignment — see Roster"), balance context
  shown inline.
- **Tab: Balances** — guard `Select` (defaults to self for guard-role
  users, hidden entirely and forced-self if the viewer lacks
  `leave:manage`), year `Select`, a small table of
  entitled/used/remaining per active leave type.
- **Tab: Leave Types** (gated `leave:manage`, hidden otherwise) — simple
  table + dialog CRUD (name, default annual days, requires-document
  toggle) — mirrors the lightweight admin-config table pattern already
  used for restricted zones before the dedicated editor existed.

**`frontend/src/App.tsx`**: `<Route path="leave" element={<LeavePage />}
/>`. **`Sidebar.tsx`**: "Leave" nav item near Attendance/Violations, gated
`leave:read`, using `EventBusyIcon` or similar from `@mui/icons-material`.

## Verification

1. Migration `0066` applies cleanly; `\d leave_requests`,
   `\d leave_balances`, `\d leave_types` show RLS enabled; `\d
   guard_leave_blocks` shows the new nullable `leave_request_id` column;
   `SELECT name, default_annual_days FROM leave_types` on the seeded
   "aegis" tenant shows the 4 defaults.
2. As a guard: submit a leave request for own account → `201`; attempt to
   submit for a different guard → `403`; submit an overlapping request →
   `409`.
3. As admin: approve the request → `guard_leave_blocks` gains a row with
   the matching `leave_request_id`; run `POST /roster/auto-schedule` over
   the same period → the guard is never assigned on those dates (confirms
   the scheduler's existing `is_guard_on_leave` read contract picked up
   the synced block with zero code changes there).
4. Cancel the now-approved request → its `guard_leave_blocks` row is
   deleted; re-run auto-schedule → the guard is assignable again.
5. `GET /balances` for that guard/type/year shows `used_days` including
   only `approved` (not `pending`/`rejected`/`cancelled`) requests;
   `remaining_days` computed correctly against the seeded
   `default_annual_days` with no `leave_balances` override row present.
6. Admin sets an explicit `leave_balances` override via `PUT /balances` →
   subsequent `GET /balances` uses the override instead of the type
   default.
7. Approve a request that overlaps an already-**published** shift →
   response includes non-empty `affected_shifts`; approve one with no
   overlap → empty array.
8. As a security_guard, `GET /requests` and `GET /balances` return only
   that guard's own rows; attempting `PUT /requests/{id}/approve` → `403`.
9. Create a new tenant via `POST /api/v1/tenants` → confirm it also has
   the 4 default `leave_types` rows (via `seed_default_leave_types`, not
   just the migration's one-time backfill).
10. Open `/leave` in two browser tabs; approve a request in one → the
    other tab's Requests list and Balances refresh within ~1s via
    WebSocket.
11. `npx tsc --noEmit` clean; `docker compose build api frontend` clean;
    live-verify via the established frontend-dev-on-5174-against-real-Docker
    workflow (aegis / ops@aegis.demo / Demo1234!).

---

# Round: Payroll / CPF / IR8A Module

## Context

Next item in the confirmed ShiftSecure build order (Employee → Attendance/
Roster → Violations → Leave → **Payroll/CPF/IR8A** → SOP Training →
Billing). Confirmed via grep across `backend/` for
`payroll|cpf|ir8a|payslip` that this is a genuine zero-code gap — the only
hit is the gap-analysis comment in migration `0060`'s own docstring.

**What already exists that this round builds on:** Phase 1's employee
profile fields (`backend/app/routers/users.py:20-31`) already carry
exactly what CPF eligibility and payroll need — `work_pass_type` (`Literal
["citizen","pr","ep","sp","wp"]`, where only `citizen`/`pr` are
CPF-eligible under Singapore rules), `date_of_birth` (CPF employee/
employer rates are age-banded), `employment_type`, `bank_name`/
`bank_account_number`. `shifts` already computes `overtime_minutes` per
shift (Phase 2A) and has `actual_start`/`actual_end` for completed shifts
— worked-hours and OT both derive from existing columns, no new
attendance tracking needed. `reports.py` already has a working
ReportLab→`StreamingResponse` PDF pattern (`backend/app/routers/
reports.py:1-33`, `reportlab>=4.2` already in `pyproject.toml`) — payslip
and IR8A PDFs reuse this exactly rather than introducing new PDF infra.
`Users.tsx`'s `UserFormDialog` already has a "Bank Details" tab (tab index
3, `Users.tsx:342-347`) — the natural place to add the two new rate
fields, not a new page.

**Scope decisions (deliberate trims — CPF/payroll is real-world-complex
enough that a v1 needs explicit, documented boundaries, matching how this
session has always traded "correct enough for a working starting point"
against "every real-world edge case"):**
- **Standard CPF rates only** — the age-banded employee/employer
  percentage table for full-rate contributors (Singapore Citizens and PRs
  from their 3rd year). The graduated first/second-year PR rates (lower,
  phased-in rates) are **not implemented** — a real payroll system needs
  per-employee PR-conversion-date tracking to get this right, which this
  round doesn't add. Documented explicitly in the service module's
  docstring and the IR8A page as a known v1 limitation.
- **Ordinary Wage (OW) ceiling only, no Additional Wage (AW) ceiling.**
  CPF is capped per-month against the OW ceiling (a fixed constant, like
  `VIOLATION_POINTS`); the AW ceiling is a once-a-year cumulative
  calculation against bonus-type payments, which this system has no
  concept of (no bonus/commission fields anywhere) — moot until one
  exists.
- **Breaks are not deducted from paid hours.** Unlike office work,
  security-guard shifts are typically paid through breaks (guard remains
  on-site/on-duty); this matches the industry norm better than treating
  `shift_breaks` as unpaid time, and avoids inventing a paid/unpaid break
  distinction nothing in this codebase has today.
- **Rate configuration is a flat `hourly_rate` or `monthly_salary` on
  `users`**, mirroring exactly how `bank_name`/`bank_account_number` were
  added directly to the table in Phase 1 rather than a side table — same
  precedent, same reasoning (a handful of scalar per-employee fields).
- **A guard with no rate configured is skipped with a warning**, not a
  run-blocking error — matches the "flag, don't block" precedent (roster
  coverage shortfalls, geofence failures).
- **No violations→pay deduction.** Violations' own scope note already
  drew this boundary ("acting on a high point total... is an HR process
  outside this system's scope") — payroll doesn't reach into `violations`
  for automatic deductions.

## Backend

**Migration `0067_payroll.py`** (down_revision `0066`):
```sql
ALTER TABLE users
  ADD COLUMN hourly_rate NUMERIC(8,2),
  ADD COLUMN monthly_salary NUMERIC(10,2);
-- both nullable; a payroll run treats monthly_salary as authoritative
-- when set (fixed-salary staff), else falls back to hourly_rate × hours.

CREATE TABLE payroll_runs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    period_start         DATE NOT NULL,
    period_end           DATE NOT NULL,
    status               VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','finalized')),
    generated_by_user_id UUID REFERENCES users(id),
    generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalized_at         TIMESTAMPTZ,
    UNIQUE (tenant_id, period_start, period_end)
);
-- standard RLS triplet

CREATE TABLE payslips (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    payroll_run_id    UUID NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    guard_user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    regular_hours     NUMERIC(6,2) NOT NULL DEFAULT 0,
    overtime_hours    NUMERIC(6,2) NOT NULL DEFAULT 0,
    base_pay          NUMERIC(10,2) NOT NULL DEFAULT 0,
    overtime_pay      NUMERIC(10,2) NOT NULL DEFAULT 0,
    gross_pay         NUMERIC(10,2) NOT NULL DEFAULT 0,
    cpf_employee      NUMERIC(10,2) NOT NULL DEFAULT 0,
    cpf_employer      NUMERIC(10,2) NOT NULL DEFAULT 0,
    net_pay           NUMERIC(10,2) NOT NULL DEFAULT 0,
    unpaid_leave_days NUMERIC(5,2) NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (payroll_run_id, guard_user_id)
);
-- standard RLS triplet + idx_payslips_run(payroll_run_id), idx_payslips_guard(tenant_id, guard_user_id)

INSERT INTO permissions (code, description, category) VALUES
  ('payroll:read',   'View own payslips; admins view all',              'guard'),
  ('payroll:manage', 'Run payroll, finalize runs, generate IR8A',        'guard')
ON CONFLICT (code) DO NOTHING;

-- payroll:manage deliberately excludes supervisor(3)/operator(4) — real
-- money + tax data, tighter than attendance/leave/violations:manage.
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.id IN (1, 2, 8) AND p.code IN ('payroll:read', 'payroll:manage')
ON CONFLICT DO NOTHING;

-- Everyone else: read-only, self-scoped by the router (a guard sees only
-- their own payslips, same self-scoping shape as violations/leave).
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.id IN (3, 4, 5, 6) AND p.code = 'payroll:read'
ON CONFLICT DO NOTHING;
```

**`backend/app/routers/users.py`**: add `hourly_rate: float | None = None`,
`monthly_salary: float | None = None` to `UserUpdate` and to
`_EMPLOYEE_FIELDS`/the SELECT column lists — same mechanical pattern as
every other Phase-1 field (no new logic, the existing dynamic-`SET`
update path already handles it once the field names are registered).

**New `backend/app/services/payroll.py`**:
```python
from decimal import Decimal, ROUND_HALF_UP

CPF_OW_CEILING = Decimal("7400")  # monthly, Singapore 2026 rate — fixed
                                    # constant, same VIOLATION_POINTS-style
                                    # "not tenant-configurable" precedent
OT_MULTIPLIER = Decimal("1.5")     # standard MOM Singapore OT rate

# (min_age_inclusive, max_age_exclusive, employee_rate, employer_rate) —
# full local rates (Citizens / PRs from 3rd year). Graduated 1st/2nd-year
# PR rates NOT implemented — documented v1 limitation.
CPF_RATE_TABLE = [
    (0, 55, Decimal("0.20"), Decimal("0.17")),
    (55, 60, Decimal("0.15"), Decimal("0.145")),
    (60, 65, Decimal("0.075"), Decimal("0.11")),
    (65, 70, Decimal("0.05"), Decimal("0.085")),
    (70, 200, Decimal("0.05"), Decimal("0.075")),
]

def is_cpf_eligible(work_pass_type: str | None) -> bool:
    return work_pass_type in ("citizen", "pr")

def compute_age(date_of_birth: date, as_of: date) -> int: ...

def compute_cpf(ordinary_wage: Decimal, age: int, work_pass_type: str | None) -> tuple[Decimal, Decimal]:
    """Returns (employee_contribution, employer_contribution), both 0 if
    not CPF-eligible. Wage capped at CPF_OW_CEILING before rate lookup."""

def compute_payslip(
    regular_hours: Decimal, overtime_hours: Decimal,
    hourly_rate: Decimal | None, monthly_salary: Decimal | None,
    age: int, work_pass_type: str | None,
) -> dict:
    """Pure function: base_pay/overtime_pay/gross_pay/cpf_employee/
    cpf_employer/net_pay. monthly_salary wins over hourly_rate when both
    are set (fixed-salary staff don't accrue hourly OT pay differently —
    kept simple: OT still paid hourly against hourly_rate if present,
    else OT is unpaid for pure-salary staff, a documented v1 choice)."""
```

**New `backend/app/routers/payroll.py`** (prefix `/api/v1/payroll`,
registered in `main.py`):
- `POST /runs` (`payroll:manage`) — body `{period_start, period_end}`.
  For each active user with `role_id` in `(3,4,5,8)`: sums `shifts` where
  `status='completed'` and `actual_start::date` falls in the period
  (`SUM(EXTRACT(EPOCH FROM actual_end-actual_start)/3600)` for total
  hours, `SUM(overtime_minutes)/60` for OT hours, regular = total − OT);
  looks up `age` from `date_of_birth` (as of `period_end`) and
  `work_pass_type`; counts approved `leave_requests` of type "Unpaid
  Leave" overlapping the period for the informational
  `unpaid_leave_days` field; calls `compute_payslip(...)`; inserts one
  `payslips` row per guard. Guards with neither `hourly_rate` nor
  `monthly_salary` set are skipped and listed in a `warnings` array in
  the response (not a run-blocking failure). Returns the created
  `payroll_runs` row + all payslips + warnings.
- `GET /runs` (`payroll:read`, admin-only in practice since guards have
  no reason to list runs — router doesn't self-scope this one, it's a
  run-level listing not guard-level) — list runs, newest first.
- `GET /runs/{id}` (`payroll:read`) — run detail + payslips joined to
  guard name; guards (roles 3,4,5) see only their own payslip row within
  the run.
- `PUT /runs/{id}/finalize` (`payroll:manage`) — `status='draft' →
  'finalized'`, sets `finalized_at`; `404` if already finalized or not
  found. Finalized runs are immutable (no edit/regenerate endpoint —
  discard and re-run if a correction is needed pre-finalization, matches
  `roster_batches`' draft/discard precedent).
- `GET /payslips/{id}/pdf` (`payroll:read`, self-or-manage) — single
  payslip PDF via the `reports.py` ReportLab pattern (brand header, guard
  name, period, hours breakdown, gross/CPF/net table).
- `GET /ir8a?year=` (`payroll:manage`) — per-guard annual summary:
  `SUM(gross_pay)`, `SUM(cpf_employer)` across all **finalized** payslips
  whose run's `period_start` falls in the given calendar year, grouped by
  guard. This is the IR8A "total employment income for the year" figure
  Singapore employers file with IRAS.
- `GET /ir8a/pdf?year=` (`payroll:manage`) — the same data as a
  ReportLab PDF table (one row per guard: name, NRIC/FIN, annual gross,
  annual employer CPF), mirroring `reports.py`'s DOB-report table pattern
  exactly.

## Frontend

**`frontend/src/api/payroll.ts`** (new) — `createPayrollRun(data)`,
`listPayrollRuns()`, `getPayrollRun(id)`, `finalizePayrollRun(id)`,
`payslipPdfUrl(id, token)` (query-param-JWT URL, mirrors
`checkinPhotoUrl`'s pattern in `api/attendance.ts` since a PDF download
link can't carry an Authorization header either), `getIr8aSummary(year)`,
`ir8aPdfUrl(year, token)`.

**`frontend/src/hooks/usePermission.ts`**: add `'payroll:read',
'payroll:manage'` to `ALL_PERMISSIONS` (roles 1/2/8 inherit); add
`'payroll:read'` to roles 3, 4, 5, 6's explicit lists (no `manage` on
any of them, mirroring the migration's tighter grant).

**`frontend/src/pages/Users.tsx`**: add `hourlyRate`/`monthlySalary`
state (mirrors `bankName`/`bankAccount`'s exact pattern) and two
`TextField`s (`type="number"`) to the existing "Bank Details" tab body
(`tab === 3` block, `Users.tsx:342-347`) — renamed in the UI to "Bank &
Pay Details" — plus include both fields in `handleSubmit`'s update
payload.

**New `frontend/src/pages/Payroll.tsx`** — tabbed (Runs / IR8A), same
`GlassCard`/`Tabs`/`fadeUpSx`/`useCountUp`/`PageHeader`/`PermissionGuard`
toolkit as Violations/Leave:
- KPI row: This Run's Gross Payroll / Total CPF (Employee + Employer) /
  Guards Paid / Guards Skipped (no rate configured).
- **Tab: Runs** — "New Payroll Run" button (gated `payroll:manage`)
  opens a period-picker dialog → `createPayrollRun` → navigates straight
  into the created run's detail view. Run detail: payslip table (guard,
  regular/OT hours, gross, CPF employee/employer, net), a warnings
  banner listing guards skipped for missing rate config, "Download
  Payslip" per row (`payslipPdfUrl`), "Finalize Run" button (gated
  `payroll:manage`, disabled once already finalized). A non-manager
  guard visiting this tab sees only their own payslip rows across runs
  (client-side filtered from `GET /runs/{id}` since the backend already
  self-scopes the response).
- **Tab: IR8A** (gated `payroll:manage`, hidden otherwise — this is
  employer tax filing, not guard-facing) — year `Select`, table of
  guard/annual-gross/annual-employer-CPF, "Download IR8A PDF" button.

**`frontend/src/App.tsx`**: `<Route path="payroll" element={<PayrollPage
/>} />`. **`Sidebar.tsx`**: "Payroll" nav item near Leave, gated
`payroll:read`, using `PaymentsIcon` or `AccountBalanceWalletIcon` from
`@mui/icons-material`.

## Verification

1. Migration `0067` applies cleanly; `\d payroll_runs`, `\d payslips` show
   RLS enabled; `\d users` shows the 2 new nullable columns.
2. Set `hourly_rate` on 2-3 seeded guards via `PUT /users/{id}` (or the
   Users.tsx Bank & Pay Details tab); leave one guard's rate unset.
3. `POST /runs` for a period covering completed shifts → payslips created
   for the rated guards with correct `regular_hours`/`overtime_hours`
   (cross-check against `SUM` over `shifts.overtime_minutes` directly);
   the unrated guard appears in the response's `warnings`, not as a
   payslip row.
4. CPF spot-check: a Citizen/PR guard's `cpf_employee`/`cpf_employer` on
   their payslip match `compute_cpf(gross_pay_capped_at_7400, age,
   work_pass_type)` computed by hand against the rate table; an EP/SP/WP
   guard's payslip shows `cpf_employee=0, cpf_employer=0`.
5. `PUT /runs/{id}/finalize` → status becomes `finalized`; re-finalizing
   → `404`.
6. `GET /payslips/{id}/pdf` returns a valid PDF; as the guard who owns
   that payslip (not payroll:manage) → `200`; as a different guard → `403`.
7. `GET /ir8a?year=2026` after finalizing at least one run in that year →
   correct per-guard annual gross/CPF totals, excluding any draft
   (non-finalized) run's payslips from the sum; `GET /ir8a/pdf?year=2026`
   returns a valid PDF.
8. As a security_guard, `GET /runs` and viewing a run detail show only
   their own payslip; `POST /runs` and `PUT .../finalize` → `403`.
9. `npx tsc --noEmit` clean; `docker compose build api frontend` clean;
   live-verify via the established frontend-dev-on-5174-against-real-Docker
   workflow (aegis / ops@aegis.demo / Demo1234!).

---

# Round: SOP Training Rebuild — Real Quiz Engine

## Context

Last item before Billing in the confirmed ShiftSecure build order (Employee
→ Attendance/Roster → Violations → Leave → Payroll/CPF/IR8A → **SOP
Training** → Billing). Confirmed by reading `backend/app/routers/
training.py` (migration `0031`) in full: `training_courses` is metadata
only (name/category/passing_score — no actual content), `training_records`
is written **exclusively by an admin manually asserting** "guard X
completed course Y, scored Z" via `POST /training/records` — there is no
question bank, no guard-facing quiz UI, no automated grading anywhere.
This is exactly the gap the memory's gap-analysis names: "existing
Training.tsx/training.py is a manual log/cert-tracker, no quiz engine."

**Real, pre-existing bug found and in scope for this round:**
`frontend/src/hooks/usePermission.ts`'s `ALL_PERMISSIONS` array **never
includes `training:read`/`training:write`/`training:manage`** even though
all three have existed in the DB since migration `0031` with real grants
(`training:read` → roles 1-6,8; `training:write` → 1,2,3,8;
`training:manage` → 1,2,8 — confirmed live against the running DB, role 8
already has all three via `0063`'s admin-grant clone since `0031` predates
`0063`). Since `Sidebar.tsx`'s "Guard Training" nav item is gated
`permission: 'training:read'` (`Sidebar.tsx:100`), and `usePermission()`
checks membership in `ALL_PERMISSIONS`-derived role lists, **the Guard
Training sidebar link is currently invisible to every role, including
super_admin** — a real, currently-shipping bug this round fixes as part of
touching this exact file for the new quiz permissions anyway.

**What already exists that this round builds on:** the manual
`training_records`/`create_record` path (`training.py:183-219`) stays
**unchanged and still works** for non-quiz training (e.g. an in-person
fire drill an admin logs by hand) — this round adds a second, automated
path into the *same* table, not a replacement. The `expires_at`
auto-computation from `course.validity_months` (`training.py:197-202`)
gets extracted into a small shared helper so both the existing manual path
and the new auto-graded path compute it identically (the "share once
there's a real 2nd caller" precedent already used for `services/face.py`).

**Scope decisions:**
- **Multiple-choice only.** The simplest gradeable format, matches "SOP
  comprehension check" well, and avoids building free-text grading (which
  would need either exact-match brittleness or human review — out of
  scope). A course with zero questions stays manual-only (existing
  behavior, unchanged) — the guard UI shows "Take Quiz" only when
  `question_count > 0`.
- **Answers stored as one JSONB column on the attempt row**
  (`{question_id: selected_index}`), not a 4th `training_attempt_answers`
  table — mirrors this codebase's existing convention for small
  per-record structured data (`restricted_zones.polygon`,
  `alerts.message_params`) rather than a table that would only ever be
  queried in the context of its parent attempt.
- **No new permission codes.** `training:read` is already granted to
  every role — a guard needs exactly that to see and take a quiz, so
  quiz-taking endpoints reuse it rather than inventing `training:take`.
  Question-bank CRUD reuses `training:manage` (same boundary as course
  CRUD — course content and its questions are the same authoring
  concern).
- **Passing a quiz writes into `training_records`, not
  `guard_certifications`.** The cert table represents externally-issued
  professional licenses (security officer license, first-aid cert) — a
  different concept from internal SOP completion. Unchanged this round.
- **Web/desktop only, mobile deferred** — same call already made for
  Violations/Leave/Payroll's admin-facing surfaces this session; SOP
  quiz-taking could reasonably move to mobile later but isn't core to
  this round's scope.
- **No time limit, no anti-cheat, unlimited retakes.** This is training
  reinforcement, not a proctored exam — matches the product's existing
  register of "pragmatic, not maximal" feature choices.

## Backend

**Migration `0068_training_quiz_engine.py`** (down_revision `0067`):
```sql
CREATE TABLE training_questions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    course_id       UUID NOT NULL REFERENCES training_courses(id) ON DELETE CASCADE,
    question_text   TEXT NOT NULL,
    options         JSONB NOT NULL,   -- ["Option A", "Option B", ...]
    correct_index   INTEGER NOT NULL,
    points          INTEGER NOT NULL DEFAULT 1,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- standard RLS triplet + idx_training_questions_course(tenant_id, course_id)

CREATE TABLE training_attempts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    course_id           UUID NOT NULL REFERENCES training_courses(id) ON DELETE CASCADE,
    guard_user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status              VARCHAR(20) NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress','submitted')),
    answers             JSONB NOT NULL DEFAULT '{}',
    score               INTEGER,   -- percentage, 0-100, set on submit
    passed              BOOLEAN,   -- set on submit
    training_record_id  UUID REFERENCES training_records(id) ON DELETE SET NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at        TIMESTAMPTZ
);
-- standard RLS triplet + idx_training_attempts_guard(tenant_id, guard_user_id),
-- idx_training_attempts_course(tenant_id, course_id)
```
No new permission codes — reuses `training:read`/`training:manage`.

**New `backend/app/services/training.py`**:
```python
def compute_expiry(completed_date: date, validity_months: int | None) -> date | None:
    """Extracted from training.py::create_record's inline logic — shared
    by the manual record path and the new auto-graded quiz-submission
    path so both compute expires_at identically."""
```

**`backend/app/routers/training.py` changes** (extend in place):
- `list_courses`: add `question_count` to the SELECT via
  `(SELECT COUNT(*) FROM training_questions q WHERE q.course_id = c.id) AS question_count`.
- `create_record`: refactor its inline `expires_at` computation to call
  `compute_expiry` — behavior-neutral, verify the existing manual-record
  flow still works identically before adding anything new.
- New question-bank CRUD (`training:manage`, mirrors the existing
  `create_course`/`update_course` mechanical style):
  - `GET /courses/{course_id}/questions` — full list including
    `correct_index` (admin editing view).
  - `POST /courses/{course_id}/questions` — `{question_text, options,
    correct_index, points?, sort_order?}`; validates `correct_index` is a
    valid index into `options`.
  - `PUT /questions/{question_id}` — partial update, same dynamic-`SET`
    pattern used throughout this codebase.
  - `DELETE /questions/{question_id}`.
- New attempt endpoints (`training:read`; guard_user_id is **always**
  `token.user_id` — no "on behalf of" parameter, since only the person
  answering can meaningfully take a quiz):
  - `POST /courses/{course_id}/attempts` — validates the course is
    `is_active` and has ≥1 question (`422` otherwise); if an
    `in_progress` attempt already exists for this guard+course, returns
    that one instead of creating a duplicate (same "don't silently
    duplicate" precedent as leave-request overlap checking); otherwise
    creates a new attempt. Returns `{attempt_id, questions: [{id,
    question_text, options, points}]}` — **`correct_index` never appears
    in this response.**
  - `GET /attempts/{attempt_id}` — resume view (same shape as the create
    response, plus already-saved `answers`); `403` if the caller isn't
    the attempt's own guard and lacks `training:manage`.
  - `PUT /attempts/{attempt_id}/answer` — body `{question_id,
    selected_index}`; merges one key into the `answers` JSONB (`answers
    = answers || jsonb_build_object(:qid, :idx)`) rather than replacing
    the whole object, so answering questions out of order and resuming
    later both work; `409` if `status != 'in_progress'`; same
    self-ownership check as above.
  - `POST /attempts/{attempt_id}/submit` — grades every question for the
    course against `answers`, `score = round(100 * earned_points /
    total_points)`, `passed = score >= course.passing_score`; sets
    `status='submitted'`, `submitted_at=now()`; **on pass**, inserts a
    `training_records` row exactly like the existing manual path
    (`recorded_by_user_id = NULL` to distinguish system-graded from
    admin-logged, `completed_at = CURRENT_DATE`, `score`, `passed=true`,
    `expires_at` via the new shared `compute_expiry` helper) and sets
    `training_attempts.training_record_id` to link them; same
    self-ownership check; `409` if already submitted. Returns `{score,
    passed, correct_count, total_count}`.
  - `GET /attempts` — filters `course_id`, `guard_user_id`, `status`;
    non-`training:manage` callers forced to their own `guard_user_id`
    (mirrors `violations.py`/`leave.py`'s established self-scoping shape).

Register nothing new in `main.py` — `training.router` is already
registered.

## Frontend

**Bug fix — `frontend/src/hooks/usePermission.ts`**: add `'training:read',
'training:write', 'training:manage'` to `ALL_PERMISSIONS` (roles 1/2/8
inherit all three automatically, matching the confirmed live DB grants
exactly); add `'training:read'` to roles 3, 4, 5, 6's explicit lists; add
`'training:write'` to role 3's explicit list only (matches DB: write is
1,2,3,8, not 4/5/6). This alone fixes the invisible sidebar link.

**`frontend/src/api/training.ts`**: extend with `TrainingQuestion`,
`TrainingAttempt` types and `getQuestions(courseId)`,
`createQuestion(courseId, data)`, `updateQuestion(id, data)`,
`deleteQuestion(id)`, `startAttempt(courseId)`, `getAttempt(id)`,
`answerQuestion(attemptId, questionId, selectedIndex)`,
`submitAttempt(attemptId)`, `listAttempts(filters)`. Extend
`TrainingCourse` with `question_count: number`.

**`frontend/src/pages/Training.tsx`** (extend in place, same file/tab
structure — `Overview`/`Courses`/`Records`/`Certifications` stay exactly
as they are):
- `CoursesTab`: add a "Manage Questions" action (gated `training:manage`)
  per course row, opening a dialog listing that course's questions with
  add/edit/delete — mirrors the existing `Add Course` dialog's form style
  exactly (`TextField`s + a `MenuItem`-based select for `correct_index`
  built from the current `options` list).
- New 5th tab **"My Training"** (visible to everyone via the now-fixed
  `training:read`): lists active courses with a question bank
  (`question_count > 0`) as cards — course name, category chip, pass
  score, and a "Start Quiz" button (or "Resume Quiz" if an `in_progress`
  attempt already exists, checked via `listAttempts({status:
  'in_progress'})`). Clicking opens a focused quiz dialog: one question
  at a time or a scrollable single-page form (single-page is simpler and
  matches this codebase's preference for fewer moving parts — no
  progress-tracking state machine needed), radio-button options,
  "Submit Quiz" button calling `submitAttempt` and showing a result
  banner (score, pass/fail, and — on pass — the computed `expires_at`
  pulled from the linked training record). Below the course cards, a
  compact "My Results" list from `listAttempts({guard_user_id: self})`
  showing past attempts with score/pass chips, reusing the existing
  `EXPIRY_STATUS_CONFIG` color convention from this file for visual
  consistency with the Records/Certifications tabs.

**`frontend/src/App.tsx`/`Sidebar.tsx`**: no changes needed — the route
and nav item already exist (`/training`, gated `training:read`); only the
permission-matrix bug fix above was actually broken.

## Verification

1. Migration `0068` applies cleanly; `\d training_questions`, `\d
   training_attempts` show RLS enabled.
2. **Confirm the bug fix**: before any other change, log in as any role
   and confirm "Guard Training" is now visible in the sidebar (currently
   it is not, on the running stack, for every role tested).
3. As admin: create a course, add 3 multiple-choice questions via the new
   question-bank dialog, confirm `question_count` shows `3` on the
   Courses tab.
4. As a guard: open "My Training" → course card shows "Start Quiz" →
   answer questions → Submit → correct score/pass computed (verify by
   hand: e.g. 2/3 correct → 67%, matches `course.passing_score`
   threshold) → a new row appears in the existing Records tab with
   `passed` and `expires_at` set correctly per `validity_months`.
5. Start a second attempt, answer one question via `PUT
   /attempts/{id}/answer`, close and reopen "My Training" → "Resume Quiz"
   returns the same in-progress attempt with that answer pre-filled, not
   a fresh one.
6. Submit an attempt twice → second submit → `409`.
7. As a different guard, attempt `GET /attempts/{id}` on someone else's
   attempt → `403`; as admin (`training:manage`) → `200`.
8. Regression: the existing manual "Log Completion" flow on the Records
   tab still creates a `training_records` row identically to before
   (confirms `compute_expiry`'s extraction didn't change behavior).
9. `npx tsc --noEmit` clean; `docker compose build api frontend` clean;
   live-verify via the established frontend-dev-on-5174-against-real-Docker
   workflow (aegis / ops@aegis.demo / Demo1234!).

---

# Round: Client Billing / Invoicing — Final ShiftSecure Item

## Context

Last item in the confirmed ShiftSecure build order (Employee → Attendance/
Roster → Violations → Leave → Payroll/CPF/IR8A → SOP Training → **Client
Billing/Invoicing**). Confirmed via direct reading that this is a genuine
zero-code gap, distinct from the concept it's easily confused with:
`backend/app/routers/billing.py` is Stripe SaaS **subscription** billing —
the tenant (the security company) paying *Seventh AI Vision* for the
platform. This round is the opposite direction: the tenant invoicing
**their own clients** (the building/site owners who pay the security
company for guard services delivered). Confirmed `sites` has zero
client/billing linkage (`\d sites` — no `client_id`, no rate column) and
there is no frontend page for either concept yet (`grep` for
`billing|Billing|subscription` across `frontend/src/pages` — zero hits),
so this round introduces both the schema and the first-ever page in this
naming space.

**Naming decision (avoids future collision):** the new page is
`Invoicing.tsx` / route `/invoicing` / permissions `invoicing:read`/
`invoicing:manage` — deliberately **not** `Billing.tsx`/`billing:*`,
since those names are already owned by the Stripe subscription concern
(`backend/app/routers/billing.py`'s existing `billing:read`/
`billing:manage` permissions) even though it has no frontend yet. Keeping
the names distinct means a future round can build the subscription-billing
UI without any rename or ambiguity.

**What already exists that this round builds on:** the guard-hours
computation is the same shape already proven twice this session —
`payroll.py::create_payroll_run`'s `EXTRACT(EPOCH FROM (actual_end -
actual_start))/3600.0` + `overtime_minutes`-based regular/OT split
(`backend/app/routers/payroll.py:88-116`) — just grouped by **site**
instead of by guard. `services/payroll.py::OT_MULTIPLIER` (the standard
1.5× rate) is reused directly rather than re-declared. The
`sites.py`/`Sites.tsx` field-addition pattern (`geofence_radius_meters`,
added in the selfie-liveness round) is the template for adding `client_id`
+ `bill_rate` to sites. The ReportLab→`StreamingResponse` PDF pattern
(`reports.py`, already reused for payslips/IR8A) generates the invoice
PDF — and this time the query-param-JWT auth for the PDF endpoint is
built correctly **from the start**, having hit that exact bug twice
already this session (Leave, Payroll) when a download-link endpoint was
first written on the standard Authorization-header pattern.

**Scope decisions:**
- **One rate per site (`sites.bill_rate`), not per-guard-per-site.**
  Matches how `payroll`'s guard rate lives directly on `users` — a single
  scalar field on the entity being rated, not a join table. A client's
  contract typically prices a site's coverage uniformly, not per
  individual guard.
- **Tax rate is a parameter at generation time, not a fixed constant.**
  Unlike CPF (legally fixed) or violation points (a business preference
  best kept out of the UI), GST/tax registration varies per business —
  the generate-invoice call takes an optional `tax_rate` (default 9%,
  the current SG GST rate, overridable to `0` for non-GST-registered
  tenants) rather than hardcoding it or adding a new tenant-wide setting
  for a single number used once per invoice.
- **No client-portal self-service invoice viewing.** The existing
  client-portal role (`role_id=7`) is scoped to *sites* via `user_sites`,
  not to a billing account — wiring "a client user can see their own
  invoices" needs a client-portal-to-billing-client link that doesn't
  exist and is a reasonable, clearly-scoped follow-up, not part of this
  round (same "documented, deferred" pattern used for mobile training/
  leave/payroll surfaces earlier).
- **Draft → finalized → paid/void**, one more state than payroll's
  draft/finalized because invoices carry real accounts-receivable
  meaning "has the client paid" that payroll's finalize doesn't need.
  `invoice_number` is assigned only at finalize (not at draft creation)
  so a discarded draft never leaves a gap in the sequence.
- **Permissions as tight as payroll's** — `invoicing:manage` excludes
  supervisor/operator (roles 1,2,8 only); `invoicing:manage` is money +
  client-facing document generation. `invoicing:read` extends one tier
  further to supervisor (1,2,3,8) since a site supervisor may reasonably
  want to confirm an invoice went out for their site, but stops there —
  unlike payroll, there's no guard "self" case to widen read access to
  every role.

## Backend

**Migration `0069_client_invoicing.py`** (down_revision `0068`):
```sql
CREATE TABLE billing_clients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    contact_name    VARCHAR(255),
    contact_email   VARCHAR(255),
    contact_phone   VARCHAR(30),
    billing_address TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- standard RLS triplet

ALTER TABLE sites
  ADD COLUMN client_id UUID REFERENCES billing_clients(id) ON DELETE SET NULL,
  ADD COLUMN bill_rate NUMERIC(8,2);
-- both nullable — a site can exist before being linked to a billing client
-- or priced, same "flag missing config, don't block" precedent as
-- geofence_radius_meters.

CREATE TABLE invoices (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id            UUID NOT NULL REFERENCES billing_clients(id) ON DELETE CASCADE,
    invoice_number       VARCHAR(30),
    period_start         DATE NOT NULL,
    period_end           DATE NOT NULL,
    status               VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','finalized','paid','void')),
    subtotal             NUMERIC(12,2) NOT NULL DEFAULT 0,
    tax_rate             NUMERIC(5,4) NOT NULL DEFAULT 0,
    tax_amount           NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_amount         NUMERIC(12,2) NOT NULL DEFAULT 0,
    due_date             DATE,
    generated_by_user_id UUID REFERENCES users(id),
    generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalized_at         TIMESTAMPTZ,
    paid_at              TIMESTAMPTZ,
    voided_at            TIMESTAMPTZ
);
-- standard RLS triplet + idx_invoices_client(tenant_id, client_id), idx_invoices_status(tenant_id, status)

CREATE TABLE invoice_line_items (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    invoice_id       UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    site_id          UUID REFERENCES sites(id) ON DELETE SET NULL,
    site_name        VARCHAR(255) NOT NULL,
    regular_hours    NUMERIC(8,2) NOT NULL DEFAULT 0,
    overtime_hours   NUMERIC(8,2) NOT NULL DEFAULT 0,
    bill_rate        NUMERIC(8,2) NOT NULL,
    regular_amount   NUMERIC(12,2) NOT NULL DEFAULT 0,
    overtime_amount  NUMERIC(12,2) NOT NULL DEFAULT 0,
    line_total       NUMERIC(12,2) NOT NULL DEFAULT 0
);
-- standard RLS triplet + idx_invoice_line_items_invoice(tenant_id, invoice_id)
-- site_id kept nullable + site_name denormalized so a later site rename/
-- deletion never corrupts a historical invoice's line item.

INSERT INTO permissions (code, description, category) VALUES
  ('invoicing:read',   'View billing clients and invoices',                'guard'),
  ('invoicing:manage', 'Manage billing clients, generate/finalize invoices','guard')
ON CONFLICT (code) DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.id IN (1, 2, 8) AND p.code IN ('invoicing:read', 'invoicing:manage')
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.id = 3 AND p.code = 'invoicing:read'
ON CONFLICT DO NOTHING;
```

**`backend/app/routers/sites.py`**: add `client_id: str | None = None`,
`bill_rate: float | None = None` to `SiteCreate`/`SiteUpdate` and the
`list_sites`/`get_site`/`create_site`/`update_site` column
lists/INSERT/UPDATE — same mechanical pattern used for
`geofence_radius_meters` (`sites.py:13-158`), no new logic.

**New `backend/app/routers/invoicing.py`** (prefix `/api/v1/invoicing`,
registered in `main.py`):

*Billing clients* (`invoicing:manage` for writes, `invoicing:read` for
list/get — mirrors `training.py`'s course-CRUD split):
- `GET /clients`, `POST /clients`, `PUT /clients/{id}`,
  `DELETE /clients/{id}` (soft-delete via `is_active=FALSE`).

*Invoices:*
- `POST /invoices` (`invoicing:manage`) — body `{client_id, period_start,
  period_end, tax_rate?: float = 0.09, due_date?: date}`. Finds all
  `sites WHERE client_id = :client_id AND is_active = TRUE`; for each
  site with `bill_rate` set, sums `shifts` hours for that site in the
  period (same `EXTRACT(EPOCH...)`/`overtime_minutes` split as
  `payroll.py`'s hours query, grouped by site instead of guard);
  `regular_amount = regular_hours * bill_rate`, `overtime_amount =
  overtime_hours * bill_rate * OT_MULTIPLIER` (imported from
  `app.services.payroll`); sites missing `bill_rate` are skipped into a
  `warnings` array, matching payroll's exact skip-and-warn shape.
  `subtotal = Σ line_total`, `tax_amount = subtotal * tax_rate`,
  `total_amount = subtotal + tax_amount`. Inserts the `invoices` row
  (`status='draft'`) and one `invoice_line_items` row per priced site.
  Returns the invoice + line items + warnings.
- `GET /invoices` (`invoicing:read`) — filters `client_id`, `status`.
- `GET /invoices/{id}` (`invoicing:read`) — detail + line items.
- `PUT /invoices/{id}/finalize` (`invoicing:manage`) — `draft →
  finalized`; assigns `invoice_number = f"INV-{year}-{seq:04d}"` where
  `seq` = count of this tenant's already-finalized-or-later invoices in
  `year` + 1 (computed in the same transaction as the status update, no
  separate sequence object — matches this codebase's precedent of
  avoiding infrastructure for low-concurrency admin actions); sets
  `finalized_at`, and `due_date` if not already provided (default 30
  days out).
- `PUT /invoices/{id}/mark-paid` (`invoicing:manage`) — `finalized →
  paid`, sets `paid_at`.
- `PUT /invoices/{id}/void` (`invoicing:manage`) — `finalized → void`
  (a `paid` invoice cannot be voided — `409`).
- `DELETE /invoices/{id}` (`invoicing:manage`) — only while `draft`
  (discard-and-regenerate, mirrors `roster_batches`' discard precedent);
  `409` otherwise.
- `GET /invoices/{id}/pdf` (`invoicing:read`) — **built on the
  query-param-JWT pattern from the start** (`Query(..., description="JWT
  access token")` → `decode_access_token` → manual `AsyncSessionLocal` +
  `set_config` + manual `role_permissions` check for `invoicing:read` →
  `FileResponse`/`StreamingResponse`), mirroring `shifts.py`'s
  check-in-photo endpoint and `payroll.py`'s (corrected) payslip PDF
  endpoint exactly — client name/address, invoice number, period, line
  items table, subtotal/tax/total, due date, via the same ReportLab
  `SimpleDocTemplate`/`Table`/`TableStyle` helpers already used in
  `reports.py`/`payroll.py`.

## Frontend

**`frontend/src/api/invoicing.ts`** (new) — `BillingClient`, `Invoice`,
`InvoiceLineItem` types; `listClients`, `createClient`, `updateClient`,
`deleteClient`, `createInvoice`, `listInvoices`, `getInvoice`,
`finalizeInvoice`, `markInvoicePaid`, `voidInvoice`, `deleteInvoice`,
`invoicePdfUrl(id, token)` (query-param-JWT URL helper, mirrors
`payslipPdfUrl` in `api/payroll.ts`).

**`frontend/src/hooks/usePermission.ts`**: add `'invoicing:read',
'invoicing:manage'` to `ALL_PERMISSIONS` (roles 1/2/8 inherit); add
`'invoicing:read'` to role 3's explicit list only (no `manage` — matches
the migration's grant exactly, mirrors how `payroll:read`-only was added
to non-manage roles in the Payroll round).

**`frontend/src/pages/Sites.tsx`**: add a billing-client `Select`
(sourced from `listClients()`) and a "Bill Rate ($/hr)" `TextField` to
the site create/edit dialog, next to the existing geofence fields —
same addition pattern as the geofence-radius field from the
selfie-liveness round.

**New `frontend/src/pages/Invoicing.tsx`** — tabbed (`Clients` /
`Invoices`), reusing the exact `GlassCard`/`Tabs`/`fadeUpSx`/
`useCountUp`/`PageHeader`/`PermissionGuard` toolkit as
Payroll.tsx/Leave.tsx:
- **Tab: Clients** — table + add/edit dialog (name, contact
  name/email/phone, billing address), gated `invoicing:manage` for
  writes.
- **Tab: Invoices** — list (client name, period, status chip) → click
  opens detail (mirrors `Payroll.tsx`'s `RunDetail` pattern): KPI row
  (Subtotal / Tax / Total / Sites Skipped), line items table (site,
  regular/OT hours, amounts), warnings banner, "Finalize" / "Mark Paid" /
  "Void" buttons per current status (gated `invoicing:manage`),
  "Download PDF" (`invoicePdfUrl`). "New Invoice" button opens a dialog
  (client picker, period-start/end date pickers, tax-rate field
  defaulting to 9%, optional due-date) → `createInvoice` → navigates
  straight into the new draft's detail view.

**`frontend/src/App.tsx`**: `<Route path="invoicing"
element={<InvoicingPage />} />`. **`Sidebar.tsx`**: "Client Invoicing"
nav item near Payroll, gated `invoicing:read`, using `ReceiptLongIcon`
or `RequestQuoteIcon` from `@mui/icons-material`.

## Verification

1. Migration `0069` applies cleanly; `\d billing_clients`, `\d invoices`,
   `\d invoice_line_items` show RLS enabled; `\d sites` shows the 2 new
   nullable columns.
2. Create a billing client; link 2 sites to it via `Sites.tsx` with
   different `bill_rate`s; leave a 3rd linked site with no rate set.
3. Ensure at least one `completed` shift with real hours (including some
   `overtime_minutes`) exists at each of the 2 rated sites within a test
   period (reuse the same live-shift-completion technique used to verify
   Payroll).
4. `POST /invoices` for that client+period → draft invoice with one line
   item per rated site, correct `regular_amount`/`overtime_amount` (spot
   check by hand against `OT_MULTIPLIER=1.5`), correct `subtotal`/
   `tax_amount` (at the default 9%)/`total_amount`; the unrated 3rd site
   appears in `warnings`, not as a line item.
5. `PUT /invoices/{id}/finalize` → status `finalized`, `invoice_number`
   assigned in `INV-{year}-0001` shape; finalizing a second invoice the
   same year → `INV-{year}-0002`.
6. `PUT /invoices/{id}/mark-paid` → status `paid`; attempting `void` on a
   paid invoice → `409`.
7. `DELETE` a still-`draft` invoice → succeeds; attempting `DELETE` on a
   `finalized` invoice → `409`.
8. `GET /invoices/{id}/pdf?token=...` (no Authorization header, token
   only in the query string) → valid PDF, `200`; without a valid token →
   `401`; as a role without `invoicing:read` → `403`.
9. As a supervisor (role 3), `GET /clients`/`GET /invoices` → `200`;
   `POST /invoices` or `PUT .../finalize` → `403` (read-only boundary).
10. `npx tsc --noEmit` clean; `docker compose build api frontend` clean;
    live-verify via the established frontend-dev-on-5174-against-real-Docker
    workflow (aegis / ops@aegis.demo / Demo1234!) — including opening the
    new dialog live in the browser and downloading a real PDF, same
    depth of check used for Payroll/Training this session.

---

# Round: Mobile Parity — Live Wall, Zone Drawing, My Violations/Leave, Command Centre Taps

## Context

With the full ShiftSecure build order and the demo pipeline both done, the user asked to work through the remaining deferred items one by one. First up: mobile parity for four web-only features built earlier this session. Researched the mobile app directly (31 screens under `mobile/src/navigation/index.tsx`, React Navigation 6, no existing per-screen role gating anywhere) before scoping this.

**Confirmed via direct reading, not assumed:**
- `DashboardScreen.tsx` (273 lines) already covers most of Command Centre's *content* (KPI cards for open alerts/incidents/cameras/detections, a live WebSocket event feed) — the actual gap is narrower than "build Command Centre for mobile": everything is currently non-interactive (no `onPress` anywhere in the file).
- `mobile/src/api/zones.ts` already has full CRUD for both restricted and crowd zones, including `polygon: Array<{x,y}>` — the missing piece for "zone drawing" is purely the touch-based polygon-authoring UI, not any API work.
- `mobile/src/api/violations.ts` and `leave.ts` **do not exist**. But `backend/app/routers/violations.py:58-60` and `leave.py:153-155` already self-scope `GET /violations` / `GET /leave/requests` to `guard_user_id = :uid` for guard-tier roles — the exact same endpoints the web admin views call. A mobile "My Violations"/"My Leave" screen needs zero backend changes.
- Live video on mobile is MJPEG-over-`<img>` inside a `WebView` (`CameraLiveScreen.tsx`) — RN's native `Image` component can't decode a `multipart/x-mixed-replace` MJPEG stream, so this WebView-with-HTML-img pattern is the only viable way to show live video and must be reused, not replaced.
- No `react-native-svg` (or any vector-drawing lib) is installed. Adding one is avoidable — the original web `ZoneDrawOverlay.tsx` build note ("no new npm dependencies — plain SVG... sufficient") sets the precedent of preferring zero new deps; on RN the equivalent zero-dep technique is absolutely-positioned `View`s for vertex dots and rotated thin `View` rectangles for connecting edges (standard RN "draw a line without SVG" trick).
- There is **no existing Users/admin-management screen on mobile at all** (confirmed absent from the full 31-screen inventory) — unlike the web round, which only needed to add a section to an already-existing `Users.tsx`. Building "admin hierarchy" for mobile means building a whole new admin screen from scratch for a narrow view-only feature, on an app whose entire existing screen set (Patrol, Shifts, DOB, Visitors, Compliance, Emergency, etc.) is guard/ops-facing, not admin-management-facing.

**Scope decision (confirmed via this analysis, not re-asked):** build all 5 requested items except mobile admin-hierarchy, which is explicitly **deferred** — the cost (new screen, new data-fetching, new UI patterns for a feature with no existing mobile home) is disproportionate to the value (org-hierarchy browsing is a desktop-admin task; every other admin-facing web feature already stays desktop-only on this app by precedent). The other four (Live Wall, Zone Drawing, My Violations/Leave, Command Centre taps) all have strong guard/operator daily value and reuse existing mobile infrastructure cleanly.

## 1. Command Centre parity — make the existing Dashboard interactive

**File:** `mobile/src/screens/DashboardScreen.tsx` (edit in place, no rewrite).

- Wrap each `KpiCard` render in a `Pressable`: Open Alerts → `navigation.navigate('Alerts')`, Open Incidents → `navigation.navigate('Incidents')`, Cameras Online → `navigation.navigate('Cameras')`, Detections Today → `navigation.navigate('More', { screen: 'Detections' })` (two-level navigate into a nested stack — standard React Navigation pattern, matches how `MoreMenuScreen.tsx` already does `nav.navigate(item.screen)` within its own stack).
- Wrap each live-feed event `Card` in a `Pressable`: `alert_created` → `navigation.navigate('Alerts', { screen: 'AlertDetail', params: { alertId } })`, `incident_created` → `navigation.navigate('Incidents', { screen: 'IncidentDetail', params: { incidentId } })`, `camera_status_changed` → `navigation.navigate('Cameras')`. The event's underlying id is already on the `RealtimeEvent` payload (same payload shape already read for `title`/`severity`/`status` in the existing `handleEvent` callback) — just needs to also be captured into the local `LiveEvent` interface (`id` field already exists, currently sourced from `e.payload.id`, which for these three event types **is** the alert/incident id — confirmed against the same payload shape the web `useRealtimeEvents.ts` already consumes).
- Use `useNavigation<NativeStackNavigationProp<...>>()` typed against a navigation prop capable of reaching sibling tabs — same approach `MoreMenuScreen.tsx` uses for its own stack, extended one level via the parent tab navigator (a standard, common RN pattern, no new library).

## 2. Live Wall (mobile) — single-WebView multi-camera grid

**New file:** `mobile/src/screens/LiveWallScreen.tsx`.
**New file:** `mobile/src/lib/liveWallStorage.ts` (AsyncStorage persistence, mirrors `frontend/src/lib/liveWallWindow.ts`'s localStorage-persistence intent but for RN).

- **Not** N separate `WebView` components (heavy on a phone — each WebView is a separate native view + process overhead). Instead, one `WebView` whose HTML contains a CSS grid of multiple `<img>` tags, each pointed at a different camera's `.../live?token=...` URL — directly extending `CameraLiveScreen.tsx`'s existing single-`<img>` HTML string to N images in a grid `<div>`. This keeps the "MJPEG via browser-native multipart decoding" trick (the reason a WebView is used at all) while staying to one native WebView instance.
- Camera picker: reuse `getCameras()`/`getStreams()` (`mobile/src/api/cameras.ts`, already confirmed present) in a simple modal list (checkbox-style selection), capped at 4 cameras (2×2 grid — appropriate for phone screen real estate, unlike desktop's 1×1–4×4 options). Selection persisted via `@react-native-async-storage/async-storage` (confirmed already a dependency) so the wall is remembered across app opens, mirroring the web version's localStorage persistence.
- Register `LiveWall: undefined` in `CamerasStackParamList` (`mobile/src/navigation/index.tsx`), add `<CamerasStack.Screen name="LiveWall" component={LiveWallScreen} options={{ title: 'Live Wall' }} />`, and add an entry button in `CamerasScreen.tsx`'s header (a header-right icon button, matching the existing header-title-set pattern already used in `CameraLiveScreen.tsx`'s `useLayoutEffect`).

## 3. Zone drawing (mobile) — touch-based polygon editor over a live WebView

**New file:** `mobile/src/lib/videoCoords.ts` — pure functions ported from `frontend/src/lib/videoCoords.ts` (no DOM dependency in the original, so this is a near-direct port: `computeContainRect`, `screenToNormalized`, `normalizedToScreen`).
**New file:** `mobile/src/components/ZoneDrawOverlay.tsx` — the touch-capture + rendering layer: an absolutely-positioned `View` (`StyleSheet.absoluteFill`) stacked on top of the live-feed `WebView`, using `onStartShouldSetResponder`/`onResponderRelease` (or a `Pressable` with `onPressIn` reading `nativeEvent.locationX/Y` — simpler, sufficient for tap-to-place) to capture vertex taps. Renders:
  - Vertex dots: small circular `View`s absolutely positioned at each point.
  - Connecting edges: thin rotated `View` rectangles between consecutive points (compute `Math.atan2(dy,dx)` for rotation and `Math.hypot(dx,dy)` for length — the standard zero-dependency "draw a line with a View" technique, avoiding the `react-native-svg` dependency the app doesn't currently have, consistent with the original web round's "no new npm dependencies" precedent).
  - Tapping back on the first vertex (within a small hit-radius) closes the polygon; a toolbar (`Undo`, `Clear`, `Close`) mirrors the web `ZonePolygonEditor.tsx`'s controls.
- **Deliberate v1 simplification for coordinate correctness:** rather than computing/correcting for `object-fit: contain` letterboxing (which the WebView HTML currently uses for `CameraLiveScreen.tsx`), the zone-draw screen's WebView HTML uses `object-fit: fill` for the `<img>` instead — this fills the container edge-to-edge with no black bars, at the cost of a slightly distorted preview image, but makes the overlay's raw touch-container coordinates map 1:1 to normalized `{x,y}` with no aspect-ratio correction needed. `videoCoords.ts` is still ported (for the `screenToNormalized`/`normalizedToScreen` pair, which is aspect-ratio-agnostic under `fill`), but `computeContainRect`'s letterboxing math isn't exercised by this screen — documented inline as a known, deliberate simplification, not a bug.
**New file:** `mobile/src/screens/ZoneDrawScreen.tsx` — composes the live WebView + `ZoneDrawOverlay`, plus a save form (name `TextInput`, severity picker, a restricted/crowd type toggle) calling the already-existing `createZone`/`createCrowdZone` (`mobile/src/api/zones.ts`).
- Register `ZoneDraw: { cameraId: string; streamId: string; cameraName: string }` in `CamerasStackParamList`; add a "Draw Zone" header-right button on `CameraLiveScreen.tsx` navigating there with the same params it already receives.

## 4. My Violations / My Leave (mobile) — single combined screen, zero backend work

**New file:** `mobile/src/api/violations.ts` — `Violation` interface + `getMyViolations()` (`GET /api/v1/violations`, self-scoped server-side for a guard token) + `getMyViolationsSummary()` (`GET /api/v1/violations/summary`, returns the single self-row for a guard caller) — mirrors `frontend/src/api/violations.ts`'s field shapes exactly (`id, guard_user_id, violation_type, description, points, status, is_auto_generated, occurred_at, site_name, ...`).
**New file:** `mobile/src/api/leave.ts` — `LeaveType`, `LeaveRequest`, `LeaveBalance` interfaces + `getLeaveTypes()`, `getMyLeaveRequests()`, `getMyLeaveBalances()`, `createLeaveRequest(data)`, `cancelLeaveRequest(id)` — mirrors `frontend/src/api/leave.ts`.
**New file:** `mobile/src/screens/MyRecordScreen.tsx` — one screen, a segmented control (two RN `Pressable` tab pills, avoiding a nested navigator for just 2 sub-views) toggling between:
  - **Violations tab:** a summary `Card` (total points, styled via the same severity-tint-by-threshold convention already used on web — green/amber/red) + a `FlatList` of the guard's own violations (type, points, status chip, date).
  - **Leave tab:** balance `Card`s per active leave type (entitled/used/remaining) + a `FlatList` of the guard's own requests (status chip) + a "Request Leave" button opening a small modal form (type picker, start/end date, reason) → `createLeaveRequest`, and a cancel action on pending/approved rows → `cancelLeaveRequest`.
- Register `MyRecord: undefined` in `MoreStackParamList`, add `<MoreStack.Screen name="MyRecord" component={MyRecordScreen} options={{ title: 'My Record' }} />`, and add an entry to `MENU_ITEMS` in `MoreMenuScreen.tsx` (icon: `document-text-outline` or `ribbon-outline`, color `colors.warning` or similar, description "Your violations and leave requests").

## Explicitly not building this round

- **Mobile admin hierarchy view** — deferred per the reasoning above (no existing mobile admin screen to extend; disproportionate new-screen cost for a desktop-admin-task feature).
- **Zone editing** (dragging an existing saved polygon to reshape it) — out of scope on both web and mobile; only zone *creation* is being built here, matching the original web round's own "create-only... a natural v2" scope note.
- **Per-cell analytics overlay on the mobile Live Wall** (detection boxes/zone outlines drawn on each tile) — the desktop Live Wall's `DetectionOverlay` module-filter feature is a bigger, separate lift; this round's mobile Live Wall is live-video-only, matching how the original mobile "CameraLiveScreen" is also video-only today (no overlay).

## Verification

1. `npx tsc --noEmit` in `mobile/` clean.
2. Dashboard: tap each KPI card → lands on the correct tab; trigger a live alert (reuse the existing demo intrusion loop) → its live-feed entry appears and tapping it opens the correct `AlertDetail` screen with matching data.
3. Live Wall: from Cameras tab, open Live Wall → pick 4 cameras → all 4 MJPEG tiles render simultaneously inside one WebView; close and reopen the app → the same 4 cameras are still selected (AsyncStorage persistence confirmed).
4. Zone drawing: open a live camera → "Draw Zone" → tap 4+ points on the live feed → tap the first vertex to close → fill in name/severity/type → Save → confirm via `GET /api/v1/zones` (or crowd-zones) that the polygon point count and severity match what was drawn; the new zone is visible on the equivalent web Zones page.
5. My Violations/Leave: as a seeded guard user (`guard1@aegis.demo` / `Demo1234!`), open More → My Record → Violations tab shows only that guard's own violations (cross-check against `GET /api/v1/violations` with that guard's token) with a correct points total; Leave tab shows correct balances and any existing requests; submit a new leave request → appears in the list with `status=pending`; cancel a pending request → status updates to `cancelled`.
6. Regression: `CameraLiveScreen.tsx`'s existing single-camera view (used by both Cameras tab and Incidents→IncidentCameraLive) still works unchanged — confirms the WebView HTML pattern extraction for Live Wall didn't disturb the original single-camera path.
7. Live-verify via Expo (`npx expo start`, either a simulator or Expo Go on a physical device against the same running Docker backend used all session — `aegis` / `guard1@aegis.demo` / `Demo1234!` for the guard-scoped screens, `ops@aegis.demo` for admin-scoped checks).

---

# Round: Client Portal Invoice Viewing

## Context

Second item from the deferred-items list. The Invoicing round explicitly deferred this, noting the missing link: "The existing client-portal role (role_id=7) is scoped to sites via `user_sites`, not to a billing account — wiring 'a client user can see their own invoices' needs a client-portal-to-billing-client link that doesn't exist."

**Confirmed via direct research, not assumed:**
- That link does **not** need to be a new direct `users → billing_clients` column. The chain `user_sites → sites.client_id → billing_clients` already exists and is sufficient to derive it transitively — mirroring exactly how role 7's visibility into alerts/incidents/cameras is already derived transitively through `user_sites`, not a direct grant.
- The precedent to mirror is `backend/app/dependencies/sites.py`'s `get_allowed_site_ids()`/`site_scope_clause()`/`is_site_allowed()` — already used by `alerts.py`, `incidents.py`, `streams.py`, `dob.py` to scope role 7's visibility. This round adds the equivalent triplet for billing clients.
- **A client-portal user's derivable billing_client set is not guaranteed to be exactly one.** `users.py`'s `PUT /{user_id}/sites` lets an admin assign any site in the tenant to any user with no validation against `client_id` — nothing stops mixing sites from two different billing_clients on one portal user. The scoping logic must handle 0, 1, or N distinct client_ids, the same way `get_allowed_site_ids` already handles a variable-length site list, not assume a single client.
- Confirmed role 7 currently has neither `invoicing:read` nor `invoicing:manage` (migration `0069_client_invoicing.py`'s grants are `1,2,8` for manage and `1,2,3,8` for read only) — a permission grant is required, not just a scoping change. `invoicing:manage` deliberately stays ungranted to role 7 — a client views invoices, never creates/finalizes/voids them.
- `ClientPortal.tsx` (`frontend/src/pages/ClientPortal.tsx`) is a flat, standalone page (not inside `AppShell`, swapped in by role at `App.tsx`'s root) with 4 existing GlassCard sections (Alerts, Incidents, Cameras, Occurrence Book), each unconditionally rendered with its own empty state — no per-section permission gating today, since role 7's permission set is fixed and every existing section's endpoint already grants role 7 access. The new Invoices section follows the identical shape.

## Backend

**New migration `0070_client_portal_invoicing.py`** (down_revision `0069`):
```sql
INSERT INTO role_permissions (role_id, permission_id)
SELECT 7, id FROM permissions WHERE code = 'invoicing:read'
ON CONFLICT DO NOTHING;
```
No new tables/columns — the derivation chain already exists.

**`backend/app/dependencies/sites.py`** — add two small additions alongside the existing `get_allowed_site_ids`/`site_scope_clause`/`is_site_allowed` trio, same file (they're the same "narrow a role-7 caller's visibility" concern, just one hop further through `sites.client_id`):
```python
async def get_allowed_client_ids(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
) -> list[str] | None:
    """None = unrestricted (every other role reaching this dependency is
    already gated by invoicing:read/manage); [] = no visibility; else the
    distinct billing_client ids reachable through the caller's own site
    assignments. Only role 7 is narrowed here — invoicing:manage already
    excludes role 7 entirely, so this only scopes the read surface."""
    if token.role_id != _CLIENT_ROLE:
        return None
    result = await db.execute(
        text(
            "SELECT DISTINCT s.client_id FROM user_sites us "
            "JOIN sites s ON s.id = us.site_id "
            "WHERE us.user_id = CAST(:uid AS uuid) AND s.client_id IS NOT NULL"
        ),
        {"uid": token.user_id},
    )
    return [str(r.client_id) for r in result]


def client_scope_clause(allowed: list[str] | None, column: str, params: dict) -> str | None:
    """Mirrors site_scope_clause exactly, kept separate rather than reused
    directly since the bound param name (allowed_client_ids) differs and
    there's only ever one caller today."""
    if allowed is None:
        return None
    if not allowed:
        return "FALSE"
    params["allowed_client_ids"] = [uuid.UUID(c) for c in allowed]
    return f"{column} = ANY(:allowed_client_ids)"


def is_client_allowed(allowed: list[str] | None, client_id) -> bool:
    if allowed is None:
        return True
    return client_id is not None and str(client_id) in allowed
```

**`backend/app/routers/invoicing.py`** — add `Depends(get_allowed_client_ids)` alongside the existing `Depends(require_permission("invoicing:read"))` on the three read endpoints:
- `list_clients`: apply `client_scope_clause(allowed, "id", params)` to the WHERE.
- `list_invoices`: apply `client_scope_clause(allowed, "i.client_id", params)`.
- `get_invoice`: apply `is_client_allowed(allowed, invoice_row.client_id)` → `404` (not `403` — matches `sites.py`'s existing not-found-not-forbidden convention for scoped-out rows) if disallowed.
- `get_invoice_pdf` (the query-param-JWT endpoint — already manually re-checks `invoicing:read` inside its own `AsyncSessionLocal` block since it can't use the normal dependency chain): add the identical role-7 derivation query inline there and check the loaded invoice's `client_id` against it → `403` if disallowed. This is the one endpoint that can't just add a FastAPI `Depends`, since it already hand-rolls its auth for the query-param-JWT pattern.
- No changes needed to any `invoicing:manage`-gated endpoint (create/finalize/mark-paid/void/delete client or invoice) — role 7 still lacks `invoicing:manage` entirely, so those already 403 for a client-portal caller with zero changes.

## Frontend

**`frontend/src/pages/ClientPortal.tsx`** — additive, same shape as the existing 4 sections:
- New 5th `GlassCard` section, "Invoices" — `useQuery(['client-invoices'], () => listInvoices())` (already exported from `frontend/src/api/invoicing.ts`, built in the Invoicing round). Row per invoice: invoice number (or "Draft" — though a client should realistically only ever see finalized+ invoices in practice since drafts are pre-numbering, but no special filtering needed, the backend scope already handles visibility correctly regardless of status), period, a status chip (reuse the existing severity/status chip convention already used elsewhere on this page), total amount, and a download icon button using the already-existing `invoicePdfUrl(id, token)` helper — same `component="img"`-style direct-link pattern this file already uses for the live-camera dialog's authenticated URL.
- New 4th KPI card, "Unpaid Invoices" — count of invoices with `status IN ('finalized')` (i.e. billed but not yet paid) from the same `listInvoices()` result, no new query. Resize the existing 3 KPI `Grid size={{xs:12,sm:4}}` cards to `sm:3` so all four sit in one row, matching the 4-up KPI-row convention already used on `Payroll.tsx`/`Invoicing.tsx`/`Violations.tsx` elsewhere this session.
- Empty state ("No invoices yet") matches the existing empty-state copy style used by the other 4 sections.

**`frontend/src/hooks/usePermission.ts`** — add `'invoicing:read'` to role 7's explicit permission list (mirrors the migration's grant exactly), even though `ClientPortal.tsx` doesn't currently call `usePermission()` directly — keeps the frontend fallback matrix consistent with the backend seed, matching the discipline already applied for every other permission grant this session (e.g. role 3 in the original Invoicing round).

## Verification

1. Migration `0070` applies cleanly; `SELECT * FROM role_permissions WHERE role_id=7 AND permission_id = (SELECT id FROM permissions WHERE code='invoicing:read')` returns a row.
2. Set up real test data: create a billing client, link a site to it via `Sites.tsx` (or the existing `sites.client_id` field from the Invoicing round), assign that same site to a role-7 test user via the existing `user_sites` admin UI, generate + finalize an invoice for that client covering that site.
3. Log in as that role-7 user → `GET /api/v1/invoicing/invoices` returns only that invoice; a second, unrelated billing client's invoice (no site overlap with this user) is **not** present.
4. `GET /api/v1/invoicing/invoices/{other_client_invoice_id}` (an invoice belonging to a client this user has no site-derived access to) → `404`.
5. `GET /api/v1/invoicing/invoices/{id}/pdf?token=...` for the visible invoice → `200` valid PDF; for the other client's invoice → `403`.
6. A role-7 user with zero site assignments → empty invoice list (fail-closed, not an error).
7. Regression: an admin/supervisor (roles 1,2,3,8) still sees the full unscoped invoice list — confirms `allowed=None` passthrough didn't break the existing Invoicing round's behavior.
8. `POST /api/v1/invoicing/invoices` (or any `:manage` endpoint) as the role-7 user → `403` (unchanged — still no `invoicing:manage` grant).
9. `npx tsc --noEmit` clean; `docker compose build api frontend` clean; live-verify by logging into the web app as the seeded role-7 client-portal test user and confirming the new "Invoices" section and "Unpaid Invoices" KPI card render correctly with real data, plus a working PDF download click.

---

# Round: Daily-Rate Pay, Guards Grid, Unified Guard Creation

## Context

User feedback on the guard-management experience: guard creation should flow straight into documents/pay/details instead of a separate edit step, the guard list should surface site/pay/employment at a glance instead of hiding it behind a dialog, and part-time/relief guards paid a flat rate per day worked have no pay-type to match ("day basis" workers) — only hourly and monthly exist today.

Confirmed by direct reading (not assumed): `Users.tsx`'s grid is 6 columns (Email, Full Name, Role, Status, Last Login, Actions) — no site, pay, or employment type shown without opening the edit dialog, even though `GET /users` already returns those fields (`_USER_SELECT_COLUMNS` includes the full `_EMPLOYEE_FIELDS` set, `users.py:73-76`). `compute_payslip` (`services/payroll.py:62-94`) has exactly two pay paths — `monthly_salary` or `hourly_rate × hours` — nothing for a flat per-day rate. `create_payroll_run` silently skips a guard with neither rate set, surfaced only via a `warnings` array. Site assignment lives entirely in a separate `SiteAccessDialog`, never inline in the grid.

**Real bug found during design, in scope to fix as required infrastructure (not drive-by cleanup):** `UserFormDialog` is mounted unconditionally in `Users.tsx` (`<UserFormDialog open={dialogOpen} ... editUser={editUser} />`, no conditional guard) and every field is seeded via `useState(editUser?.foo ?? '')`. React only evaluates a `useState` initializer on first mount — so `editUser` changing after that (Create → seed into Edit, or Edit user A → Edit user B without a reload) never resyncs the form fields. This has to be fixed for the unified-creation flow to work at all, and it also fixes a latent stale-data bug on the existing Edit-icon path.

## A. Daily-rate ("day basis") pay support

**Migration `backend/alembic/versions/0071_daily_rate_pay.py`** (down_revision `0070`):
```sql
ALTER TABLE users ADD COLUMN daily_rate NUMERIC(8,2);
ALTER TABLE payslips ADD COLUMN days_worked NUMERIC(5,2) NOT NULL DEFAULT 0;
```
Same precedent as `0067`'s `hourly_rate`/`monthly_salary` — scalar columns directly on `users`, no side table. `days_worked` is computed and shown for **every** guard regardless of pay type (it's real attendance data), not just daily-rate ones — same treatment as `regular_hours`/`overtime_hours` today (shown as `0.00`, never hidden).

**`backend/app/services/payroll.py::compute_payslip`** — extend the precedence chain: `monthly_salary` wins, else `daily_rate × days_worked`, else `hourly_rate × regular_hours`, else `0`. **Overtime pay stays gated purely on `hourly_rate` being set**, independent of which field drove base pay — this is not a new rule, it's the exact same behavior a `monthly_salary` + `hourly_rate` guard already gets today (salaried base pay, hourly-driven OT on top). A day-rate-only guard's overtime is tracked (`overtime_minutes` already computed per shift) but unpaid — deliberate v1 simplification, documented in the migration's own comment: a per-day rate has no natural hourly-equivalent baseline to multiply an OT premium against without inventing one.

**`backend/app/routers/payroll.py::create_payroll_run`**:
- Guards query: add `daily_rate` to the SELECT.
- Skip condition: widen to `hourly_rate IS None and monthly_salary IS None and daily_rate IS None`.
- Hours query: add `COUNT(DISTINCT actual_start::date) AS days_worked`, and add `AND actual_end IS NOT NULL` as a safety net — without it, a `status='completed'` shift missing `actual_end` (a data anomaly nothing currently enforces against) would inflate a daily-rate guard's `days_worked`/pay while contributing `$0` to an hourly guard's pay for the identical bad row.
- Pass `days_worked` into `compute_payslip` and into the `payslips` INSERT/RETURNING.

**`get_payroll_run`**: add `p.days_worked` to the payslips SELECT. **Payslip PDF**: add a "Days Worked" row to the line-item table, shown unconditionally (same reasoning as above).

**`backend/app/routers/users.py`**: add `"daily_rate"` to `_EMPLOYEE_FIELDS`; add `daily_rate: float | None = None` to `UserUpdate`. Not added to `UserCreate` — rates stay edit-only, same as the other two.

**Frontend**: `Users.tsx` Bank & Pay Details tab gets a third field, "Daily Rate ($/day)", between Hourly and Monthly, with helper text stating the precedence (including that Hourly Rate always drives OT pay even when a different field drives base pay). `types/api.ts`/`api/users.ts` gain `daily_rate`. `api/payroll.ts`'s `Payslip` type and `Payroll.tsx`'s `RunDetail` table gain `days_worked` (a "Days" column between OT Hrs and Gross).

## B. Guards grid enhancement

**`backend/app/routers/users.py::list_users`** — extend (only this endpoint, not the shared `_USER_SELECT_COLUMNS` used by `get_user`/`create_user`, so single-row fetches stay simple) with two `LEFT JOIN` subqueries:
```sql
SELECT {_USER_SELECT_COLUMNS},
       COALESCE(sn.site_names, '{}') AS site_names,
       COALESCE(dc.total_count, 0)::int AS documents_total_count,
       COALESCE(dc.expiring_count, 0)::int AS documents_expiring_count,
       COALESCE(dc.expired_count, 0)::int AS documents_expired_count
FROM users
LEFT JOIN (
    SELECT us.user_id, ARRAY_AGG(s.name ORDER BY s.name) AS site_names
    FROM user_sites us JOIN sites s ON s.id = us.site_id GROUP BY us.user_id
) sn ON sn.user_id = users.id
LEFT JOIN (
    SELECT ed.user_id, COUNT(*) AS total_count,
           COUNT(*) FILTER (WHERE ed.expiry_date IS NOT NULL AND ed.expiry_date < CURRENT_DATE) AS expired_count,
           COUNT(*) FILTER (WHERE ed.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days') AS expiring_count
    FROM employee_documents ed GROUP BY ed.user_id
) dc ON dc.user_id = users.id
ORDER BY users.created_at DESC
```
Relies on existing RLS on `user_sites`/`employee_documents` (same pattern as `roles.py`'s joins) — no manual tenant filter needed. `total_count` unfiltered so the frontend can tell "no documents" apart from "documents, none expiring."

**`Users.tsx` grid — 6 columns → 9**, folding Email into the Name cell as a secondary caption line (not dropped, just de-emphasized) to make room without losing information:

| User (name + email) | Role | Site(s) | Employment | Pay | Documents | Status | Last Login | Actions |

- **Site(s)**: first site as a Chip + `+N` with a Tooltip listing the rest; neutral "Unrestricted" if none (role ≠ client), "No Sites" error chip if none and role = client.
- **Pay**: `$X/mo` / `$X/day` / `$X/hr` per the same precedence as the backend; warning chip "Not set" (mirrors `Sites.tsx`'s existing "Geofence not configured" chip exactly) if nothing's configured.
- **Documents**: "N Expired" (error) / "N Expiring" (warning) / "Clean" (success) / muted `—` if zero uploaded.
- `types/api.ts`'s `User` gains `site_names?`, `documents_total_count?`, `documents_expiring_count?`, `documents_expired_count?` as list-only optional fields.

## C. Unified guard-creation flow

Fix the resync bug first (required for this to work at all): convert every `useState(editUser?.foo ?? '')` initializer in `UserFormDialog` to a bare `useState('')`, and add one `useEffect` keyed on `[open, editUser?.id]` that (re)populates every field — chosen over a `key={editUser?.id}` remount because a `key` change would visibly flicker MUI's `Dialog` backdrop at exactly the create→edit transition moment; a `useEffect` resync keeps one continuous `Dialog` instance open. This also fixes the same stale-data bug on the pre-existing Edit-icon path (worth noting in the round's own description, not a separate scope item).

**Backend**: widen `create_user`'s narrow `RETURNING id, tenant_id, role_id, email, full_name, is_active, created_at` to `RETURNING {_USER_SELECT_COLUMNS}` — once the frontend actually consumes the create response to seed the edit dialog, the previous narrow shape (silently mismatched against the `User` TS type already) becomes a real gap, not a latent one.

**Frontend**: `UserFormDialog` gains an `onCreated?: (user: User) => void` prop; the create mutation's `onSuccess` calls `onCreated?.(created)` instead of `onClose()` — dialog stays open, widens from `xs` to `sm`, the Tabs bar appears, lands on the Personal tab. The update mutation's `onSuccess` is unchanged (still closes — Save always finalizes). `Users.tsx`'s call site: `onCreated={(user) => setEditUser(user)}`. No other wiring needed — the existing "Add User" button already resets `editUser` to `null` first, so a fresh Create session still starts blank.

## Verification

1. `alembic upgrade head` / `downgrade -1` / `upgrade head` round-trips cleanly; `\d users`, `\d payslips` show the new columns.
2. Daily-rate payroll scenario: guard with `daily_rate=100`, no hourly/monthly, 3 completed shifts on 3 distinct dates (24h total, 1h of it overtime) → payslip shows `days_worked=3.00`, `base_pay=300.00`, `overtime_pay=0.00` (no hourly_rate ⇒ OT unpaid), CPF computed off `gross_pay=300.00`. An hourly-only guard in the same run is unaffected (precedence regression check). A guard with both `monthly_salary` and `daily_rate` set: confirm `monthly_salary` wins.
3. `GET /api/v1/users` for a user with 2 sites and 1 expired + 1 valid document → `site_names` has 2 entries, `documents_expired_count=1`, `documents_expiring_count=0`. `GET /api/v1/users/{id}` for the same user has no `site_names`/`documents_*` keys (scope boundary holds).
4. Grid renders 9 columns cleanly at 1366px and 1440px; a guard with no rate set shows the "Not set" warning chip and appears in the next payroll run's `warnings`.
5. Full creation walkthrough: Add User → email/password/role → Create → dialog stays open, widens, lands on Personal tab (not blank/frozen) → fill Employment → upload a document on Work Pass & Documents (confirms the multipart POST succeeds immediately post-creation — the actual risk this round exists to close) → set a Daily Rate on Bank & Pay Details → Save → grid shows the new row's site/employment/pay/documents columns populated.
6. Regression: Add User again immediately after → dialog opens blank, not showing the previous user's data. Open Edit on two different existing users back-to-back with no reload → each shows its own data, not the previous one's.
7. Payslip PDF shows "Days Worked" for both a daily-rate guard and an hourly guard (their real worked-day count, not hidden). `Payroll.tsx` RunDetail table shows the new "Days" column.
8. `npx tsc --noEmit` clean; `docker compose build api frontend` clean; live-verify via the established frontend-dev-on-5174-against-real-Docker workflow (aegis / ops@aegis.demo / Demo1234!) once Docker Desktop is stable enough to rebuild — if it's still down when implementation finishes, ship the source changes with tsc verification only and note the container rebuild as pending.

---

# Round: Live Attendance Redesign + Cross-Role Action Center (Duty Guidance)

## Context

The user wants the platform to stop being a passive dashboard and start **actively guiding every role through their duties** — "make their job easier." Two concrete asks: (1) the Live Attendance monitor should be more animated and instantly readable — site-grouped guards with name + contact number, status that flips **green on check-in** and clearly distinguishes **on-time / late / not-checked-in**, and it should **surface which guards a command-centre officer needs to contact**; (2) a broader **cross-role "what should I do now" guidance layer** for command-centre officers, supervisors, and security guards (including patrolling). Confirmed scope decisions (via question): build the full cross-role Action Center this round; the contact flow is **reminder-first** — the system surfaces who/what needs attention and the officer acts ("he will do the rest"), with a one-tap Call link as a convenience but **no mandatory logging or auto-escalation**; guards get their own duty reminders too; the guards-to-contact surface lives on the **Attendance page**.

**Confirmed from code (not assumed):** `Attendance.tsx`'s `GET /live` already returns everything Part A needs — `guard_name`, `guard_phone` (added earlier this session), `site_name`, `scheduled_start/end`, `actual_start/end`, `live_status` (`not_started`/`late`/`checked_in`/`on_break`/`checked_out`), `is_late`, `late_minutes`, `overtime_minutes`, `on_break`, photos, geofence/mock flags. So **Part A is frontend-only** — every new visual state (on-time vs. was-late vs. overdue-not-checked-in) is derivable from fields already present (`scheduled_start` vs. now, `is_late`, `live_status`). `motion.ts` provides `fadeUpSx` (staggered entrance) and `useCountUp` (KPI animation), both already used on this page. `command_centre.py`'s `/overview` already computes site-scoped guards-on-duty, unacked alert counts, offline cameras, and last-checkpoint times — the exact joins the Action Center reuses. `useRealtimeEvents.ts` already invalidates `['attendance-live']` on the `attendance_status_changed` WS event, so check-ins flip green live with no polling. Site scoping via `get_allowed_site_ids`/`site_scope_clause` (Gap 81) applies to every new query.

**Deployment reality (this host):** Docker *image builds* crash the BuildKit builder (confirmed this session), but the running api container + host dev server are stable. So: **frontend changes verify live on the running Vite dev server (port 5174, `frontend-dev` launch config) with zero Docker build**; **backend changes deploy via `docker cp` hot-swap** onto the running `docker-api-1` (the proven no-build path). Mobile changes are code-/tsc-verified only (no emulator this session), same ceiling as every mobile round.

## Part A — Live Attendance: animative redesign + status clarity + contact (FRONTEND ONLY)

**File:** `frontend/src/pages/Attendance.tsx` (no backend change — all fields already served).

- **Status model (derive, don't add columns):** replace the single `live_status → color` map with a richer per-row state computed client-side:
  - `checked_in` + `!is_late` → **On Time** (green `#00E396`, solid).
  - `checked_in` + `is_late` → **Checked In** green chip **plus** a small amber "· was {late_minutes}m late" sub-badge (keeps the green "present" signal but preserves the lateness fact — today it's silently lost once active).
  - `late` (not yet in, past grace) → **Late** amber, **pulsing** glow.
  - `not_started` + now < `scheduled_start` → **Scheduled** grey (calm, nothing wrong yet).
  - `not_started` + now ≥ `scheduled_start` + grace → **Not Checked In** red, **pulsing** — this is the "should be here, isn't" state.
  - `on_break` violet, `checked_out` grey.
- **Animation:** add a `@keyframes` pulse (opacity/box-shadow) for attention rows; keep `fadeUpSx` staggered entrance on site groups + rows; keep `useCountUp` KPIs; add a brief green "flash" ring on a row when its status transitions to checked_in (drive off the WS-invalidated data change — compare previous vs. current status in a `useRef` map). Respect `prefers-reduced-motion` (pulse/flash disabled), consistent with `motion.ts`.
- **Contact:** make `guard_phone` a clear **Call** affordance per row (icon button, `href="tel:…"`) instead of the current plain caption link; show name + phone prominently in each site group.
- **"Action Required — Guards to Contact" panel** (top of page, above the site list, only rendered when non-empty): the reminder surface. Lists every guard currently **Late** or **Not Checked In (overdue)** — guard name, site, scheduled start, how overdue, and a one-tap **Call** button. Reminder-first per the scope decision: **no log/ack/escalate controls** — the officer sees who to contact and calls; the item auto-clears when that guard checks in (data-driven, via the same WS refresh). A subtle count badge ("3 guards need contact") with pulsing accent so it's impossible to miss in a control room.
- Everything reuses existing `GlassCard`, `fadeUpSx`, `useCountUp`, `STATUS_META`, the `checkinPhotoUrl` thumbnails, and the site-grouping `useMemo` already in the file.

## Part B — Cross-Role Action Center (new web page + backend endpoint)

The "guide officers on their duty" surface — one role-aware page that answers *"what needs my attention right now?"* for whoever is logged in.

**Backend — new `backend/app/routers/action_center.py`** (prefix `/api/v1/action-center`, registered in `main.py`), one endpoint `GET /` returning a flat, ranked list of action items. Role-aware, site-scoped (`get_allowed_site_ids`/`site_scope_clause`), reusing `command_centre.py`'s existing joins:
- **Ops roles (1 super-admin, 2 admin, 3 supervisor, 4 operator, 8 manager)** — the control-room/supervisor feed:
  - `contact_guard` — guards Late or overdue-not-checked-in today (same derivation as Part A), each with `guard_phone`. *severity high.*
  - `ack_alert` — unacknowledged `critical`/`high` alerts (reuse the recent-alerts query). *severity critical/high.*
  - `overdue_checkpoint` — active-shift guards whose last checkpoint scan is > threshold old (reuse the `checkpoint_scans`/`patrol_sessions` subquery already in `/overview`). *severity medium.*
  - `pending_approval` — count of pending `attendance_corrections` + `leave_requests` (supervisor/manager/admin only). *severity low.*
  - `camera_offline` — offline camera count per site (reuse the streams-status LATERAL). *severity medium.*
- **Guard role (5 security_guard) — self-duty feed** (`guard_user_id = token.user_id`):
  - `check_in` — today's `scheduled` shift not yet started. *"Check in for your shift at {site}".*
  - `patrol_due` / `overdue_checkpoint` — active shift with a stale/overdue checkpoint. *"Scan your next checkpoint".*
  - `respond_incident` — open incident assigned to this guard (if any). *severity high.*
  - `doc_expiry` — own `employee_documents` expiring ≤ 30 days / expired. *"Renew your {doc}".*
- Each item shape: `{ id, category, severity, title, subtitle, action_route?, phone?, entity_id }`. Response also returns a small `summary` (counts by severity) for the KPI row. Item ranking: critical → high → medium → low, then newest.

**Frontend — new `frontend/src/pages/ActionCenter.tsx`** + `frontend/src/api/actionCenter.ts`:
- Role-aware "Action Center" / "Duty Board" page: `PageHeader` ("Action Center" / "What needs your attention now"), a `useCountUp` KPI strip (Critical / High / Total actions), then the ranked action-item list grouped by category, each card colour-coded by severity with a category icon, the title/subtitle, a **Call** button when `phone` is present, and a navigate-to affordance when `action_route` is set (e.g. `ack_alert` → `/alerts`, `overdue_checkpoint` → `/roster`, `pending_approval` → `/attendance` or `/leave`). Reuse `GlassCard`, `fadeUpSx`, `useCountUp`, `SeverityChip`. `useQuery(['action-center'], …)` with the WS-invalidation pattern (invalidate on `attendance_status_changed` / `alert_created` / relevant events in `useRealtimeEvents.ts`), plus a slow fallback poll.
- `App.tsx` route `/action-center`; `Sidebar.tsx` nav item near the top (this is meant to be a landing surface), gated on `alert:read` for ops roles and always shown for guards (they always have self-duties) — simplest: show to everyone authenticated, since the endpoint self-scopes what each role sees. Icon e.g. `ChecklistIcon`/`TaskAltIcon`.
- No new permission codes — the endpoint's role-awareness is the gate; frontend just renders whatever it returns.

## Part C — Mobile guard duty reminders (mobile; code-/tsc-verified only this session)

Deliver the "remind the guards as well to do their duty, make their job easier" half on the guard-facing app.

- **`mobile/src/api/actionCenter.ts`** (new) — `getMyDuties()` hitting the same `GET /api/v1/action-center` (returns the guard-self feed for a guard token).
- **`mobile/src/screens/DashboardScreen.tsx`** — add a "Your Duties" card at the top: the guard's own pending actions (check in, patrol/checkpoint due, assigned incident, expiring document) as tappable rows that deep-link to the right screen (Shift, Patrol, Incident, My Record) using the tap-to-navigate pattern added earlier this session. Live via the existing WS/`useRealtimeEvents` refresh where wired, else on focus. This makes the app tell each guard what to do next instead of them having to remember.

## Deployment (no-build path — matches this session's proven method)

1. Backend (`action_center.py` new + `main.py` registration): `docker cp` both files into `docker-api-1` at `/app/backend/app/routers/action_center.py` and `/app/backend/app/main.py`, then `docker restart docker-api-1` (uvicorn reloads with the new router; no image build). Verify `GET /health` 200 and the new route responds.
2. Frontend (all Part A + B files): verified live on the **running Vite dev server (port 5174)** — no build. Just save; HMR picks it up.
3. Mobile (Part C): `npx tsc --noEmit` in `mobile/` only.
4. A proper `docker compose build api frontend` to bake images permanently remains a later step for a higher-RAM machine (this host's builder crashes) — not required for the features to run now.

## Verification

1. **Attendance page (live on 5174, aegis / ops@aegis.demo / Demo1234!):** grid shows site-grouped guards with name + Call button; a checked-in-on-time guard is solid green; a checked-in-late guard is green + "was Nm late"; an overdue-not-checked-in guard pulses red; the "Guards to Contact" panel lists exactly the late/overdue guards with working `tel:` Call links, and clears a guard when they check in (simulate by flipping a shift's `actual_start`/`status` in the DB and confirming the row flips green live via WS, panel count drops).
2. **Action Center (web):** as an ops user → shows contact-guard / unacked-alert / overdue-checkpoint / pending-approval / offline-camera items, site-scoped (cross-check a site-restricted user sees only their sites). As a guard user (guard1@aegis.demo) → shows only self-duties (check-in / patrol / doc-expiry), no ops items. Severity ordering correct; Call buttons dial; navigate affordances route correctly.
3. **Backend:** `GET /api/v1/action-center` returns correctly shaped, role-scoped JSON for an ops token vs. a guard token; `curl`/in-container python check like the earlier `GET /users` verification.
4. **Mobile:** `npx tsc --noEmit` clean; DashboardScreen "Your Duties" card renders the guard-self items (code-reviewed; live Expo check deferred, same ceiling as prior mobile rounds).
5. Frontend `npx tsc --noEmit` clean overall; no console errors on the dev server.

## What else the system should do (roadmap — answering "what else we need?")

Prioritized, each building on data the platform already captures:
1. **Auto-escalation on no-contact / no-response** — if a Late guard isn't checked in within N more minutes, auto-notify the supervisor (reuse `alert_routing.py::resolve_push_targets` + `dispatch.py` SLA pattern). Turns reminders into guaranteed follow-through. *(Deferred here per the "officer does the rest" decision, but the natural next step.)*
2. **Shift briefing & handover prompts** — on check-in, show the guard their post orders / site SOP for the shift; on shift end, a structured handover checklist to the next guard (post-orders + site-documents infra already exists, Gap 87).
3. **Geofence-breach & mock-GPS auto-flag on the monitor** — the data is already captured at check-in (`is_within_geofence`, `check_in_is_mock_location`); surface it as an Action Center item ("{guard} checked in outside the geofence / with mock GPS") and auto-open a Violation.
4. **Patrol-route guidance on mobile** — turn "scan next checkpoint" into an ordered route with due-times and overdue nudges (checkpoints/patrol_sessions already modeled).
5. **Real-time attendance push everywhere** — extend the WS attendance events so the Command Centre guards board and the Action Center reflect check-ins instantly (partially there via `attendance_status_changed`).
6. **Configurable duty rules** — make the "overdue" thresholds (checkpoint staleness, not-checked-in grace) tenant-settings-driven (the `tenant_settings` + `config_keys.py` mechanism already exists) instead of hardcoded.
7. **Escalation matrix / on-call chain** — who to contact when the primary guard is unreachable (supervisor → manager), surfaced directly in the contact panel.

---

# Round: Live Wall Multi-Screen Control Room + Real Pop-Out Windows

## Context

The user shared a screenshot of Live Wall in its current "Full Screen" state showing two real bugs — the wall doesn't fit the viewport (dead space / doesn't fill the screen) and camera-name labels visually overlap on some cells — and paired it with a substantial feature request: a real multi-monitor control-room capability. Quoted verbatim: "if live wall multi screen should be ask how many live wall screens required... once i configured the camera in multiple screen should be save how many screens and cameras and which analytics configured each camera next should be open with previous setting based if need more screen more camera need that option to if saved follow the same. Each full screen other models also required separate screen not in same window full screen new window and into full screen. those thing in web and windows application too." This describes: (1) a setup flow that asks how many physical screens a control room needs, (2) per-screen camera + analytics configuration, (3) saving the whole multi-screen set as one named profile that reopens with everything restored, (4) the ability to add more screens/cameras later onto a saved profile, and (5) generalizing "Full Screen" so it opens a genuine separate OS/browser window (not just CSS-fullscreen the current window) on Live Wall **and** Command Centre **and** Action Center, in both the web app and the Windows desktop app. The user closed with "Once finish this i will give you the Visitor management application requirement" — this is a discrete deliverable, not to be run together with that follow-up.

**Confirmed via direct reading, not assumed (this session, this round):**
- The screen-fit bug's root cause: `LiveWall.tsx`'s wall `Box` (`p:3`, line 586) and every cell (`aspectRatio:'16/9'`, filled line 206 / empty line 722) have no height constraint anywhere in the tree — total wall height is purely `rows × (cell-width ÷ 16:9)`, driven bottom-up from content, not top-down from the viewport. Kiosk/focus mode (the already-built `focusMode.ts` store + `AppShell.tsx` conditional chrome from the prior "True Operator Control Room" round) only removes the Sidebar/TopBar — it never touches LiveWall's own layout, so the grid still under- or over-fills whatever space focus mode frees up.
- The overlapping-label bug has a plausible but **unconfirmed** lead: grid cells are keyed by `idx` (line 708), not by `cell.camera_id`, and the auto-pop camera-swap logic (lines 444-454) reuses the same DOM node across swaps with no `Fade`/transition wrapper around the name overlay (lines 241-244) — this needs live reproduction in the browser during implementation to confirm before committing to a fix, not a static-analysis-only guess.
- The existing `wall_layouts` table/router (`backend/alembic/versions/0053_wall_layouts.py`, `backend/app/routers/wall_layouts.py`) is strictly **one saved camera-grid layout at a time** — `cells` is a flat JSONB array of `{camera_id, stream_id, camera_name, site_name}` with no concept of "N layouts grouped and reopened together." This is the exact gap the profile feature closes, and the cleanest way to close it is additive columns on this same table (mirrors this session's own established precedent — `roster_batches`/`roster_draft_shifts`, `payroll_runs`/`payslips`, `invoices`/`invoice_line_items` — a lightweight parent table plus a `parent_id` FK on the existing child rows, not a parallel schema).
- `openLiveWallWindow(layoutId?)` (`frontend/src/lib/liveWallWindow.ts`) and `openAttendanceWindow()` (`frontend/src/lib/attendanceWindow.ts`) are two near-identical Electron-IPC-vs-`window.open()` dual-path helpers — with this round adding Command Centre and Action Center as two more callers, this crosses this session's own established "share once there's a real 2nd/3rd caller" threshold (already invoked for `services/face.py`, `GUARD_ROLES`, etc.) — these should consolidate into one generic helper.
- `desktop/electron/main.js` has two near-duplicate window-tracking blocks (`createLiveWallWindow`/`liveWallWindows` array, `createAttendanceWindow`/`attendanceWindows` array) and a `set-kiosk` IPC handler that **always** operates on the module-level `mainWindow` reference (`if (!mainWindow || mainWindow.isDestroyed()) return false; mainWindow.setKiosk(...)`), never on whichever window actually invoked it. Every secondary window's own local Escape-key handler calls `win.setKiosk()` directly (so kiosk still basically works today), but the exposed `electronAPI.setKiosk()` IPC path is wrong for any secondary window that calls it — a real, pre-existing bug this round must fix as required infrastructure for per-screen Full Screen to work correctly on desktop.
- `ActionCenter.tsx` and `CommandCentre.tsx` have **zero** existing fullscreen/kiosk/pop-out code. Their realtime freshness comes entirely from the single `useRealtimeEvents()` mount in `AppShell.tsx` — but since every popped-out secondary window loads the full app bundle at a different route (a completely separate React render tree, not a view swapped inside the same tree), `AppShell` mounts again independently in that window, so `useRealtimeEvents()` already runs there for free. **No new realtime wiring is needed** for popped-out Command Centre/Action Center windows.
- `frontend/src/api/licenses.ts::getMyEnabledModules()` and `DetectionOverlay.tsx`'s `moduleFilter` prop (both built in the "True Operator Control Room" round) already give a wall-wide "which AI modules to overlay" filter. This round moves that same mechanism to **per-screen** granularity (one level more granular, not per-individual-camera-cell — consistent with that round's own documented scope note: "Per-camera analytics filtering... is not built... easy to extend to per-cell later if asked").

**Design decision — schema (resolves the JSONB-array vs. FK-child-table fork from Phase 1 exploration):** extend `wall_layouts` with three nullable/defaulted columns (`profile_id`, `screen_index`, `analytics_modules`) rather than building a parallel schema. A **standalone** layout (today's existing single-wall-save feature, used as-is by every current caller) keeps `profile_id IS NULL` and is completely unaffected. A **profile screen** is just a `wall_layouts` row with `profile_id` set — the setup wizard's "configure this screen's cameras" step *is* today's single-wall camera/grid builder, run once per screen under one parent umbrella. This reuses 100% of the existing `wall_layouts` CRUD, RLS, and `_MAX_CELLS` validation with zero duplication, and is a smaller, lower-risk migration than a second parallel table.

**Design decision — launch behavior:** clicking "Launch Profile" applies screen 1's layout to the **current** window (not a 5th new window when 4 are requested) and pops out screens 2..N as new windows with a small stagger — matches how an operator actually invokes this (already sitting at one monitor, wants the rest to open around them). On the desktop app, secondary windows are cascaded across available physical displays via Electron's `screen.getAllDisplays()` API when there are more displays than already-open windows, directly answering "multiple sites have monitor with command center control screen" — the whole reason multi-screen exists at all.

## 1. Database — profile grouping on top of the existing `wall_layouts` table

**New migration** (confirm the current Alembic head via `docker exec docker-api-1 python -m alembic heads` before assigning the next revision number — do not hardcode one from memory):
```sql
CREATE TABLE wall_profiles (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    screen_count  INTEGER NOT NULL,
    is_shared     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, name)
);
-- standard RLS triplet, identical to wall_layouts' own policy (0053)

ALTER TABLE wall_layouts
  ADD COLUMN profile_id        UUID REFERENCES wall_profiles(id) ON DELETE CASCADE,
  ADD COLUMN screen_index      INTEGER,
  ADD COLUMN analytics_modules JSONB NOT NULL DEFAULT '[]';
CREATE INDEX idx_wall_layouts_profile ON wall_layouts(profile_id, screen_index) WHERE profile_id IS NOT NULL;
```
`screen_count` is denormalized (also derivable via `COUNT(*)` over child rows) purely so the profile list view can show "4 screens" without a join — same convenience-denormalization already used for `roster_batches.rules_summary`.

## 2. Backend — `wall_profiles` router

**New `backend/app/routers/wall_profiles.py`** (prefix `/api/v1/wall-profiles`, gated `camera:read` throughout — same permission `wall_layouts.py` already uses, since a profile is still personal display configuration, not a management-tier concept), registered in `main.py`:

- `GET /` — my profiles + `is_shared=true` ones, each with its screens nested (one query, `json_agg` of the child `wall_layouts` rows ordered by `screen_index`) — same nested-aggregation shape as `roster.py::get_batch`.
- `POST /` — body `{name, is_shared, screens: [{grid_size, cells, analytics_modules}, ...]}` (2-8 screens — see §4 for the cap rationale). In one transaction: insert the `wall_profiles` row (`screen_count = len(screens)`), then insert one `wall_layouts` row per screen with `profile_id` set, `screen_index = i`, and an internal name (`f"{profile.name} — Screen {i+1}"`) so each screen is still a fully valid, independently-openable layout through the existing `openLiveWallWindow` path.
- `PUT /{id}` — update name/is_shared; replace all screens (delete + recreate the child `wall_layouts` rows in the same transaction — simplest given the small N, matches `roster.py`'s draft-shift replace-on-edit precedent rather than a diffing update).
- `POST /{id}/screens` — append one more screen to an existing profile (the "need more screens later" requirement), increments `screen_count`. This is what makes "add more screens/cameras later while preserving the saved config" real without forcing a full profile edit.
- `DELETE /{id}` — cascades to child `wall_layouts` rows via the FK.
- Reuse `wall_layouts.py`'s existing `_fetch_editable` (404-not-403 ownership check) and `_MAX_CELLS` constant directly rather than re-deriving them.

## 3. Frontend — fix the two visible Live Wall bugs

**File:** `frontend/src/pages/LiveWall.tsx`.

- **Screen-fit fix**: replace the MUI `Grid`+`aspectRatio` cell layout with a CSS-grid wall that is height-constrained top-down instead of content-driven bottom-up — wrap the wall in a `Box` with `height: 'calc(100vh - <toolbar height>)'` (or `100%` inside an already-height-constrained flex ancestor when in focus mode) and `overflow: 'hidden'`, and change the grid container to `display: 'grid', gridTemplateColumns: repeat(cols, 1fr), gridTemplateRows: repeat(rows, 1fr)` so every cell fills its allotted fraction of the actual viewport. Each cell's video content (`<img>`/canvas) uses `objectFit: 'contain'` internally so the 16:9 source doesn't distort — the aspect ratio moves from "drives total layout height" to "constrains content within an already-sized cell." Reduce/zero the outer `p:3` padding specifically when `focusMode` is active (read from the existing `focusMode.ts` store) so kiosk mode actually reaches edge-to-edge.
- **Overlapping-label fix**: reproduce live in the browser first (open Live Wall, trigger the auto-pop camera-swap path, watch for the overlap) to confirm the lead before fixing. If confirmed: key each grid cell by a stable identity (`cell.camera_id ?? \`empty-${idx}\`` instead of bare `idx`, line 708) so React unmounts/remounts the cell (and its label) on a camera swap instead of morphing text in place, and wrap the name overlay (lines 241-244) in a `Fade` keyed the same way so a swap is a clean cross-fade rather than a flash of overlapping text.

## 4. Frontend — "how many screens" setup wizard + per-screen editor

**Extract** the existing single-wall camera-picker/grid-size/analytics-chip UI out of `LiveWall.tsx` into a reusable `frontend/src/components/common/WallScreenEditor.tsx` (`props: { screen: {gridSize, cells, analyticsModules}, onChange }`) — behavior-neutral extraction, verify the standalone Live Wall page still works identically before building anything new on top of it. This is what lets both the existing single-wall builder and the new wizard share one implementation instead of forking it.

**New `frontend/src/components/common/WallProfileSetupDialog.tsx`**:
1. Step 1 — "How many screens do you need?" numeric stepper, 1-8 (8 is a generous real-world control-room monitor ceiling; "add more later" via the `POST /{id}/screens` endpoint covers anyone who genuinely needs more, so this cap isn't a hard wall).
2. Step 2 — one `WallScreenEditor` per screen (tabbed or stepper-through UI), analytics selection sourced from `getMyEnabledModules()` (same licensed-module chip list `LiveWall.tsx` already uses wall-wide, scoped per screen here).
3. Finish → `createWallProfile(...)`.

**`LiveWall.tsx` additions**: a "Multi-Screen Setup" entry point (icon button near the existing layout `Select`) opening the wizard; extend the layout dropdown to also list saved profiles (visually grouped "Layouts" vs. "Screen Profiles" — `ListSubheader`). Selecting a profile calls a new `openWallProfile(profile)`: applies screen 1's layout to the current window's state, then loops `openLiveWallWindow(screen.id)` for screens 2..N with a ~250ms stagger between each call (avoids simultaneous popup-blocker/window-manager contention).

## 5. Generalize pop-out windows + Full Screen to Command Centre and Action Center

**New `frontend/src/lib/popoutWindow.ts`** — consolidates `liveWallWindow.ts` + `attendanceWindow.ts`'s duplicated Electron-IPC-vs-`window.open()` pattern into one `openInNewWindow(routePath: string, electronChannel: string, ipcArg?: unknown)` helper. `liveWallWindow.ts`/`attendanceWindow.ts` become thin wrappers calling it (behavior-neutral refactor, verify both existing pop-out flows still work before adding the two new callers).

**New `frontend/src/hooks/useKioskToggle.ts`** — extracts `LiveWall.tsx`'s existing `toggleKiosk` (Electron `setKiosk` IPC / Fullscreen API + `setFocusMode` from the already-built `focusMode.ts` store) into a shared hook, since it now has 3 call sites instead of 1.

**`CommandCentre.tsx`**: add a "Full Screen" button (via `useKioskToggle`) and a pop-out-to-new-window button (via `openInNewWindow('/command-centre', 'open-command-centre-window')`) to the page header, next to the existing controls.

**`ActionCenter.tsx`**: same two additions in its `PageHeader` area. No realtime-wiring changes needed (confirmed in Context above — `AppShell`'s `useRealtimeEvents()` mount is per-window already).

## 6. Desktop (Electron) changes

**`desktop/electron/main.js`**:
- Replace the two near-duplicate `createLiveWallWindow`/`createAttendanceWindow` functions and their two separate tracking arrays with one generic `createSecondaryWindow(routePath, options)` plus a single `secondaryWindows` array — each still gets its own kiosk-capable `BrowserWindow` and its own Escape-key handler, just via one shared function instead of two copies.
- Add two new IPC handlers, `open-command-centre-window` and `open-action-center-window`, both trivial calls into `createSecondaryWindow('/command-centre', ...)` / `createSecondaryWindow('/action-center', ...)`.
- **Fix the `set-kiosk` bug**: change the handler from unconditionally targeting `mainWindow` to resolving the calling window via `BrowserWindow.fromWebContents(event.sender)`, so every secondary window's own Full Screen button correctly kiosks *that* window instead of silently trying to kiosk `mainWindow`. This is a required fix, not optional polish — it's what makes per-screen/per-window Full Screen actually correct once there are more windows in play.
- **Multi-display placement**: when `createSecondaryWindow` is invoked as part of a profile launch (screens 2..N), use `screen.getAllDisplays()` to place each new window on a distinct physical display when more displays than already-open windows exist (cascading offset fallback otherwise) — this is the concrete answer to "multiple sites have monitor with command center control screen," letting a real multi-monitor control room auto-arrange itself.

**`desktop/electron/preload.js`**: expose `openCommandCentreWindow`/`openActionCenterWindow` alongside the existing `openLiveWallWindow`/`openAttendanceWindow` in the `electronAPI` bridge.

## Scope guardrails (explicitly not doing)

- No per-individual-camera-cell analytics override within a screen this round — analytics selection is per-screen, one step more granular than the existing wall-wide filter, matching the "True Operator Control Room" round's own documented "easy to extend to per-cell later if asked" note.
- No changes to how a *standalone* (non-profile) saved layout works — `profile_id IS NULL` rows are the existing feature, untouched.
- Mobile is not in scope — the user's ask was explicitly "in web and windows application," and mobile has no Live Wall multi-camera grid or Command Centre/Action Center equivalent to generalize this to.

## Build sequence

1. Migration: `wall_profiles` table + 3 new `wall_layouts` columns; confirm head via `alembic heads` first.
2. Backend: `wall_profiles.py` router, registered in `main.py`.
3. Frontend bug fixes first (screen-fit CSS-grid rework, then live-reproduce and fix the label-overlap issue) — these are visible, standalone wins independent of everything else.
4. Extract `WallScreenEditor.tsx` from `LiveWall.tsx` (behavior-neutral, verify unchanged before proceeding).
5. `WallProfileSetupDialog.tsx` + `api/wallLayouts.ts` (or a new `api/wallProfiles.ts`) additions + `openWallProfile` wiring in `LiveWall.tsx`.
6. `popoutWindow.ts` consolidation (behavior-neutral refactor of the 2 existing helpers) + `useKioskToggle.ts` extraction, then wire both into `CommandCentre.tsx`/`ActionCenter.tsx`.
7. Desktop: `createSecondaryWindow` generalization, the two new IPC channels, the `set-kiosk` `fromWebContents` fix, multi-display placement, `preload.js` exposure.
8. `npx tsc --noEmit` clean, live-verify per below, then a desktop rebuild (`cd desktop && npm run dist`) to ship everything to the Windows exe.

## Verification

1. Live Wall fills the actual viewport edge-to-edge in focus/kiosk mode with no dead space, at 2×2/3×3/4×4 grid sizes and at a few different browser-window sizes; video content stays undistorted inside each cell.
2. Trigger the auto-pop camera-swap path repeatedly → no visual overlap of camera-name labels (confirms whichever fix was actually needed after live reproduction).
3. "Multi-Screen Setup" → set 3 screens → configure distinct cameras + distinct analytics chips per screen → Save → the profile appears in the layout dropdown's "Screen Profiles" group.
4. Select and launch that profile → current window shows Screen 1's cameras/analytics; two new windows open (staggered, not simultaneous) each showing their own screen's cameras/analytics correctly.
5. Reopen the app later, launch the same saved profile again → all 3 screens restore with the exact same cameras/analytics as saved, with zero reconfiguration.
6. Add a 4th screen to the existing profile via the "add more screens" flow → original 3 screens' config is preserved, new 4th screen configurable and launches alongside the rest next time.
7. Command Centre and Action Center each get a working Full Screen button (chrome hidden, edge-to-edge) and a working pop-out button (opens a genuine second browser tab/window at that route, independently receiving realtime WS updates).
8. Desktop (`cd desktop && npm run dist`, launch the rebuilt exe): repeat checks 1, 4, and 7 inside Electron; confirm each secondary window's own Full Screen button kiosks *that* window (not the main window) — the `set-kiosk` fix; on a multi-monitor test machine (or simulated via `screen.getAllDisplays()` logging if only one physical display is available), confirm profile-launch windows distribute across available displays.
9. `npx tsc --noEmit` clean; `docker compose build api frontend` clean (or the established `docker cp` hot-swap path if the image builder is still unstable on this host, per the prior round's documented fallback).
