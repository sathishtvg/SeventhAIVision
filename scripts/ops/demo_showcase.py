"""Load a demonstration's worth of sample data into the `demo` organisation.

WHAT IT IS FOR
    The demo organisation has months of cameras, alerts, detections, shifts and
    patrols, and nothing at all in the modules added since: the SOP library, the
    places of a site, guard response and its clocks, the occurrence book's
    reviews and instructions, visitor authorisations, investigations, evidence
    packages. This fills them with one small, connected story at Jurong
    Logistics Hub, so that every screen has something true-to-life on it.

HOW IT WORKS
    Through the running application, signed in as the demo people, so that what
    it makes is made the way a person would make it: numbered, audited, cut into
    passages, sealed. SQL is used only to look things up, to add number plate
    reads (nothing else writes those without a camera worker), and to move the
    time of a row this script has itself just made.

    It ADDS. It never deletes, and it never touches a row it did not make —
    except to start today's rostered shifts of the three demo staff, which is
    what checking them in would do.

    Run it again whenever you like: what is already there is left alone.

RUN IT (from the repository root; the api container must be healthy)

    docker exec -i docker-api-1 python - < scripts/ops/demo_showcase.py

    DEMO_STEP=setup     the lasting things only (procedures, places, clocks ...)
    DEMO_STEP=today     what goes stale: today's shifts, visits, incidents, book
    DEMO_STEP=live      what must be fresh: one unanswered incident to dispatch,
                        and two alerts that become a situation awaiting a decision
    (unset)             setup, then today

        docker exec -i -e DEMO_STEP=today docker-api-1 python - < scripts/ops/demo_showcase.py

    Run `today` on the day of the demo, an hour or so before it, and `live`
    five minutes before you start: an unanswered incident goes late within
    minutes, and a situation settles by itself after about half an hour.

THE PASSWORD of the demo accounts is read from backend/scripts/seed_demo.py and
is never printed. Never run that file itself: it deletes the whole organisation.
"""
from __future__ import annotations

import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

API = os.environ.get("DEMO_API", "http://localhost:8000") + "/api/v1"
STEP = os.environ.get("DEMO_STEP", "all")
ORG = "demo"
SGT = timezone(timedelta(hours=8))
NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone(SGT).date()

PEOPLE = {"ops": "ops@demo.local", "sup": "supervisor@demo.local", "g1": "guard1@demo.local", "g2": "guard2@demo.local"}
HUB, TOWER = "Jurong Logistics Hub", "Marina Bay Tower"
#: Not a real registration: its last letter fails the checksum every Singapore plate carries.
PLATE = "SKD2468A"
BADGE = "V-017"
SAMPLE = "Sample procedure for demonstration. Replace it with your organisation's own."

made: list[str] = []
kept: list[str] = []
failed: list[str] = []


def at(day_offset: int, hour: int, minute: int = 0) -> datetime:
    """A moment on a day relative to today, on the site's clock."""
    return datetime.combine(TODAY + timedelta(days=day_offset), datetime.min.time(), SGT).replace(hour=hour, minute=minute)


def ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


# ─── The database, for looking things up ─────────────────────────────────────

db = psycopg.connect(os.environ["ALEMBIC_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://"),
                     autocommit=True, row_factory=dict_row)


def rows(sql: str, *params) -> list[dict]:
    return db.execute(sql, params).fetchall()


def one(sql: str, *params):
    found = rows(sql, *params)
    return found[0] if found else None


T = one("SELECT id FROM tenants WHERE slug = %s", ORG)["id"]
USER = {k: one("SELECT id, full_name FROM users WHERE tenant_id = %s AND email = %s", T, e) for k, e in PEOPLE.items()}
SITE = {r["name"]: r for r in rows("SELECT id, name, latitude, longitude FROM sites WHERE tenant_id = %s", T)}
CAMERA = {r["name"]: r["id"] for r in rows("SELECT id, name FROM cameras WHERE tenant_id = %s", T)}
missing = [k for k, v in USER.items() if v is None] + [s for s in (HUB, TOWER) if s not in SITE]
if missing:
    sys.exit(f"The demo organisation is not as expected; missing: {missing}")
HUB_ID, TOWER_ID = SITE[HUB]["id"], SITE[TOWER]["id"]
LAT, LNG = float(SITE[HUB]["latitude"]), float(SITE[HUB]["longitude"])


# ─── The application, for making things ──────────────────────────────────────

class Refused(Exception):
    pass


def _password() -> str:
    seed = Path("/app/backend/scripts/seed_demo.py").read_text(encoding="utf-8")
    return re.search(r'DEMO_PASSWORD\s*=\s*["\'](.+?)["\']', seed).group(1)


http = httpx.Client(base_url=API, timeout=120)
TOKEN: dict[str, dict] = {}


