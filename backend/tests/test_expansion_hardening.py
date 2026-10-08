"""A sweep over everything the enterprise expansion added: 36 tables and the routes of 16 routers (phase 13).

Each phase tested its own tables and routes as it was built. This asks the same
questions of all of them at once, from the database's catalogue and the
application's route table rather than from a list somebody keeps, so that a
table or a route added later is asked too.

A. Every table: row level security forced, one policy, and what the application's role may do to it.
B. Another organisation sees none of it and can put nothing into it.
C. Every route: a permission, typed parameters, bodies that take only what they declare.
D. Every write is audited and is a person's, with each exception named and why.
E. Nobody without a token, and no role without the permission, gets anything.

What is found and left as it is, is in ENTERPRISE_SECURITY_HARDENING.md.
"""
from __future__ import annotations

import inspect
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import app
from app.db.session import AsyncSessionLocal
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, VIEWER, _auth, _client, _sql, _world

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
#: The migrations of the enterprise expansion: 0143 (phase 1) to 0156 (phase 13).
EXPANSION = range(143, 157)
#: The routers the expansion added, by module.
ROUTERS = ("cases", "daily_briefings", "data_governance", "evidence_packages", "incident_responses", "investigations",
           "maintenance", "occurrence_book", "operations_board", "operations_reports", "security_advice",
           "security_assets", "site_map", "sop", "visitor_authorizations", "workforce")
POLICY = "(tenant_id = (current_setting('app.current_tenant'::text, true))::uuid)"
#: A refusal, as every router of the expansion writes it: the test, then the 403.
REFUSES_A_KEY = r"if token\.via_api_key:\s+raise HTTPException\(403"
REFUSES_SUPPORT = r"if token\.support_session_id:\s+raise HTTPException\(403"

#: Tables a row may be deleted from by the application's role, and the step that does it.
MAY_DELETE = {
    "evidence_package_items": "An item is taken out of a package that has not been sealed",
    "sop_incident_types": "The kinds of incident a procedure is for are replaced when they are set again",
    "visitor_authorization_places": "The places a visit is for are replaced when they are set again",
}
#: Tables the application's role may update in any column: settings a person edits, each row kept in its
#: organisation by the policy's check.
MAY_UPDATE_ANY = {"escalation_policies": "A policy is edited as a whole", "site_places": "A place is edited as a whole"}
#: Tables the application's role may add to and read, and nothing else.
ADD_ONLY = frozenset({
    "case_entries", "device_health_changes", "evidence_custody_events", "incident_escalations",
    "incident_response_steps", "occurrence_entry_corrections", "occurrence_entry_reviews", "risk_advice_answers",
    "site_instruction_reads", "sop_passages", "visitor_movement_reviews", "workforce_advice_answers",
})

#: A draft that is counted again says who counted it and when. Once it is settled a trigger holds it still.
RECOUNTED = {"daily_briefings": {"drafted_at", "drafted_by_user_id"}}
#: The one existing permission the client role holds that opens a route of the expansion, and the routes it opens:
#: the occurrence book as the existing list already shows it. A review of an entry is not served under it.
CLIENT_READS = {"dob:read": {"/api/v1/occurrence-book/entries", "/api/v1/occurrence-book/entries/{entry_id:uuid}",
                             "/api/v1/occurrence-book/kinds"}}

