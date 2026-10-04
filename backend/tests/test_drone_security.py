"""Drone patrol, phase 13: the security review, pinned.

  A — Row level security reaches the telemetry partitions, and will keep
      reaching the ones made later
  B — Every operation is behind a permission or a gateway key; nothing answers
      without credentials; a user with no drone permission is refused everywhere
  C — Another tenant's ids, and another site's, are not found by any operation —
      and nothing of theirs is changed by the attempt
  D — No response carries a secret
  E — The audit log: evidence taken, reports taken out, and every operation that
      changes something
  F — What a gateway may send
  G — Failures say nothing about themselves
  H — The platform owner's health row
  I — Reports are limited per person

Sections B to E walk the application's own route table instead of a list kept
here, so an operation added later is covered — or fails — without anyone
remembering to add it to a test.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.services import drone_analytics
from app.services import drone_platform_health as dph
from app.services import drone_report_delivery as delivery
from app.services.drone_access import caller_key
from app.services.drone_edge_wire import MediaIn
from tests.test_drone_ai_pipeline import _flying, _last
from tests.test_drone_ai_pipeline import _world as _ai_world
from tests.test_drone_edge_sync import _client, _key, _now, _run, _sql, _sync
from tests.test_drone_edge_sync import _world as _edge_world
from tests.test_drone_reports import _flight_with_event, _snapshot_for
from tests.test_drone_schema import _as_app
from tests.test_drone_schema import _world as _schema_world

SGT = ZoneInfo("Asia/Singapore")
SUPERVISOR, CLIENT = 3, 7
WRITE = {"POST", "PUT", "PATCH"}
EDGE = "/api/v1/drone-edge/"
PLATFORM = "/api/v1/platform/"


@pytest.fixture
def evidence(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "EVIDENCE_ROOT", str(tmp_path))
    return tmp_path


# ─── The route table ─────────────────────────────────────────────────────────

def _ops() -> list[tuple[str, str, APIRoute]]:
    """Every drone operation the application serves: (method, path, route).

    Included routers are held as contexts rather than as routes of the app
    itself, so both are walked. The count is checked here, once, so that no sweep
    below can pass by having nothing to sweep."""
    found = []

    def take(r):
        if "drone" in getattr(r, "path", "") and hasattr(r, "dependant"):
            for method in sorted(r.methods - {"HEAD"}):
                found.append((method, r.path, r))

    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        if contexts is None:
            take(r)
        else:
            for inner in (contexts() if callable(contexts) else contexts):
                take(inner)
    assert len(found) >= 100, f"only {len(found)} drone operations found — the route walk is broken"
    return sorted(found, key=lambda o: (o[1], o[0]))


def _guards(route: APIRoute) -> tuple[set[str], set[str]]:
    """(permissions required, names of every dependency) of one operation."""
    perms, names = set(), set()

    def walk(dep):
        call = dep.call
        name = getattr(call, "__qualname__", getattr(call, "__name__", str(call)))
        names.add(name.split(".")[0])
        if "require_permission" in name:
            perms.update(c.cell_contents for c in (call.__closure__ or ())
                         if isinstance(c.cell_contents, str))
        for sub in dep.dependencies:
            walk(sub)

    for d in route.dependant.dependencies:
        walk(d)
    return perms, names


def _fill(path: str, ids: dict) -> str:
    return re.sub(r"{(\w+)}", lambda m: str(ids[m.group(1)]), path)


def _params(path: str) -> list[str]:
    return re.findall(r"{(\w+)}", path)


#: What an operation needs before it will look its id up; every other one takes
#: an empty body. An operation added later that needs more must be given its
#: minimum here, or the sweeps below fail on its 422 - on purpose: a sweep that
#: stops at validation has not tested the lookup.
MINIMUM = {
    ("POST", "/api/v1/drone-events/{event_id}/false-positive"): {"json": {"reason": "a bird"}},
    ("PATCH", "/api/v1/drone-missions/{mission_id}/enabled"): {"params": {"enabled": "false"}},
    ("POST", "/api/v1/drone-missions/{mission_id}/schedules"):
        {"json": {"schedule_type": "DAILY", "start_date": "2030-01-01", "launch_time": "23:00"}},
    ("PUT", "/api/v1/drone-routes/{route_id}/waypoints"):
        {"json": {"waypoints": [{"latitude": 1.301, "longitude": 103.8}]}},
    ("POST", "/api/v1/drones/{drone_id}/maintenance"): {"json": {"maintenance_type": "INSPECTION"}},
}


async def _call(c: AsyncClient, method: str, path: str, ids: dict, headers: dict | None):
    kw = dict(MINIMUM.get((method, path), {}))
    if method in WRITE:
        kw.setdefault("json", {})
    return await c.request(method, _fill(path, ids), headers=headers or {}, **kw)


def _random_ids() -> dict:
    return {p: uuid.uuid4() for _, path, _ in _ops() for p in _params(path)}


# ─── A tenant with one of everything the API addresses by id ─────────────────

async def _everything(evidence: Path) -> dict:
    """A flown flight with a verified event and its picture, and beside it a
    gateway, a schedule, a profile, a report recipient, a failed delivery and a
    fixed camera with coverage — plus a second site with its own supervisor, and
    a user who holds no drone permission at all."""
    w = await _ai_world()
    at = _last(2, 17)
    sid, event = await _flight_with_event(w, at)
    await _snapshot_for(w, event, evidence)
    media = (await _sql("SELECT id FROM drone_event_media WHERE event_id = :e", {"e": event["id"]}))[0]["id"]

    x = {k: uuid.uuid4() for k in ("gateway", "schedule", "profile", "recipient", "delivery", "fixed_camera",
                                   "site2", "supervisor2", "client")}
    w["key"], digest = _key(w["tenant"])
    t = w["tenant"]
    stmts = [
        ("INSERT INTO drone_edge_gateways (id, tenant_id, site_id, name, code, credential_hash, credential_prefix) "
         "VALUES (:i,:t,:s,'Depot Edge','EDGE-1',:h,'x')", {"i": x["gateway"], "t": t, "s": w["site"], "h": digest}),
        ("INSERT INTO drone_schedules (id, tenant_id, mission_id, schedule_type, start_date, launch_time) "
         "VALUES (:i,:t,:m,'DAILY',CURRENT_DATE,'23:00')", {"i": x["schedule"], "t": t, "m": w["mission"]}),
        ("INSERT INTO drone_security_profiles (id, tenant_id, name) VALUES (:i,:t,'Night High Security')",
         {"i": x["profile"], "t": t}),
        ("INSERT INTO drone_report_recipients (id, tenant_id, site_id, email, frequency) "
         "VALUES (:i,:t,:s,'ops@agency.test','IMMEDIATE')", {"i": x["recipient"], "t": t, "s": w["site"]}),
        ("INSERT INTO drone_report_email_queue (id, tenant_id, session_id, site_id, frequency, recipients, subject, "
         "    status, attempts, last_error, scheduled_at) "
         "VALUES (:i,:t,:ss,:s,'IMMEDIATE','ops@agency.test','Patrol report','FAILED',5,'refused',now())",
         {"i": x["delivery"], "t": t, "ss": uuid.UUID(sid), "s": w["site"]}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'CAM-27')",
         {"i": x["fixed_camera"], "t": t, "s": w["site"]}),
        ("INSERT INTO drone_camera_coverage (tenant_id, camera_id, heading_deg, fov_deg, range_m) "
         "VALUES (:t,:c,90,60,40)", {"t": t, "c": x["fixed_camera"]}),
        ("INSERT INTO sites (id, tenant_id, name, latitude, longitude, geofence_radius_meters) "
         "VALUES (:i,:t,'Annexe',1.35,103.85,300)", {"i": x["site2"], "t": t}),
    ]
    for key, role in (("supervisor2", SUPERVISOR), ("client", CLIENT)):
        stmts.append(("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                      "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
                      {"i": x[key], "t": t, "r": role, "e": f"{key}-{x[key].hex[:8]}@sec.test", "n": key.title()}))
    stmts.append(("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)",
                  {"u": x["supervisor2"], "s": x["site2"], "t": t}))
    await _run(stmts)

    w.update(x)
    w["session"], w["event"], w["media"], w["at"] = sid, event, media, at
    w["h_supervisor2"] = {"Authorization": f"Bearer {create_access_token(str(x['supervisor2']), str(t), SUPERVISOR)}"}
    w["h_client"] = {"Authorization": f"Bearer {create_access_token(str(x['client']), str(t), CLIENT)}"}
    w["ids"] = {"provider_id": w["provider"], "gateway_id": x["gateway"], "camera_id": x["fixed_camera"],
                "drone_id": w["drone"], "zone_id": w["zone"], "profile_id": x["profile"], "route_id": w["route"],
                "mission_id": w["mission"], "schedule_id": x["schedule"], "session_id": sid,
                "event_id": event["id"], "media_id": media, "recipient_id": x["recipient"],
                "delivery_id": x["delivery"], "client_ref": uuid.uuid4(), "tenant_id": t}
    return w


async def _fingerprint(tenant) -> dict[str, str]:
    """Every drone row a tenant owns, and the alerts and incidents a drone action
    can touch — as a count and a digest per table."""
    tables = [r["relname"] for r in await _sql(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        " WHERE n.nspname = 'public' AND c.relname LIKE 'drone%' AND c.relkind IN ('r','p') "
        "   AND NOT c.relispartition ORDER BY 1")] + ["alerts", "incidents"]
    rows = await _run([(f"SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) AS h "
                        f"  FROM {table} x WHERE tenant_id = :t", {"t": tenant}) for table in tables])
    return {table: f"{r[0]['n']}:{r[0]['h']}" for table, r in zip(tables, rows)}


def _detail(row: dict) -> dict:
    d = row["detail"]
    return json.loads(d) if isinstance(d, str) else d


async def _audit_rows(tenant, action: str) -> list[dict]:
    return [dict(r) for r in await _sql(
        "SELECT action, user_id, resource_type, resource_id, detail FROM audit_logs "
        " WHERE tenant_id = :t AND action = :a ORDER BY created_at, id", {"t": tenant, "a": action})]


# ═════════════════════════════════════════════════════════════════════════════
# A. Row level security on the partitions
# ═════════════════════════════════════════════════════════════════════════════

_PARTITIONS = """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
           (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
      FROM pg_inherits i
      JOIN pg_class c ON c.oid = i.inhrelid
      JOIN pg_class p ON p.oid = i.inhparent
      JOIN pg_namespace n ON n.oid = p.relnamespace
     WHERE n.nspname = 'public' AND p.relname LIKE 'drone%' AND c.relkind IN ('r','p')
"""


@pytest.mark.asyncio
async def test_every_drone_partition_is_under_forced_rls_with_a_policy():
    """The table test in test_drone_schema skips partitions, which is how eight of
    them went without a policy. Postgres does not hand a parent's row security to
    its partitions, and a partition can be selected from by name."""
    rows = await _sql(_PARTITIONS)
    assert len(rows) >= 2, "drone_telemetry has no partitions to check"
    unsafe = [r["relname"] for r in rows
              if not (r["relrowsecurity"] and r["relforcerowsecurity"] and r["policies"] == 1)]
    assert unsafe == [], f"drone partitions readable across tenants by name: {unsafe}"


@pytest.mark.asyncio
async def test_a_partition_named_directly_shows_only_the_tenants_own_track():
    a, b = await _schema_world(), await _schema_world()
    for w in (a, b):
        await _sql("INSERT INTO drone_telemetry (tenant_id, drone_id, recorded_at) VALUES (:t,:d,now())",
                   {"t": w["tenant"], "d": w["drone"]})
    part = (await _sql("SELECT tableoid::regclass::text AS p FROM drone_telemetry WHERE drone_id = :d",
                       {"d": b["drone"]}))[0]["p"]
    assert re.fullmatch(r"drone_telemetry_\w+", part), part
    both = (await _sql(f"SELECT count(DISTINCT tenant_id) AS n FROM {part} WHERE drone_id IN (:a, :b)",
                       {"a": a["drone"], "b": b["drone"]}))[0]["n"]
    assert both == 2, "both tracks must be in this partition, or the test proves nothing"

    seen = (await _as_app(a["tenant"], f"""
        SELECT count(*) FILTER (WHERE drone_id = :own)   AS own,
               count(*) FILTER (WHERE drone_id = :other) AS theirs,
               count(*) FILTER (WHERE tenant_id <> :t)   AS anyone_else
          FROM {part}
    """, {"own": a["drone"], "other": b["drone"], "t": a["tenant"]}))[0]
    assert seen["own"] == 1
    assert seen["theirs"] == 0 and seen["anyone_else"] == 0, dict(seen)


@pytest.mark.asyncio
async def test_a_partition_made_later_is_protected_by_the_platforms_daily_sweep():
    """Next month's partition is made by pg_partman and arrives with no policy.
    The scheduler runs `apply_partition_rls()` straight after, as the app role —
    this is that call, on a drone partition."""
    name = "drone_telemetry_p20990101"
    state = ("SELECT c.relrowsecurity AND c.relforcerowsecurity AS protected, "
             "       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies "
             "  FROM pg_class c WHERE c.relname = :n")
    await _sql(f"DROP TABLE IF EXISTS {name}")
    try:
        await _sql(f"CREATE TABLE {name} PARTITION OF drone_telemetry "
                   f"FOR VALUES FROM ('2099-01-01') TO ('2099-02-01')")
        born = (await _sql(state, {"n": name}))[0]
        assert not born["protected"] and born["policies"] == 0, \
            "a new partition now inherits row security — the sweep, and this test, can go"
        async with AsyncSessionLocal() as db:
            who = (await db.execute(text(
                "SELECT (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"))).scalar()
            assert who is False
            fixed = await db.scalar(text("SELECT public.apply_partition_rls()"))
            await db.commit()
        assert fixed >= 1
        swept = (await _sql(state, {"n": name}))[0]
        assert swept["protected"] and swept["policies"] == 1
    finally:
        await _sql(f"DROP TABLE IF EXISTS {name}")


# ═════════════════════════════════════════════════════════════════════════════
# B. Every operation is guarded
# ═════════════════════════════════════════════════════════════════════════════

def test_every_drone_operation_is_behind_a_permission_or_a_gateway_key():
    unguarded = []
    for method, path, route in _ops():
        perms, names = _guards(route)
        if path.startswith(EDGE):
            ok = "get_gateway" in names and not perms
        else:
            ok = bool(perms) and "get_token_payload" in names
        if not ok:
            unguarded.append(f"{method} {path}")
    assert unguarded == []


def test_nothing_that_changes_data_is_behind_a_read_permission():
    wrong = [f"{m} {p} {sorted(_guards(r)[0])}" for m, p, r in _ops()
             if m != "GET" and any(perm.endswith(":read") for perm in _guards(r)[0])]
    assert wrong == []


def test_only_the_platform_owner_grants_the_licence():
    platform = [(m, p, _guards(r)[0]) for m, p, r in _ops() if p.startswith(PLATFORM)]
    assert platform and all(perms == {"license:manage"} for _, _, perms in platform), platform


@pytest.mark.asyncio
async def test_no_drone_operation_answers_without_credentials():
    ids = _random_ids()
    answered = []
    async with _client() as c:
        for method, path, _ in _ops():
            r = await _call(c, method, path, ids, None)
            if r.status_code not in (401, 403):
                answered.append(f"{method} {path} -> {r.status_code}")
    assert answered == []


@pytest.mark.asyncio
async def test_a_user_with_no_drone_permission_is_refused_everywhere(evidence):
    """A client-role user of a licensed tenant, with that tenant's real ids: the
    refusal must come from the permission, not from something not being found."""
    w = await _everything(evidence)
    before = await _fingerprint(w["tenant"])
    wrong = []
    async with _client() as c:
        for method, path, _ in _ops():
            r = await _call(c, method, path, w["ids"], w["h_client"])
            expected = 401 if path.startswith(EDGE) else 403
            if r.status_code != expected:
                wrong.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
    assert wrong == []
    assert await _fingerprint(w["tenant"]) == before


# ═════════════════════════════════════════════════════════════════════════════
# C. Another tenant's ids, and another site's
# ═════════════════════════════════════════════════════════════════════════════

def _by_id() -> list[tuple[str, str, APIRoute]]:
    """Operations a signed-in user addresses by id."""
    return [(m, p, r) for m, p, r in _ops()
            if _params(p) and not p.startswith(EDGE) and not p.startswith(PLATFORM)]


@pytest.mark.asyncio
async def test_another_tenants_ids_are_not_found_by_any_operation(evidence):
    """Tenant B's administrator — every drone permission, a live licence — tries
    every id-addressed operation with tenant A's ids and a body the operation
    accepts. Each must read as absent: exactly 404. Not a success, not a 403 that
    confirms the thing exists, not a 422 that never looked, not a 500. And A's
    rows must be exactly what they were."""
    a = await _everything(evidence)
    b = await _ai_world()
    before = await _fingerprint(a["tenant"])
    wrong = []
    ops = _by_id()
    assert len(ops) >= 60
    async with _client() as c:
        for method, path, _ in ops:
            r = await _call(c, method, path, a["ids"], b["h_admin"])
            if r.status_code != 404:
                wrong.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
    assert wrong == []
    assert await _fingerprint(a["tenant"]) == before


#: Addressed by id but belonging to the organisation, not to a site: security
#: profiles are shared by every site's missions, and a provider connection is
#: the organisation's account with a drone maker. A supervisor of any site may
#: read a profile; changing either needs a permission supervisors do not hold.
TENANT_LEVEL = {
    ("GET", "/api/v1/drone-security-profiles/{profile_id}"),
    ("PUT", "/api/v1/drone-security-profiles/{profile_id}"),
    ("DELETE", "/api/v1/drone-security-profiles/{profile_id}"),
    ("PUT", "/api/v1/drones/providers/{provider_id}"),
    ("DELETE", "/api/v1/drones/providers/{provider_id}"),
}


def test_every_id_addressed_operation_is_site_scoped_unless_it_is_listed_here():
    unscoped = {(m, p) for m, p, r in _by_id() if "get_allowed_site_ids" not in _guards(r)[1]}
    assert unscoped == TENANT_LEVEL


@pytest.mark.asyncio
async def test_another_sites_ids_are_not_found_by_a_supervisor_of_a_different_site(evidence):
    """Same tenant, so row level security does not help: this is the application's
    own site scoping. The supervisor belongs to the Annexe; everything here is at
    the Depot."""
    a = await _everything(evidence)
    before = await _fingerprint(a["tenant"])
    wrong, hidden = [], 0
    async with _client() as c:
        for method, path, _ in _by_id():
            if (method, path) in TENANT_LEVEL:
                continue
            r = await _call(c, method, path, a["ids"], a["h_supervisor2"])
            hidden += r.status_code == 404
            # 403: the role lacks the permission and never got as far as the id.
            if r.status_code not in (403, 404) or (method == "GET" and r.status_code != 404):
                wrong.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
        own_site = await c.get("/api/v1/drones", headers=a["h_supervisor2"])
    assert wrong == []
    assert hidden >= 45, f"only {hidden} operations answered 'not found' — the supervisor holds too little to test"
    assert own_site.status_code == 200 and own_site.json()["total"] == 0
    assert await _fingerprint(a["tenant"]) == before


# ═════════════════════════════════════════════════════════════════════════════
# D. Secrets
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_no_response_carries_a_secret(evidence):
    """Every GET the module serves, as the tenant's own administrator, with real
    ids — and the gateway's sync answer. None may contain a stored secret, a
    credential hash, a password hash or a camera's stream address."""
    a = await _everything(evidence)
    mark = {k: f"{k}-{uuid.uuid4().hex}" for k in ("CIPHERTEXT", "CAMPASS", "CAMAUTH", "PWHASH")}
    digest = hashlib.sha256(a["key"].encode()).hexdigest()
    await _run([
        ("UPDATE drone_provider_configs SET secret_encrypted = :v WHERE id = :i",
         {"v": mark["CIPHERTEXT"], "i": a["provider"]}),
        ("INSERT INTO streams (tenant_id, camera_id, url, auth_config) VALUES (:t,:c,:u,CAST(:a AS jsonb))",
         {"t": a["tenant"], "c": a["camera"], "u": f"rtsp://viewer:{mark['CAMPASS']}@10.9.9.9/live",
          "a": json.dumps({"password": mark["CAMAUTH"]})}),
        ("INSERT INTO streams (tenant_id, camera_id, url, auth_config) VALUES (:t,:c,:u,CAST(:a AS jsonb))",
         {"t": a["tenant"], "c": a["fixed_camera"], "u": f"rtsp://viewer:{mark['CAMPASS']}@10.9.9.8/live",
          "a": json.dumps({"password": mark["CAMAUTH"]})}),
        ("UPDATE users SET hashed_password = :v WHERE tenant_id = :t", {"v": mark["PWHASH"], "t": a["tenant"]}),
    ])
    day = a["at"].astimezone(SGT).date().isoformat()
    bodies: list[tuple[str, str]] = []
    unread = []
    async with _client() as c:
        for method, path, _ in _ops():
            if method != "GET" or path.startswith(EDGE) or path.startswith(PLATFORM):
                continue
            r = await c.get(_fill(path, a["ids"]), headers=a["h_admin"], params={"from": day, "to": day})
            if r.status_code != 200:
                unread.append(f"{path} -> {r.status_code} {r.text[:120]}")
            elif "json" in r.headers.get("content-type", ""):
                bodies.append((path, r.text))
        edge = await _sync(c, a["key"])
        assert edge.status_code == 200, edge.text
        bodies.append(("drone-edge/sync", edge.text))
    # Every one must answer, or what it would have said has not been read.
    assert unread == []
    assert len(bodies) >= 40 and sum(len(b) for _, b in bodies) > 20_000

    secrets = {**mark, "GATEWAY_KEY": a["key"], "GATEWAY_SECRET": a["key"].split(".", 2)[2], "CREDENTIAL_HASH": digest}
    names = ('"secret_encrypted"', '"credential_hash"', '"hashed_password"', '"auth_config"')
    leaks = [f"{path}: {what}" for path, body in bodies
             for what, value in [*secrets.items(), *((n, n) for n in names)] if value in body]
    assert leaks == []
    assert not any("rtsp://" in body for _, body in bodies), "a camera's stream address is in a response"


# ═════════════════════════════════════════════════════════════════════════════
# E. The audit log
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_taking_a_piece_of_evidence_is_in_the_audit_log(evidence):
    a = await _everything(evidence)
    await _snapshot_for(a, a["event"], evidence, location="local", write=False)
    at_site = (await _sql("SELECT id FROM drone_event_media WHERE event_id = :e AND storage_location = 'local'",
                          {"e": a["event"]["id"]}))[0]["id"]
    async with _client() as c:
        taken = await c.get(f"/api/v1/drone-media/{a['media']}/file", headers=a["h_op"])
        held = await c.get(f"/api/v1/drone-media/{at_site}/file", headers=a["h_op"])
        absent = await c.get(f"/api/v1/drone-media/{uuid.uuid4()}/file", headers=a["h_op"])
    assert (taken.status_code, held.status_code, absent.status_code) == (200, 409, 404)

    rows = await _audit_rows(a["tenant"], "drone.media.access")
    assert len(rows) == 1, "only the file that was handed over is recorded"
    row, detail = rows[0], _detail(rows[0])
    assert row["user_id"] == a["operator"] and row["resource_type"] == "drone_event_media"
    assert row["resource_id"] == a["media"]
    assert detail["media_kind"] == "SNAPSHOT" and detail["event_id"] == str(a["event"]["id"])
    assert detail["checksum_sha256"] == hashlib.sha256(taken.content).hexdigest()


@pytest.mark.asyncio
async def test_taking_a_report_out_is_in_the_audit_log(evidence):
    a = await _everything(evidence)
    day = a["at"].astimezone(SGT).date().isoformat()
    period = {"from": day, "to": day}
    base = f"/api/v1/drone-patrols/{a['session']}/report"
    async with _client() as c:
        read = await c.get(base, headers=a["h_admin"])
        pdf = await c.get(f"{base}/pdf", headers=a["h_admin"])
        xlsx = await c.get(f"{base}/excel", headers=a["h_admin"])
        totals = await c.get("/api/v1/drone-reports/summary", headers=a["h_admin"], params=period)
        summary = await c.get("/api/v1/drone-reports/summary/excel", headers=a["h_admin"], params=period)
    assert [r.status_code for r in (read, pdf, xlsx, totals, summary)] == [200] * 5

    rows = await _audit_rows(a["tenant"], "drone.report.export")
    # Reading a report on screen is not taking it out.
    assert [(r["resource_type"], _detail(r)["format"]) for r in rows] == [
        ("drone_patrol_session", "pdf"), ("drone_patrol_session", "xlsx"), ("drone_report_summary", "xlsx")]
    for row, response in zip(rows, (pdf, xlsx, summary)):
        detail = _detail(row)
        assert row["user_id"] == a["admin"]
        assert detail["checksum_sha256"] == hashlib.sha256(response.content).hexdigest(), \
            "the audit entry must name the exact document that was handed over"
        assert detail["size_bytes"] == len(response.content)
    assert rows[0]["resource_id"] == uuid.UUID(a["session"]) and rows[2]["resource_id"] is None
    assert _detail(rows[2])["from"] == day and _detail(rows[2])["flights"] == 1


def _audits(fn, seen: set | None = None, depth: int = 0) -> bool:
    """Does this function, or a drone function it calls, write to the audit log?"""
    seen = seen if seen is not None else set()
    fn = inspect.unwrap(fn)          # a rate-limited operation is registered as its wrapper
    if fn in seen or depth > 3:
        return False
    seen.add(fn)
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    if re.search(r"\baudit\(|\bwrite_audit_log\(", src):
        return True
    module = inspect.getmodule(fn)
    for name in set(re.findall(r"\b([A-Za-z_][\w.]*)\(", src)):
        obj = module
        for part in name.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if inspect.isfunction(obj) and re.match(r"app\.(routers|services)\.(platform_)?drone",
                                                 inspect.getmodule(obj).__name__):
            if _audits(obj, seen, depth + 1):
                return True
    return False


#: A gateway's sync is a machine reporting, not a person acting. What it sent
#: and what became of each item is its own ledger, `drone_sync_receipts`, and a
#: claim is recorded on the flight it starts.
NOT_IN_THE_AUDIT_LOG = {
    ("POST", "/api/v1/drone-edge/sync"),
    ("POST", "/api/v1/drone-edge/sessions/{session_id}/claim"),
    ("PUT", "/api/v1/drone-edge/media/{client_ref}"),
}


def test_every_operation_that_changes_something_writes_to_the_audit_log():
    changing = [(m, p, r) for m, p, r in _ops() if m != "GET"]
    assert len(changing) >= 50
    silent = {(m, p) for m, p, r in changing if not _audits(r.endpoint)}
    assert silent == NOT_IN_THE_AUDIT_LOG


# ═════════════════════════════════════════════════════════════════════════════
# F. What a gateway may send
# ═════════════════════════════════════════════════════════════════════════════

def _media(**extra) -> dict:
    return {"client_ref": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "media_kind": "SNAPSHOT",
            "captured_at": _now().isoformat(), "checksum_sha256": "a" * 64, "size_bytes": 10, **extra}


def test_a_telemetry_snapshot_is_a_few_readings_not_a_payload():
    kept = MediaIn(**_media(telemetry_snapshot={"battery_level": 81, "altitude_m": 40.5}))
    assert kept.telemetry_snapshot == {"battery_level": 81, "altitude_m": 40.5}
    assert MediaIn(**_media()).telemetry_snapshot is None
    with pytest.raises(ValueError, match="larger than 8 KB"):
        MediaIn(**_media(telemetry_snapshot={"blob": "x" * 9000}))


@pytest.mark.asyncio
async def test_an_oversized_item_refuses_the_batch_before_anything_is_stored():
    w = await _edge_world()
    async with _client() as c:
        r = await _sync(c, w["key"], media=[_media(telemetry_snapshot={"blob": "x" * 9000})])
    assert r.status_code == 422
    assert (await _sql("SELECT count(*) AS n FROM drone_event_media WHERE tenant_id = :t", {"t": w["tenant"]}))[0]["n"] == 0


@pytest.mark.asyncio
async def test_a_gateway_cannot_speak_for_another_tenants_drone():
    a, b = await _edge_world(), await _edge_world()
    seen = "SELECT battery_level, last_heartbeat_at, status FROM drones WHERE id = :d"
    before = dict((await _sql(seen, {"d": b["drone"]}))[0])
    async with _client() as c:
        r = await _sync(
            c, a["key"],
            health=[{"drone_id": str(b["drone"]), "observed_at": _now().isoformat(), "battery_level": 3}],
            events=[{"client_ref": str(uuid.uuid4()), "drone_id": str(b["drone"]), "module_type": "intrusion",
                     "detected_at": _now().isoformat()}])
    assert r.status_code == 200, r.text
    body = r.json()
    for part in ("health", "events"):
        assert body[part]["accepted"] == 0, body[part]
        assert body[part]["rejected"] or body[part].get("ignored"), body[part]
    assert dict((await _sql(seen, {"d": b["drone"]}))[0]) == before
    counted = await _sql("SELECT count(*) AS n FROM drone_events WHERE drone_id = :d", {"d": b["drone"]})
    assert counted[0]["n"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# G. Failures say nothing about themselves
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_an_unexpected_failure_says_nothing_about_itself(monkeypatch):
    w = await _ai_world()

    async def broken(*args, **kwargs):
        raise RuntimeError('relation "drone_provider_configs" password=hunter2 at /app/backend/app/services/x.py')

    monkeypatch.setattr(drone_analytics, "overview", broken)
    transport = ASGITransport(app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/drone-analytics/overview", headers=w["h_admin"],
                        params={"from": "2026-10-01", "to": "2026-10-03"})
    assert r.status_code == 500
    for inside in ("hunter2", "drone_provider_configs", "/app/backend", "RuntimeError", "Traceback"):
        assert inside not in r.text, r.text


def test_a_failed_email_shows_the_mail_servers_words_but_never_this_systems():
    from aiosmtplib import SMTPRecipientRefused, SMTPRecipientsRefused, SMTPResponseException

    assert delivery.delivery_error(SMTPResponseException(552, "Mailbox full")) == \
        "The mail server refused the message: 552 Mailbox full"
    refused = SMTPRecipientsRefused([SMTPRecipientRefused(550, "No such user", "ops@agency.test")])
    assert delivery.delivery_error(refused) == \
        "The mail server refused the recipients: ops@agency.test: 550 No such user"

    for unreachable in (ConnectionRefusedError("[Errno 111] smtp.internal.example:587"),
                        TimeoutError("smtp.internal.example did not answer")):
        assert delivery.delivery_error(unreachable) == "The mail server could not be reached."

    ours = RuntimeError('[SQL: SELECT * FROM drone_reports] /data/evidence/drone/2026/report.pdf')
    assert delivery.delivery_error(ours) == "The report could not be prepared (RuntimeError)."
    assert len(delivery.delivery_error(SMTPResponseException(552, "x" * 2000))) <= 500


# ═════════════════════════════════════════════════════════════════════════════
# H. The platform owner's health row
# ═════════════════════════════════════════════════════════════════════════════

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
IDLE = {"licensed": 0, "live": 0, "commands_overdue": 0, "emails_overdue": 0}
BUSY = {"licensed": 3, "live": 2, "commands_overdue": 0, "emails_overdue": 0}


def _beat(seconds_ago: int = 4, tick_ok: bool = True) -> dict:
    return json.loads(dph.heartbeat_value(NOW - timedelta(seconds=seconds_ago), tick_ok))


def test_an_installation_that_does_not_fly_is_healthy_without_a_runner():
    row = dph.verdict(IDLE, None, heartbeat_asked=True)
    assert row["status"] == "ok" and "no organisation is licensed" in row["detail"]
    # ...and it is not asked about a heartbeat it has no reason to have.
    assert dph.verdict(IDLE, None, heartbeat_asked=False)["status"] == "ok"


def test_a_silent_runner_is_down_and_one_that_cannot_be_asked_is_unknown():
    down = dph.verdict(BUSY, None, heartbeat_asked=True)
    assert down["status"] == "down"
    assert "2 flights in the air" in down["detail"] and "3 organisations licensed" in down["detail"]
    assert dph.verdict(BUSY, None, heartbeat_asked=False)["status"] == "unknown"
    assert dph.verdict(None, _beat(), heartbeat_asked=True)["status"] == "unknown"
    # A lapsed licence does not excuse the runner: an email is still owed.
    owed = dph.verdict({**IDLE, "emails_overdue": 1}, None, heartbeat_asked=True)
    assert owed["status"] == "down"


def test_a_live_runner_is_ok_until_it_falls_behind():
    ok = dph.verdict(BUSY, _beat(4), heartbeat_asked=True, now=NOW)
    assert ok["status"] == "ok" and ok["heartbeat_age_seconds"] == 4 and ok["live"] == 2

    failed = dph.verdict(BUSY, _beat(tick_ok=False), heartbeat_asked=True, now=NOW)
    assert failed["status"] == "degraded" and "last flight pass failed" in failed["detail"]

    behind = dph.verdict({**BUSY, "commands_overdue": 2, "emails_overdue": 1}, _beat(), heartbeat_asked=True, now=NOW)
    assert behind["status"] == "degraded"
    assert "2 flight commands waiting over a minute" in behind["detail"]
    assert "1 report email more than 15 minutes late" in behind["detail"]


@pytest.mark.asyncio
async def test_the_heartbeat_expires_by_itself():
    class Recorder:
        def __init__(self):
            self.calls = []

        async def set(self, key, value, ex=None):
            self.calls.append((key, value, ex))

    redis = Recorder()
    await dph.write_heartbeat(redis, False, NOW)
    [(key, value, ex)] = redis.calls
    assert key == dph.HEARTBEAT_KEY and ex == dph.HEARTBEAT_TTL_SECONDS == 60
    assert json.loads(value) == {"at": NOW.isoformat(), "tick_ok": False}


@pytest.mark.asyncio
async def test_a_failed_pass_is_reported_and_a_dead_redis_never_stops_the_runner():
    from app import drone_runner_main as runner_main

    async def clean():
        return {"commands": 0}

    async def failing():
        raise RuntimeError("provider down")

    assert await runner_main._guarded("tick", clean()) is True
    assert await runner_main._guarded("tick", failing()) is False

    class Dead:
        async def set(self, *args, **kwargs):
            raise ConnectionError("redis is gone")

    class Recorder:
        value = None

        async def set(self, key, value, ex=None):
            self.value = value

    await runner_main._beat(Dead(), True)          # logged, never raised: a flight is worth more
    redis = Recorder()
    await runner_main._beat(redis, False)
    assert json.loads(redis.value)["tick_ok"] is False


@pytest.mark.asyncio
async def test_the_health_counts_see_every_tenant_as_the_app_role(monkeypatch):
    """The console runs outside any tenant. Counting the tables directly would
    see nothing and call a busy service idle; the function sees past that, and
    returns counts only."""
    async def counts() -> dict:
        async with AsyncSessionLocal() as db:
            who = (await db.execute(text(
                "SELECT (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"))).scalar()
            assert who is False
            return dict((await db.execute(text("SELECT * FROM platform_drone_health()"))).mappings().first())

    before = await counts()
    a, b = await _ai_world(), await _ai_world()
    at = _last(2, 17)
    sid_a, sid_b = await _flying(a, at), await _flying(b, at)
    await _run([
        # Owed by the central runner for five minutes.
        ("INSERT INTO drone_session_commands (tenant_id, session_id, command, status, requested_at) "
         "VALUES (:t,:s,'PAUSE','PENDING', now() - interval '5 minutes')", {"t": a["tenant"], "s": uuid.UUID(sid_a)}),
        # Asked a moment ago: not late yet.
        ("INSERT INTO drone_session_commands (tenant_id, session_id, command, status, requested_at) "
         "VALUES (:t,:s,'RESUME','PENDING', now())", {"t": a["tenant"], "s": uuid.UUID(sid_a)}),
        # An hour late.
        ("INSERT INTO drone_report_email_queue (tenant_id, session_id, frequency, recipients, subject, status, "
         "    attempts, scheduled_at) VALUES (:t,:s,'IMMEDIATE','x@agency.test','Report','PENDING',1, "
         "    now() - interval '1 hour')", {"t": b["tenant"], "s": uuid.UUID(sid_b)}),
        # Out of attempts: a failed delivery the organisation can retry, not work owed.
        ("INSERT INTO drone_report_email_queue (tenant_id, frequency, scope_key, scope_label, period_start, "
         "    period_end, recipients, subject, status, attempts, scheduled_at) "
         "VALUES (:t,'DAILY','org','All sites', CURRENT_DATE - 1, CURRENT_DATE - 1,'x@agency.test','Summary',"
         "        'FAILED',5, now() - interval '1 hour')", {"t": b["tenant"]}),
    ])
    after = await counts()
    assert {k: after[k] - before[k] for k in after} == \
        {"licensed": 2, "live": 2, "commands_overdue": 1, "emails_overdue": 1}

    async def no_runner():
        return True, None

    monkeypatch.setattr(dph, "read_heartbeat", no_runner)
    async with AsyncSessionLocal() as db:
        row = await dph.check_drone_patrol(db)
    assert row["service"] == "drone-patrol" and row["status"] == "down"
    assert row["live"] == after["live"] and "has not reported" in row["detail"]


@pytest.mark.asyncio
async def test_the_console_shows_the_drone_service_and_only_as_counts():
    from tests.test_platform_health import SUPER_ADMIN
    from tests.test_platform_health import _client as _console
    from tests.test_platform_health import _token

    async with await _console(await _token(SUPER_ADMIN)) as c:
        r = await c.get("/api/v1/platform/health")
    assert r.status_code == 200, r.text
    rows = [s for s in r.json()["services"] if s["service"] == "drone-patrol"]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] in ("ok", "degraded", "down", "unknown") and row["detail"]
    assert set(row) <= {"service", "status", "detail", "licensed", "live", "commands_overdue",
                        "emails_overdue", "heartbeat_age_seconds"}


# ═════════════════════════════════════════════════════════════════════════════
# I. Reports are limited per person
# ═════════════════════════════════════════════════════════════════════════════

def _forget_limits() -> None:
    """The limiter counts in the real Redis, and its counts outlive a test."""
    import redis as sync_redis

    r = sync_redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        for pattern in ("LIMITER*", "*LIMITS*", "limiter*"):
            keys = r.keys(pattern)
            if keys:
                r.delete(*keys)
    finally:
        r.close()


def test_a_limit_is_counted_per_person_not_per_address():
    class Asked:
        def __init__(self, authorization: str | None):
            self.headers = {"authorization": authorization} if authorization else {}
            self.client = type("Client", (), {"host": "10.1.2.3"})()

    user = uuid.uuid4()
    token = create_access_token(str(user), str(uuid.uuid4()), 2)
    assert caller_key(Asked(f"Bearer {token}")) == f"user:{user}"
    # Two people behind one address are two buckets; an unreadable token is the address.
    assert caller_key(Asked(f"Bearer {create_access_token(str(uuid.uuid4()), str(uuid.uuid4()), 2)}")) != \
        caller_key(Asked(f"Bearer {token}"))
    for unreadable in (None, "Bearer", "Bearer not.a-token.at-all", "Basic abc"):
        assert caller_key(Asked(unreadable)) == "10.1.2.3", unreadable


@pytest.mark.asyncio
async def test_one_person_may_take_thirty_reports_a_minute_and_nobody_else_pays_for_it():
    """The three exports share one count per person. The thirty-first is refused
    before anything is rendered or looked up; reading on screen is not counted;
    and somebody else at the same address is not affected."""
    a, b = await _ai_world(), await _ai_world()
    period = {"from": "2026-09-01", "to": "2026-09-02"}
    url = "/api/v1/drone-reports/summary/excel"
    _forget_limits()
    try:
        async with _client() as c:
            taken = [(await c.get(url, headers=a["h_admin"], params=period)).status_code for _ in range(31)]
            another_export = await c.get(f"/api/v1/drone-patrols/{uuid.uuid4()}/report/pdf", headers=a["h_admin"])
            reading = await c.get("/api/v1/drone-reports/summary", headers=a["h_admin"], params=period)
            somebody_else = await c.get(url, headers=b["h_admin"], params=period)
    finally:
        _forget_limits()
    assert taken == [200] * 30 + [429]
    assert another_export.status_code == 429, "the PDF and the workbooks are one allowance"
    assert reading.status_code == 200
    assert somebody_else.status_code == 200
    assert len(await _audit_rows(a["tenant"], "drone.report.export")) == 30, "a refused export took nothing"
