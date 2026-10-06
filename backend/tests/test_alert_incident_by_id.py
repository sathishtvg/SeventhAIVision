"""One alert, one incident, by its id.

The lists are paginated, and the phone's detail screens used to look for their
record in the first page of the unfiltered list: on a tenant with thousands of
open alerts nearly every one opened as "Alert not found". `GET /alerts/{id}`
and `GET /incidents/{id}` are what a screen holding only an id asks.

  A — The record is there by its id, wherever it is in the list
  B — In the shape the list gives, so a screen can show either
  C — The list's permission, the list's site scope, the caller's own tenant
  D — Nothing that was served before is served differently
  E — Dispatching: what the phone's sheet now sends, and what it used to
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql
from tests.test_intel_events import _alert, _world

NOW = datetime.now(timezone.utc)


async def _incident(w: dict, *, camera: str = "cam_a", title: str = "Forced gate", at: datetime | None = None,
                    description: str | None = "The north gate was found open.") -> uuid.UUID:
    iid = uuid.uuid4()
    await _sql("INSERT INTO incidents (id, tenant_id, camera_id, title, description, severity, status, created_at) "
               "VALUES (:i,:t,:c,:title,:d,'high','open',:at)",
               {"i": iid, "t": w["tenant"], "c": w[camera] if camera else None, "title": title, "d": description,
                "at": at or NOW})
    return iid


# ─── A. By id, wherever it is in the list ────────────────────────────────────

@pytest.mark.asyncio
async def test_an_alert_that_is_not_on_the_lists_first_page_is_there_by_its_id():
    w = await _world(enabled=False)
    old = await _alert(w, "intrusion", title="The one from August", at=NOW - timedelta(days=60))
    await _run([("INSERT INTO alerts (id, tenant_id, camera_id, site_id, module_type, severity, title, created_at) "
                 "SELECT gen_random_uuid(), :t, :c, :s, 'intrusion', 'low', 'Newer ' || n, now() - make_interval(mins => n) "
                 "  FROM generate_series(1, 55) n", {"t": w["tenant"], "c": w["cam_a"], "s": w["site_a"]})])
    async with _client() as c:
        page = (await c.get("/api/v1/alerts", headers=w["h"][OPERATOR])).json()
        one = await c.get(f"/api/v1/alerts/{old}", headers=w["h"][OPERATOR])
    assert page["total"] == 56 and len(page["items"]) == 50
    assert str(old) not in {a["id"] for a in page["items"]}, "the list's first page does not hold it"
    assert one.status_code == 200, one.text
    assert one.json()["id"] == str(old) and one.json()["title"] == "The one from August"


@pytest.mark.asyncio
async def test_an_incident_that_is_not_on_the_lists_first_page_is_there_by_its_id():
    w = await _world(enabled=False)
    old = await _incident(w, title="The one from August", at=NOW - timedelta(days=60))
    await _run([("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
                 "SELECT gen_random_uuid(), :t, :c, 'Newer ' || n, 'low', 'open', now() - make_interval(mins => n) "
                 "  FROM generate_series(1, 55) n", {"t": w["tenant"], "c": w["cam_a"]})])
    async with _client() as c:
        page = (await c.get("/api/v1/incidents", headers=w["h"][OPERATOR])).json()
        one = await c.get(f"/api/v1/incidents/{old}", headers=w["h"][OPERATOR])
    assert page["total"] == 56 and str(old) not in {i["id"] for i in page["items"]}
    assert one.status_code == 200, one.text
    assert one.json()["title"] == "The one from August"
    assert one.json()["description"] == "The north gate was found open."


# ─── B. The list's shape ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_record_by_id_is_the_lists_row_and_a_little_more():
    w = await _world(enabled=False)
    alert = await _alert(w, "intrusion", title="Person at Gate 1", code="intrusion.zone_breach")
    await _sql("UPDATE alerts SET message = 'Two people at the north fence.' WHERE id = :a", {"a": alert})
    incident = await _incident(w)
    async with _client() as c:
        (row,) = (await c.get("/api/v1/alerts", headers=w["h"][ADMIN])).json()["items"]
        one = (await c.get(f"/api/v1/alerts/{alert}", headers=w["h"][ADMIN])).json()
        (irow,) = (await c.get("/api/v1/incidents", headers=w["h"][ADMIN])).json()["items"]
        ione = (await c.get(f"/api/v1/incidents/{incident}", headers=w["h"][ADMIN])).json()
    assert {k: one[k] for k in row} == row, "every field of the list's row, with the same value"
    assert set(one) - set(row) == {"message"} and one["message"] == "Two people at the north fence."
    assert (one["camera_name"], one["site_name"]) == ("Gate 1", "Factory A")
    assert {k: ione[k] for k in irow} == irow
    assert set(ione) - set(irow) == {"description", "updated_at", "resolved_at"}


# ─── C. Who, which sites, whose ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_lists_permission_and_site_scope_apply_and_another_tenants_record_is_not_found():
    w, other = await _world(enabled=False), await _world(enabled=False)
    here = await _alert(w, "intrusion", camera="cam_a", site="site_a")
    there = await _alert(w, "intrusion", camera="cam_b", site="site_b")
    inc_here = await _incident(w, camera="cam_a")
    inc_there = await _incident(w, camera="cam_b")
    nobody = uuid.uuid4()
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
               "VALUES (:i,:t,7,:e,'x','A Client')",
               {"i": nobody, "t": w["tenant"], "e": f"client-{nobody.hex[:8]}@byid.test"})
    async with _client() as c:
        for role in (ADMIN, SUPERVISOR, OPERATOR, GUARD, VIEWER):
            assert (await c.get(f"/api/v1/alerts/{here}", headers=w["h"][role])).status_code == 200, role
            assert (await c.get(f"/api/v1/incidents/{inc_here}", headers=w["h"][role])).status_code == 200, role
        # The supervisor is restricted to site A: site B's are not found, exactly as in the lists.
        listed = {a["id"] for a in (await c.get("/api/v1/alerts", headers=w["h"][SUPERVISOR])).json()["items"]}
        assert listed == {str(here)}
        assert (await c.get(f"/api/v1/alerts/{there}", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"/api/v1/incidents/{inc_there}", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"/api/v1/alerts/{there}", headers=w["h"][ADMIN])).status_code == 200
        # Another organisation's, and one that does not exist, answer alike.
        for path in (f"/api/v1/alerts/{here}", f"/api/v1/incidents/{inc_here}"):
            assert (await c.get(path, headers=other["h"][ADMIN])).status_code == 404
            assert (await c.get(path)).status_code in (401, 403)
        assert (await c.get(f"/api/v1/alerts/{uuid.uuid4()}", headers=w["h"][ADMIN])).status_code == 404
        assert (await c.get(f"/api/v1/incidents/{uuid.uuid4()}", headers=w["h"][ADMIN])).status_code == 404
        # A site's customer with no site given to them: whoever a list refuses is refused
        # here, and whoever a list shows nothing to finds nothing here.
        client = _auth(nobody, w["tenant"], 7)
        for listing, one in (("/api/v1/alerts", f"/api/v1/alerts/{here}"),
                             ("/api/v1/incidents", f"/api/v1/incidents/{inc_here}")):
            shown, found = await c.get(listing, headers=client), await c.get(one, headers=client)
            if shown.status_code == 403:
                assert found.status_code == 403, one
            else:
                assert shown.json()["items"] == [] and found.status_code == 404, one


# ─── D. Nothing else moved ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_new_paths_take_an_id_only_and_the_paths_beside_them_answer_as_before():
    w = await _world(enabled=False)
    alert, incident = await _alert(w, "intrusion"), await _incident(w)
    async with _client() as c:
        h = w["h"][ADMIN]
        # Not an id: not this route, and not mistaken for one.
        assert (await c.get("/api/v1/alerts/export", headers=h)).status_code == 404
        assert (await c.get("/api/v1/incidents/not-an-id", headers=h)).status_code == 404
        # What was there before is still what answers.
        assert (await c.get(f"/api/v1/alerts/{alert}/notes", headers=h)).status_code == 200
        assert (await c.get(f"/api/v1/alerts/{alert}/correlated", headers=h)).status_code == 200
        assert (await c.get(f"/api/v1/incidents/{incident}/timeline", headers=h)).status_code == 200
        lists = [(await c.get(p, headers=h)).json() for p in ("/api/v1/alerts", "/api/v1/incidents")]
    assert [set(page) for page in lists] == [{"items", "total", "limit", "offset", "has_more"}] * 2


def _permissions_of(route) -> set[str]:
    """The permission codes a route's dependencies ask for."""
    found: set[str] = set()

    def walk(dependency):
        if "require_permission" in getattr(dependency.call, "__qualname__", ""):
            found.update(cell.cell_contents for cell in (dependency.call.__closure__ or ())
                         if isinstance(cell.cell_contents, str))
        for inner in dependency.dependencies:
            walk(inner)

    for dependency in route.dependant.dependencies:
        walk(dependency)
    return found


