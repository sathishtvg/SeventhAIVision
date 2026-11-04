"""A camera's frame rate, delay and missed frames measured (Phase 6 of the known limits, 4 November 2026).

A. The figures of a check, from frame times, and the limits it broke - in words.
B. Listening: the answer to OPTIONS from a stand-in camera, carrying no sign-in; a stand-in ffmpeg that sends frames,
   stalls, refuses the sign-in or never answers; why a check could not run, never what ffmpeg said.
C. Kept, read and judged: a breaking check makes a camera Degraded, a passing, old or failed one does not; who sees
   what; the limits read by asset readers and changed by settings writers, audited; as the application's role, one
   organisation sees nothing of another's.
D. The loop: every stream once, two at a time, drone cameras, switched-off cameras and non-RTSP addresses left out.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.main import app  # noqa: F401 - imported first, so the first test does not absorb the import
from app.db.session import AsyncSessionLocal
from app.services import camera_checks as checks
from tests.test_drone_api import ADMIN, GUARD, MANAGER, SUPERVISOR, VIEWER, _client, _drone, _sql, _world

LATEST, SETTINGS = "/api/v1/camera-checks/latest", "/api/v1/camera-checks/settings"
NOW = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)


def _steady(n: int, rate: float = 15.0, start: float = 0.5) -> tuple[list[float], list[float]]:
    return [start + i / rate for i in range(n)], [10.0 + i / rate for i in range(n)]


# ─── A. The figures, and the limits ─────────────────────────────────────────

def test_a_steady_stream_reads_its_own_rate_no_stall_and_nothing_missed():
    arrivals, stamps = _steady(151)
    got = checks.figures(arrivals, stamps, listened_s=10.0)
    assert got["frames"] == 151 and got["stated_rate"] == 15.0 and got["received_rate"] == 15.0
    assert got["missed_pct"] == 0.0 and got["longest_stall_ms"] == 67 and got["first_picture_ms"] == 500
    assert got["listened_ms"] == 10000
    assert checks.judge(got, checks.DEFAULTS, 40) == []


def test_frames_the_camera_sent_and_that_did_not_arrive_are_missed_and_a_stall_is_the_longest_wait():
    arrivals, stamps = _steady(151)
    # Frames 50 to 59 never arrive: the camera's own times jump by 11 frames, the arrivals wait 0.73 s.
    keep = [i for i in range(151) if not 50 <= i < 60]
    got = checks.figures([arrivals[i] for i in keep], [stamps[i] for i in keep], listened_s=10.0)
    assert got["frames"] == 141 and got["stated_rate"] == 15.0
    assert got["missed_pct"] == round(100 * 10 / 151, 2) and got["longest_stall_ms"] == 733
    # A stream that stops after three seconds: half its rate is not what arrived, and the wait to the end is a stall.
    early, times = _steady(46)
    stopped = checks.figures(early, times, listened_s=10.0)
    assert stopped["received_rate"] == 4.5 and stopped["longest_stall_ms"] == 7000
    said = [b["text"] for b in checks.judge(stopped, checks.DEFAULTS, None)]
    assert said == ["frame rate 4.5 of 15", "a stall of 7.0 s"]


def test_a_restart_of_the_cameras_clock_is_not_missed_frames_and_nothing_arriving_has_no_figures():
    arrivals, stamps = _steady(30)
    stamps = stamps[:15] + [s + 3600 for s in stamps[15:]]
    assert checks.figures(arrivals, stamps, listened_s=2.0)["missed_pct"] == 0.0
    empty = checks.figures([], [])
    assert empty["frames"] == 0 and all(empty[k] is None for k in empty if k != "frames")


def test_each_limit_is_said_in_words_and_only_when_broken():
    figs = {"stated_rate": 25.0, "received_rate": 12.0, "longest_stall_ms": 2600, "missed_pct": 3.5,
            "first_picture_ms": 7100}
    said = checks.judge(figs, checks.DEFAULTS, 820)
    assert [(b["limit"], b["text"]) for b in said] == [
        ("least_rate_pct", "frame rate 12.0 of 25"), ("most_stall_ms", "a stall of 2.6 s"),
        ("most_missed_pct", "3.5% of frames missed"), ("most_answer_ms", "it took 820 ms to answer"),
        ("most_first_picture_ms", "the first picture took 7.1 s")]
    loose = {**checks.DEFAULTS, "least_rate_pct": 40, "most_stall_ms": 3000, "most_missed_pct": 5,
             "most_answer_ms": 1000, "most_first_picture_ms": 8000}
    assert checks.judge(figs, loose, 820) == []


def test_why_a_check_could_not_run_is_read_from_what_ffmpeg_said():
    assert checks.failure("[rtsp @ 0x1] method DESCRIBE failed: 401 Unauthorized") == "SIGN_IN_REFUSED"
    assert checks.failure("[rtsp @ 0x1] method SETUP failed: 453 Not Enough Bandwidth") == "SECOND_CONNECTION_REFUSED"
    assert checks.failure("method DESCRIBE failed: 404 Stream Not Found") == "NO_VIDEO"
    assert checks.failure("Stream map '0:v:0' matches no streams.") == "NO_VIDEO"
    assert checks.failure("Connection to tcp://10.0.0.9:554 failed: Connection refused") == "NO_CONNECTION"
    assert checks.failure("Failed to resolve hostname cam: Name or service not known") == "NO_CONNECTION"
    assert checks.failure("something else entirely") == "FAILED" and checks.failure("") == "FAILED"
    assert set(checks.RESULT_TEXT) == set(checks.RESULTS)


# ─── B. Listening ───────────────────────────────────────────────────────────

async def test_the_answer_time_is_timed_from_a_stand_in_camera_and_the_request_carries_no_sign_in():
    heard: list[bytes] = []

    async def camera(reader, writer, reply=b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n"):
        heard.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(camera, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        ms = await checks.answer_ms(f"rtsp://admin:s3cret-pass@127.0.0.1:{port}/Streaming/101")
    finally:
        server.close()
        await server.wait_closed()
    assert isinstance(ms, int) and 0 <= ms < 2000
    assert heard[0].startswith(f"OPTIONS rtsp://127.0.0.1:{port}/Streaming/101 RTSP/1.0\r\n".encode())
    assert b"s3cret" not in heard[0] and b"admin" not in heard[0]

    async def web(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        writer.close()

    other = await asyncio.start_server(web, "127.0.0.1", 0)
    try:
        assert await checks.answer_ms(f"rtsp://127.0.0.1:{other.sockets[0].getsockname()[1]}/x") is None
    finally:
        other.close()
        await other.wait_closed()
    assert await checks.answer_ms(f"rtsp://127.0.0.1:{port}/x", wait_s=1) is None, "nothing listens there now"
    assert await checks.answer_ms("http://127.0.0.1/x") is None


FAKE_FFMPEG = r'''#!{python}
import sys, time
url = sys.argv[sys.argv.index("-i") + 1]
out = sys.stdout
if url.endswith("/sign-in"):
    sys.stderr.write("[rtsp @ 0x5] method DESCRIBE failed: 401 Unauthorized " + url + "\n")
    sys.exit(1)
if url.endswith("/never"):
    time.sleep(30)
    sys.exit(0)
out.write("#tb 0: 1/90000\n#media_type 0: video\n")
out.flush()
time.sleep(0.2)
n = 15 if url.endswith("/stalls") else 40
for i in range(n):
    out.write("0, %d, %d, 6000, 900, 0xabc\n" % (i * 6000, i * 6000))
    out.flush()
    time.sleep(1 / 15)
time.sleep(30 if url.endswith("/stalls") else 0)
'''


@pytest.fixture
def ffmpeg(tmp_path):
    path = tmp_path / "ffmpeg"
    path.write_text(FAKE_FFMPEG.replace("{python}", sys.executable), encoding="utf-8")
    path.chmod(0o755)
    return str(path)


async def test_a_listen_reads_frames_as_they_arrive_a_stall_and_why_it_could_not_run(ffmpeg):
    steady = await checks.listen("rtsp://cam/steady", seconds=2.0, most_s=8.0, ffmpeg=ffmpeg)
    assert steady["result"] == "OK" and steady["stated_rate"] == 15.0
    assert 12 <= steady["received_rate"] <= 16 and steady["missed_pct"] == 0.0
    assert 150 <= steady["first_picture_ms"] <= 2000 and steady["longest_stall_ms"] < 500
    stalls = await checks.listen("rtsp://cam/stalls", seconds=2.0, most_s=3.0, ffmpeg=ffmpeg)
    assert stalls["result"] == "OK" and stalls["longest_stall_ms"] >= 900 and stalls["received_rate"] < 9
    refused = await checks.listen("rtsp://admin:pw@cam/sign-in", ffmpeg=ffmpeg)
    assert refused == {"result": "SIGN_IN_REFUSED", **checks.figures([], [])}
    never = await checks.listen("rtsp://cam/never", most_s=1.0, ffmpeg=ffmpeg)
    assert never["result"] == "TIMED_OUT" and never["frames"] == 0
    assert (await checks.listen("rtsp://cam/x", ffmpeg="/no/such/ffmpeg"))["result"] == "FAILED"


def test_ffmpeg_copies_packets_and_is_told_not_to_wait():
    args = checks.ffmpeg_args("rtsp://cam/1")
    for pair in (["-c", "copy"], ["-analyzeduration", "0"], ["-fflags", "nobuffer"], ["-rtsp_transport", "tcp"],
                 ["-f", "framecrc"], ["-flush_packets", "1"]):
        at = args.index(pair[0])
        assert args[at:at + 2] == pair, pair
    assert "-vf" not in args and "-frames:v" not in args, "no picture is decoded"


# ─── C. Kept, read and judged ───────────────────────────────────────────────

async def _cameras(w: dict) -> dict:
    ids = {k: uuid.uuid4() for k in ("gate", "yard", "off", "web", "s_gate", "s_yard", "s_off", "s_web")}
    await _sql("""
        INSERT INTO cameras (id, tenant_id, site_id, name, is_active) VALUES
            (:gate, :t, :a, 'Gate', TRUE), (:yard, :t, :b, 'Yard', TRUE), (:off, :t, :a, 'Old store', FALSE),
            (:web, :t, :a, 'Lobby', TRUE)
    """, {**ids, "t": w["tenant"], "a": w["site_a"], "b": w["site_b"]})
    await _sql("""
        INSERT INTO streams (id, tenant_id, camera_id, protocol, url, status) VALUES
            (:s_gate, :t, :gate, 'rtsp', 'rtsp://10.1.1.5:554/live', 'online'),
            (:s_yard, :t, :yard, 'rtsp', 'rtsp://10.1.1.6:554/live', 'online'),
            (:s_off, :t, :off, 'rtsp', 'rtsp://10.1.1.7:554/live', 'offline'),
            (:s_web, :t, :web, 'http', 'http://10.1.1.8/video.mjpg', 'online')
    """, {**ids, "t": w["tenant"]})
    return ids


async def _keep(w, camera, stream, heard, answer=40, at=None):
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        kept = await checks.keep(db, {"camera_id": camera, "stream_id": stream}, answer, heard,
                                 at or datetime.now(timezone.utc))
        await db.commit()
    return kept


GOOD = {"result": "OK", **checks.figures(*_steady(151), listened_s=10.0)}
POOR = {"result": "OK", **checks.figures(*_steady(46), listened_s=10.0)}


async def _health(c, w, who=ADMIN):
    r = await c.get("/api/v1/security-assets/health", headers=w["h"][who], params={"kind": "CAMERA"})
    assert r.status_code == 200, r.text
    return {i["name"]: i for i in r.json()["items"]}


async def test_a_breaking_check_makes_a_camera_degraded_and_a_passing_old_or_failed_one_does_not():
    w = await _world()
    ids = await _cameras(w)
    kept = await _keep(w, ids["gate"], ids["s_gate"], POOR)
    assert [b["limit"] for b in kept["breaks"]] == ["least_rate_pct", "most_stall_ms"]
    async with _client() as c:
        gate = (await _health(c, w))["Gate"]
        assert gate["state"] == "DEGRADED"
        assert "At its last check: frame rate 4.5 of 15; a stall of 7.0 s." in gate["reasons"]
        assert gate["facts"]["last_check_at"]
        # The next check that passes clears it.
        await _keep(w, ids["gate"], ids["s_gate"], GOOD)
        assert (await _health(c, w))["Gate"]["state"] == "OK"
        # A check that could not run, while the feed works, makes no state; neither does an old one.
        await _keep(w, ids["gate"], ids["s_gate"], {"result": "SECOND_CONNECTION_REFUSED", **checks.figures([], [])})
        assert (await _health(c, w))["Gate"]["state"] == "OK"
        await _keep(w, ids["yard"], ids["s_yard"], POOR, at=datetime.now(timezone.utc) - timedelta(minutes=50))
        assert (await _health(c, w))["Yard"]["state"] == "OK"
        got = (await c.get("/api/v1/security-assets/health", headers=w["h"][ADMIN])).json()
    assert "for a camera, from a 10-second check every 15 minutes" in got["note"]
    assert got["not_measured"][0] == "The quality of the picture: darkness, glare, focus"
    assert not any(m in ("Frame rate", "Latency", "Gaps in a recording") for m in got["not_measured"])


async def test_who_sees_a_cameras_latest_check_and_why_a_stream_is_not_checked():
    w = await _world()
    ids = await _cameras(w)
    await _drone_camera(w)
    await _keep(w, ids["gate"], ids["s_gate"], GOOD)
    await _keep(w, ids["yard"], ids["s_yard"], {"result": "SIGN_IN_REFUSED", **checks.figures([], [])},
                at=datetime.now(timezone.utc) - timedelta(hours=1))
    async with _client() as c:
        seen = (await c.get(LATEST, headers=w["h"][GUARD])).json()
        by = {cam["camera_name"]: cam for cam in seen["cameras"]}
        assert set(by) == {"Gate", "Yard", "Old store", "Lobby", "Drone One camera"}
        gate = by["Gate"]["streams"][0]
        assert gate["not_checked"] is None and gate["check"]["result_text"] == "Checked"
        assert gate["check"]["received_rate"] == 15.0 and gate["check"]["stale"] is False
        yard = by["Yard"]["streams"][0]["check"]
        assert yard["result_text"] == "The camera refused the sign-in" and yard["stale"] is True
        assert by["Old store"]["streams"][0]["not_checked"] == "Switched off in the platform."
        assert by["Lobby"]["streams"][0]["not_checked"] == "Only an RTSP address is checked."
        assert by["Drone One camera"]["streams"][0]["not_checked"].startswith("A drone's camera is not checked")
        assert "rtsp://" not in json.dumps(seen), "no camera address is shown"
        assert "not the delay between the scene and the screen" in seen["note"]
        # Held to site A: the yard is not there, and asking for it is a camera not found.
        mine = (await c.get(LATEST, headers=w["h"][SUPERVISOR])).json()["cameras"]
        assert "Yard" not in {cam["camera_name"] for cam in mine}
        assert (await c.get(LATEST, headers=w["h"][SUPERVISOR], params={"camera_id": str(ids["yard"])})).status_code == 404
        assert (await c.get(LATEST, headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        # Seven days of a camera: whoever reads device health; a guard does not.
        assert (await c.get(f"/api/v1/camera-checks/cameras/{ids['gate']}", headers=w["h"][GUARD])).status_code == 403
        week = (await c.get(f"/api/v1/camera-checks/cameras/{ids['gate']}", headers=w["h"][VIEWER])).json()
        assert [k["result"] for k in week["checks"]] == ["OK"] and week["days"] == 7 and week["gaps"] == []
        assert week["limits"]["most_gap_s"] == 60 and week["camera"]["name"] == "Gate"
        assert (await c.get(f"/api/v1/camera-checks/cameras/{ids['yard']}", headers=w["h"][SUPERVISOR])).status_code == 404


async def _drone_camera(w: dict) -> uuid.UUID:
    async with _client() as c:
        drone = await _drone(c, w)
    cam, stream = uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:c,:t,:s,'Drone One camera')",
               {"c": cam, "t": w["tenant"], "s": w["site_a"]})
    await _sql("INSERT INTO streams (id, tenant_id, camera_id, url) VALUES (:i,:t,:c,'rtsp://gw/drone')",
               {"i": stream, "t": w["tenant"], "c": cam})
    await _sql("UPDATE drones SET camera_id = :c WHERE id = :d", {"c": cam, "d": drone["id"]})
    return cam


async def test_the_limits_are_read_by_asset_readers_changed_by_settings_writers_audited_and_used():
    w = await _world()
    ids = await _cameras(w)
    body = {**checks.DEFAULTS, "least_rate_pct": 30, "most_stall_ms": 8000}
    async with _client() as c:
        read = (await c.get(SETTINGS, headers=w["h"][VIEWER])).json()
        assert read["least_rate_pct"] == 70 and read["may_set"] is False and read["updated_at"] is None
        assert read["ranges"]["most_gap_s"] == {"least": 10, "most": 3600}
        assert (await c.get(SETTINGS, headers=w["h"][GUARD])).status_code == 403
        for role in (SUPERVISOR, VIEWER):
            assert (await c.put(SETTINGS, headers=w["h"][role], json=body)).status_code == 403, role
        assert (await c.put(SETTINGS, headers=w["h"][MANAGER], json={**body, "most_gap_s": 5})).status_code == 422
        put = await c.put(SETTINGS, headers=w["h"][MANAGER], json=body)
        assert put.status_code == 200 and put.json()["least_rate_pct"] == 30
        assert put.json()["updated_by_name"] == "Role 8 User"
        assert (await c.get(SETTINGS, headers=w["h"][ADMIN])).json()["may_set"] is True
    audit = await _sql("SELECT detail FROM audit_logs WHERE tenant_id = :t AND action = 'camera_check.settings'",
                       {"t": w["tenant"]})
    detail = audit[0]["detail"] if isinstance(audit[0]["detail"], dict) else json.loads(audit[0]["detail"])
    assert detail["from"]["least_rate_pct"] == 70 and detail["to"]["most_stall_ms"] == 8000
    assert (await _keep(w, ids["gate"], ids["s_gate"], POOR))["breaks"] == [], "judged by the new limits"


async def test_as_the_application_role_an_organisation_sees_and_writes_only_its_own_checks():
    w, other = await _world(), await _world()
    mine, theirs = await _cameras(w), await _cameras(other)
    await _keep(w, mine["gate"], mine["s_gate"], GOOD)
    await _keep(other, theirs["gate"], theirs["s_gate"], POOR)
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles "
                                          "WHERE rolname = current_user"))).scalar()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        seen = (await db.execute(text("SELECT camera_id FROM camera_checks"))).scalars().all()
        assert seen == [mine["gate"]]
        with pytest.raises(Exception):
            await db.execute(text("""
                INSERT INTO camera_checks (tenant_id, camera_id, stream_id, result)
                VALUES (:t, :c, :s, 'FAILED')"""), {"t": str(other["tenant"]), "c": theirs["gate"],
                                                     "s": theirs["s_gate"]})
        await db.rollback()


# ─── D. The loop ────────────────────────────────────────────────────────────

async def test_every_stream_is_checked_once_two_at_a_time_and_the_address_is_never_logged(monkeypatch, caplog):
    w = await _world()
    ids = await _cameras(w)
    await _drone_camera(w)
    await _sql("UPDATE streams SET auth_config = CAST(:a AS jsonb) WHERE id = :s",
               {"a": json.dumps({"username": "admin", "password": "pl41n-pw"}), "s": ids["s_gate"]})
    due = [s for s in await checks.due(AsyncSessionLocal) if s["tenant_id"] == str(w["tenant"])]
    assert sorted(str(s["stream_id"]) for s in due) == sorted([str(ids["s_gate"]), str(ids["s_yard"])])

    async def only_mine(_):
        return due

    monkeypatch.setattr(checks, "due", only_mine)
    running, most = [0], [0]
    urls: list[str] = []

    async def heard(url):
        urls.append(url)
        running[0] += 1
        most[0] = max(most[0], running[0])
        await asyncio.sleep(0.05)
        running[0] -= 1
        return POOR

    async def answered(url):
        return 900

    def build(url, auth):
        return url.replace("rtsp://", f"rtsp://{auth['username']}:{auth['password']}@") if auth else url

    async def check(factory, stream, build_url):
        return await checks.check_one(factory, stream, build_url, listen_fn=heard, answer_fn=answered)

    caplog.set_level(logging.INFO, logger="app.services.camera_checks")
    assert await checks.run_cycle(AsyncSessionLocal, build, every_s=0.1, at_once=2, check=check) == 2
    assert most[0] <= 2 and any("pl41n-pw" in u for u in urls), "the check is given the sign-in"
    assert "pl41n-pw" not in caplog.text and "rtsp://" not in caplog.text
    rows = await _sql("SELECT stream_id, result, answer_ms, breaks FROM camera_checks WHERE tenant_id = :t",
                      {"t": w["tenant"]})
    assert sorted(str(r["stream_id"]) for r in rows) == sorted([str(ids["s_gate"]), str(ids["s_yard"])])
    breaks = rows[0]["breaks"] if isinstance(rows[0]["breaks"], list) else json.loads(rows[0]["breaks"])
    assert {b["limit"] for b in breaks} == {"least_rate_pct", "most_stall_ms", "most_answer_ms"}