def sign_in() -> None:
    password = _password()
    for who, email in PEOPLE.items():
        r = http.post("/auth/login", json={"tenant_slug": ORG, "email": email, "password": password})
        if r.status_code != 200 or "access_token" not in r.json():
            sys.exit(f"Could not sign in as {email} (HTTP {r.status_code}). Is the account active, without 2FA?")
        TOKEN[who] = {"Authorization": f"Bearer {r.json()['access_token']}"}


def call(who: str, method: str, path: str, body=None, *, params=None, ok=(200, 201)):
    r = http.request(method, path, headers=TOKEN[who], json=body, params=params)
    if r.status_code not in ok:
        raise Refused(f"{method} {path} as {PEOPLE[who]}: HTTP {r.status_code} {r.text[:260]}")
    return r.json() if r.content and "json" in r.headers.get("content-type", "") else None


def do(label: str, work) -> None:
    """One piece of the story. A refusal is reported and the rest carries on."""
    try:
        result = work()
        (kept if result is False else made).append(label)
    except Exception as exc:  # noqa: BLE001 - a demo loader reports and carries on
        failed.append(f"{label}: {exc}")


# ─── SETUP: what lasts ───────────────────────────────────────────────────────

TIMES = {"critical": (120, 300, 3600), "high": (300, 600, 7200), "medium": (900, 1800, 14400), "low": (1800, 3600, 28800)}
POLICIES = [
    {"name": "High-severity incident nobody has answered", "severity": "high", "trigger": "NOT_ACKNOWLEDGED",
     "after_seconds": 600, "notify_role_id": 3},
    {"name": "Critical incident nobody has answered", "severity": "critical", "trigger": "NOT_ACKNOWLEDGED",
     "after_seconds": 240, "notify_role_id": 2},
    {"name": "Guard sent and not yet on scene", "severity": None, "trigger": "NOT_ARRIVED",
     "after_seconds": 1200, "notify_role_id": 3},
]


def clocks():
    have = {r["severity"] for r in rows("SELECT severity FROM sla_configs WHERE tenant_id = %s", T)}
    for severity, (ack, arrive, resolve) in TIMES.items():
        if severity not in have:
            call("ops", "PUT", f"/sla/configs/{severity}", {
                "ack_within_seconds": ack, "dispatch_within_seconds": arrive, "resolve_within_seconds": resolve,
                "escalation_user_id": str(USER["sup"]["id"])})
    if not call("ops", "GET", "/incident-responses/settings")["sla_enabled"]:
        call("ops", "PUT", "/incident-responses/settings", {"sla_enabled": True})
    named = {r["name"] for r in rows("SELECT name FROM escalation_policies WHERE tenant_id = %s", T)}
    for policy in POLICIES:
        if policy["name"] not in named:
            call("ops", "POST", "/incident-responses/policies", policy)
    return None if len(have) < len(TIMES) or len(named) < len(POLICIES) else False


def _place(site_id, name: str, kind: str, d_lat: float, d_lng: float, *, parent=None, door=None, level=None,
           about: str | None = None):
    found = one("SELECT id FROM site_places WHERE site_id = %s AND name = %s AND is_active", site_id, name)
    if found:
        return found["id"]
    site = one("SELECT latitude, longitude FROM sites WHERE id = %s", site_id)
    body = {"site_id": str(site_id), "kind": kind, "name": name, "description": about,
            "latitude": float(site["latitude"]) + d_lat, "longitude": float(site["longitude"]) + d_lng}
    if parent:
        body["parent_id"] = str(parent)
    if door:
        body["door_id"] = str(door)
    if level is not None:
        body["level"] = level
    return call("ops", "POST", "/site-map/places", body)["id"]


def _door(name: str, where: str):
    found = one("SELECT id FROM access_doors WHERE tenant_id = %s AND site_id = %s AND name = %s", T, HUB_ID, name)
    if found:
        return found["id"]
    return call("ops", "POST", "/access/doors", {"name": name, "location": where, "site_id": str(HUB_ID)})["id"]


PLACES: dict[str, object] = {}


