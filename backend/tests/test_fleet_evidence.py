"""Evidence around a geofence entry or exit: plate reads, barrier commands, door events and incidents at the site,
each "plate matches" or "near in time only" - never confirmed, and only for whoever may read each.

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 6.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.main import app  # noqa: F401
from app.services import fleet_evidence as fe
from tests.test_drone_api import ADMIN, GUARD, SUPERVISOR, VIEWER, _client, _sql, _world
from tests.test_fleet_geofences import _fence
from tests.test_fleet_tracking import BASE, _vehicle
from tests.test_investigation_search import _audit

AT = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)


async def _scene(w) -> dict:
    """A vehicle SBA 1234 K, a geofence at Factory A, one entry at 08:00, and what the site recorded around it."""
    async with _client() as c:
        v = await _vehicle(c, w, "SBA 1234 K")
        g = (await _fence(c, w)).json()
        org = (await _fence(c, w, site=None)).json()
    s = {"v": v, "g": g, "org": org, "event": uuid.uuid4(), "org_event": uuid.uuid4()}
    cam_a, cam_b, barrier, door, alert, incident = (uuid.uuid4() for _ in range(6))
    stmts = [
        ("INSERT INTO geofence_events (id, tenant_id, vehicle_id, geofence_id, event_type, occurred_at) VALUES (:i, :t, :v, :g, 'entry', :at)",
         {"i": s["event"], "t": w["tenant"], "v": v["id"], "g": g["id"], "at": AT}),
        ("INSERT INTO geofence_events (id, tenant_id, vehicle_id, geofence_id, event_type, occurred_at) VALUES (:i, :t, :v, :g, 'entry', :at)",
         {"i": s["org_event"], "t": w["tenant"], "v": v["id"], "g": org["id"], "at": AT}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i, :t, :s, 'Gate cam')",
         {"i": cam_a, "t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i, :t, :s, 'Other site cam')",
         {"i": cam_b, "t": w["tenant"], "s": w["site_b"]}),
    ]
    for plate, seconds, camera in (("SBA1234K", -40, cam_a), ("sba 1234-k", 90, cam_a), ("SGX 9 Z", 10, cam_a),
                                   ("SBA1234K", 400, cam_a), ("SBA1234K", 5, cam_b)):
        read = {"d": uuid.uuid4(), "at": AT + timedelta(seconds=seconds), "t": w["tenant"], "c": camera, "p": plate}
        stmts.append(("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at) "
                      "VALUES (:d, :t, :c, 'lpr', 0.91, :at)", read))
        stmts.append(("INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number, plate_confidence, direction) "
                      "VALUES (:d, :at, :t, :c, :p, 0.91, 'in')", read))
    stmts += [
        ("INSERT INTO barriers (id, tenant_id, site_id, name, vendor, host) VALUES (:i, :t, :s, 'Main barrier', 'generic', '10.0.0.9')",
         {"i": barrier, "t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO barrier_commands (tenant_id, barrier_id, command, source, decision, plate_number, succeeded, created_at) "
         "VALUES (:t, :b, 'open', 'decision_engine', 'allow', 'SBA 1234K', TRUE, :at)",
         {"t": w["tenant"], "b": barrier, "at": AT + timedelta(seconds=-20)}),
        ("INSERT INTO access_doors (id, tenant_id, site_id, name) VALUES (:i, :t, :s, 'Guard house')",
         {"i": door, "t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO access_events (tenant_id, door_id, event_type, occurred_at) VALUES (:t, :d, 'granted', :at)",
         {"t": w["tenant"], "d": door, "at": AT + timedelta(seconds=30)}),
        ("INSERT INTO alerts (id, tenant_id, module_type, severity, title, status, site_id) VALUES (:i, :t, 'gps', 'high', 'x', 'open', :s)",
         {"i": alert, "t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO incidents (id, tenant_id, alert_id, title, severity, status, created_at) VALUES (:i, :t, :a, 'Tailgating at gate', 'high', 'open', :at)",
         {"i": incident, "t": w["tenant"], "a": alert, "at": AT - timedelta(hours=1)}),
    ]
    from tests.test_drone_api import _run
    await _run(stmts)
    return s


async def test_what_the_site_recorded_is_plate_matches_or_near_in_time_only_and_never_confirmed():
    w = await _world(licensed=False)
    s = await _scene(w)
    async with _client() as c:
        r = await c.get(f"{BASE}/geofence-events/{s['event']}/evidence", headers=w["h"][ADMIN])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "confirm" not in json.dumps(body).lower()
    assert body["window_s"] == fe.DEFAULT_WINDOW_S and body["site_name"] == "Factory A"
    reads = body["sections"]["plate_reads"]["items"]
    assert [(i["plate_number"], i["match"], i["gap_s"]) for i in reads] == [
        ("SBA1234K", "plate_matches", -40), ("sba 1234-k", "plate_matches", 90), ("SGX 9 Z", "near_in_time_only", 10)], \
        "the vehicle's plate first, nearest first; 400 s after and the other site's camera are not shown"
    assert reads[0]["confidence"] == 0.91 and reads[0]["place"] == "Gate cam"
    [command] = body["sections"]["barrier_commands"]["items"]
    assert (command["match"], command["gap_s"], command["command"]) == ("plate_matches", -20, "open")
    [door] = body["sections"]["door_events"]["items"]
    assert (door["match"], door["gap_s"], door["place"]) == ("near_in_time_only", 30, "Guard house")
    [incident] = body["sections"]["incidents"]["items"]
    assert incident["title"] == "Tailgating at gate" and "match" not in incident
    assert body["match_words"] == {"plate_matches": "Plate matches", "near_in_time_only": "Near in time only"}


async def test_a_wider_window_finds_more_and_none_found_is_said():
    w = await _world(licensed=False)
    s = await _scene(w)
    async with _client() as c:
        assert (await c.put(f"{BASE}/evidence-settings", headers=w["h"][SUPERVISOR], json={"window_s": 600})).status_code == 403
        assert (await c.put(f"{BASE}/evidence-settings", headers=w["h"][ADMIN], json={"window_s": 10})).status_code == 422
        assert (await c.put(f"{BASE}/evidence-settings", headers=w["h"][ADMIN], json={"window_s": 600})).status_code == 200
        body = (await c.get(f"{BASE}/geofence-events/{s['event']}/evidence", headers=w["h"][ADMIN])).json()
        assert [i["gap_s"] for i in body["sections"]["plate_reads"]["items"]] == [-40, 90, 400, 10]
        await c.put(f"{BASE}/evidence-settings", headers=w["h"][ADMIN], json={"window_s": 30})
        body = (await c.get(f"{BASE}/geofence-events/{s['event']}/evidence", headers=w["h"][ADMIN])).json()
    assert body["sections"]["door_events"]["items"][0]["gap_s"] == 30
    assert body["sections"]["barrier_commands"]["items"][0]["gap_s"] == -20
    assert [i["gap_s"] for i in body["sections"]["plate_reads"]["items"]] == [10]
    await _sql("DELETE FROM access_events WHERE tenant_id = :t", {"t": w["tenant"]})
    async with _client() as c:
        body = (await c.get(f"{BASE}/geofence-events/{s['event']}/evidence", headers=w["h"][ADMIN])).json()
    assert body["sections"]["door_events"] == {"label": "Door events at the site", "allowed": True, "items": [], "said": "None found."}
    assert [a["detail"] for a in await _audit(w, "fleet.evidence_settings.changed")] == [{"window_s": 600}, {"window_s": 30}]


async def test_each_section_is_for_whoever_may_read_it_and_an_organisation_geofence_has_no_site_to_look_at():
    w = await _world(licensed=False)
    s = await _scene(w)
    held = {r["role_id"]: r["codes"] for r in await _sql("""
        SELECT rp.role_id, array_agg(p.code) AS codes FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE p.code = ANY(:codes) GROUP BY rp.role_id""", {"codes": list(fe.PERMISSIONS)})}
    async with _client() as c:
        for who in (VIEWER, GUARD):
            r = await c.get(f"{BASE}/geofence-events/{s['event']}/evidence", headers=w["h"][who])
            assert r.status_code == 200, r.text
            for name, (code, _) in fe.SECTIONS.items():
                section = r.json()["sections"][name]
                assert section["allowed"] == (code in (held.get(who) or [])), (who, name)
                if not section["allowed"]:
                    assert section["items"] == [] and section["said"] == f"Not shown: it needs {code}."
        r = await c.get(f"{BASE}/geofence-events/{s['org_event']}/evidence", headers=w["h"][ADMIN])
        assert r.json()["sections"] == {} and "whole organisation" in r.json()["said"]
        assert (await c.get(f"{BASE}/geofence-events/{uuid.uuid4()}/evidence", headers=w["h"][ADMIN])).status_code == 404


async def test_somebody_limited_to_other_sites_does_not_see_it():
    w = await _world(licensed=False)
    async with _client() as c:
        v = await _vehicle(c, w, "SITE B 1", site="site_b")
        g = (await _fence(c, w, site="site_b")).json()
    event = uuid.uuid4()
    await _sql("INSERT INTO geofence_events (id, tenant_id, vehicle_id, geofence_id, event_type, occurred_at) VALUES (:i, :t, :v, :g, 'exit', now())",
               {"i": event, "t": w["tenant"], "v": v["id"], "g": g["id"]})
    async with _client() as c:
        assert (await c.get(f"{BASE}/geofence-events/{event}/evidence", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"{BASE}/geofence-events/{event}/evidence", headers=w["h"][ADMIN])).status_code == 200


def test_a_gap_is_counted_from_the_nearer_end_of_a_between_stretch():
    start, end = AT, AT + timedelta(minutes=20)
    assert fe._gap(AT - timedelta(seconds=30), start, end) == -30
    assert fe._gap(AT + timedelta(minutes=5), start, end) == 0
    assert fe._gap(end + timedelta(seconds=45), start, end) == 45
    assert fe._match("SBA1234K", "sba-1234 k") == fe.PLATE_MATCHES
    assert fe._match("SBA1234K", "SBA1234") == fe.NEAR_ONLY
    assert fe._match(None, "SBA1234K") == fe.NEAR_ONLY, "a vehicle without a plate matches nothing"
