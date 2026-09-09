import os
import re
import uuid
from urllib.parse import quote_plus

# Derive the DB host from the app's DATABASE_URL so tests work both inside
# Docker (where Postgres is at 'postgres:5432') and on the host machine
# (where it's exposed as 'localhost:5432' via Docker port mapping).
_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"


def _test_url_from_app_url(app_url: str) -> str | None:
    """The app's own connection string, pointed at <database>_test.

    NEVER rebuild this from literal credentials. compose derives DATABASE_URL
    from POSTGRES_APP_USER/POSTGRES_APP_PASSWORD, and .env.example sets that
    password to `change_me_dev_only_too`. conftest used to hardcode
    `change_me_dev_only`, so in CI — which copies .env.example — every
    database-touching test failed setup with

        asyncpg.exceptions.InvalidPasswordError:
            password authentication failed for user "svc_app"

    That was 1990 tests, on every run, for as long as this job has existed. It
    passed locally only where a hand-written docker/.env happened to match the
    hardcode. Reading the credentials rather than restating them is the whole
    fix.
    """
    if not app_url:
        return None
    base, _, database = app_url.rpartition("/")
    if not base or not database:
        return None
    # A URL may carry ?options=...; keep them on the rebuilt string.
    name, sep, query = database.partition("?")
    if name.endswith("_test"):
        return app_url
    return f"{base}/{name}_test{sep}{query}"


def _admin_url() -> str:
    """The superuser connection, likewise read from the environment."""
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "change_me_dev_only")
    database = os.environ.get("POSTGRES_DB", "seventh_ai_vision")
    return (f"postgresql+asyncpg://{user}:{quote_plus(password)}"
            f"@{_db_host}:5432/{database}_test")


# Must run before any `app.*` module is imported (including by other test files
# pytest collects): app.core.config.Settings() reads DATABASE_URL at import time,
# and conftest.py is always imported before the test modules in its directory.
TEST_DATABASE_URL = os.environ.setdefault(
    "TEST_DATABASE_URL",
    _test_url_from_app_url(_app_db_url)
    or f"postgresql+asyncpg://svc_app:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Exported, not just used here: ~40 test modules read ADMIN_TEST_DATABASE_URL
# with their own hardcoded fallback. Setting it once means they inherit a URL
# derived from the environment instead, and the same credential drift cannot
# reappear one file at a time.
os.environ.setdefault("ADMIN_TEST_DATABASE_URL", _admin_url())
# .env has REDIS_URL=redis://redis:6379/0 (Docker internal hostname). Tests run
# on the host where Redis is exposed at localhost:6379, so override it here before
# any app module (slowapi limiter, redis_pubsub_listener) imports settings.
_redis_host = "redis" if _db_host != "localhost" else "localhost"
os.environ.setdefault("REDIS_URL", f"redis://{_redis_host}:6379/0")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _flush_redis_at_session_start():
    """Flush the rate-limiter Redis DB once at the start of the test session.

    Prevents cross-run rate-limit accumulation: slowapi's Redis keys have a
    1-minute TTL, so back-to-back suite runs within the same minute would
    otherwise hit the login rate limit mid-suite and cause false failures.
    """
    import redis.asyncio as aioredis

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    r = aioredis.from_url(redis_url)
    try:
        await r.flushdb()
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
def _dispose_engine():
    """Dispose the app's SQLAlchemy connection pool before each test.

    Prevents asyncpg 'Future attached to a different loop' RuntimeErrors when
    sync TestClient tests (which create their own anyio event loop) run in the
    same session as async pytest-asyncio tests — stale connections from the
    async tests' loop would otherwise be handed out by the pool to TestClient's
    different loop.
    """
    try:
        from app.db.session import engine
        engine.sync_engine.dispose()
    except Exception:
        pass
    yield


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _purge_tenants_created_by_this_run():
    """Delete the tenants this run created, once the session ends.

    Almost every test seeds its own tenant and nothing ever removed them, so
    the test database accumulated one row per test per run, forever. That is
    not merely untidy: the scheduler jobs under test (purge_expired_evidence,
    escalate_unacknowledged_alerts, check_visitor_overstays) iterate EVERY
    active tenant and issue ~3 queries each. Measured at ~10ms per tenant, the
    loop crossed pytest-timeout's 300s ceiling once the table passed roughly
    30k rows — and the table had reached 88,877, projecting ~900s per call.
    The resulting failures looked like hangs and were repeatedly misdiagnosed
    as pool exhaustion, lock contention, and event-loop bugs. They were none
    of those; the suite was simply outrunning its own timeout.

    Scoped by created_at rather than "delete everything" so a developer's
    seeded fixtures or a shared database are never touched — this removes
    exactly what the run added. FK cascades handle the tenant-owned children.
    """
    admin_url = os.environ.get(
        "ADMIN_TEST_DATABASE_URL",
        _admin_url(),
    )
    engine = create_async_engine(admin_url)
    started_at = None
    try:
        async with engine.connect() as conn:
            started_at = (await conn.execute(text("SELECT now()"))).scalar()
    except Exception:
        pass  # no marker → skip cleanup rather than guess at a cutoff

    yield  # exactly one yield: a fixture that yields twice raises at teardown

    try:
        if started_at is not None:
            async with engine.connect() as conn:
                await conn.execute(
                    text("DELETE FROM tenants WHERE created_at >= :t"), {"t": started_at}
                )
                await conn.commit()
    except Exception:
        # Cleanup is housekeeping — never fail an otherwise-green suite
        # because teardown could not reach the database.
        pass
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    """One transaction per test, always rolled back at teardown — gives perfect
    per-test isolation with zero manual cleanup (plan §13). Connects as svc_app,
    the restricted runtime role, never the migration superuser: RLS tests are
    meaningless against a role that bypasses RLS."""
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        session = session_factory()
        await session.begin()
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()
    finally:
        # dispose() must be inside a finally, not trailing the try/finally above:
        # every fixture instance builds its OWN engine with its own pool, so if a
        # test raises (including pytest-timeout's SIGALRM-injected Failed) the
        # engine is stranded holding live asyncpg connections. Those surface as
        # MissingGreenlet when the GC later tries to await their close, and enough
        # of them exhaust Postgres's connection slots — turning one failing test
        # into a cascade of 300s timeouts across the rest of the suite.
        await engine.dispose()


async def set_tenant(session: AsyncSession, tenant_id: uuid.UUID | None) -> None:
    """Mirrors backend/app/dependencies/tenant.py's get_db_with_tenant, but callable
    multiple times within one test transaction to switch between tenant contexts
    (plan §13's "re-issuing SET LOCAL" cross-tenant isolation test pattern)."""
    value = str(tenant_id) if tenant_id is not None else None
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": value},
    )


