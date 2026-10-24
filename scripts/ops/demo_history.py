"""Give the `demo` organisation a believable month of history, up to now.

WHAT IT IS FOR
    demo_showcase.py tells one connected story on the day of a demo. Around it,
    every screen that counts, charts or lists the past needs a past: the roster
    and attendance, guard patrols, the occurrence book, visitors, plate reads at
    Gate 1, AI alerts with their pictures and the incidents some of them opened,
    camera health, and virtual patrols with their reports. This writes the last
    DEMO_DAYS days (30) of that, ending now.

HOW IT WORKS
    SQL, as the database superuser, as the original seeder did: nothing else can
    write a detection without a camera worker, or a shift that ended last week.
    The rows have the shapes the application's own workers and endpoints write
    (titles, codes, parameters, statuses). Virtual patrols are made with the
    application's own functions, and their PDF and Excel reports are written by
    its own report builder.

    It writes the PAST only. Nothing is rostered, scheduled or left open for
    the future, because on a machine nobody works a future shift becomes a
    no-show and a future patrol becomes a missed one. Today's shifts are the
    showcase's.

    Run it again whenever you like — on the day of the demo, before the
    showcase: it fills only the time since it last ran (it remembers by the
    newest detection it wrote), closes its own alerts that have been open for
    more than twelve hours, and completes the virtual patrols the scheduler
    created in the meantime and nobody carried out.

    The pictures are cut from the same sample files the test streams play
    (docker/mediamtx/videos), so an alert's picture is what its camera shows.
    Reception (a photograph of two real people) and the two cameras behind a
    login are given no AI alerts.

RUN IT (from the repository root; the api container must be healthy)

    docker cp docker/mediamtx/videos/. docker-api-1:/tmp/demo-videos
    docker exec -i docker-api-1 python - < scripts/ops/demo_history.py

    DEMO_DRY_RUN=1   do everything inside a transaction and roll it back
    DEMO_DAYS=30     how far back the first run reaches

Then the showcase: DEMO_STEP=setup, today, live (see docs/DEMO-SCRIPT.md).
Never run backend/scripts/seed_demo.py: it deletes the whole organisation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import secrets
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import cv2
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ORG = "demo"
SGT = timezone(timedelta(hours=8))
NOW = datetime.now(timezone.utc).replace(microsecond=0)
END = NOW - timedelta(minutes=2)
DAYS = int(os.environ.get("DEMO_DAYS", "30"))
DRY = os.environ.get("DEMO_DRY_RUN") == "1"
VIDEOS = Path(os.environ.get("DEMO_VIDEOS", "/tmp/demo-videos"))
EVIDENCE_ROOT = Path(os.environ.get("EVIDENCE_ROOT", "/data/evidence"))
MARK = "demo-history"          # raw_metadata.sample on every detection this writes
OPEN_FOR = timedelta(hours=12)  # an alert of ours left open longer than this is closed on the next run

db = psycopg.connect(os.environ["ALEMBIC_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://"),
                     row_factory=dict_row)


def rows(sql: str, *params) -> list[dict]:
    return db.execute(sql, params).fetchall()


def one(sql: str, *params):
    found = rows(sql, *params)
    return found[0] if found else None


def run(sql: str, *params) -> None:
    db.execute(sql, params)


T = one("SELECT id FROM tenants WHERE slug = %s", ORG)["id"]
EMAIL = {"ops": "ops@demo.local", "sup": "supervisor@demo.local", "g1": "guard1@demo.local", "g2": "guard2@demo.local"}
USER = {k: one("SELECT id, full_name FROM users WHERE tenant_id = %s AND email = %s AND is_active", T, e)
        for k, e in EMAIL.items()}
SITE = {r["name"]: r for r in rows("SELECT id, name, latitude, longitude FROM sites WHERE tenant_id = %s", T)}
CAM = {r["name"]: r for r in rows("SELECT id, name, site_id FROM cameras WHERE tenant_id = %s", T)}
HUB, TOWER, CHANGI, ORCHARD = "Jurong Logistics Hub", "Marina Bay Tower", "Changi Business Park", "Orchard Central Mall"
missing = [k for k, v in USER.items() if v is None] + [s for s in (HUB, TOWER, CHANGI, ORCHARD) if s not in SITE]
missing += [c for c in ("Gate 1 (Vehicles)", "Warehouse Floor", "Lobby Entrance", "Loading Bay",
                        "Service Corridor", "Perimeter East") if c not in CAM]
if missing:
    sys.exit(f"The demo organisation is not as expected; missing: {missing}")
DEF = {r["shift_type"]: r for r in rows(
    "SELECT id, shift_type, start_time, duration_minutes FROM shift_definitions WHERE tenant_id = %s AND is_active", T)}

THROUGH = one("SELECT max(detected_at) AS t FROM detections WHERE tenant_id = %s AND raw_metadata->>'sample' = %s",
              T, MARK)["t"]
#: Two hours of overlap with the last run: every kind of record checks for itself before it is written.
START = (THROUGH - timedelta(hours=2)) if THROUGH else (END - timedelta(days=DAYS))
if THROUGH and THROUGH >= END - timedelta(minutes=30):
    sys.exit("Nothing to do: the history already reaches now.")

made: dict[str, int] = {}
files_written: list[Path] = []


def count(what: str, n: int = 1) -> None:
    made[what] = made.get(what, 0) + n


def sgt(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), SGT).astimezone(timezone.utc)


def days_in_window() -> list[date]:
    first = START.astimezone(SGT).date() - timedelta(days=1)
    last = END.astimezone(SGT).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def rng(*key) -> random.Random:
    """Deterministic per day and kind, so a rerun over the same day would make the same choices."""
    return random.Random("|".join(str(k) for k in key))


def inside(moment: datetime) -> bool:
    return START < moment <= END


# ─── Pictures ────────────────────────────────────────────────────────────────

def _jpeg(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("could not encode a frame")
    return buf.tobytes()


def _pictures() -> dict[str, list[bytes]]:
    still = cv2.imread(str(VIDEOS / "bus.jpg"))
    if still is None:
        sys.exit(f"No sample pictures in {VIDEOS}. First: docker cp docker/mediamtx/videos/. docker-api-1:{VIDEOS}")
    walking: list[bytes] = []
    video = cv2.VideoCapture(str(VIDEOS / "vtest.avi"))
    index = 0
    while True:
        ok, frame = video.read()
        if not ok:
            break
        if index % 9 == 0:
            walking.append(_jpeg(frame))
        index += 1
    video.release()
    if not walking:
        sys.exit("vtest.avi gave no frames")
    return {"still": [_jpeg(still)], "walking": walking}


PICTURES = _pictures()
#: Which sample each test stream plays (docker/mediamtx/mediamtx.yml): cam1 the video, cam2-8 the still.
SOURCE = {"Lobby Entrance": "walking", "Loading Bay": "still", "Service Corridor": "still",
          "Gate 1 (Vehicles)": "still", "Warehouse Floor": "still", "Perimeter East": "still"}


def picture(camera: str, moment: datetime) -> bytes:
    pool = PICTURES[SOURCE[camera]]
    return pool[int(moment.timestamp()) // 7 % len(pool)]


def store(rel: str, data: bytes) -> str:
    """Write a file under the evidence root; returns its SHA-256."""
    if not DRY:
        path = EVIDENCE_ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files_written.append(path)
    return hashlib.sha256(data).hexdigest()


# ─── Setup the history needs ─────────────────────────────────────────────────

FENCE = [{"x": 0.10, "y": 0.45}, {"x": 0.90, "y": 0.45}, {"x": 0.90, "y": 0.95}, {"x": 0.10, "y": 0.95}]


def zones() -> dict[str, dict]:
    """Gate 1 and Perimeter East run intrusion detection but had no zone to breach."""
    for camera, name in (("Gate 1 (Vehicles)", "Gate 1 — vehicle lane"), ("Perimeter East", "Perimeter East — fence line")):
        if not one("SELECT 1 FROM restricted_zones WHERE camera_id = %s AND is_active", CAM[camera]["id"]):
            run("INSERT INTO restricted_zones (tenant_id, camera_id, name, polygon, severity) VALUES (%s, %s, %s, %s, 'high')",
                T, CAM[camera]["id"], name, Jsonb(FENCE))
            count("restricted zones added")
    found = {}
    for r in rows("SELECT z.id, z.severity, c.name FROM restricted_zones z JOIN cameras c ON c.id = z.camera_id "
                  "WHERE z.tenant_id = %s AND z.is_active ORDER BY z.created_at", T):
        found.setdefault(r["name"], r)
    return found


JURONG_ROUND = [("Gate 1 guardhouse", -0.00055, -0.00060), ("Loading docks 1 to 3", 0.00022, 0.00012),
                ("Warehouse A staff door", 0.00048, 0.00036), ("Office block side door", -0.00026, 0.00066),
                ("Gate 2 (closed for resurfacing)", 0.00070, -0.00050)]


def routes() -> dict[str, dict]:
    hub = SITE[HUB]
    if not one("SELECT 1 FROM patrol_routes WHERE tenant_id = %s AND site_id = %s AND is_active", T, hub["id"]):
        route = one("INSERT INTO patrol_routes (tenant_id, site_id, name, description) VALUES (%s, %s, %s, %s) RETURNING id",
                    T, hub["id"], "Jurong perimeter round", "Gate 1, the docks, Warehouse A, the office block, Gate 2.")
        for seq, (name, d_lat, d_lng) in enumerate(JURONG_ROUND, 1):
            run("INSERT INTO patrol_checkpoints (tenant_id, route_id, sequence, name, latitude, longitude, qr_code) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)", T, route["id"], seq, name,
                float(hub["latitude"]) + d_lat, float(hub["longitude"]) + d_lng, f"DEMO-JLH-CP{seq}")
        count("patrol routes added")
    found = {}
    for r in rows("SELECT r.id, r.site_id FROM patrol_routes r WHERE r.tenant_id = %s AND r.is_active", T):
        points = rows("SELECT id, latitude, longitude FROM patrol_checkpoints WHERE route_id = %s AND is_active "
                      "ORDER BY sequence", r["id"])
        if points:
            found[r["site_id"]] = {"id": r["id"], "points": points}
    return found


VP_PLAN = [
    {"name": "Jurong night camera round", "site": HUB, "at": time(22, 0), "officer": "ops",
     "cameras": {"Gate 1 (Vehicles)": ["Is the vehicle gate closed, with the barrier down?",
                                       "Is the lane clear of people and vehicles?"],
                 "Warehouse Floor": ["Are the loading dock shutters closed?",
                                     "Is the floor clear of smoke, water and obstructions?"]}},
    {"name": "Marina Bay morning camera round", "site": TOWER, "at": time(7, 30), "officer": "ops",
     "cameras": {"Lobby Entrance": ["Is the lobby entrance unobstructed?",
                                    "Is the restricted area clear of people?"],
                 "Loading Bay": ["Is the loading bay roller shutter down?",
                                 "Is the bay clear of unattended objects?"]}},
]


def vp_schedules() -> list[dict]:
    first = (END - timedelta(days=DAYS)).astimezone(SGT).date()
    for plan in VP_PLAN:
        if one("SELECT 1 FROM virtual_patrol_schedules WHERE tenant_id = %s AND name = %s", T, plan["name"]):
            continue
        sched = one("""INSERT INTO virtual_patrol_schedules (tenant_id, site_id, name, description, schedule_type,
                         start_date, patrol_time, grace_minutes, assigned_user_id, email_frequency, created_by_user_id)
                       VALUES (%s, %s, %s, %s, 'DAILY', %s, %s, 30, %s, 'DAILY', %s) RETURNING id""",
                    T, SITE[plan["site"]]["id"], plan["name"], "Each camera looked at, two questions each.",
                    first, plan["at"], USER[plan["officer"]]["id"], USER["ops"]["id"])
        for seq, (camera, questions) in enumerate(plan["cameras"].items(), 1):
            sc = one("INSERT INTO virtual_patrol_schedule_cameras (tenant_id, schedule_id, camera_id, sequence_no) "
                     "VALUES (%s, %s, %s, %s) RETURNING id", T, sched["id"], CAM[camera]["id"], seq)
            for qn, words in enumerate(questions, 1):
                run("INSERT INTO virtual_patrol_questions (tenant_id, schedule_camera_id, question_text, question_type, "
                    "sequence_no, failure_action) VALUES (%s, %s, %s, 'YES_NO', %s, %s)", T, sc["id"], words, qn,
                    "CREATE_INCIDENT" if qn == 2 else "NONE")
        count("virtual patrol schedules added")
    return rows("SELECT id, name, site_id, patrol_time, assigned_user_id FROM virtual_patrol_schedules "
                "WHERE tenant_id = %s AND name = ANY(%s)", T, [p["name"] for p in VP_PLAN])


# ─── The roster: who worked where, and when ──────────────────────────────────

@dataclass
class Shift:
    id: str
    who: str
    site: str
    start: datetime
    end: datetime
    came: datetime
    left: datetime
    rounds: list = field(default_factory=list)


def works(who: str, day: date) -> tuple[bool, str]:
    """Four days on, two off for the guards; Monday to Friday for the supervisor. Today is a working day for all three."""
    today = NOW.astimezone(SGT).date()
    offset = (today - day).days
    if who == "sup":
        return day.weekday() < 5, HUB
    cycle = offset % 6 if who == "g2" else (offset + 1) % 6
    on = cycle in (0, 1, 2, 3)
    if who == "g1":
        return on, TOWER if cycle in (2, 3) else HUB
    return on, HUB


SHIFTS: list[Shift] = []


def roster() -> None:
    day_def = DEF.get("day")
    for day in days_in_window():
        for who in ("g1", "g2", "sup"):
            on, site = works(who, day)
            if not on:
                continue
            start, end = sgt(day, 7), sgt(day, 19)
            if not inside(end):
                continue
            uid = USER[who]["id"]
            if one("SELECT 1 FROM shifts WHERE guard_user_id = %s AND scheduled_start < %s AND scheduled_end > %s",
                   uid, end, start):
                continue
            r = rng("shift", who, day)
            late = r.random() < 0.06
            came = start + timedelta(minutes=r.randint(12, 26) if late else r.randint(-12, 4))
            left = end + timedelta(minutes=r.randint(0, 14))
            early = not late and r.random() < 0.02
            if early:
                left = end - timedelta(minutes=r.randint(35, 70))
            s = SITE[site]
            lat, lng = float(s["latitude"]) + r.uniform(-0.0002, 0.0002), float(s["longitude"]) + r.uniform(-0.0002, 0.0002)
            row = one("""INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start,
                            actual_end, status, check_in_lat, check_in_lon, check_out_lat, check_out_lon, created_by_user_id,
                            created_at, updated_at, is_within_geofence, is_late, late_minutes, overtime_minutes, shift_type,
                            shift_definition_id, check_in_liveness_score, check_out_liveness_score,
                            check_in_is_mock_location, check_out_is_mock_location)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed', %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, 0, 'day',
                                 %s, %s, %s, FALSE, FALSE) RETURNING id""",
                      T, s["id"], uid, start, end, came, left, lat, lng, lat + 0.00003, lng - 0.00002,
                      USER["ops"]["id"], start - timedelta(days=9), left, late,
                      max(0, int((came - start).total_seconds() // 60)) if late else 0,
                      day_def["id"] if day_def else None, round(r.uniform(0.93, 0.99), 3), round(r.uniform(0.93, 0.99), 3))
            shift = Shift(str(row["id"]), who, site, start, end, came, left)
            SHIFTS.append(shift)
            count("shifts")
            if late or early:
                kind, points = ("late_checkin", 3) if late else ("early_departure", 5)
                settled = END - left > timedelta(days=2)
                run("""INSERT INTO violations (tenant_id, guard_user_id, shift_id, site_id, violation_type, description, points,
                         status, is_auto_generated, reviewed_by_user_id, reviewed_at, review_notes, occurred_at, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s)""",
                    T, uid, shift.id, s["id"], kind,
                    f"Checked in {int((came - start).total_seconds() // 60)} minutes after the start of the shift."
                    if late else f"Checked out {int((end - left).total_seconds() // 60)} minutes before the end of the shift.",
                    points, "acknowledged" if settled else "open",
                    USER["sup"]["id"] if settled else None, left + timedelta(days=1) if settled else None,
                    ("Bus breakdown on the AYE; told the supervisor before the start." if late
                     else "Left early with the supervisor's agreement: family matter.") if settled else None,
                    came if late else left, came if late else left)
                count("violations")


def on_shift(site: str, moment: datetime) -> Shift | None:
    for s in SHIFTS:
        if s.site == site and s.came <= moment <= s.left:
            return s
    return None


# ─── Guard patrols and the occurrence book ───────────────────────────────────

ROUND_WORDS = {
    HUB: ["Gate 1 to Gate 2 round complete. All clear.", "Perimeter round complete. Fence intact, docks shut.",
          "Round complete. Dock 3 shutter left half open by the night crew; closed it.",
          "Round complete. Two fence lights out beside dock 2 — reported for maintenance."],
    TOWER: ["Night round complete. Lobby, level 3 lift lobby, car park B2 and the plant room all clear.",
            "Round complete. Water on the floor at car park B2 near pillar 14; cones placed, building management told.",
            "Round complete. Rooftop plant room door found unlocked; locked it and noted for the morning."],
}
GENERAL = {
    HUB: [("equipment_check", "Radio and torch checked; fire extinguishers at docks 1 to 3 within date."),
          ("delivery", "{company} lorry {plate}, {pallets} pallets for Warehouse A, dock {dock}. Seal intact and matched the delivery order."),
          ("general", "Forklift charging bay tidy; charger 2 showing a fault light. Told the warehouse supervisor."),
          ("maintenance", "Contractor replaced the fence light beside dock 2. Tested after dark: working."),
          ("general", "Stray dog inside the perimeter near Gate 2; walked it out and closed the pedestrian gate.")],
    TOWER: [("equipment_check", "Fire panel normal. Sprinkler valve room locked and sealed."),
            ("general", "Lift 3 out of service for maintenance from 10:00; signs placed at every floor."),
            ("visitor_arrival", "Building management walk-through with two engineers from {company}. Escorted throughout."),
            ("general", "Lost and found: a black umbrella left at the lobby bench; kept at the security counter.")],
}
COMPANIES = ["YCH Logistics", "Kerry Logistics", "Poh Tiong Choon", "Teck Wah Transport", "Schenker Singapore",
             "Pan Asia Movers", "Keppel Freight", "Coolair Engineering", "Sing Lift Services", "Hoe Kee Electrical"]


def letters_ok(prefix: str, digits: str) -> str:
    """The check letter a Singapore plate carries. We use any OTHER letter, so no plate here is real."""
    tail = prefix[-2:] if len(prefix) == 3 else prefix.rjust(2, "@")
    values = [0 if c == "@" else ord(c) - 64 for c in tail] + [int(d) for d in digits.rjust(4, "0")]
    return "AZYXUTSRPMLKJHGEDCB"[sum(v * w for v, w in zip(values, (9, 4, 5, 4, 3, 2))) % 19]


def not_a_real_plate(r: random.Random) -> str:
    prefix = "G" + r.choice("BCDKLMNPTX") if r.random() < 0.5 else "S" + r.choice("GJKLMN") + r.choice("ABCDEFGHJKLMNPRSTUXYZ")
    digits = str(r.randint(100, 9899))
    good = letters_ok(prefix, digits)
    return prefix + digits + r.choice([c for c in "ABCDEGHJKLMPRSTUXYZ" if c != good])


FLEET = [not_a_real_plate(rng("fleet", i)) for i in range(48)]


def patrols_and_book(route_of: dict) -> None:
    for s in SHIFTS:
        r = rng("book", s.id)
        site = SITE[s.site]
        uid = USER[s.who]["id"]
        route = route_of.get(site["id"])

        def entry(kind: str, body: str, when: datetime, severity=None, alert=None, incident=None) -> None:
            run("""INSERT INTO occurrence_book_entries (tenant_id, site_id, shift_id, author_user_id, entry_type, body,
                     severity, related_alert_id, related_incident_id, latitude, longitude, occurred_at, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                T, site["id"], s.id, uid, kind, body, severity, alert, incident,
                float(site["latitude"]), float(site["longitude"]), when, when + timedelta(minutes=r.randint(1, 6)))
            count("occurrence book entries")

        entry("general", f"On duty at {s.site}. Took over from the night control room; keys and radio received.",
              s.came + timedelta(minutes=4))
        if route and s.who != "sup":
            for hour in (10, 15):
                begun = s.start.replace(minute=0) + timedelta(hours=hour - 7, minutes=r.randint(0, 20))
                points = route["points"]
                missed = r.random() < 0.05
                scanned = points[:-1] if missed else points
                session = one("""INSERT INTO patrol_sessions (tenant_id, route_id, guard_user_id, shift_id, status, started_at,
                                   completed_at, total_checkpoints, scanned_checkpoints, created_at)
                                 VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s, %s, %s) RETURNING id""",
                              T, route["id"], uid, s.id, begun, begun + timedelta(minutes=9 * len(scanned)),
                              len(points), len(scanned), begun)
                for k, cp in enumerate(scanned):
                    run("""INSERT INTO checkpoint_scans (tenant_id, session_id, checkpoint_id, scanned_at, scan_method, latitude,
                             longitude, verified, guard_user_id) VALUES (%s, %s, %s, %s, 'qr', %s, %s, TRUE, %s)""",
                        T, session["id"], cp["id"], begun + timedelta(minutes=9 * k + r.randint(0, 3)),
                        cp["latitude"], cp["longitude"], uid)
                    count("checkpoint scans")
                count("guard patrols")
                entry("patrol_start", "Commenced patrol round.", begun)
                words = r.choice(ROUND_WORDS[s.site])
                if missed:
                    words = "Round complete except Gate 2: closed for resurfacing, could not reach the checkpoint."
                entry("patrol_end", words, begun + timedelta(minutes=9 * len(scanned)))
        for _ in range(r.randint(1, 2)):
            kind, words = r.choice(GENERAL[s.site])
            words = words.format(company=r.choice(COMPANIES), plate=r.choice(FLEET), pallets=r.randint(6, 22),
                                 dock=r.randint(1, 3))
            entry(kind, words, s.came + timedelta(minutes=r.randint(60, 600)))
        entry("handover", f"Handed over to the night control room. {r.choice(['Nothing outstanding.', 'Nothing outstanding.', 'Dock 2 fence light still out; maintenance booked.', 'Lift 3 back in service.'])}",
              s.left - timedelta(minutes=3))


