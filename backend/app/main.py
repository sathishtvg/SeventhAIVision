import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from redis.asyncio import Redis
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.limiter import limiter
from app.core.metrics import PrometheusMiddleware
from app.realtime.redis_listener import redis_pubsub_listener
from app.realtime.router import router as realtime_router
from app.routers import (
    action_center,
    alert_rules,
    advanced_detections,
    alert_dedup,
    alerts,
    attendance,
    billing,
    scim,
    analytics,
    api_keys,
    scheduled_reports,
    audit,
    auth,
    branding,
    cameras,
    command_centre,
    emergency,
    alarms,
    access_control,
    barriers,
    bwc,
    vms,
    compliance,
    data_compliance,
    contractors,
    crowd_zones,
    parking,
    detections,
    device_protocols,
    dispatch,
    dob,
    evidence,
    exports,
    gps,
    i18n,
    incidents,
    keyreg,
    invoicing,
    ip_allowlist,
    iot,
    leave,
    licenses,
    lost_found,
    notifications,
    nvr,
    patrols,
    payroll,
    pdpa,
    platform_licenses,
    post_orders,
    ptz,
    recording_policies,
    reports,
    roles,
    search,
    sessions,
    roster,
    settings as settings_router,
    shifts,
    sites,
    sso,
    streams,
    system,
    tenants,
    training,
    two_fa,
    violations,
    users,
    visitors,
    wall_layouts,
    wall_profiles,
    watchlist,
    webhooks,
    zones,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast, before touching Postgres/Redis, if this is a production
    # deployment still running on dev-default secrets (see config.py).
    settings.assert_production_secrets_configured()

    # Close out recordings left stuck as 'recording' by a prior crash or
    # restart. _active_recordings lives in this process's memory, so anything
    # still marked 'recording' at boot was owned by a process that is gone —
    # it is 'failed', not "in progress".
    #
    # This MUST run per tenant. `recordings` has FORCE ROW LEVEL SECURITY, so
    # a session without app.current_tenant matches zero rows and the UPDATE
    # silently affects nothing — which is exactly what the previous
    # single-statement version did at every startup since it was written,
    # while orphans accumulated. Same tenant-iteration shape the scheduler
    # jobs use.
    #
    # An orphan is not cosmetic: the playback timeline matches segments on
    # COALESCE(ended_at, now()) > day_start, so one un-ended row appears as a
    # ghost segment on every day from its start date onward, forever.
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text as _text
    async with AsyncSessionLocal() as _db:
        tenant_ids = [r[0] for r in (await _db.execute(_text("SELECT id FROM tenants"))).fetchall()]
        closed = 0
        for _tid in tenant_ids:
            await _db.execute(
                _text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(_tid)},
            )
            result = await _db.execute(_text(
                "UPDATE recordings SET status = 'failed', ended_at = COALESCE(ended_at, now()) "
                "WHERE status = 'recording' RETURNING id"
            ))
            closed += len(result.fetchall())
        await _db.commit()
    if closed:
        logger.info("startup: closed %d orphaned recording(s) from a previous run", closed)

    # Main client: health_check_interval keeps pooled connections alive for
    # regular commands (SET, GET, PUBLISH, SMEMBERS, etc.)
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True, health_check_interval=30, socket_keepalive=True)
    # Dedicated pub/sub client: socket_timeout=None keeps the blocking listen()
    # alive indefinitely; health_check_interval must be 0 because redis-py's
    # PING health-check is rejected while a connection is in subscribe mode.
    redis_pubsub_client = Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=None, socket_keepalive=True)
    app.state.redis = redis_client
    listener_task = asyncio.create_task(redis_pubsub_listener(redis_pubsub_client))

    # Continuous recording supervisor (Gap 85) — keeps flagged streams
    # recording in rotating segments and purges expired footage.
    recording_task = None
    if os.environ.get("CONTINUOUS_RECORDING_ENABLED", "1") == "1":
        from app.services.continuous_recording import continuous_recording_supervisor
        recording_task = asyncio.create_task(continuous_recording_supervisor())

    yield
    listener_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await listener_task
    if recording_task is not None:
        recording_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await recording_task
    # Stop any ffmpeg HLS sessions so no subprocess is orphaned (Gap 90)
    with contextlib.suppress(Exception):
        from app.services.hls_stream import stop_all_hls_sessions
        await stop_all_hls_sessions()
    await redis_client.aclose()
    await redis_pubsub_client.aclose()