def places():
    before = one("SELECT count(*) AS n FROM site_places WHERE tenant_id = %s", T)["n"]
    staff = _door("Warehouse A staff door", "Warehouse A, ground floor, north side")
    office = _door("Office block side door", "Office block, east side")
    PLACES["warehouse"] = _place(HUB_ID, "Warehouse A", "BUILDING", 0.00035, 0.00040, about="Bonded warehouse, three loading docks")
    PLACES["level1"] = _place(HUB_ID, "Warehouse A, level 1", "FLOOR", 0.00035, 0.00040, parent=PLACES["warehouse"], level=1)
    # A place is a part of a building and of nothing else: the door is the warehouse's, not the floor's.
    PLACES["staff_door"] = _place(HUB_ID, "Warehouse A staff door", "ACCESS_POINT", 0.00048, 0.00036,
                                  parent=PLACES["warehouse"], door=staff)
    PLACES["office"] = _place(HUB_ID, "Office block", "BUILDING", -0.00030, 0.00055, about="Site office and control room")
    PLACES["office_door"] = _place(HUB_ID, "Office block side door", "ACCESS_POINT", -0.00026, 0.00066,
                                   parent=PLACES["office"], door=office)
    _place(HUB_ID, "Gate 1 (vehicles)", "GATE", -0.00055, -0.00060, about="All vehicles and deliveries")
    _place(HUB_ID, "Gate 2 (closed for resurfacing)", "GATE", 0.00070, -0.00050)
    _place(HUB_ID, "Loading docks 1 to 3", "ZONE", 0.00022, 0.00012)
    _place(HUB_ID, "Visitor car park", "PARKING", -0.00050, 0.00010)
    _place(HUB_ID, "Assembly point A", "ASSEMBLY_POINT", -0.00068, 0.00020, about="Beside the visitor car park")
    _place(HUB_ID, "Fire command post", "EMERGENCY_POINT", -0.00034, 0.00044, about="Office block, ground floor")
    _place(TOWER_ID, "Car park entrance", "GATE", -0.00030, 0.00025)
    _place(TOWER_ID, "Loading bay", "ZONE", 0.00028, -0.00030)
    _place(TOWER_ID, "Assembly point, Marina Boulevard", "ASSEMBLY_POINT", 0.00045, 0.00040)
    return None if one("SELECT count(*) AS n FROM site_places WHERE tenant_id = %s", T)["n"] > before else False


PROCEDURES = [
    {"title": "Intrusion into a restricted area", "category": "incident_response", "site": None, "types": ["intrusion"],
     "body": f"""{SAMPLE}

## On the alert
Look at the camera that raised it before anything else. Say on the radio which zone, and whether you can see a person.

## Sending a guard
Send the nearest guard who is free. Do not send a guard alone into an unlit area: send two, or wait for the second.

## On scene
Approach so that the camera can see you. Challenge from a distance. If the person does not stop, do not chase: keep them in view and call the supervisor.

## Afterwards
Write the occurrence book entry before the end of the shift. Keep the camera footage: ask the control room to place it under a hold.
"""},
    {"title": "Fire or smoke", "category": "fire", "site": None, "types": ["fire_smoke", "alarm"],
     "body": f"""{SAMPLE}

## First minute
Confirm on the camera or by eye. Call 995. Sound the alarm if it has not sounded.

## Evacuation
Open Gate 1 for the fire engines and keep the lane clear. Send everybody to Assembly point A. Nobody goes back in.

## Fire command post
The supervisor takes the fire command post and meets the SCDF officer with the site plan and the key to the riser room.

## Afterwards
Count heads at the assembly point against the visitor and contractor lists. Record the times in the occurrence book.
"""},
    {"title": "Unknown or listed vehicle at the gate", "category": "access", "site": HUB, "types": ["lpr"],
     "body": f"""{SAMPLE}

## A vehicle that is not expected
Keep the barrier down. Ask the driver who they are here to see and telephone that person. No answer, no entry.

## A vehicle on the watch list
Do not confront the driver. Note the time, the plate and how many people are inside. Tell the control room at once.

## A vehicle that waits outside
A vehicle parked outside Gate 1 for more than ten minutes is written into the occurrence book as unusual activity, with its plate.
"""},
    {"title": "Person down or medical emergency", "category": "medical", "site": None, "types": ["fall", "guard"],
     "body": f"""{SAMPLE}

## First
Call 995 if the person does not answer you or is bleeding heavily. Say the site name and Gate 1.

## Until help arrives
Do not move the person unless they are in danger where they lie. The first aid kit and the AED are in the fire command post.

## A guard who is down
A man-down alert is treated as real until the guard answers. Send the nearest guard and call the guard's phone.

## Afterwards
Meet the ambulance at Gate 1 and escort it. Write the occurrence book entry and tell the client's duty manager.
"""},
    {"title": "Visitors and contractors", "category": "visitor", "site": None, "types": ["access"],
     "body": f"""{SAMPLE}

## Before anybody is let in
Look at what stands on the visit: who approved it, until when, and for which places. See the visitor's ID and record the kind of document. Do not write down its number.

## Escort
A visitor who is to be escorted waits at the gate until the escort comes. A contractor works only under an approved work permit.

## A badge used somewhere it was not issued for
Do not confront the visitor. Tell the supervisor, who will look at it and record what was found.
"""},
]
WAITING = {"title": "Suspicious or abandoned object", "category": "incident_response", "site": None, "types": ["abandoned"],
           "body": f"""{SAMPLE}

## Do not touch it
Do not move, open or cover the object. Do not use a radio or a phone within fifteen metres of it.

## Clear the area
Move people at least fifty metres away, out of line of sight. Close the nearest doors.

## Tell
Tell the control room what it looks like, exactly where it is, and when it was first seen on camera. The supervisor decides whether to call 999.
"""}


