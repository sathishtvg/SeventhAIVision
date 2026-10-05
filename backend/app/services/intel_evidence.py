"""Evidence: what was kept about a situation, and where it is opened.

REFERENCES, NEVER MEDIA. The platform already keeps evidence, and keeps it
carefully: frames and clips in `evidence` with a checksum, recordings with
integrity verification, a drone's media under the drone module, a virtual
patrol's snapshot under that module. This layer copies none of it, stores
nothing about it and serves no file. It answers one question — *which of those
things belong to this situation* — and for each says what it is, when, which of
the situation's events it goes with, its checksum where the platform recorded
one, and the EXISTING endpoint that serves it with the permission that endpoint
asks for.

A STORAGE PATH NEVER LEAVES. No `storage_path` or `file_path` is selected into
anything this module returns; a path handed to a client is an invitation to
walk the directory.

FOUND BY WHAT THE RECORDS ALREADY SAY. A frame belongs because it is the frame
of a detection one of the situation's events came from, or of its incident. A
recording belongs because it is that camera's recording and was running when
the event happened. A drone's media belongs to the drone sighting. A patrol's
snapshot is the one taken at the check that found the exception. Nothing is
matched by guesswork, and a recording says how far into it the event is.

OPENING IS A PERSON'S ACT, THROUGH THE PLATFORM'S OWN DOOR. `opening()` says
what opening one item would take and where it is served; the API then writes
the existing chain-of-custody entry (for an `evidence` row) and an audit entry
naming the situation, and the client fetches from the existing endpoint. The
runner never opens anything: it does not import this module.

`collect()` and `opening()` only read.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

KINDS = ("SNAPSHOT", "CLIP", "RECORDING", "DRONE_MEDIA", "PATROL_SNAPSHOT")
#: The existing permission each kind's own endpoint asks for.
NEEDS = {"SNAPSHOT": "evidence:read", "CLIP": "evidence:read", "RECORDING": "recording:read",
         "DRONE_MEDIA": "drone:event:read", "PATROL_SNAPSHOT": "vpatrol:read"}
#: How each existing endpoint logs the opening, so the screen can say so.
CUSTODY = {"SNAPSHOT": "evidence_access_log", "CLIP": "evidence_access_log", "RECORDING": "audit_log",
           "DRONE_MEDIA": "audit_log", "PATROL_SNAPSHOT": "audit_log"}
#: What a capture is, in words, by the platform's own `capture_kind`.
CAPTURE_WORDS = {"frame": "Frame at the detection", "plate_crop": "Number plate, cropped",
                 "face_crop": "Face, cropped"}
#: A drone's media, by the drone module's own `media_kind`.
DRONE_WORDS = {"SNAPSHOT": "Drone snapshot", "PRE_CLIP": "Drone clip from before the sighting",
               "EVENT_CLIP": "Drone clip of the sighting", "POST_CLIP": "Drone clip from after the sighting",
               "CLIP": "Drone clip"}
#: How far either side of a situation its frames and clips are looked for.
MARGIN = timedelta(hours=1)
LIMIT = 200


def served_at(kind: str, item: Mapping) -> dict:
    """Where the platform's own endpoint serves this item, for showing it on a
    screen. `token_in_query` is true where that endpoint takes the caller's
    token in the query string, because an image or a video tag cannot carry a
    header; each of those endpoints checks the token's permission itself."""
    if kind in ("SNAPSHOT", "CLIP"):
        return {"path": f"/api/v1/evidence/{item['id']}/image", "token_in_query": True}
    if kind == "RECORDING":
        return {"path": f"/api/v1/recordings/{item['id']}/play", "token_in_query": True}
    if kind == "DRONE_MEDIA":
        return {"path": f"/api/v1/drone-media/{item['id']}/file", "token_in_query": False}
    return {"path": f"/api/v1/virtual-patrol/sessions/{item['session_id']}/cameras/{item['id']}/snapshot",
            "token_in_query": True}


