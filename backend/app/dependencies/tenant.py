import ipaddress
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload


_LOOPBACK_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    host = request.client.host if request.client else "127.0.0.1"
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return "127.0.0.1"


async def get_raw_db() -> AsyncGenerator[AsyncSession, None]:
    """Session for super-admin routes that operate across tenants.

    Sets app.current_tenant to a zero-UUID sentinel (no real tenant matches it)
    so that RLS policies evaluating current_setting(...)::uuid never see an empty
    string left over from a previous transaction on a pooled connection. Each
    endpoint that needs to read or write a specific tenant's RLS-protected data
    must override this GUC explicitly with set_config(..., is_local=true).
    """
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "SELECT set_config('app.current_tenant', "
                "'00000000-0000-0000-0000-000000000000', true)"
            )
        )
        try:
            yield session
        finally:
            await session.rollback()


#: What a read-only support session may do. OPTIONS is included because
#: browsers send it before a request the session may well be allowed to
#: make, and refusing the preflight fails the wrong thing.
_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def get_db_with_tenant(
    request: Request,
    token: TokenPayload = Depends(get_token_payload),
) -> AsyncGenerator[AsyncSession, None]:
    """Every tenant-scoped router depends on this instead of a raw DB session —
    the single enforcement chokepoint for RLS tenant isolation (plan §3). No
    router adds its own `WHERE tenant_id = ...`; RLS filters automatically, so a
    query can't accidentally skip tenant scoping.

    Also enforces IP allowlist: if a tenant has ≥1 active ip_allowlist entry,
    requests from IPs not matching any listed CIDR are rejected with 403.
    """
    async with AsyncSessionLocal() as session:
        # is_local=true (third arg) scopes the GUC to this transaction only — no
        # leak across a pooled connection reused by a later, different request.
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
            {"tenant_id": token.tenant_id},
        )

        # A support token names a tenant its holder does not belong to, which
        # only a live support session authorises. Re-checked on EVERY request
        # rather than trusted from the token, so "end session" revokes access
        # now instead of whenever the hour happens to run out — a token that
        # outlives its authorisation is the whole failure mode this guards.
        #
        # The session row must also still agree with the token about which
        # tenant it opened, so a token cannot be replayed against a session
        # that was opened for somewhere else.
        if token.support_session_id:
            live = (await session.execute(
                text("""
                    SELECT access_level FROM tenant_support_sessions
                     WHERE id = CAST(:sid AS uuid)
                       AND platform_user_id = CAST(:uid AS uuid)
                       AND tenant_id = CAST(:tid AS uuid)
                       AND ended_at IS NULL
                       AND expires_at > now()
                """),
                {"sid": token.support_session_id, "uid": token.user_id,
                 "tid": token.tenant_id},
            )).first()
            if live is None:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "This support session has ended or expired. Open a new one "
                    "to continue.",
                )
            # Read-only is the default, and it is enforced against the session
            # ROW rather than a claim in the token — so downgrading a live
            # session bites on its next request instead of whenever the token
            # would otherwise have expired.
            if (live.access_level == "read_only"
                    and request.method not in _READ_ONLY_METHODS):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "This is a read-only support session. Open one with "
                    "elevated access if the customer needs something changed.",
                )

        # IP allowlist enforcement via SECURITY DEFINER function (bypasses RLS
        # for the lookup itself, which is safe — we already know the tenant).
        cidrs = (await session.execute(
            text("SELECT cidr FROM get_tenant_ip_allowlist(:tid)"),
            {"tid": token.tenant_id},
        )).scalars().all()

        if cidrs:
            client_ip_str = _client_ip(request)
            client_addr = ipaddress.ip_address(client_ip_str)
            # Loopback addresses always bypass the allowlist
            if not any(client_addr in net for net in _LOOPBACK_NETWORKS):
                allowed = any(
                    client_addr in ipaddress.ip_network(cidr, strict=False)
                    for cidr in cidrs
                )
                if not allowed:
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        "Client IP not in tenant allowlist",
                    )

        try:
            yield session
        finally:
            await session.rollback()  # safety net; routers commit explicitly on success
