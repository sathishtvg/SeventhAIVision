"""Demo seed — populates a realistic 'Aegis Security' tenant for client demos.

Run inside the api container:
    docker exec -w /app/backend docker-api-1 \
        python -m scripts.seed_demo

Idempotent: deletes any existing tenant with slug 'aegis' (cascades) then
recreates everything. Uses the postgres superuser connection to bypass RLS.

Partition-safety: alerts + incidents are unpartitioned, so their timestamps
span 21 days for rich trends. Partitioned tables (detections, intrusion_events,
evidence, occurrence_book_entries, checkpoint data) are dated within the
current month so their pg_partman partitions always exist.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password

random.seed(7)

_host = os.environ.get("SEED_DB_HOST", "postgres")
ADMIN_URL = f"postgresql+asyncpg://postgres:change_me_dev_only@{_host}:5432/seventh_ai_vision"

DEMO_PASSWORD = "Demo1234!"
SLUG = "aegis"

AI_MODULES = ["lpr", "face", "intrusion", "ppe", "crowd",
              "fire_smoke", "weapon", "behavior", "tampering", "abandoned", "fall"]

ALERT_TITLES = {
    "intrusion":  ("Intrusion detected in restricted zone", "intrusion.zone_breach", "critical"),
    "lpr":        ("Blocklisted vehicle plate detected", "lpr.blocklist_hit", "high"),
    "face":       ("Unrecognized person detected", "face.unrecognized", "medium"),
    "ppe":        ("Worker without hard hat", "ppe.missing", "medium"),
    "fire_smoke": ("Smoke detected", "fire_smoke.smoke", "critical"),
    "weapon":     ("Possible weapon detected", "weapon.firearm", "critical"),
    "crowd":      ("Crowd density threshold exceeded", "crowd.overcrowd", "high"),
    "tampering":  ("Camera tampering detected", "tampering.blocked", "high"),
    "abandoned":  ("Abandoned object detected", "abandoned.object", "medium"),
    "fall":       ("Person fall detected", "fall.detected", "high"),
}


async def seed():
    engine = create_async_engine(ADMIN_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        # Reset ---------------------------------------------------------------
        await s.execute(text("DELETE FROM tenants WHERE slug = :slug"), {"slug": SLUG})
        await s.commit()

        tenant_id = uuid.uuid4()
        branding = {"primary_color": "#1E88E5", "company_name": "Aegis Security Services",
                    "logo_url": ""}
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, branding, timezone) "
                 "VALUES (:id, :name, :slug, CAST(:b AS jsonb), 'Asia/Singapore')"),
            {"id": tenant_id, "name": "Aegis Security Services", "slug": SLUG,
             "b": json.dumps(branding)},
        )

        pw = hash_password(DEMO_PASSWORD)

        def mk_user(email, role, name):
            uid = uuid.uuid4()
            return uid, {"id": uid, "tid": tenant_id, "role": role, "email": email,
                         "pw": pw, "name": name}

        users = {
            "super":  mk_user("super@aegis.demo", 1, "System Administrator"),
            "ops":    mk_user("ops@aegis.demo", 2, "Priya Nair — Ops Manager"),
            "sup":    mk_user("supervisor@aegis.demo", 3, "David Lim — Supervisor"),
            "guard1": mk_user("guard1@aegis.demo", 5, "Tan Wei Ming"),
            "guard2": mk_user("guard2@aegis.demo", 5, "Rajesh Kumar"),
            "viewer": mk_user("viewer@aegis.demo", 6, "Control Room Viewer"),
            "client": mk_user("client@marinabay.demo", 7, "Marina Bay Property Mgr"),
        }
        for _uid, row in users.values():
            await s.execute(
                text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                     "VALUES (:id, :tid, :role, :email, :pw, :name)"),
                row,
            )
        ops_id = users["ops"][0]
        g1 = users["guard1"][0]
        g2 = users["guard2"][0]
        client_id = users["client"][0]

        # Module licenses (all enabled) --------------------------------------
        for m in AI_MODULES:
            await s.execute(
                text("INSERT INTO tenant_module_licenses (tenant_id, module_type, is_enabled) "
                     "VALUES (:tid, :m, TRUE)"),
                {"tid": tenant_id, "m": m},
            )

        # Sites ---------------------------------------------------------------
        sites_def = [
            ("Marina Bay Tower", "10 Marina Blvd, Singapore 018983", 1.2820, 103.8549),
            ("Orchard Central Mall", "181 Orchard Rd, Singapore 238896", 1.3009, 103.8398),
            ("Jurong Logistics Hub", "1 Jurong West St, Singapore 649364", 1.3395, 103.7070),
            ("Changi Business Park", "1 Changi Business Park, Singapore 486036", 1.3336, 103.9670),
        ]
        site_ids = {}
        for name, addr, lat, lon in sites_def:
            sid = uuid.uuid4()
            site_ids[name] = sid
            await s.execute(
                text("INSERT INTO sites (id, tenant_id, name, address, latitude, longitude) "
                     "VALUES (:id, :tid, :name, :addr, :lat, :lon)"),
                {"id": sid, "tid": tenant_id, "name": name, "addr": addr, "lat": lat, "lon": lon},
            )

        # user_sites (scoping) ------------------------------------------------
        assigns = [
            (g1, "Marina Bay Tower"), (g1, "Orchard Central Mall"),
            (g2, "Jurong Logistics Hub"), (g2, "Changi Business Park"),
            (client_id, "Marina Bay Tower"),
        ]
        for uid, sname in assigns:
            await s.execute(
                text("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u, :s, :t)"),
                {"u": uid, "s": site_ids[sname], "t": tenant_id},
            )

        # Cameras + streams ---------------------------------------------------
        cam_defs = [
            ("Marina Bay Tower", "Lobby Entrance", ["face", "intrusion"]),
            ("Marina Bay Tower", "Car Park Ramp", ["lpr", "intrusion"]),
            ("Marina Bay Tower", "Loading Bay", ["intrusion", "abandoned"]),
            ("Orchard Central Mall", "Main Atrium", ["crowd", "face"]),
            ("Orchard Central Mall", "Service Corridor", ["intrusion", "ppe"]),
            ("Jurong Logistics Hub", "Gate 1 (Vehicles)", ["lpr", "intrusion"]),
            ("Jurong Logistics Hub", "Warehouse Floor", ["ppe", "fall", "fire_smoke"]),
            ("Changi Business Park", "Perimeter East", ["intrusion", "weapon"]),
            ("Changi Business Park", "Reception", ["face", "abandoned"]),
        ]
        cameras = []  # (cam_id, site_name, name, modules, stream_id, cam_index)
        for i, (sname, cname, modules) in enumerate(cam_defs, start=1):
            cam_id = uuid.uuid4()
            stream_id = uuid.uuid4()
            await s.execute(
                text("INSERT INTO cameras (id, tenant_id, site_id, name, location, "
                     "ai_modules_enabled, is_active) "
                     "VALUES (:id, :tid, :sid, :name, :loc, CAST(:mods AS jsonb), TRUE)"),
                {"id": cam_id, "tid": tenant_id, "sid": site_ids[sname], "name": cname,
                 "loc": sname, "mods": json.dumps(modules)},
            )
            await s.execute(
                text("INSERT INTO streams (id, tenant_id, camera_id, protocol, url, status, "
                     "continuous_recording) "
                     "VALUES (:id, :tid, :cid, 'rtsp', :url, 'online', :cr)"),
                {"id": stream_id, "tid": tenant_id, "cid": cam_id,
                 "url": f"rtsp://mediamtx:8554/cam{i}", "cr": i <= 3},
            )
            cameras.append((cam_id, sname, cname, modules, stream_id, i))

        # Restricted zones (normalized polygons) ------------------------------
        zone_cams = [c for c in cameras if "intrusion" in c[3]][:4]
        for cam_id, sname, cname, _m, _sid, _i in zone_cams:
            await s.execute(
                text("INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon, "
                     "severity, is_active) VALUES (:id, :tid, :cid, :name, CAST(:poly AS jsonb), "
                     ":sev, TRUE)"),
                {"id": uuid.uuid4(), "tid": tenant_id, "cid": cam_id,
                 "name": f"{cname} — Restricted Area",
                 "poly": json.dumps([{"x": 0.15, "y": 0.35}, {"x": 0.85, "y": 0.35},
                                     {"x": 0.85, "y": 0.92}, {"x": 0.15, "y": 0.92}]),
                 "sev": random.choice(["high", "critical"])},
            )

        # Roster: patterns + shifts (one ACTIVE now) --------------------------
        await s.execute(
            text("INSERT INTO shift_patterns (tenant_id, site_id, guard_user_id, label, "
                 "days_of_week, start_time, duration_minutes) "
                 "VALUES (:tid, :sid, :gid, 'Day Shift', :dow, '08:00', 720)"),
            {"tid": tenant_id, "sid": site_ids["Marina Bay Tower"], "gid": g1,
             "dow": [0, 1, 2, 3, 4]},
        )
        await s.execute(
            text("INSERT INTO shift_patterns (tenant_id, site_id, guard_user_id, label, "
                 "days_of_week, start_time, duration_minutes) "
                 "VALUES (:tid, :sid, :gid, 'Night Shift', :dow, '20:00', 720)"),
            {"tid": tenant_id, "sid": site_ids["Jurong Logistics Hub"], "gid": g2,
             "dow": [0, 1, 2, 3, 4, 5, 6]},
        )
        # An active shift for guard1 at Marina Bay (started 2h ago) — so the
        # Command Centre shows a guard on duty and alert routing has a target.
        active_shift = uuid.uuid4()
        await s.execute(
            text("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, "
                 "scheduled_end, actual_start, status, created_by_user_id) VALUES "
                 "(:id, :tid, :sid, :gid, now() - interval '2 hours', now() + interval '6 hours', "
                 "now() - interval '2 hours', 'active', :ops)"),
            {"id": active_shift, "tid": tenant_id, "sid": site_ids["Marina Bay Tower"],
             "gid": g1, "ops": ops_id},
        )
        # A few upcoming scheduled shifts
        for d in range(1, 4):
            await s.execute(
                text("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, "
                     "scheduled_end, status, created_by_user_id) VALUES "
                     "(:id, :tid, :sid, :gid, (CURRENT_DATE + (:d)::int) + time '08:00', "
                     "(CURRENT_DATE + (:d)::int) + time '20:00', 'scheduled', :ops)"),
                {"id": uuid.uuid4(), "tid": tenant_id, "sid": site_ids["Marina Bay Tower"],
                 "gid": g1, "d": d, "ops": ops_id},
            )

        # Post orders ---------------------------------------------------------
        po_defs = [
            ("Marina Bay Tower", "Access Control Procedure", "access",
             "1. All visitors must sign in at reception and present photo ID.\n"
             "2. Verify contractor work permits before granting access.\n"
             "3. Escort all non-tenant visitors above level 3."),
            ("Marina Bay Tower", "Emergency Contacts", "contacts",
             "Building Manager: +65 6100 1234\nFire Command Centre: 995\n"
             "Aegis Ops Room: +65 6200 5678\nPolice: 999"),
            ("Jurong Logistics Hub", "Vehicle Gate Procedure", "patrol",
             "1. Log every vehicle plate at Gate 1.\n2. Blocklisted plates: deny entry, "
             "notify supervisor immediately.\n3. Patrol the perimeter every 2 hours."),
            ("Changi Business Park", "Perimeter Patrol Requirements", "patrol",
             "Scan all 4 perimeter checkpoints each patrol round. Report any fence "
             "damage or lighting faults in the Occurrence Book."),
        ]
        for sname, title, cat, body in po_defs:
            await s.execute(
                text("INSERT INTO post_orders (tenant_id, site_id, title, body, category, "
                     "created_by_user_id) VALUES (:tid, :sid, :title, :body, :cat, :ops)"),
                {"tid": tenant_id, "sid": site_ids[sname], "title": title, "body": body,
                 "cat": cat, "ops": ops_id},
            )

        # Patrol route + checkpoints + a completed session --------------------
        route_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO patrol_routes (id, tenant_id, site_id, name, is_active) "
                 "VALUES (:id, :tid, :sid, 'Marina Bay Night Round', TRUE)"),
            {"id": route_id, "tid": tenant_id, "sid": site_ids["Marina Bay Tower"]},
        )
        cp_ids = []
        for seq, cpname in enumerate(["Lobby", "Level 3 Lift", "Car Park B2", "Rooftop Plant"], 1):
            cp = uuid.uuid4()
            cp_ids.append(cp)
            await s.execute(
                text("INSERT INTO patrol_checkpoints (id, tenant_id, route_id, sequence, name, qr_code) "
                     "VALUES (:id, :tid, :rid, :seq, :name, :qr)"),
                {"id": cp, "tid": tenant_id, "rid": route_id, "seq": seq, "name": cpname,
                 "qr": f"AEGIS-MB-CP{seq}"},
            )
        sess_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO patrol_sessions (id, tenant_id, route_id, guard_user_id, shift_id, "
                 "status, started_at, completed_at, total_checkpoints, scanned_checkpoints) VALUES "
                 "(:id, :tid, :rid, :gid, :sh, 'completed', now() - interval '3 hours', "
                 "now() - interval '2 hours 20 minutes', 4, 4)"),
            {"id": sess_id, "tid": tenant_id, "rid": route_id, "gid": g1, "sh": active_shift},
        )
        for k, cp in enumerate(cp_ids):
            await s.execute(
                text("INSERT INTO checkpoint_scans (id, tenant_id, session_id, checkpoint_id, "
                     "scan_method, scanned_at, guard_user_id, verified) VALUES "
                     "(:id, :tid, :sess, :cp, 'qr', now() - interval '3 hours' + ((:k)::int * interval '12 minutes'), "
                     ":gid, TRUE)"),
                {"id": uuid.uuid4(), "tid": tenant_id, "sess": sess_id, "cp": cp, "k": k, "gid": g1},
            )

        await s.commit()

        # ── Historical analytics data ────────────────────────────────────────
        # Alerts (unpartitioned) — 21-day spread, varied severity/module/status.
        alert_ids = []
        for _ in range(55):
            cam = random.choice(cameras)
            module = random.choice(cam[3])
            title, code, base_sev = ALERT_TITLES.get(module, ("Event detected", "generic", "medium"))
            sev = base_sev if random.random() < 0.7 else random.choice(["low", "medium", "high", "critical"])
            days_ago = random.random() * 21
            status = random.choice(["open", "acknowledged", "resolved", "resolved", "acknowledged"])
            # Keep some recent critical/high for the Command Centre 4h window
            aid = uuid.uuid4()
            alert_ids.append((aid, cam, module))
            await s.execute(
                text("INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, "
                     "alert_code, title, status, created_at, acknowledged_by_user_id, acknowledged_at) "
                     "VALUES (:id, :tid, :cid, :mod, :sev, :code, :title, :status, "
                     "now() - ((:days)::float8 * interval '1 day'), :ackby, :ackat)"),
                {"id": aid, "tid": tenant_id, "cid": cam[0], "mod": module, "sev": sev,
                 "code": code, "title": title, "status": status, "days": days_ago,
                 "ackby": g1 if status != "open" else None,
                 "ackat": None},
            )
        # A couple of fresh critical alerts (last 90 min) for the live wall / CC
        for cam in [c for c in cameras if "intrusion" in c[3]][:2]:
            aid = uuid.uuid4()
            alert_ids.append((aid, cam, "intrusion"))
            await s.execute(
                text("INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, "
                     "alert_code, title, status, created_at) VALUES "
                     "(:id, :tid, :cid, 'intrusion', 'critical', 'intrusion.zone_breach', "
                     "'Intrusion detected in restricted zone', 'open', now() - interval '25 minutes')"),
                {"id": aid, "tid": tenant_id, "cid": cam[0]},
            )

        # Incidents (unpartitioned) — mix open/resolved over 21 days.
        for _ in range(14):
            cam = random.choice(cameras)
            days_ago = random.random() * 21
            resolved = random.random() < 0.6
            sev = random.choice(["medium", "high", "high", "critical"])
            await s.execute(
                text("INSERT INTO incidents (id, tenant_id, camera_id, title, description, severity, "
                     "status, is_auto_created, created_at, resolved_at, assigned_to_user_id) VALUES "
                     "(:id, :tid, :cid, :title, :desc, :sev, :status, :auto, "
                     "now() - ((:days)::float8 * interval '1 day'), :res, :assignee)"),
                {"id": uuid.uuid4(), "tid": tenant_id, "cid": cam[0],
                 "title": f"Security incident — {cam[2]}",
                 "desc": "Auto-created from a critical alert; guard dispatched and situation contained.",
                 "sev": sev, "status": "resolved" if resolved else random.choice(["open", "investigating"]),
                 "auto": True, "days": days_ago,
                 "res": (datetime.now(timezone.utc) - timedelta(days=days_ago) + timedelta(hours=random.randint(1, 8))) if resolved else None,
                 "assignee": random.choice([g1, g2])},
            )

        # Detections + intrusion_events (partitioned) — current month only.
        for _ in range(130):
            cam = random.choice(cameras)
            module = random.choice(cam[3])
            det_id = uuid.uuid4()
            fw, fh = 1920, 1080
            x1, y1 = random.randint(100, 1400), random.randint(80, 700)
            bbox = {"x1": x1, "y1": y1, "x2": x1 + random.randint(120, 380),
                    "y2": y1 + random.randint(200, 360)}
            meta = {"model_version": "yolov8s-2026.06", "frame_width": fw, "frame_height": fh}
            # random moment between start of month and now
            row = (await s.execute(
                text("SELECT (date_trunc('month', now()) + random() * (now() - date_trunc('month', now()))) AS ts")
            )).first()
            ts = row.ts
            await s.execute(
                text("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, "
                     "bounding_box, raw_metadata, detected_at) VALUES "
                     "(:id, :tid, :cid, :mod, :conf, CAST(:bbox AS jsonb), CAST(:meta AS jsonb), :ts)"),
                {"id": det_id, "tid": tenant_id, "cid": cam[0], "mod": module,
                 "conf": round(random.uniform(0.62, 0.97), 4),
                 "bbox": json.dumps(bbox), "meta": json.dumps(meta), "ts": ts},
            )

        # Occurrence book entries (clamp to current month) --------------------
        ob_types = ["general", "patrol_start", "patrol_end", "incident", "handover",
                    "visitor_arrival", "equipment_check"]
        ob_bodies = [
            "Commenced perimeter patrol round 1. All clear.",
            "Visitor signed in at reception — contractor for level 12 aircon works.",
            "Completed patrol; noted flickering light at car park B2, logged for maintenance.",
            "Shift handover to night team. No outstanding issues.",
            "Responded to intrusion alert at loading bay — false alarm, stray cat.",
            "Fire extinguisher check on level 3 — all within date.",
        ]
        for _ in range(16):
            sname = random.choice(list(site_ids.keys()))
            await s.execute(
                text("INSERT INTO occurrence_book_entries (id, tenant_id, site_id, author_user_id, "
                     "entry_type, body, occurred_at) VALUES (:id, :tid, :sid, :uid, :et, :body, "
                     "date_trunc('month', now()) + random() * (now() - date_trunc('month', now())))"),
                {"id": uuid.uuid4(), "tid": tenant_id, "sid": site_ids[sname], "uid": random.choice([g1, g2]),
                 "et": random.choice(ob_types), "body": random.choice(ob_bodies)},
            )

        await s.commit()

    await engine.dispose()
    print("✅ Demo seed complete.")
    print(f"   Tenant slug : {SLUG}")
    print(f"   Password    : {DEMO_PASSWORD} (all users)")
    print("   Logins      : ops@aegis.demo (admin) · supervisor@aegis.demo ·")
    print("                 guard1@aegis.demo · viewer@aegis.demo · client@marinabay.demo")
    print("   4 sites · 9 cameras · zones · roster (1 active shift) · post orders ·")
    print("   patrol history · ~57 alerts · 14 incidents · 130 detections · occurrence book")


if __name__ == "__main__":
    asyncio.run(seed())
