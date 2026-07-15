# Security Controls Inventory — Seventh AI Vision

**Version:** 1.0  
**Last reviewed:** 2026-07-02  
**Owner:** Security / Platform Engineering

This document inventories the technical and organisational security controls built into the platform. Use it as the Technical and Organisational Measures (TOMs) annex to Data Processing Agreements and as the basis for penetration test scope definition.

---

## 1. Authentication

| Control | Implementation | Configuration |
|---------|---------------|--------------|
| Password hashing | bcrypt, cost factor 12 (`passlib[bcrypt]`) | Non-configurable minimum |
| JWT access tokens | HS256 (or RS256 with key rotation); 15-minute TTL | `JWT_SECRET_KEY_CURRENT`, `JWT_ACTIVE_KID` |
| JWT refresh tokens | 7-day TTL; single-use (rotation on each `/auth/refresh`); stored as hash, not plaintext | `REFRESH_TOKEN_EXPIRE_DAYS` |
| JWT key rotation | Multi-key `kid`-based verification; old key retained during transition window | `JWT_SECRET_KEY_PREVIOUS`, `JWT_PREVIOUS_KID` |
| 2FA (TOTP) | RFC 6238 TOTP (pyotp); 30-second window; QR code via `/api/v1/2fa/setup` | Per-user opt-in; admin-enforceable |
| Account lockout | 5 failed login attempts → 15-minute lockout; tracked in `user_lockouts` table | `LOGIN_MAX_ATTEMPTS`, `LOCKOUT_MINUTES` |
| Session management | Active sessions listed at `GET /api/v1/sessions`; admin can terminate any session | User-visible; admin-terminable |

---

## 2. Authorisation

| Control | Implementation |
|---------|---------------|
| Role-Based Access Control | 6 roles (super_admin, admin, supervisor, operator, security_guard, viewer) |
| Permission codes | 40+ fine-grained codes; `require_permission()` FastAPI dependency on every mutating endpoint |
| Row-Level Security | PostgreSQL `FORCE ROW LEVEL SECURITY`; every tenant-scoped table enforces `app.current_tenant` GUC |
| Cross-tenant isolation | RLS policy blocks all cross-tenant reads/writes at the DB layer, regardless of application code |
| Privilege separation | `get_raw_db()` (no RLS) used only by super_admin endpoints; distinct from `get_db_with_tenant()` |
| Module licensing | Per-tenant AI module gates via `tenant_module_licenses`; unlicensed module access returns 422 |
| IP allowlist | `ip_allowlist` table; `GET /api/v1/ip-allowlist`; enforced as middleware | 

---

## 3. Data Protection

| Control | Implementation |
|---------|---------------|
| Data in transit | TLS 1.2+ required; all API, WebSocket, and MJPEG endpoints served over HTTPS in production |
| Data at rest | **[CUSTOMER]** Volume encryption (LUKS/dm-crypt on Linux, BitLocker on Windows) at the OS level |
| Evidence file checksums | SHA-256 checksum stored in `evidence.checksum_sha256`; validates file integrity |
| PDPA masking | `GET /api/v1/pdpa/mask` partially obfuscates NRIC/FIN/passport in display strings |
| No PII in URLs | Query parameters carry only UUIDs and filter values; no PII in URL paths |
| Secret management | All credentials via environment variables; never hardcoded; `.env.example` contains placeholders only |
| Face embedding storage | 512-dimensional ArcFace vectors stored as `vector(512)` (pgvector); no raw face images in DB |
| Evidence retention | Auto-purged by scheduler per `evidence.retention_days` tenant setting (default 90 days) |
| Audit log archival | Audit log partitions archived (not dropped); 7-year default retention |

---

## 4. Network Security

| Control | Implementation |
|---------|---------------|
| Rate limiting | `slowapi` (Redis-backed); 5/minute on auth endpoints; 100/minute global per IP |
| IP allowlist | Middleware blocks requests from non-allowlisted IPs when list is non-empty |
| CORS | Configurable `ALLOWED_ORIGINS`; defaults to `localhost` in development |
| WebSocket auth | JWT token required as query param before `ws.accept()`; unauthorised connections rejected at upgrade |
| Docker network isolation | API and AI workers on separate Docker networks; Redis/Postgres not exposed on host in production Compose |
| Kubernetes network policy | Helm chart defines `NetworkPolicy` resources restricting inter-pod communication |

