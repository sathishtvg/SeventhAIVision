"""Gap 6: i18n feature tests.

Covers:
- Supported locales list endpoint
- Alert template catalog per locale
- Single alert translation rendering with params
- Fallback behaviour for unknown locale / unknown alert_code
- User locale preference (PUT /me/locale)
- Tenant default locale (GET + PUT /tenant/locale)
- Pure-unit: translate_alert helper
"""
import json
import pytest
from httpx import AsyncClient

from app.i18n.alert_translations import (
    SUPPORTED_LOCALES,
    FALLBACK_LOCALE,
    TRANSLATIONS,
    translate_alert,
)


# ── Public endpoints ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_locales_public(app_client: AsyncClient):
    resp = await app_client.get("/api/v1/i18n/locales")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["locales"]) == set(SUPPORTED_LOCALES)
    assert data["fallback"] == FALLBACK_LOCALE


@pytest.mark.asyncio
async def test_get_alert_templates_english(app_client: AsyncClient):
    resp = await app_client.get("/api/v1/i18n/en/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["locale"] == "en"
    assert "lpr.blocklist_hit" in data["templates"]
    assert "face.unrecognized" in data["templates"]
    assert "intrusion.zone_breach" in data["templates"]


@pytest.mark.asyncio
async def test_get_alert_templates_chinese(app_client: AsyncClient):
    resp = await app_client.get("/api/v1/i18n/zh/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["locale"] == "zh"
    assert "摄像头" in data["templates"]["camera.offline"]


@pytest.mark.asyncio
async def test_get_alert_templates_malay(app_client: AsyncClient):
    resp = await app_client.get("/api/v1/i18n/ms/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["locale"] == "ms"
    assert "Kamera" in data["templates"]["camera.offline"]


@pytest.mark.asyncio
async def test_get_alert_templates_tamil(app_client: AsyncClient):
    resp = await app_client.get("/api/v1/i18n/ta/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["locale"] == "ta"
    assert "கேமரா" in data["templates"]["camera.offline"]


@pytest.mark.asyncio
async def test_unknown_locale_falls_back_to_english(app_client: AsyncClient):
    """An unsupported locale must return English, not 404."""
    resp = await app_client.get("/api/v1/i18n/fr/alerts")
    assert resp.status_code == 200
    assert resp.json()["locale"] == "en"


# ── Authenticated: single alert translation ───────────────────────────────────

@pytest.mark.asyncio
async def test_translate_alert_with_params(auth_client: AsyncClient):
    params = json.dumps({"plate": "SGB1234X", "confidence": 0.91})
    resp = await auth_client.get(
        f"/api/v1/i18n/en/alerts/lpr.blocklist_hit",
        params={"params": params},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "SGB1234X" in data["message"]
    assert "91%" in data["message"]


@pytest.mark.asyncio
async def test_translate_alert_chinese_with_params(auth_client: AsyncClient):
    params = json.dumps({"plate": "SGB9999Z", "confidence": 0.85})
    resp = await auth_client.get(
        "/api/v1/i18n/zh/alerts/lpr.blocklist_hit",
        params={"params": params},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "SGB9999Z" in data["message"]
    assert data["locale"] == "zh"


@pytest.mark.asyncio
async def test_translate_unknown_alert_code_404(auth_client: AsyncClient):
    resp = await auth_client.get("/api/v1/i18n/en/alerts/nonexistent.code")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_translate_alert_invalid_params_json_422(auth_client: AsyncClient):
    resp = await auth_client.get(
        "/api/v1/i18n/en/alerts/lpr.blocklist_hit",
        params={"params": "NOT_JSON"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_translate_alert_no_params_returns_template(auth_client: AsyncClient):
    """Calling without params returns raw template (placeholders unfilled)."""
    resp = await auth_client.get("/api/v1/i18n/en/alerts/camera.offline")
    assert resp.status_code == 200
    # camera.offline has no params — message should be the full string
    assert resp.json()["message"] == TRANSLATIONS["en"]["camera.offline"]


@pytest.mark.asyncio
async def test_translate_requires_auth(app_client: AsyncClient):
    resp = await app_client.get("/api/v1/i18n/en/alerts/camera.offline")
    assert resp.status_code == 401


# ── User locale preference ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_my_locale_zh(auth_client: AsyncClient):
    resp = await auth_client.put("/api/v1/i18n/me/locale", json={"locale": "zh"})
    assert resp.status_code == 200
    assert resp.json()["locale"] == "zh"


@pytest.mark.asyncio
async def test_set_my_locale_back_to_en(auth_client: AsyncClient):
    # Set to ms then back to en
    await auth_client.put("/api/v1/i18n/me/locale", json={"locale": "ms"})
    resp = await auth_client.put("/api/v1/i18n/me/locale", json={"locale": "en"})
    assert resp.status_code == 200
    assert resp.json()["locale"] == "en"


@pytest.mark.asyncio
async def test_set_my_locale_unsupported_422(auth_client: AsyncClient):
    resp = await auth_client.put("/api/v1/i18n/me/locale", json={"locale": "fr"})
    assert resp.status_code == 422


# ── Tenant locale ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tenant_locale_default_en(auth_client: AsyncClient):
    resp = await auth_client.get("/api/v1/i18n/tenant/locale")
    assert resp.status_code == 200
    # Newly created test tenant defaults to 'en'
    assert resp.json()["locale"] in SUPPORTED_LOCALES


@pytest.mark.asyncio
async def test_set_tenant_locale_admin(auth_client: AsyncClient):
    resp = await auth_client.put("/api/v1/i18n/tenant/locale", json={"locale": "zh"})
    assert resp.status_code == 200
    assert resp.json()["locale"] == "zh"
    # Restore
    await auth_client.put("/api/v1/i18n/tenant/locale", json={"locale": "en"})


@pytest.mark.asyncio
async def test_set_tenant_locale_unsupported_422(auth_client: AsyncClient):
    resp = await auth_client.put("/api/v1/i18n/tenant/locale", json={"locale": "de"})
    assert resp.status_code == 422


# ── Pure-unit: translate_alert helper ─────────────────────────────────────────

def test_translate_alert_en_lpr():
    msg = translate_alert("lpr.blocklist_hit", {"plate": "ABC123", "confidence": 0.95}, "en")
    assert "ABC123" in msg
    assert "95%" in msg


def test_translate_alert_zh():
    msg = translate_alert("camera.offline", {}, "zh")
    assert "摄像头" in msg


def test_translate_alert_ms():
    msg = translate_alert("camera.offline", {}, "ms")
    assert "Kamera" in msg


def test_translate_alert_ta():
    msg = translate_alert("camera.offline", {}, "ta")
    assert "கேமரா" in msg


def test_translate_alert_unknown_locale_falls_back():
    msg = translate_alert("camera.offline", {}, "fr")
    # Falls back to English
    assert msg == TRANSLATIONS["en"]["camera.offline"]


def test_translate_alert_unknown_code_returns_code():
    msg = translate_alert("totally.unknown.code", {}, "en")
    assert msg == "totally.unknown.code"


def test_translate_alert_missing_param_returns_template():
    """If message_params is missing a placeholder key, return the raw template."""
    msg = translate_alert("lpr.blocklist_hit", {}, "en")
    # Should return the raw template string (not crash)
    assert "{plate}" in msg or "lpr.blocklist_hit" in msg or "License plate" in msg


def test_translate_alert_zone_breach():
    msg = translate_alert("intrusion.zone_breach", {"zone_name": "Server Room"}, "en")
    assert "Server Room" in msg


def test_translate_alert_crowd_density():
    msg = translate_alert("crowd.density_high", {"count": 42}, "en")
    assert "42" in msg


def test_all_locales_have_same_alert_codes():
    """Every locale must cover the exact same set of alert codes as English."""
    en_codes = set(TRANSLATIONS["en"].keys())
    for locale in SUPPORTED_LOCALES:
        if locale == "en":
            continue
        locale_codes = set(TRANSLATIONS[locale].keys())
        missing = en_codes - locale_codes
        extra = locale_codes - en_codes
        assert not missing, f"Locale '{locale}' missing codes: {missing}"
        assert not extra, f"Locale '{locale}' has extra codes: {extra}"
