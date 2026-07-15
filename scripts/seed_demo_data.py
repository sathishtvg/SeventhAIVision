"""
Demo data seed for Seventh AI Vision — run inside the API container.
Inserts realistic security operations data for the 'demo' tenant.
All passwords are Demo@1234 (except existing admin which stays as-is).
"""
import asyncio, uuid, json, random, math
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from app.db.session import AsyncSessionLocal
from app.core.security import hash_password

TENANT_ID = "061922a5-4c8c-4ac7-a47d-c5494c5b51a5"
ADMIN_ID  = "0e716d13-9050-438a-9ebc-f8187b4c10a3"
DEMO_PASS = hash_password("Demo@1234")

def uid(): return str(uuid.uuid4())
def now_utc(): return datetime.now(timezone.utc)
def ago(**kw): return now_utc() - timedelta(**kw)
def ahead(**kw): return now_utc() + timedelta(**kw)

def norm_vec(dim=512):
    v = [random.gauss(0,1) for _ in range(dim)]
    mag = math.sqrt(sum(x*x for x in v))
    return [round(x/mag, 6) for x in v]

# ─────────────────────────────────────────────────────────────────
async def seed():
    async with AsyncSessionLocal() as db:
        t = TENANT_ID
        await db.execute(text("SELECT set_config('app.current_tenant',:t,true)"), {"t": t})

        print("── Sites ─────────────────────────────────────")
        sites = [
            (uid(), "HQ Building",    "10 Toa Payoh Industrial Park, Singapore 319061", 1.3343, 103.8481),
            (uid(), "Warehouse Alpha", "23 Gul Drive, Jurong Industrial Estate, Singapore 629480", 1.3207, 103.6710),
            (uid(), "Car Park Bravo",  "Basement 1, 1 Raffles Place, Singapore 048616", 1.2841, 103.8516),
        ]
        for sid, name, addr, lat, lon in sites:
            await db.execute(text("""
                INSERT INTO sites(id,tenant_id,name,address,latitude,longitude,is_active,created_at,updated_at)
                VALUES(:id,:t,:n,:a,:lat,:lon,true,now(),now())
                ON CONFLICT DO NOTHING
            """), {"id":sid,"t":t,"n":name,"a":addr,"lat":lat,"lon":lon})
        print(f"  {len(sites)} sites inserted")

        print("── Users ─────────────────────────────────────")
        SUPERVISOR_ID  = uid()
        OPERATOR1_ID   = uid()
        OPERATOR2_ID   = uid()
        GUARD1_ID      = uid()
        GUARD2_ID      = uid()
        CLIENT_VIEW_ID = uid()
        new_users = [
            (SUPERVISOR_ID,  3, "supervisor@seventh.ai",   "Rajan Mehta"),
            (OPERATOR1_ID,   4, "operator1@seventh.ai",    "Priya Chandran"),
            (OPERATOR2_ID,   4, "operator2@seventh.ai",    "Hafiz Ismail"),
            (GUARD1_ID,      5, "guard1@seventh.ai",       "Muthu Kumar"),
            (GUARD2_ID,      5, "guard2@seventh.ai",       "Siti Rahimah"),
            (CLIENT_VIEW_ID, 6, "client.view@seventh.ai",  "Client Viewer"),
        ]
        for uid_, role, email, name in new_users:
            await db.execute(text("""
                INSERT INTO users(id,tenant_id,role_id,email,hashed_password,full_name,is_active,created_at,updated_at)
                VALUES(:id,:t,:r,:e,:p,:n,true,now(),now())
                ON CONFLICT(tenant_id,email) DO NOTHING
            """), {"id":uid_,"t":t,"r":role,"e":email,"p":DEMO_PASS,"n":name})
        print(f"  {len(new_users)} users inserted (password: Demo@1234)")

        print("── Cameras ───────────────────────────────────")
        site_hq, site_wh, site_pk = sites[0][0], sites[1][0], sites[2][0]
        AI_ALL  = json.dumps(["lpr","face","intrusion","ppe","crowd"])
        AI_LPR  = json.dumps(["lpr","intrusion"])
        AI_FACE = json.dumps(["face","intrusion","ppe"])
        cameras = [
            (uid(), site_hq, "CAM-HQ-01", "Main Entrance",      AI_ALL,  1.3344, 103.8482, "online"),
            (uid(), site_hq, "CAM-HQ-02", "Lobby East",         AI_FACE, 1.3343, 103.8483, "online"),
            (uid(), site_hq, "CAM-HQ-03", "Server Room B2",     AI_FACE, 1.3342, 103.8480, "online"),
            (uid(), site_hq, "CAM-HQ-04", "Car Park Entrance",  AI_LPR,  1.3340, 103.8479, "online"),
            (uid(), site_wh, "CAM-WH-01", "Loading Bay 1",      AI_ALL,  1.3208, 103.6711, "online"),
            (uid(), site_wh, "CAM-WH-02", "Loading Bay 2",      AI_LPR,  1.3209, 103.6712, "online"),
            (uid(), site_wh, "CAM-WH-03", "Cold Storage Area",  AI_FACE, 1.3206, 103.6709, "degraded"),
            (uid(), site_pk, "CAM-PK-01", "Entry Gate",         AI_LPR,  1.2842, 103.8517, "online"),
            (uid(), site_pk, "CAM-PK-02", "Level B1 North",     AI_ALL,  1.2840, 103.8515, "online"),
            (uid(), site_pk, "CAM-PK-03", "Level B2 South",     AI_LPR,  1.2839, 103.8514, "offline"),
        ]
        cam_ids = [c[0] for c in cameras]
        for cid, sid, name, loc, ai_mods, lat, lon, status in cameras:
            await db.execute(text("""
                INSERT INTO cameras(id,tenant_id,site_id,name,location,latitude,longitude,ai_modules_enabled,is_active,created_at,updated_at)
                VALUES(:id,:t,:s,:n,:l,:lat,:lon,:ai,true,:cr,:cr)
                ON CONFLICT DO NOTHING
            """), {"id":cid,"t":t,"s":sid,"n":name,"l":loc,"lat":lat,"lon":lon,
                   "ai":ai_mods,"cr":ago(days=30)})
        print(f"  {len(cameras)} cameras inserted")

        print("── Streams ───────────────────────────────────")
        stream_ids = []
        rtsp_base = "rtsp://admin:Cam@1234@192.168.10.{}/stream1"
        for i,(cid,sid,name,loc,_,lat,lon,status) in enumerate(cameras):
            strid = uid()
            stream_ids.append(strid)
            await db.execute(text("""
                INSERT INTO streams(id,tenant_id,camera_id,protocol,url,status,auth_config,last_frame_at,created_at,updated_at)
                VALUES(:id,:t,:c,'rtsp',:u,:st,:ac,now()-INTERVAL '2 minutes',now(),now())
                ON CONFLICT DO NOTHING
            """), {"id":strid,"t":t,"c":cid,"u":rtsp_base.format(100+i),
                   "st":status,"ac":json.dumps({"username":"admin","password":"Cam@1234"})})
        print(f"  {len(stream_ids)} streams inserted")

        print("── Restricted Zones ──────────────────────────")
        zone_ids = []
        zone_defs = [
            (cam_ids[0], "Main Gate Exclusion",  "high",    [[0.1,0.1],[0.9,0.1],[0.9,0.4],[0.1,0.4]]),
            (cam_ids[1], "Receptionist Desk",    "medium",  [[0.3,0.2],[0.7,0.2],[0.7,0.6],[0.3,0.6]]),
            (cam_ids[2], "Server Room Access",   "critical",[[0.0,0.0],[1.0,0.0],[1.0,0.5],[0.0,0.5]]),
            (cam_ids[3], "Barrier Zone",         "high",    [[0.2,0.0],[0.8,0.0],[0.8,0.3],[0.2,0.3]]),
            (cam_ids[4], "Loading Dock Inner",   "high",    [[0.0,0.6],[0.5,0.6],[0.5,1.0],[0.0,1.0]]),
            (cam_ids[5], "Vehicle Check Point",  "medium",  [[0.1,0.3],[0.9,0.3],[0.9,0.7],[0.1,0.7]]),
            (cam_ids[6], "Cold Storage Entry",   "critical",[[0.3,0.0],[0.7,0.0],[0.7,0.4],[0.3,0.4]]),
            (cam_ids[8], "B1 Restricted Bay",    "high",    [[0.6,0.0],[1.0,0.0],[1.0,0.8],[0.6,0.8]]),
        ]
        for cid, zname, sev, poly in zone_defs:
            zid = uid()
            zone_ids.append(zid)
            await db.execute(text("""
                INSERT INTO restricted_zones(id,tenant_id,camera_id,name,polygon,severity,is_active,created_at,updated_at)
                VALUES(:id,:t,:c,:n,:p,:s,true,now(),now()) ON CONFLICT DO NOTHING
            """), {"id":zid,"t":t,"c":cid,"n":zname,
                   "p":json.dumps([{"x":pt[0],"y":pt[1]} for pt in poly]),"s":sev})
        print(f"  {len(zone_defs)} zones inserted")

        print("── Watchlist (Plates) ────────────────────────")
        plates_block = [
            ("SGA1234B","Suspected vehicle — robbery case ref SGP-2026-001"),
            ("PHM8812X","Flagged: unpaid compound notices × 14"),
            ("SBF4491T","Stolen vehicle report LOC-2026-0551"),
        ]
        plates_allow = [
            ("SGP8889A","MD vehicle — VIP access all hours"),
            ("SCE2200K","Security contractor weekly maintenance"),
            ("SGA7750P","Board member permanent pass"),
        ]
        for plate, reason in plates_block:
            await db.execute(text("""
                INSERT INTO watchlist_entries(id,tenant_id,plate_number,list_type,reason,added_by_user_id,is_active,created_at,updated_at)
                VALUES(:id,:t,:p,'block',:r,:u,true,now(),now()) ON CONFLICT DO NOTHING
            """), {"id":uid(),"t":t,"p":plate,"r":reason,"u":ADMIN_ID})
        for plate, reason in plates_allow:
            await db.execute(text("""
                INSERT INTO watchlist_entries(id,tenant_id,plate_number,list_type,reason,added_by_user_id,is_active,created_at,updated_at)
                VALUES(:id,:t,:p,'allow',:r,:u,true,now(),now()) ON CONFLICT DO NOTHING
            """), {"id":uid(),"t":t,"p":plate,"r":reason,"u":ADMIN_ID})
        print(f"  {len(plates_block)} blocked + {len(plates_allow)} allowed plates")

        print("── Face Watchlist ────────────────────────────")
        face_defs = [
            ("Ahmad Zarif (Wanted)",   "block", "Wanted for trespass — court order SGP-C-2026-1122"),
            ("Lim Wei Jie (Blacklist)","block", "Terminated employee — access revoked 2026-05-01"),
            ("Tan CEO",                "allow",  "CEO — VIP unrestricted access"),
            ("Sarah CFO",              "allow",  "CFO — VIP unrestricted access"),
            ("Vendor: IT Contractor",  "allow",  "Approved vendor — Mon–Fri 09:00–18:00"),
        ]
        for name, ltype, reason in face_defs:
            emb = norm_vec(512)
            emb_str = "[" + ",".join(str(x) for x in emb) + "]"
            await db.execute(text("""
                INSERT INTO face_watchlist_entries(id,tenant_id,person_name,embedding,embedding_v,list_type,is_active,created_at,updated_at)
                VALUES(:id,:t,:n,:e,CAST(:ev AS vector),:l,true,now(),now()) ON CONFLICT DO NOTHING
            """), {"id":uid(),"t":t,"n":name,"e":emb,"ev":emb_str,"l":ltype})
        print(f"  {len(face_defs)} face entries inserted")

        print("── Module Licenses ───────────────────────────")
        modules = ["lpr","face","intrusion","ppe","crowd","fire_smoke","weapon","behavior",
                   "tampering","fall","abandoned"]
        for mod in modules:
            await db.execute(text("""
                INSERT INTO tenant_module_licenses(id,tenant_id,module_type,is_enabled,licensed_by_user_id,notes,created_at,updated_at)
                VALUES(:id,:t,:m,true,:u,'Demo tenant — all modules enabled',now(),now())
                ON CONFLICT(tenant_id,module_type) DO UPDATE SET is_enabled=true
            """), {"id":uid(),"t":t,"m":mod,"u":ADMIN_ID})
        print(f"  {len(modules)} module licenses enabled")

        print("── Tenant Settings ───────────────────────────")
        settings = [
            ("lpr.confidence_threshold",      0.60),
            ("face.match_threshold",           0.65),
            ("intrusion.breach_cooldown_seconds", 45),
            ("evidence.retention_days",        180),
            ("ppe.confidence_threshold",       0.55),
            ("crowd.alert_threshold_ratio",    0.80),
            ("fire_smoke.confidence_threshold",0.70),
            ("weapon.confidence_threshold",    0.75),
            ("behavior.loitering_dwell_seconds", 120),
            ("2fa.required",                   False),
            ("2fa.grace_hours",                48),
        ]
        for key, val in settings:
            await db.execute(text("""
                INSERT INTO tenant_settings(id,tenant_id,setting_key,setting_value,updated_by_user_id,updated_at)
                VALUES(:id,:t,:k,CAST(:v AS jsonb),:u,now())
                ON CONFLICT(tenant_id,setting_key) DO UPDATE SET setting_value=CAST(:v AS jsonb),updated_at=now()
            """), {"id":uid(),"t":t,"k":key,"v":json.dumps(val),"u":ADMIN_ID})
        print(f"  {len(settings)} settings configured")

        print("── Notification Channels ─────────────────────")
        notif_channels = [
            (uid(),"Ops Email Alert","email",
             {"recipients":["ops-team@seventh.ai","security-lead@seventh.ai"],"from_name":"7th AI Vision"}),
            (uid(),"Slack Security Ops","webhook",
             {"url":"https://hooks.slack.com/services/DEMO/DEMO/DemoWebhookKey",
              "method":"POST","headers":{"Content-Type":"application/json"},
              "body_template":'{"text":"[ALERT] {{severity}} — {{title}}"}'}),
            (uid(),"SMS On-Call Guard","sms",
             {"to":["+6591234567","+6598765432"],"from":"+6565551234"}),
        ]
        for nid, nname, ntype, cfg in notif_channels:
            await db.execute(text("""
                INSERT INTO notification_channels(id,tenant_id,name,channel_type,config,is_active,created_at,updated_at)
                VALUES(:id,:t,:n,:tp,CAST(:c AS jsonb),true,now(),now()) ON CONFLICT DO NOTHING
            """), {"id":nid,"t":t,"n":nname,"tp":ntype,"c":json.dumps(cfg)})
        print(f"  {len(notif_channels)} notification channels inserted")

        print("── Patrol Routes & Checkpoints ───────────────")
        route_id = uid()
        await db.execute(text("""
            INSERT INTO patrol_routes(id,tenant_id,site_id,name,description,is_active,created_at,updated_at)
            VALUES(:id,:t,:s,'HQ Night Patrol','Full perimeter patrol route for HQ Building',true,now(),now())
            ON CONFLICT DO NOTHING
        """), {"id":route_id,"t":t,"s":site_hq})
        checkpoints = [
            (1,"Main Entrance Gate",      1.3344, 103.8482,"CP-001"),
            (2,"East Wing Corridor",      1.3343, 103.8484,"CP-002"),
            (3,"Server Room B2",          1.3342, 103.8480,"CP-003"),
            (4,"Car Park Level B1",       1.3340, 103.8479,"CP-004"),
            (5,"Roof Access Control",     1.3345, 103.8481,"CP-005"),
        ]
        cp_ids = []
        for seq, cpname, lat, lon, qr in checkpoints:
            cpid = uid()
            cp_ids.append(cpid)
            await db.execute(text("""
                INSERT INTO patrol_checkpoints(id,tenant_id,route_id,sequence,name,latitude,longitude,qr_code,is_active,created_at)
                VALUES(:id,:t,:r,:s,:n,:lat,:lon,:qr,true,now()) ON CONFLICT DO NOTHING
            """), {"id":cpid,"t":t,"r":route_id,"s":seq,"n":cpname,"lat":lat,"lon":lon,"qr":qr})
        print(f"  1 patrol route + {len(checkpoints)} checkpoints inserted")

        print("── Shifts ────────────────────────────────────")
        shift_defs = [
            (GUARD1_ID, site_hq,  ago(hours=8),  ahead(hours=4),  "active"),
            (GUARD2_ID, site_wh,  ago(hours=6),  ahead(hours=6),  "active"),
            (GUARD1_ID, site_hq,  ago(days=1),   ago(hours=20),   "completed"),
            (GUARD2_ID, site_pk,  ago(days=1,hours=4), ago(hours=28), "completed"),
        ]
        for gid, sid, start, end, status in shift_defs:
            await db.execute(text("""
                INSERT INTO shifts(id,tenant_id,site_id,guard_user_id,scheduled_start,scheduled_end,
                actual_start,status,created_by_user_id,created_at,updated_at)
                VALUES(:id,:t,:s,:g,:ss,:se,:ss,:st,:cb,now(),now()) ON CONFLICT DO NOTHING
            """), {"id":uid(),"t":t,"s":sid,"g":gid,"ss":start,"se":end,
                   "st":status,"cb":SUPERVISOR_ID})
        print(f"  {len(shift_defs)} shifts inserted")

        print("── Detections + Events + Alerts + Incidents ──")
        alert_ids = []
        incident_ids = []
        det_count = 0

        det_scenarios = [
            # (module, camera_idx, sub_data, alert?, severity, incident?)
            ("lpr",       3, {"plate":"SGA1234B","conf":0.91,"dir":"entry","type":"sedan","color":"black","match":"block"}, True,  "critical", True),
            ("lpr",       3, {"plate":"SGA7750P","conf":0.87,"dir":"entry","type":"suv",  "color":"silver","match":"allow"}, True, "low", False),
            ("lpr",       7, {"plate":"SBF4491T","conf":0.93,"dir":"exit", "type":"van",  "color":"white", "match":"block"}, True,  "critical", True),
            ("lpr",       7, {"plate":"SCE2200K","conf":0.85,"dir":"entry","type":"truck","color":"blue",  "match":"allow"}, True,  "low",  False),
            ("lpr",       8, {"plate":"PHM8812X","conf":0.89,"dir":"entry","type":"sedan","color":"red",  "match":"block"}, True,  "critical", True),
            ("lpr",       3, {"plate":"SKA3310J","conf":0.78,"dir":"entry","type":"sedan","color":"grey", "match":None},    False, None,  False),
            ("lpr",       7, {"plate":"SBX9901Z","conf":0.82,"dir":"entry","type":"suv",  "color":"black","match":None},    False, None,  False),
            ("face",      0, {"match":"block","score":0.82, "wl":"Ahmad Zarif"},  True,  "high",    True),
            ("face",      1, {"match":"block","score":0.79, "wl":"Lim Wei Jie"}, True,  "high",    True),
            ("face",      2, {"match":"allow","score":0.91, "wl":"Tan CEO"},      True,  "low",     False),
            ("face",      0, {"match":None,   "score":None, "wl":None},           True,  "info",    False),
            ("face",      1, {"match":None,   "score":None, "wl":None},           True,  "info",    False),
            ("intrusion", 2, {"zone":0,"dwell":15.3},                             True,  "critical", True),
            ("intrusion", 4, {"zone":1,"dwell":8.1},                              True,  "high",    True),
            ("intrusion", 6, {"zone":2,"dwell":22.5},                             True,  "critical", True),
            ("intrusion", 8, {"zone":3,"dwell":6.7},                              True,  "high",    True),
            ("ppe",       4, {"violation":"no_helmet","conf":0.82},               True,  "high",    False),
            ("ppe",       4, {"violation":"no_vest",  "conf":0.78},               True,  "medium",  False),
            ("ppe",       5, {"violation":"no_helmet","conf":0.85},               True,  "high",    False),
            ("crowd",     1, {"density":0.91,"count":47},                         True,  "high",    True),
            ("crowd",     0, {"density":0.85,"count":38},                         True,  "medium",  False),
            ("fire_smoke",4, {"type":"smoke","conf":0.73},                        True,  "critical", True),
            ("weapon",    0, {"type":"knife","conf":0.81},                        True,  "critical", True),
            ("behavior",  0, {"type":"loitering","dwell":187},                    True,  "medium",  False),
            ("behavior",  8, {"type":"running",  "conf":0.76},                    True,  "low",     False),
        ]

        alert_templates = {
            "lpr": {
                "block": ("Blocked Vehicle Detected", "lpr.blocklist_hit",
                          "Vehicle {plate} on block list entered at {loc}"),
                "allow": ("Authorised Vehicle Entry", "lpr.allowlist_pass",
                          "Authorised vehicle {plate} recorded at {loc}"),
            },
            "face": {
                "block": ("Blacklisted Person Detected", "face.blacklist_match",
                          "Blacklisted individual ({person}) detected at {loc}"),
                "allow": ("VIP Access Recorded", "face.vip_entry",
                          "VIP {person} entered {loc}"),
                None:    ("Unknown Person Detected", "face.unrecognized",
                          "Unrecognized individual at {loc}"),
            },
            "intrusion": ("Zone Breach Detected", "intrusion.zone_breach",
                          "Person in restricted zone '{zone}' at {loc}"),
            "ppe":        ("PPE Violation Detected", "ppe.violation",
                          "{violation} violation detected at {loc}"),
            "crowd":      ("Crowd Density Alert", "crowd.density_exceeded",
                          "Crowd density {density}% exceeded threshold at {loc}"),
            "fire_smoke": ("Fire/Smoke Detected", "fire_smoke.detected",
                          "{type} detected at {loc} — evacuate immediately"),
            "weapon":     ("Weapon Detected", "weapon.detected",
                          "{type} detected at {loc} — dispatch security"),
            "behavior":   ("Suspicious Behaviour", "behavior.detected",
                          "{type} detected at {loc}"),
        }

        for i, (module, cam_idx, sub, do_alert, sev, do_inc) in enumerate(det_scenarios):
            det_id = uid()
            cam = cameras[cam_idx]
            cid = cam[0]
            loc = cam[3]
            ts = ago(hours=random.randint(1, 72))
            det_ts = ts

            meta = {"model_version": f"yolov8s-{module}-2026.06", "scenario": i}
            await db.execute(text("""
                INSERT INTO detections(id,tenant_id,camera_id,module_type,confidence,raw_metadata,detected_at,created_at)
                VALUES(:id,:t,:c,:m,:conf,CAST(:meta AS jsonb),:ts,:ts) ON CONFLICT DO NOTHING
            """), {"id":det_id,"t":t,"c":cid,"m":module,
                   "conf":round(random.uniform(0.72,0.95),4),
                   "meta":json.dumps(meta),"ts":ts})
            det_count += 1

            # Module-specific event rows
            if module == "lpr":
                plate = sub["plate"]; match = sub.get("match")
                await db.execute(text("""
                    INSERT INTO lpr_events(detection_id,detected_at,tenant_id,camera_id,
                    plate_number,plate_confidence,direction,vehicle_type,vehicle_color,watchlist_match,created_at)
                    VALUES(:did,:ts,:t,:c,:plate,:conf,:dir,:vt,:vc,:wm,:ts) ON CONFLICT DO NOTHING
                """), {"did":det_id,"ts":ts,"t":t,"c":cid,"plate":plate,
                       "conf":sub["conf"],"dir":sub["dir"],"vt":sub["type"],
                       "vc":sub["color"],"wm":match})

            elif module == "intrusion" and zone_ids:
                zone_pick = zone_ids[sub["zone"] % len(zone_ids)]
                bbox = {"x1":0.2,"y1":0.3,"x2":0.5,"y2":0.9}
                await db.execute(text("""
                    INSERT INTO intrusion_events(detection_id,detected_at,tenant_id,camera_id,
                    zone_id,person_bbox,dwell_time_seconds,created_at)
                    VALUES(:did,:ts,:t,:c,:z,CAST(:b AS jsonb),:dw,:ts) ON CONFLICT DO NOTHING
                """), {"did":det_id,"ts":ts,"t":t,"c":cid,"z":zone_pick,
                       "b":json.dumps(bbox),"dw":sub["dwell"]})

            if not do_alert: continue

            # Build alert
            alert_id = uid()
            alert_ids.append(alert_id)
            if module == "lpr":
                key = sub.get("match"); tmpl = alert_templates["lpr"].get(key)
                if not tmpl: continue
                title, code, msg = tmpl
                title = title; msg = msg.format(plate=sub["plate"],loc=loc)
                params = {"plate":sub["plate"],"confidence":sub["conf"],"location":loc}
            elif module == "face":
                key = sub.get("match"); tmpl = alert_templates["face"].get(key)
                if not tmpl: tmpl = alert_templates["face"][None]
                title, code, msg = tmpl
                msg = msg.format(person=sub.get("wl","Unknown"),loc=loc)
                params = {"person":sub.get("wl","Unknown"),"score":sub.get("score"),"location":loc}
            elif module == "intrusion":
                title, code, msg = alert_templates["intrusion"]
                zone_name = zone_defs[sub["zone"] % len(zone_defs)][1] if sub["zone"] < len(zone_defs) else "Unknown Zone"
                msg = msg.format(zone=zone_name, loc=loc)
                params = {"zone":zone_name,"dwell_seconds":sub["dwell"],"location":loc}
            elif module == "ppe":
                title, code, msg = alert_templates["ppe"]
                viol = sub.get("violation","no_ppe").replace("_"," ").title()
                msg = msg.format(violation=viol, loc=loc)
                params = {"violation":viol,"confidence":sub.get("conf"),"location":loc}
            elif module == "crowd":
                title, code, msg = alert_templates["crowd"]
                density = int(sub.get("density",0)*100)
                msg = msg.format(density=density, loc=loc)
                params = {"density_pct":density,"count":sub.get("count"),"location":loc}
            elif module == "fire_smoke":
                title, code, msg = alert_templates["fire_smoke"]
                msg = msg.format(type=sub.get("type","unknown").title(), loc=loc)
                params = {"detection_type":sub.get("type"),"location":loc}
            elif module == "weapon":
                title, code, msg = alert_templates["weapon"]
                msg = msg.format(type=sub.get("type","unknown").title(), loc=loc)
                params = {"weapon_type":sub.get("type"),"location":loc}
            elif module == "behavior":
                title, code, msg = alert_templates["behavior"]
                msg = msg.format(type=sub.get("type","unknown").replace("_"," ").title(), loc=loc)
                params = {"behavior_type":sub.get("type"),"location":loc}
            else:
                continue

            status = random.choice(["open","open","open","acknowledged","resolved"])
            ack_by = OPERATOR1_ID if status in ("acknowledged","resolved") else None
            ack_at = (ago(hours=random.randint(1,4)) if ack_by else None)

            await db.execute(text("""
                INSERT INTO alerts(id,tenant_id,detection_id,camera_id,module_type,severity,
                alert_code,message_params,title,message,status,
                acknowledged_by_user_id,acknowledged_at,created_at)
                VALUES(:id,:t,:did,:c,:m,:sev,:code,CAST(:params AS jsonb),:title,:msg,:st,:ack,:ack_at,:ts)
                ON CONFLICT DO NOTHING
            """), {"id":alert_id,"t":t,"did":det_id,"c":cid,"m":module,"sev":sev,
                   "code":code,"params":json.dumps(params),"title":title,"msg":msg,
                   "st":status,"ack":ack_by,"ack_at":ack_at,"ts":ts})

            if not do_inc: continue

            inc_id = uid()
            incident_ids.append(inc_id)
            inc_status = random.choice(["open","investigating","open","resolved"])
            assigned = random.choice([OPERATOR1_ID, OPERATOR2_ID, SUPERVISOR_ID])
            resolved_at = (ago(hours=random.randint(1,12)) if inc_status=="resolved" else None)
            await db.execute(text("""
                INSERT INTO incidents(id,tenant_id,alert_id,camera_id,title,description,
                alert_code,message_params,severity,status,is_auto_created,
                assigned_to_user_id,resolved_at,created_at,updated_at)
                VALUES(:id,:t,:aid,:c,:title,:desc,:code,CAST(:params AS jsonb),:sev,:st,true,:asn,:res,:ts,:ts)
                ON CONFLICT DO NOTHING
            """), {"id":inc_id,"t":t,"aid":alert_id,"c":cid,
                   "title":f"[AUTO] {title}","desc":msg,
                   "code":code,"params":json.dumps(params),"sev":sev,
                   "st":inc_status,"asn":assigned,"res":resolved_at,"ts":ts})

        print(f"  {det_count} detections | {len(alert_ids)} alerts | {len(incident_ids)} incidents")

        print("── Visitor Logs ──────────────────────────────")
        visitor_scenarios = [
            (site_hq, GUARD1_ID, "check_in",  "VIS-2026-0801", "Delivery: Office supplies — DHL Logistics"),
            (site_hq, GUARD1_ID, "check_out", "VIS-2026-0801", None),
            (site_hq, GUARD2_ID, "check_in",  "VIS-2026-0802", "IT contractor: network upgrade"),
            (site_wh, GUARD2_ID, "check_in",  "VIS-2026-0803", "Warehouse audit — MOM inspector"),
            (site_hq, GUARD1_ID, "check_in",  "VIS-2026-0804", "Client visit: Fong & Partners"),
        ]
        for sid, gid, evtype, badge, notes in visitor_scenarios:
            await db.execute(text("""
                INSERT INTO visitor_logs(id,tenant_id,site_id,guard_user_id,event_type,badge_number,notes,is_unregistered,occurred_at,created_at)
                VALUES(:id,:t,:s,:g,:ev,:b,:n,false,now()-INTERVAL '3 hours',now()) ON CONFLICT DO NOTHING
            """), {"id":uid(),"t":t,"s":sid,"g":gid,"ev":evtype,"b":badge,"n":notes})
        print(f"  {len(visitor_scenarios)} visitor log entries inserted")

        await db.commit()
        print("\n✓ Seed complete.")

asyncio.run(seed())