# ─── Visitors ────────────────────────────────────────────────────────────────

GIVEN = ["Ahmad", "Siti", "Kumar", "Mei Ling", "Jun Hao", "Nurul", "Ravi", "Hui Min", "Daniel", "Aisyah", "Wei Jie",
         "Priscilla", "Arun", "Farid", "Shu Fen", "Marcus", "Kavitha", "Zhi Wei", "Hafiz", "Grace"]
FAMILY = ["Tan", "Lim", "Ng", "Rahman", "Ong", "Goh", "Pillai", "Chua", "Yusof", "Koh", "Teo", "Nair", "Sim", "Ismail"]
PURPOSE = {HUB: [("delivery", "Delivery to Warehouse A"), ("delivery", "Collection from dock 3"),
                 ("walk_in", "Forklift servicing"), ("vehicle", "Container pick-up")],
           TOWER: [("walk_in", "Aircon servicing, level 12"), ("walk_in", "Lift maintenance"),
                   ("walk_in", "Meeting with building management"), ("delivery", "Courier for level 8")]}


def visitors() -> None:
    for day in days_in_window():
        for site in (HUB, TOWER):
            r = rng("visit", site, day)
            for _ in range(r.choice([0, 1, 1, 2, 2, 3])):
                came = sgt(day, r.randint(9, 15), r.choice([0, 10, 20, 30, 40, 50]))
                went = came + timedelta(minutes=r.randint(25, 150))
                guard = on_shift(site, came)
                if guard is None or not inside(went) or not guard.came <= went <= guard.left:
                    continue
                if one("SELECT 1 FROM visitors WHERE site_id = %s AND arrived_at = %s", SITE[site]["id"], came):
                    continue
                kind, purpose = r.choice(PURPOSE[site])
                name = f"{r.choice(GIVEN)} {r.choice(FAMILY)}"
                plate = r.choice(FLEET) if kind in ("delivery", "vehicle") else None
                visitor = one("""INSERT INTO visitors (tenant_id, site_id, full_name, company, host_name, purpose, vehicle_plate,
                                   expected_from, expected_until, created_by_user_id, created_at, updated_at, qr_token, status,
                                   visit_type, arrived_at, departed_at)
                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'departed', %s, %s, %s) RETURNING id""",
                              T, SITE[site]["id"], name, r.choice(COMPANIES),
                              "Warehouse supervisor" if site == HUB else "Building management office", purpose, plate,
                              came - timedelta(minutes=30), came + timedelta(hours=4), USER["ops"]["id"],
                              came - timedelta(hours=r.randint(2, 30)), went, secrets.token_urlsafe(18), kind, came, went)
                badge = f"V-{r.randint(1, 60):03d}"
                for event, when, method in (("arrival", came, "manual"), ("departure", went, "manual")):
                    run("""INSERT INTO visitor_logs (tenant_id, visitor_id, site_id, guard_user_id, event_type, badge_number,
                             occurred_at, created_at, checkin_method) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        T, visitor["id"], SITE[site]["id"], USER[guard.who]["id"], event, badge, when, when, method)
                count("visits")


# ─── Plate reads at Gate 1 ───────────────────────────────────────────────────

COLOURS = ["white", "white", "white", "silver", "grey", "black", "blue", "red"]
KINDS = [("lorry", 0.42), ("van", 0.26), ("car", 0.24), ("motorcycle", 0.08)]


def plate_reads() -> None:
    camera = CAM["Gate 1 (Vehicles)"]["id"]
    for day in days_in_window():
        r = rng("lpr", day)
        visits = r.randint(12, 20) if day.weekday() < 5 else r.randint(4, 8)
        for _ in range(visits):
            plate = r.choice(FLEET)
            kind = r.choices([k for k, _ in KINDS], [w for _, w in KINDS])[0]
            colour = r.choice(COLOURS)
            arrive = sgt(day, r.randint(6, 18), r.randint(0, 59))
            leave = arrive + timedelta(minutes=r.randint(18, 160))
            for direction, when in (("in", arrive), ("out", leave)):
                if not inside(when) or one("SELECT 1 FROM lpr_events WHERE camera_id = %s AND detected_at = %s",
                                           camera, when):
                    continue
                det = uuid.uuid4()
                run("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, raw_metadata, detected_at, created_at) "
                    "VALUES (%s, %s, %s, 'lpr', %s, %s, %s, %s)", det, T, camera, round(r.uniform(0.86, 0.98), 3),
                    Jsonb({"sample": MARK, "model_version": "lpr-sg-2026.06"}), when, when)
                run("""INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number, plate_confidence,
                         direction, vehicle_type, vehicle_color, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    det, when, T, camera, plate, round(r.uniform(0.84, 0.97), 3), direction, kind, colour, when)
                count("plate reads")


