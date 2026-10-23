"""Footage Review, phase 3: the events found in a video, and the counts behind its timeline.

The worker finds the events (ai-worker/tests/test_footage_worker.py); here they
are written straight into the database, and what is tested is what the API
gives of them, to whom.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services import footage
from tests.test_drone_api import ADMIN, GUARD, SUPERVISOR, VIEWER, _client, _run, _world

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/footage"


async def _video_with_events(w: dict, *, site: str = "site_a", filmed_at: datetime | None = None) -> str:
    vid = uuid.uuid4()
    path = footage.original_path(w["tenant"], vid, ".mp4")
    stmts = [("""
        INSERT INTO footage_videos (id, tenant_id, title, original_filename, size_bytes, received_bytes, part_size,
                                    storage_path, site_id, status, expires_at, duration_seconds, filmed_at)
        VALUES (:i, :t, 'Car park', 'x.mp4', 10, 10, 8, :p, :s, 'READY', now() + interval '30 days', 30, :f)
    """, {"i": vid, "t": w["tenant"], "p": path, "s": w[site], "f": filmed_at})]
    counts = {3: (1, 0), 4: (2, 0), 5: (1, 1), 20: (0, 1), 21: (0, 1)}
    for second in range(30):
        people, vehicles = counts.get(second, (0, 0))
        stmts.append(("INSERT INTO footage_frames (tenant_id, video_id, second, thumb_path, embedding, model_version, "
                      "people, vehicles) VALUES (:t, :v, :s, 'x', CAST(:e AS vector), 'm', :p, :c)",
                      {"t": w["tenant"], "v": vid, "s": second, "e": "[" + ",".join(["0.036"] * 768) + "]",
                       "p": people, "c": vehicles}))
    for kind, start, end, best, most, score in (("VEHICLES", 5, 21, 20, 1, 0.81), ("PEOPLE", 3, 5, 4, 2, 0.92)):
        stmts.append(("INSERT INTO footage_findings (tenant_id, video_id, kind, start_second, end_second, best_second, "
                      "score, most, reasons, model_version) VALUES (:t, :v, :k, :a, :b, :c, :sc, :m, "
                      "CAST(:r AS jsonb), 'yolov8s-coco-onnx')",
                      {"t": w["tenant"], "v": vid, "k": kind, "a": start, "b": end, "c": best, "sc": score, "m": most,
                       "r": json.dumps({"seen_in_seconds": end - start + 1, "most_at_once": most})}))
    await _run(stmts)
    return str(vid)


async def test_a_video_gives_its_events_in_order_and_the_seconds_anything_was_counted():
    w = await _world()
    filmed = datetime(2026, 10, 3, 22, 0, tzinfo=timezone.utc)
    vid = await _video_with_events(w, filmed_at=filmed)
    async with _client() as c:
        r = await c.get(f"{BASE}/{vid}/findings", headers=w["h"][VIEWER])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(f["kind"], f["start_second"], f["end_second"], f["best_second"], f["most"]) for f in body["findings"]] == [
        ("PEOPLE", 3, 5, 4, 2), ("VEHICLES", 5, 21, 20, 1)]
    people = body["findings"][0]
    assert (people["kind_words"], people["score"], people["verdict"], people["verdict_words"]) == (
        "People present", 0.92, None, None)
    assert people["reasons"] == {"seen_in_seconds": 3, "most_at_once": 2}
    assert datetime.fromisoformat(people["at"]) == filmed + timedelta(seconds=4)
    assert body["counts"] == [[3, 1, 0], [4, 2, 0], [5, 1, 1], [20, 0, 1], [21, 0, 1]], "only seconds with something"
    assert body["may"]["review"] is False


async def test_events_are_seen_by_who_may_see_the_video_and_nobody_else():
    w, x = await _world(), await _world()
    at_b = await _video_with_events(w, site="site_b")
    async with _client() as c:
        limited = await c.get(f"{BASE}/{at_b}/findings", headers=w["h"][SUPERVISOR])
        guard = await c.get(f"{BASE}/{at_b}/findings", headers=w["h"][GUARD])
        stranger = await c.get(f"{BASE}/{at_b}/findings", headers=x["h"][ADMIN])
        admin = await c.get(f"{BASE}/{at_b}/findings", headers=w["h"][ADMIN])
    assert (limited.status_code, guard.status_code, stranger.status_code, admin.status_code) == (404, 403, 404, 200)


async def _altercation(w: dict, vid: str) -> str:
    fid = uuid.uuid4()
    await _run([("INSERT INTO footage_findings (id, tenant_id, video_id, kind, start_second, end_second, best_second, "
                 "score, reasons, model_version) VALUES (:i, :t, :v, 'ALTERCATION', 10, 14, 12, 0.83, CAST(:r AS jsonb), "
                 "'siglip2+yolov8s-pose')",
                 {"i": fid, "t": w["tenant"], "v": vid,
                  "r": json.dumps({"looked_like": "people pushing each other", "moments_with_fast_arms_within_reach": 4})})])
    return str(fid)


async def test_a_possible_altercation_is_marked_by_who_may_review_and_every_verdict_is_audited():
    from tests.test_drone_api import MANAGER, OPERATOR
    from tests.test_investigation_search import _audit

    w, x = await _world(), await _world()
    vid = await _video_with_events(w)
    fid = await _altercation(w, vid)
    people = (await _run([("SELECT id FROM footage_findings WHERE video_id = :v AND kind = 'PEOPLE'", {"v": vid})]))[0][0]["id"]
    async with _client() as c:
        before = (await c.get(f"{BASE}/{vid}", headers=w["h"][ADMIN])).json()
        confirmed = await c.post(f"{BASE}/{vid}/findings/{fid}/verdict", headers=w["h"][OPERATOR],
                                 json={"verdict": "CONFIRMED", "note": "  Two men shoving at the gate  "})
        changed = await c.post(f"{BASE}/{vid}/findings/{fid}/verdict", headers=w["h"][MANAGER],
                               json={"verdict": "REJECTED", "note": "They were moving a pallet together"})
        viewer = await c.post(f"{BASE}/{vid}/findings/{fid}/verdict", headers=w["h"][VIEWER], json={"verdict": "CONFIRMED"})
        not_one = await c.post(f"{BASE}/{vid}/findings/{people}/verdict", headers=w["h"][OPERATOR],
                               json={"verdict": "CONFIRMED"})
        wrong = await c.post(f"{BASE}/{vid}/findings/{fid}/verdict", headers=w["h"][OPERATOR], json={"verdict": "ASSAULT"})
        stranger = await c.post(f"{BASE}/{vid}/findings/{fid}/verdict", headers=x["h"][ADMIN], json={"verdict": "CONFIRMED"})
        after = (await c.get(f"{BASE}/{vid}", headers=w["h"][ADMIN])).json()
        listed = (await c.get(f"{BASE}/{vid}/findings", headers=w["h"][VIEWER])).json()
    assert before["altercations_to_review"] == 1 and after["altercations_to_review"] == 0
    assert confirmed.status_code == 200 and confirmed.json()["verdict_note"] == "Two men shoving at the gate"
    assert changed.json() == {"id": fid, "verdict": "REJECTED", "verdict_words": "Not an altercation",
                              "verdict_note": "They were moving a pallet together"}
    assert (viewer.status_code, not_one.status_code, wrong.status_code, stranger.status_code) == (403, 409, 422, 404)
    finding = next(f for f in listed["findings"] if f["kind"] == "ALTERCATION")
    assert (finding["kind_words"], finding["verdict"], finding["verdict_by_name"]) == (
        "Possible altercation", "REJECTED", "Role 8 User")
    assert finding["reasons"]["looked_like"] == "people pushing each other"
    verdicts = await _audit(w, "footage.verdict")
    assert [(a["detail"]["verdict"], a["detail"]["was"]) for a in verdicts] == [("CONFIRMED", None), ("REJECTED", "CONFIRMED")]
    rows = await _run([("SELECT count(*) AS n FROM alerts WHERE tenant_id = :t", {"t": w["tenant"]}),
                       ("SELECT count(*) AS n FROM incidents WHERE tenant_id = :t", {"t": w["tenant"]})])
    assert (rows[0][0]["n"], rows[1][0]["n"]) == (0, 0), "a verdict raises nothing"