#: The one route that asks for either of two permissions, in its own code.
EITHER = {("GET", "/api/v1/incident-responses/{incident_id:uuid}"): ("response:read", "response:act")}
#: Path parameters that are words, each checked against a list by its route.
WORDS = {"/api/v1/operations-reports/{key}": "key", "/api/v1/security-assets/health/{kind}/{device_id:uuid}": "kind"}
#: Requests that are not a GET and change nothing: a question with its words in the body.
READS = {
    ("POST", "/api/v1/sop/ask"): "A question put to the library",
    ("POST", "/api/v1/investigations/search"): "A search; what was asked is audited",
    ("POST", "/api/v1/data-governance/subjects/find"): "A name matched to choose a person",
    ("POST", "/api/v1/data-governance/subjects/written"): "A report; that it was asked is audited",
}
#: Writes that are not audited, and why.
NOT_AUDITED = {
    ("POST", "/api/v1/occurrence-book/instructions/{instruction_id:uuid}/read"):
        "The reading is the record: a row that says who read which instruction, and when",
    ("PATCH", "/api/v1/occurrence-book/shift-summaries/{summary_id:uuid}"):
        "A draft is corrected. Confirming or discarding it is audited, and says whether it was edited",
    ("PATCH", "/api/v1/sop/versions/{version_id:uuid}"):
        "A draft is corrected. Submitting, approving and rejecting it are audited",
    ("POST", "/api/v1/sop/ask"): "It reads",
    ("POST", "/api/v1/data-governance/subjects/find"): "It reads. The report that follows is audited",
}
#: Requests that are not a GET and may come from an API key or a support session, and why.
NOT_A_PERSONS = {("POST", "/api/v1/sop/ask"): "Whoever may read the library may ask it"}
#: Records leaving the platform: each refuses a support session.
TAKEN_OUT = (
    ("GET", "/api/v1/operations-reports/{key}"), ("GET", "/api/v1/cases/{case_id:uuid}/report"),
    ("GET", "/api/v1/cases/{case_id:uuid}/report.pdf"), ("POST", "/api/v1/evidence-packages/{package_id:uuid}/export"),
    ("GET", "/api/v1/evidence-packages/{package_id:uuid}/items/{item_id:uuid}/file"),
    ("GET", "/api/v1/data-governance/subjects/staff/{user_id:uuid}"),
    ("GET", "/api/v1/data-governance/subjects/visitor/{visitor_id:uuid}"),
)
#: The only route that removes a row.
DELETES = {("DELETE", "/api/v1/evidence-packages/{package_id:uuid}/items/{item_id:uuid}")}


def expansion_tables() -> list[str]:
    found = []
    for path in sorted(VERSIONS.glob("01*.py")):
        if int(path.name[:4]) in EXPANSION:
            upgrade = path.read_text(encoding="utf-8").split("def upgrade", 1)[1].split("def downgrade", 1)[0]
            found += re.findall(r"CREATE TABLE (\w+)", upgrade)
    return found


def _source(endpoint, depth: int = 3) -> str:
    """An endpoint's own source with that of the module's helpers it calls, to a small depth."""
    module = sys.modules[endpoint.__module__]
    seen, out, todo = set(), [], [(endpoint, 0)]
    while todo:
        fn, level = todo.pop()
        if fn in seen:
            continue
        seen.add(fn)
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        out.append(src)
        if level < depth:
            for name in set(re.findall(r"\b(_[a-z_]+)\(", src)):
                helper = getattr(module, name, None)
                if inspect.isfunction(helper):
                    todo.append((helper, level + 1))
    return "\n".join(out)


def routes() -> list[dict]:
    """Every route of the expansion's routers: what it asks for and what its code does."""
    out = []
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None or not hasattr(route, "dependant"):
                continue
            module = endpoint.__module__
            if not module.startswith("app.routers.") or module.rsplit(".", 1)[-1] not in ROUTERS:
                continue
            needs, called = set(), set()

            def walk(dep, needs=needs, called=called):
                name = getattr(dep.call, "__qualname__", "")
                called.add(name.rsplit(".", 1)[-1])
                if "require_permission" in name:
                    needs.update(c.cell_contents for c in (dep.call.__closure__ or ()) if isinstance(c.cell_contents, str))
                for sub in dep.dependencies:
                    walk(sub)

            for d in route.dependant.dependencies:
                walk(d)
            src = _source(endpoint)
            for method in sorted(route.methods - {"HEAD"}):
                out.append({
                    "module": module.rsplit(".", 1)[-1], "method": method, "path": route.path, "needs": frozenset(needs),
                    "audited": bool(re.search(r"intel_audit\.record|write_audit_log", src)),
                    "a_person": "_a_person" in called or bool(re.search(REFUSES_A_KEY, src)),
                    "no_support": "_a_person" in called or bool(re.search(REFUSES_SUPPORT, src)),
                    "params": {p.name: p.field_info.annotation for p in route.dependant.path_params},
                    "source": src,
                })
    return sorted(out, key=lambda x: (x["path"], x["method"]))