def _procedure(p: dict, approve: bool):
    if one("SELECT 1 FROM sop_documents WHERE tenant_id = %s AND title = %s", T, p["title"]):
        return False
    doc = call("sup", "POST", "/sop/documents", {
        "title": p["title"], "category": p["category"], "body": p["body"], "incident_types": p["types"],
        "site_id": str(SITE[p["site"]]["id"]) if p["site"] else None})
    version = doc["versions"][0]["id"]
    call("sup", "POST", f"/sop/versions/{version}/submit")
    if approve:
        call("ops", "POST", f"/sop/versions/{version}/approve", {})
    return None


def contractor():
    found = one("SELECT id FROM contractors WHERE tenant_id = %s AND company_name = %s", T, "Coolair Engineering Pte Ltd")
    if found:
        return False
    row = call("ops", "POST", "/contractors", {
        "company_name": "Coolair Engineering Pte Ltd", "contact_name": "Ravi Menon",
        "specialization": "Air-conditioning and mechanical ventilation"})
    call("ops", "PUT", f"/contractors/{row['id']}/vet", {
        "vetting_status": "approved", "vetting_notes": "Sample contractor for demonstration."})
    return None


def watch_list():
    if one("SELECT 1 FROM watchlist_entries WHERE tenant_id = %s AND plate_number = %s", T, PLATE):
        return False
    call("ops", "POST", "/watchlist/plates", {
        "plate_number": PLATE, "category": "watchlist", "vehicle_type": "van", "vehicle_color": "white",
        "reason": "Seen waiting outside Gate 1 on more than one day. Reported by the gate guard."})
    return None


def plate_reads():
    """Where the listed van was read. Nothing but a camera worker writes these, so they are put in directly."""
    if one("SELECT count(*) AS n FROM lpr_events WHERE tenant_id = %s AND plate_number = %s", T, PLATE)["n"] >= 6:
        return False
    gate, ramp = CAMERA["Gate 1 (Vehicles)"], CAMERA["Car Park Ramp"]
    for camera, when, direction in ((gate, at(-2, 21, 40), "in"), (gate, at(-2, 21, 58), "out"),
                                    (ramp, at(-1, 13, 5), "in"), (ramp, at(-1, 13, 50), "out"),
                                    (gate, at(0, 6, 12), "in"), (gate, at(0, 6, 31), "out")):
        detection = uuid.uuid4()
        db.execute("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at, raw_metadata) "
                   "VALUES (%s, %s, %s, 'lpr', 0.93, %s, '{\"sample\": true}'::jsonb)", (detection, T, camera, when))
        db.execute("INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number, "
                   "plate_confidence, direction, vehicle_type, vehicle_color) VALUES (%s, %s, %s, %s, %s, 0.93, %s, 'van', 'white')",
                   (detection, when, T, camera, PLATE, direction))
    return None


def setup() -> None:
    do("The three clocks switched on, with times per severity and three escalation policies", clocks)
    do("The places of Jurong Logistics Hub and Marina Bay Tower, and two doors", places)
    for p in PROCEDURES:
        do(f"Procedure in force: {p['title']}", lambda p=p: _procedure(p, approve=True))
    do(f"Procedure awaiting approval: {WAITING['title']}", lambda: _procedure(WAITING, approve=False))
    do("Contractor: Coolair Engineering Pte Ltd, vetted", contractor)
    do(f"Vehicle {PLATE} on the watch list", watch_list)
    do(f"Six number plate reads of {PLATE} over three days, at two sites", plate_reads)


# ─── TODAY: what goes stale ──────────────────────────────────────────────────

SHIFT: dict[str, object] = {}


def shifts():
    """The three demo staff are rostered at the Hub. Checking them in is what puts them on the map and in the ranking."""
    started = 0
    for who, (d_lat, d_lng) in (("g2", (-0.00050, -0.00052)), ("g1", (0.00030, 0.00030)), ("sup", (-0.00028, 0.00052))):
        shift = one("SELECT id, status FROM shifts WHERE tenant_id = %s AND guard_user_id = %s AND site_id = %s "
                    "AND scheduled_start <= now() AND scheduled_end > now() ORDER BY scheduled_start DESC LIMIT 1",
                    T, USER[who]["id"], HUB_ID)
        if shift is None:
            # Outside the rostered hours (07:00 to 19:00 at the site): a shift for the demo itself.
            begins = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            shift = call("ops", "POST", "/shifts", {
                "guard_user_id": str(USER[who]["id"]), "site_id": str(HUB_ID),
                "scheduled_start": begins.isoformat(), "scheduled_end": (begins + timedelta(hours=8)).isoformat()})
        SHIFT[who] = shift["id"]
        if shift["status"] == "scheduled":
            # As an on-time check-in writes it. Through the endpoint, a start this late would log a lateness violation.
            db.execute("UPDATE shifts SET status = 'active', actual_start = scheduled_start + interval '3 minutes', "
                       "check_in_lat = %s, check_in_lon = %s, is_within_geofence = TRUE, is_late = FALSE, late_minutes = 0 "
                       "WHERE id = %s AND status = 'scheduled'", (LAT + d_lat, LNG + d_lng, shift["id"]))
            started += 1
    return None if started else False


