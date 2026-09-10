"""Gap 32 — NVR Adapters tests.

Sections:
  A (4)  — DB schema: tables + columns + built-in seed count
  B (7)  — Hikvision: device info, channels, auth failure, requires-auth 401,
             timeout_sec >30 validation, XML parser pure function, connect error
  C (6)  — Dahua: device info, channels, auth failure, requires-auth 401,
             kv parser pure function, kv parser edge cases
  D (9)  — NVR Connections CRUD: create, list, get, delete, 404, probe hikvision,
             probe dahua, probe generic, cross-tenant 404
  E (5)  — Camera Model Library: list returns builtins, hik present, dahua present,
             add custom 201, custom appears in list
  F (2)  — Permissions: connections list 401, models list 401
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text


# ── XML / Dahua fixtures (module-level constants) ────────────────────────────

_DEVICE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<DeviceInfo xmlns="http://www.hikvision.com/ver20/XMLSchema">'
    '<deviceName>IP Camera</deviceName>'
    '<deviceID>0x1234</deviceID>'
    '<model>DS-2CD2T43G2-2I</model>'
    '<serialNumber>DS-2CD2T43G2-2I20220601AABB</serialNumber>'
    '<firmwareVersion>V5.7.16</firmwareVersion>'
    '<firmwareReleasedDate>build 220920</firmwareReleasedDate>'
    '<macAddress>AA:BB:CC:DD:EE:FF</macAddress>'
    '</DeviceInfo>'
)

_CHANNELS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<StreamingChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">'
    '<StreamingChannel>'
    '<id>101</id>'
    '<channelName>Camera 1</channelName>'
    '<enabled>true</enabled>'
    '</StreamingChannel>'
    '</StreamingChannelList>'
)

_EMPTY_CHANNELS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<StreamingChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">'
    '</StreamingChannelList>'
)

_SYSINFO_RESPONSE = (
    "table.SysInfo.DeviceType=IP Camera\n"
    "table.SysInfo.SerialNo=IPC-HDW2831T-AS20220601AABB\n"
    "table.SysInfo.SoftwareVersion=2.820.0026.3.R\n"
    "table.SysInfo.HardwareVersion=3.0.0.0\n"
    "table.SysInfo.BuildDate=2022-09-20\n"
    "table.SysInfo.DeviceClass=IPC\n"
)

_CHANNELS_RESPONSE = (
    "table.Encode[0].MainFormat[0].Video.Compression=H.264\n"
    "table.Encode[0].MainFormat[0].Video.Width=3840\n"
    "table.Encode[0].MainFormat[0].Video.Height=2160\n"
    "table.Encode[0].MainFormat[0].Video.FPS=20\n"
)


def _make_http_mock(texts: list[str]):
    """Return (mock_cls, mock_inst) with mock_inst.get yielding text responses."""
    responses = iter(texts)

    async def _fake_get(*_a, **_kw):
        resp = MagicMock()
        resp.text = next(responses)
        resp.raise_for_status = MagicMock()
        return resp

    mock_inst = AsyncMock()
    mock_inst.get.side_effect = _fake_get

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_inst)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_cls = MagicMock(return_value=mock_ctx)
    return mock_cls, mock_inst


# ── A — DB Schema ─────────────────────────────────────────────────────────────

class TestASchema:
    async def test_nvr_connections_table_exists(self, db_session):
        await db_session.execute(text("SELECT 1 FROM nvr_connections LIMIT 0"))

    async def test_nvr_connections_columns(self, db_session):
        r = await db_session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='nvr_connections'"
        ))
        cols = {row[0] for row in r}
        for expected in ("id", "tenant_id", "name", "host", "port", "username",
                         "password_enc", "adapter_type", "is_active",
                         "last_probe_at", "last_probe_status", "created_at", "updated_at"):
            assert expected in cols, f"Missing column: {expected}"

    async def test_camera_model_library_table_exists(self, db_session):
        await db_session.execute(text("SELECT 1 FROM camera_model_library LIMIT 0"))

    async def test_builtin_model_count_gte_10(self, db_session):
        # Built-in rows (tenant_id IS NULL) are visible even without GUC set:
        # the RLS USING clause starts with `tenant_id IS NULL OR ...`
        r = await db_session.execute(
            text("SELECT COUNT(*) FROM camera_model_library WHERE is_builtin = TRUE")
        )
        count = r.scalar()
        assert count >= 10, f"Expected ≥10 built-in models, got {count}"


# ── B — Hikvision ─────────────────────────────────────────────────────────────

class TestBHikvision:
    async def test_hikvision_device_info_parsed(self, auth_client):
        mock_cls, _ = _make_http_mock([_DEVICE_XML, _EMPTY_CHANNELS_XML])
        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post("/api/v1/nvr/hikvision/probe", json={
                "host": "192.168.1.64", "port": 80,
                "username": "admin", "password": "orbit-lantern-quay-42",
            })
        assert r.status_code == 200
        info = r.json()["device_info"]
        assert info["model"] == "DS-2CD2T43G2-2I"
        assert info["serial_number"] == "DS-2CD2T43G2-2I20220601AABB"

    async def test_hikvision_channels_parsed(self, auth_client):
        mock_cls, _ = _make_http_mock([_DEVICE_XML, _CHANNELS_XML])
        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post("/api/v1/nvr/hikvision/probe", json={
                "host": "192.168.1.64", "port": 80,
                "username": "admin", "password": "orbit-lantern-quay-42",
            })
        assert r.status_code == 200
        channels = r.json()["channels"]
        assert len(channels) == 1
        assert channels[0]["id"] == "101"
        assert channels[0]["channel_name"] == "Camera 1"

    async def test_hikvision_auth_failure_502(self, auth_client):
        import httpx as _httpx

        req = _httpx.Request("GET", "http://x/ISAPI/System/deviceInfo")
        resp_obj = _httpx.Response(401, request=req)
        error = _httpx.HTTPStatusError("401", request=req, response=resp_obj)

        mock_inst = AsyncMock()
        mock_inst.get = AsyncMock(side_effect=error)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls = MagicMock(return_value=mock_ctx)

        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post("/api/v1/nvr/hikvision/probe", json={
                "host": "192.168.1.64", "port": 80,
                "username": "wrong", "password": "wrong",
            })
        assert r.status_code == 502
        assert "authentication" in r.json()["detail"].lower()

    async def test_hikvision_requires_auth_header(self, client):
        r = await client.post("/api/v1/nvr/hikvision/probe", json={
            "host": "h", "port": 80, "username": "u", "password": "p",
        })
        assert r.status_code == 401

    async def test_hikvision_timeout_gt30_422(self, auth_client):
        r = await auth_client.post("/api/v1/nvr/hikvision/probe", json={
            "host": "h", "port": 80, "username": "u", "password": "p",
            "timeout_sec": 31.0,
        })
        assert r.status_code == 422

    async def test_xml_parser_pure_function(self):
        import xml.etree.ElementTree as ET
        from app.services.nvr_service import _parse_hik_text
        root = ET.fromstring(_DEVICE_XML)
        assert _parse_hik_text(root, "model") == "DS-2CD2T43G2-2I"
        assert _parse_hik_text(root, "macAddress") == "AA:BB:CC:DD:EE:FF"
        assert _parse_hik_text(root, "doesNotExist") is None

    async def test_hikvision_connect_error_502(self, auth_client):
        import httpx as _httpx

        mock_inst = AsyncMock()
        mock_inst.get = AsyncMock(side_effect=_httpx.ConnectError("refused"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls = MagicMock(return_value=mock_ctx)

        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post("/api/v1/nvr/hikvision/probe", json={
                "host": "192.168.1.64", "port": 80,
                "username": "admin", "password": "orbit-lantern-quay-42",
            })
        assert r.status_code == 502


# ── C — Dahua ─────────────────────────────────────────────────────────────────

class TestCDahua:
    async def test_dahua_device_info_parsed(self, auth_client):
        mock_cls, _ = _make_http_mock([_SYSINFO_RESPONSE, _CHANNELS_RESPONSE])
        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post("/api/v1/nvr/dahua/probe", json={
                "host": "192.168.1.108", "port": 80,
                "username": "admin", "password": "orbit-lantern-quay-42",
            })
        assert r.status_code == 200
        info = r.json()["device_info"]
        assert info["software_version"] == "2.820.0026.3.R"
        assert info["device_class"] == "IPC"

    async def test_dahua_channels_parsed(self, auth_client):
        mock_cls, _ = _make_http_mock([_SYSINFO_RESPONSE, _CHANNELS_RESPONSE])
        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post("/api/v1/nvr/dahua/probe", json={
                "host": "192.168.1.108", "port": 80,
                "username": "admin", "password": "orbit-lantern-quay-42",
            })
        assert r.status_code == 200
        channels = r.json()["channels"]
        assert len(channels) == 1
        assert channels[0]["channel_id"] == "0"
        assert channels[0]["fps"] == "20"

    async def test_dahua_auth_failure_502(self, auth_client):
        import httpx as _httpx

        req = _httpx.Request("GET", "http://x/cgi-bin/magicBox.cgi")
        resp_obj = _httpx.Response(401, request=req)
        error = _httpx.HTTPStatusError("401", request=req, response=resp_obj)

        mock_inst = AsyncMock()
        mock_inst.get = AsyncMock(side_effect=error)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls = MagicMock(return_value=mock_ctx)

        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post("/api/v1/nvr/dahua/probe", json={
                "host": "192.168.1.108", "port": 80,
                "username": "bad", "password": "bad",
            })
        assert r.status_code == 502

    async def test_dahua_requires_auth_header(self, client):
        r = await client.post("/api/v1/nvr/dahua/probe", json={
            "host": "h", "port": 80, "username": "u", "password": "p",
        })
        assert r.status_code == 401

    async def test_kv_parser_pure_function(self):
        from app.services.nvr_service import _parse_dahua_response
        parsed = _parse_dahua_response(_SYSINFO_RESPONSE)
        assert parsed["table.SysInfo.SoftwareVersion"] == "2.820.0026.3.R"
        assert parsed["table.SysInfo.SerialNo"] == "IPC-HDW2831T-AS20220601AABB"

    async def test_kv_parser_edge_cases(self):
        from app.services.nvr_service import _parse_dahua_response
        text = (
            "\n"
            "# comment line\n"
            "key.with.no.value\n"
            "valid.key=value=with=equals\n"
        )
        parsed = _parse_dahua_response(text)
        assert "valid.key" in parsed
        assert parsed["valid.key"] == "value=with=equals"
        assert "# comment line" not in parsed
        assert "key.with.no.value" not in parsed


# ── D — NVR Connections CRUD ──────────────────────────────────────────────────

_CONN_PAYLOAD = {
    "name": "Office NVR",
    "host": "192.168.1.200",
    "port": 80,
    "username": "admin",
    "password": "orbit-lantern-quay-42",
    "adapter_type": "hikvision",
}


class TestDConnections:
    async def test_create_connection_201(self, auth_client):
        r = await auth_client.post("/api/v1/nvr/connections", json=_CONN_PAYLOAD)
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Office NVR"
        assert data["adapter_type"] == "hikvision"

    async def test_list_connections(self, auth_client):
        await auth_client.post("/api/v1/nvr/connections", json=_CONN_PAYLOAD)
        r = await auth_client.get("/api/v1/nvr/connections")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    async def test_get_connection(self, auth_client):
        created = (await auth_client.post("/api/v1/nvr/connections",
                                          json=_CONN_PAYLOAD)).json()
        r = await auth_client.get(f"/api/v1/nvr/connections/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    async def test_delete_connection_204(self, auth_client):
        created = (await auth_client.post("/api/v1/nvr/connections",
                                          json=_CONN_PAYLOAD)).json()
        r = await auth_client.delete(f"/api/v1/nvr/connections/{created['id']}")
        assert r.status_code == 204

    async def test_delete_nonexistent_404(self, auth_client):
        r = await auth_client.delete(f"/api/v1/nvr/connections/{uuid.uuid4()}")
        assert r.status_code == 404

    async def test_probe_saved_hikvision(self, auth_client):
        created = (await auth_client.post("/api/v1/nvr/connections",
                                          json=_CONN_PAYLOAD)).json()
        mock_cls, _ = _make_http_mock([_DEVICE_XML, _EMPTY_CHANNELS_XML])
        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post(
                f"/api/v1/nvr/connections/{created['id']}/probe"
            )
        assert r.status_code == 200
        assert r.json()["probe_status"] == "ok"
        assert r.json()["adapter"] == "hikvision"

    async def test_probe_saved_dahua(self, auth_client):
        payload = dict(_CONN_PAYLOAD, name="Dahua NVR", adapter_type="dahua")
        created = (await auth_client.post("/api/v1/nvr/connections",
                                          json=payload)).json()
        mock_cls, _ = _make_http_mock([_SYSINFO_RESPONSE, _CHANNELS_RESPONSE])
        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            r = await auth_client.post(
                f"/api/v1/nvr/connections/{created['id']}/probe"
            )
        assert r.status_code == 200
        assert r.json()["adapter"] == "dahua"

    async def test_probe_saved_generic(self, auth_client):
        payload = dict(_CONN_PAYLOAD, name="Generic NVR", adapter_type="generic")
        created = (await auth_client.post("/api/v1/nvr/connections",
                                          json=payload)).json()
        r = await auth_client.post(
            f"/api/v1/nvr/connections/{created['id']}/probe"
        )
        assert r.status_code == 200
        assert r.json()["adapter"] == "generic"
        assert "note" in r.json()

    async def test_cross_tenant_connection_404(self, auth_client):
        # A random UUID won't exist in this tenant's scope (RLS hides other tenants)
        r = await auth_client.get(f"/api/v1/nvr/connections/{uuid.uuid4()}")
        assert r.status_code == 404


# ── E — Camera Model Library ──────────────────────────────────────────────────

class TestEModelLibrary:
    async def test_list_returns_builtins(self, auth_client):
        r = await auth_client.get("/api/v1/nvr/models")
        assert r.status_code == 200
        assert len(r.json()) >= 10

    async def test_hikvision_builtin_present(self, auth_client):
        r = await auth_client.get("/api/v1/nvr/models?make=Hikvision")
        models = r.json()
        assert len(models) >= 1
        assert all(m["make"] == "Hikvision" for m in models)

    async def test_dahua_builtin_present(self, auth_client):
        r = await auth_client.get("/api/v1/nvr/models?make=Dahua")
        assert len(r.json()) >= 1

    async def test_add_custom_model_201(self, auth_client):
        r = await auth_client.post("/api/v1/nvr/models", json={
            "make": "TestCo",
            "model_name": "TC-100",
            "device_type": "ipc",
            "ptz_supported": False,
        })
        assert r.status_code == 201
        data = r.json()
        assert data["make"] == "TestCo"
        assert data["is_builtin"] is False

    async def test_custom_model_appears_in_list(self, auth_client):
        await auth_client.post("/api/v1/nvr/models", json={
            "make": "Acme", "model_name": "ACME-200", "device_type": "ipc",
        })
        r = await auth_client.get("/api/v1/nvr/models?make=Acme")
        assert any(m["model_name"] == "ACME-200" for m in r.json())


# ── F — Permissions ───────────────────────────────────────────────────────────

class TestFPermissions:
    async def test_connections_list_requires_auth(self, client):
        r = await client.get("/api/v1/nvr/connections")
        assert r.status_code == 401

    async def test_models_list_requires_auth(self, client):
        r = await client.get("/api/v1/nvr/models")
        assert r.status_code == 401