def _item(kind: str, row: Mapping, *, what: str, at: Any, event_id: Any, mine: set[str], **extra) -> dict:
    return {
        "kind": kind, "id": row["id"], "what": what, "captured_at": at, "event_id": event_id,
        "camera_name": row.get("camera_name"), "checksum_sha256": row.get("checksum_sha256"),
        "kept": row.get("storage_location"), "sync_state": row.get("sync_state"),
        "needs": NEEDS[kind], "may_open": NEEDS[kind] in mine, "logged_in": CUSTODY[kind],
        "served_at": served_at(kind, row), **extra,
    }


def offset_into(recording: Mapping, at: datetime) -> int | None:
    """How many seconds into a recording a moment is, or None when the
    recording was not running then."""
    start, end = recording["started_at"], recording.get("ended_at")
    if at < start or (end is not None and at > end):
        return None
    return int((at - start).total_seconds())


def _attrs(event: Mapping) -> dict:
    a = event.get("attributes")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = {}
    return a if isinstance(a, dict) else {}


async def collect(db: AsyncSession, situation: Mapping, mine: set[str], now: datetime | None = None) -> list[dict]:
    """Everything the platform kept that belongs to this situation, oldest
    first. `mine` is the caller's permissions: each item says whether its own
    endpoint would let them open it. The session must be scoped to the tenant."""
    now = now or datetime.now(timezone.utc)
    sid = situation["id"]
    events = [dict(r) for r in (await db.execute(text("""
        SELECT e.id, e.source_table, e.source_id, e.occurred_at, e.title, e.camera_id, cam.name AS camera_name,
               e.detection_id, e.incident_id, e.attributes, l.is_duplicate
          FROM security_situation_events l
          JOIN security_events e ON e.id = l.event_id
          LEFT JOIN cameras cam ON cam.id = e.camera_id
         WHERE l.situation_id = :s ORDER BY e.occurred_at, e.id
    """), {"s": sid})).mappings().all()]
    if not events:
        return []
    first, last = events[0]["occurred_at"], events[-1]["occurred_at"]
    until = (situation.get("closed_at") or now) + MARGIN
    items: list[dict] = []

    # ── Frames and clips: of a detection an event came from, or of the incident ──
    by_detection = {e["detection_id"]: e for e in reversed(events) if e.get("detection_id") is not None}
    incidents = {e["incident_id"] for e in events if e.get("incident_id") is not None}
    if situation.get("incident_id") is not None:
        incidents.add(situation["incident_id"])
    if by_detection or incidents:
        rows = (await db.execute(text("""
            SELECT ev.id, ev.detection_id, ev.incident_id, ev.media_type, ev.capture_kind, ev.captured_at,
                   ev.checksum_sha256, ev.storage_location, ev.sync_state
              FROM evidence ev
             WHERE ev.captured_at BETWEEN :a AND :b
               AND (ev.detection_id = ANY(CAST(:detections AS uuid[]))
                    OR ev.incident_id = ANY(CAST(:incidents AS uuid[])))
             ORDER BY ev.captured_at, ev.id LIMIT :n
        """), {"a": first - MARGIN, "b": until, "detections": list(by_detection), "incidents": list(incidents),
               "n": LIMIT})).mappings().all()
        for r in rows:
            event = by_detection.get(r["detection_id"])
            kind = "CLIP" if r["media_type"] == "video" else "SNAPSHOT"
            what = "Clip of the detection" if kind == "CLIP" else CAPTURE_WORDS.get(r["capture_kind"] or "", "Snapshot")
            if event is None:
                what += " — kept with the incident"
            items.append(_item(kind, {**dict(r), "camera_name": event["camera_name"] if event else None},
                               what=what, at=r["captured_at"], event_id=event["id"] if event else None, mine=mine,
                               media_type=r["media_type"]))

    # ── Recordings: that camera's, running when the event happened ───────────
    seen = [e for e in events if e.get("camera_id") is not None and not e.get("is_duplicate")]
    if seen:
        rows = (await db.execute(text("""
            SELECT r.id, r.camera_id, c.name AS camera_name, r.started_at, r.ended_at, r.status,
                   r.duration_seconds, r.checksum_sha256, r.checksum_status, r.storage_location, r.sync_state
              FROM recordings r JOIN cameras c ON c.id = r.camera_id
             WHERE r.camera_id = ANY(CAST(:cameras AS uuid[]))
               AND r.started_at <= :last AND COALESCE(r.ended_at, :now) >= :first
             ORDER BY r.started_at, r.id LIMIT :n
        """), {"cameras": list({e["camera_id"] for e in seen}), "first": first, "last": last, "now": now,
               "n": LIMIT})).mappings().all()
        for r in rows:
            within = [(e, offset_into(r, e["occurred_at"])) for e in seen if e["camera_id"] == r["camera_id"]]
            within = [(e, off) for e, off in within if off is not None]
            if not within:
                continue
            event, offset = within[0]
            items.append(_item("RECORDING", r, what=f"Recording of {r['camera_name']}", at=r["started_at"],
                               event_id=event["id"], mine=mine, media_type="video", ended_at=r["ended_at"],
                               offset_seconds=offset, status=r["status"], checksum_status=r["checksum_status"]))

    # ── A drone's media: of the drone sighting itself ────────────────────────
    sightings = {e["source_id"]: e for e in events if e["source_table"] == "drone_events"}
    if sightings:
        rows = (await db.execute(text("""
            SELECT m.id, m.event_id, m.media_kind, m.captured_at, m.checksum_sha256, m.duration_seconds,
                   m.storage_location, m.sync_state
              FROM drone_event_media m
             WHERE m.event_id = ANY(CAST(:events AS uuid[]))
             ORDER BY m.captured_at, m.id LIMIT :n
        """), {"events": list(sightings), "n": LIMIT})).mappings().all()
        for r in rows:
            kind_of = str(r["media_kind"] or "")
            items.append(_item("DRONE_MEDIA", r, what=DRONE_WORDS.get(kind_of, "Drone media"), at=r["captured_at"],
                               event_id=sightings[r["event_id"]]["id"], mine=mine,
                               media_type="video" if "CLIP" in kind_of else "image",
                               duration_seconds=r["duration_seconds"]))

    # ── A virtual patrol's snapshot: taken at the check that found the exception ──
    checks = {}
    for e in events:
        if e["source_table"] == "virtual_patrol_session_answers" and _attrs(e).get("session_camera_id"):
            checks.setdefault(_attrs(e)["session_camera_id"], e)
    if checks:
        rows = (await db.execute(text("""
            SELECT sc.id, sc.session_id, sc.camera_name, sc.snapshot_taken_at, sc.snapshot_checksum AS checksum_sha256
              FROM virtual_patrol_session_cameras sc
             WHERE sc.id = ANY(CAST(:ids AS uuid[])) AND sc.snapshot_path IS NOT NULL
             ORDER BY sc.snapshot_taken_at, sc.id
        """), {"ids": list(checks)})).mappings().all()
        for r in rows:
            event = checks[str(r["id"])]
            items.append(_item("PATROL_SNAPSHOT", r, what="Snapshot taken at the virtual patrol's check",
                               at=r["snapshot_taken_at"] or event["occurred_at"], event_id=event["id"], mine=mine,
                               media_type="image"))

    items.sort(key=lambda i: (i["captured_at"], KINDS.index(i["kind"]), str(i["id"])))
    return items


def opening(items: list[Mapping], kind: str, item_id: Any) -> dict | None:
    """The item of this situation that a person is asking to open, or None when
    it is not one of the situation's — whatever else it may be."""
    return next((dict(i) for i in items if i["kind"] == kind and str(i["id"]) == str(item_id)), None)


def summary(items: list[Mapping]) -> dict:
    """How much of each kind there is, and how much of it the caller may open."""
    out = {kind: 0 for kind in KINDS}
    for i in items:
        out[i["kind"]] += 1
    return {"total": len(items), "by_kind": out, "may_open": sum(1 for i in items if i["may_open"])}
