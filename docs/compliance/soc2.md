# SOC 2 Type II Readiness — Seventh AI Vision

**Framework:** AICPA Trust Service Criteria (TSC) 2017 (updated 2022)  
**Relevant Trust Service Categories:** Security (CC), Availability (A), Confidentiality (C)  
**Note:** This document describes the platform's built-in controls relevant to a SOC 2 examination. A formal SOC 2 report requires engagement with a licensed CPA firm. Customers seeking SOC 2 attestation must implement the complementary user entity controls (CUECs) listed in each section.

---

## CC1 — Control Environment

| Criteria | Evidence / Control |
|----------|-------------------|
| CC1.1 – Commitment to integrity | Code of conduct enforced via RBAC; audit logging of all privileged actions |
| CC1.2 – Board oversight | **[CUEC]** Governance documentation; risk committee minutes |
| CC1.3 – Organisational structure | Role hierarchy: super_admin > admin > supervisor > operator > security_guard > viewer |
| CC1.4 – Commitment to competence | Guard training module with certification tracking (`/api/v1/training`) |
| CC1.5 – Accountability | Every action in `audit_logs` carries `user_id`, IP address, resource, and timestamp |

---

## CC2 — Communication and Information

| Criteria | Evidence / Control |
|----------|-------------------|
| CC2.1 – COSO principle 13: uses relevant information | Prometheus metrics + Grafana dashboards; real-time WebSocket event push |
| CC2.2 – Internal communication | Alert → incident workflow with role-based notifications (email, SMS, webhook) |
| CC2.3 – External communication | `GET /api/v1/system/version` for operational transparency; PDPA/GDPR notices |

---

## CC3 — Risk Assessment

| Criteria | Evidence / Control |
|----------|-------------------|
| CC3.1 – Risk assessment | **[CUEC]** Annual risk register maintained by customer |
| CC3.2 – Risk identification | Threat detection (intrusion, weapon, fire) feeds risk-event queue |
| CC3.3 – Risk analysis | Severity ratings on alerts (info/low/medium/high/critical); SLA escalation |
| CC3.4 – Risk mitigation | Auto-escalation scheduler; dispatch + incident management workflow |

---

## CC4 — Monitoring Controls

| Criteria | Evidence / Control |
|----------|-------------------|
| CC4.1 – Ongoing evaluations | Prometheus scrapes all services every 15s; Grafana alerts on metric thresholds |
| CC4.2 – Deficiency evaluation | Alert auto-escalation after configurable SLA window; incident resolution tracking |

---

## CC5 — Control Activities

| Criteria | Evidence / Control |
|----------|-------------------|
| CC5.1 – Selects and develops control activities | RBAC `require_permission()` enforced on every mutating endpoint; RLS at DB layer |
| CC5.2 – Selects and develops technology controls | 2FA (TOTP), JWT with rotating keys, bcrypt (cost 12), TLS 1.2+ |
| CC5.3 – Policies and procedures | This document set; `docs/compliance/security-controls.md` |

---

## CC6 — Logical and Physical Access Controls

| Criteria | Evidence / Control |
|----------|-------------------|
| CC6.1 – Logical access security | JWT authentication; refresh token rotation; token revocation on logout |
| CC6.2 – Prior to issuing system credentials | Admin creates users; role assigned at creation; no self-registration |
| CC6.3 – Role-based access | 6 RBAC roles; 40+ permission codes; `role_permissions` enforced in FastAPI dependencies |
| CC6.4 – Access removal | User deactivation (`is_active=FALSE`); active session termination endpoint |
| CC6.5 – Physical access | **[CUEC]** Data centre/server room physical access controls |
| CC6.6 – Logical access from outside | IP allowlist (`/api/v1/ip-allowlist`); rate limiting (5/min login, 100/min general); 2FA |
| CC6.7 – Restrict access to information | Row-Level Security; `audit:read` permission-gated audit log access |
| CC6.8 – Malicious software prevention | **[CUEC]** Host AV/EDR; Docker image scanning (Trivy/Snyk recommended) |

---

## CC7 — System Operations

| Criteria | Evidence / Control |
|----------|-------------------|
| CC7.1 – Vulnerability detection | **[CUEC]** `pip-audit` in CI; Dependabot; annual penetration test |
| CC7.2 – Monitor infrastructure and software | Prometheus metrics for all 16+ services; DCGM exporter for GPU nodes |
| CC7.3 – Evaluate security events | Alert severity triage; SLA escalation; incident management workflow |
| CC7.4 – Respond to security incidents | Dispatch module; SOS panic button; incident status workflow (open→in_progress→resolved→closed) |
| CC7.5 – Recovery from identified incidents | Evidence chain-of-custody; audit log integrity (7-year archival); UPGRADE.md rollback runbook |

---

## CC8 — Change Management

| Criteria | Evidence / Control |
|----------|-------------------|
| CC8.1 – Authorise and design changes | Semantic versioning; Alembic migrations with `upgrade()` + `downgrade()`; git-tagged releases |
| CC8.1 – Test changes prior to implementation | 1029 automated tests; smoke test gate before deployment |
| CC8.1 – Deploy changes | `docs/UPGRADE.md` step-by-step runbook with rollback procedure |

---

## CC9 — Risk Mitigation

| Criteria | Evidence / Control |
|----------|-------------------|
| CC9.1 – Risk mitigation activities | Multi-camera alert correlation; crowd density breach cooldowns; de-duplication |
| CC9.2 – Business disruption risk | Docker Compose HA configuration; Kubernetes HPA; Redis Streams crash-recovery (XPENDING/XCLAIM) |

---

## A1 — Availability

| Criteria | Evidence / Control |
|----------|-------------------|
| A1.1 – Capacity planning | Kubernetes HPA (configured in Helm values); Prometheus capacity metrics |
| A1.2 – Environmental threats | Fire/smoke detection AI module feeds into physical threat response |
| A1.3 – Backup and recovery | **[CUEC]** pg_dump / WAL streaming backup; tested restore procedure |

---

## C1 — Confidentiality

| Criteria | Evidence / Control |
|----------|-------------------|
| C1.1 – Identifies and maintains confidential info | Biometric face embeddings marked as sensitive; face recognition permission-gated |
| C1.2 – Disposes of confidential information | Retention-based evidence purge; `DELETE /api/v1/watchlist/faces/{id}` for embeddings |

---

## Complementary User Entity Controls (CUECs)

The following controls are **not implemented in the platform** and must be maintained by the deploying organisation:

1. Background screening of personnel with access to the system
2. Physical security of servers/data centres
3. OS-level hardening of Docker hosts
4. Network perimeter controls (firewall rules, WAF)
5. Host-level antivirus/EDR
6. Database backup (pg_dump) and tested restore
7. Change advisory board (CAB) process for production changes
8. Annual penetration testing by a qualified firm
9. Formal risk register and risk acceptance process
10. Business continuity / disaster recovery plan and annual test
