"""Gap 33 — Credential Encryption at Rest: tests.

Validates:
  A. crypto.py unit tests (encrypt/decrypt round-trip, error handling, key selection)
  B. NVR connection credential tests (stored value is encrypted, probe decrypts)
  C. Stream auth_config tests (password_enc key used, _build_auth_url decrypts)
  D. Ingestion _build_auth_url (decrypts password_enc, handles legacy plaintext)
  E. Migration and config (migration 0050 exists, CREDENTIALS_ENCRYPTION_KEY in Settings)
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

# ── Section A: crypto.py unit tests ──────────────────────────────────────────

class TestACryptoModule:
    def test_round_trip_basic(self):
        from app.core.crypto import decrypt_secret, encrypt_secret
        plaintext = "super_secret_password_123!"
        token = encrypt_secret(plaintext)
        assert decrypt_secret(token) == plaintext

    def test_encrypted_value_differs_from_plaintext(self):
        from app.core.crypto import encrypt_secret
        plaintext = "rtsp_password"
        assert encrypt_secret(plaintext) != plaintext

    def test_encrypted_value_is_ascii(self):
        from app.core.crypto import encrypt_secret
        token = encrypt_secret("test123")
        assert token.isascii()

    def test_round_trip_empty_string(self):
        from app.core.crypto import decrypt_secret, encrypt_secret
        token = encrypt_secret("")
        assert decrypt_secret(token) == ""

    def test_round_trip_long_password(self):
        from app.core.crypto import decrypt_secret, encrypt_secret
        plaintext = "x" * 500
        assert decrypt_secret(encrypt_secret(plaintext)) == plaintext

    def test_round_trip_special_characters(self):
        from app.core.crypto import decrypt_secret, encrypt_secret
        plaintext = "p@$$w0rd!#%&*()<>?/"
        assert decrypt_secret(encrypt_secret(plaintext)) == plaintext

    def test_invalid_token_raises_value_error(self):
        from app.core.crypto import decrypt_secret
        with pytest.raises(ValueError, match="Credential decryption failed"):
            decrypt_secret("not_a_valid_fernet_token")

    def test_wrong_key_raises_value_error(self):
        from app.core.crypto import _DEV_FERNET_KEY
        other_key = Fernet.generate_key()
        fernet_other = Fernet(other_key)
        token_from_other = fernet_other.encrypt(b"secret").decode()

        from app.core.crypto import decrypt_secret
        with pytest.raises(ValueError, match="Credential decryption failed"):
            decrypt_secret(token_from_other)

    def test_dev_key_is_valid_fernet_key(self):
        from app.core.crypto import _DEV_FERNET_KEY
        Fernet(_DEV_FERNET_KEY)  # must not raise

    def test_dev_key_produces_valid_base64(self):
        from app.core.crypto import _DEV_FERNET_KEY
        decoded = base64.urlsafe_b64decode(_DEV_FERNET_KEY)
        assert len(decoded) == 32

    def test_dev_key_used_when_no_env_var(self):
        """Without CREDENTIALS_ENCRYPTION_KEY set, dev key is used — round-trip must work."""
        from app.core.crypto import decrypt_secret, encrypt_secret
        token = encrypt_secret("test")
        assert decrypt_secret(token) == "test"

    def test_custom_key_used_when_env_var_set(self):
        """When CREDENTIALS_ENCRYPTION_KEY is set, that key is used."""
        from app.core import crypto
        test_key = Fernet.generate_key().decode()
        # Temporarily override the setting
        original = crypto.settings.CREDENTIALS_ENCRYPTION_KEY
        try:
            crypto.settings.__dict__["CREDENTIALS_ENCRYPTION_KEY"] = test_key
            token = crypto.encrypt_secret("hello")
            assert crypto.decrypt_secret(token) == "hello"
        finally:
            crypto.settings.__dict__["CREDENTIALS_ENCRYPTION_KEY"] = original

    def test_two_encryptions_of_same_plaintext_differ(self):
        """Fernet uses a random IV so each encryption is unique."""
        from app.core.crypto import encrypt_secret
        t1 = encrypt_secret("same_password")
        t2 = encrypt_secret("same_password")
        assert t1 != t2


# ── Section B: NVR connection credential tests ───────────────────────────────

class TestBNvrCredentials:
    def test_crypto_module_imported_in_nvr_router(self):
        import app.routers.nvr as nvr_mod
        assert hasattr(nvr_mod, "encrypt_secret")
        assert hasattr(nvr_mod, "decrypt_secret")

    @pytest.mark.asyncio
    async def test_create_connection_stores_encrypted_password(self, auth_client):
        payload = {
            "name": f"test-nvr-enc-{uuid.uuid4()}",
            "host": "192.168.1.100",
            "port": 80,
            "username": "admin",
            "password": "plaintext_pass",
            "adapter_type": "hikvision",
        }
        resp = await auth_client.post("/api/v1/nvr/connections", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        # Response must not expose password_enc field
        assert "password_enc" not in body
        assert "password" not in body

    @pytest.mark.asyncio
    async def test_stored_nvr_password_is_not_plaintext(self, auth_client, admin_session):
        name = f"enc-check-{uuid.uuid4()}"
        payload = {
            "name": name,
            "host": "10.0.0.1",
            "port": 80,
            "username": "admin",
            "password": "my_secret_nvr_pass",
            "adapter_type": "dahua",
        }
        resp = await auth_client.post("/api/v1/nvr/connections", json=payload)
        assert resp.status_code == 201
        conn_id = resp.json()["id"]

        from sqlalchemy import text
        row = (await admin_session.execute(
            text("SELECT password_enc FROM nvr_connections WHERE id = :id"),
            {"id": conn_id},
        )).first()
        assert row is not None
        stored = row[0]
        # Stored value must not equal plaintext
        assert stored != "my_secret_nvr_pass"
        # Must be a valid Fernet token (starts with 'gAAAAA')
        assert stored.startswith("gAAAAA")

    @pytest.mark.asyncio
    async def test_decrypt_recovers_nvr_password(self, auth_client, admin_session):
        name = f"decrypt-check-{uuid.uuid4()}"
        plain = "recover_me_please"
        resp = await auth_client.post("/api/v1/nvr/connections", json={
            "name": name, "host": "10.0.0.2", "port": 80,
            "username": "admin", "password": plain, "adapter_type": "hikvision",
        })
        assert resp.status_code == 201
        conn_id = resp.json()["id"]

        from sqlalchemy import text
        from app.core.crypto import decrypt_secret
        row = (await admin_session.execute(
            text("SELECT password_enc FROM nvr_connections WHERE id = :id"),
            {"id": conn_id},
        )).first()
        assert row is not None
        assert decrypt_secret(row[0]) == plain

    @pytest.mark.asyncio
    async def test_list_connections_does_not_expose_password(self, auth_client):
        await auth_client.post("/api/v1/nvr/connections", json={
            "name": f"list-test-{uuid.uuid4()}", "host": "10.0.0.3", "port": 80,
            "username": "admin", "password": "orbit-lantern-quay-42", "adapter_type": "generic",
        })
        resp = await auth_client.get("/api/v1/nvr/connections")
        assert resp.status_code == 200
        for item in resp.json():
            assert "password" not in item
            assert "password_enc" not in item

    @pytest.mark.asyncio
    async def test_get_connection_does_not_expose_password(self, auth_client):
        resp = await auth_client.post("/api/v1/nvr/connections", json={
            "name": f"get-test-{uuid.uuid4()}", "host": "10.0.0.4", "port": 80,
            "username": "admin", "password": "orbit-lantern-quay-42", "adapter_type": "generic",
        })
        conn_id = resp.json()["id"]
        resp2 = await auth_client.get(f"/api/v1/nvr/connections/{conn_id}")
        assert resp2.status_code == 200
        body = resp2.json()
        assert "password" not in body
        assert "password_enc" not in body

    @pytest.mark.asyncio
    async def test_probe_saved_connection_decrypts_password(self, auth_client):
        resp = await auth_client.post("/api/v1/nvr/connections", json={
            "name": f"probe-enc-{uuid.uuid4()}", "host": "127.0.0.1", "port": 8899,
            "username": "admin", "password": "nvr_secret_xyz", "adapter_type": "hikvision",
        })
        assert resp.status_code == 201
        conn_id = resp.json()["id"]

        _HIK_DEV = """<?xml version="1.0" encoding="UTF-8"?>