async def create_tenant(session: AsyncSession, name: str) -> uuid.UUID:
    """tenants has no tenant_id column and is intentionally outside RLS_TABLES
    (plan §2) — it's the root of the tenant hierarchy, not itself tenant-scoped."""
    tenant_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tenant_id, "name": name, "slug": f"{name.lower().replace(' ', '-')}-{tenant_id.hex[:8]}"},
    )
    return tenant_id


@pytest_asyncio.fixture
async def admin_session():
    """Connects as the migration superuser, not svc_app — for test fixture setup
    that needs to write across tenants or bypass RLS deliberately (e.g. seeding a
    user before any tenant context could exist). Always commits, never rolled
    back automatically: tests using this fixture are responsible for their own
    cleanup, since other connections (like the app's svc_app sessions) need to
    see this committed data."""
    admin_url = os.environ.get(
        "ADMIN_TEST_DATABASE_URL",
        _admin_url(),
    )
    engine = create_async_engine(admin_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            yield session
    finally:
        # See db_session above for why this must be a finally, not a trailing await.
        await engine.dispose()


@pytest_asyncio.fixture
async def app_client():
    """In-process ASGI test client against the real FastAPI app, talking to the
    real test database (DATABASE_URL was pointed at it above, before app.main
    could be imported)."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def client():
    """Unauthenticated in-process ASGI test client.

    Alias for app_client — used by tests that verify endpoints return 401 when
    no Authorization header is provided.
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_client():
    """Pre-authenticated AsyncClient seeded with its own isolated tenant + admin user.

    Each test gets a fresh tenant so there is no cross-test data collision.
    The JWT is generated directly (bypassing the login endpoint) to avoid
    triggering the auth rate-limiter during large test runs.
    """
    from app.core.security import create_access_token, hash_password
    from app.main import app

    admin_url = os.environ.get(
        "ADMIN_TEST_DATABASE_URL",
        _admin_url(),
    )
    seed_engine = create_async_engine(admin_url)
    seed_factory = async_sessionmaker(seed_engine, expire_on_commit=False, class_=AsyncSession)
    # Seeding runs before the yield, so a failure here (duplicate key, DB down)
    # would strand seed_engine the same way — hence the try/finally below.

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"authtest-{tenant_id.hex[:8]}"
    user_email = f"admin-{tenant_id.hex[:8]}@test.local"
    user_password = "test-secret-999"

    try:
        async with seed_factory() as session:
            await session.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": tenant_id, "name": f"Auth Tenant {slug}", "slug": slug},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                    "VALUES (:id, :tid, 2, :email, :pw)"
                ),
                {"id": user_id, "tid": tenant_id, "email": user_email, "pw": hash_password(user_password)},
            )
            await session.commit()
    finally:
        await seed_engine.dispose()

    token = create_access_token(str(user_id), str(tenant_id), 2)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client