def test_each_new_route_is_a_read_behind_the_lists_permission():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            if getattr(route, "path", "") in ("/api/v1/alerts/{alert_id:uuid}", "/api/v1/incidents/{incident_id:uuid}"):
                served[route.path] = (set(route.methods) - {"HEAD"}, _permissions_of(route))
    assert served == {"/api/v1/alerts/{alert_id:uuid}": ({"GET"}, {"alert:read"}),
                      "/api/v1/incidents/{incident_id:uuid}": ({"GET"}, {"incident:read"})}


# ─── E. Dispatching ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_dispatch_names_a_guard_and_its_notes_are_kept_under_the_servers_own_name():
    """The phone's sheet sent an empty guard with `eta_minutes` and `notes`.
    The server cannot store an empty guard, and reads neither of those names."""
    w = await _world(enabled=False)
    incident = await _incident(w)
    async with _client() as c:
        try:
            old = await c.post(f"/api/v1/dispatch/incidents/{incident}", headers=w["h"][OPERATOR],
                               json={"guard_user_id": "", "eta_minutes": 5, "notes": "Use the north gate."})
            refused = old.status_code >= 400
        except Exception:  # noqa: BLE001 — the test client re-raises what the server could not handle
            refused = True
        assert refused, "what the phone used to send was never a dispatch"
        untouched = (await _sql("SELECT dispatched_guard_id, status FROM incidents WHERE id = :i", {"i": incident}))[0]
        assert untouched["dispatched_guard_id"] is None and untouched["status"] == "open"

        new = await c.post(f"/api/v1/dispatch/incidents/{incident}", headers=w["h"][OPERATOR],
                           json={"guard_user_id": str(w["users"][GUARD]), "dispatch_notes": "ETA 5 min. Use the north gate."})
    assert new.status_code == 200, new.text
    row = (await _sql("SELECT dispatched_guard_id, dispatch_notes, status FROM incidents WHERE id = :i",
                      {"i": incident}))[0]
    assert row["dispatched_guard_id"] == w["users"][GUARD]
    assert row["dispatch_notes"] == "ETA 5 min. Use the north gate." and row["status"] == "in_progress"
