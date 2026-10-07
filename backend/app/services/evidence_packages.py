"""Evidence packages: finding what was kept, sealing it, and handing it over intact.

  WHAT BELONGS      `candidates` — the frames, clips, recordings and drone media
                    that go with a set of records, found by what the records
                    already say: the frame OF that detection, the recording of
                    THAT camera running at THAT moment. Nothing is matched by
                    guesswork.
  WHAT IT IS NOW    `things` — the same items read where they live, as the
                    caller may see them.
  THE SEAL          `manifest`, `canonical`, `digest` — everything in a package
                    written one way only, and the SHA-256 of that.
  THE EXPORT        `build_export` — a ZIP of the sealed manifest, each original
                    file byte for byte with its checksum re-computed and
                    compared, and a marked viewing copy of each picture.

REFERENCES, NEVER MEDIA, UNTIL AN EXPORT. The platform already keeps evidence
and keeps it carefully. A package refers to it. The only time bytes are read is
when a person with the permission exports a sealed package, and then they are
read to be handed over and compared with the checksum recorded for them.

A STORAGE PATH NEVER LEAVES. Paths are read here, to open files, and are held
under the private key `_path`, which `public()` removes. Nothing a route
returns has passed anywhere but through `public()`.

THE ORIGINAL IS NOT MARKED. A watermark changes a file and so changes its
checksum; an original that no longer matches its checksum is no longer
evidence. Originals are exported untouched. A picture also gets a second,
marked copy for showing to people, which says on its face that it is not the
original.

The wording and the permission each kind needs are the intelligence layer's
own (`intel_evidence`), so that the two never describe one thing differently.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.dependencies.sites import site_scope_clause
from app.services.intel_evidence import CAPTURE_WORDS, CUSTODY, DRONE_WORDS, NEEDS as _NEEDS, offset_into, served_at

KINDS = ("SNAPSHOT", "CLIP", "RECORDING", "DRONE_MEDIA")
#: The existing permission each kind's own endpoint asks for.
NEEDS = {kind: _NEEDS[kind] for kind in KINDS}
#: The most items one package holds.
MAX_ITEMS = 200
#: How far either side of a record its frames and clips are looked for.
MARGIN = timedelta(hours=1)
#: The most one export carries. A file that would take it past this is listed
#: in the export as left out, with its checksum, and is fetched from where it lives.
MAX_EXPORT_BYTES = int(os.environ.get("EVIDENCE_EXPORT_MAX_MB", "1024")) * 1024 * 1024
RECORDINGS_ROOT = os.environ.get("RECORDINGS_ROOT", "/data/recordings")
MANIFEST_FORMAT = "seventh-evidence-manifest/1"
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Anchor:
    """A record evidence can belong to."""
    kind: str
    id: str
    at: datetime
    detection_id: str | None = None
    camera_id: str | None = None


def anchors_of(records: Iterable[Mapping]) -> list[Anchor]:
    """The anchors in a set of records read by `investigation_sources`."""
    return [Anchor(kind=r["kind"], id=str(r["id"]), at=r["occurred_at"],
                   detection_id=str(r["detection_id"]) if r.get("detection_id") else None,
                   camera_id=str(r["camera_id"]) if r.get("camera_id") else None) for r in records]


# ─── Reading the things themselves ───────────────────────────────────────────

_EVIDENCE = """
    SELECT ev.id, ev.media_type, ev.capture_kind, ev.captured_at, ev.checksum_sha256, ev.storage_location,
           ev.sync_state, ev.detection_id, ev.incident_id, d.camera_id, c.name AS camera_name,
           COALESCE(c.site_id, ev.site_id) AS site_id, st.name AS site_name, ev.storage_path
      FROM evidence ev
      LEFT JOIN LATERAL (
          SELECT camera_id FROM detections
           WHERE id = ev.detection_id
             AND detected_at BETWEEN ev.captured_at - INTERVAL '1 hour' AND ev.captured_at + INTERVAL '1 hour'
           LIMIT 1) d ON TRUE
      LEFT JOIN cameras c ON c.id = d.camera_id
      LEFT JOIN sites st ON st.id = COALESCE(c.site_id, ev.site_id)