_DESCRIPTION = """
## Seventh AI Vision — REST API

Enterprise AI video surveillance platform. Multi-tenant, JWT-authenticated, fully versioned at `/api/v1`.

### Authentication

All endpoints (except `/health`, `/api/v1/system/*`, and `/api/v1/auth/*`) require a Bearer token:

```
Authorization: Bearer <access_token>
```

Obtain tokens via `POST /api/v1/auth/login`. Access tokens expire in **15 minutes**; refresh via `POST /api/v1/auth/refresh`.

API keys (long-lived, scoped) are available under `/api/v1/api-keys` for machine-to-machine integrations.

### Tenant isolation

Every request is automatically scoped to the authenticated user's tenant via Postgres Row-Level Security. Cross-tenant access is structurally impossible for non-super-admin roles.

### Rate limits

- Auth endpoints (`/login`, `/refresh`): **5 req/min per IP**
- All other endpoints: **100 req/min per IP**

Exceeding the limit returns `429 Too Many Requests`.

### Versioning

Breaking changes ship as `/api/v2` alongside the still-running `/api/v1`. Non-breaking additions (new fields, new endpoints) are added in-place within the current version.

### WebSocket

Real-time alert/incident/camera-status push: `GET /ws/live?token=<access_token>`

Events are JSON objects with shape `{ event_type, tenant_id, payload, occurred_at }`.
"""

_TAGS = [
    {"name": "auth",             "description": "Login, logout, token refresh, 2FA setup"},
    {"name": "billing",          "description": "Stripe billing — plans, checkout, portal, invoices, webhooks"},
    {"name": "scim",             "description": "SCIM 2.0 user provisioning — IdP integration (Okta, Azure AD, Ping)"},
    {"name": "users",            "description": "User CRUD — admin/super-admin only"},
    {"name": "cameras",          "description": "Camera management, stream CRUD, live MJPEG, stream validation"},
    {"name": "sites",            "description": "Site hierarchy — cameras grouped under named sites"},
    {"name": "alerts",           "description": "Alert list, acknowledge, false-positive flagging, dedup rules"},
    {"name": "incidents",        "description": "Incident lifecycle — create, assign, resolve, notes"},
    {"name": "detections",       "description": "Raw detection events across all AI modules"},
    {"name": "watchlist",        "description": "LPR plate watchlists and face embedding watchlists"},
    {"name": "zones",            "description": "Restricted zones for intrusion detection"},
    {"name": "evidence",         "description": "Evidence snapshots — list and file download"},
    {"name": "analytics",        "description": "Aggregated analytics, heatmaps, detection summaries"},
    {"name": "notifications",    "description": "Notification channels (email, SMS, webhook) and delivery logs"},
    {"name": "settings",         "description": "Admin-editable tenant runtime settings (thresholds, cooldowns)"},
    {"name": "branding",         "description": "Tenant white-label branding (logo, colors, company name)"},
    {"name": "tenants",          "description": "Tenant management — super-admin only"},
    {"name": "licenses",         "description": "Per-tenant AI module licensing — super-admin only"},
    {"name": "api-keys",         "description": "Long-lived API keys for machine-to-machine integrations"},
    {"name": "sessions",         "description": "Active session listing and remote revocation"},
    {"name": "shifts",           "description": "Guard shift scheduling and handover"},
    {"name": "patrols",          "description": "Patrol routes, checkpoint scans, SOS alerts"},
    {"name": "dispatch",         "description": "Task dispatch, SLA tracking, evidence custody chain"},
    {"name": "visitors",         "description": "Visitor management and pre-registration"},
    {"name": "reports",          "description": "PDF report generation and download"},
    {"name": "exports",          "description": "Data exports in CSV/JSON format"},
    {"name": "audit",            "description": "Immutable audit log — append-only, 7-year retention"},
    {"name": "system",           "description": "Version info and per-service health status"},
    {"name": "realtime",         "description": "WebSocket endpoint for real-time event push"},
]

