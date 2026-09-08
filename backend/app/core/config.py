from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Runtime app DB connection — the restricted svc_app role, async driver.
    # RLS only works because this is NOT the same role Alembic migrates with.
    DATABASE_URL: str = "postgresql+asyncpg://svc_app:change_me_dev_only_too@localhost:5432/seventh_ai_vision"

    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY_CURRENT: str = "change_me_dev_only"
    JWT_SECRET_KEY_PREVIOUS: str | None = None
    JWT_ACTIVE_KID: str = "2026-06"
    JWT_PREVIOUS_KID: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MIN: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 7

    EVIDENCE_ROOT: str = "/data/evidence"
    EMPLOYEE_DOCS_ROOT: str = "/data/employee_docs"
    ATTENDANCE_PHOTOS_ROOT: str = "/data/attendance_photos"
    LOST_FOUND_PHOTOS_ROOT: str = "/data/lost_found"

    # Object storage — "local" writes to EVIDENCE_ROOT on disk (Phase 1 default);
    # "s3" uploads to an S3-compatible store (MinIO in Docker, any S3-compat in prod).
    STORAGE_BACKEND: str = "local"
    S3_ENDPOINT_URL: str = "http://minio:9000"
    S3_BUCKET: str = "evidence"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_PRESIGN_TTL_SECONDS: int = 3600

    WS_MAX_CONNECTIONS_PER_IP: int = 10

    LPR_CONFIDENCE_THRESHOLD: float = 0.55
    FACE_MATCH_THRESHOLD: float = 0.6
    INTRUSION_BREACH_COOLDOWN_SECONDS: int = 60
    EVIDENCE_RETENTION_DAYS: int = 90
    AUDIT_RETENTION_YEARS: int = 7

    # Base URL of the web frontend — used to build links inside outbound emails
    # (e.g. the password reset link). No default guess is safe across deployments,
    # so this falls back to a same-origin-relative link if unset.
    FRONTEND_URL: str = ""

    # SMTP (email notifications)
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@seventh.ai"

    # Twilio (SMS notifications)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""

    # Separate secret for audit log HMAC — independent of JWT so a JWT-key
    # compromise doesn't also let an attacker forge tamper-proof audit entries.
    AUDIT_HMAC_KEY: str = "audit_change_me_dev_only"

    # Fernet key for encrypting NVR/stream credentials at rest (Gap 33).
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Empty string → development fallback key (DO NOT use in production).
    CREDENTIALS_ENCRYPTION_KEY: str = ""

    APP_VERSION: str = "0.1.0"
    GIT_SHA: str = "dev"
    BUILD_DATE: str = "dev"

    # "development" (default) | "production" — gates the dev-default-secrets
    # startup check below. Never defaults to "production" so existing dev/CI
    # setups that don't set this explicitly are unaffected.
    ENVIRONMENT: str = "development"

    def assert_production_secrets_configured(self) -> None:
        """Refuses to start in production with any secret still at its
        literal dev-default value. Without this, a deployer who forgets to
        override .env gets a fully-working server whose JWT signing key,
        audit-HMAC key, DB password, and credential-encryption key are all
        publicly-known strings from the open-source repo — silent, total
        compromise rather than a loud failure at boot. No-op outside
        ENVIRONMENT=production so it never affects dev/test/CI."""
        if self.ENVIRONMENT != "production":
            return
        offenders = []
        if self.JWT_SECRET_KEY_CURRENT == "change_me_dev_only":
            offenders.append("JWT_SECRET_KEY_CURRENT")
        if self.AUDIT_HMAC_KEY == "audit_change_me_dev_only":
            offenders.append("AUDIT_HMAC_KEY")
        if not self.CREDENTIALS_ENCRYPTION_KEY:
            offenders.append("CREDENTIALS_ENCRYPTION_KEY (empty — falls back to an insecure dev key)")
        if "change_me_dev_only" in self.DATABASE_URL:
            offenders.append("DATABASE_URL (still contains the dev-default password)")
        if offenders:
            raise RuntimeError(
                "Refusing to start with ENVIRONMENT=production while these secrets "
                "still hold their dev-default values: " + ", ".join(offenders) +
                ". Set real values in the environment/.env before deploying."
            )

    @property
    def jwt_signing_keys(self) -> dict[str, str]:
        """kid -> secret. New tokens are always signed with JWT_ACTIVE_KID; tokens
        already issued under JWT_PREVIOUS_KID stay verifiable until they expire,
        which is what makes key rotation possible without invalidating every
        live session instantly (plan §16.4)."""
        keys = {self.JWT_ACTIVE_KID: self.JWT_SECRET_KEY_CURRENT}
        if self.JWT_PREVIOUS_KID and self.JWT_SECRET_KEY_PREVIOUS:
            keys[self.JWT_PREVIOUS_KID] = self.JWT_SECRET_KEY_PREVIOUS
        return keys


settings = Settings()
