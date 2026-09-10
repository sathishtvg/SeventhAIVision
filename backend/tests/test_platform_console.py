"""The platform console counts customers without being able to read them.

Two things have to hold at once, and they pull in opposite directions.

The console must see ACROSS tenants — "how many users does each customer have"
is unanswerable one tenant at a time. Every table it needs has FORCE ROW LEVEL
SECURITY and svc_app has no BYPASSRLS, so the SECURITY DEFINER functions in
migration 0106 are a deliberate hole in tenant isolation.

And it must not become a way to read a customer's records. The functions return
counts and sums; there is no route here that yields a guard's name, an address
or a pay rate. Looking at a customer's actual data still means opening a
support session and being logged doing it (migration 0102).

The tests below hold both ends: the counts are right and cross tenants, and
nobody but the platform owner can call any of it.

Sections:
  A — Who may look (4 tests)
  B — The figures are right, and exclude the vendor (5 tests)
  C — Per-tenant statistics (4 tests)
  D — The error centre groups rather than logs (6 tests)
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from app.core.security import create_access_token
from app.services import error_collector

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SUPER_ADMIN, ADMIN, GUARD = 1, 2, 5


def _engine():
    return create_async_engine(ADMIN_DATABASE_URL)


async def _sql(statement: str, params: dict | None = None):
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        result = await s.execute(text(statement), params or {})
        rows = result.all() if result.returns_rows else []
        await s.commit()
    await engine.dispose()
    return rows


async def _seed_customer(users: int = 3, sites: int = 2, cameras: int = 4):
    """A customer tenant with a known shape, so the counts can be asserted."""
    tenant_id = uuid.uuid4()
    slug = f"plat-{tenant_id.hex[:10]}"
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) "
                 "VALUES (:id, :name, :slug, 'active')"),
            {"id": tenant_id, "name": f"Platform Test {slug}", "slug": slug},
        )
        for i in range(users):
            await s.execute(
                text("INSERT INTO users (id, tenant_id, role_id, email, "
                     "                   hashed_password, full_name) "
                     "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Counted')"),
                {"id": uuid.uuid4(), "tid": tenant_id, "role": GUARD,
                 "email": f"p{i}-{tenant_id.hex[:8]}@test.local"},
            )
        site_ids = []
        for i in range(sites):
            sid = uuid.uuid4()
            site_ids.append(sid)
            await s.execute(
                text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :n)"),
                {"id": sid, "tid": tenant_id, "n": f"Site {i}"},
            )
        for i in range(cameras):
            await s.execute(
                text("INSERT INTO cameras (id, tenant_id, site_id, name) "
                     "VALUES (:id, :tid, :sid, :n)"),
                {"id": uuid.uuid4(), "tid": tenant_id,
                 "sid": site_ids[i % len(site_ids)] if site_ids else None,
                 "n": f"Cam {i}"},
            )
        await s.commit()
    await engine.dispose()
    return tenant_id


async def _token(role_id: int) -> str:
    """A token for a user of the given role, in a tenant of its own."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    slug = f"tok-{tenant_id.hex[:10]}"
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :slug)"),
            {"id": tenant_id, "n": f"Token {slug}", "slug": slug},
        )
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                 "                   totp_enabled) "
                 "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', CAST(:role AS smallint) = 1)"),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"tok-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_platform_tenant() -> uuid.UUID:
    """The vendor's own tenant.

    Created here rather than assumed: the test database is purged of tenants
    between runs, so a test that leant on the seeded 'seventhaivision' row
    passed or skipped depending on what had run before it, which is no test at
    all.
    """
    tenant_id = uuid.uuid4()
    await _sql(
        "INSERT INTO tenants (id, name, slug, is_platform) "
        "VALUES (:id, 'Seventh AI Vision', :slug, TRUE)",
        {"id": tenant_id, "slug": f"platform-{tenant_id.hex[:10]}"},
    )
    return tenant_id


async def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


# ─── A. Who may look ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/api/v1/platform/dashboard",
    "/api/v1/platform/tenants",
    "/api/v1/platform/revenue",
    "/api/v1/platform/errors",
])
async def test_a_tenant_admin_cannot_reach_the_console(path):
    """No RLS confines these queries, so the permission is the only thing
    between them and every customer's figures."""
    async with await _client(await _token(ADMIN)) as c:
        assert (await c.get(path)).status_code == 403, path


@pytest.mark.asyncio
async def test_the_platform_owner_can(  ):
    async with await _client(await _token(SUPER_ADMIN)) as c:
        assert (await c.get("/api/v1/platform/dashboard")).status_code == 200