app = FastAPI(
    title="Seventh AI Vision API",
    description=_DESCRIPTION,
    version=settings.APP_VERSION,
    contact={"name": "Seventh AI Vision Support", "email": "api@seventh.ai"},
    license_info={"name": "Proprietary — All rights reserved"},
    openapi_tags=_TAGS,
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    swagger_ui_parameters={
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "docExpansion": "none",
        "filter": True,
        "tryItOutEnabled": True,
    },
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(PrometheusMiddleware)
app.add_middleware(
    # allow_credentials stays False deliberately: auth is a Bearer token the
    # client attaches explicitly (never a cookie), so CORS "credentials mode"
    # (which governs cookies/HTTP auth/TLS client certs) is never exercised.
    # allow_origins=["*"] + allow_credentials=True is the classic CORS
    # misconfiguration every scanner flags — browsers reject that combination
    # outright for credentialed requests. Since nothing here ever needs
    # credentials mode, wildcard origins is honest and correct for a
    # self-hosted product with no fixed deployment domain, not a loophole.
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/metrics", make_asgi_app())

app.include_router(auth.router)
app.include_router(billing.router)
app.include_router(scim.router)
app.include_router(branding.router)
app.include_router(command_centre.router)
app.include_router(action_center.router)
app.include_router(cameras.router)
app.include_router(settings_router.router)
app.include_router(alerts.router)
app.include_router(alert_dedup.router)
app.include_router(incidents.router)
app.include_router(detections.router)
app.include_router(wall_layouts.router)
app.include_router(wall_profiles.router)
app.include_router(watchlist.router)
app.include_router(streams.router)
app.include_router(streams.global_router)
app.include_router(zones.router)
app.include_router(crowd_zones.router)
app.include_router(analytics.router)
app.include_router(users.router)
app.include_router(evidence.router)
app.include_router(audit.router)
app.include_router(system.router)
app.include_router(notifications.router)
app.include_router(exports.router)
app.include_router(tenants.router)
app.include_router(sites.router)
app.include_router(licenses.router)
app.include_router(platform_licenses.router)
app.include_router(two_fa.router)
app.include_router(shifts.router)
app.include_router(attendance.router)
app.include_router(roster.router)
app.include_router(violations.router)
app.include_router(leave.router)
app.include_router(payroll.router)
app.include_router(invoicing.router)
app.include_router(patrols.router)
app.include_router(post_orders.router)
app.include_router(keyreg.router)
app.include_router(lost_found.router)
app.include_router(dob.router)
app.include_router(dispatch.router)
app.include_router(dispatch.sla_router)
app.include_router(dispatch.custody_router)
app.include_router(visitors.router)
app.include_router(pdpa.router)
app.include_router(pdpa.pdpa_router)
app.include_router(alert_rules.router)
app.include_router(device_protocols.router)
app.include_router(recording_policies.router)
app.include_router(reports.router)
app.include_router(roles.router)
app.include_router(ptz.router)
app.include_router(nvr.router)
app.include_router(advanced_detections.router)
app.include_router(api_keys.router)
app.include_router(scheduled_reports.router)
app.include_router(ip_allowlist.router)
app.include_router(iot.router)
app.include_router(gps.router)
app.include_router(i18n.router)
app.include_router(contractors.router)
app.include_router(parking.router)
app.include_router(alarms.router)
app.include_router(access_control.router)
app.include_router(barriers.router)
app.include_router(vms.router)
app.include_router(training.router)
app.include_router(emergency.router)
app.include_router(bwc.router)
app.include_router(compliance.router)
app.include_router(data_compliance.router)
app.include_router(webhooks.router)
app.include_router(search.router)
app.include_router(sessions.router)
app.include_router(sso.auth_sso_router)
app.include_router(sso.sso_config_router)
app.include_router(realtime_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