def _place_ids(*keys: str) -> list[str]:
    found = []
    for name in keys:
        row = one("SELECT id FROM site_places WHERE site_id = %s AND name = %s AND is_active", HUB_ID, name)
        if row:
            found.append(str(row["id"]))
    return found


def _half_hour(moment: datetime) -> datetime:
    return moment.replace(minute=30 if moment.minute >= 30 else 0, second=0, microsecond=0)


def _visit(name: str, company: str, purpose: str, host: str | None, hours: tuple[float, float]):
    """A visit expected from so many hours before now until so many after: a
    visit is of the day it is shown on, whatever hour the demo is at."""
    found = one("SELECT id FROM visitors WHERE tenant_id = %s AND site_id = %s AND full_name = %s AND is_active "
                "AND status <> 'departed' AND expected_until > now()", T, HUB_ID, name)
    if found:
        return found["id"], False
    row = call("ops", "POST", "/visitors", {
        "full_name": name, "company": company, "purpose": purpose, "site_id": str(HUB_ID),
        "host_user_id": str(USER[host]["id"]) if host else None,
        "expected_from": _half_hour(NOW + timedelta(hours=hours[0])).astimezone(SGT).isoformat(),
        "expected_until": _half_hour(NOW + timedelta(hours=hours[1])).astimezone(SGT).isoformat()})
    return row["id"], True


def _stands(visitor=None, permit=None) -> dict:
    return call("g2", "GET", "/visitor-authorizations/standing",
                params={"visitor_id": str(visitor)} if visitor else {"work_permit_id": str(permit)})


def visits():
    new = 0
    # 1. Approved by her host, escorted, ID seen, checked in with a badge — and the badge used at a door she was not sent to.
    farah, fresh = _visit("Farah Binte Ismail", "Schneider Electric", "Switchboard inspection, Warehouse A", "sup", (-3, 5))
    if _stands(visitor=farah)["standing"] == "NOT_ASKED":
        auth = call("g2", "POST", "/visitor-authorizations", {
            "visitor_id": str(farah), "escort_required": True, "escort_user_id": str(USER["g2"]["id"]),
            "escort_note": "Switch room is a restricted area", "place_ids": _place_ids("Warehouse A")})
        call("sup", "POST", f"/visitor-authorizations/{auth['id']}/approve", {"note": "Expected. Annual inspection."})
        call("g2", "POST", f"/visitor-authorizations/{auth['id']}/id-seen", {"kind": "Work pass"})
        log = call("g2", "POST", f"/visitors/{farah}/checkin", {"badge_number": BADGE})
        # She arrived two hours ago, not this second.
        db.execute("UPDATE visitor_logs SET occurred_at = %s WHERE id = %s", (ago(120), log["id"]))
        db.execute("UPDATE visitors SET arrived_at = %s WHERE id = %s", (ago(120), farah))
        if not one("SELECT 1 FROM access_credentials WHERE tenant_id = %s AND credential_ref = %s", T, BADGE):
            call("ops", "POST", "/access/credentials", {
                "holder_name": f"Visitor badge {BADGE}", "credential_type": "card", "credential_ref": BADGE})
        doors = {r["name"]: r["id"] for r in rows("SELECT id, name FROM access_doors WHERE site_id = %s", HUB_ID)}
        for door, minutes in (("Warehouse A staff door", 112), ("Warehouse A staff door", 61), ("Office block side door", 34)):
            call("ops", "POST", f"/access/doors/{doors[door]}/events", {
                "event_type": "granted", "credential_ref": BADGE, "occurred_at": ago(minutes).isoformat()})
        new += 1
    # 2. Waiting for the presenter's own answer.
    marcus, _ = _visit("Marcus Tan", "Kuehne + Nagel", "Customer audit of the bonded store", "ops", (0.5, 6))
    if _stands(visitor=marcus)["standing"] == "NOT_ASKED":
        call("g2", "POST", "/visitor-authorizations", {
            "visitor_id": str(marcus), "place_ids": _place_ids("Warehouse A", "Office block")})
        new += 1
    # 3. Declined by the host, with why.
    wong, _ = _visit("Wong Kah Wai", "Swift Couriers", "Parcel for the site office", "sup", (-2, 2))
    if _stands(visitor=wong)["standing"] == "NOT_ASKED":
        auth = call("g2", "POST", "/visitor-authorizations", {"visitor_id": str(wong)})
        call("sup", "POST", f"/visitor-authorizations/{auth['id']}/decline", {
            "reason": "Not expected. Parcels are left at the Gate 1 guardhouse, not brought in."})
        new += 1
    # 4. A contractor's work permit, asked of nobody in particular.
    firm = one("SELECT id FROM contractors WHERE tenant_id = %s AND company_name = %s", T, "Coolair Engineering Pte Ltd")
    if firm:
        permit = one("SELECT id FROM work_permits WHERE tenant_id = %s AND contractor_id = %s AND site_id = %s "
                     "AND end_at > now() AND status IN ('pending', 'approved', 'active') ORDER BY created_at DESC LIMIT 1",
                     T, firm["id"], HUB_ID)
        if permit is None:
            permit = call("ops", "POST", "/work-permits", {
                "contractor_id": str(firm["id"]), "site_id": str(HUB_ID), "work_type": "mechanical",
                "work_description": "Chiller servicing on the roof of Warehouse A", "workers_count": 3,
                "requested_by_name": USER["sup"]["full_name"],
                "start_at": _half_hour(NOW - timedelta(hours=3)).astimezone(SGT).isoformat(),
                "end_at": at(2, 18).isoformat()})
            call("ops", "PUT", f"/work-permits/{permit['id']}/approve", {})
        if _stands(permit=permit["id"])["standing"] == "NOT_ASKED":
            call("g2", "POST", "/visitor-authorizations", {
                "work_permit_id": str(permit["id"]), "escort_required": True, "place_ids": _place_ids("Warehouse A")})
            new += 1
    return None if new else False


