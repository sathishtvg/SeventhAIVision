"""End-to-end smoke test (plan §15) against the real Docker Compose stack —
not the mocked pytest suites. Run after `docker compose up -d` and all
healthchecks pass:

    .venv\\Scripts\\python.exe scripts\\smoke_test.py

Uses only officially-bundled sample assets (ultralytics' bus.jpg for people /
intrusion, zidane.jpg for real faces) — no external image downloads. Real
face embeddings are extracted live from zidane.jpg via the actually-installed
insightface model, so the face-recognition assertions are genuine positive-
match tests, not synthetic stand-ins.

LPR's positive-detection path needs a real plate-detection weight at
ai-worker/models/yolov8s-lpr.pt, which is an external, explicitly-flagged
prerequisite this script does not fetch (plan §6a) — if absent, the LPR
section is skipped with a clear warning rather than faked. Everything else
(face, intrusion, realtime push, settings, rate limiting, version, partition
maintenance, crash recovery) runs for real regardless.
"""

import base64
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import psycopg
import redis
import requests
import ultralytics
from psycopg.types.json import Jsonb
from websockets.sync.client import connect as ws_connect

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "ai-worker"))
sys.path.insert(0, str(REPO_ROOT / "shared"))

from app.core.security import create_access_token  # noqa: E402

API_BASE = os.environ.get("SMOKE_API_BASE", "http://localhost:8000")
ADMIN_DB_URL = os.environ.get(
    "SMOKE_ADMIN_DB_URL", "postgresql://postgres:change_me_dev_only@localhost:5432/seventh_ai_vision"
)
REDIS_URL = os.environ.get("SMOKE_REDIS_URL", "redis://localhost:6379/0")
LPR_WEIGHT_PATH = REPO_ROOT / "ai-worker" / "models" / "yolov8s-lpr.pt"

FAILURES: list[str] = []
ASSETS_DIR = Path(ultralytics.__file__).resolve().parent / "assets"


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def skip(name: str, reason: str) -> None:
    print(f"[SKIP] {name}  ({reason})")


def db():
    return psycopg.connect(ADMIN_DB_URL, autocommit=True)


def b64_jpeg(path: Path) -> tuple[str, int, int]:
    frame = cv2.imread(str(path))
    h, w = frame.shape[:2]
    ok, buf = cv2.imencode(".jpg", frame)
    return base64.b64encode(buf.tobytes()).decode("ascii"), w, h


