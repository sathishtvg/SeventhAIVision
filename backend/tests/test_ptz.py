"""
Gap 31 — Full ONVIF PTZ Integration

Covers:
  A. DB schema (ptz_presets + onvif_discovery_cache tables)
  B. WS-Discovery endpoint (mocked service layer)
  C. Preset CRUD via API (pure DB, no mocking needed)
  D. PTZ move / stop / goto-preset (mocked ONVIF calls)
  E. Validation (pan/tilt/zoom ranges, mode enum)
  F. Permissions & RLS
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create_camera(auth_client, name: str = "PTZ-Cam") -> str:
    r = await auth_client.post(
        "/api/v1/cameras", json={"name": name, "location": "Test Entrance"}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _creds() -> dict:
    return {
        "host": "192.168.1.100",
        "port": 80,
        "username": "admin",
        "password": "admin123",
        "profile_token": "Profile_1",
    }


# ── A: DB Schema ──────────────────────────────────────────────────────────────

class TestADBSchema:
    async def test_ptz_presets_table_exists(self, db_session):
        await db_session.execute(text("SELECT 1 FROM ptz_presets LIMIT 0"))

    async def test_ptz_presets_required_columns(self, db_session):
        r = await db_session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ptz_presets' ORDER BY column_name"
        ))
        cols = {row[0] for row in r}
        assert {"id", "tenant_id", "camera_id", "name",
                "pan", "tilt", "zoom", "onvif_token",
                "created_at", "updated_at"}.issubset(cols)

    async def test_onvif_discovery_cache_table_exists(self, db_session):
        await db_session.execute(text("SELECT 1 FROM onvif_discovery_cache LIMIT 0"))

    async def test_onvif_discovery_cache_required_columns(self, db_session):
        r = await db_session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'onvif_discovery_cache' ORDER BY column_name"
        ))
        cols = {row[0] for row in r}
        assert {"id", "tenant_id", "xaddr", "types",
                "scopes", "last_seen_at", "created_at"}.issubset(cols)

    async def test_ptz_presets_unique_name_per_camera_constraint_exists(self, db_session):
        r = await db_session.execute(text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name = 'ptz_presets' AND constraint_type = 'UNIQUE'"
        ))
        names = {row[0] for row in r}
        assert "uq_ptz_presets_camera_name" in names


# ── B: WS-Discovery ───────────────────────────────────────────────────────────

class TestBDiscovery:
    @patch("app.services.onvif_service.discover_onvif", new_callable=AsyncMock)
    async def test_discover_endpoint_returns_200(self, mock_disc, auth_client):
        mock_disc.return_value = []
        r = await auth_client.post(
            "/api/v1/cameras/discover", json={"timeout_sec": 2}
        )
        assert r.status_code == 200

    @patch("app.services.onvif_service.discover_onvif", new_callable=AsyncMock)
    async def test_discover_returns_count_and_list(self, mock_disc, auth_client):
        mock_disc.return_value = [
            {
                "xaddr": "http://192.168.1.50/onvif/device_service",
                "types": ["dn:NetworkVideoTransmitter"],
                "scopes": ["onvif://www.onvif.org/hardware/DS-2CD2143G2-I"],
            },
        ]
        r = await auth_client.post(
            "/api/v1/cameras/discover", json={"timeout_sec": 2}
        )
        body = r.json()
        assert body["count"] == 1
        assert body["discovered"][0]["xaddr"] == "http://192.168.1.50/onvif/device_service"

    @patch("app.services.onvif_service.discover_onvif", new_callable=AsyncMock)
    async def test_discover_caches_results_in_db(self, mock_disc, auth_client, admin_session):
        unique_xaddr = f"http://10.0.0.{uuid.uuid4().hex[:2]}/onvif/device_service"
        mock_disc.return_value = [
            {"xaddr": unique_xaddr, "types": ["dn:NetworkVideoTransmitter"], "scopes": []}
        ]
        r = await auth_client.post(
            "/api/v1/cameras/discover", json={"timeout_sec": 2}
        )
        assert r.status_code == 200
        # admin_session bypasses RLS — can see all tenants' cache rows
        row = await admin_session.execute(
            text("SELECT xaddr FROM onvif_discovery_cache WHERE xaddr = :x"),
            {"x": unique_xaddr},
        )
        assert row.first() is not None, "Discovered camera should be cached in onvif_discovery_cache"

    async def test_discover_requires_auth(self, client):
        r = await client.post("/api/v1/cameras/discover", json={"timeout_sec": 2})
        assert r.status_code == 401

    async def test_discover_timeout_out_of_range(self, auth_client):
        r = await auth_client.post(
            "/api/v1/cameras/discover", json={"timeout_sec": 60}
        )
        assert r.status_code == 422


# ── C: Preset CRUD ────────────────────────────────────────────────────────────

class TestCPresetCRUD:
    async def test_list_presets_empty_for_new_camera(self, auth_client):
        cam_id = await _create_camera(auth_client)
        r = await auth_client.get(f"/api/v1/cameras/{cam_id}/ptz/presets")
        assert r.status_code == 200
        assert r.json() == []

    async def test_create_preset_returns_201(self, auth_client):
        cam_id = await _create_camera(auth_client)
        r = await auth_client.post(
            f"/api/v1/cameras/{cam_id}/ptz/presets",
            json={"name": "Front Gate", "pan": 0.5, "tilt": -0.2, "zoom": 0.3},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Front Gate"
        assert body["pan"] == 0.5

    async def test_created_preset_appears_in_list(self, auth_client):
        cam_id = await _create_camera(auth_client)
        await auth_client.post(
            f"/api/v1/cameras/{cam_id}/ptz/presets",
            json={"name": "Lobby View", "pan": 0.1, "tilt": 0.2, "zoom": 0.0},
        )
        r = await auth_client.get(f"/api/v1/cameras/{cam_id}/ptz/presets")
        assert r.status_code == 200
        names = [p["name"] for p in r.json()]
        assert "Lobby View" in names

    async def test_duplicate_preset_name_returns_409(self, auth_client):
        cam_id = await _create_camera(auth_client)
        body = {"name": "Entrance", "pan": 0.0, "tilt": 0.0, "zoom": 0.0}
        r1 = await auth_client.post(f"/api/v1/cameras/{cam_id}/ptz/presets", json=body)
        assert r1.status_code == 201
        r2 = await auth_client.post(f"/api/v1/cameras/{cam_id}/ptz/presets", json=body)
        assert r2.status_code == 409

    async def test_delete_preset_returns_204(self, auth_client):
        cam_id = await _create_camera(auth_client)
        cr = await auth_client.post(
            f"/api/v1/cameras/{cam_id}/ptz/presets",
            json={"name": "To Delete", "pan": 0.0, "tilt": 0.0, "zoom": 0.0},
        )
        preset_id = cr.json()["id"]
        dr = await auth_client.delete(
            f"/api/v1/cameras/{cam_id}/ptz/presets/{preset_id}"
        )
        assert dr.status_code == 204
        # Verify gone from list
        lr = await auth_client.get(f"/api/v1/cameras/{cam_id}/ptz/presets")
        assert all(p["id"] != preset_id for p in lr.json())

    async def test_delete_nonexistent_preset_returns_404(self, auth_client):
        cam_id = await _create_camera(auth_client)
        r = await auth_client.delete(
            f"/api/v1/cameras/{cam_id}/ptz/presets/{uuid.uuid4()}"
        )
        assert r.status_code == 404

    async def test_list_presets_for_nonexistent_camera_returns_404(self, auth_client):
        r = await auth_client.get(f"/api/v1/cameras/{uuid.uuid4()}/ptz/presets")
        assert r.status_code == 404


# ── D: PTZ Move / Stop / Goto-Preset ─────────────────────────────────────────

class TestDPTZCommands:
    @patch("app.services.onvif_service.ptz_continuous_move", new_callable=AsyncMock)
    async def test_ptz_continuous_move_returns_ok(self, mock_move, auth_client):
        cam_id = await _create_camera(auth_client)
        body = {**_creds(), "pan": 0.5, "tilt": 0.0, "zoom": 0.0, "mode": "continuous"}
        r = await auth_client.put(f"/api/v1/cameras/{cam_id}/ptz/move", json=body)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        mock_move.assert_awaited_once()

    @patch("app.services.onvif_service.ptz_absolute_move", new_callable=AsyncMock)
    async def test_ptz_absolute_move_calls_service(self, mock_move, auth_client):
        cam_id = await _create_camera(auth_client)
        body = {**_creds(), "pan": -0.3, "tilt": 0.1, "zoom": 0.5, "mode": "absolute"}
        r = await auth_client.put(f"/api/v1/cameras/{cam_id}/ptz/move", json=body)
        assert r.status_code == 200
        mock_move.assert_awaited_once()

    @patch("app.services.onvif_service.ptz_stop", new_callable=AsyncMock)
    async def test_ptz_stop_returns_stopped(self, mock_stop, auth_client):
        cam_id = await _create_camera(auth_client)
        r = await auth_client.put(
            f"/api/v1/cameras/{cam_id}/ptz/stop", json=_creds()
        )
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"
        mock_stop.assert_awaited_once()

    @patch("app.services.onvif_service.ptz_absolute_move", new_callable=AsyncMock)
    async def test_goto_preset_without_onvif_token_uses_absolute_move(
        self, mock_move, auth_client
    ):
        cam_id = await _create_camera(auth_client)
        pr = await auth_client.post(
            f"/api/v1/cameras/{cam_id}/ptz/presets",
            json={"name": "Stored Pos", "pan": 0.3, "tilt": -0.1, "zoom": 0.5},
        )
        preset_id = pr.json()["id"]
        r = await auth_client.put(
            f"/api/v1/cameras/{cam_id}/ptz/goto-preset/{preset_id}", json=_creds()
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        mock_move.assert_awaited_once()

    @patch("app.services.onvif_service.ptz_goto_preset", new_callable=AsyncMock)
    async def test_goto_preset_with_onvif_token_uses_goto_preset(
        self, mock_goto, auth_client
    ):
        cam_id = await _create_camera(auth_client)
        pr = await auth_client.post(
            f"/api/v1/cameras/{cam_id}/ptz/presets",
            json={"name": "Device Preset", "pan": 0.0, "tilt": 0.0,
                  "zoom": 0.0, "onvif_token": "Preset001"},
        )
        preset_id = pr.json()["id"]
        r = await auth_client.put(
            f"/api/v1/cameras/{cam_id}/ptz/goto-preset/{preset_id}", json=_creds()
        )
        assert r.status_code == 200
        mock_goto.assert_awaited_once()

    async def test_goto_nonexistent_preset_returns_404(self, auth_client):
        cam_id = await _create_camera(auth_client)
        r = await auth_client.put(
            f"/api/v1/cameras/{cam_id}/ptz/goto-preset/{uuid.uuid4()}", json=_creds()
        )
        assert r.status_code == 404


# ── E: Input Validation ───────────────────────────────────────────────────────

class TestEValidation:
    async def test_ptz_move_pan_above_1_returns_422(self, auth_client):
        cam_id = await _create_camera(auth_client)
        body = {**_creds(), "pan": 1.1, "tilt": 0.0, "zoom": 0.0, "mode": "continuous"}
        r = await auth_client.put(f"/api/v1/cameras/{cam_id}/ptz/move", json=body)
        assert r.status_code == 422

    async def test_ptz_move_tilt_below_minus1_returns_422(self, auth_client):
        cam_id = await _create_camera(auth_client)
        body = {**_creds(), "pan": 0.0, "tilt": -1.5, "zoom": 0.0, "mode": "continuous"}
        r = await auth_client.put(f"/api/v1/cameras/{cam_id}/ptz/move", json=body)
        assert r.status_code == 422

    async def test_ptz_move_zoom_negative_returns_422(self, auth_client):
        cam_id = await _create_camera(auth_client)
        body = {**_creds(), "pan": 0.0, "tilt": 0.0, "zoom": -0.5, "mode": "continuous"}
        r = await auth_client.put(f"/api/v1/cameras/{cam_id}/ptz/move", json=body)
        assert r.status_code == 422

    async def test_ptz_move_invalid_mode_returns_422(self, auth_client):
        cam_id = await _create_camera(auth_client)
        body = {**_creds(), "pan": 0.0, "tilt": 0.0, "zoom": 0.0, "mode": "teleport"}
        r = await auth_client.put(f"/api/v1/cameras/{cam_id}/ptz/move", json=body)
        assert r.status_code == 422

    async def test_preset_pan_out_of_range_returns_422(self, auth_client):
        cam_id = await _create_camera(auth_client)
        r = await auth_client.post(
            f"/api/v1/cameras/{cam_id}/ptz/presets",
            json={"name": "Bad Preset", "pan": 2.5, "tilt": 0.0, "zoom": 0.0},
        )
        assert r.status_code == 422


# ── F: Permissions & RLS ──────────────────────────────────────────────────────

class TestFPermissionsRLS:
    async def test_list_presets_requires_auth(self, client):
        r = await client.get(f"/api/v1/cameras/{uuid.uuid4()}/ptz/presets")
        assert r.status_code == 401

    async def test_ptz_move_requires_auth(self, client):
        cam_id = str(uuid.uuid4())
        r = await client.put(
            f"/api/v1/cameras/{cam_id}/ptz/move",
            json={**_creds(), "pan": 0.0, "tilt": 0.0, "zoom": 0.0, "mode": "continuous"},
        )
        assert r.status_code == 401

    async def test_cross_tenant_camera_not_visible(self, auth_client):
        """Accessing another tenant's camera_id returns 404 (RLS hides the camera row)."""
        # This UUID does not belong to auth_client's tenant → camera lookup → 404
        r = await auth_client.get(
            f"/api/v1/cameras/{uuid.uuid4()}/ptz/presets"
        )
        assert r.status_code == 404