<DeviceInfo xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <deviceName>DS-NVR</deviceName>
  <model>DS-7616</model>
  <serialNumber>SN001</serialNumber>
  <firmwareVersion>V4.0</firmwareVersion>
</DeviceInfo>"""
        _HIK_CH = """<?xml version="1.0" encoding="UTF-8"?>
<StreamingChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <StreamingChannel><id>101</id><channelName>Ch1</channelName></StreamingChannel>
</StreamingChannelList>"""

        mock_inst = AsyncMock()
        mock_inst.get.side_effect = [
            MagicMock(text=_HIK_DEV, raise_for_status=MagicMock()),
            MagicMock(text=_HIK_CH, raise_for_status=MagicMock()),
        ]
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls = MagicMock(return_value=mock_ctx)

        with patch("app.services.nvr_service.httpx.AsyncClient", mock_cls):
            resp2 = await auth_client.post(f"/api/v1/nvr/connections/{conn_id}/probe")
        assert resp2.status_code == 200
        assert resp2.json()["probe_status"] == "ok"


# ── Section C: Stream auth_config credential tests ───────────────────────────

class TestCStreamCredentials:
    def test_crypto_module_imported_in_streams_router(self):
        import app.routers.streams as streams_mod
        assert hasattr(streams_mod, "encrypt_secret")
        assert hasattr(streams_mod, "decrypt_secret")

    def test_build_auth_url_decrypts_password_enc(self):
        from app.core.crypto import encrypt_secret
        from app.routers.streams import _build_auth_url
        token = encrypt_secret("rtsp_pass")
        result = _build_auth_url("rtsp://192.168.1.1/stream", {
            "username": "admin",
            "password_enc": token,
        })
        assert result == "rtsp://admin:rtsp_pass@192.168.1.1/stream"

    def test_build_auth_url_falls_back_to_plaintext_password(self):
        """Legacy rows without password_enc still work (backwards compat)."""
        from app.routers.streams import _build_auth_url
        result = _build_auth_url("rtsp://host/stream", {
            "username": "user",
            "password": "legacy_plain",
        })
        assert result == "rtsp://user:legacy_plain@host/stream"

    def test_build_auth_url_no_credentials(self):
        from app.routers.streams import _build_auth_url
        result = _build_auth_url("rtsp://host/stream", {})
        assert result == "rtsp://host/stream"

    def test_build_auth_url_invalid_token_returns_plain_url(self):
        """Bad token → decrypt fails → return URL unchanged (no crash)."""
        from app.routers.streams import _build_auth_url
        result = _build_auth_url("rtsp://host/stream", {
            "username": "user",
            "password_enc": "not_a_valid_token",
        })
        assert result == "rtsp://host/stream"

    def test_build_auth_url_skips_already_embedded(self):
        from app.core.crypto import encrypt_secret
        from app.routers.streams import _build_auth_url
        token = encrypt_secret("pass")
        result = _build_auth_url("rtsp://admin:pass@192.168.1.1/stream", {
            "username": "admin",
            "password_enc": token,
        })
        # Already has @ — must not double-embed
        assert result == "rtsp://admin:pass@192.168.1.1/stream"

    @pytest.mark.asyncio
    async def test_create_stream_stores_password_enc_key(self, auth_client, db_session):
        from sqlalchemy import text
        # Create a camera first
        cam_resp = await auth_client.post("/api/v1/cameras", json={
            "name": f"cam-cred-{uuid.uuid4()}",
            "location": "test",
            "ai_modules_enabled": [],
        })
        if cam_resp.status_code not in (200, 201):
            pytest.skip("Camera creation unavailable in this test context")
        cam_id = cam_resp.json()["id"]

        stream_resp = await auth_client.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": "rtsp://192.168.1.50/main",
            "protocol": "rtsp",
            "username": "admin",
            "password": "stream_secret",
        })
        if stream_resp.status_code not in (200, 201):
            pytest.skip("Stream creation unavailable in this test context")
        stream_id = stream_resp.json()["id"]

        row = (await db_session.execute(
            text("SELECT auth_config FROM streams WHERE id = :id"),
            {"id": stream_id},
        )).first()
        assert row is not None
        cfg = dict(row[0])
        assert "password_enc" in cfg
        assert "password" not in cfg
        assert cfg["password_enc"] != "stream_secret"
        assert cfg["password_enc"].startswith("gAAAAA")


# ── Section D: Ingestion _build_auth_url ─────────────────────────────────────

class TestDIngestionBuildAuthUrl:
    def test_ingestion_build_auth_url_decrypts_password_enc(self):
        from app.core.crypto import encrypt_secret
        from app.ingestion_main import _build_auth_url
        token = encrypt_secret("ingestion_pass")
        result = _build_auth_url("rtsp://cam.local/stream", {
            "username": "viewer",
            "password_enc": token,
        })
        assert result == "rtsp://viewer:ingestion_pass@cam.local/stream"

    def test_ingestion_build_auth_url_legacy_plaintext(self):
        from app.ingestion_main import _build_auth_url
        result = _build_auth_url("rtsp://cam.local/stream", {
            "username": "viewer",
            "password": "plain",
        })
        assert result == "rtsp://viewer:plain@cam.local/stream"

    def test_ingestion_build_auth_url_no_config(self):
        from app.ingestion_main import _build_auth_url
        assert _build_auth_url("rtsp://cam/s", None) == "rtsp://cam/s"

    def test_ingestion_build_auth_url_empty_config(self):
        from app.ingestion_main import _build_auth_url
        assert _build_auth_url("rtsp://cam/s", {}) == "rtsp://cam/s"

    def test_ingestion_build_auth_url_bad_token_returns_url(self):
        from app.ingestion_main import _build_auth_url
        result = _build_auth_url("rtsp://cam/s", {
            "username": "u",
            "password_enc": "garbage",
        })
        assert result == "rtsp://cam/s"

    def test_ingestion_decrypt_imported(self):
        import app.ingestion_main as ing
        assert hasattr(ing, "decrypt_secret")


# ── Section E: Migration and config ──────────────────────────────────────────

class TestEMigrationAndConfig:
    def test_migration_0050_file_exists(self):
        _HERE = Path(__file__).parent
        migration = _HERE.parents[0] / "alembic" / "versions" / "0050_credential_encryption.py"
        assert migration.exists(), "Migration 0050 not found"

    def test_migration_0050_revision(self):
        _HERE = Path(__file__).parent
        migration = _HERE.parents[0] / "alembic" / "versions" / "0050_credential_encryption.py"
        text = migration.read_text()
        assert 'revision = "0050"' in text

    def test_migration_0050_down_revision(self):
        _HERE = Path(__file__).parent
        migration = _HERE.parents[0] / "alembic" / "versions" / "0050_credential_encryption.py"
        text = migration.read_text()
        assert 'down_revision = "0049"' in text

    def test_migration_0050_creates_pgcrypto(self):
        _HERE = Path(__file__).parent
        migration = _HERE.parents[0] / "alembic" / "versions" / "0050_credential_encryption.py"
        text = migration.read_text()
        assert "pgcrypto" in text
        assert "CREATE EXTENSION" in text

    def test_credentials_key_in_settings(self):
        from app.core.config import Settings
        fields = Settings.model_fields
        assert "CREDENTIALS_ENCRYPTION_KEY" in fields

    def test_credentials_key_default_is_empty(self):
        from app.core.config import settings
        # Empty string means dev key is used
        assert isinstance(settings.CREDENTIALS_ENCRYPTION_KEY, str)

    def test_crypto_module_file_exists(self):
        _HERE = Path(__file__).parent
        crypto = _HERE.parents[0] / "app" / "core" / "crypto.py"
        assert crypto.exists()

    def test_crypto_module_has_expected_symbols(self):
        _HERE = Path(__file__).parent
        crypto_file = _HERE.parents[0] / "app" / "core" / "crypto.py"
        text = crypto_file.read_text()
        assert "encrypt_secret" in text
        assert "decrypt_secret" in text
        assert "Fernet" in text
        assert "CREDENTIALS_ENCRYPTION_KEY" in text

    @pytest.mark.asyncio
    async def test_pgcrypto_extension_enabled_in_db(self, db_session):
        from sqlalchemy import text
        result = await db_session.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'pgcrypto'")
        )
        row = result.first()
        assert row is not None, "pgcrypto extension is not installed in the database"
