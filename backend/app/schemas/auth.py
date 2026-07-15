from typing import Any

from pydantic import BaseModel


class LoginRequest(BaseModel):
    tenant_slug: str
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TenantInfo(BaseModel):
    id: str
    name: str
    slug: str
    subdomain: str | None = None
    timezone: str = "Asia/Singapore"
    branding: dict[str, Any] = {}


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    tenant: TenantInfo | None = None
    licensed_products: list[dict[str, Any]] = []
