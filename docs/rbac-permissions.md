# RBAC Permissions

## Roles

| ID | Code | Typical user |
|----|------|-------------|
| 1 | `super_admin` | Platform operator — cross-tenant access, full control |
| 2 | `admin` | Tenant administrator — full control within their tenant |
| 3 | `supervisor` | Site supervisor — read/write operations, no user management |
| 4 | `operator` | Security operator — monitors alerts and dispatches guards |
| 5 | `security_guard` | On-site guard — patrols, scans, DOB entries, SOS |
| 6 | `viewer` | Read-only — dashboards and reports, no write access |
| 7 | `client_viewer` | External client portal — scoped read-only access to own tenant data |

---

## Permission Grant Matrix

`✓` = granted &nbsp; `—` = denied

| Permission | super_admin | admin | supervisor | operator | security_guard | viewer | client_viewer |
|------------|:-----------:|:-----:|:----------:|:--------:|:--------------:|:------:|:-------------:|
| **Cameras** | | | | | | | |
| `camera:create` | ✓ | ✓ | ✓ | — | — | — | — |
| `camera:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `camera:update` | ✓ | ✓ | ✓ | — | — | — | — |
| `camera:delete` | ✓ | ✓ | — | — | — | — | — |
| **Users** | | | | | | | |
| `user:create` | ✓ | ✓ | — | — | — | — | — |
| `user:read` | ✓ | ✓ | ✓ | — | — | — | — |
| `user:update` | ✓ | ✓ | — | — | — | — | — |
| `user:delete` | ✓ | ✓ | — | — | — | — | — |
| `role:manage` | ✓ | — | — | — | — | — | — |
| `tenant:manage` | ✓ | — | — | — | — | — | — |
| **Settings** | | | | | | | |
| `settings:read` | ✓ | ✓ | — | — | — | — | — |
| `settings:write` | ✓ | ✓ | — | — | — | — | — |
| **Detections** | | | | | | | |
| `detection:read` | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| **Alerts** | | | | | | | |
| `alert:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `alert:acknowledge` | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `alert:create` | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `alert:dedup:manage` | ✓ | ✓ | — | — | — | — | — |
| **Incidents** | | | | | | | |
| `incident:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `incident:create` | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `incident:update` | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `incident:resolve` | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `incident:assign` | ✓ | ✓ | ✓ | — | — | — | — |
| `incident:dispatch` | ✓ | ✓ | ✓ | ✓ | — | — | — |
| **Evidence & Audit** | | | | | | | |
| `evidence:read` | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| `evidence:custody:read` | ✓ | ✓ | ✓ | — | — | — | — |
| `audit:read` | ✓ | ✓ | — | — | — | — | — |
| **Watchlists & Zones** | | | | | | | |
| `watchlist:manage` | ✓ | ✓ | ✓ | — | — | — | — |
| `zone:manage` | ✓ | ✓ | ✓ | — | — | — | — |
| **Sites & Recordings** | | | | | | | |
| `site:manage` | ✓ | ✓ | ✓ | — | — | — | — |
| `recording:create` | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `recording:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| **Licensing** | | | | | | | |
| `license:manage` | ✓ | — | — | — | — | — | — |
| **Guard Operations** | | | | | | | |
| `shift:manage` | ✓ | ✓ | ✓ | — | — | — | — |
| `shift:read` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `patrol:manage` | ✓ | ✓ | ✓ | — | — | — | — |
| `patrol:read` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `patrol:scan` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `dob:write` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `dob:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `guard:sos` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `session:manage` | ✓ | ✓ | — | — | — | — | — |
| `handover:create` | ✓ | ✓ | ✓ | — | ✓ | — | — |
| `handover:read` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `scan:create` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| **Visitor Management** | | | | | | | |
| `visitor:manage` | ✓ | ✓ | ✓ | — | — | — | — |
| `visitor:read` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `visitor:checkin` | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| **Privacy & Compliance** | | | | | | | |
| `privacy:manage` | ✓ | ✓ | — | — | — | — | — |
| `pdpa:admin` | ✓ | ✓ | — | — | — | — | — |
| `pdpa:read` | ✓ | ✓ | ✓ | — | — | — | — |
| **SLA** | | | | | | | |
| `sla:manage` | ✓ | ✓ | — | — | — | — | — |

---

## Enforcement

**Backend**: every protected route declares `Depends(require_permission("code"))`. The dependency resolves the caller's `role_id` from their JWT, looks up `role_permissions`, and raises `HTTP 403` if the code is absent.

**Frontend**: `usePermission(code)` reads `role_id` from the decoded JWT and checks a local mirror of the matrix. UI elements (buttons, nav items, pages) are hidden when the permission is absent. The backend is still the authoritative enforcement point — the frontend gate is UX-only.

**RLS**: tenant isolation is enforced at the database layer independently of RBAC. A misconfigured permission cannot leak cross-tenant data because Postgres's `FORCE ROW LEVEL SECURITY` rejects any query whose session GUC (`app.current_tenant`) doesn't match the row's `tenant_id`.

---

## Adding a New Permission

1. Add a row to the `permissions` table seed in `0001_initial_schema.py` (or a new migration).
2. Insert the appropriate `role_permissions` rows.
3. Add the code to `frontend/src/hooks/usePermission.ts` ROLE_PERMISSIONS map.
4. Gate the new endpoint with `Depends(require_permission("new:code"))`.
5. Add the code to this document.
