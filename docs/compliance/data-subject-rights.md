# Data Subject Rights Procedures — Seventh AI Vision

**Applicable regulations:** GDPR Articles 15–22; Singapore PDPA Sections 21–24  
**Last reviewed:** 2026-07-02

This document describes the step-by-step procedures for handling Data Subject Rights (DSR) requests using the platform's built-in tools. All DSR actions must be recorded in the organisation's DSR register (maintained outside the platform).

---

## Request Intake & Identity Verification

1. Receive DSR request by email / web form (maintain a dedicated `privacy@[domain]` address).
2. **Verify identity** before processing any request:
   - Employees: match to employee ID + work email
   - Visitors: match to visitor log entry + NRIC/FIN (redacted in response)
   - Members of public: government-issued photo ID + declaration form
3. Log the request with: date received, requestor identity, right(s) requested, and verification method.
4. **Response deadline:** 30 days (GDPR); 30 calendar days (PDPA). Note the deadline in the register immediately.

---

## Right of Access (GDPR Art. 15 / PDPA S21)

**What:** Provide the data subject with a copy of their personal data and information about how it is processed.

**Procedure:**
1. As admin user, call:
   ```
   POST /api/v1/data-compliance/dsr-export/{user_id}
   ```
   This returns a JSON package containing: user profile, alert events linked to their face/plate, audit log entries, session history, visitor records.
2. For non-registered data subjects (members of the public captured on CCTV):
   - Identify the approximate date/time and camera.
   - Export evidence: `GET /api/v1/evidence?camera_id=...&date_from=...&date_to=...`
   - Export LPR events: `GET /api/v1/detections/lpr-events?camera_id=...`
   - Export face events if face recognition enabled: `GET /api/v1/detections/face-events?camera_id=...`
3. Redact third-party faces/plates from CCTV snapshots before providing to the requestor.
4. Provide the export as a downloadable JSON or summary PDF. Do not send raw DB exports.

**Format:** Machine-readable (JSON) preferred; human-readable summary acceptable.  
**Fee:** GDPR: no fee for first request; reasonable fee if manifestly unfounded/excessive. PDPA: ≤ SGD 10.

---

## Right to Rectification (GDPR Art. 16 / PDPA S22)

**What:** Correct inaccurate personal data.

**Procedure:**
1. For user account data (name, email, role):
   ```
   PUT /api/v1/users/{user_id}
   ```
2. For face watchlist entry (incorrect person name or image):
   - Delete the incorrect entry: `DELETE /api/v1/watchlist/faces/{entry_id}`
   - Re-enrol with correct image: `POST /api/v1/watchlist/faces/enroll`
3. For LPR watchlist entry (incorrect plate number):
   ```
   PUT /api/v1/watchlist/plates/{entry_id}
   ```
4. Document the correction in the DSR register including the original value (for audit purposes).

---

## Right to Erasure ("Right to be Forgotten") (GDPR Art. 17)

**What:** Delete personal data where there is no overriding legitimate reason to retain it.

**When applicable:** Data subject withdraws consent; data no longer necessary for original purpose; unlawful processing.

**When NOT applicable:** Legal obligation to retain (e.g. law enforcement hold); exercise/defence of legal claims.

**Procedure:**
1. Deactivate the user account (does not delete records, preserves audit trail):
   ```
   PUT /api/v1/users/{user_id}   body: { "is_active": false }
   ```
2. Delete face watchlist entries: `DELETE /api/v1/watchlist/faces/{entry_id}`
3. Delete plate watchlist entries: `DELETE /api/v1/watchlist/plates/{entry_id}`
4. For evidence files: allow retention policy to auto-expire, OR manually delete specific files:
   - Identify `storage_path` from `GET /api/v1/evidence?detection_id=...`
   - Remove the file from `EVIDENCE_ROOT` on the server
   - Delete the evidence row: (requires direct DB access or a support tool — no public endpoint, by design, to prevent accidental erasure of evidence needed for legal proceedings)
5. **Retain audit logs** — erasure of audit entries is not permitted under SOC 2 / ISO 27001 and would undermine the system's forensic integrity. Inform the requestor that audit logs are retained for the legally required period.
6. Document the erasure actions and any retained data with justification.

---

## Right to Restriction of Processing (GDPR Art. 18)

**What:** Restrict processing while accuracy is contested, or pending objection outcome.

**Procedure:**
1. Disable specific AI modules on the relevant camera(s):
   ```
   PUT /api/v1/cameras/{camera_id}
   body: { "ai_modules_enabled": [] }
   ```
2. For a specific user's data: deactivate the user account (prevents new processing of their interactions).
3. Document the restriction start date and review date.
4. Re-enable when the basis for restriction is resolved.

---

## Right to Data Portability (GDPR Art. 20)

**What:** Provide personal data in a structured, commonly-used, machine-readable format for transfer to another controller.

**Applicable to:** Data provided by the data subject; processed by automated means; based on consent or contract.

**Procedure:**
1. Generate DSR export:
   ```
   POST /api/v1/data-compliance/dsr-export/{user_id}
   ```
2. The response is a JSON object — machine-readable and structured.
3. Provide the JSON file directly or convert to CSV/XML per the requestor's preference.

---

## Right to Object (GDPR Art. 21)

**What:** Object to processing based on legitimate interests.

**Procedure:**
1. Assess whether the organisation's legitimate interests override the individual's objection (e.g. safety of other persons, legal obligation).
2. If the objection is upheld:
   - Disable face recognition for that individual's camera coverage areas
   - Remove any watchlist entries for the individual
   - Disable AI modules on cameras covering the individual's regular locations
3. Document the outcome and rationale.

---

## Rights Related to Automated Decision-Making (GDPR Art. 22)

**Platform position:** Seventh AI Vision generates **alerts** and **incidents** that are reviewed by human operators. No fully automated decisions with legal or similarly significant effects are made without human review. AI-generated alerts are recommendations, not final decisions.

**Response template:**
> "The system uses AI to detect anomalies and generate alerts for review by our security team. All decisions affecting your rights (access control, incident escalation, law enforcement referrals) are made by authorised human personnel after reviewing the AI-generated information. You have the right to request human review of any decision made based on AI-generated alerts involving you by contacting [DPO email]."

---

## DSR Register Template

Maintain a register outside the platform (Excel / dedicated DSR tool):

| Field | Description |
|-------|-------------|
| Request ID | Sequential reference |
| Date received | ISO 8601 |
| Data subject name | As provided |
| Right(s) requested | Access / Rectification / Erasure / Restriction / Portability / Objection |
| Verification method | Employee ID / Government ID / Declaration form |
| Verified Y/N | Yes / No / Pending |
| Deadline | Date received + 30 days |
| Actions taken | System calls made, data deleted, etc. |
| Date completed | |
| Exceptions / retained data | Justification for any data not erased |
| Requestor notified | Date and method |
