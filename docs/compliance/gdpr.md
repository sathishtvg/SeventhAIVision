# GDPR Compliance — Seventh AI Vision

**Regulation:** Regulation (EU) 2016/679 (General Data Protection Regulation)  
**Applicability:** Any deployment where personal data of EU/EEA data subjects is processed.  
**Last reviewed:** 2026-07-02

---

## Article 30 — Record of Processing Activities (RoPA)

| Field | Detail |
|-------|--------|
| **Controller name** | [Customer organisation name] |
| **DPO contact** | [DPO email / Data Protection Officer details] |
| **System name** | Seventh AI Vision v1.0 |
| **Processing purpose** | Physical security monitoring, access control, intruder/PPE/fire/weapon detection, licence plate recognition, face recognition of enrolled individuals, visitor management |
| **Legal basis** | Art. 6(1)(f) Legitimate interests (security of persons and property); Art. 6(1)(c) Legal obligation (statutory security requirements for critical infrastructure operators) |
| **Special-category basis** | Art. 9(2)(g) Substantial public interest — biometric face recognition (where enabled), processed only for identity verification of enrolled watchlist persons |
| **Data subjects** | Employees, contractors, visitors, members of the public captured on CCTV |
| **Categories of personal data** | Face images and biometric embeddings (512-d ArcFace vectors); vehicle licence plates; behaviour/movement patterns; GPS track of guard personnel; visitor PII (name, NRIC/passport, host, purpose) |
| **Recipients** | Authorised security personnel within the organisation; law enforcement on lawful request |
| **Third-country transfers** | None by default. Data is stored on-premises or in the customer's own cloud region. If MinIO/S3 is configured to an external provider, a GDPR-compliant data processing agreement and appropriate safeguards (adequacy decision or SCCs) are required. |
| **Retention** | Configurable per-tenant (default 90 days); see `evidence.retention_days` tenant setting. Face embeddings retained until watchlist entry is deactivated or expires. Audit logs retained for compliance period (default 7 years, configurable `audit.retention_years`). |
| **Security measures** | Row-Level Security (PostgreSQL RLS); AES-256 encryption at rest (volume/disk level); TLS 1.2+ in transit; JWT authentication with rotating signing keys; RBAC; 2FA (TOTP); audit logging of all access and changes |

---

## Lawful Bases

### Legitimate Interests (Art. 6(1)(f))
The primary lawful basis for standard CCTV analytics (intrusion, crowd, fire/smoke, weapon detection).  
Legitimate interests assessment (LIA) must be documented by the controller confirming:
- Purpose: protecting the physical security of premises and people on site
- Necessity: CCTV analytics are necessary and proportionate to achieve the security objective
- Balancing: reasonable expectation of monitoring in controlled-access premises; signage posted

### Legal Obligation (Art. 6(1)(c))
Applicable where national law mandates security measures (e.g. critical infrastructure, financial institutions, healthcare facilities).

### Consent (Art. 6(1)(a))
Not recommended as primary basis for CCTV; impractical for members of the public. Consent IS required for optional biometric face recognition of visitors (Art. 9(2)(a)) unless another Art. 9(2) ground applies.

---

## Data Minimisation & Purpose Limitation

- **Face recognition** must be disabled (`ai_modules_enabled`) for cameras where biometric processing lacks a lawful Art. 9 basis.
- **LPR**: plates of non-watchlisted vehicles are logged in `lpr_events` but trigger no alert. If local law prohibits blanket plate logging without suspicion, disable LPR or configure the watchlist to block-list-only mode.
- **Evidence snapshots**: JPEG evidence is retained per the `evidence.retention_days` tenant setting. Shorter retention (30 days) is recommended unless operational or legal requirements mandate longer.
- **Visitor data**: NRIC/passport numbers stored in `visitors` table. Minimise to what is strictly needed; mask display in UI using the built-in PDPA masking feature (`GET /api/v1/pdpa/mask`).

---

## Data Subject Rights (Articles 15–22)

See `docs/compliance/data-subject-rights.md` for the full procedure.

| Right | Art. | System support |
|-------|------|----------------|
| Right of access | 15 | `POST /api/v1/data-compliance/dsr-export/{user_id}` |
| Right to rectification | 16 | `PUT /api/v1/users/{id}` (user record); face watchlist entry update |
| Right to erasure | 17 | User deactivation + manual evidence file deletion; `DELETE /api/v1/watchlist/faces/{id}` |
| Right to restriction | 18 | Deactivate camera modules; consult DPO for partial restriction |
| Right to portability | 20 | DSR export (JSON) via data-compliance API |
| Right to object | 21 | Disable specific AI modules on specific cameras (`PUT /api/v1/cameras/{id}`) |
| Rights re. automated decisions | 22 | No fully automated decisions with legal/significant effect. Alerts trigger human review. |

---

## Data Breach Notification (Articles 33–34)

- **Art. 33**: Notify supervisory authority within **72 hours** of becoming aware of a personal data breach. Use `GET /api/v1/audit` to compile a breach timeline.
- **Art. 34**: Notify affected data subjects if the breach is likely to result in high risk to their rights and freedoms.
- The system's audit log (`audit_logs` table, retained 7 years) supports post-incident forensic investigation.

---

## Privacy by Design (Article 25)

Controls built into the system:
- RLS ensures tenant data is never accessible across tenant boundaries at the database layer.
- RBAC (6 roles) limits data access to authorised personnel only.
- Evidence files reference only internal `detection_id`; no direct PII in filenames.
- PDPA masking API (`/api/v1/pdpa/mask`) strips NRIC from display strings before rendering in UI.
- Face embeddings are mathematical vectors (not images); raw face images in evidence expire per retention policy.
- 2FA (TOTP) available for all user accounts to prevent unauthorised access.

---

## Data Processing Agreement

Customers deploying Seventh AI Vision as a cloud SaaS (controller → processor relationship) must execute a Data Processing Agreement (DPA) with the SaaS provider. The DPA must cover:
- Sub-processors list (hosting provider, CDN, backup provider)
- Technical and organisational measures (TOMs) — reference this document
- Audit rights
- Data deletion on contract termination
