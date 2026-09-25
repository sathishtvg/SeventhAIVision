"""Drone patrol, phase 9: a drone names the camera that stands for its video.

The fleet screen links a drone to a camera (decision D1: the AI workers treat
the drone's video like any camera, and drone events are read from it). The
link is checked: the camera exists in this organisation, is at the drone's
site, is not a fixed camera with recorded coverage, and stands for one drone.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_drone_api import ADMIN, _client, _drone, _run, _world


async def _camera(w: dict, site: str = "site_a", name: str = "Drone cam") -> uuid.UUID:
    cid = uuid.uuid4()
    await _run([("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,:n)",
                 {"i": cid, "t": w["tenant"], "s": w[site], "n": name})])
    return cid


@pytest.mark.asyncio
async def test_a_drone_is_linked_to_its_camera_on_create_and_update():
    w = await _world()
    cam, other = await _camera(w), await _camera(w, name="Spare cam")
    async with _client() as c:
        d = await _drone(c, w, code="CAM-1", camera_id=str(cam))
        got = await c.get(f"/api/v1/drones/{d['id']}", headers=w["h"][ADMIN])
        moved = await c.put(f"/api/v1/drones/{d['id']}", headers=w["h"][ADMIN], json={"camera_id": str(other)})
    assert d["camera_id"] == str(cam)
    assert got.status_code == 200 and got.json()["camera_name"] == "Drone cam", got.text
    assert moved.status_code == 200 and moved.json()["camera_id"] == str(other), moved.text


@pytest.mark.asyncio
async def test_the_camera_must_be_at_the_drones_site_and_in_this_organisation():
    w, stranger = await _world(), await _world()
    elsewhere, foreign = await _camera(w, site="site_b"), await _camera(stranger)
    async with _client() as c:
        wrong_site = await c.post("/api/v1/drones", headers=w["h"][ADMIN], json={
            "name": "D", "code": "CAM-2", "site_id": str(w["site_a"]), "camera_id": str(elsewhere)})
        not_ours = await c.post("/api/v1/drones", headers=w["h"][ADMIN], json={
            "name": "D", "code": "CAM-3", "site_id": str(w["site_a"]), "camera_id": str(foreign)})
    assert wrong_site.status_code == 422 and "drone's site" in wrong_site.json()["detail"], wrong_site.text
    # Another tenant's camera is invisible under RLS: indistinguishable from none.
    assert not_ours.status_code == 422 and "not found" in not_ours.json()["detail"], not_ours.text


@pytest.mark.asyncio
async def test_a_camera_stands_for_one_drone_and_never_for_a_fixed_view():
    w = await _world()
    cam, fixed = await _camera(w), await _camera(w, name="Gate cam")
    await _run([("INSERT INTO drone_camera_coverage (tenant_id, camera_id, heading_deg, fov_deg, range_m) "
                 "VALUES (:t,:c,90,60,40)", {"t": w["tenant"], "c": fixed})])
    async with _client() as c:
        await _drone(c, w, code="CAM-4", camera_id=str(cam))
        taken = await c.post("/api/v1/drones", headers=w["h"][ADMIN], json={
            "name": "D", "code": "CAM-5", "site_id": str(w["site_a"]), "camera_id": str(cam)})
        covered = await c.post("/api/v1/drones", headers=w["h"][ADMIN], json={
            "name": "D", "code": "CAM-6", "site_id": str(w["site_a"]), "camera_id": str(fixed)})
    assert taken.status_code == 409 and "another drone" in taken.json()["detail"], taken.text
    assert covered.status_code == 409 and "fixed coverage" in covered.json()["detail"], covered.text