def _url(path: str) -> str:
    """A route's address with something of the right shape in each parameter."""
    path = re.sub(r"\{[a-z_]+:uuid\}", lambda _: str(uuid.uuid4()), path)
    return path.replace("{key}", "response").replace("{kind}", "camera")


# ─── A. Every table ──────────────────────────────────────────────────────────

async def test_every_table_is_under_forced_row_level_security_with_one_policy_and_the_fewest_rights():
    tables = expansion_tables()
    assert len(tables) == len(set(tables)) == 36
    assert not [t for t in tables if t.startswith("security_")], "tables named security_ are the intelligence layer's"
    rows = {r["name"]: r for r in await _sql("""
        SELECT c.relname AS name, c.relrowsecurity AS on_, c.relforcerowsecurity AS forced,
               pg_get_userbyid(c.relowner) AS owner,
               has_table_privilege('svc_app', c.oid, 'SELECT') AS sel, has_table_privilege('svc_app', c.oid, 'INSERT') AS ins,
               has_table_privilege('svc_app', c.oid, 'UPDATE') AS upd, has_table_privilege('svc_app', c.oid, 'DELETE') AS del,
               has_table_privilege('svc_app', c.oid, 'TRUNCATE') AS trunc,
               has_table_privilege('svc_app', c.oid, 'REFERENCES') AS refs, has_table_privilege('svc_app', c.oid, 'TRIGGER') AS trig,
               (SELECT array_agg(a.attname::text ORDER BY a.attnum) FROM pg_attribute a
                 WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                   AND has_column_privilege('svc_app', c.oid, a.attnum, 'UPDATE')) AS may_change,
               (SELECT a.attnotnull FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id') AS tenant_kept
          FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(:t)
    """, {"t": tables})}
    assert set(rows) == set(tables)
    policies: dict[str, list] = {}
    for p in await _sql("""
        SELECT p.polrelid::regclass::text AS tbl, p.polname::text AS name, p.polcmd::text AS cmd, p.polpermissive AS permissive,
               pg_get_expr(p.polqual, p.polrelid) AS reads, pg_get_expr(p.polwithcheck, p.polrelid) AS writes,
               p.polroles::text AS roles
          FROM pg_policy p WHERE p.polrelid::regclass::text = ANY(:t)
    """, {"t": tables}):
        policies.setdefault(p["tbl"], []).append(p)

    for t in tables:
        row = rows[t]
        assert row["on_"] and row["forced"], f"{t}: row level security is on, and forced on its owner too"
        assert row["owner"] != "svc_app" and row["tenant_kept"], t
        # One policy, for every command and every role, that reads and writes the caller's organisation only.
        assert len(policies[t]) == 1, t
        policy = policies[t][0]
        assert (policy["name"], policy["cmd"], policy["permissive"], policy["roles"]) == (f"tenant_isolation_{t}", "*", True, "{0}"), t
        assert policy["reads"] == policy["writes"] == POLICY, t
        # The application's role reads and adds. It never empties a table, hangs a key on one or puts a trigger on one.
        assert row["sel"] and row["ins"] and not (row["trunc"] or row["refs"] or row["trig"]), t
        assert row["del"] == (t in MAY_DELETE), t
        assert row["upd"] == (t in MAY_UPDATE_ANY), t
        changed = set(row["may_change"] or [])
        if t in ADD_ONLY or t in MAY_DELETE:
            assert not changed, f"{t} is added to and read, and never rewritten"
        elif t not in MAY_UPDATE_ANY:
            assert changed and not changed & {"id", "tenant_id"}, f"{t}: a row is never renumbered or moved to another organisation"
    assert ADD_ONLY | set(MAY_DELETE) == {t for t in tables if not rows[t]["may_change"]}
    # A history is not rewritten: what a row says of when and by whom it was made is not among what may be changed.
    made = {"created_at", "created_by_user_id", "opened_at", "opened_by_user_id", "requested_at", "requested_by_user_id",
            "drafted_at", "drafted_by_user_id", "raised_at", "raised_by_user_id", "placed_at", "placed_by_user_id",
            "issued_at", "issued_by_user_id", "added_at", "added_by_user_id", "linked_at", "linked_by_user_id",
            "dispatched_at"}
    for t in tables:
        if t not in MAY_UPDATE_ANY:
            assert set(rows[t]["may_change"] or []) & made == RECOUNTED.get(t, set()), (t, set(rows[t]["may_change"]) & made)
    # Each kind of thing that is settled is held still by the database, not only by the route.
    triggers = {r["tbl"]: r["names"] for r in await _sql("""
        SELECT g.tgrelid::regclass::text AS tbl, array_agg(g.tgname::text ORDER BY g.tgname) AS names
          FROM pg_trigger g WHERE NOT g.tgisinternal AND g.tgrelid::regclass::text = ANY(:t) GROUP BY 1
    """, {"t": tables})}
    assert triggers == {
        "case_entries": ["case_entries_closed"], "case_files": ["case_file_closed"], "case_investigators": ["case_investigators_closed"],
        "case_links": ["case_links_closed"], "case_parties": ["case_parties_closed"], "case_tasks": ["case_tasks_closed"],
        "daily_briefings": ["daily_briefing_settled"], "evidence_package_items": ["evidence_package_item_sealed"],
        "evidence_packages": ["evidence_package_sealed"], "maintenance_work_orders": ["maintenance_work_order_over"],
        "shift_handover_summaries": ["shift_summary_settled"], "sop_versions": ["sop_version_decided"]}