---

## 5. Audit Logging

| Control | Implementation |
|---------|---------------|
| Scope | All authentication events, all data mutations, all permission-checked actions |
| Fields captured | `user_id`, `action`, `resource_type`, `resource_id`, `ip_address`, `detail` (JSONB), `created_at` |
| Tenant isolation | `audit_logs` table is RLS-protected; super_admin can query across tenants via `get_raw_db()` |
| Immutability | No `DELETE` or `UPDATE` endpoint exists for audit logs; rows are append-only |
| Retention | Partitioned by month (pg_partman); partitions archived per `audit.retention_years` (default 7) |
| Tamper detection | **[CUSTOMER]** Consider write-once S3 (Object Lock) or WORM storage for the archive exports |

---

## 6. Vulnerability Management

| Control | Status | Recommendation |
|---------|--------|----------------|
| Dependency pinning | Implemented | `pyproject.toml` locked versions (`uv.lock` / `poetry.lock`) |
| Known CVE scanning | Partial | Run `pip-audit` in CI pipeline; add to GitHub Actions / GitLab CI |
| Container image scanning | Not implemented | Add Trivy or Snyk scan step to build pipeline |
| DAST / Penetration test | Not implemented | Schedule annual external pentest; scope: API endpoints + WebSocket + auth flows |
| SAST | Partial | Pydantic input validation; parameterised SQL; no `eval`/`exec` in application code |
| Secrets scanning | Not implemented | Add `detect-secrets` / `trufflehog` pre-commit hook |

---

## 7. Incident Response

**Severity levels:**

| Level | Definition | Response time |
|-------|-----------|---------------|
| P1 — Critical | Active breach, data exfiltration suspected, production down | 15 minutes |
| P2 — High | Unauthorised access, multiple failed logins from suspicious IP | 1 hour |
| P3 — Medium | Anomalous alert pattern, non-critical service degraded | 4 hours |
| P4 — Low | Informational security event, no evidence of impact | 24 hours |

**Response steps:**
1. Detect via Prometheus alert, audit log anomaly, or user report
2. Contain — isolate affected service; revoke compromised credentials via `DELETE /api/v1/sessions/{id}`
3. Investigate — pull audit logs (`GET /api/v1/audit`) for the time window; review Redis pub/sub event history
4. Eradicate — patch vulnerability; rotate JWT keys; reset affected user passwords
5. Recover — `docs/UPGRADE.md` rollback procedure if a bad release caused the incident
6. Post-incident review — within 5 business days; update this document if new controls are warranted
7. Notify — PDPC within 3 days / DPA supervisory authority within 72 hours if personal data is affected

---

## 8. Hardening Checklist

### Production Docker Compose
- [ ] Set `POSTGRES_PASSWORD` to a strong random value (≥ 32 chars)
- [ ] Set `JWT_SECRET_KEY_CURRENT` to a strong random value (≥ 64 chars)
- [ ] Change `GRAFANA_ADMIN_PASSWORD` from default `admin`
- [ ] Remove `ports:` mappings for postgres and redis (not needed externally)
- [ ] Mount `evidence_data` volume on an encrypted filesystem
- [ ] Enable Docker Content Trust (`DOCKER_CONTENT_TRUST=1`)

### Kubernetes / Helm
- [ ] Use `SealedSecrets` or HashiCorp Vault for secrets; do NOT store plaintext in `values.yaml`
- [ ] Enable PodSecurityAdmission: `restricted` namespace policy
- [ ] Set `readOnlyRootFilesystem: true` on all containers except evidence writer
- [ ] Set `runAsNonRoot: true` and `runAsUser: 1000`
- [ ] Apply `NetworkPolicy` to restrict inter-service communication
- [ ] Enable Kubernetes audit logging at the API server level

### Operating System (Docker Host)
- [ ] Apply monthly OS security patches
- [ ] Enable UFW / iptables; allow only 443 (HTTPS) and 22 (SSH from jump host)
- [ ] Install EDR agent (CrowdStrike, SentinelOne, or equivalent)
- [ ] Enable file integrity monitoring (AIDE, Wazuh)
- [ ] Configure `docker daemon.json`: `"userns-remap": "default"`, `"no-new-privileges": true`