"""
_RECORDING = """
    SELECT r.id, r.camera_id, c.name AS camera_name, COALESCE(r.site_id, c.site_id) AS site_id, st.name AS site_name,
           r.started_at, r.ended_at, r.status, r.duration_seconds, r.file_size_bytes, r.checksum_sha256,
           r.checksum_status, r.storage_location, r.sync_state, r.file_path
      FROM recordings r
      JOIN cameras c ON c.id = r.camera_id
      LEFT JOIN sites st ON st.id = COALESCE(r.site_id, c.site_id)
"""
_DRONE = """
    SELECT m.id, m.event_id, m.media_kind, m.captured_at, m.checksum_sha256, m.size_bytes, m.duration_seconds,
           m.storage_location, m.sync_state, COALESCE(e.site_id, s.site_id) AS site_id, st.name AS site_name,
           m.storage_path
      FROM drone_event_media m
      LEFT JOIN drone_events e ON e.id = m.event_id
      LEFT JOIN drone_patrol_sessions s ON s.id = m.session_id
      LEFT JOIN sites st ON st.id = COALESCE(e.site_id, s.site_id)
"""
_EVIDENCE_SITE = "COALESCE(c.site_id, ev.site_id)"
_RECORDING_SITE = "COALESCE(r.site_id, c.site_id)"
_DRONE_SITE = "COALESCE(e.site_id, s.site_id)"


def _thing(kind: str, row: Mapping, mine, *, what: str, at: Any, media_type: str, path: str | None,
           **more) -> dict:
    return {
        "kind": kind, "id": str(row["id"]), "what": what, "captured_at": at, "media_type": media_type,
        "site_id": row.get("site_id"), "site_name": row.get("site_name"), "camera_id": row.get("camera_id"),
        "camera_name": row.get("camera_name"), "checksum_sha256": row.get("checksum_sha256"),
        "kept": row.get("storage_location"), "sync_state": row.get("sync_state"),
        "size_bytes": more.pop("size_bytes", None), "duration_seconds": more.pop("duration_seconds", None),
        "needs": NEEDS[kind], "may_open": NEEDS[kind] in mine, "logged_in": CUSTODY[kind],
        "served_at": served_at(kind, row), "_path": path, **more,
    }


def _of_evidence(row: Mapping, mine) -> dict:
    kind = "CLIP" if row["media_type"] == "video" else "SNAPSHOT"
    what = "Clip of the detection" if kind == "CLIP" else CAPTURE_WORDS.get(row["capture_kind"] or "", "Snapshot")
    if row.get("detection_id") is None and row.get("incident_id") is not None:
        what += " — kept with the incident"
    return _thing(kind, row, mine, what=what, at=row["captured_at"], media_type=row["media_type"],
                  path=row["storage_path"], detection_id=row.get("detection_id"),
                  incident_id=row.get("incident_id"))


def _of_recording(row: Mapping, mine) -> dict:
    return _thing("RECORDING", row, mine, what=f"Recording of {row['camera_name']}", at=row["started_at"],
                  media_type="video", path=row["file_path"], size_bytes=row["file_size_bytes"],
                  duration_seconds=row["duration_seconds"], ended_at=row["ended_at"], status=row["status"],
                  checksum_status=row["checksum_status"])


def _of_drone(row: Mapping, mine) -> dict:
    media_kind = str(row["media_kind"] or "")
    duration = row["duration_seconds"]
    return _thing("DRONE_MEDIA", row, mine, what=DRONE_WORDS.get(media_kind, "Drone media"), at=row["captured_at"],
                  media_type="video" if "CLIP" in media_kind else "image", path=row["storage_path"],
                  size_bytes=row["size_bytes"], duration_seconds=float(duration) if duration is not None else None,
                  drone_event_id=row.get("event_id"))


def public(thing: Mapping | None) -> dict | None:
    """A thing as a route may return it: everything but where the file is."""
    return None if thing is None else {k: v for k, v in thing.items() if not k.startswith("_")}


async def things(db: AsyncSession, refs: Sequence[tuple[str, Any, datetime]], mine, allowed: list[str] | None,
                 ) -> dict[tuple[str, str], dict]:
    """The items `refs` point at — (kind, id, when it was captured) — as the
    caller may see them now, keyed by (kind, id). A kind the caller's
    permissions do not reach is not read at all; an item outside their sites,
    or no longer held, is simply absent."""
    wanted: dict[str, list[tuple[uuid.UUID, datetime]]] = {}
    for kind, ref_id, at in refs:
        if kind in NEEDS and NEEDS[kind] in mine:
            wanted.setdefault("EVIDENCE" if kind in ("SNAPSHOT", "CLIP") else kind, []).append(
                (uuid.UUID(str(ref_id)), at))
    out: dict[tuple[str, str], dict] = {}

    async def read(sql: str, site: str, where: str, pairs, build) -> None:
        params: dict = {"ids": [i for i, _ in pairs]}
        scope = site_scope_clause(allowed, site, params)
        rows = await db.execute(text(f"{sql} WHERE {where}" + (f" AND {scope}" if scope else "")), {
            **params, "a": min(at for _, at in pairs) - timedelta(seconds=1),
            "b": max(at for _, at in pairs) + timedelta(seconds=1)})
        for row in rows.mappings():
            thing = build(row, mine)
            out[(thing["kind"], thing["id"])] = thing

    if wanted.get("EVIDENCE"):
        # The time is given back to the database: `evidence` is partitioned by it.
        await read(_EVIDENCE, _EVIDENCE_SITE, "ev.id = ANY(:ids) AND ev.captured_at BETWEEN :a AND :b",
                   wanted["EVIDENCE"], _of_evidence)
    if wanted.get("RECORDING"):
        await read(_RECORDING, _RECORDING_SITE, "r.id = ANY(:ids) AND r.started_at BETWEEN :a AND :b",
                   wanted["RECORDING"], _of_recording)
    if wanted.get("DRONE_MEDIA"):
        await read(_DRONE, _DRONE_SITE, "m.id = ANY(:ids) AND m.captured_at BETWEEN :a AND :b",
                   wanted["DRONE_MEDIA"], _of_drone)
    return out


async def candidates(db: AsyncSession, anchors: Sequence[Anchor], mine, allowed: list[str] | None, *,
                     now: datetime | None = None, limit: int = MAX_ITEMS) -> list[dict]:
    """Everything the platform kept that belongs to these records, oldest
    first. Each says which record it goes with."""
    if not anchors:
        return []
    now = now or datetime.now(timezone.utc)
    first, last = min(a.at for a in anchors), max(a.at for a in anchors)
    found: list[dict] = []

    def scoped(site: str, params: dict) -> str:
        scope = site_scope_clause(allowed, site, params)
        return f" AND {scope}" if scope else ""

    # ── Frames and clips: of a detection a record came from, or of the incident ──
    by_detection = {a.detection_id: a for a in reversed(anchors) if a.detection_id}
    incidents = {a.id: a for a in anchors if a.kind == "INCIDENT"}
    if (by_detection or incidents) and NEEDS["SNAPSHOT"] in mine:
        params: dict = {"a": first - MARGIN, "b": last + MARGIN, "n": limit,
                        "detections": [uuid.UUID(d) for d in by_detection],
                        "incidents": [uuid.UUID(i) for i in incidents]}
        rows = await db.execute(text(f"""{_EVIDENCE}
             WHERE ev.captured_at BETWEEN :a AND :b
               AND (ev.detection_id = ANY(:detections) OR ev.incident_id = ANY(:incidents))
               {scoped(_EVIDENCE_SITE, params)}
             ORDER BY ev.captured_at, ev.id LIMIT :n"""), params)
        for row in rows.mappings():
            thing = _of_evidence(row, mine)
            anchor = by_detection.get(str(row["detection_id"])) or incidents.get(str(row["incident_id"]))
            found.append({**thing, "goes_with": {"kind": anchor.kind, "id": anchor.id}})

    # ── Recordings: that camera's, running when the record happened ──────────
    seen = [a for a in anchors if a.camera_id]
    if seen and NEEDS["RECORDING"] in mine:
        params = {"cameras": list({uuid.UUID(a.camera_id) for a in seen}), "first": first, "last": last,
                  "now": now, "n": limit}
        rows = await db.execute(text(f"""{_RECORDING}
             WHERE r.camera_id = ANY(:cameras) AND r.status = 'completed'
               AND r.started_at <= :last AND COALESCE(r.ended_at, :now) >= :first
               {scoped(_RECORDING_SITE, params)}
             ORDER BY r.started_at, r.id LIMIT :n"""), params)
        for row in rows.mappings():
            within = [(a, offset_into(row, a.at)) for a in seen if a.camera_id == str(row["camera_id"])]
            within = [(a, off) for a, off in within if off is not None]
            if within:
                anchor, offset = within[0]
                found.append({**_of_recording(row, mine), "offset_seconds": offset,
                              "goes_with": {"kind": anchor.kind, "id": anchor.id}})

    # ── A drone's media: of the drone sighting itself ────────────────────────
    sightings = {a.id: a for a in anchors if a.kind == "DRONE"}
    if sightings and NEEDS["DRONE_MEDIA"] in mine:
        params = {"events": [uuid.UUID(i) for i in sightings], "n": limit}
        rows = await db.execute(text(f"""{_DRONE}
             WHERE m.event_id = ANY(:events) {scoped(_DRONE_SITE, params)}
             ORDER BY m.captured_at, m.id LIMIT :n"""), params)
        for row in rows.mappings():
            anchor = sightings[str(row["event_id"])]
            found.append({**_of_drone(row, mine), "goes_with": {"kind": anchor.kind, "id": anchor.id}})

    found.sort(key=lambda t: (t["captured_at"], KINDS.index(t["kind"]), t["id"]))
    return found[:limit]


# ─── The seal ────────────────────────────────────────────────────────────────

def _plain(value: Any) -> Any:
    """A value as JSON holds it, so that what is hashed is what is stored."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (uuid.UUID, Path)):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, float) and value == int(value):
        return int(value)
    return value


