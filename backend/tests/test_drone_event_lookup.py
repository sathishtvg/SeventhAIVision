"""Drone patrol, phase 10: from an alert, or an incident, to the drone event.

The phone opens a drone alert from its Alerts tab and needs the event behind it
— the picture, the place, the response. The alert list carries no link, so the
event list answers by alert id, and by incident id for the same reason.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_drone_api import ADMIN, SUPERVISOR, _client, _event, _sql, _world


async def _linked(w: dict, *, site: str = "site_a") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """An event with the alert it raised and the incident it opened."""
    event, alert, incident = await _event(w, site=site), uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO alerts (id, tenant_id, module_type, title, site_id) "
               "VALUES (:i,:t,'drone_patrol','Drone: intrusion',:s)", {"i": alert, "t": w["tenant"], "s": w[site]})
    await _sql("INSERT INTO incidents (id, tenant_id, alert_id, title) VALUES (:i,:t,:a,'Intrusion')",
               {"i": incident, "t": w["tenant"], "a": alert})
    await _sql("UPDATE drone_events SET alert_id = :a, incident_id = :n WHERE id = :e",
               {"a": alert, "n": incident, "e": event})
    return event, alert, incident


@pytest.mark.asyncio
async def test_an_alert_and_an_incident_each_lead_to_their_event():
    w = await _world()
    event, alert, incident = await _linked(w)
    other = await _event(w)  # an event with neither, which neither filter may return
    async with _client() as c:
        by_alert = await c.get("/api/v1/drone-events", headers=w["h"][ADMIN], params={"alert_id": str(alert)})
        by_incident = await c.get("/api/v1/drone-events", headers=w["h"][ADMIN],
                                  params={"incident_id": str(incident)})
        unknown = await c.get("/api/v1/drone-events", headers=w["h"][ADMIN], params={"alert_id": str(uuid.uuid4())})
        everything = await c.get("/api/v1/drone-events", headers=w["h"][ADMIN])
    assert [e["id"] for e in by_alert.json()["items"]] == [str(event)], by_alert.text
    assert [e["id"] for e in by_incident.json()["items"]] == [str(event)], by_incident.text
    assert unknown.status_code == 200 and unknown.json()["items"] == [] and unknown.json()["total"] == 0
    assert {e["id"] for e in everything.json()["items"]} == {str(event), str(other)}


@pytest.mark.asyncio
async def test_the_lookup_does_not_reach_past_site_scoping_or_the_tenant():
    w, stranger = await _world(), await _world()
    _, alert_b, _ = await _linked(w, site="site_b")          # the supervisor is scoped to site A
    _, foreign_alert, _ = await _linked(stranger)
    async with _client() as c:
        out_of_scope = await c.get("/api/v1/drone-events", headers=w["h"][SUPERVISOR],
                                   params={"alert_id": str(alert_b)})
        not_ours = await c.get("/api/v1/drone-events", headers=w["h"][ADMIN], params={"alert_id": str(foreign_alert)})
        malformed = await c.get("/api/v1/drone-events", headers=w["h"][ADMIN], params={"alert_id": "not-a-uuid"})
    assert out_of_scope.status_code == 200 and out_of_scope.json()["items"] == [], out_of_scope.text
    assert not_ours.status_code == 200 and not_ours.json()["items"] == [], not_ours.text
    assert malformed.status_code == 422
