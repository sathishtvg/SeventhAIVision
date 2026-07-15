# PDPA Compliance — Seventh AI Vision

**Regulation:** Personal Data Protection Act 2012 (Singapore), as amended by the PDPA Amendment Act 2020  
**Applicability:** Any deployment in Singapore or where personal data of Singapore individuals is collected, used, or disclosed.  
**Last reviewed:** 2026-07-02

---

## Data Protection Obligations Summary

| Obligation | PDPA Section | Status |
|------------|-------------|--------|
| Consent | S13–17 | Managed via visitor consent workflow; CCTV notices posted |
| Purpose Limitation | S18 | Enforced via per-camera `ai_modules_enabled` config |
| Notification | S20 | Privacy notice template in this document |
| Access & Correction | S21–22 | DSR export + user/watchlist edit endpoints |
| Accuracy | S23 | User-correctable records; face watchlist re-enrolment |
| Protection | S24 | RLS, RBAC, 2FA, TLS, audit logs (see security-controls.md) |
| Retention Limitation | S25 | Configurable `evidence.retention_days` (default 90); auto-purge via scheduler |
| Transfer Limitation | S26 | No cross-border transfers by default; customer must comply if using offshore S3 |
| Data Breach | S26C | 72-hour MCI notification for notifiable breaches |
| Do Not Call | S36–43 | Not applicable (no outbound marketing) |

---

## Data Protection Officer (DPO)

Organisations with ≥ 250 employees or that process personal data on a large scale must appoint a DPO and register with the Personal Data Protection Commission (PDPC).

**DPO registration:** PDPC Business Associate Portal — `https://www.pdpc.gov.sg`  
**DPO responsibilities under PDPA:**
- Ensure data protection policies are implemented
- Conduct Data Protection Impact Assessments (DPIAs) for high-risk processing
- Be the point of contact for data subjects and PDPC
- Maintain the organisation's Record of Data Assets

---

## Categories of Personal Data Processed

| Data Type | PDPA Classification | Retention |
|-----------|--------------------|-----------| 
| Face images (CCTV evidence) | Personal data | `evidence.retention_days` (default 90 days) |
| Biometric face embeddings | **Sensitive personal data** | Until watchlist entry expires/deactivated |
| Licence plate numbers | Personal data (linked to registered owner) | `evidence.retention_days` |
| Visitor NRIC / FIN / Passport | Personal data | `evidence.retention_days` or visitor record retention |
| Guard GPS tracks | Personal data | Per patrol session; no standalone retention beyond evidence period |
| Security guard employee data | Personal data (employment) | Per HR policy; user accounts deactivated, not deleted |

---

## Consent Management

### CCTV Notice (Section 20 notification)
Post notices at all camera entry points containing:
- Identity of the organisation
- Purpose of collection (security monitoring)
- Contact of the DPO
- Retention period

### Visitor Consent (Biometric face enrolment)
Where face recognition is used on visitors (not employees), **explicit written consent** must be obtained before enrolment:
- Use the visitor consent form (template in `docs/compliance/templates/visitor-consent-form.docx`)
- Consent record must be retained for the duration of processing + 2 years
- Withdraw consent: DELETE the face watchlist entry via `/api/v1/watchlist/faces/{id}`

### Employee Processing
Employee CCTV monitoring is typically based on **legitimate interests** (security of workplace, company assets) supplemented by employment contract terms. Inform employees via the Employee Handbook / Acceptable Use Policy.

---

## Data Access & Correction Requests (Sections 21–22)

**Timeline:** Respond within **30 calendar days** (extensions allowable with notification).

**Process:**
1. Verify identity of requestor (employee ID / NRIC match)
2. Use `POST /api/v1/data-compliance/dsr-export/{user_id}` to generate a JSON export of all personal data held
3. Provide export to requestor in machine-readable format (JSON) or summary PDF
4. For correction requests: update via `PUT /api/v1/users/{user_id}` or re-enrol face

**Fee:** PDPA permits charging a reasonable fee for access requests. PDPC guidance suggests ≤ SGD 10.

---

## Data Breach Notification (Section 26C, effective 1 Feb 2022)

**Notifiable breach:** A breach that (i) is likely to result in significant harm to individuals, or (ii) is of a significant scale (≥ 500 individuals affected).

**Timeline:**
- Notify PDPC via `https://www.pdpc.gov.sg/Compliance-and-Enforcement/Notify-Us-of-a-Data-Breach` within **3 calendar days** of assessing the breach as notifiable
- Notify affected individuals as soon as practicable

**Breach response procedure:**
1. Activate incident response (see `docs/compliance/security-controls.md`)
2. Use `GET /api/v1/audit` (filter by `date_from`/`date_to` and affected resource type) to compile affected records
3. Use `POST /api/v1/data-compliance/dsr-export` to identify affected data subjects
4. Complete PDPC breach notification form
5. Preserve all audit logs (do NOT purge during/after an investigation)

---

## Data Masking

The platform's built-in PDPA masking feature (`GET /api/v1/pdpa/mask?text=...`) partially obfuscates NRIC/FIN/passport numbers in display strings, e.g. `S1234567A` → `S****567A`. Enable in the frontend Settings page.

---

## Cross-Border Data Transfer (Section 26)

By default, no data leaves Singapore. If the customer configures MinIO/S3 with an offshore endpoint:
- Ensure the recipient country provides a standard of protection comparable to PDPA
- OR execute a data transfer agreement (DTA) conforming to PDPC's Model DTA clauses
- Document the transfer in the Record of Data Assets

---

## PDPA Accountability: Mandatory Data Protection Policies

The PDPA requires organisations to implement and communicate data protection policies. Minimum set:

1. **Data Protection Policy** — public-facing document describing data practices
2. **Data Retention and Disposal Policy** — aligns with `evidence.retention_days` settings
3. **Data Breach Response Plan** — procedure above; tested annually
4. **Access Control Policy** — RBAC roles defined in `docs/compliance/security-controls.md`
5. **Vendor Management Policy** — governs sub-processors (hosting, backup, CDN)