# ─── AI alerts, their pictures, and the incidents some of them opened ────────

#: Expected alerts per day, by camera and kind. Night-weighted kinds are marked.
RATES = {
    "Lobby Entrance": [("face", 3.0, False), ("intrusion", 0.35, True)],
    "Loading Bay": [("intrusion", 0.55, True), ("abandoned", 0.2, False)],
    "Service Corridor": [("intrusion", 0.35, True), ("ppe", 0.6, False)],
    "Gate 1 (Vehicles)": [("intrusion", 0.35, True)],
    "Warehouse Floor": [("ppe", 1.4, False), ("fall", 0.06, False), ("fire_smoke", 0.08, False)],
    "Perimeter East": [("intrusion", 0.55, True), ("weapon", 0.03, False)],
}
FALSE_ALARMS = {
    "intrusion": ["Cleaner emptying the bins in the restricted area. Expected.", "Building technician with a work order.",
                  "Shadow of a passing lorry across the zone.", "A cat. Nobody on the camera when looked at."],
    "ppe": ["Visitor in the walkway, not on the warehouse floor.", "Hard hat is on; the camera saw the back of the head."],
    "fall": ["Worker kneeling to strap a pallet. Nobody fell."],
    "fire_smoke": ["Forklift exhaust in cold air at the dock door.", "Dust from sweeping the floor."],
    "weapon": ["Folded umbrella carried by a delivery driver."],
    "abandoned": ["Pallet jack left beside the bay by the morning crew; moved."],
}
FOUND = ["Guard checked: nobody there by the time of arrival. Logged.",
         "Delivery driver who walked into the lane looking for the office. Escorted out.",
         "Contractor without a permit for that area; walked back to the visitor route.",
         "Person on foot cutting through to the bus stop. Asked to leave; left.",
         "Two workers taking a smoking break beside the fence. Told them where the smoking point is."]