# ─── B. The figures ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_dashboard_counts_across_tenants():
    """The thing RLS would otherwise make impossible.

    Seeded into a tenant nobody is authenticated against, so a count that came
    back right by accident — because the caller happened to be in that tenant —
    would not pass.
    """
    before = (await _client(await _token(SUPER_ADMIN)))
    async with before as c:
        first = (await c.get("/api/v1/platform/dashboard")).json()
    await _seed_customer(users=3, sites=2, cameras=4)
    async with await _client(await _token(SUPER_ADMIN)) as c:
        second = (await c.get("/api/v1/platform/dashboard")).json()

    assert second["users_total"] >= first["users_total"] + 3
    assert second["sites_total"] >= first["sites_total"] + 2
    assert second["cameras_total"] >= first["cameras_total"] + 4


@pytest.mark.asyncio
async def test_the_vendors_own_tenant_is_never_counted():
    """Counting yourself as a customer is how a dashboard starts lying — about
    tenant totals, about revenue, about churn."""
    await _seed_platform_tenant()
    # The token is minted BEFORE the counts are taken: _token() creates a
    # tenant of its own, so measuring first and authenticating second compares
    # the dashboard against a total that is already out of date.
    token = await _token(SUPER_ADMIN)
    platform_count = (await _sql(
        "SELECT count(*) FROM tenants WHERE is_platform"))[0][0]
    all_count = (await _sql("SELECT count(*) FROM tenants"))[0][0]

    async with await _client(token) as c:
        data = (await c.get("/api/v1/platform/dashboard")).json()

    assert platform_count >= 1
    assert data["tenants_total"] == all_count - platform_count


@pytest.mark.asyncio
async def test_tenant_usage_has_a_row_per_customer():
    tenant_id = await _seed_customer(users=2, sites=1, cameras=3)
    async with await _client(await _token(SUPER_ADMIN)) as c:
        rows = (await c.get("/api/v1/platform/tenants")).json()
    mine = next((r for r in rows if r["id"] == str(tenant_id)), None)
    assert mine is not None
    assert mine["users"] == 2
    assert mine["sites"] == 1
    assert mine["cameras"] == 3


@pytest.mark.asyncio
async def test_tenant_usage_never_lists_the_platform_tenant():
    platform_id = await _seed_platform_tenant()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        rows = (await c.get("/api/v1/platform/tenants")).json()
    assert all(r["id"] != str(platform_id) for r in rows)


@pytest.mark.asyncio
async def test_revenue_reports_mrr_and_arr():
    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.get("/api/v1/platform/revenue")
    assert r.status_code == 200
    body = r.json()
    assert body["arr"] == pytest.approx(body["mrr"] * 12)


# ─── C. Per-tenant statistics ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_statistics_without_entering_the_tenant():
    """§9. Answering "how many guards does ABC Security have" should not need a
    support session — and this route cannot return who they are."""
    tenant_id = await _seed_customer(users=5, sites=0, cameras=0)
    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.get(f"/api/v1/platform/tenants/{tenant_id}/users")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert sum(row["count"] for row in body["by_role"]) == 5


@pytest.mark.asyncio
async def test_the_statistics_carry_no_personal_data():
    """The other half of the bargain. Counts are running a business; names and
    pay rates are the customer's, and no route here returns them."""
    tenant_id = await _seed_customer(users=2, sites=0, cameras=0)
    async with await _client(await _token(SUPER_ADMIN)) as c:
        body = (await c.get(f"/api/v1/platform/tenants/{tenant_id}/users")).json()
    flat = str(body)
    for leak in ("hourly_rate", "hashed_password", "nric", "@test.local"):
        assert leak not in flat, f"{leak} reached the platform console"