BOOK = [
    # (kind, minutes ago, severity, words)
    ("patrol_start", 235, None, "Perimeter patrol started from Gate 1. All in order at handover."),
    ("unusual_activity", 215, "medium",
     f"White van, plate {PLATE}, parked on the road outside Gate 1 for about twenty minutes with the engine running. "
     "Two people inside. Drove off towards Jurong Pier Road when I walked over."),
    ("delivery", 170, None, "YCH lorry, 14 pallets for Warehouse A, dock 3. Seal intact and matched the delivery order."),
    ("general", 150, None, "Fence light out beside dock 2."),
    ("equipment_check", 120, None, "Radios 1 to 4 checked. Radio 3 battery holds under an hour: swapped for the spare."),
    ("visitor_arrival", 118, None, "Farah Binte Ismail, Schneider Electric, for the switchboard inspection. Work pass seen. Badge V-017."),
    ("patrol_end", 95, None, "Perimeter patrol complete. All six checkpoints scanned."),
]


def book():
    if one("SELECT 1 FROM occurrence_book_entries WHERE tenant_id = %s AND body LIKE %s AND occurred_at > now() - interval '20 hours'",
           T, "Perimeter patrol started from Gate 1%"):
        return False
    written = {}
    for kind, minutes, severity, words in BOOK:
        entry = call("g2", "POST", "/dob", {
            "entry_type": kind, "body": words, "severity": severity, "site_id": str(HUB_ID),
            "shift_id": str(SHIFT["g2"]) if SHIFT.get("g2") else None, "occurred_at": ago(minutes).isoformat()}, ok=(200, 201))
        written[kind] = entry["id"]
    # The supervisor reads the book: one noted, one to be followed up.
    call("sup", "POST", f"/occurrence-book/entries/{written['delivery']}/review", {"outcome": "NOTED"})
    call("sup", "POST", f"/occurrence-book/entries/{written['unusual_activity']}/review", {
        "outcome": "FOLLOW_UP", "note": "Second time this week. Put the plate on the watch list and brief the night shift."})
    # A mistake is put right by a further entry; the first stays as it was written.
    call("g2", "POST", f"/occurrence-book/entries/{written['general']}/correct", {
        "body": "Two fence lights out: beside dock 2 and beside dock 3.", "reason": "Miscounted on the first round."})
    return None


INSTRUCTIONS = [
    ("No hot work in Warehouse A until the sprinkler valve on level 1 has been replaced.", 3),
    ("Gate 2 is closed for resurfacing. All vehicles and deliveries through Gate 1.", None),
]


def instructions():
    new = 0
    for words, days in INSTRUCTIONS:
        if one("SELECT 1 FROM site_instructions WHERE tenant_id = %s AND body = %s AND closed_at IS NULL", T, words):
            continue
        made_ = call("sup", "POST", "/occurrence-book/instructions", {
            "site_id": str(HUB_ID), "body": words, "expires_at": (NOW + timedelta(days=days)).isoformat() if days else None})
        if days is None:
            call("g2", "POST", f"/occurrence-book/instructions/{made_['id']}/read")
        new += 1
    return None if new else False