def manifest(package: Mapping, items: Sequence[Mapping], *, sealed_at: datetime, sealed_by: str | None) -> dict:
    """Everything in the package as it stands. `items` are (the stored item,
    merged with the thing read now), oldest first."""
    listed = []
    for n, item in enumerate(items, start=1):
        listed.append({
            "n": n, "kind": item["kind"], "id": str(item["id"]), "what": item["what"],
            "captured_at": item["captured_at"], "ended_at": item.get("ended_at"), "site": item.get("site_name"),
            "camera": item.get("camera_name"), "media_type": item.get("media_type"),
            # The checksum the platform had recorded. Its absence is said, not hidden.
            "checksum_sha256": item.get("checksum_sha256"),
            "checksum_recorded": bool(item.get("checksum_sha256")),
            "size_bytes": item.get("size_bytes"), "duration_seconds": item.get("duration_seconds"),
            "kept": item.get("kept"), "note": item.get("note"),
        })
    return _plain({
        "format": MANIFEST_FORMAT, "package_number": package["package_number"], "title": package["title"],
        "purpose": package["purpose"], "site": package.get("site_name"),
        "investigation": package.get("investigation_number"), "incident_id": package.get("incident_id"),
        "created_at": package["created_at"], "created_by": package.get("created_by_name"),
        "sealed_at": sealed_at, "sealed_by": sealed_by, "items": listed,
        "counts": {kind: sum(1 for i in listed if i["kind"] == kind) for kind in KINDS},
        "without_checksum": sum(1 for i in listed if not i["checksum_recorded"]),
    })


