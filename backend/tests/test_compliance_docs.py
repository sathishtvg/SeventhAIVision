"""Gap 11 — Regulatory compliance documentation + data-compliance API tests.

Two sections:
  A. Documentation files — pure filesystem checks that each doc file exists and
     contains the expected regulatory terms. No DB, no network.
  B. API tests — verify /api/v1/data-compliance endpoints behave correctly,
     including permission gating and response structure.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role

# ─── Paths ────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent            # /app/backend/tests/
_APP = _HERE.parents[1]                  # /app/
DOCS_DIR = _APP / "docs" / "compliance"

GDPR_DOC          = DOCS_DIR / "gdpr.md"
PDPA_DOC          = DOCS_DIR / "pdpa.md"
ISO27001_DOC      = DOCS_DIR / "iso27001.md"
SOC2_DOC          = DOCS_DIR / "soc2.md"
SECURITY_DOC      = DOCS_DIR / "security-controls.md"
DSR_DOC           = DOCS_DIR / "data-subject-rights.md"


# ═══════════════════════════════════════════════════════════════════════════════
# Section A — Compliance documentation file checks
# ═══════════════════════════════════════════════════════════════════════════════

class TestComplianceDocFiles:
    """Verify all six compliance markdown files exist and contain expected terms."""

    # ── Existence ──────────────────────────────────────────────────────────────

    def test_gdpr_doc_exists(self):
        assert GDPR_DOC.exists(), f"{GDPR_DOC} must exist"

    def test_pdpa_doc_exists(self):
        assert PDPA_DOC.exists(), f"{PDPA_DOC} must exist"

    def test_iso27001_doc_exists(self):
        assert ISO27001_DOC.exists(), f"{ISO27001_DOC} must exist"

    def test_soc2_doc_exists(self):
        assert SOC2_DOC.exists(), f"{SOC2_DOC} must exist"

    def test_security_controls_doc_exists(self):
        assert SECURITY_DOC.exists(), f"{SECURITY_DOC} must exist"

    def test_data_subject_rights_doc_exists(self):
        assert DSR_DOC.exists(), f"{DSR_DOC} must exist"

    # ── GDPR content ──────────────────────────────────────────────────────────

    def test_gdpr_doc_mentions_article_30(self):
        content = GDPR_DOC.read_text()
        assert "Article 30" in content, "gdpr.md must include Article 30 (Record of Processing Activities)"

    def test_gdpr_doc_mentions_data_subject_rights(self):
        content = GDPR_DOC.read_text()
        assert "data subject" in content.lower(), "gdpr.md must describe data subject rights"

    def test_gdpr_doc_mentions_retention(self):
        content = GDPR_DOC.read_text()
        assert "retention" in content.lower(), "gdpr.md must address data retention"

    # ── PDPA content ──────────────────────────────────────────────────────────

    def test_pdpa_doc_mentions_dpo(self):
        content = PDPA_DOC.read_text()
        assert "Data Protection Officer" in content or "DPO" in content, (
            "pdpa.md must mention the Data Protection Officer requirement"
        )

    def test_pdpa_doc_mentions_breach_notification(self):
        content = PDPA_DOC.read_text()
        assert "breach" in content.lower(), "pdpa.md must address data breach notification"

    def test_pdpa_doc_mentions_pdpc(self):
        content = PDPA_DOC.read_text()
        assert "PDPC" in content or "Personal Data Protection Commission" in content, (
            "pdpa.md must reference the PDPC (Personal Data Protection Commission)"
        )

    # ── ISO 27001 content ─────────────────────────────────────────────────────

    def test_iso27001_doc_mentions_annex_a(self):
        content = ISO27001_DOC.read_text()
        assert "Annex A" in content, "iso27001.md must reference ISO 27001 Annex A controls"

    def test_iso27001_doc_has_controls_table(self):
        content = ISO27001_DOC.read_text()
        assert "A.5" in content and "A.8" in content, (
            "iso27001.md must cover at least A.5 (Organisational) and A.8 (Technological) control groups"
        )

    # ── SOC 2 content ─────────────────────────────────────────────────────────

    def test_soc2_doc_mentions_trust_service_criteria(self):
        content = SOC2_DOC.read_text()
        assert "Trust Service" in content or "TSC" in content, (
            "soc2.md must reference the AICPA Trust Service Criteria"
        )

    def test_soc2_doc_mentions_cuec(self):
        content = SOC2_DOC.read_text()
        assert "CUEC" in content or "Complementary User Entity" in content, (
            "soc2.md must list Complementary User Entity Controls (CUECs)"
        )

    # ── Security controls content ─────────────────────────────────────────────

    def test_security_controls_doc_mentions_rls(self):
        content = SECURITY_DOC.read_text()
        assert "Row-Level Security" in content or "RLS" in content, (
            "security-controls.md must document Row-Level Security as a data isolation control"
        )

    def test_security_controls_doc_mentions_bcrypt(self):
        content = SECURITY_DOC.read_text()
        assert "bcrypt" in content, (
            "security-controls.md must document bcrypt password hashing"
        )

    # ── DSR content ───────────────────────────────────────────────────────────

    def test_dsr_doc_mentions_right_of_access(self):
        content = DSR_DOC.read_text()
        assert "Right of Access" in content or "Art. 15" in content or "Article 15" in content, (
            "data-subject-rights.md must cover the Right of Access (GDPR Art. 15)"
        )

    def test_dsr_doc_mentions_erasure(self):
        content = DSR_DOC.read_text()
        assert "Erasure" in content or "erasure" in content or "forgotten" in content.lower(), (
            "data-subject-rights.md must cover the Right to Erasure (GDPR Art. 17)"
        )

    def test_dsr_doc_mentions_dsr_export_endpoint(self):
        content = DSR_DOC.read_text()
        assert "dsr-export" in content, (
            "data-subject-rights.md must reference the /api/v1/data-compliance/dsr-export endpoint"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Section B — /api/v1/data-compliance API tests
# ═══════════════════════════════════════════════════════════════════════════════

def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


@pytest.mark.asyncio
async def test_compliance_summary_accessible_to_admin(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await app_client.get("/api/v1/data-compliance/summary", headers=_hdr(user_id, tenant_id))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_compliance_summary_denied_to_viewer(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    r = await app_client.get("/api/v1/data-compliance/summary", headers=_hdr(user_id, tenant_id, role_id=6))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_compliance_summary_response_structure(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await app_client.get("/api/v1/data-compliance/summary", headers=_hdr(user_id, tenant_id))
    assert r.status_code == 200
    data = r.json()
    assert "security" in data
    assert "data_protection" in data
    assert "users" in data
    assert data["security"]["rls_enforced"] is True
    assert data["security"]["rbac_enabled"] is True
    assert "evidence_retention_days" in data["data_protection"]


@pytest.mark.asyncio
async def test_audit_stats_returns_counts(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await app_client.get("/api/v1/data-compliance/audit-stats", headers=_hdr(user_id, tenant_id))
    assert r.status_code == 200
    data = r.json()
    assert "total_entries" in data
    assert "unique_actions" in data
    assert "entries_last_30_days" in data
    assert "top_actions_last_30d" in data
    assert isinstance(data["total_entries"], int)


@pytest.mark.asyncio
async def test_audit_stats_denied_to_viewer(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    r = await app_client.get("/api/v1/data-compliance/audit-stats", headers=_hdr(user_id, tenant_id, role_id=6))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_retention_config_accessible_to_admin(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await app_client.get("/api/v1/data-compliance/retention-config", headers=_hdr(user_id, tenant_id))
    assert r.status_code == 200
    data = r.json()
    assert "evidence_retention" in data
    assert "audit_retention" in data
    assert isinstance(data["evidence_retention"]["days"], int)
    assert data["evidence_retention"]["days"] > 0
    assert isinstance(data["audit_retention"]["years"], int)
    assert data["audit_retention"]["years"] > 0


@pytest.mark.asyncio
async def test_dsr_export_returns_subject_data(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await app_client.post(
        f"/api/v1/data-compliance/dsr-export/{user_id}",
        json={},
        headers=_hdr(user_id, tenant_id),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "subject" in data
    assert "audit_log_last_12_months" in data
    assert "active_sessions" in data
    assert data["regulation"] == "GDPR Article 20 / PDPA Section 21"
    assert str(user_id) == data["subject"]["id"]


@pytest.mark.asyncio
async def test_dsr_export_nonexistent_user_404(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await app_client.post(
        f"/api/v1/data-compliance/dsr-export/{uuid.uuid4()}",
        json={},
        headers=_hdr(user_id, tenant_id),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_dsr_export_denied_to_security_guard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=5)
    r = await app_client.post(
        f"/api/v1/data-compliance/dsr-export/{user_id}",
        json={},
        headers=_hdr(user_id, tenant_id, role_id=5),
    )
    assert r.status_code == 403