def _incident(title: str, about: str, severity: str, camera: str, code: str, *, fresh_for_minutes: int):
    """The sample incident of this title, if one was raised recently enough to show; otherwise a new one. An older
    one that this script raised and nobody closed is resolved first, so the desk shows one of each and not a pile."""
    live = rows("SELECT id, created_at > now() - make_interval(mins => %s) AS fresh FROM incidents "
                "WHERE tenant_id = %s AND title = %s AND alert_code = %s AND status NOT IN ('resolved', 'closed') "
                "ORDER BY created_at DESC", fresh_for_minutes, T, title, code)
    for old in live[1:] + [r for r in live[:1] if not r["fresh"]]:
        call("ops", "PUT", f"/incidents/{old['id']}/status", {
            "status": "resolved", "notes": "Sample incident closed before the next demonstration."})
    if live and live[0]["fresh"]:
        return live[0]["id"], False
    row = call("ops", "POST", "/incidents", {"title": title, "description": about, "severity": severity,
                                             "camera_id": str(CAMERA[camera])})
    # A hand-raised incident has no kind, and the procedure for an incident is found by its kind.
    db.execute("UPDATE incidents SET alert_code = %s WHERE id = %s", (code, row["id"]))
    return row["id"], True


def answered_incident():
    """An incident a guard was sent to and has dealt with: the whole of a response, to read back."""
    incident, fresh = _incident(
        "Intrusion: person in the restricted lane at Gate 1",
        "Somebody on foot inside the vehicle lane at Gate 1, walking towards the loading docks.",
        "high", "Gate 1 (Vehicles)", "intrusion.zone_breach", fresh_for_minutes=20 * 60)
    if not fresh:
        return False
    call("ops", "POST", f"/dispatch/incidents/{incident}", {
        "guard_user_id": str(USER["g2"]["id"]), "dispatch_notes": "Nearest to Gate 1. Check the lane and docks 1 to 3."})
    near = {"latitude": LAT - 0.00052, "longitude": LNG - 0.00056}
    call("g2", "POST", f"/incident-responses/{incident}/accept", near)
    call("g2", "POST", f"/incident-responses/{incident}/en-route", near)
    call("g2", "POST", f"/incident-responses/{incident}/arrived", {"latitude": LAT - 0.00054, "longitude": LNG - 0.00060})
    call("g2", "POST", f"/incident-responses/{incident}/report", {
        "note": "A delivery driver on foot looking for the site office. Walked him back to the guardhouse and signed him in. "
                "Nothing taken, nothing damaged."})
    return None


def unanswered_incident():
    """One nobody has been sent to yet: for 'who to send', and for the clocks."""
    _, fresh = _incident(
        "Person down on the warehouse floor",
        "The fall detector on the Warehouse Floor camera: one person on the ground beside racking aisle 4, not moving.",
        "critical", "Warehouse Floor", "fall.person_fallen", fresh_for_minutes=45)
    return None if fresh else False


def live_situation():
    """Two alerts at the Hub half a minute apart, for the intelligence layer to relate into one situation that
    waits for a person's decision. A situation settles by itself after a while, so this is made last."""
    if one("SELECT 1 FROM security_situations WHERE tenant_id = %s AND site_id = %s AND status = 'ACTIVE' "
           "AND started_at > now() - interval '2 hours'", T, HUB_ID):
        return False
    sample = "Sample alert raised for the demonstration."
    call("ops", "POST", "/alerts", {"camera_id": str(CAMERA["Gate 1 (Vehicles)"]), "module_type": "intrusion",
                                    "severity": "critical", "title": "Person on foot in the vehicle lane at Gate 1",
                                    "message": sample})
    time.sleep(25)
    call("ops", "POST", "/alerts", {"camera_id": str(CAMERA["Warehouse Floor"]), "module_type": "behavior",
                                    "severity": "critical", "title": "Person loitering beside the loading docks",
                                    "message": sample})
    return None


def summaries():
    new = 0
    for who, confirm in (("g1", True), ("g2", False)):
        shift = SHIFT.get(who)
        if not shift or one("SELECT 1 FROM shift_handover_summaries WHERE shift_id = %s AND state <> 'DISCARDED'", shift):
            continue
        drafted = call(who, "POST", "/occurrence-book/shift-summaries", {"shift_id": str(shift)})
        if confirm:
            call(who, "POST", f"/occurrence-book/shift-summaries/{drafted['id']}/confirm")
        new += 1
    return None if new else False