# ─── B. Another organisation ─────────────────────────────────────────────────

async def test_another_organisation_reads_none_of_it_and_can_put_nothing_into_it():
    w, other = await _world(), await _world()
    tables = expansion_tables()
    # Something of the first organisation's in several of them.
    async with _client() as c:
        case = await c.post("/api/v1/cases", headers=w["h"][ADMIN], json={"title": "Gate", "summary": "Forced."})
        assert case.status_code == 201, case.text
        place = await c.post("/api/v1/site-map/places", headers=w["h"][ADMIN], json={
            "site_id": str(w["site_a"]), "kind": "GATE", "name": "East gate", "latitude": 1.3001, "longitude": 103.8001})
        assert place.status_code in (200, 201), place.text
        draft = await c.post("/api/v1/daily-briefings", headers=w["h"][ADMIN], json={
            "briefing_date": (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()})
        assert draft.status_code in (200, 201), draft.text
    theirs = {r["t"]: r["n"] for r in await _sql(
        " UNION ALL ".join(f"SELECT '{t}' AS t, count(*) AS n FROM {t} WHERE tenant_id = :w" for t in tables), {"w": w["tenant"]})}
    assert theirs["case_files"] == theirs["case_entries"] == theirs["site_places"] == theirs["daily_briefings"] == 1

    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        seen = {r["t"]: r["n"] for r in (await db.execute(text(
            " UNION ALL ".join(f"SELECT '{t}' AS t, count(*) AS n FROM {t}" for t in tables)))).mappings()}
        await db.rollback()
    assert seen == {t: 0 for t in tables}, "the other organisation reads no row of any of them"

    # A row for the first organisation, put in from the other's scope, is refused by the policy before anything else.
    for t in tables:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
            with pytest.raises(DBAPIError, match="row-level security"):
                await db.execute(text(f"INSERT INTO {t} (tenant_id) VALUES (:w)"), {"w": w["tenant"]})
            await db.rollback()
    # And with no organisation in scope at all, nothing is read: the question is refused, or answered with no row.
    for t in tables:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', '', true)"))
            try:
                nothing = (await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
            except DBAPIError as refused:
                assert "uuid" in str(refused), t
                nothing = 0
            await db.rollback()
        assert nothing == 0, t
    # Through the API, the other organisation's admin is told there is no such thing.
    async with _client() as c:
        assert (await c.get(f"/api/v1/cases/{case.json()['id']}", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.get("/api/v1/cases", headers=other["h"][ADMIN])).json()["items"] == []
        assert (await c.get(f"/api/v1/daily-briefings/{draft.json()['id']}", headers=other["h"][ADMIN])).status_code == 404


# ─── C. Every route ──────────────────────────────────────────────────────────

def test_every_route_asks_for_a_permission_and_takes_only_what_it_declares():
    found = routes()
    assert {r["module"] for r in found} == set(ROUTERS)
    by_method: dict[str, int] = {}
    for r in found:
        by_method[r["method"]] = by_method.get(r["method"], 0) + 1
    assert by_method == {"GET": 72, "POST": 87, "PATCH": 10, "PUT": 6, "DELETE": 1} and len(found) == 176
    # A permission on every one; the one that asks for either of two says so in its own code.
    bare = {(r["method"], r["path"]) for r in found if not r["needs"]}
    assert bare == set(EITHER)
    for key, either in EITHER.items():
        src = next(r["source"] for r in found if (r["method"], r["path"]) == key)
        assert all(f'"{code}"' in src for code in either) and "raise HTTPException(403" in src
    # Nothing the platform owner or the client role holds opens any of them.
    codes = sorted({code for r in found for code in r["needs"]} | {c for either in EITHER.values() for c in either})
    assert len(codes) == len(set(codes)) and all(re.fullmatch(r"[a-z_]+(:[a-z_]+)+", code) for code in codes)
    # An id in an address is an id: anything else is not a route at all. A word in one is checked against a list.
    loose = {r["path"]: sorted(n for n, kind in r["params"].items() if kind is not uuid.UUID) for r in found}
    assert {path: names for path, names in loose.items() if names} == {path: [name] for path, name in WORDS.items()}
    for r in found:
        for name in r["params"]:
            if r["path"] not in WORDS or name != WORDS[r["path"]]:
                assert f"{{{name}:uuid}}" in r["path"], (r["path"], name)
    # One route removes a row; no other answers DELETE.
    assert {(r["method"], r["path"]) for r in found if r["method"] == "DELETE"} == DELETES
    # A request's body takes the fields it declares and no other.
    spec = app.openapi()
    paths = {re.sub(r":uuid\}", "}", r["path"]) for r in found}
    bodies = 0
    for path in paths:
        for method, op in spec["paths"][path].items():
            content = (op.get("requestBody") or {}).get("content", {})
            if "application/json" not in content:
                continue
            schema = content["application/json"]["schema"]
            # A body that may be left out is the body or nothing.
            schema = next((s for s in schema.get("anyOf", []) if "$ref" in s), schema)
            if "$ref" in schema:
                schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
            assert schema.get("additionalProperties") is False, (method, path)
            bodies += 1
    assert bodies >= 70


# ─── D. Every write is audited, and is a person's ────────────────────────────

def test_every_write_is_audited_and_is_a_persons_with_each_exception_named():
    found = routes()
    by = {(r["method"], r["path"]): r for r in found}
    assert set(NOT_AUDITED) | set(NOT_A_PERSONS) | set(READS) | set(TAKEN_OUT) | set(DELETES) <= set(by)
    writes = {key: r for key, r in by.items() if r["method"] != "GET"}
    assert {key for key, r in writes.items() if not r["audited"]} == set(NOT_AUDITED)
    assert {key for key, r in writes.items() if not r["a_person"]} == set(NOT_A_PERSONS)
    # What is not a GET and changes nothing, changes nothing: it adds, updates and removes no row but an audit line.
    for key in READS:
        own = inspect.getsource(sys.modules[f"app.routers.{by[key]['module']}"]).split(f"async def {_name(by[key])}", 1)[1]
        own = own.split("\n@router.", 1)[0].split("\n@holds_router.", 1)[0]
        assert not re.search(r"INSERT INTO|UPDATE \w+ SET|DELETE FROM", own), key
    # The two drafts that are corrected without an audit line are drafts: each refuses anything already settled.
    assert "_draft_of(" in by[("PATCH", "/api/v1/occurrence-book/shift-summaries/{summary_id:uuid}")]["source"]
    assert 'row["state"] != "DRAFT"' in by[("PATCH", "/api/v1/sop/versions/{version_id:uuid}")]["source"]
    assert "site_instruction_reads" in by[("POST", "/api/v1/occurrence-book/instructions/{instruction_id:uuid}/read")]["source"]
    # Records leaving the platform are the organisation's own to take: each refuses a support session, and is audited.
    for key in TAKEN_OUT:
        assert by[key]["no_support"] and by[key]["audited"], key
    # No route hands back where a file is kept.
    for r in found:
        returned = re.findall(r'"(storage_path|file_path|attachment_path)":', r["source"])
        assert not returned, (r["path"], returned)


def _name(route: dict) -> str:
    return re.search(r"async def (\w+)", route["source"]).group(1)


# ─── E. Nobody without a token, and no role without the permission ───────────

async def test_nobody_without_a_token_and_no_role_without_the_permission_gets_anything():
    found = routes()
    w = await _world()
    client_role = uuid.uuid4()
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
               "VALUES (:i, :t, CAST(7 AS smallint), :e, 'x', 'Role 7 User')",
               {"i": client_role, "t": w["tenant"], "e": f"r7-{client_role.hex[:8]}@sweep.test"})
    headers = {**w["h"], 7: _auth(client_role, w["tenant"], 7)}
    held = {role: set() for role in (GUARD, VIEWER, OPERATOR, 7, 1)}
    for r in await _sql("SELECT rp.role_id, p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
                        " WHERE rp.role_id = ANY(:roles)", {"roles": list(held)}):
        held[r["role_id"]].add(r["code"])
    every = {code for r in found for code in r["needs"]} | {c for either in EITHER.values() for c in either}
    # The platform owner holds nothing that opens any route of the expansion. The client role holds one existing
    # permission that does, and it opens the occurrence book as the existing list already shows it.
    assert not held[1] & every
    assert held[7] & every == set(CLIENT_READS)
    assert {r["path"] for r in found if r["needs"] and r["needs"] <= held[7]} == CLIENT_READS["dob:read"]
    assert all(r["method"] == "GET" for r in found if r["needs"] and r["needs"] <= held[7])
    book = inspect.getsource(sys.modules["app.routers.occurrence_book"])
    assert 'return bool(held & {"dob:write", "dob:review"})' in book, "what reviewers wrote is for those who keep the book"

    async with _client() as c:
        for r in found:
            url, send = _url(r["path"]), {"json": {}} if r["method"] in ("POST", "PUT", "PATCH") else {}
            bare = await c.request(r["method"], url, **send)
            assert bare.status_code in (401, 403), (r["method"], r["path"], bare.status_code)
            for role in (GUARD, VIEWER, OPERATOR, 7):
                needs = r["needs"] or frozenset()
                if (r["method"], r["path"]) in EITHER:
                    if held[role] & set(EITHER[(r["method"], r["path"])]):
                        continue
                elif needs <= held[role]:
                    continue
                got = await c.request(r["method"], url, headers=headers[role], **send)
                assert got.status_code == 403, (role, r["method"], r["path"], got.status_code, got.text[:200])
        # A word in an address that is not on the route's list is not found, not an error.
        assert (await c.get("/api/v1/operations-reports/everything", headers=w["h"][ADMIN])).status_code == 404
        wrong = await c.get(f"/api/v1/security-assets/health/toaster/{uuid.uuid4()}", headers=w["h"][ADMIN])
        assert wrong.status_code in (404, 422), wrong.text
        # An id that is not an id is no route at all.
        assert (await c.get("/api/v1/cases/1%20OR%201=1", headers=w["h"][ADMIN])).status_code == 404


# ─── F. What was found in what existed before, and left ──────────────────────

async def test_what_was_found_in_what_existed_before_is_still_as_the_document_says():
    """ENTERPRISE_SECURITY_HARDENING.md, section 9, item 1. This fails the day it is put right - and the document
    is then to be brought up to date."""
    rights = (await _sql("SELECT has_table_privilege('svc_app', 'occurrence_book_entries', 'UPDATE') AS upd, "
                         "       has_table_privilege('svc_app', 'occurrence_book_entries', 'DELETE') AS del, "
                         "       has_table_privilege('svc_app', 'occurrence_book_entries', 'TRUNCATE') AS trunc"))[0]
    assert (rights["upd"], rights["del"], rights["trunc"]) == (True, True, False)
    app_dir = Path(__file__).resolve().parents[1] / "app"
    wrote = [p.name for p in app_dir.rglob("*.py")
             if re.search("UPDATE occurrence_book_entries|DELETE FROM occurrence_book_entries", p.read_text(encoding="utf-8"))]
    assert not wrote, "no code edits or removes an entry"
