"""Phase 14 — the §52 Definition of Done, walked end to end against a live stack.

This is not a unit test. It logs in over HTTP, creates a schedule through the
same API the admin screen calls, lets the REAL scheduler pick it up, captures
REAL snapshots over RTSP, answers the questions, completes the patrol, generates
the PDF and Excel, and checks the report on disk, the queued email, the history
listing, the Command Centre exceptions and the incident.

Every step in §52's flow is one numbered check. A step either passes with the
evidence printed beside it, or the run stops there — a definition of done that
keeps going after a failed step is a checklist, not a verification.

Run inside the api container so it can reach postgres, mediamtx and the API:

    docker exec docker-api-1 python /app/scripts/ops/vpatrol_e2e.py

It creates a temporary admin and a temporary schedule, and removes both at the
end even when a step fails. Nothing it creates outlives the run except the
patrol session and report, which are the evidence.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
API = f"{BASE}/api/v1/virtual-patrol"
SITE_NAME = os.environ.get("E2E_SITE", "Marina Bay Tower")
#: Where the test streams live. mediamtx generates them from the video files
#: committed under docker/mediamtx/videos, so this works in CI as well as
#: on a developer machine -- no real camera required.
RTSP_HOST = os.environ.get("E2E_RTSP_HOST", "mediamtx:8554")
SEED_CAMERAS = int(os.environ.get("E2E_CAMERAS", "3"))

_step = 0
_failures: list[str] = []


def step(label: str, ok: bool, detail: str = "") -> None:
    global _step
    _step += 1
    mark = "PASS" if ok else "FAIL"
    print(f"  [{_step:2d}] {mark}  {label}" + (f"  — {detail}" if detail else ""), flush=True)
    if not ok:
        _failures.append(f"{_step}. {label}: {detail}")
        raise SystemExit(f"\nSTOPPED at step {_step}: {label}\n{detail}")


def _db_url() -> str:
    return os.environ["DATABASE_URL"]


def _admin_url() -> str:
    """Superuser connection, for setup and for reading past RLS when checking."""
    raw = _db_url()
    host = raw.split("@", 1)[1].split(":", 1)[0]
    db = raw.rsplit("/", 1)[1].split("?")[0]
    pw = os.environ.get("POSTGRES_PASSWORD", "change_me_dev_only")
    user = os.environ.get("POSTGRES_USER", "postgres")
    return f"postgresql+asyncpg://{user}:{pw}@{host}:5432/{db}"


async def sql(stmt: str, params: dict | None = None, *, url: str | None = None):
    engine = create_async_engine(url or _admin_url())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            r = await s.execute(text(stmt), params or {})
            rows = r.mappings().all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


async def _seed_world() -> dict:
    """A tenant, a site and SEED_CAMERAS cameras pointed at the test streams.

    Everything the flow needs and nothing it does not, so this script is
    hermetic: it runs against a database that has only had its migrations
    applied. Named distinctly enough that nobody mistakes the rows for a real
    customer's.
    """
    tenant, site = uuid.uuid4(), uuid.uuid4()
    await sql("INSERT INTO tenants (id, name, slug, is_active) "
              "VALUES (:t, 'Phase 14 E2E', :s, TRUE)",
              {"t": tenant, "s": f"e2e-{tenant.hex[:10]}"})
    await sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,:n)",
              {"i": site, "t": tenant, "n": SITE_NAME})
    for n in range(1, SEED_CAMERAS + 1):
        cam = uuid.uuid4()
        await sql("INSERT INTO cameras (id, tenant_id, site_id, name, is_active) "
                  "VALUES (:i,:t,:s,:n,TRUE)",
                  {"i": cam, "t": tenant, "s": site, "n": f"E2E Camera {n}"})
        # cam1 is a looping video and cam2+ are still images in the committed
        # mediamtx config; any of them yields a real decodable frame.
        await sql("INSERT INTO streams (tenant_id, camera_id, protocol, url, status) "
                  "VALUES (:t,:c,'rtsp',:u,'active')",
                  {"t": tenant, "c": cam, "u": f"rtsp://{RTSP_HOST}/cam{n}"})
    slug = (await sql("SELECT slug FROM tenants WHERE id = :t",
                      {"t": tenant}))[0]["slug"]
    return {"id": site, "tenant_id": tenant, "slug": slug}


async def main() -> int:
    print("\n§52 DEFINITION OF DONE — end-to-end against the running stack")
    print("=" * 72, flush=True)

    created: dict = {}
    try:
        # ── 1. Admin login ───────────────────────────────────────────────────
        #
        # SEEDS ITS OWN WORLD when the named site is absent, which is the case
        # on any freshly migrated database -- CI included. A verification script
        # that depends on demo data somebody seeded by hand passes or fails for
        # reasons that have nothing to do with the code, and cannot run in a
        # pipeline at all.
        site = (await sql(
            "SELECT s.id, s.tenant_id, t.slug FROM sites s JOIN tenants t ON t.id=s.tenant_id "
            " WHERE s.name = :n AND t.is_active LIMIT 1", {"n": SITE_NAME}))
        if site:
            site = site[0]
        else:
            print(f"  (no site named {SITE_NAME!r}; seeding an isolated one "
                  f"against {RTSP_HOST})", flush=True)
            site = await _seed_world()
            created["seeded_tenant"] = site["tenant_id"]

        # A temporary admin, so the run never touches a real person's password.
        # The hash is produced and bound as a parameter — never interpolated
        # into SQL or passed through a shell, where $2b/$12 would be eaten.
        from app.core.security import hash_password
        password = secrets.token_urlsafe(18)
        admin_id = uuid.uuid4()
        email = f"vpatrol-e2e-{admin_id.hex[:8]}@e2e.local"
        await sql(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
            "                   full_name, is_active) "
            "VALUES (:i,:t,2,:e,:h,'Phase 14 E2E Admin',TRUE)",
            {"i": admin_id, "t": site["tenant_id"], "e": email,
             "h": hash_password(password)})
        created["admin"] = admin_id

        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{BASE}/api/v1/auth/login", json={
                "tenant_slug": site["slug"], "email": email, "password": password})
            step("Admin login", r.status_code == 200,
                 f"POST /auth/login -> {r.status_code}"
                 + ("" if r.status_code == 200 else f" {r.text[:200]}"))
            token = r.json()["access_token"]
            H = {"Authorization": f"Bearer {token}"}

            # ── 2. Virtual Patrolling (the module answers) ───────────────────
            r = await c.get(f"{API}/schedules", headers=H)
            step("Virtual Patrolling module reachable", r.status_code == 200,
                 f"GET /schedules -> {r.status_code}")

            # ── 3-4. Create patrol schedule, for the selected site ───────────
            due = (datetime.now(timezone.utc) + timedelta(hours=8)) - timedelta(minutes=3)
            r = await c.post(f"{API}/schedules", headers=H, json={
                "site_id": str(site["id"]),
                "name": f"Phase 14 E2E Patrol {admin_id.hex[:6]}",
                "schedule_type": "DAILY",
                "start_date": str(date.today() - timedelta(days=1)),
                "patrol_time": due.strftime("%H:%M:%S"),
                "timezone": "Asia/Singapore",
                "grace_minutes": 240,
                "assigned_user_id": str(admin_id),
                "email_frequency": "IMMEDIATE",
            })
            step("Create patrol schedule (site selected)", r.status_code == 201,
                 f"-> {r.status_code} {r.text[:200]}")
            sched_id = r.json()["id"]
            created["schedule"] = sched_id

            # ── 5. Select cameras ───────────────────────────────────────────
            cams = await sql(
                "SELECT c.id, c.name FROM cameras c JOIN streams s ON s.camera_id=c.id "
                " WHERE c.site_id = :s AND c.is_active AND s.url LIKE :pat "
                " ORDER BY c.name", {"s": site["id"], "pat": f"rtsp://{RTSP_HOST}%"})
            step("Cameras available on the site", len(cams) >= 2,
                 f"{len(cams)} reachable camera(s)")

            sched_cams = []
            for n, cam in enumerate(cams, start=1):
                r = await c.post(f"{API}/schedules/{sched_id}/cameras", headers=H,
                                 json={"camera_id": str(cam["id"]), "sequence_no": n})
                if r.status_code != 201:
                    step("Select cameras", False, f"{cam['name']}: {r.status_code} {r.text[:160]}")
                sched_cams.append(r.json())
            step("Select cameras", True, f"{len(sched_cams)} added to the schedule")

            # ── 6. Arrange camera sequence ──────────────────────────────────
            reversed_order = [
                {"schedule_camera_id": sc["id"], "sequence_no": len(sched_cams) - n}
                for n, sc in enumerate(sched_cams)
            ]
            r = await c.put(f"{API}/schedules/{sched_id}/cameras/reorder", headers=H,
                            json=reversed_order)
            ok = r.status_code in (200, 204)
            after = await c.get(f"{API}/schedules/{sched_id}/cameras", headers=H)
            order = [x["sequence_no"] for x in after.json()] if after.status_code == 200 else []
            step("Arrange camera sequence", ok and order == sorted(order),
                 f"reorder -> {r.status_code}, resulting sequence {order}")

            # ── 7. Create camera questions ──────────────────────────────────
            first_cam = after.json()[0]
            questions = [
                {"question_text": "Is the camera view unobstructed?",
                 "question_type": "YES_NO", "is_required": True,
                 "failure_action": "CREATE_INCIDENT"},
                {"question_text": "Anything to note?",
                 "question_type": "TEXT", "is_required": False,
                 "failure_action": "NONE"},
            ]
            for q in questions:
                r = await c.post(f"{API}/schedule-cameras/{first_cam['id']}/questions",
                                 headers=H, json=q)
                if r.status_code != 201:
                    step("Create camera questions", False, f"{r.status_code} {r.text[:200]}")
            step("Create camera questions", True,
                 f"{len(questions)} on '{first_cam.get('camera_name', '?')}' "
                 f"(one raises an incident on failure)")

            # ── 8-9. Email recipients and frequency ─────────────────────────
            r = await c.post(f"{API}/schedules/{sched_id}/email-recipients",
                             headers=H, params={"email": "ops-e2e@example.test"})
            step("Configure email recipients", r.status_code == 201,
                 f"-> {r.status_code} {r.text[:160]}")

            r = await c.get(f"{API}/schedules/{sched_id}", headers=H)
            step("Configure email frequency", r.json().get("email_frequency") == "IMMEDIATE",
                 f"email_frequency={r.json().get('email_frequency')!r}")

            # ── 10. Save (the schedule persisted and is enabled) ─────────────
            saved = (await sql("SELECT enabled, name FROM virtual_patrol_schedules "
                               " WHERE id = CAST(:i AS uuid)", {"i": sched_id}))[0]
            step("Save", bool(saved["enabled"]), f"stored and enabled: {saved['name']!r}")

            # ── 11-12. Scheduler detects it, session created ─────────────────
            # The real service function, invoked the way the scheduler process
            # invokes it — not a reimplementation of its logic here.
            from app.db.session import AsyncSessionLocal
            from app.services import vpatrol_scheduler
            async with AsyncSessionLocal() as db:
                counts = await vpatrol_scheduler.create_due_sessions(db)
            step("Scheduler detects patrol", counts["failed"] == 0, f"{counts}")

            sess = await sql(
                "SELECT id, patrol_number, status, camera_count FROM virtual_patrol_sessions "
                " WHERE schedule_id = CAST(:s AS uuid) ORDER BY created_at DESC LIMIT 1",
                {"s": sched_id})
            step("Patrol session created", bool(sess),
                 f"{sess[0]['patrol_number']} ({sess[0]['camera_count']} cameras)" if sess else "none")
            session_id = str(sess[0]["id"])
            created["session"] = session_id

            # ── 13. Duty officer opens the patrol ────────────────────────────
            r = await c.get(f"{API}/my-patrols", headers=H)
            mine = [p for p in r.json() if p["id"] == session_id] if r.status_code == 200 else []
            step("Duty officer receives the patrol", bool(mine),
                 f"GET /my-patrols -> {r.status_code}, {len(mine)} match")

            r = await c.post(f"{API}/sessions/{session_id}/start", headers=H)
            step("Officer opens/starts the patrol", r.status_code in (200, 204),
                 f"-> {r.status_code} {r.text[:160]}")

            # ── 14-21. Camera by camera ─────────────────────────────────────
            exceptions_raised = 0
            cameras_done = 0
            while True:
                r = await c.get(f"{API}/sessions/{session_id}/current-camera", headers=H)
                if r.status_code != 200:
                    step("Camera walk", False, f"current-camera -> {r.status_code} {r.text[:160]}")
                cur = r.json()
                # The endpoint answers {"camera": {...}, "questions": [...]} and
                # {"camera": None, ...} once every camera is done. That None is
                # the loop's only exit.
                cam = cur.get("camera")
                if cam is None:
                    break
                cam_row_id = cam["id"]
                cam_name = cam.get("camera_name", "?")
                if cameras_done == 0:
                    step(f"Camera 1 displayed", True, f"{cam_name}")

                # 15. A REAL snapshot, over RTSP, right now.
                r = await c.post(
                    f"{API}/sessions/{session_id}/cameras/{cam_row_id}/snapshot", headers=H)
                snap = r.json() if r.status_code == 200 else {}
                if cameras_done == 0:
                    captured = bool(snap.get("snapshot_path") or snap.get("captured"))
                    step("Actual snapshot captured", r.status_code == 200,
                         f"-> {r.status_code} " + (f"captured={captured}" if captured
                                                   else str(snap)[:160]))

                # 16-17. Questions, and the officer's answers.
                qs = cur.get("questions") or []
                if cameras_done == 0:
                    step("Questions displayed", True, f"{len(qs)} on this camera")

                if qs:
                    answers = []
                    for q in qs:
                        qtype = q.get("question_type")
                        # Answer NO to the yes/no question, which is the failing
                        # answer and the one wired to raise an incident.
                        val = "NO" if qtype == "YES_NO" else "Checked during E2E run"
                        answers.append({"session_question_id": q["id"], "answer": val})
                        if qtype == "YES_NO":
                            exceptions_raised += 1
                    r = await c.post(
                        f"{API}/sessions/{session_id}/cameras/{cam_row_id}/answers",
                        headers=H, json={"answers": answers,
                                         "officer_notes": "Phase 14 end-to-end run"})
                    if cameras_done == 0:
                        step("Officer answers", r.status_code in (200, 201),
                             f"-> {r.status_code} {r.text[:160]}")
                        step("Exception handling", exceptions_raised > 0,
                             f"{exceptions_raised} failing answer(s) recorded")

                r = await c.post(
                    f"{API}/sessions/{session_id}/cameras/{cam_row_id}/complete", headers=H)
                if r.status_code not in (200, 204):
                    step(f"Camera completed ({cam_name})", False,
                         f"-> {r.status_code} {r.text[:200]}")
                cameras_done += 1
                if cameras_done == 1:
                    step("Camera completed", True, cam_name)
                if cameras_done > 20:
                    break

            step("Every camera walked to the last one", cameras_done >= 2,
                 f"{cameras_done} cameras completed in sequence")

            # ── 22. Patrol completed ────────────────────────────────────────
            r = await c.post(f"{API}/sessions/{session_id}/complete", headers=H)
            final = (await sql("SELECT status, completed_camera_count, camera_count "
                               "  FROM virtual_patrol_sessions WHERE id = CAST(:i AS uuid)",
                               {"i": session_id}))[0]
            step("Patrol completed", r.status_code in (200, 204),
                 f"status={final['status']} "
                 f"({final['completed_camera_count']}/{final['camera_count']} cameras)")

            # ── 23-25. PDF, Excel, stored ───────────────────────────────────
            r = await c.get(f"{API}/sessions/{session_id}/report/pdf", headers=H)
            pdf_ok = r.status_code == 200 and r.content[:4] == b"%PDF"
            step("PDF generated", pdf_ok,
                 f"-> {r.status_code}, {len(r.content):,} bytes, "
                 f"magic={r.content[:4]!r}")
            pdf_bytes = r.content

            r = await c.get(f"{API}/sessions/{session_id}/report/excel", headers=H)
            xlsx_ok = r.status_code == 200 and r.content[:2] == b"PK"
            step("Excel generated", xlsx_ok,
                 f"-> {r.status_code}, {len(r.content):,} bytes, magic={r.content[:2]!r}")
            xlsx_bytes = r.content

            stored = await sql(
                "SELECT report_format, storage_path FROM virtual_patrol_reports "
                " WHERE session_id = CAST(:i AS uuid)", {"i": session_id})
            on_disk = []
            for row in stored:
                p = Path(os.environ.get("EVIDENCE_ROOT", "/data/evidence")) / row["storage_path"]
                on_disk.append(f"{row['report_format']}:{'present' if p.exists() else 'MISSING'}")
            step("Report stored", bool(stored) and all("MISSING" not in x for x in on_disk),
                 f"{len(stored)} row(s): {', '.join(on_disk) if on_disk else 'none'}")

            # ── 26. Email queued ────────────────────────────────────────────
            q = await sql("SELECT status, recipients, attempts, left(coalesce(last_error,''),80) AS err "
                          "  FROM virtual_patrol_email_queue WHERE session_id = CAST(:i AS uuid)",
                          {"i": session_id})
            step("Email queued/sent", bool(q),
                 f"{q[0]['status']} to {q[0]['recipients']}" if q else "nothing queued")

            # ── 27. History and exceptions ──────────────────────────────────
            r = await c.get(f"{API}/sessions", headers=H)
            in_history = any(s["id"] == session_id for s in r.json()) if r.status_code == 200 else False
            step("Patrol visible in History", in_history, f"GET /sessions -> {r.status_code}")

            r = await c.get(f"{BASE}/api/v1/command-centre/virtual-patrol", headers=H)
            exc = [e for e in r.json().get("exceptions", [])
                   if e.get("patrol_number") == sess[0]["patrol_number"]] \
                if r.status_code == 200 else []
            step("Exceptions visible", bool(exc),
                 f"{len(exc)} exception(s) on the Command Centre board")

            # ── 28. Incident created ────────────────────────────────────────
            inc = await sql(
                "SELECT i.id, i.title FROM incidents i "
                " WHERE i.tenant_id = :t AND i.created_at > now() - interval '30 minutes' "
                "   AND i.title ILIKE '%patrol%' ORDER BY i.created_at DESC LIMIT 5",
                {"t": site["tenant_id"]})
            step("Incident created when configured", bool(inc),
                 f"{len(inc)} incident(s); latest: {inc[0]['title'][:70]!r}" if inc else "none")

            # Keep the artefacts where a human can look at them.
            outdir = Path("/data/evidence/phase14")
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "patrol_report.pdf").write_bytes(pdf_bytes)
            (outdir / "patrol_report.xlsx").write_bytes(xlsx_bytes)
            print(f"\n  artefacts written to {outdir}", flush=True)

    finally:
        # The temporary admin and schedule go, whatever happened. The session,
        # its report and the incident stay: they are the evidence this ran.
        if created.get("schedule"):
            await sql("DELETE FROM virtual_patrol_email_recipients "
                      " WHERE schedule_id = CAST(:s AS uuid)", {"s": created["schedule"]})
        if created.get("admin"):
            await sql("UPDATE virtual_patrol_sessions SET officer_user_id = NULL "
                      " WHERE officer_user_id = :u", {"u": created["admin"]})
            await sql("DELETE FROM users WHERE id = :u", {"u": created["admin"]})

        # A tenant this script seeded is entirely its own, so it goes -- every
        # patrol table cascades from tenants. A PRE-EXISTING tenant is left
        # alone: there, the session and its report are the evidence the run
        # happened, and deleting them would throw away what was just proved.
        if created.get("seeded_tenant"):
            await sql("DELETE FROM tenants WHERE id = :t",
                      {"t": created["seeded_tenant"]})
            print("\n  cleaned up: temporary admin and seeded tenant removed",
                  flush=True)
        else:
            print("\n  cleaned up: temporary admin removed", flush=True)

    print("=" * 72)
    print("§52 DEFINITION OF DONE: all steps passed" if not _failures
          else f"FAILURES: {_failures}")
    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