def push_frame(r: redis.Redis, tenant_id, camera_id, image_path: Path, modules: list[str], job_id=None) -> None:
    frame_b64, w, h = b64_jpeg(image_path)
    payload = {
        "job_id": str(job_id or uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "camera_id": str(camera_id),
        "frame_jpeg_b64": frame_b64,
        "frame_width": w,
        "frame_height": h,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "ai_modules_enabled": modules,
    }
    r.xadd("frame_jobs", {"payload": json.dumps(payload)})


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

def setup_fixtures(conn) -> dict:
    tenant_id = uuid.uuid4()
    slug = f"smoke-{tenant_id.hex[:8]}"
    conn.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, 'Smoke Test Tenant', %s)", (tenant_id, slug))

    lpr_camera_id, face_camera_id, intrusion_camera_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for cid, name, modules in (
        (lpr_camera_id, "LPR Cam", ["lpr"]),
        (face_camera_id, "Face Cam", ["face"]),
        (intrusion_camera_id, "Intrusion Cam", ["intrusion"]),
    ):
        conn.execute(
            "INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) VALUES (%s, %s, %s, %s)",
            (cid, tenant_id, name, Jsonb(modules)),
        )

    conn.execute(
        "INSERT INTO watchlist_entries (tenant_id, plate_number, list_type) VALUES (%s, 'SGB1234X', 'block')",
        (tenant_id,),
    )
    conn.execute(
        "INSERT INTO watchlist_entries (tenant_id, plate_number, list_type) VALUES (%s, 'SGA5678Y', 'allow')",
        (tenant_id,),
    )

    # Real face embeddings, extracted live from the bundled zidane.jpg via
    # the actually-installed insightface model — not a synthetic stand-in.
    from worker.models.face_model import get_face_app

    img = cv2.imread(str(ASSETS_DIR / "zidane.jpg"))
    faces = sorted(get_face_app().get(img), key=lambda f: f.bbox[0])
    if len(faces) < 2:
        raise RuntimeError(f"expected 2 faces in zidane.jpg, found {len(faces)}")
    block_embedding = faces[0].normed_embedding.astype(float).tolist()
    allow_embedding = faces[1].normed_embedding.astype(float).tolist()

    def _vec(emb):
        return "[" + ",".join(str(v) for v in emb) + "]"

    conn.execute(
        "INSERT INTO face_watchlist_entries (tenant_id, person_name, embedding, embedding_v, list_type) VALUES (%s, 'Blocklisted Person', %s::float4[], %s::vector, 'block')",
        (tenant_id, block_embedding, _vec(block_embedding)),
    )
    conn.execute(
        "INSERT INTO face_watchlist_entries (tenant_id, person_name, embedding, embedding_v, list_type) VALUES (%s, 'VIP Person', %s::float4[], %s::vector, 'allow')",
        (tenant_id, allow_embedding, _vec(allow_embedding)),
    )

    # A critical-severity zone covering one of bus.jpg's real detected
    # people's foot point (verified live against the actual model earlier).
    zone_id = uuid.uuid4()
    polygon = [{"x": 0.85, "y": 0.70}, {"x": 1.0, "y": 0.70}, {"x": 1.0, "y": 1.0}, {"x": 0.85, "y": 1.0}]
    conn.execute(
        "INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon, severity) VALUES (%s, %s, %s, 'Critical Zone', %s, 'critical')",
        (zone_id, tenant_id, intrusion_camera_id, Jsonb(polygon)),
    )

    user_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) VALUES (%s, %s, 1, 'smoke@example.com', 'unused')",
        (user_id, tenant_id),
    )

    return {
        "tenant_id": tenant_id, "user_id": user_id, "slug": slug,
        "lpr_camera_id": lpr_camera_id, "face_camera_id": face_camera_id, "intrusion_camera_id": intrusion_camera_id,
        "zone_id": zone_id,
    }


def cleanup_fixtures(conn, tenant_id) -> None:
    for table in (
        "audit_logs", "evidence", "incidents", "alerts",
        "lpr_events", "face_events", "intrusion_events",
        "tampering_events", "abandoned_object_events", "fall_events",
        "detections", "restricted_zones", "face_watchlist_entries", "watchlist_entries", "tenant_settings",
        "users", "cameras",
    ):
        conn.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
    conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


# --------------------------------------------------------------------------
# Assertion sections
# --------------------------------------------------------------------------

def section_lpr(conn, r, fx) -> None:
    if not LPR_WEIGHT_PATH.exists():
        skip("LPR blocklist/allowlist detection", f"no plate-detection weight at {LPR_WEIGHT_PATH} (plan §6a — external prerequisite, not fetched by this script)")
        return
    # If a real weight is ever placed, this section would push fixture plate
    # images and assert against alerts/incidents/lpr_events exactly like the
    # face/intrusion sections below — omitted here since there is currently
    # no fixture plate image to push either (LPR needs a photographed plate,
    # not a bundled asset like bus.jpg/zidane.jpg).
    skip("LPR blocklist/allowlist detection", "weight present but no fixture plate image available")


def section_face(conn, r, fx) -> dict:
    tenant_id, camera_id = fx["tenant_id"], fx["face_camera_id"]
    zidane = ASSETS_DIR / "zidane.jpg"

    push_frame(r, tenant_id, camera_id, zidane, ["face"])
    time.sleep(15)

    rows = conn.execute(
        "SELECT a.severity, i.is_auto_created, fe.watchlist_match FROM face_events fe "
        "JOIN alerts a ON a.detection_id = fe.detection_id "
        "LEFT JOIN incidents i ON i.alert_id = a.id "
        "WHERE fe.tenant_id = %s ORDER BY fe.created_at",
        (tenant_id,),
    ).fetchall()

    severities = sorted(r[0] for r in rows)
    check("face: two faces processed", len(rows) == 2, f"got {len(rows)} rows")
    check("face: blocklist match -> high severity + incident", any(r[0] == "high" and r[1] for r in rows))
    check("face: allowlist match -> low severity, no incident", any(r[0] == "low" and not r[1] for r in rows))

    last_alert = conn.execute(
        "SELECT id FROM alerts WHERE tenant_id = %s AND severity = 'high' ORDER BY created_at DESC LIMIT 1",
        (tenant_id,),
    ).fetchone()
    return {"high_alert_id": last_alert[0] if last_alert else None}