def _hour(r: random.Random, night: bool) -> int:
    if night:
        return r.choice([0, 1, 2, 3, 4, 5, 21, 22, 23, 19, 20, 12, 14])
    return r.choice(range(7, 20))


def _alert_shape(kind: str, r: random.Random, zone: dict | None) -> dict:
    if kind == "intrusion":
        sev = zone["severity"] if zone["severity"] in ("high", "critical") else "high"
        return {"module": "intrusion", "severity": sev, "code": "intrusion.zone_breach",
                "params": {"zone_id": str(zone["id"]), "severity": sev, "zone_severity": zone["severity"]},
                "title": "Restricted zone breach", "message": f"Person detected in zone (severity={sev})",
                "incident": ("Restricted zone intrusion", sev)}
    if kind == "face":
        return {"module": "face", "severity": "info", "code": "face.unrecognized", "params": {},
                "title": "Unrecognized face detected", "message": "Unrecognized face detected", "incident": None}
    if kind == "ppe":
        missing = r.choice([["hardhat"], ["vest"], ["hardhat", "vest"]])
        return {"module": "ppe", "severity": "medium", "code": "ppe.violation", "params": {"items_missing": missing},
                "title": "PPE violation detected", "message": f"Person missing required PPE: {', '.join(missing)}",
                "incident": None}
    if kind == "fall":
        c = round(r.uniform(0.62, 0.78), 2)
        return {"module": "fall", "severity": "high", "code": "fall.person_fallen", "params": {"fall_confidence": c},
                "title": "Person fallen detected", "message": f"Slip/fall event detected (confidence={c:.2f})",
                "incident": ("Person fallen — possible medical emergency", "high")}
    if kind == "fire_smoke":
        c = round(r.uniform(0.48, 0.66), 2)
        return {"module": "fire_smoke", "severity": "high", "code": "fire_smoke.smoke_detected",
                "params": {"detection_type": "smoke", "confidence": c}, "title": "Smoke detected",
                "message": f"Smoke detected (confidence={c:.2f})", "incident": ("Smoke emergency", "high")}
    if kind == "weapon":
        c = round(r.uniform(0.45, 0.58), 2)
        return {"module": "weapon", "severity": "critical", "code": "weapon.knife_detected",
                "params": {"weapon_type": "knife", "confidence": c}, "title": "Knife detected",
                "message": f"Knife detected (confidence={c:.2f})", "incident": ("Weapon incident (knife)", "critical")}
    dwell = float(r.randint(300, 1500))
    return {"module": "abandoned", "severity": "medium", "code": "abandoned.object_detected",
            "params": {"dwell_seconds": dwell, "object_class": r.choice(["suitcase", "backpack", "handbag"])},
            "title": "Abandoned object detected", "message": f"Unattended object stationary for {dwell:.0f}s",
            "incident": None}