@pytest.mark.asyncio
async def test_tenant_detail_lists_modules_and_subscription():
    tenant_id = await _seed_customer(users=1, sites=1, cameras=2)
    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.get(f"/api/v1/platform/tenants/{tenant_id}/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["cameras"] == 2
    assert isinstance(body["modules"], list)
    assert "subscription" in body


@pytest.mark.asyncio
async def test_the_platform_tenant_is_not_addressable_as_a_customer():
    """A 404 rather than a confusing row of the vendor counting itself."""
    platform_id = await _seed_platform_tenant()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.get(f"/api/v1/platform/tenants/{platform_id}/users")
    assert r.status_code == 404


# ─── D. The error centre ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_same_failure_seen_many_times_is_one_row():
    """The difference between a console and a log. One bad deploy produces the
    same failure thousands of times; ungrouped, "is anything broken" becomes a
    scrolling exercise."""
    from app.db.session import AsyncSessionLocal
    marker = f"/api/v1/grouping-{uuid.uuid4().hex[:8]}/{{id}}"
    for _ in range(5):
        await error_collector.record(
            AsyncSessionLocal, error_type="ValueError", message="same thing",
            method="GET", path=marker, status_code=500,
        )
    rows = await _sql(
        "SELECT occurrence_count FROM platform_errors WHERE path_pattern = :p",
        {"p": marker})
    assert len(rows) == 1, "five occurrences should be one problem"
    assert rows[0][0] == 5


@pytest.mark.asyncio
async def test_ids_in_the_path_do_not_split_one_bug_into_many():
    """/users/6f3a.../documents and /users/91bc.../documents are the same bug
    seen twice. A fingerprint containing the id would call them two and hide
    the frequency that makes one worth fixing first."""
    from app.db.session import AsyncSessionLocal
    stem = f"/api/v1/things-{uuid.uuid4().hex[:8]}"
    for _ in range(3):
        await error_collector.record(
            AsyncSessionLocal, error_type="KeyError", message="boom",
            method="GET", path=f"{stem}/{uuid.uuid4()}/detail", status_code=500,
        )
    rows = await _sql(
        "SELECT occurrence_count, path_pattern FROM platform_errors "
        " WHERE path_pattern LIKE :p", {"p": f"{stem}%"})
    assert len(rows) == 1, f"three ids produced {len(rows)} problems"
    assert rows[0][0] == 3
    assert "{id}" in rows[0][1]


@pytest.mark.asyncio
async def test_each_occurrence_keeps_which_tenant_it_happened_to():
    """The group says something is wrong; only the events say who it is
    happening to."""
    from app.db.session import AsyncSessionLocal
    tenant_id = await _seed_customer(users=1, sites=0, cameras=0)
    marker = f"/api/v1/whose-{uuid.uuid4().hex[:8]}"
    await error_collector.record(
        AsyncSessionLocal, error_type="RuntimeError", message="x",
        method="GET", path=marker, status_code=500, tenant_id=str(tenant_id),
    )
    rows = await _sql("""
        SELECT ev.tenant_id FROM platform_error_events ev
          JOIN platform_errors e ON e.id = ev.error_id
         WHERE e.path_pattern = :p
    """, {"p": marker})
    assert [str(r[0]) for r in rows] == [str(tenant_id)]


@pytest.mark.asyncio
async def test_a_database_failure_is_classified_critical():
    """§18's bands are assigned at collection, not judged at 2am by whoever is
    reading the console."""
    from app.db.session import AsyncSessionLocal
    marker = f"/api/v1/sev-{uuid.uuid4().hex[:8]}"
    await error_collector.record(
        AsyncSessionLocal, error_type="OperationalError",
        message="connection refused", path=marker, status_code=500,
    )
    rows = await _sql(
        "SELECT severity FROM platform_errors WHERE path_pattern = :p",
        {"p": marker})
    assert rows[0][0] == "critical"


@pytest.mark.asyncio
async def test_resolving_and_reopening():
    """Resolving is not deleting. A fingerprint seen again after being resolved
    reopens, because a fix that did not hold is worse news than a new bug."""
    from app.db.session import AsyncSessionLocal
    marker = f"/api/v1/reopen-{uuid.uuid4().hex[:8]}"
    await error_collector.record(
        AsyncSessionLocal, error_type="ValueError", message="x",
        path=marker, status_code=500,
    )
    error_id = (await _sql(
        "SELECT id FROM platform_errors WHERE path_pattern = :p", {"p": marker}))[0][0]

    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.put(f"/api/v1/platform/errors/{error_id}",
                        json={"status": "resolved", "resolution": "fixed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"

    await error_collector.record(
        AsyncSessionLocal, error_type="ValueError", message="x",
        path=marker, status_code=500,
    )
    rows = await _sql(
        "SELECT status, resolved_at FROM platform_errors WHERE id = :id",
        {"id": error_id})
    assert rows[0][0] == "open"
    assert rows[0][1] is None, "a reopened problem must not still look resolved"


@pytest.mark.asyncio
async def test_the_collector_never_raises():
    """It runs on the error path. Losing an error record is a nuisance; losing
    the response the client was owed is a bug."""
    class Broken:
        def __call__(self, *a, **k):
            raise RuntimeError("no database today")

    # Must return normally despite the session factory being unusable.
    await error_collector.record(
        Broken(), error_type="ValueError", message="x",
        path="/api/v1/whatever", status_code=500,
    )
