"""Seed demo data for the Demo Security Org tenant.
Run inside the api container:
  docker exec docker-api-1 python /app/scripts/seed_demo.py
"""
import os, uuid, hashlib, datetime, random, sys
import bcrypt
import psycopg

DB_DSN = "host=postgres port=5432 dbname=seventh_ai_vision user=postgres password=change_me_dev_only"

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

DEMO_TENANT_ID = "f7ef97a5-e5e3-4c7a-94e6-b9ad275c8521"

def uid():
    return str(uuid.uuid4())

def now(delta_hours=0):
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=delta_hours)).isoformat()

def rand_confidence():
    return round(random.uniform(0.75, 0.99), 4)

with psycopg.connect(DB_DSN, autocommit=False) as conn:
    with conn.cursor() as cur:

        # ── Wipe existing demo data so script is idempotent ──────────────────
        cur.execute("SET session_replication_role = replica;")  # disable FK triggers
        for tbl in [
            "notification_logs", "notification_rules", "notification_channels",
            "audit_logs", "incident_notes", "incidents", "alerts",
            "lpr_events", "face_events", "intrusion_events",
            "ppe_events", "fire_smoke_events", "weapon_events",
            "crowd_events", "behavior_events",
            "tampering_events", "abandoned_object_events", "fall_events",
            "evidence", "detections",
            "face_watchlist_entries", "watchlist_entries",
            "crowd_zones", "restricted_zones",
            "streams", "cameras",
            "tenant_settings", "refresh_tokens", "users",
        ]:
            try:
                cur.execute(f"DELETE FROM {tbl} WHERE tenant_id = %s", (DEMO_TENANT_ID,))
            except Exception:
                conn.rollback()
                cur.execute("SET session_replication_role = replica;")
        cur.execute("SET session_replication_role = DEFAULT;")

        # ── Users ─────────────────────────────────────────────────────────────
        admin_id = uid()
        operator_id = uid()
        viewer_id = uid()

        cur.execute("""
            INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name, is_active)
            VALUES
              (%s, %s, 2, 'admin@demo.com',    %s, 'Demo Admin',    TRUE),
              (%s, %s, 4, 'operator@demo.com', %s, 'Demo Operator', TRUE),
              (%s, %s, 6, 'viewer@demo.com',   %s, 'Demo Viewer',   TRUE)
        """, (
            admin_id,    DEMO_TENANT_ID, hash_password("admin123"),
            operator_id, DEMO_TENANT_ID, hash_password("operator123"),
            viewer_id,   DEMO_TENANT_ID, hash_password("viewer123"),
        ))
        print("✓ Users created")

        # ── Tenant settings ───────────────────────────────────────────────────
        settings = [
            ("lpr.confidence_threshold",      0.55),
            ("face.match_threshold",           0.6),
            ("intrusion.breach_cooldown_seconds", 60),
            ("evidence.retention_days",        90),
        ]
        for key, val in settings:
            cur.execute("""
                INSERT INTO tenant_settings (id, tenant_id, setting_key, setting_value, updated_by_user_id)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, setting_key) DO NOTHING
            """, (uid(), DEMO_TENANT_ID, key, str(val), admin_id))
        print("✓ Tenant settings created")

        # ── Cameras ──────────────────────────────────────────────────────────
        cam_ids = []
        cameras = [
            ("Main Entrance",    "Block A - Ground Floor",  ["lpr", "face"]),
            ("Parking Lot A",    "Parking Level 1",          ["lpr", "intrusion"]),
            ("Server Room",      "IT Wing - Level 2",        ["intrusion", "face"]),
            ("Loading Bay",      "Warehouse - Rear",         ["lpr", "ppe", "intrusion"]),
            ("Lobby",            "Main Building - Level 1",  ["face", "crowd", "behavior"]),
            ("Emergency Exit B", "Block B - Ground Floor",   ["intrusion"]),
            ("CCTV Control Room", "Security Office - Level 1", ["tampering"]),
            ("Atrium",            "Main Building - Level 2",   ["abandoned", "fall"]),
        ]
        for name, loc, modules in cameras:
            cid = uid()
            cam_ids.append(cid)
            cur.execute("""
                INSERT INTO cameras (id, tenant_id, name, location, ai_modules_enabled, is_active)
                VALUES (%s, %s, %s, %s, %s::jsonb, TRUE)
            """, (cid, DEMO_TENANT_ID, name, loc, str(modules).replace("'", '"')))

        # ── Streams ──────────────────────────────────────────────────────────
        statuses = ["online", "online", "online", "online", "degraded", "offline"]
        for cid, status in zip(cam_ids, statuses):
            cur.execute("""
                INSERT INTO streams (id, tenant_id, camera_id, protocol, url, status, last_frame_at)
                VALUES (%s, %s, %s, 'rtsp', %s, %s, %s)
            """, (uid(), DEMO_TENANT_ID, cid,
                  f"rtsp://192.168.1.{random.randint(10,99)}/stream1",
                  status,
                  now(-random.randint(0, 12)) if status != "offline" else None))
        print(f"✓ {len(cam_ids)} cameras + streams created")

        # ── Watchlist entries (plates) ────────────────────────────────────────
        plates = [
            ("SGA1234X", "block", "Stolen vehicle - reported 2026-05-10"),
            ("SGK5678Y", "block", "Suspected criminal – case #SG2026-0042"),
            ("SGB9988Z", "block", "Unregistered commercial vehicle"),
            ("SGC1111A", "allow", "CEO – priority access"),
            ("SGD2222B", "allow", "VIP Visitor – permanent pass"),
        ]
        for plate, ltype, reason in plates:
            cur.execute("""
                INSERT INTO watchlist_entries
                  (id, tenant_id, plate_number, list_type, reason, is_active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
            """, (uid(), DEMO_TENANT_ID, plate, ltype, reason))
        print("✓ Plate watchlist created")

        # ── Face watchlist entries ────────────────────────────────────────────
        import struct
        def fake_embedding():
            vec = [random.gauss(0, 1) for _ in range(512)]
            norm = sum(x*x for x in vec) ** 0.5
            return [x/norm for x in vec]

        faces = [
            ("John Doe – Blacklisted", "block"),
            ("Jane Smith – Blacklisted", "block"),
            ("CEO Mr. Lee – VIP", "allow"),
            ("CFO Ms. Tan – VIP", "allow"),
        ]
        face_ids = []
        for name, ltype in faces:
            fid = uid()
            face_ids.append(fid)
            emb = fake_embedding()
            emb_float4 = "{" + ",".join(str(round(v, 6)) for v in emb) + "}"
            emb_vector = "[" + ",".join(str(round(v, 6)) for v in emb) + "]"
            cur.execute("""
                INSERT INTO face_watchlist_entries
                  (id, tenant_id, person_name, embedding, embedding_v, list_type, is_active)
                VALUES (%s, %s, %s, %s::float4[], %s::vector, %s, TRUE)
            """, (fid, DEMO_TENANT_ID, name, emb_float4, emb_vector, ltype))
        print("✓ Face watchlist created")

        # ── Restricted zones ──────────────────────────────────────────────────
        zone_ids = []
        zones = [
            (cam_ids[2], "Server Room Perimeter", "critical",
             [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.9, "y": 0.9}, {"x": 0.1, "y": 0.9}]),
            (cam_ids[3], "Loading Bay Restricted Area", "high",
             [{"x": 0.0, "y": 0.5}, {"x": 0.5, "y": 0.5}, {"x": 0.5, "y": 1.0}, {"x": 0.0, "y": 1.0}]),
            (cam_ids[5], "Emergency Exit Zone", "medium",
             [{"x": 0.3, "y": 0.3}, {"x": 0.7, "y": 0.3}, {"x": 0.7, "y": 0.7}, {"x": 0.3, "y": 0.7}]),
        ]
        for cid, name, sev, poly in zones:
            import json
            zid = uid()
            zone_ids.append(zid)
            cur.execute("""
                INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon, severity, is_active)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, TRUE)
            """, (zid, DEMO_TENANT_ID, cid, name, json.dumps(poly), sev))
        print("✓ Restricted zones created")

        # ── Detections + module events + alerts + incidents ───────────────────
        alert_count = 0
        incident_count = 0

        def insert_detection(cur, cam_id, module, confidence, bbox, meta, detected_at):
            did = uid()
            cur.execute("""
                INSERT INTO detections
                  (id, tenant_id, camera_id, module_type, confidence, bounding_box, raw_metadata, detected_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            """, (did, DEMO_TENANT_ID, cam_id, module, confidence,
                  json.dumps(bbox), json.dumps(meta), detected_at))
            return did

        def insert_alert(cur, det_id, cam_id, module, severity, code, params, title, msg, status, h_ago):
            aid = uid()
            ts = now(-h_ago)
            cur.execute("""
                INSERT INTO alerts
                  (id, tenant_id, detection_id, camera_id, module_type, severity,
                   alert_code, message_params, title, message, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            """, (aid, DEMO_TENANT_ID, det_id, cam_id, module, severity,
                  code, json.dumps(params), title, msg, status, ts))
            return aid

        def insert_incident(cur, alert_id, cam_id, title, desc, code, params, severity, status, is_auto, h_ago):
            iid = uid()
            ts = now(-h_ago)
            cur.execute("""
                INSERT INTO incidents
                  (id, tenant_id, alert_id, camera_id, title, description,
                   alert_code, message_params, severity, status, is_auto_created, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            """, (iid, DEMO_TENANT_ID, alert_id, cam_id, title, desc,
                  code, json.dumps(params), severity, status, is_auto, ts))
            return iid

        # LPR events — blocklist hits
        blocklist_plates = [
            ("SGA1234X", cam_ids[0], 2, "open"),
            ("SGK5678Y", cam_ids[1], 5, "acknowledged"),
            ("SGB9988Z", cam_ids[1], 10, "resolved"),
            ("SGA1234X", cam_ids[1], 24, "resolved"),
        ]
        for plate, cam, h_ago, status in blocklist_plates:
            ts = now(-h_ago)
            det_bbox = {"x1": 0.2, "y1": 0.3, "x2": 0.5, "y2": 0.45}
            conf = rand_confidence()
            did = insert_detection(cur, cam, "lpr", conf, det_bbox,
                                   {"model_version": "yolov8s-lpr-2026.06", "plate": plate}, ts)
            cur.execute("""
                INSERT INTO lpr_events
                  (detection_id, detected_at, tenant_id, camera_id, plate_number,
                   plate_confidence, direction, watchlist_match)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (did, ts, DEMO_TENANT_ID, cam, plate, conf,
                  random.choice(["entry", "exit"]), "block"))
            aid = insert_alert(cur, did, cam, "lpr", "critical", "lpr.blocklist_hit",
                               {"plate": plate, "confidence": conf},
                               f"Blocklist vehicle detected: {plate}",
                               f"Vehicle {plate} matched the blocklist. Immediate action required.",
                               status, h_ago)
            inc_status = "resolved" if status == "resolved" else ("open" if status == "open" else "open")
            insert_incident(cur, aid, cam,
                            f"Blocklist Vehicle: {plate}",
                            f"Vehicle {plate} was detected at camera. Confidence: {conf:.0%}.",
                            "lpr.blocklist_hit", {"plate": plate},
                            "critical", inc_status, True, h_ago)
            alert_count += 1; incident_count += 1

        # LPR events — allowlist (low alert, no incident)
        for plate, cam in [("SGC1111A", cam_ids[0]), ("SGD2222B", cam_ids[0])]:
            ts = now(-random.randint(1, 8))
            conf = rand_confidence()
            did = insert_detection(cur, cam, "lpr", conf,
                                   {"x1": 0.2, "y1": 0.3, "x2": 0.5, "y2": 0.45},
                                   {"model_version": "yolov8s-lpr-2026.06", "plate": plate}, ts)
            cur.execute("""
                INSERT INTO lpr_events
                  (detection_id, detected_at, tenant_id, camera_id, plate_number,
                   plate_confidence, watchlist_match)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (did, ts, DEMO_TENANT_ID, cam, plate, conf, "allow"))
            insert_alert(cur, did, cam, "lpr", "low", "lpr.allowlist_entry",
                         {"plate": plate},
                         f"Allowlist vehicle entered: {plate}", "", "resolved",
                         random.randint(1, 8))
            alert_count += 1

        # Face events — blocklist
        face_data = [
            (face_ids[0], "block", "high", cam_ids[0], 3, "open"),
            (face_ids[1], "block", "high", cam_ids[4], 6, "acknowledged"),
        ]
        for fid, ltype, sev, cam, h_ago, status in face_data:
            ts = now(-h_ago)
            emb = fake_embedding()
            emb_str = "{" + ",".join(str(round(v, 6)) for v in emb) + "}"
            conf = rand_confidence()
            did = insert_detection(cur, cam, "face", conf,
                                   {"x1": 0.35, "y1": 0.1, "x2": 0.65, "y2": 0.5},
                                   {"model_version": "insightface-buffalo_l"}, ts)
            cur.execute("""
                INSERT INTO face_events
                  (detection_id, detected_at, tenant_id, camera_id, embedding,
                   matched_watchlist_id, match_confidence, watchlist_match)
                VALUES (%s, %s, %s, %s, %s::float4[], %s, %s, %s)
            """, (did, ts, DEMO_TENANT_ID, cam, emb_str, fid, conf, ltype))
            aid = insert_alert(cur, did, cam, "face", sev, "face.blocklist_match",
                               {"confidence": conf},
                               "Blocklist face matched",
                               f"A blacklisted individual was detected. Confidence: {conf:.0%}.",
                               status, h_ago)
            insert_incident(cur, aid, cam,
                            "Blacklisted Person Detected",
                            f"Face matched blacklist entry. Confidence: {conf:.0%}.",
                            "face.blocklist_match", {"confidence": conf},
                            sev, "open", True, h_ago)
            alert_count += 1; incident_count += 1

        # Face events — unrecognized (info alerts)
        for i in range(4):
            cam = random.choice(cam_ids[:3])
            h_ago = random.randint(0, 24)
            ts = now(-h_ago)
            emb = fake_embedding()
            emb_str = "{" + ",".join(str(round(v, 6)) for v in emb) + "}"
            conf = rand_confidence()
            did = insert_detection(cur, cam, "face", conf,
                                   {"x1": 0.3, "y1": 0.1, "x2": 0.6, "y2": 0.5},
                                   {"model_version": "insightface-buffalo_l"}, ts)
            cur.execute("""
                INSERT INTO face_events
                  (detection_id, detected_at, tenant_id, camera_id, embedding,
                   matched_watchlist_id, match_confidence, watchlist_match)
                VALUES (%s, %s, %s, %s, %s::float4[], NULL, NULL, NULL)
            """, (did, ts, DEMO_TENANT_ID, cam, emb_str))
            insert_alert(cur, did, cam, "face", "info", "face.unrecognized",
                         {}, "Unknown face detected",
                         "An unrecognized person was detected in a monitored area.",
                         "open" if h_ago < 6 else "resolved", h_ago)
            alert_count += 1

        # Intrusion events
        intrusion_data = [
            (zone_ids[0], cam_ids[2], "critical", 1, "open"),
            (zone_ids[0], cam_ids[2], "critical", 4, "acknowledged"),
            (zone_ids[1], cam_ids[3], "high", 2, "open"),
            (zone_ids[2], cam_ids[5], "medium", 12, "resolved"),
        ]
        for zid, cam, sev, h_ago, status in intrusion_data:
            ts = now(-h_ago)
            bbox = {"x1": 0.2, "y1": 0.3, "x2": 0.4, "y2": 0.9}
            conf = rand_confidence()
            did = insert_detection(cur, cam, "intrusion", conf, bbox,
                                   {"model_version": "yolov8s-person-2026.06"}, ts)
            cur.execute("""
                INSERT INTO intrusion_events
                  (detection_id, detected_at, tenant_id, camera_id, zone_id,
                   person_bbox, dwell_time_seconds)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """, (did, ts, DEMO_TENANT_ID, cam, zid,
                  json.dumps(bbox), round(random.uniform(2, 45), 1)))
            aid = insert_alert(cur, did, cam, "intrusion", sev, "intrusion.zone_breach",
                               {"zone_id": zid},
                               "Unauthorized zone breach detected",
                               "A person has entered a restricted zone without authorization.",
                               status, h_ago)
            if sev in ("high", "critical"):
                insert_incident(cur, aid, cam,
                                "Restricted Zone Breach",
                                f"Unauthorized access to restricted area detected.",
                                "intrusion.zone_breach", {"zone_id": zid},
                                sev, "open" if status != "resolved" else "resolved", True, h_ago)
                incident_count += 1
            alert_count += 1

        # Tampering events — CCTV Control Room (cam_ids[6])
        tampering_data = [
            ("blocked",   0.88, "Camera lens partially blocked by object", 1, "open"),
            ("moved",     0.91, "Camera orientation shifted from baseline", 4, "acknowledged"),
            ("defocused", 0.76, "Camera image abnormally blurred",         8, "resolved"),
        ]
        for ttype, score, reason, h_ago, status in tampering_data:
            ts = now(-h_ago)
            conf = round(score + random.uniform(-0.03, 0.03), 4)
            did = insert_detection(cur, cam_ids[6], "tampering", conf,
                                   {"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0},
                                   {"model_version": "tampering-detector-2026.06"}, ts)
            cur.execute("""
                INSERT INTO tampering_events
                  (detection_id, detected_at, tenant_id, camera_id,
                   tampering_type, score, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (did, ts, DEMO_TENANT_ID, cam_ids[6], ttype, score, reason))
            aid = insert_alert(cur, did, cam_ids[6], "tampering", "critical",
                               "tampering.detected",
                               {"tampering_type": ttype, "score": score},
                               f"Camera tampering detected: {ttype}",
                               f"Camera has been {ttype}. Immediate inspection required. Score: {score:.2f}.",
                               status, h_ago)
            if status != "resolved":
                insert_incident(cur, aid, cam_ids[6],
                                f"Camera Tampering: {ttype.title()}",
                                f"Security camera appears to have been {ttype}. Score: {score:.2f}.",
                                "tampering.detected", {"tampering_type": ttype},
                                "critical", "open", True, h_ago)
                incident_count += 1
            alert_count += 1

        # Abandoned object events — Atrium (cam_ids[7])
        abandoned_data = [
            ("backpack", 45.0, 3, "open"),
            ("suitcase", 92.5, 7, "acknowledged"),
            ("bag",      31.0, 18, "resolved"),
        ]
        for obj_class, dwell, h_ago, status in abandoned_data:
            ts = now(-h_ago)
            conf = rand_confidence()
            bbox = {"x1": round(random.uniform(0.1, 0.4), 2),
                    "y1": round(random.uniform(0.3, 0.6), 2),
                    "x2": round(random.uniform(0.5, 0.8), 2),
                    "y2": round(random.uniform(0.7, 0.9), 2)}
            did = insert_detection(cur, cam_ids[7], "abandoned", conf, bbox,
                                   {"model_version": "abandoned-detector-2026.06"}, ts)
            cur.execute("""
                INSERT INTO abandoned_object_events
                  (detection_id, detected_at, tenant_id, camera_id,
                   object_class, dwell_seconds, bbox)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (did, ts, DEMO_TENANT_ID, cam_ids[7], obj_class, dwell, json.dumps(bbox)))
            sev = "high" if dwell > 60 else "medium"
            aid = insert_alert(cur, did, cam_ids[7], "abandoned", sev,
                               "abandoned.object_detected",
                               {"object_class": obj_class, "dwell_seconds": dwell},
                               f"Abandoned {obj_class} detected",
                               f"An unattended {obj_class} has been stationary for {dwell:.0f}s.",
                               status, h_ago)
            if sev == "high":
                insert_incident(cur, aid, cam_ids[7],
                                f"Unattended Object: {obj_class.title()}",
                                f"An unattended {obj_class} left for {dwell:.0f}s — potential security threat.",
                                "abandoned.object_detected", {"object_class": obj_class},
                                sev, "open" if status != "resolved" else "resolved", True, h_ago)
                incident_count += 1
            alert_count += 1

        # Fall/slip events — Atrium (cam_ids[7])
        fall_data = [
            (0.93, 2, "open"),
            (0.81, 11, "resolved"),
        ]
        for fall_conf, h_ago, status in fall_data:
            ts = now(-h_ago)
            bbox = {"x1": 0.2, "y1": 0.4, "x2": 0.6, "y2": 0.95}
            did = insert_detection(cur, cam_ids[7], "fall", fall_conf, bbox,
                                   {"model_version": "fall-detector-2026.06"}, ts)
            cur.execute("""
                INSERT INTO fall_events
                  (detection_id, detected_at, tenant_id, camera_id,
                   fall_confidence, pose_keypoints, person_bbox)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """, (did, ts, DEMO_TENANT_ID, cam_ids[7], fall_conf,
                  json.dumps({"hip": [0.4, 0.7], "shoulder": [0.4, 0.5]}),
                  json.dumps(bbox)))
            aid = insert_alert(cur, did, cam_ids[7], "fall", "high",
                               "fall.person_detected",
                               {"confidence": fall_conf},
                               "Person fall/slip detected",
                               f"A person appears to have fallen. Confidence: {fall_conf:.0%}. Medical attention may be required.",
                               status, h_ago)
            insert_incident(cur, aid, cam_ids[7],
                            "Person Fall Detected",
                            f"Fall detection confidence: {fall_conf:.0%}. Verify and dispatch assistance if required.",
                            "fall.person_detected", {"confidence": fall_conf},
                            "high", "open" if status != "resolved" else "resolved", True, h_ago)
            alert_count += 1; incident_count += 1

        # ── Notification channel (demo) ───────────────────────────────────────
        try:
            cur.execute("""
                INSERT INTO notification_channels
                  (id, tenant_id, name, channel_type, config, is_active)
                VALUES (%s, %s, 'Demo Email Alerts', 'email', '{"to": ["security@demo.com"]}'::jsonb, TRUE)
                ON CONFLICT DO NOTHING
            """, (uid(), DEMO_TENANT_ID))
        except Exception:
            conn.rollback()

        conn.commit()
        print(f"✓ Alerts: {alert_count}")
        print(f"✓ Incidents: {incident_count}")
        print("\n Demo data seeding complete!")
        print("\n Login credentials:")
        print("   Tenant slug : demo")
        print("   Admin       : admin@demo.com / admin123")
        print("   Operator    : operator@demo.com / operator123")
        print("   Viewer      : viewer@demo.com / viewer123")