def _status(history: list, incident: str, who, when: datetime, before: str | None, after: str, notes=None) -> str:
    run("INSERT INTO incident_status_history (tenant_id, incident_id, changed_by_user_id, from_status, to_status, notes, changed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)", T, incident, who, before, after, notes, when)
    history.append(after)
    return after


def ai_alerts(zone_of: dict) -> None:
    for camera, kinds in RATES.items():
        cam = CAM[camera]
        site_name = next(n for n, s in SITE.items() if s["id"] == cam["site_id"])
        for day in days_in_window():
            for kind, per_day, night in kinds:
                r = rng("alert", camera, kind, day)
                n = sum(1 for _ in range(12) if r.random() < per_day / 12)
                for _ in range(n):
                    when = sgt(day, _hour(r, night), r.randint(0, 59)) + timedelta(seconds=r.randint(0, 59))
                    if not inside(when) or one("SELECT 1 FROM detections WHERE camera_id = %s AND detected_at = %s",
                                               cam["id"], when):
                        continue
                    _one_alert(camera, cam, site_name, kind, when, r, zone_of.get(camera))


def _one_alert(camera: str, cam: dict, site_name: str, kind: str, when: datetime, r: random.Random, zone) -> None:
    if kind == "intrusion" and zone is None:
        return
    shape = _alert_shape(kind, r, zone)
    det, alert = uuid.uuid4(), uuid.uuid4()
    x1, y1 = r.randint(80, 1300), r.randint(60, 600)
    box = {"x1": x1, "y1": y1, "x2": x1 + r.randint(110, 360), "y2": y1 + r.randint(180, 420)}
    meta = {"sample": MARK, "model_version": "insightface-buffalo_l" if kind == "face" else "yolov8s-2026.06",
            "frame_width": 1920, "frame_height": 1080}
    if zone is not None and kind == "intrusion":
        meta["zone_id"] = str(zone["id"])
    run("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, bounding_box, raw_metadata, detected_at, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", det, T, cam["id"], shape["module"],
        round(r.uniform(0.55, 0.96), 3), Jsonb(box), Jsonb(meta), when, when)
    count("detections")
    if kind == "intrusion":
        run("INSERT INTO intrusion_events (detection_id, detected_at, tenant_id, camera_id, zone_id, person_bbox, dwell_time_seconds, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", det, when, T, cam["id"], zone["id"], Jsonb(box),
            round(r.uniform(2, 45), 2), when)
    rel = f"{T}/{when:%Y}/{when:%m}/{when:%d}/{det}.jpg"
    data = picture(camera, when)
    evidence = uuid.uuid4()
    run("INSERT INTO evidence (id, tenant_id, detection_id, media_type, storage_path, checksum_sha256, captured_at, created_at, site_id) "
        "VALUES (%s, %s, %s, 'image', %s, %s, %s, %s, %s)", evidence, T, det, rel, store(rel, data), when, when, cam["site_id"])
    count("evidence pictures")

    age = END - when
    guard = on_shift(site_name, when)
    responder = USER[guard.who]["id"] if guard else USER["sup"]["id"]
    ops = USER["ops"]["id"]
    seen = when + timedelta(minutes=r.randint(1, 6))
    false_alarm = r.random() < {"intrusion": 0.45, "face": 0.0, "ppe": 0.25, "fall": 1.0, "fire_smoke": 1.0,
                                "weapon": 1.0, "abandoned": 0.6}[kind]
    status, ack_by, ack_at, fp_reason = "open", None, None, None
    settled = age > timedelta(hours=2)
    if shape["incident"] is not None:
        # An alert that opened an incident goes as its incident goes.
        if settled:
            status, ack_by, ack_at = "resolved", ops, seen
        elif age > timedelta(minutes=8):
            status, ack_by, ack_at = "acknowledged", ops, seen
    elif age > timedelta(minutes=r.randint(20, 300)):
        if kind == "face":
            status = "dismissed" if r.random() < 0.8 else "acknowledged"
            ack_by, ack_at = (ops, seen) if status == "acknowledged" else (None, None)
        elif false_alarm:
            status, fp_reason = "false_positive", r.choice(FALSE_ALARMS[kind])
        else:
            status, ack_by, ack_at = ("resolved" if r.random() < 0.6 else "acknowledged"), ops, seen
    run("""INSERT INTO alerts (id, tenant_id, detection_id, camera_id, module_type, severity, alert_code, message_params, title,
             message, status, acknowledged_by_user_id, acknowledged_at, created_at, fp_reason, fp_marked_by_user_id, fp_marked_at,
             site_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        alert, T, det, cam["id"], shape["module"], shape["severity"], shape["code"], Jsonb(shape["params"]), shape["title"],
        shape["message"], status, ack_by, ack_at, when, fp_reason, ops if fp_reason else None,
        seen if fp_reason else None, cam["site_id"])
    count("alerts")
    if status != "open" and kind != "face" and r.random() < 0.18:
        run("INSERT INTO alert_notes (tenant_id, alert_id, author_user_id, note, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
            T, alert, ops, fp_reason or r.choice(FOUND), seen + timedelta(minutes=2), seen + timedelta(minutes=2))
        count("alert notes")

    if shape["incident"] is None:
        return
    title, severity = shape["incident"]
    incident = uuid.uuid4()
    found = r.choice(FALSE_ALARMS[kind]) if false_alarm else r.choice(FOUND)
    # Only a guard on shift at that site can be sent; elsewhere the control room looks on the camera.
    dispatched = settled and guard is not None and r.random() < 0.6
    arrived = seen + timedelta(minutes=r.randint(4, 11))
    resolved_at = arrived + timedelta(minutes=r.randint(12, 60)) if settled else None
    run("""INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, description, alert_code, message_params, severity,
             status, is_auto_created, assigned_to_user_id, resolved_at, created_at, updated_at, dispatched_guard_id,
             dispatched_at, guard_arrived_at, dispatch_notes)
           VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s, %s)""",
        incident, T, alert, cam["id"], title, shape["code"], Jsonb(shape["params"]), severity,
        "resolved" if settled else "investigating", responder, resolved_at, when, resolved_at or seen,
        responder if dispatched else None, seen + timedelta(minutes=1) if dispatched else None,
        arrived if dispatched else None,
        f"Check {camera} and the zone beside it." if dispatched else None)
    run("UPDATE evidence SET incident_id = %s WHERE id = %s AND captured_at = %s", incident, evidence, when)
    history: list = []
    state = _status(history, incident, ops, seen, "open", "investigating")
    if dispatched:
        state = _status(history, incident, ops, seen + timedelta(minutes=1), state, "dispatched")
        state = _status(history, incident, responder, seen + timedelta(minutes=2), state, "en_route")
        state = _status(history, incident, responder, arrived, state, "on_scene")
    if settled:
        _status(history, incident, responder if dispatched else ops, resolved_at, state, "resolved", found)
        run("INSERT INTO incident_notes (tenant_id, incident_id, author_user_id, note, created_at) VALUES (%s, %s, %s, %s, %s)",
            T, incident, responder if dispatched else ops, found, resolved_at - timedelta(minutes=1))
        if guard and dispatched:
            run("""INSERT INTO occurrence_book_entries (tenant_id, site_id, shift_id, author_user_id, entry_type, body, severity,
                     related_alert_id, related_incident_id, occurred_at, created_at)
                   VALUES (%s, %s, %s, %s, 'incident', %s, %s, %s, %s, %s, %s)""",
                T, cam["site_id"], guard.id, responder, f"{title} on {camera}. {found}", severity, alert, incident,
                resolved_at, resolved_at + timedelta(minutes=3))
            count("occurrence book entries")
    count("incidents")


MANUAL = [
    (HUB, "Fence light out beside dock 2", "low", "Two perimeter lights beside dock 2 not working after dark.",
     "Electrician replaced both lamps the next morning."),
    (HUB, "Driver refused entry without a delivery order", "medium",
     "Lorry at Gate 1 with no delivery order and no booking. Driver insisted.",
     "Warehouse supervisor confirmed nothing was expected. Driver left; plate written in the book."),
    (HUB, "Pedestrian gate at Gate 2 found unlocked", "medium", "Found unlocked on the 15:00 round. Gate 2 is closed for resurfacing.",
     "Contractor had left it open. Locked; contractor's supervisor told."),
    (TOWER, "Water leak at car park B2", "medium", "Water on the floor near pillar 14, dripping from a pipe joint.",
     "Building management isolated the pipe; plumber fixed it the same afternoon."),
    (TOWER, "Person sleeping in the stairwell", "medium", "Man asleep on the level 2 landing of stairwell B.",
     "Woken and escorted out politely. Did not return."),
    (TOWER, "Lift 2 stopped between floors", "high", "Lift 2 stopped between levels 6 and 7 with two people inside.",
     "Lift company released them in 18 minutes. Both well. Lift out of service until inspected."),
    (CHANGI, "Fence cut beside the perimeter path", "high", "A 40 cm cut in the east fence found by the morning check.",
     "No sign anyone went through. Fence repaired; camera angle adjusted to cover the spot."),
    (ORCHARD, "Shoplifting reported by a tenant", "medium", "Tenant on level 1 reported a customer leaving without paying.",
     "Police informed by the tenant; footage kept under a hold at the tenant's request."),
]


def manual_incidents() -> None:
    days = days_in_window()
    for i, (site, title, severity, about, outcome) in enumerate(MANUAL):
        r = rng("manual", i, START.date())
        day = days[min(len(days) - 1, int(len(days) * (i + 0.5) / len(MANUAL)))]
        when = sgt(day, r.randint(9, 17), r.randint(0, 59))
        if not inside(when) or END - when < timedelta(hours=3) or one(
                "SELECT 1 FROM incidents WHERE tenant_id = %s AND title = %s AND created_at > %s",
                T, title, END - timedelta(days=DAYS + 5)):
            continue
        guard = on_shift(site, when)
        reporter = USER[guard.who]["id"] if guard else USER["sup"]["id"]
        incident = uuid.uuid4()
        done = min(when + timedelta(hours=r.randint(1, 20)), END - timedelta(minutes=10))
        run("""INSERT INTO incidents (id, tenant_id, title, description, severity, status, is_auto_created, assigned_to_user_id,
                 resolved_at, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 'resolved', FALSE, %s, %s, %s, %s)""",
            incident, T, title, about, severity, reporter, done, when, done)
        history: list = []
        state = _status(history, incident, reporter, when + timedelta(minutes=5), "open", "investigating")
        _status(history, incident, USER["sup"]["id"], done, state, "resolved", outcome)
        run("INSERT INTO incident_notes (tenant_id, incident_id, author_user_id, note, created_at) VALUES (%s, %s, %s, %s, %s)",
            T, incident, USER["sup"]["id"], outcome, done)
        if guard:
            run("""INSERT INTO occurrence_book_entries (tenant_id, site_id, shift_id, author_user_id, entry_type, body, severity,
                     related_incident_id, occurred_at, created_at) VALUES (%s, %s, %s, %s, 'incident', %s, %s, %s, %s, %s)""",
                T, SITE[site]["id"], guard.id, reporter, f"{title}. {about}", severity, incident, when, when + timedelta(minutes=4))
            count("occurrence book entries")
        count("incidents")


# ─── Camera health ───────────────────────────────────────────────────────────

def camera_health() -> None:
    last_frame = {r["camera_id"]: r["last_frame_at"] for r in rows(
        "SELECT camera_id, last_frame_at FROM streams WHERE tenant_id = %s", T)}
    for name, cam in CAM.items():
        stopped = last_frame.get(cam["id"])
        for day in days_in_window():
            r = rng("health", name, day)
            if r.random() > 0.22:
                continue
            when = sgt(day, r.randint(0, 23), r.randint(0, 59))
            if not inside(when) or (stopped and when > stopped) or one(
                    "SELECT 1 FROM camera_health_events WHERE camera_id = %s AND occurred_at = %s", cam["id"], when):
                continue
            run("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, detail, occurred_at) VALUES (%s, %s, %s, %s, %s)",
                T, cam["id"], "stream_degraded", "consecutive_failures=3", when)
            run("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, detail, occurred_at) VALUES (%s, %s, %s, %s, %s)",
                T, cam["id"], "stream_reconnected", None, when + timedelta(seconds=r.randint(20, 240)))
            count("camera health events", 2)
        if stopped and inside(stopped) and not one(
                "SELECT 1 FROM camera_health_events WHERE camera_id = %s AND event_type = 'stream_disconnected' "
                "AND occurred_at = %s", cam["id"], stopped + timedelta(seconds=30)):
            run("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, detail, occurred_at) VALUES (%s, %s, %s, %s, %s)",
                T, cam["id"], "stream_disconnected", "consecutive_failures=10", stopped + timedelta(seconds=30))
            count("camera health events")


# ─── Close out what has aged ─────────────────────────────────────────────────

def close_aged() -> None:
    aged = rows("""SELECT a.id, a.module_type FROM alerts a JOIN detections d ON d.id = a.detection_id
                    WHERE a.tenant_id = %s AND a.status = 'open' AND d.raw_metadata->>'sample' = %s
                      AND a.created_at < %s""", T, MARK, END - OPEN_FOR)
    for a in aged:
        status = "dismissed" if a["module_type"] == "face" else "acknowledged"
        run("UPDATE alerts SET status = %s, acknowledged_by_user_id = %s, acknowledged_at = created_at + interval '9 minutes' "
            "WHERE id = %s AND status = 'open'", status, USER["ops"]["id"] if status == "acknowledged" else None, a["id"])
    for i in rows("""SELECT i.id, i.created_at FROM incidents i JOIN alerts a ON a.id = i.alert_id
                       JOIN detections d ON d.id = a.detection_id
                      WHERE i.tenant_id = %s AND i.status = 'investigating' AND d.raw_metadata->>'sample' = %s
                        AND i.created_at < %s""", T, MARK, END - OPEN_FOR):
        done = i["created_at"] + timedelta(minutes=48)
        run("UPDATE incidents SET status = 'resolved', resolved_at = %s, updated_at = %s WHERE id = %s", done, done, i["id"])
        _status([], i["id"], USER["ops"]["id"], done, "investigating", "resolved", "Checked on camera; nothing found.")
    if aged:
        count("aged alerts closed", len(aged))


# ─── Virtual patrols, made with the application's own functions ──────────────

ANSWER_NOTES = ["All clear.", "Clear.", "Nothing to note.", "Normal."]


async def virtual_patrols(schedules: list[dict]) -> None:
    sys.path.insert(0, "/app/backend")
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services import virtual_patrol as vp
    from app.services import vpatrol_reports
    from app.services.vpatrol_snapshot import storage_path

    engine = create_async_engine(os.environ["ALEMBIC_DATABASE_URL"])
    try:
        async with AsyncSession(engine) as s:
            for sched in schedules:
                plan = next(p for p in VP_PLAN if p["name"] == sched["name"])
                officer = str(USER[plan["officer"]]["id"])
                for day in days_in_window():
                    due = datetime.combine(day, sched["patrol_time"], SGT).astimezone(timezone.utc)
                    if not inside(due) or END - due < timedelta(minutes=45):
                        continue
                    r = rng("vp", sched["name"], day)
                    session = (await s.execute(text(
                        "SELECT id, status FROM virtual_patrol_sessions WHERE schedule_id = :s AND scheduled_for = :w"),
                        {"s": sched["id"], "w": due})).mappings().first()
                    if session is None:
                        made_ = await vp.create_session(s, schedule_id=str(sched["id"]), scheduled_for=due,
                                                        officer_user_id=officer)
                        session_id = made_["session_id"]
                    elif session["status"] in ("SCHEDULED", "MISSED"):
                        session_id = str(session["id"])   # the scheduler's, which nobody carried out on this machine
                    else:
                        continue
                    missed = r.random() < 0.04
                    if missed:
                        await s.execute(text("UPDATE virtual_patrol_sessions SET status = 'MISSED', updated_at = :u WHERE id = :i"),
                                        {"u": due + timedelta(minutes=31), "i": session_id})
                        count("virtual patrols")
                        continue
                    begun = due + timedelta(minutes=r.randint(0, 9))
                    clock = begun
                    cams = (await s.execute(text(
                        "SELECT sc.id, sc.camera_name FROM virtual_patrol_session_cameras sc WHERE sc.session_id = :i "
                        "ORDER BY sc.sequence_no"), {"i": session_id})).mappings().all()
                    answered = 0
                    exception_at = None
                    for sc in cams:
                        clock += timedelta(seconds=r.randint(40, 110))
                        rel = storage_path(tenant_id=str(T), site_id=str(sched["site_id"]), session_id=session_id,
                                           session_camera_id=str(sc["id"]), taken_at=clock)
                        data = picture(sc["camera_name"], clock)
                        checksum = store(rel, data)
                        await s.execute(text("""UPDATE virtual_patrol_session_cameras SET status = 'COMPLETED', started_at = :a,
                                                  completed_at = :b, snapshot_path = :p, snapshot_filename = :f,
                                                  snapshot_taken_at = :a, snapshot_checksum = :c, snapshot_bytes = :n
                                                WHERE id = :i"""),
                                        {"a": clock, "b": clock + timedelta(seconds=r.randint(30, 80)), "p": rel,
                                         "f": Path(rel).name, "c": checksum, "n": len(data), "i": sc["id"]})
                        questions = (await s.execute(text(
                            "SELECT id, question_type FROM virtual_patrol_session_questions WHERE session_camera_id = :i "
                            "ORDER BY sequence_no"), {"i": sc["id"]})).mappings().all()
                        for q in questions:
                            clock += timedelta(seconds=r.randint(8, 25))
                            wrong = exception_at is None and r.random() < 0.03
                            await s.execute(text("""INSERT INTO virtual_patrol_session_answers (tenant_id, session_question_id,
                                                      answered_by_user_id, answer_text, answered_at, is_exception, exception_reason)
                                                    VALUES (:t, :q, CAST(:u AS uuid), :a, :w, :x, :why)"""),
                                            {"t": T, "q": q["id"], "u": officer, "a": "NO" if wrong else "YES", "w": clock,
                                             "x": wrong, "why": "A person seen on the camera; the guard was asked to look."
                                             if wrong else None})
                            if wrong:
                                exception_at = clock
                            answered += 1
                        clock += timedelta(seconds=r.randint(20, 60))
                    await s.execute(text("""UPDATE virtual_patrol_sessions SET status = 'COMPLETED', officer_user_id = CAST(:u AS uuid),
                                              started_at = :a, completed_at = :b, completed_camera_count = camera_count,
                                              answered_question_count = :n, notes = :note, updated_at = :b WHERE id = :i"""),
                                    {"u": officer, "a": begun, "b": clock, "n": answered, "i": session_id,
                                     "note": "Exception raised; see the answers." if exception_at else r.choice(ANSWER_NOTES)})
                    if not DRY:
                        await vpatrol_reports.store_reports(s, session_id)
                        await s.execute(text("UPDATE virtual_patrol_reports SET generated_at = :b WHERE session_id = :i"),
                                        {"b": clock + timedelta(seconds=5), "i": session_id})
                    count("virtual patrols")
            if DRY:
                await s.rollback()
            else:
                await s.commit()
    finally:
        await engine.dispose()


# ─── Run ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"Demo history for '{ORG}': {START.astimezone(SGT):%d %b %H:%M} to {END.astimezone(SGT):%d %b %H:%M} SGT"
          f"{' (dry run)' if DRY else ''}")
    try:
        zone_of = zones()
        route_of = routes()
        schedules = vp_schedules()
        # Shifts the generator wrote on an earlier run, so alerts and visits can find who was on duty.
        for r in rows("""SELECT s.id, u.email, st.name AS site, s.scheduled_start, s.scheduled_end, s.actual_start, s.actual_end
                           FROM shifts s JOIN users u ON u.id = s.guard_user_id JOIN sites st ON st.id = s.site_id
                          WHERE s.tenant_id = %s AND s.status = 'completed' AND s.actual_end > %s""",
                      T, START - timedelta(days=1)):
            who = next((k for k, e in EMAIL.items() if e == r["email"]), None)
            if who is None:
                continue
            SHIFTS.append(Shift(str(r["id"]), who, r["site"], r["scheduled_start"], r["scheduled_end"],
                                r["actual_start"], r["actual_end"]))
        known = {s.id for s in SHIFTS}
        roster()
        new_shifts = [s for s in SHIFTS if s.id not in known]
        everything = list(SHIFTS)
        SHIFTS[:] = new_shifts
        patrols_and_book(route_of)
        SHIFTS[:] = everything
        visitors()
        plate_reads()
        ai_alerts(zone_of)
        manual_incidents()
        camera_health()
        close_aged()
        if DRY:
            db.rollback()
        else:
            db.commit()
    except BaseException:
        db.rollback()
        for path in files_written:
            path.unlink(missing_ok=True)
        raise
    if DRY:
        print("  (dry run: virtual patrols skipped — their schedules were rolled back with the rest)")
    else:
        asyncio.run(virtual_patrols(schedules))
    for what, n in sorted(made.items()):
        print(f"  {n:6d}  {what}")


main()