def canonical(document: Mapping) -> bytes:
    """One way of writing a manifest, and only one: keys in order, nothing
    padded, UTF-8. What is hashed, and what is put in an export."""
    return json.dumps(_plain(document), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(document: Mapping) -> str:
    return hashlib.sha256(canonical(document)).hexdigest()


def intact(package: Mapping) -> bool | None:
    """Whether a sealed package's manifest still has the hash it was sealed
    with. None for a draft, which has neither."""
    if package.get("status") != "SEALED" or package.get("manifest") is None:
        return None
    stored = package["manifest"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    return digest(stored) == package.get("manifest_sha256")


# ─── The files ───────────────────────────────────────────────────────────────

def local_path(kind: str, stored: str | None) -> Path | None:
    """Where a file is on this deployment's own disk — or None when the path
    is missing, or points outside the directory that kind is kept in."""
    if not stored:
        return None
    root = Path(RECORDINGS_ROOT if kind == "RECORDING" else settings.EVIDENCE_ROOT).resolve()
    path = (root / stored).resolve()
    return path if root in path.parents else None


def _fetch(kind: str, thing: Mapping, workdir: Path) -> tuple[Path | None, str | None]:
    """(a file to read, or None; why not). Recordings are files on the shared
    disk whatever the evidence store is."""
    if thing.get("kept") not in (None, "central"):
        return None, "It is held at the site and has not been uploaded."
    if kind != "RECORDING" and settings.STORAGE_BACKEND == "s3":
        if not thing.get("_path"):
            return None, "The platform holds no file for it."
        from app.core.object_store import _get_client

        target = workdir / f"{thing['id']}.bin"
        try:
            _get_client().download_file(settings.S3_BUCKET, thing["_path"], str(target))
        except Exception:  # noqa: BLE001 — whatever the store says, the item is not in this export
            return None, "The file could not be fetched from the object store."
        return target, None
    path = local_path(kind, thing.get("_path"))
    if path is None or not path.is_file():
        return None, "The file is no longer on disk."
    return path, None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def watermark(image: bytes, line: str) -> bytes:
    """A copy of a picture with a band across the foot saying what it is. For
    showing to people. It is not the original and says so."""
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(io.BytesIO(image)) as opened:
        picture = opened.convert("RGB")
    size = max(12, picture.width // 60)
    font = ImageFont.load_default(size=size)
    band = size * 2
    marked = Image.new("RGB", (picture.width, picture.height + band), (0, 0, 0))
    marked.paste(picture, (0, 0))
    ImageDraw.Draw(marked).text((size // 2, picture.height + size // 2), line, fill=(255, 255, 255), font=font)
    out = io.BytesIO()
    marked.save(out, format="JPEG", quality=88)
    return out.getvalue()


def _extension(thing: Mapping) -> str:
    stored = str(thing.get("_path") or "")
    suffix = Path(stored).suffix.lower()
    if suffix and len(suffix) <= 5:
        return suffix
    return ".jpg" if thing.get("media_type") == "image" else ".mp4"


README = """EVIDENCE PACKAGE {number}
{title}

This archive was exported from Seventh AI Vision on {exported_at} by {exported_by}.
Reason given for the export: {reason}

WHAT IS IN IT
  manifest.json      The package as it was sealed on {sealed_at} by {sealed_by}:
                     every item, when it was captured and the SHA-256 checksum
                     the platform had recorded for it.
  manifest.sha256    The SHA-256 of manifest.json. It is the value stored with
                     the package when it was sealed.
  export.json        What this export contains: for each item, whether its
                     file is here, the checksum computed from the file now, and
                     whether that matches the checksum in the manifest.
  originals/         The files themselves, byte for byte as the platform holds
                     them. These are the evidence.
  viewing/           A copy of each picture with a band across the foot naming
                     this package. These are for showing to people. They are
                     NOT the originals and will not match any checksum.

HOW TO CHECK IT
  sha256sum manifest.json        must equal the value in manifest.sha256
  sha256sum originals/<file>     must equal "checksum_sha256" for that item in
                                 manifest.json

{summary}
An item listed in export.json as not included is still held by the platform;
its checksum is in the manifest, and it is fetched from where it is kept.
"""


def build_export(package: Mapping, items: Sequence[Mapping], *, exported_by: str, reason: str,
                 exported_at: datetime, target: Path, marked_copies: bool = True,
                 max_bytes: int | None = None) -> dict:
    """Write the export of a sealed package to `target` and say what went into
    it. `items` are the things read now, each carrying its `_path` and its
    place `n` in the manifest. Synchronous: the caller runs it off the event loop."""
    budget = MAX_EXPORT_BYTES if max_bytes is None else max_bytes
    stored = package["manifest"] if not isinstance(package["manifest"], str) else json.loads(package["manifest"])
    sealed = canonical(stored)
    recorded = {(i["kind"], i["id"]): i for i in stored["items"]}
    line = (f"{package['package_number']} · exported {exported_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC "
            f"by {exported_by} · viewing copy, not the original")
    report: list[dict] = []
    used = 0
    workdir = target.parent / f"{target.name}.parts"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", sealed, zipfile.ZIP_DEFLATED)
            archive.writestr("manifest.sha256", f"{package['manifest_sha256']}  manifest.json\n")
            for listed in stored["items"]:
                key = (listed["kind"], listed["id"])
                thing = next((i for i in items if (i["kind"], str(i["id"])) == key), None)
                entry = {"n": listed["n"], "kind": listed["kind"], "id": listed["id"], "included": False,
                         "file": None, "bytes": None, "sha256_now": None, "sha256_in_manifest": listed["checksum_sha256"],
                         "verified": None, "viewing_copy": None, "why_not": None}
                report.append(entry)
                if thing is None:
                    entry["why_not"] = "It is no longer held by the platform."
                    continue
                path, why_not = _fetch(listed["kind"], thing, workdir)
                if path is None:
                    entry["why_not"] = why_not
                    continue
                size = path.stat().st_size
                if used + size > budget:
                    entry["why_not"] = "It is larger than one export carries. Fetch it from where it is kept."
                    entry["bytes"] = size
                    continue
                name = f"originals/{listed['n']:03d}-{listed['kind'].lower()}-{listed['id']}{_extension(thing)}"
                computed = hashlib.sha256()
                with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as sink:
                    for chunk in iter(lambda: source.read(_CHUNK), b""):
                        computed.update(chunk)
                        sink.write(chunk)
                used += size
                entry.update(included=True, file=name, bytes=size, sha256_now=computed.hexdigest())
                if listed["checksum_sha256"]:
                    entry["verified"] = computed.hexdigest() == listed["checksum_sha256"].lower()
                if marked_copies and thing.get("media_type") == "image":
                    try:
                        copy = watermark(path.read_bytes(), line)
                    except Exception:  # noqa: BLE001 — a picture that will not open still has its original here
                        copy = None
                    if copy is not None:
                        entry["viewing_copy"] = f"viewing/{listed['n']:03d}-{listed['kind'].lower()}-{listed['id']}.jpg"
                        archive.writestr(entry["viewing_copy"], copy)

            totals = summarise(report)
            export = _plain({
                "package_number": package["package_number"], "manifest_sha256": package["manifest_sha256"],
                "exported_at": exported_at, "exported_by": exported_by, "reason": reason, "items": report,
                **totals})
            archive.writestr("export.json", json.dumps(export, indent=2, ensure_ascii=False), zipfile.ZIP_DEFLATED)
            if totals["mismatched"]:
                summary = (f"WARNING: {totals['mismatched']} file(s) in originals/ DO NOT MATCH the checksum in the "
                           "manifest. See export.json. A file that does not match has changed since it was captured.\n")
            else:
                summary = (f"{totals['verified']} of the {totals['included']} file(s) in originals/ were compared "
                           "with their checksum when this archive was made, and matched.\n")
            if totals["unverifiable"]:
                summary += (f"{totals['unverifiable']} file(s) had no checksum recorded when they were captured "
                            "and could not be compared.\n")
            if totals["left_out"]:
                summary += f"{totals['left_out']} item(s) of the package are not in this archive.\n"
            archive.writestr("README.txt", README.format(
                number=package["package_number"], title=package["title"],
                exported_at=f"{exported_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC", exported_by=exported_by,
                reason=reason, sealed_at=stored.get("sealed_at"), sealed_by=stored.get("sealed_by") or "—",
                summary=summary), zipfile.ZIP_DEFLATED)
    finally:
        for leftover in workdir.glob("*"):
            leftover.unlink(missing_ok=True)
        workdir.rmdir()
    assert set(recorded) == {(r["kind"], r["id"]) for r in report}
    return {"items": report, **summarise(report), "bytes": used}


def summarise(report: Sequence[Mapping]) -> dict:
    included = [r for r in report if r["included"]]
    return {
        "included": len(included), "left_out": len(report) - len(included),
        "verified": sum(1 for r in included if r["verified"] is True),
        "mismatched": sum(1 for r in included if r["verified"] is False),
        "unverifiable": sum(1 for r in included if r["verified"] is None),
    }


async def held_permissions(db: AsyncSession, role_id: int) -> frozenset[str]:
    """Which of the permissions this module consults the role holds."""
    codes = sorted(set(NEEDS.values()) | {"evidence:package:read", "evidence:package:manage",
                                          "evidence:package:export", "evidence:hold:manage",
                                          "evidence:custody:read"})
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code = ANY(CAST(:codes AS text[]))
    """), {"role": role_id, "codes": codes})
    return frozenset(r.code for r in rows)