def investigation():
    title = f"White van waiting outside Gate 1: {PLATE}"
    if one("SELECT 1 FROM investigations WHERE tenant_id = %s AND title = %s", T, title):
        return False
    file = call("ops", "POST", "/investigations", {
        "title": title, "site_id": str(HUB_ID),
        "reason": "The gate guard reported the same van waiting outside Gate 1 on two days. Establish where else it has been seen."})
    found = call("ops", "POST", "/investigations/search", {"plate": PLATE, "from": at(-4, 0).isoformat(), "limit": 50})
    hits = [{"kind": h["kind"], "id": h["id"], "occurred_at": h["occurred_at"]} for h in found.get("items", [])]
    if hits:
        call("ops", "POST", f"/investigations/{file['id']}/items", {
            "records": hits, "note": "Every read of the plate by the gate and car park cameras."})
    words = call("ops", "POST", "/investigations/search", {"text": PLATE, "kinds": ["OCCURRENCE"], "from": at(-4, 0).isoformat()})
    entries = [{"kind": h["kind"], "id": h["id"], "occurred_at": h["occurred_at"]} for h in words.get("items", [])][:3]
    if entries:
        call("ops", "POST", f"/investigations/{file['id']}/items", {"records": entries, "note": "The gate guard's own entry."})
    call("ops", "POST", f"/investigations/{file['id']}/notes", {
        "note": "Read at Gate 1 at night two days ago, at the Marina Bay Tower car park yesterday afternoon, and outside "
                "Gate 1 again this morning. Same van at two of our sites. Plate put on the watch list; night shift briefed."})
    return None


def evidence_package():
    title = "Intrusion at Marina Bay Tower: frames kept for the client"
    if one("SELECT 1 FROM evidence_packages WHERE tenant_id = %s AND title = %s AND status <> 'DRAFT'", T, title):
        return False
    # An incident whose detection still has its frame, taken at the moment of the incident: a package is offered
    # what was kept around the time of its records, and a frame from long before is not among it.
    incident = one("""
        SELECT i.id FROM incidents i JOIN alerts a ON a.id = i.alert_id JOIN cameras c ON c.id = i.camera_id
         WHERE i.tenant_id = %s AND i.alert_code = 'intrusion.zone_breach' AND c.site_id = %s
           AND EXISTS (SELECT 1 FROM evidence e WHERE e.detection_id = a.detection_id
                          AND e.captured_at BETWEEN i.created_at - interval '2 minutes' AND i.created_at + interval '2 minutes')
         ORDER BY i.created_at DESC LIMIT 1""", T, TOWER_ID)
    if incident is None:
        raise Refused("No intrusion incident at Marina Bay Tower still has the frame of its detection.")
    package = one("SELECT id FROM evidence_packages WHERE tenant_id = %s AND title = %s AND status = 'DRAFT' "
                  "AND incident_id = %s", T, title, incident["id"])
    if package is None:
        package = call("ops", "POST", "/evidence-packages", {
            "title": title, "incident_id": str(incident["id"]),
            "purpose": "The client's property manager asked for what was recorded of this intrusion."})
    offered = call("ops", "GET", f"/evidence-packages/{package['id']}/candidates").get("items", [])
    if not offered:
        raise Refused("The incident has nothing kept that this account may package.")
    call("ops", "POST", f"/evidence-packages/{package['id']}/items", {
        "items": [{"kind": c["kind"], "id": c["id"], "captured_at": c["captured_at"]} for c in offered[:4]],
        "note": "The frames of the detection, as the camera recorded them."})
    call("ops", "POST", f"/evidence-packages/{package['id']}/seal")
    return None


def today() -> None:
    do("Today's shifts of Rajesh Kumar, Tan Wei Ming and David Lim started at the Hub", shifts)
    do("Four visits today: one approved and on site, one waiting for Priya Nair, one declined, one work permit", visits)
    do("Seven occurrence book entries, two reviews and a correction", book)
    do("Two instructions in force at the Hub", instructions)
    do("An incident a guard was sent to and has reported on", answered_incident)
    do("A shift summary confirmed (Tan Wei Ming) and one in draft (Rajesh Kumar)", summaries)
    do(f"An investigation into {PLATE}, with the plate reads and the guard's entry filed", investigation)
    do("A sealed evidence package for an intrusion at Marina Bay Tower", evidence_package)
    do("An incident nobody has been sent to yet", unanswered_incident)


# ─── Run ─────────────────────────────────────────────────────────────────────

sign_in()
if STEP in ("all", "setup"):
    setup()
if STEP in ("all", "today"):
    today()
if STEP == "evidence":
    do("A sealed evidence package for an intrusion at Marina Bay Tower", evidence_package)
if STEP in ("live", "incident"):
    do("An incident nobody has been sent to yet", unanswered_incident)
    do("Two alerts for the intelligence layer to relate into a situation awaiting a decision", live_situation)

print(f"\nDemo data for '{ORG}' — step: {STEP} — {NOW.astimezone(SGT):%d %b %Y %H:%M} SGT")
for heading, lines in (("MADE", made), ("ALREADY THERE", kept), ("NOT DONE", failed)):
    print(f"\n{heading} ({len(lines)})")
    for line in lines:
        print("  -", line)
sys.exit(1 if failed else 0)