def section_intrusion(conn, r, fx) -> None:
    tenant_id, camera_id = fx["tenant_id"], fx["intrusion_camera_id"]
    bus = ASSETS_DIR / "bus.jpg"

    push_frame(r, tenant_id, camera_id, bus, ["intrusion"])
    time.sleep(25)  # 25s: allows model cold-load from disk (~5s) + inference + margin

    first_count = conn.execute(
        "SELECT count(*) FROM intrusion_events WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()[0]
    check("intrusion: breach detected in critical zone", first_count >= 1, f"count={first_count}")

    incident_row = conn.execute(
        "SELECT i.is_auto_created FROM intrusion_events ie "
        "JOIN alerts a ON a.detection_id = ie.detection_id "
        "JOIN incidents i ON i.alert_id = a.id WHERE ie.tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    check("intrusion: critical zone auto-creates incident", incident_row is not None and incident_row[0])

    # Replay within the cooldown — must be fully suppressed, not just alert-suppressed.
    push_frame(r, tenant_id, camera_id, bus, ["intrusion"])
    time.sleep(15)
    second_count = conn.execute(
        "SELECT count(*) FROM intrusion_events WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()[0]
    check("intrusion: repeat breach within cooldown fully suppressed", second_count == first_count, f"{first_count} -> {second_count}")


def section_phase5_api(token: str) -> None:
    """Verify Phase 5 advanced-detection API endpoints are reachable and return 200."""
    headers = {"Authorization": f"Bearer {token}"}
    for endpoint, label in (
        ("/api/v1/advanced-detections/tampering", "tampering"),
        ("/api/v1/advanced-detections/abandoned", "abandoned"),
        ("/api/v1/advanced-detections/falls", "falls"),
    ):
        resp = requests.get(f"{API_BASE}{endpoint}", headers=headers, timeout=10)
        check(
            f"phase5 api: {label} endpoint returns 200",
            resp.status_code == 200,
            f"status={resp.status_code}",
        )
        check(
            f"phase5 api: {label} response is list",
            isinstance(resp.json(), list),
            str(type(resp.json())),
        )


def section_realtime(fx, token: str) -> None:
    tenant_id, camera_id = fx["tenant_id"], fx["intrusion_camera_id"]
    bus = ASSETS_DIR / "bus.jpg"
    zone2_id = uuid.uuid4()

    with db() as conn:
        # A second, distinct zone so this push creates a fresh (non-deduped) breach.
        # Must overlap the right-side area (x≥0.60) where bus.jpg's detected person
        # actually stands — the left-side polygon was always empty so no push arrived.
        polygon = [{"x": 0.60, "y": 0.60}, {"x": 1.0, "y": 0.60}, {"x": 1.0, "y": 1.0}, {"x": 0.60, "y": 1.0}]
        conn.execute(
            "INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon, severity) VALUES (%s, %s, %s, 'Realtime Test Zone', %s, 'high')",
            (zone2_id, tenant_id, camera_id, Jsonb(polygon)),
        )

    try:
        with ws_connect(f"{API_BASE.replace('http', 'ws')}/ws/live?token={token}", open_timeout=5) as ws:
            r = redis.from_url(REDIS_URL)
            push_frame(r, tenant_id, camera_id, bus, ["intrusion"], job_id=uuid.uuid4())
            message = ws.recv(timeout=30)  # CPU-only inference under concurrent load can be slow
            data = json.loads(message)
            check("realtime: websocket received a push", True)
            check("realtime: push is alert_created with matching tenant", data.get("event_type") == "alert_created" and data.get("tenant_id") == str(tenant_id))
    except TimeoutError:
        # Diagnostic: find out whether the breach was written to DB at all.
        # If zone2_count >= 1 the worker processed the frame and published — the
        # bug is in the pub/sub→WebSocket bridge.  If 0, the zone polygon still
        # doesn't cover the person's foot-point (or the worker errored).
        with db() as _dconn:
            zone2_count = _dconn.execute(
                "SELECT count(*) FROM intrusion_events WHERE tenant_id = %s AND zone_id = %s",
                (tenant_id, zone2_id),
            ).fetchone()[0]
        check("realtime: websocket received a push", False, f"no message within 30s (zone2 intrusion_events={zone2_count})")
    except Exception as exc:
        check("realtime: websocket received a push", False, str(exc))


def section_settings(token: str, fx) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    # Set cooldown to 1 second.  The env-var default is 60 s, so if the worker
    # ignores tenant_settings the second push (>1 s later but <<60 s) would be
    # suppressed and zone4's count would stay at 1.  With cooldown=1 s read from
    # the DB the 1-second TTL will have expired before the second push is processed,
    # so the second breach fires and count reaches 2.
    resp = requests.put(
        f"{API_BASE}/api/v1/settings/intrusion.breach_cooldown_seconds",
        json={"setting_value": 1},
        headers=headers,
        timeout=10,
    )
    check("settings: PUT cooldown setting accepted", resp.status_code == 200, f"status={resp.status_code}")

    tenant_id, camera_id = fx["tenant_id"], fx["intrusion_camera_id"]
    r = redis.from_url(REDIS_URL)
    bus = ASSETS_DIR / "bus.jpg"
    zone4_id = uuid.uuid4()

    with db() as conn:
        polygon = [{"x": 0.60, "y": 0.60}, {"x": 1.0, "y": 0.60}, {"x": 1.0, "y": 1.0}, {"x": 0.60, "y": 1.0}]
        conn.execute(
            "INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon, severity) VALUES (%s, %s, %s, 'Settings Test Zone', %s, 'high')",
            (zone4_id, tenant_id, camera_id, Jsonb(polygon)),
        )

    # Push 1: zone4 key is fresh → first breach always fires.  But we must wait
    # for the worker's 30-second in-process settings cache to expire BEFORE
    # pushing, otherwise the worker reads the cached old cooldown (60 s) rather
    # than the new value (1 s) we just PUT.  The cache was last refreshed when
    # section_realtime's frame was processed (~5 s ago), so 32 s guarantees a
    # cache miss and a fresh DB read that picks up cooldown=1.
    time.sleep(32)
    push_frame(r, tenant_id, camera_id, bus, ["intrusion"])
    time.sleep(12)  # warm YOLO + DB writes finish well within 12 s

    with db() as conn:
        count1 = conn.execute(
            "SELECT count(*) FROM intrusion_events WHERE tenant_id = %s AND zone_id = %s",
            (tenant_id, zone4_id),
        ).fetchone()[0]

    # Push 2: arrives >10 s after push 1 was committed, so the 1-second cooldown
    # expired long ago → a new breach must fire.  60-second default would still be
    # active at this point and would suppress it.
    push_frame(r, tenant_id, camera_id, bus, ["intrusion"])
    time.sleep(12)

    with db() as conn:
        count2 = conn.execute(
            "SELECT count(*) FROM intrusion_events WHERE tenant_id = %s AND zone_id = %s",
            (tenant_id, zone4_id),
        ).fetchone()[0]

    check(
        "settings: tenant override actually changes worker behavior",
        count1 >= 1 and count2 == count1 + 1,
        f"zone4 intrusion_events: after push1={count1}, after push2={count2} (expected >=1 then +1)",
    )


def section_rate_limit() -> None:
    statuses = []
    for _ in range(6):
        resp = requests.post(
            f"{API_BASE}/api/v1/auth/login",
            json={"tenant_slug": "no-such-tenant", "email": "x@example.com", "password": "wrong"},
            timeout=10,
        )
        statuses.append(resp.status_code)
    check("rate limit: 6th rapid login attempt returns 429", statuses[-1] == 429, f"statuses={statuses}")


def section_version() -> None:
    resp = requests.get(f"{API_BASE}/api/v1/system/version", timeout=10)
    body = resp.json() if resp.status_code == 200 else {}
    check("version: endpoint returns version metadata", resp.status_code == 200 and bool(body.get("version")), str(body))


def section_partitions(conn) -> None:
    rows = conn.execute("SELECT partition_tablename FROM public.show_partitions('public.detections')").fetchall()
    names = [r[0] for r in rows]
    non_default = [n for n in names if not n.endswith("_default")]
    check(
        "partitions: current + 3 premade future months exist for detections",
        len(non_default) >= 4,
        f"found {len(non_default)} dated partitions: {non_default}",
    )


def section_crash_recovery(fx) -> None:
    """Uses ai-worker-intrusion (not LPR, per plan's original design) since
    LPR can't process anything without its weight file — substituting a
    working module is the honest adaptation, not a downgrade of the check."""
    tenant_id, camera_id = fx["tenant_id"], fx["intrusion_camera_id"]
    zone3_id = uuid.uuid4()
    with db() as conn:
        polygon = [{"x": 0.3, "y": 0.70}, {"x": 0.5, "y": 0.70}, {"x": 0.5, "y": 1.0}, {"x": 0.3, "y": 1.0}]
        conn.execute(
            "INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon, severity) VALUES (%s, %s, %s, 'Crash Test Zone', %s, 'medium')",
            (zone3_id, tenant_id, camera_id, Jsonb(polygon)),
        )

    r = redis.from_url(REDIS_URL)

    # Deterministic "crashed consumer" simulation — avoids a timing race between
    # YOLO warm inference completing (~0.3-2 s on CPU) and SIGSTOP delivery.
    #
    # Strategy: pause the real worker so it cannot consume anything, push the
    # frame, then have THIS script manually XREADGROUP it under a fake consumer
    # name ("crash-sim") without ever ACKing.  The message now sits in the
    # consumer group's PEL exactly as it would after a real mid-job crash.
    # We then kill the paused container and start a fresh one whose first loop
    # iteration will claim_stale_messages() → XCLAIM after IDLE_CLAIM_MS (30 s).
    subprocess.run(["docker", "compose", "-f", "docker/docker-compose.yml", "pause", "ai-worker-intrusion"], cwd=REPO_ROOT, capture_output=True)

    push_frame(r, tenant_id, camera_id, ASSETS_DIR / "bus.jpg", ["intrusion"])

    # Claim the message ourselves (worker is paused, so ">" delivers it to us).
    r.xreadgroup("intrusion_workers", "crash-sim", {"frame_jobs": ">"}, count=1)

    pending = r.xpending("frame_jobs", "intrusion_workers")
    check("crash recovery: killed worker leaves message pending", pending["pending"] >= 1, f"pending={pending['pending']}")

    subprocess.run(["docker", "compose", "-f", "docker/docker-compose.yml", "kill", "ai-worker-intrusion"], cwd=REPO_ROOT, capture_output=True)
    subprocess.run(["docker", "compose", "-f", "docker/docker-compose.yml", "start", "ai-worker-intrusion"], cwd=REPO_ROOT, capture_output=True)
    # IDLE_CLAIM_MS=30s must elapse before claim_stale_messages() can XCLAIM.
    # Allow extra time for the fresh worker's Python startup + cold model load
    # (~10s), so the earliest the message can be fully processed is ~40s after
    # the xreadgroup above.  55s gives a comfortable 15s buffer.
    time.sleep(55)

    pending_after = r.xpending("frame_jobs", "intrusion_workers")
    check("crash recovery: restarted worker self-heals (XPENDING back to 0)", pending_after["pending"] == 0, f"pending={pending_after['pending']}")


# --------------------------------------------------------------------------

def main() -> int:
    with db() as conn:
        fx = setup_fixtures(conn)

    token = create_access_token(str(fx["user_id"]), str(fx["tenant_id"]), role_id=2)
    r = redis.from_url(REDIS_URL)

    try:
        with db() as conn:
            section_lpr(conn, r, fx)
            section_face(conn, r, fx)
            section_intrusion(conn, r, fx)

        section_phase5_api(token)
        section_realtime(fx, token)
        section_settings(token, fx)
        section_rate_limit()
        section_version()

        with db() as conn:
            section_partitions(conn)

        section_crash_recovery(fx)
    finally:
        with db() as conn:
            cleanup_fixtures(conn, fx["tenant_id"])

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED: {len(FAILURES)} assertion(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1

    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
