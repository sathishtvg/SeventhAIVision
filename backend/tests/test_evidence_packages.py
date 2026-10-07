"""Evidence packages: what was kept about a matter — collected, sealed, held and accounted for.

  A — The rules, with nothing running: the manifest, its hash, the marked copy, where a file may be
  B — Collecting: only what belongs, only by someone who may open it
  C — Sealing: the manifest, the holds, and that nothing changes afterwards
  D — Leaving the platform: the export, an original, sharing and release
  E — The chain of custody
  F — Holds, and the three retention jobs that honour them
  G — Who may, at which sites, in which organisation
  H — What the application's role can and cannot do

Every request goes through the real app over ASGI, as svc_app with RLS
enforced. The files are real files in a directory of the test's own.

The claims, each with tests: a package refers to evidence and never copies it
until a person exports it; only what belongs to the investigation's records
goes in; a sealed package cannot be changed by anybody; an original leaves
byte for byte and is compared with its checksum on the way out; anything under
a hold is left alone by every purge and everything else is purged as before;
and every step is in one chain of custody with who, in what role, and why.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app import scheduler_main
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import evidence_packages as api
from app.services import continuous_recording, drone_retention, evidence_hold
from app.services import evidence_packages as packages
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql, _world
from tests.test_investigation_search import _audit, _iso, _seed

BASE = "/api/v1/evidence-packages"
HOLDS = "/api/v1/evidence-holds"
INVESTIGATIONS = "/api/v1/investigations"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0144_evidence_packages.py").read_text(encoding="utf-8")
EVERY_KIND = set(packages.KINDS)


def _jpeg(colour=(40, 60, 80), size=(640, 360)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="JPEG")
    return out.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Directories of the test's own for evidence and for recordings."""
    evidence, recordings = tmp_path / "evidence", tmp_path / "recordings"
    evidence.mkdir()
    recordings.mkdir()
    monkeypatch.setattr(settings, "EVIDENCE_ROOT", str(evidence))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(packages, "RECORDINGS_ROOT", str(recordings))
    monkeypatch.setattr(scheduler_main, "EVIDENCE_ROOT", evidence)
    return evidence, recordings


async def _kept(w: dict, seed: dict, roots, *, site: str = "site_a") -> dict:
    """What the platform kept about the seed's records: files on disk and the
    rows that point at them. A frame and a plate crop of the plate read, a clip
    kept with the incident, the camera's recording, a drone snapshot — and a
    frame of some other detection, which belongs to none of them."""
    evidence_root, recordings_root = roots
    t, s = w["tenant"], w[site]
    ids = {k: uuid.uuid4() for k in ("frame", "crop", "clip", "recording", "drone", "stranger", "stream")}
    data = {"frame": _jpeg((40, 60, 80)), "crop": _jpeg((200, 200, 40), (160, 60)), "clip": b"clip-bytes-" * 400,
            "recording": b"recording-bytes-" * 4000, "drone": _jpeg((20, 120, 60)), "stranger": _jpeg((9, 9, 9))}
    paths = {k: f"{t}/{k}-{ids[k]}{'.mp4' if k in ('clip', 'recording') else '.jpg'}" for k in data}
    for name, content in data.items():
        target = (recordings_root if name == "recording" else evidence_root) / paths[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    at = seed["at"]
    started = seed["since"] - timedelta(minutes=5)
    evidence = ("INSERT INTO evidence (id, tenant_id, detection_id, incident_id, media_type, storage_path, "
                "    checksum_sha256, captured_at, site_id, capture_kind) "
                "VALUES (:i,:t,:d,:inc,:m,:p,:sum,:at,:s,:k)")
    await _run([
        (evidence, {"i": ids["frame"], "t": t, "d": seed["ids"]["PLATE_READ"], "inc": None, "m": "image",
                    "p": paths["frame"], "sum": _sha(data["frame"]), "at": at["PLATE_READ"], "s": s, "k": "frame"}),
        (evidence, {"i": ids["crop"], "t": t, "d": seed["ids"]["PLATE_READ"], "inc": None, "m": "image",
                    "p": paths["crop"], "sum": _sha(data["crop"]), "at": at["PLATE_READ"] + timedelta(seconds=1),
                    "s": s, "k": "plate_crop"}),
        (evidence, {"i": ids["clip"], "t": t, "d": None, "inc": seed["ids"]["INCIDENT"], "m": "video",
                    "p": paths["clip"], "sum": _sha(data["clip"]), "at": at["INCIDENT"], "s": s, "k": "clip"}),
        (evidence, {"i": ids["stranger"], "t": t, "d": uuid.uuid4(), "inc": None, "m": "image",
                    "p": paths["stranger"], "sum": _sha(data["stranger"]), "at": at["PLATE_READ"], "s": s,
                    "k": "frame"}),
        ("INSERT INTO streams (id, tenant_id, camera_id, url) VALUES (:i,:t,:c,'rtsp://203.0.113.10/stream')",
         {"i": ids["stream"], "t": t, "c": seed["camera"]}),
        ("INSERT INTO recordings (id, tenant_id, camera_id, stream_id, site_id, status, started_at, ended_at, "
         "    file_path, file_size_bytes, duration_seconds, checksum_sha256, checksum_status) "
         "VALUES (:i,:t,:c,:st,:s,'completed',:a,:b,:p,:n,3600,:sum,'PASSED')",
         {"i": ids["recording"], "t": t, "c": seed["camera"], "st": ids["stream"], "s": s, "a": started,
          "b": started + timedelta(hours=1), "p": paths["recording"], "n": len(data["recording"]),
          "sum": _sha(data["recording"])}),
        ("INSERT INTO drone_event_media (id, tenant_id, event_id, media_kind, storage_path, storage_location, "
         "    checksum_sha256, size_bytes, captured_at) VALUES (:i,:t,:e,'SNAPSHOT',:p,'central',:sum,:n,:at)",
         {"i": ids["drone"], "t": t, "e": seed["ids"]["DRONE"], "p": paths["drone"], "sum": _sha(data["drone"]),
          "n": len(data["drone"]), "at": at["DRONE"]}),
    ])
    return {"ids": ids, "data": data, "paths": paths, "started": started,
            "files": {k: (recordings_root if k == "recording" else evidence_root) / paths[k] for k in data}}


def _record(seed: dict, kind: str) -> dict:
    return {"kind": kind, "id": str(seed["ids"][kind]), "occurred_at": _iso(seed["at"][kind])}


async def _investigation(c, w: dict, seed: dict, *, kinds=("PLATE_READ", "INCIDENT", "ALERT", "DRONE"),
                         site: str | None = "site_a", who: int = ADMIN) -> str:
    r = await c.post(INVESTIGATIONS, headers=w["h"][who], json={
        "title": "Lorry at the back fence", "reason": "Reported by the night supervisor.",
        **({"site_id": str(w[site])} if site else {})})
    assert r.status_code == 201, r.text
    file_id = r.json()["id"]
    if kinds:
        r = await c.post(f"{INVESTIGATIONS}/{file_id}/items", headers=w["h"][who],
                         json={"records": [_record(seed, k) for k in kinds]})
        assert r.status_code == 201, r.text
    return file_id


async def _package(c, w: dict, who: int = ADMIN, expect: int = 201, **body) -> dict:
    body = {"title": "Evidence of the lorry", "purpose": "For the client's insurer.", **body}
    r = await c.post(BASE, headers=w["h"][who], json=body)
    assert r.status_code == expect, r.text
    return r.json()


async def _get(c, w: dict, package_id: str, who: int = ADMIN) -> dict:
    r = await c.get(f"{BASE}/{package_id}", headers=w["h"][who])
    assert r.status_code == 200, r.text
    return r.json()


async def _candidates(c, w: dict, package_id: str, who: int = ADMIN) -> dict:
    r = await c.get(f"{BASE}/{package_id}/candidates", headers=w["h"][who])
    assert r.status_code == 200, r.text
    return r.json()


def _refs(items: list[dict]) -> list[dict]:
    return [{"kind": i["kind"], "id": i["id"], "captured_at": i["captured_at"]} for i in items]


async def _post(c, w: dict, path: str, body: dict | None = None, who: int = ADMIN):
    return await c.post(f"{BASE}/{path}", headers=w["h"][who], json=body or {})


async def _ready(c, w: dict, roots, *, who: int = ADMIN, seal: bool = False) -> dict:
    """A package for an investigation with everything that belongs to it added."""
    seed = await _seed(w)
    kept = await _kept(w, seed, roots)
    file_id = await _investigation(c, w, seed)
    package = await _package(c, w, who, investigation_id=file_id)
    offered = (await _candidates(c, w, package["id"]))["items"]
    r = await _post(c, w, f"{package['id']}/items", {"items": _refs(offered)}, who)
    assert r.status_code == 201 and len(r.json()["added"]) == 5, r.text
    sealed = None
    if seal:
        r = await _post(c, w, f"{package['id']}/seal", who=who)
        assert r.status_code == 200, r.text
        sealed = r.json()
    return {"seed": seed, "kept": kept, "investigation": file_id, "id": package["id"],
            "number": package["package_number"], "sealed": sealed}


async def _export(c, w: dict, package_id: str, who: int = ADMIN, **body):
    r = await c.post(f"{BASE}/{package_id}/export", headers=w["h"][who],
                     json={"reason": "Requested by the insurer's assessor.", **body})
    return r, (zipfile.ZipFile(io.BytesIO(r.content)) if r.status_code == 200 else None)


async def _chain(c, w: dict, package_id: str, who: int = ADMIN) -> list[dict]:
    r = await c.get(f"{BASE}/{package_id}/custody", headers=w["h"][who])
    assert r.status_code == 200, r.text
    return r.json()["chain"]


# ─── A. The rules ────────────────────────────────────────────────────────────

def _things() -> tuple[dict, list[dict]]:
    at = datetime(2026, 10, 5, 17, 12, 40, tzinfo=timezone.utc)
    package = {"package_number": "EVP-20261006-0001", "title": "Evidence of the lorry", "purpose": "For the insurer.",
               "site_name": "Factory A", "investigation_number": "INV-20261006-0001", "incident_id": None,
               "created_at": at, "created_by_name": "Priya Nair"}
    items = [
        {"kind": "SNAPSHOT", "id": uuid.UUID(int=1), "what": "Frame at the detection", "captured_at": at,
         "site_name": "Factory A", "camera_name": "North Gate", "media_type": "image", "checksum_sha256": "ab" * 32,
         "size_bytes": None, "duration_seconds": None, "kept": "central", "note": "The lorry · as it came in"},
        {"kind": "RECORDING", "id": uuid.UUID(int=2), "what": "Recording of North Gate", "captured_at": at,
         "ended_at": at + timedelta(hours=1), "site_name": "Factory A", "camera_name": "North Gate",
         "media_type": "video", "checksum_sha256": None, "size_bytes": 64000, "duration_seconds": 3600.0,
         "kept": "central", "note": None},
    ]
    return package, items


def test_a_manifest_says_everything_in_the_package_and_is_written_one_way_only():
    package, items = _things()
    sealed_at = datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc)
    document = packages.manifest(package, items, sealed_at=sealed_at, sealed_by="Priya Nair")
    assert document["format"] == "seventh-evidence-manifest/1" and document["package_number"] == "EVP-20261006-0001"
    assert (document["sealed_by"], document["sealed_at"]) == ("Priya Nair", "2026-10-06T02:00:00+00:00")
    assert [i["n"] for i in document["items"]] == [1, 2]
    first, second = document["items"]
    assert (first["id"], first["checksum_recorded"], first["captured_at"]) == (
        str(uuid.UUID(int=1)), True, "2026-10-05T17:12:40+00:00")
    assert (second["checksum_sha256"], second["checksum_recorded"], second["duration_seconds"]) == (None, False, 3600)
    assert document["counts"] == {"SNAPSHOT": 1, "CLIP": 0, "RECORDING": 1, "DRONE_MEDIA": 0}
    assert document["without_checksum"] == 1, "an item with no checksum is counted, not hidden"
    assert "storage_path" not in json.dumps(document) and "_path" not in json.dumps(document)

    written = packages.canonical(document)
    assert written == packages.canonical(json.loads(written)), "what is stored reads back to the same bytes"
    assert written == packages.canonical(dict(reversed(list(document.items())))), "whatever order the keys came in"
    assert b" " not in written.replace(b"The lorry \xc2\xb7 as it came in", b"").replace(
        b"Evidence of the lorry", b"").replace(b"For the insurer.", b"").replace(b"Frame at the detection", b"") \
        .replace(b"Recording of North Gate", b"").replace(b"Factory A", b"").replace(b"North Gate", b"") \
        .replace(b"Priya Nair", b""), "nothing padded"
    assert "·".encode() in written, "written as it is, not escaped"
    assert packages.digest(document) == hashlib.sha256(written).hexdigest()


def test_a_sealed_package_is_intact_only_while_its_manifest_has_the_hash_it_was_sealed_with():
    package, items = _things()
    document = packages.manifest(package, items, sealed_at=datetime.now(timezone.utc), sealed_by="Priya Nair")
    sealed = {"status": "SEALED", "manifest": document, "manifest_sha256": packages.digest(document)}
    assert packages.intact(sealed) is True
    assert packages.intact({**sealed, "manifest": json.dumps(document)}) is True, "as the database hands it back"
    altered = json.loads(json.dumps(document))
    altered["items"][0]["checksum_sha256"] = "cd" * 32
    assert packages.intact({**sealed, "manifest": altered}) is False
    assert packages.intact({**sealed, "manifest": {**document, "purpose": "For somebody else."}}) is False
    assert packages.intact({"status": "DRAFT", "manifest": None, "manifest_sha256": None}) is None


def test_a_marked_copy_is_a_different_picture_and_says_what_it_is():
    original = _jpeg()
    marked = packages.watermark(original, "EVP-20261006-0001 · viewing copy, not the original")
    assert marked != original and _sha(marked) != _sha(original), "which is why the original is never marked"
    with Image.open(io.BytesIO(marked)) as picture, Image.open(io.BytesIO(original)) as was:
        assert picture.width == was.width and picture.height > was.height, "a band is added; the picture is not covered"
        assert picture.format == "JPEG"
        foot = picture.crop((0, was.height, picture.width, picture.height)).convert("L")
        assert foot.getextrema()[1] > 200 and foot.getextrema()[0] < 40, "white words on a black band"
    with pytest.raises(Exception):
        packages.watermark(b"not a picture", "x")


def test_a_file_is_opened_only_inside_the_directory_its_kind_is_kept_in(roots):
    evidence_root, recordings_root = roots
    (evidence_root / "t").mkdir()
    (evidence_root / "t" / "a.jpg").write_bytes(b"x")
    assert packages.local_path("SNAPSHOT", "t/a.jpg") == (evidence_root / "t" / "a.jpg").resolve()
    assert packages.local_path("RECORDING", "t/a.mp4") == (recordings_root / "t" / "a.mp4").resolve()
    for outside in ("../recordings/t/a.mp4", "../../etc/passwd", "/etc/passwd", "t/../../x", None, ""):
        assert packages.local_path("SNAPSHOT", outside) is None, outside
    thing = {"kind": "SNAPSHOT", "id": "1", "_path": "t/a.jpg", "kept": "central", "media_type": "image"}
    assert packages.public(thing) == {"kind": "SNAPSHOT", "id": "1", "kept": "central", "media_type": "image"}
    assert packages.public(None) is None


def test_a_file_in_the_object_store_is_fetched_to_be_read_and_one_at_the_site_is_not(roots, tmp_path, monkeypatch):
    from app.core import object_store

    asked: list[tuple] = []

    class Store:
        def download_file(self, bucket, key, filename):
            asked.append((bucket, key))
            if "missing" in key:
                raise RuntimeError("NoSuchKey")
            Path(filename).write_bytes(b"from the store")

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(object_store, "_get_client", lambda: Store())
    thing = {"id": "1", "_path": "t/frame.jpg", "kept": "central"}
    path, why_not = packages._fetch("SNAPSHOT", thing, tmp_path)
    assert why_not is None and path.read_bytes() == b"from the store" and path.parent == tmp_path
    assert asked == [(settings.S3_BUCKET, "t/frame.jpg")]
    assert packages._fetch("CLIP", {**thing, "_path": "t/missing.mp4"}, tmp_path) == (
        None, "The file could not be fetched from the object store.")
    assert packages._fetch("CLIP", {**thing, "_path": None}, tmp_path) == (None, "The platform holds no file for it.")
    assert packages._fetch("SNAPSHOT", {**thing, "kept": "local"}, tmp_path) == (
        None, "It is held at the site and has not been uploaded.")
    # A recording is a file on the shared disk whatever the evidence store is.
    recording = roots[1] / "t" / "rec.mp4"
    recording.parent.mkdir()
    recording.write_bytes(b"a recording")
    assert packages._fetch("RECORDING", {"id": "2", "_path": "t/rec.mp4", "kept": "central"}, tmp_path) == (
        recording.resolve(), None)
    assert len(asked) == 2, "the store was not asked for the recording"
    assert packages._fetch("RECORDING", {"id": "3", "_path": "t/gone.mp4", "kept": None}, tmp_path) == (
        None, "The file is no longer on disk.")


def test_each_kind_asks_for_the_permission_its_own_screen_asks_for():
    assert packages.NEEDS == {"SNAPSHOT": "evidence:read", "CLIP": "evidence:read", "RECORDING": "recording:read",
                              "DRONE_MEDIA": "drone:event:read"}
    from app.services import intel_evidence
    assert all(packages.NEEDS[k] == intel_evidence.NEEDS[k] for k in packages.KINDS), \
        "the same words as the intelligence layer uses for the same things"
    listed = set(re.findall(r"'([A-Z_]+)'", re.search(r'^KINDS = "(.*)"$', MIGRATION, re.M).group(1)))
    assert listed == EVERY_KIND, "the database and the code disagree on what a package can hold"


def test_the_question_the_purges_ask_is_asked_in_one_place():
    assert evidence_hold.not_held(evidence_hold.FRAMES_AND_CLIPS, "evidence.id") == (
        "NOT EXISTS (SELECT 1 FROM evidence_holds held WHERE held.kind IN ('SNAPSHOT', 'CLIP') "
        "AND held.ref_id = evidence.id AND held.released_at IS NULL)")
    assert "'RECORDING'" in evidence_hold.not_held(evidence_hold.RECORDINGS, "r.id")
    assert "'DRONE_MEDIA'" in evidence_hold.not_held(evidence_hold.DRONE_MEDIA, "m.id")
    for bad in ((("ANYTHING",), "r.id"), (evidence_hold.RECORDINGS, "r.id; DROP TABLE x")):
        with pytest.raises(AssertionError):
            evidence_hold.not_held(*bad)
    held = set(evidence_hold.FRAMES_AND_CLIPS + evidence_hold.RECORDINGS + evidence_hold.DRONE_MEDIA)
    assert held == EVERY_KIND, "every kind a package can hold is a kind some purge is told to leave alone"
    app_dir = Path(packages.__file__).resolve().parents[1]
    for path, kinds in (("scheduler_main.py", "FRAMES_AND_CLIPS"), ("services/continuous_recording.py", "RECORDINGS"),
                        ("services/drone_retention.py", "DRONE_MEDIA")):
        code = (app_dir / path).read_text(encoding="utf-8")
        assert f"not_held({kinds}," in code, f"{path} does not ask whether a thing is held before deleting it"


# ─── B. Collecting ───────────────────────────────────────────────────────────

async def test_what_belongs_to_an_investigation_is_found_by_what_its_records_say(roots):
    w = await _world()
    seed = await _seed(w)
    kept = await _kept(w, seed, roots)
    async with _client() as c:
        file_id = await _investigation(c, w, seed)
        package = await _package(c, w, investigation_id=file_id)
        assert re.fullmatch(r"EVP-\d{8}-0001", package["package_number"]) and package["status"] == "DRAFT"
        found = await _candidates(c, w, package["id"])
    assert (found["records"], found["already_in"], found["not_looked_for"]) == (4, 0, [])
    by_id = {i["id"]: i for i in found["items"]}
    assert set(by_id) == {str(kept["ids"][k]) for k in ("frame", "crop", "clip", "recording", "drone")}, \
        "the frame of some other detection belongs to none of these records"
    frame, crop, clip = (by_id[str(kept["ids"][k])] for k in ("frame", "crop", "clip"))
    recording, drone = by_id[str(kept["ids"]["recording"])], by_id[str(kept["ids"]["drone"])]
    assert (frame["kind"], frame["what"], frame["goes_with"]) == (
        "SNAPSHOT", "Frame at the detection", {"kind": "PLATE_READ", "id": str(seed["ids"]["PLATE_READ"])})
    assert (crop["what"], crop["camera_name"], crop["site_name"]) == ("Number plate, cropped", "Gate Camera A",
                                                                     "Factory A")
    assert (clip["kind"], clip["what"], clip["goes_with"]["kind"]) == (
        "CLIP", "Clip of the detection — kept with the incident", "INCIDENT")
    assert (recording["kind"], recording["what"], recording["checksum_status"]) == (
        "RECORDING", "Recording of Gate Camera A", "PASSED")
    assert 0 < recording["offset_seconds"] < 3600 and recording["goes_with"]["kind"] in ("ALERT", "INCIDENT",
                                                                                     "PLATE_READ")
    assert (drone["kind"], drone["what"], drone["goes_with"]["kind"]) == ("DRONE_MEDIA", "Drone snapshot", "DRONE")
    assert frame["checksum_sha256"] == _sha(kept["data"]["frame"]) and frame["may_open"] is True
    assert frame["served_at"] == {"path": f"/api/v1/evidence/{frame['id']}/image", "token_in_query": True}
    times = [i["captured_at"] for i in found["items"]]
    assert times == sorted(times), "oldest first"
    assert "storage_path" not in json.dumps(found) and "file_path" not in json.dumps(found)
    assert str(w["tenant"]) not in json.dumps(found), "a path would have had the organisation's id in it"


async def test_only_what_belongs_goes_in_and_all_or_none(roots):
    w = await _world()
    seed = await _seed(w)
    kept = await _kept(w, seed, roots)
    async with _client() as c:
        file_id = await _investigation(c, w, seed, kinds=("PLATE_READ",))
        package = await _package(c, w, investigation_id=file_id)
        offered = (await _candidates(c, w, package["id"]))["items"]
        assert {i["kind"] for i in offered} == {"SNAPSHOT", "RECORDING"}, "the clip and the drone snapshot belong " \
            "to records that are not in this investigation"
        stranger = {"kind": "SNAPSHOT", "id": str(kept["ids"]["stranger"]),
                    "captured_at": _iso(seed["at"]["PLATE_READ"])}
        clip = {"kind": "CLIP", "id": str(kept["ids"]["clip"]), "captured_at": _iso(seed["at"]["INCIDENT"])}
        r = await _post(c, w, f"{package['id']}/items", {"items": [*_refs(offered), stranger, clip]})
        assert r.status_code == 404 and "Nothing was added" in r.json()["detail"]["message"]
        assert r.json()["detail"]["not_found"] == [{"kind": "SNAPSHOT", "id": stranger["id"]},
                                                   {"kind": "CLIP", "id": clip["id"]}]
        assert (await _get(c, w, package["id"]))["items"] == []

        r = await _post(c, w, f"{package['id']}/items", {"items": _refs(offered), "note": "  The lorry.  "})
        assert r.status_code == 201 and len(r.json()["added"]) == 3 and r.json()["already_in"] == []
        again = await _post(c, w, f"{package['id']}/items", {"items": _refs(offered[:1])})
        assert again.json() == {"added": [], "already_in": [{"kind": offered[0]["kind"], "id": offered[0]["id"]}]}
        after = await _candidates(c, w, package["id"])
        assert (after["items"], after["already_in"]) == ([], 3)
        for bad in ({"items": []}, {"items": [{"kind": "SNAPSHOT", "id": "x", "captured_at": _iso(seed["since"])}]},
                    {}, {"items": _refs(offered) * 20}):
            assert (await _post(c, w, f"{package['id']}/items", bad)).status_code == 422
        unknown = await _post(c, w, f"{package['id']}/items",
                              {"items": [{**_refs(offered)[0], "kind": "PATROL_SNAPSHOT"}]})
        assert unknown.status_code == 422 and "Unknown kind of evidence" in unknown.json()["detail"]
        naive = await _post(c, w, f"{package['id']}/items",
                            {"items": [{**_refs(offered)[0], "captured_at": "2026-10-05T01:00:00"}]})
        assert naive.status_code == 422 and "needs a time zone" in naive.json()["detail"]

        # When the record is filed in the investigation, its evidence belongs.
        await c.post(f"{INVESTIGATIONS}/{file_id}/items", headers=w["h"][ADMIN],
                     json={"records": [_record(seed, "INCIDENT")]})
        assert [i["kind"] for i in (await _candidates(c, w, package["id"]))["items"]] == ["CLIP"]
        assert (await _post(c, w, f"{package['id']}/items", {"items": [clip]})).status_code == 201
        got = await _get(c, w, package["id"])
    assert got["counts"] == {"items": 4, "held": 0, "not_shown": 0, "without_checksum": 0}
    assert all(i["state"] == "SHOWN" and i["note"] in ("The lorry.", None) for i in got["items"])
    assert "storage_path" not in json.dumps(got) and "file_path" not in json.dumps(got)
    stored = await _sql("SELECT kind, checksum_sha256 FROM evidence_package_items WHERE package_id = :p",
                        {"p": package["id"]})
    assert {r["checksum_sha256"] for r in stored} == {_sha(kept["data"][k]) for k in ("frame", "crop", "recording",
                                                                                    "clip")}


async def test_a_package_is_opened_for_an_investigation_or_an_incident_and_says_what_for(roots):
    w = await _world()
    seed = await _seed(w)
    kept = await _kept(w, seed, roots)
    async with _client() as c:
        file_id = await _investigation(c, w, seed)
        for bad, why in (({}, "one of them"),
                         ({"investigation_id": file_id, "incident_id": str(seed["ids"]["INCIDENT"])}, "one of them")):
            assert why in (await _package(c, w, expect=422, **bad))["detail"]
        for bad in ({"purpose": ""}, {"purpose": "why"}, {"title": "ab"}, {"colour": "red"}):
            r = await c.post(BASE, headers=w["h"][ADMIN], json={
                "title": "Evidence", "purpose": "For the insurer.", "investigation_id": file_id, **bad})
            assert r.status_code == 422, bad
        assert (await _package(c, w, expect=404, investigation_id=str(uuid.uuid4())))["detail"] == \
            "Investigation not found"
        assert (await _package(c, w, expect=404, incident_id=str(uuid.uuid4())))["detail"] == "Incident not found"

        from_incident = await _package(c, w, OPERATOR, incident_id=str(seed["ids"]["INCIDENT"]))
        found = await _candidates(c, w, from_incident["id"], OPERATOR)
        got = await _get(c, w, from_incident["id"])
    assert (from_incident["package_number"][-4:], found["records"]) == ("0001", 2), "the incident and its alert"
    assert {i["id"] for i in found["items"]} == {str(kept["ids"]["clip"]), str(kept["ids"]["recording"])}
    assert (got["site_name"], got["incident_id"], got["investigation_id"], got["purpose"]) == (
        "Factory A", str(seed["ids"]["INCIDENT"]), None, "For the client's insurer.")
    assert got["created_by_name"] == f"Role {OPERATOR} User" and got["intact"] is None
    assert got["can"] == {"manage": True, "export": True, "hold": True, "custody": True}


async def test_an_item_is_taken_out_of_a_draft_and_that_it_was_in_stays_on_the_record(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots)
        items = (await _get(c, w, p["id"]))["items"]
        drone = next(i for i in items if i["kind"] == "DRONE_MEDIA")
        r = await c.delete(f"{BASE}/{p['id']}/items/{drone['id']}", headers=w["h"][OPERATOR])
        assert r.status_code == 200 and r.json() == {"removed": True}
        assert (await c.delete(f"{BASE}/{p['id']}/items/{drone['id']}", headers=w["h"][ADMIN])).status_code == 404
        got = await _get(c, w, p["id"])
        offered = (await _candidates(c, w, p["id"]))["items"]
        chain = await _chain(c, w, p["id"])
    assert got["counts"]["items"] == 4 and [i["kind"] for i in offered] == ["DRONE_MEDIA"], "it can be added again"
    removed = [s for s in chain if s["step"] == "REMOVED"]
    assert len(removed) == 1 and removed[0]["ref_id"] == drone["ref_id"] and removed[0]["actor_role"] == OPERATOR


# ─── C. Sealing ──────────────────────────────────────────────────────────────

async def test_sealing_writes_the_manifest_and_its_hash_and_holds_every_item(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots)
        before = await _get(c, w, p["id"])
        r = await _post(c, w, f"{p['id']}/seal", who=OPERATOR)
        assert r.status_code == 200, r.text
        sealed = r.json()
        got = await _get(c, w, p["id"])
        listed = (await c.get(BASE, headers=w["h"][ADMIN])).json()["items"]
    assert (sealed["status"], sealed["items"], sealed["holds"], sealed["without_checksum"]) == ("SEALED", 5, 5, 0)
    assert re.fullmatch(r"[0-9a-f]{64}", sealed["manifest_sha256"])
    assert (got["status"], got["intact"], got["manifest_sha256"], got["sealed_by_name"]) == (
        "SEALED", True, sealed["manifest_sha256"], f"Role {OPERATOR} User")
    assert got["counts"] == {"items": 5, "held": 5, "not_shown": 0, "without_checksum": 0}
    assert before["counts"]["held"] == 0 and all(i["held"] for i in got["items"])
    assert "manifest" not in got, "the manifest leaves in an export, not in a listing"
    assert (listed[0]["items"], listed[0]["holds_in_force"], listed[0]["status"]) == (5, 5, "SEALED")

    row = (await _sql("SELECT manifest, manifest_sha256 FROM evidence_packages WHERE id = :p", {"p": p["id"]}))[0]
    document = row["manifest"] if isinstance(row["manifest"], dict) else json.loads(row["manifest"])
    assert packages.digest(document) == row["manifest_sha256"] == sealed["manifest_sha256"]
    assert (document["package_number"], document["purpose"], document["site"]) == (
        p["number"], "For the client's insurer.", "Factory A")
    assert document["investigation"].startswith("INV-") and document["sealed_by"] == f"Role {OPERATOR} User"
    assert [i["n"] for i in document["items"]] == [1, 2, 3, 4, 5]
    assert {i["checksum_sha256"] for i in document["items"]} == {
        _sha(p["kept"]["data"][k]) for k in ("frame", "crop", "clip", "recording", "drone")}
    assert "storage_path" not in json.dumps(document) and str(w["tenant"]) not in json.dumps(document)
    holds = await _sql("SELECT kind, reason, package_id, released_at FROM evidence_holds WHERE tenant_id = :t",
                       {"t": w["tenant"]})
    assert len(holds) == 5 and {h["kind"] for h in holds} == EVERY_KIND
    assert all(h["reason"] == f"Sealed in evidence package {p['number']}." and h["released_at"] is None
               and str(h["package_id"]) == p["id"] for h in holds)


async def test_nothing_in_a_sealed_package_changes_whoever_asks(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots, seal=True)
        item = (await _get(c, w, p["id"]))["items"][0]
        for call in (_post(c, w, f"{p['id']}/items", {"items": _refs([item["thing"]])}),
                     c.delete(f"{BASE}/{p['id']}/items/{item['id']}", headers=w["h"][ADMIN]),
                     _post(c, w, f"{p['id']}/seal")):
            r = await call
            assert r.status_code == 409 and "sealed" in r.json()["detail"], r.text
    # Not the application, and not somebody with the database's own password either.
    for statement in (
        "UPDATE evidence_packages SET purpose = 'For somebody else.' WHERE id = :p",
        "UPDATE evidence_packages SET manifest = '{}'::jsonb WHERE id = :p",
        "UPDATE evidence_packages SET status = 'DRAFT', sealed_at = NULL, manifest = NULL, manifest_sha256 = NULL "
        " WHERE id = :p",
        "DELETE FROM evidence_packages WHERE id = :p",
        "DELETE FROM evidence_package_items WHERE package_id = :p",
        "UPDATE evidence_package_items SET checksum_sha256 = repeat('0', 64) WHERE package_id = :p",
        "INSERT INTO evidence_package_items (tenant_id, package_id, kind, ref_id, captured_at) "
        "SELECT tenant_id, id, 'CLIP', gen_random_uuid(), now() FROM evidence_packages WHERE id = :p",
    ):
        with pytest.raises(DBAPIError, match="sealed"):
            await _sql(statement, {"p": p["id"]})
    after = (await _sql("SELECT purpose, status, (SELECT count(*) FROM evidence_package_items i "
                        " WHERE i.package_id = e.id) AS n FROM evidence_packages e WHERE id = :p", {"p": p["id"]}))[0]
    assert (after["purpose"], after["status"], after["n"]) == ("For the client's insurer.", "SEALED", 5)


async def test_the_databases_own_housekeeping_still_works_on_a_sealed_package():
    """A seal refuses a statement. It does not refuse a foreign key's own
    SET NULL or CASCADE, or removing somebody who has left, or a whole
    organisation, would be stopped by every package they ever sealed."""
    w = await _world()
    person, package, item = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:u, :t, 4, :e, 'x', 'Leaving Soon')",
         {"u": person, "t": w["tenant"], "e": f"leaving-{person.hex[:8]}@evidence.test"}),
        ("INSERT INTO evidence_packages (id, tenant_id, site_id, package_number, title, purpose, created_by_user_id) "
         "VALUES (:p, :t, :s, 'EVP-X-1', 'A title', 'A purpose.', :u)",
         {"p": package, "t": w["tenant"], "s": w["site_a"], "u": person}),
        ("INSERT INTO evidence_package_items (id, tenant_id, package_id, kind, ref_id, captured_at, site_id, "
         "    added_by_user_id) VALUES (:i, :t, :p, 'CLIP', gen_random_uuid(), now(), :s, :u)",
         {"i": item, "t": w["tenant"], "p": package, "s": w["site_a"], "u": person}),
        ("UPDATE evidence_packages SET status = 'SEALED', sealed_at = now(), sealed_by_user_id = :u, "
         "    manifest = '{}'::jsonb, manifest_sha256 = repeat('a', 64) WHERE id = :p", {"u": person, "p": package}),
    ])
    # The very change a foreign key is about to make is refused when a statement makes it.
    for statement in ("UPDATE evidence_packages SET sealed_by_user_id = NULL WHERE id = :p",
                      "UPDATE evidence_package_items SET added_by_user_id = NULL WHERE package_id = :p"):
        with pytest.raises(DBAPIError, match="sealed"):
            await _sql(statement, {"p": package})

    await _sql("DELETE FROM users WHERE id = :u", {"u": person})
    row = (await _sql("SELECT created_by_user_id, sealed_by_user_id, status, manifest_sha256 FROM evidence_packages "
                      " WHERE id = :p", {"p": package}))[0]
    assert (row["created_by_user_id"], row["sealed_by_user_id"], row["status"]) == (None, None, "SEALED")
    assert row["manifest_sha256"] == "a" * 64, "nothing of what was sealed has changed"
    kept = (await _sql("SELECT added_by_user_id, kind FROM evidence_package_items WHERE id = :i", {"i": item}))[0]
    assert (kept["added_by_user_id"], kept["kind"]) == (None, "CLIP")

    await _sql("DELETE FROM tenants WHERE id = :t", {"t": w["tenant"]})
    assert await _sql("SELECT 1 FROM evidence_packages WHERE id = :p", {"p": package}) == []
    assert await _sql("SELECT 1 FROM evidence_package_items WHERE id = :i", {"i": item}) == []


async def test_a_manifest_is_not_sealed_over_something_its_sealer_could_not_see(roots):
    w = await _world()
    async with _client() as c:
        seed = await _seed(w)
        file_id = await _investigation(c, w, seed)
        empty = await _package(c, w, investigation_id=file_id)
        r = await _post(c, w, f"{empty['id']}/seal")
        assert r.status_code == 409 and "nothing in this package" in r.json()["detail"]

    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots)
        # The platform now records a different checksum for the frame than when it was collected.
        await _sql("UPDATE evidence SET checksum_sha256 = repeat('f', 64) WHERE id = :i",
                   {"i": p["kept"]["ids"]["frame"]})
        got = await _get(c, w, p["id"])
        assert [i["checksum_changed"] for i in got["items"] if i["ref_id"] == str(p["kept"]["ids"]["frame"])] == [True]
        r = await _post(c, w, f"{p['id']}/seal")
        assert r.status_code == 409 and "not the one it recorded when they were added" in r.json()["detail"]["message"]
        assert r.json()["detail"]["items"] == [{"kind": "SNAPSHOT", "id": str(p["kept"]["ids"]["frame"])}]
        await _sql("UPDATE evidence SET checksum_sha256 = :s WHERE id = :i",
                   {"s": _sha(p["kept"]["data"]["frame"]), "i": p["kept"]["ids"]["frame"]})

        # The clip has gone — purged, say, before anybody sealed anything.
        await _sql("DELETE FROM evidence WHERE id = :i", {"i": p["kept"]["ids"]["clip"]})
        got = await _get(c, w, p["id"])
        gone = next(i for i in got["items"] if i["kind"] == "CLIP")
        assert (gone["state"], gone["thing"], got["counts"]["not_shown"]) == ("NOT_AVAILABLE", None, 1)
        r = await _post(c, w, f"{p['id']}/seal")
        assert r.status_code == 409 and r.json()["detail"]["items"] == [
            {"kind": "CLIP", "id": str(p["kept"]["ids"]["clip"])}]
        assert (await c.delete(f"{BASE}/{p['id']}/items/{gone['id']}", headers=w["h"][ADMIN])).status_code == 200
        sealed = await _post(c, w, f"{p['id']}/seal")
    assert sealed.status_code == 200 and sealed.json()["items"] == 4


# ─── D. Leaving the platform ─────────────────────────────────────────────────

async def test_an_export_carries_the_manifest_as_sealed_and_each_original_byte_for_byte(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots, seal=True)
        r, archive = await _export(c, w, p["id"], SUPERVISOR)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip", r.text
    assert f'{p["number"]}.zip' in r.headers["content-disposition"]
    assert {k: r.headers[f"x-evidence-{k}"] for k in ("included", "left-out", "verified", "mismatched",
                                                    "unverifiable")} == {
        "included": "5", "left-out": "0", "verified": "5", "mismatched": "0", "unverifiable": "0"}
    names = archive.namelist()
    assert {"manifest.json", "manifest.sha256", "export.json", "README.txt"} <= set(names)
    assert archive.testzip() is None

    sealed = archive.read("manifest.json")
    document = json.loads(sealed)
    assert sealed == packages.canonical(document), "the bytes that were hashed, not a rendering of them"
    assert _sha(sealed) == p["sealed"]["manifest_sha256"]
    assert archive.read("manifest.sha256").decode() == f"{p['sealed']['manifest_sha256']}  manifest.json\n"

    originals = sorted(n for n in names if n.startswith("originals/"))
    assert len(originals) == 5
    wanted = {str(p["kept"]["ids"][k]): p["kept"]["data"][k] for k in ("frame", "crop", "clip", "recording", "drone")}
    for listed in document["items"]:
        name = next(n for n in originals if listed["id"] in n)
        assert name.startswith(f"originals/{listed['n']:03d}-{listed['kind'].lower()}-")
        assert archive.read(name) == wanted[listed["id"]], f"{name} is not what the platform holds"
        assert _sha(archive.read(name)) == listed["checksum_sha256"]
        assert name.endswith(".mp4" if listed["media_type"] == "video" else ".jpg")

    viewing = sorted(n for n in names if n.startswith("viewing/"))
    assert len(viewing) == 3, "a marked copy of each picture, and of nothing that is not one"
    for name in viewing:
        original = archive.read(name.replace("viewing/", "originals/"))
        assert archive.read(name) != original, "the marked copy is not the original"
        with Image.open(io.BytesIO(archive.read(name))) as marked, Image.open(io.BytesIO(original)) as was:
            assert marked.height > was.height

    export = json.loads(archive.read("export.json"))
    assert (export["package_number"], export["manifest_sha256"], export["exported_by"], export["reason"]) == (
        p["number"], p["sealed"]["manifest_sha256"], f"Role {SUPERVISOR} User", "Requested by the insurer's assessor.")
    assert (export["included"], export["verified"], export["mismatched"], export["left_out"]) == (5, 5, 0, 0)
    assert all(i["included"] and i["verified"] is True and i["sha256_now"] == i["sha256_in_manifest"]
               for i in export["items"])
    readme = archive.read("README.txt").decode()
    assert p["number"] in readme and "5 of the 5 file(s)" in readme and "NOT the originals" in readme
    assert "sha256sum manifest.json" in readme and "WARNING" not in readme
    for name in names:
        assert str(w["tenant"]) not in name, "no storage path in an archive's file names"
    assert str(w["tenant"]) not in archive.read("export.json").decode()


async def test_an_export_without_marked_copies_and_one_that_is_not_allowed(roots):
    w = await _world()
    async with _client() as c:
        draft = await _ready(c, w, roots)
        r, _ = await _export(c, w, draft["id"])
        assert r.status_code == 409 and "has not been sealed" in r.json()["detail"]
        assert (await _post(c, w, f"{draft['id']}/seal")).status_code == 200
        plain, archive = await _export(c, w, draft["id"], marked_copies=False)
        assert plain.status_code == 200 and not [n for n in archive.namelist() if n.startswith("viewing/")]
        for bad in ({"reason": ""}, {"reason": "why"}, {"reason": "     "}, {"reason": None}):
            r = await c.post(f"{BASE}/{draft['id']}/export", headers=w["h"][ADMIN], json=bad)
            assert r.status_code == 422, bad
        for role in (OPERATOR, VIEWER):
            r, _ = await _export(c, w, draft["id"], role)
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: evidence:package:export"


async def test_a_file_that_has_changed_since_it_was_captured_is_exported_and_said_not_to_match(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots, seal=True)
        p["kept"]["files"]["frame"].write_bytes(_jpeg((250, 0, 0)))
        p["kept"]["files"]["clip"].unlink()
        r, archive = await _export(c, w, p["id"])
        chain = await _chain(c, w, p["id"])
    assert r.status_code == 200
    assert (r.headers["x-evidence-mismatched"], r.headers["x-evidence-left-out"], r.headers["x-evidence-verified"]) == (
        "1", "1", "3")
    export = json.loads(archive.read("export.json"))
    by_id = {i["id"]: i for i in export["items"]}
    frame, clip = by_id[str(p["kept"]["ids"]["frame"])], by_id[str(p["kept"]["ids"]["clip"])]
    assert (frame["included"], frame["verified"]) == (True, False) and frame["sha256_now"] != frame["sha256_in_manifest"]
    assert (clip["included"], clip["file"], clip["why_not"]) == (False, None, "The file is no longer on disk.")
    assert clip["sha256_in_manifest"] == _sha(p["kept"]["data"]["clip"]), "what it was is still on the record"
    readme = archive.read("README.txt").decode()
    assert "WARNING: 1 file(s) in originals/ DO NOT MATCH" in readme and "1 item(s) of the package are not in" in readme
    exported = next(s for s in chain if s["step"] == "EXPORTED")
    assert (exported["detail"]["mismatched"], exported["detail"]["left_out"], exported["detail"]["included"]) == (1, 1, 4)
    (audit,) = await _audit(w, "evidence.package.export")
    assert audit["detail"]["result"] == "checksum_mismatch" and audit["detail"]["mismatched"] == 1
    logged = await _sql("SELECT evidence_id, action, checksum_verified FROM evidence_access_log WHERE tenant_id = :t "
                        " ORDER BY evidence_id", {"t": w["tenant"]})
    assert {(str(r["evidence_id"]), r["checksum_verified"]) for r in logged} == {
        (str(p["kept"]["ids"]["frame"]), False), (str(p["kept"]["ids"]["crop"]), True)}, \
        "each frame that left is in the log that has always recorded who took one"
    assert all(r["action"] == "export" for r in logged)


async def test_an_export_carries_so_much_and_says_what_it_left_out(roots, monkeypatch):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots, seal=True)
        monkeypatch.setattr(packages, "MAX_EXPORT_BYTES", len(p["kept"]["data"]["recording"]) - 1)
        r, archive = await _export(c, w, p["id"])
    export = json.loads(archive.read("export.json"))
    recording = next(i for i in export["items"] if i["kind"] == "RECORDING")
    assert (recording["included"], recording["bytes"]) == (False, len(p["kept"]["data"]["recording"]))
    assert "larger than one export carries" in recording["why_not"]
    assert (export["included"], export["left_out"], r.headers["x-evidence-left-out"]) == (4, 1, "1")
    assert not [n for n in archive.namelist() if "recording" in n]


async def test_one_original_is_downloaded_as_the_platform_holds_it(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots)
        items = (await _get(c, w, p["id"]))["items"]
        frame = next(i for i in items if i["ref_id"] == str(p["kept"]["ids"]["frame"]))
        recording = next(i for i in items if i["kind"] == "RECORDING")
        early = await c.get(f"{BASE}/{p['id']}/items/{frame['id']}/file", headers=w["h"][ADMIN])
        assert early.status_code == 409 and "has not been sealed" in early.json()["detail"]
        await _post(c, w, f"{p['id']}/seal")
        r = await c.get(f"{BASE}/{p['id']}/items/{frame['id']}/file", headers=w["h"][SUPERVISOR])
        assert r.status_code == 200 and r.content == p["kept"]["data"]["frame"]
        assert r.headers["content-type"] == "image/jpeg" and f"{p['number']}-snapshot-" in r.headers["content-disposition"]
        video = await c.get(f"{BASE}/{p['id']}/items/{recording['id']}/file", headers=w["h"][ADMIN])
        assert video.content == p["kept"]["data"]["recording"] and video.headers["content-type"] == "video/mp4"
        assert (await c.get(f"{BASE}/{p['id']}/items/{uuid.uuid4()}/file", headers=w["h"][ADMIN])).status_code == 404
        assert (await c.get(f"{BASE}/{p['id']}/items/{frame['id']}/file", headers=w["h"][OPERATOR])).status_code == 403
        p["kept"]["files"]["frame"].unlink()
        gone = await c.get(f"{BASE}/{p['id']}/items/{frame['id']}/file", headers=w["h"][ADMIN])
        assert gone.status_code == 404 and gone.json()["detail"] == "The file is no longer on disk."
        chain = await _chain(c, w, p["id"])
    taken = [s for s in chain if s["step"] == "DOWNLOADED"]
    assert [(s["kind"], s["actor_role"]) for s in taken] == [("SNAPSHOT", SUPERVISOR), ("RECORDING", ADMIN)], \
        "a download that handed nothing over is not a step"
    logged = await _sql("SELECT action FROM evidence_access_log WHERE evidence_id = :e", {"e": frame["ref_id"]})
    assert [r["action"] for r in logged] == ["download"]


async def test_sharing_and_releasing_are_a_persons_statement_with_who_and_why(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots)
        body = {"step": "SHARED", "recipient": "  Insp. Lee Kah Wai ", "organisation": "Jurong Police Division",
                "reason": "Requested under the police report."}
        early = await _post(c, w, f"{p['id']}/disclosures", body)
        assert early.status_code == 409 and "has not been sealed" in early.json()["detail"]
        await _post(c, w, f"{p['id']}/seal")
        for bad in ({**body, "step": "SENT"}, {**body, "recipient": ""}, {**body, "reason": "why"},
                    {**body, "reason": "      "}, {k: v for k, v in body.items() if k != "reason"}):
            assert (await _post(c, w, f"{p['id']}/disclosures", bad)).status_code == 422, bad
        assert (await _post(c, w, f"{p['id']}/disclosures", body, OPERATOR)).status_code == 403
        shared = await _post(c, w, f"{p['id']}/disclosures", body, SUPERVISOR)
        released = await _post(c, w, f"{p['id']}/disclosures", {
            "step": "RELEASED", "recipient": "Acme Insurance", "reason": "Claim 4471, as the client instructed."})
        assert (shared.status_code, shared.json(), released.json()) == (201, {"recorded": "SHARED"},
                                                                       {"recorded": "RELEASED"})
        chain = await _chain(c, w, p["id"])
    said = [(s["step"], s["reason"], s["detail"], s["actor_role"]) for s in chain if s["step"] in ("SHARED", "RELEASED")]
    assert said == [
        ("SHARED", "Requested under the police report.",
         {"recipient": "Insp. Lee Kah Wai", "organisation": "Jurong Police Division"}, SUPERVISOR),
        ("RELEASED", "Claim 4471, as the client instructed.", {"recipient": "Acme Insurance", "organisation": None},
         ADMIN)]
    assert len(await _audit(w, "evidence.package.disclose")) == 2


# ─── E. The chain of custody ─────────────────────────────────────────────────

async def test_one_chain_from_capture_to_release_with_who_in_what_role_and_why(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots, who=OPERATOR)
        frame = str(p["kept"]["ids"]["frame"])
        # Somebody opens the frame on the Evidence screen, as they always could: the existing log.
        r = await c.post(f"/api/v1/custody/{frame}", headers=w["h"][VIEWER], params={"action": "view"})
        assert r.status_code == 200, r.text
        await _post(c, w, f"{p['id']}/seal", who=OPERATOR)
        await _get(c, w, p["id"], ADMIN)
        await _get(c, w, p["id"], SUPERVISOR)
        await _get(c, w, p["id"], SUPERVISOR)
        await _export(c, w, p["id"], SUPERVISOR)
        await _post(c, w, f"{p['id']}/disclosures", {"step": "RELEASED", "recipient": "Acme Insurance",
                                                     "reason": "Claim 4471."}, MANAGER)
        await _post(c, w, f"{p['id']}/release-holds", {"reason": "The claim is settled."}, MANAGER)
        answer = (await c.get(f"{BASE}/{p['id']}/custody", headers=w["h"][SUPERVISOR])).json()
        for role in (OPERATOR, VIEWER):
            r = await c.get(f"{BASE}/{p['id']}/custody", headers=w["h"][role])
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: evidence:custody:read"
    chain = answer["chain"]
    assert (answer["package_number"], answer["status"]) == (p["number"], "SEALED")
    assert "the platform sends nothing to anybody" in answer["note"]
    times = [s["at"] for s in chain]
    assert times == sorted(times), "oldest first"
    count = {}
    for step in chain:
        count[step["step"]] = count.get(step["step"], 0) + 1
    assert count == {"CAPTURED": 5, "COLLECTED": 5, "ACCESSED": 4, "LOCKED": 5, "SEALED": 1, "VIEWED": 2,
                     "EXPORTED": 1, "RELEASED": 1, "UNLOCKED": 5}, count
    of_frame = [s for s in chain if s["ref_id"] == frame]
    assert [s["step"] for s in of_frame] == ["CAPTURED", "COLLECTED", "ACCESSED", "LOCKED", "ACCESSED", "UNLOCKED"]
    captured, collected, opened, locked, exported, unlocked = of_frame
    assert (captured["actor_name"], captured["source"]) == (None, "the item itself")
    assert captured["detail"] == {"checksum_sha256": _sha(p["kept"]["data"]["frame"])}
    assert (collected["actor_name"], collected["actor_role"]) == (f"Role {OPERATOR} User", OPERATOR)
    assert collected["detail"]["goes_with"]["kind"] == "PLATE_READ"
    assert (opened["detail"]["how"], opened["actor_role"], opened["source"]) == ("Opened", VIEWER, "evidence_access_log")
    assert locked["reason"] == f"Sealed in evidence package {p['number']}."
    assert (exported["detail"], exported["actor_role"]) == ({"how": "Exported", "checksum_verified": True}, SUPERVISOR)
    assert (unlocked["reason"], unlocked["actor_role"]) == ("The claim is settled.", MANAGER)
    viewed = [s for s in chain if s["step"] == "VIEWED"]
    assert sorted(s["actor_role"] for s in viewed) == [ADMIN, SUPERVISOR], "once a person in an hour, not once a look"
    sealed = next(s for s in chain if s["step"] == "SEALED")
    assert sealed["detail"]["manifest_sha256"] == answer["manifest_sha256"] and sealed["detail"]["items"] == 5
    assert "storage_path" not in json.dumps(answer) and str(w["tenant"]) not in json.dumps(answer)


async def test_every_step_is_also_in_the_audit_log(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots, who=OPERATOR)
        drone = next(i for i in (await _get(c, w, p["id"]))["items"] if i["kind"] == "DRONE_MEDIA")
        await c.delete(f"{BASE}/{p['id']}/items/{drone['id']}", headers=w["h"][OPERATOR])
        await _post(c, w, f"{p['id']}/seal", who=OPERATOR)
        await _export(c, w, p["id"], SUPERVISOR)
        frame = next(i for i in (await _get(c, w, p["id"]))["items"] if i["kind"] == "SNAPSHOT")
        await c.get(f"{BASE}/{p['id']}/items/{frame['id']}/file", headers=w["h"][SUPERVISOR])
        await _post(c, w, f"{p['id']}/release-holds", {"reason": "The claim is settled."}, ADMIN)
        await _post(c, w, f"{p['id']}/release-holds", {"reason": "And again."}, ADMIN)
    expected = {
        "evidence.package.create": (OPERATOR, {"number": p["number"], "investigation_id": p["investigation"]}),
        "evidence.package.item.add": (OPERATOR, {"number": p["number"]}),
        "evidence.package.item.remove": (OPERATOR, {"kind": "DRONE_MEDIA"}),
        "evidence.package.seal": (OPERATOR, {"items": 4}),
        "evidence.package.export": (SUPERVISOR, {"included": 4, "verified": 4, "mismatched": 0,
                                                 "reason": "Requested by the insurer's assessor."}),
        "evidence.package.download": (SUPERVISOR, {"kind": "SNAPSHOT"}),
        "evidence.package.holds.release": (ADMIN, {"released": 4, "reason": "The claim is settled."}),
    }
    for action, (role, detail) in expected.items():
        rows = await _audit(w, action)
        assert len(rows) == 1, f"{action}: once, and not for the request that changed nothing"
        row = rows[0]
        assert row["user_id"] == w["users"][role] and row["row_hash"], action
        assert (row["resource_type"], str(row["resource_id"])) == ("evidence_package", p["id"]), action
        assert row["detail"]["actor_role"] == role and row["detail"]["site_id"] == str(w["site_a"])
        assert detail.items() <= row["detail"].items(), (action, row["detail"])


# ─── F. Holds, and the retention jobs ────────────────────────────────────────

async def _purge_everything(w: dict, roots) -> dict:
    """Run the three retention jobs as the application's own role, with a
    retention of no days: everything this organisation holds is old enough to go."""
    evidence_root, recordings_root = roots
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
               "VALUES (:t, 'evidence.retention_days', '0'::jsonb, :u) "
               "ON CONFLICT (tenant_id, setting_key) DO NOTHING", {"t": w["tenant"], "u": w["users"][ADMIN]})
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar(), \
            "a purge that can see past row-level security proves nothing about one that cannot"
        frames = await scheduler_main.purge_expired_evidence(db)
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        recordings = await continuous_recording.purge_expired_recordings(db, str(recordings_root), 0)
    async with AsyncSessionLocal() as db:
        media = await drone_retention.purge_media(db, str(w["tenant"]), datetime.now(timezone.utc), 0)
    return {"frames": frames, "recordings": recordings, "media": media["media_deleted"]}


async def _left(w: dict) -> dict:
    rows = await _sql("""
        SELECT 'evidence' AS t, count(*) AS n FROM evidence WHERE tenant_id = :t
        UNION ALL SELECT 'recordings', count(*) FROM recordings WHERE tenant_id = :t
        UNION ALL SELECT 'media', count(*) FROM drone_event_media WHERE tenant_id = :t""", {"t": w["tenant"]})
    return {r["t"]: r["n"] for r in rows}


async def test_what_is_held_is_left_alone_by_every_purge_and_everything_else_goes_as_before(roots):
    mine, theirs = await _world(), await _world()
    async with _client() as c:
        p = await _ready(c, mine, roots, seal=True)
        # Another organisation's evidence, never packaged: its purge must be untouched by any of this.
        other = await _kept(theirs, await _seed(theirs), roots)
    kept = p["kept"]
    assert await _left(mine) == {"evidence": 4, "recordings": 1, "media": 1}

    await _purge_everything(theirs, roots)
    purged = await _purge_everything(mine, roots)
    assert await _left(theirs) == {"evidence": 0, "recordings": 0, "media": 0}, "nothing held: purged as before"
    assert not any(f.exists() for f in other["files"].values())
    assert await _left(mine) == {"evidence": 3, "recordings": 1, "media": 1}, \
        "the frame that was in no package is gone; what is held is all still here"
    assert purged["recordings"] == 0 and purged["media"] == 0
    assert not kept["files"]["stranger"].exists()
    for name in ("frame", "crop", "clip", "recording", "drone"):
        assert kept["files"][name].read_bytes() == kept["data"][name], f"the {name} was held and is intact"

    async with _client() as c:
        # Still there to export, after everything else of its age was deleted.
        r, archive = await _export(c, mine, p["id"])
        assert (r.status_code, r.headers["x-evidence-verified"]) == (200, "5")
        lifted = await _post(c, mine, f"{p['id']}/release-holds", {"reason": "The claim is settled."})
        assert lifted.json() == {"released": 5}
        again = await _post(c, mine, f"{p['id']}/release-holds", {"reason": "And again."})
        assert again.status_code == 409 and "no hold in force" in again.json()["detail"]
    purged = await _purge_everything(mine, roots)
    assert (purged["recordings"], purged["media"]) == (1, 1)
    assert await _left(mine) == {"evidence": 0, "recordings": 0, "media": 0}, "lifted, so kept no longer than anything"
    assert not any(f.exists() for f in kept["files"].values())

    async with _client() as c:
        got = await _get(c, mine, p["id"])
        r, archive = await _export(c, mine, p["id"])
    assert (got["status"], got["intact"], got["counts"]["held"], got["counts"]["not_shown"]) == ("SEALED", True, 0, 5)
    assert all(i["state"] == "NOT_AVAILABLE" for i in got["items"])
    export = json.loads(archive.read("export.json"))
    assert (export["included"], export["left_out"]) == (0, 5), "the manifest remains; it says what there was"
    assert len(json.loads(archive.read("manifest.json"))["items"]) == 5


async def test_a_hold_is_placed_by_hand_with_a_reason_and_lifted_with_one(roots):
    w = await _world()
    seed = await _seed(w)
    kept = await _kept(w, seed, roots)
    stranger = {"kind": "SNAPSHOT", "id": str(kept["ids"]["stranger"]), "captured_at": _iso(seed["at"]["PLATE_READ"])}
    async with _client() as c:
        for bad, code in (({**stranger, "reason": ""}, 422), ({**stranger, "reason": "why"}, 422),
                          ({**stranger, "reason": "      "}, 422), ({**stranger, "kind": "PICTURE", "reason": "A reason."}, 422),
                          ({**stranger, "id": str(uuid.uuid4()), "reason": "A reason."}, 404),
                          ({**stranger, "captured_at": "2026-10-05T01:00:00", "reason": "A reason."}, 422)):
            r = await c.post(HOLDS, headers=w["h"][ADMIN], json=bad)
            assert r.status_code == code, (bad, r.text)
        for role in (SUPERVISOR, OPERATOR, VIEWER):
            r = await c.post(HOLDS, headers=w["h"][role], json={**stranger, "reason": "Police asked for it."})
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: evidence:hold:manage"
        placed = await c.post(HOLDS, headers=w["h"][MANAGER], json={**stranger, "reason": " Police asked for it. "})
        assert placed.status_code == 201, placed.text
        hold_id = placed.json()["id"]
        listed = (await c.get(HOLDS, headers=w["h"][VIEWER])).json()
        assert listed["total"] == 1
        (hold,) = listed["items"]
        assert (hold["kind"], hold["ref_id"], hold["reason"], hold["placed_by_name"], hold["package_number"]) == (
            "SNAPSHOT", stranger["id"], "Police asked for it.", f"Role {MANAGER} User", None)
        assert (await c.get(HOLDS, headers=w["h"][ADMIN], params={"kind": "PICTURE"})).status_code == 422

    await _purge_everything(w, roots)
    assert kept["files"]["stranger"].exists() and not kept["files"]["frame"].exists(), "the one held by hand is kept"

    async with _client() as c:
        for bad in ({}, {"reason": "ok"}, {"reason": "      "}):
            assert (await c.post(f"{HOLDS}/{hold_id}/release", headers=w["h"][ADMIN], json=bad)).status_code == 422
        assert (await c.post(f"{HOLDS}/{hold_id}/release", headers=w["h"][SUPERVISOR],
                             json={"reason": "No longer needed."})).status_code == 403
        assert (await c.post(f"{HOLDS}/{uuid.uuid4()}/release", headers=w["h"][ADMIN],
                             json={"reason": "No longer needed."})).status_code == 404
        r = await c.post(f"{HOLDS}/{hold_id}/release", headers=w["h"][ADMIN], json={"reason": "The case is closed."})
        assert r.status_code == 200 and r.json() == {"released": True}
        twice = await c.post(f"{HOLDS}/{hold_id}/release", headers=w["h"][ADMIN], json={"reason": "Again."})
        assert twice.status_code == 409
        assert (await c.get(HOLDS, headers=w["h"][ADMIN])).json()["total"] == 0
        (lifted,) = (await c.get(HOLDS, headers=w["h"][ADMIN], params={"in_force": "false"})).json()["items"]
    assert (lifted["release_reason"], lifted["released_by_name"]) == ("The case is closed.", f"Role {ADMIN} User")
    await _purge_everything(w, roots)
    assert not kept["files"]["stranger"].exists()
    steps = await _sql("SELECT step, reason, actor_role, package_id FROM evidence_custody_events WHERE tenant_id = :t "
                       " ORDER BY occurred_at", {"t": w["tenant"]})
    assert [(s["step"], s["reason"], s["actor_role"], s["package_id"]) for s in steps] == [
        ("LOCKED", "Police asked for it.", MANAGER, None), ("UNLOCKED", "The case is closed.", ADMIN, None)]
    assert len(await _audit(w, "evidence.hold.place")) == 1 and len(await _audit(w, "evidence.hold.release")) == 1


# ─── G. Who may ──────────────────────────────────────────────────────────────

async def test_who_may_do_what_with_a_package(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots)
        item = (await _get(c, w, p["id"]))["items"][0]
        writes = (
            ("POST", "", {"title": "Mine", "purpose": "Because I want one.", "investigation_id": p["investigation"]}),
            ("POST", f"/{p['id']}/items", {"items": _refs([item["thing"]])}),
            ("DELETE", f"/{p['id']}/items/{item['id']}", None),
            ("POST", f"/{p['id']}/seal", {}),
        )
        for method, path, body in writes:
            r = await c.request(method, f"{BASE}{path}", headers=w["h"][VIEWER], json=body)
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: evidence:package:manage", path
            r = await c.request(method, f"{BASE}{path}", headers=w["h"][GUARD], json=body)
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: evidence:package:read", path
        viewer = await _get(c, w, p["id"], VIEWER)
        assert viewer["can"] == {"manage": False, "export": False, "hold": False, "custody": False}
        assert (await c.get(BASE, headers=w["h"][VIEWER])).json()["total"] == 1
        assert (await _get(c, w, p["id"], SUPERVISOR))["can"] == {"manage": True, "export": True, "hold": False,
                                                                 "custody": True}
        assert (await _get(c, w, p["id"], OPERATOR))["can"] == {"manage": True, "export": False, "hold": False,
                                                                "custody": False}
        for role in (1, 7):
            headers = _auth(uuid.uuid4(), w["tenant"], role)
            for method, path in (("GET", ""), ("GET", f"/{p['id']}"), ("GET", f"/{p['id']}/candidates"),
                                 ("GET", f"/{p['id']}/custody"), ("POST", f"/{p['id']}/export")):
                r = await c.request(method, f"{BASE}{path}", headers=headers, json={} if method == "POST" else None)
                assert r.status_code == 403, (role, path)
            assert (await c.get(HOLDS, headers=headers)).status_code == 403
        assert (await c.get(BASE)).status_code == 401
        await _post(c, w, f"{p['id']}/seal")
        for role in (SUPERVISOR, OPERATOR, VIEWER):
            r = await _post(c, w, f"{p['id']}/release-holds", {"reason": "The claim is settled."}, role)
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: evidence:hold:manage"


async def test_the_permissions_are_held_by_the_roles_that_handle_evidence():
    rows = await _sql("SELECT rp.role_id, p.code FROM role_permissions rp JOIN permissions p ON p.id = "
                      "rp.permission_id WHERE p.code LIKE 'evidence:package:%' OR p.code = 'evidence:hold:manage'")
    held: dict[str, set[int]] = {}
    for r in rows:
        held.setdefault(r["code"], set()).add(r["role_id"])
    assert held == {"evidence:package:read": {2, 3, 4, 6, 8}, "evidence:package:manage": {2, 3, 4, 8},
                    "evidence:package:export": {2, 3, 8}, "evidence:hold:manage": {2, 8}}


async def test_an_api_key_and_a_support_session_do_not_handle_evidence(roots):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots, seal=True)
    for not_a_person, why in (
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True),
         "not by an API key"),
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN,
                      support_session_id=str(uuid.uuid4())), "not from a support session"),
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as refused:
            api._a_person(not_a_person)
        assert refused.value.status_code == 403 and why in refused.value.detail
        if not_a_person.via_api_key:
            app.dependency_overrides[get_token_payload] = lambda who=not_a_person: who
            try:
                async with _client() as c:
                    for method, path in (("GET", BASE), ("GET", f"{BASE}/{p['id']}"), ("GET", HOLDS),
                                         ("POST", f"{BASE}/{p['id']}/export")):
                        r = await c.request(method, path, json={"reason": "An integration wants it."}
                                            if method == "POST" else None)
                        assert r.status_code == 403 and why in r.json()["detail"], (path, r.text)
            finally:
                app.dependency_overrides.pop(get_token_payload, None)
    api._a_person(TokenPayload(user_id="u", tenant_id="t", role_id=ADMIN))
    assert await _audit(w, "evidence.package.export") == []


async def test_someone_held_to_a_site_handles_that_sites_evidence_and_no_other(roots):
    w = await _world()
    seed_a, seed_b = await _seed(w, site="site_a", tag="A"), await _seed(w, site="site_b", tag="B")
    kept_a, kept_b = await _kept(w, seed_a, roots), await _kept(w, seed_b, roots, site="site_b")
    async with _client() as c:
        file_a = await _investigation(c, w, seed_a, who=SUPERVISOR)
        file_b = await _investigation(c, w, seed_b, site="site_b")
        # Spanning both sites, with a record of each in it.
        both = await _investigation(c, w, seed_a, site=None, kinds=("PLATE_READ",))
        await c.post(f"{INVESTIGATIONS}/{both}/items", headers=w["h"][ADMIN],
                     json={"records": [_record(seed_b, "PLATE_READ")]})
        mine = await _package(c, w, SUPERVISOR, investigation_id=file_a)
        theirs = await _package(c, w, ADMIN, investigation_id=file_b)
        spanning = await _package(c, w, ADMIN, investigation_id=both)
        for hidden in (file_b, both):
            refused = await _package(c, w, SUPERVISOR, expect=404, investigation_id=hidden)
            assert refused["detail"] == "Investigation not found"
        assert (await _package(c, w, SUPERVISOR, expect=404, incident_id=str(seed_b["ids"]["INCIDENT"])))["detail"] == \
            "Incident not found"
        listed = (await c.get(BASE, headers=w["h"][SUPERVISOR])).json()
        assert [i["id"] for i in listed["items"]] == [mine["id"]]
        for hidden in (theirs, spanning):
            for method, path in (("GET", ""), ("GET", "/candidates"), ("POST", "/seal"), ("GET", "/custody")):
                r = await c.request(method, f"{BASE}/{hidden['id']}{path}", headers=w["h"][SUPERVISOR])
                assert r.status_code == 404, (path, r.text)
        offered = (await _candidates(c, w, mine["id"], SUPERVISOR))["items"]
        assert {i["site_name"] for i in offered} == {"Factory A"} and len(offered) == 5
        wide = (await _candidates(c, w, spanning["id"], ADMIN))["items"]
        assert {i["site_name"] for i in wide} == {"Factory A", "Factory B"}
        other_frame = {"kind": "SNAPSHOT", "id": str(kept_b["ids"]["frame"]),
                       "captured_at": _iso(seed_b["at"]["PLATE_READ"])}
        r = await _post(c, w, f"{mine['id']}/items", {"items": [other_frame]}, SUPERVISOR)
        assert r.status_code == 404, "another site's frame is not theirs to put anywhere"
        hold = await c.post(HOLDS, headers=w["h"][MANAGER], json={**other_frame, "reason": "Police asked for it."})
        assert hold.status_code == 201
        assert (await c.get(HOLDS, headers=w["h"][SUPERVISOR])).json()["total"] == 0
        assert (await c.get(HOLDS, headers=w["h"][ADMIN])).json()["total"] == 1
    assert kept_a["ids"]["frame"] != kept_b["ids"]["frame"]


async def test_another_organisations_packages_and_evidence_are_never_reached(roots):
    mine, theirs = await _world(), await _world()
    async with _client() as c:
        p = await _ready(c, theirs, roots, seal=True)
        item = (await _get(c, theirs, p["id"]))["items"][0]
        for method, path in (("GET", ""), ("GET", "/candidates"), ("GET", "/custody"), ("POST", "/export"),
                             ("POST", "/release-holds"), ("GET", f"/items/{item['id']}/file")):
            r = await c.request(method, f"{BASE}/{p['id']}{path}", headers=mine["h"][ADMIN],
                                json={"reason": "Let me in please."} if method == "POST" else None)
            assert r.status_code == 404, (path, r.text)
        assert (await c.get(BASE, headers=mine["h"][ADMIN])).json()["total"] == 0
        assert (await c.get(HOLDS, headers=mine["h"][ADMIN])).json()["total"] == 0
        seed = await _seed(mine)
        file_id = await _investigation(c, mine, seed)
        own = await _package(c, mine, investigation_id=file_id)
        stolen = {"kind": item["kind"], "id": item["ref_id"], "captured_at": item["captured_at"]}
        assert (await _post(c, mine, f"{own['id']}/items", {"items": [stolen]})).status_code == 404
        hold = await c.post(HOLDS, headers=mine["h"][ADMIN], json={**stolen, "reason": "Somebody else's frame."})
        assert hold.status_code == 404
        assert own["package_number"] == p["number"], "numbered per organisation"
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(mine["tenant"])})
        for table, expected in (("evidence_packages", 1), ("evidence_package_items", 0), ("evidence_holds", 0),
                                ("evidence_custody_events", 0)):
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == expected, table
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text("INSERT INTO evidence_holds (tenant_id, kind, ref_id, reason) "
                                  "VALUES (:t, 'CLIP', gen_random_uuid(), 'Planted.')"), {"t": theirs["tenant"]})
        await db.rollback()
    for table in ("evidence_packages", "evidence_package_items", "evidence_holds", "evidence_custody_events"):
        row = (await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n",
                          {"n": table}))[0]
        assert row["relrowsecurity"] and row["relforcerowsecurity"], table


# ─── H. What the application's role can and cannot do ────────────────────────

async def test_the_application_role_cannot_rewrite_custody_remove_a_hold_or_delete_a_package(roots):
    w = await _world()
    async with _client() as c:
        draft = await _ready(c, w, roots)
    refused = (
        "DELETE FROM evidence_packages WHERE id = :p",
        "UPDATE evidence_packages SET title = 'Rewritten' WHERE id = :p",
        "UPDATE evidence_packages SET purpose = 'For somebody else.' WHERE id = :p",
        "UPDATE evidence_packages SET package_number = 'EVP-1' WHERE id = :p",
        "UPDATE evidence_packages SET created_by_user_id = NULL WHERE id = :p",
        "UPDATE evidence_package_items SET checksum_sha256 = repeat('0', 64) WHERE package_id = :p",
        "UPDATE evidence_package_items SET ref_id = gen_random_uuid() WHERE package_id = :p",
        "DELETE FROM evidence_custody_events WHERE package_id = :p",
        "UPDATE evidence_custody_events SET reason = 'Rewritten' WHERE package_id = :p",
        "UPDATE evidence_custody_events SET actor_user_id = NULL WHERE package_id = :p",
        "DELETE FROM evidence_holds",
        "UPDATE evidence_holds SET reason = 'Rewritten'",
        "UPDATE evidence_holds SET ref_id = gen_random_uuid()",
        "TRUNCATE evidence_custody_events",
        "TRUNCATE evidence_holds",
        "TRUNCATE evidence_packages CASCADE",
    )
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        for statement in refused:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement), {"p": draft["id"]} if ":p" in statement else {})
            await db.rollback()
    assert (await _sql("SELECT count(*) AS n FROM evidence_custody_events WHERE package_id = :p",
                       {"p": draft["id"]}))[0]["n"] == 5, "the five that were collected, as they were"
    assert MIGRATION.count("REVOKE ALL ON {table} FROM svc_app") == 1 and "GRANT ALL" not in MIGRATION
    changes = re.search(r'^PACKAGE_CHANGES = "(.*)"$', MIGRATION, re.M).group(1)
    assert set(changes.split(", ")) == {"status", "sealed_by_user_id", "sealed_at", "manifest", "manifest_sha256",
                                       "updated_at"}
    assert re.search(r'^HOLD_CHANGES = "(.*)"$', MIGRATION, re.M).group(1) == \
        "released_by_user_id, released_at, release_reason"


async def test_the_database_itself_refuses_a_package_a_hold_or_a_step_without_what_it_needs():
    w = await _world()
    package = uuid.uuid4()
    await _sql("INSERT INTO evidence_packages (id, tenant_id, package_number, title, purpose) "
               "VALUES (:p, :t, 'EVP-X-1', 'A title', 'A purpose.')", {"p": package, "t": w["tenant"]})
    for statement, constraint in (
        ("INSERT INTO evidence_packages (tenant_id, package_number, title, purpose) VALUES (:t, 'EVP-X-2', 'T', ' ')",
         "ck_evpkg_purpose"),
        ("INSERT INTO evidence_packages (tenant_id, package_number, title, purpose) VALUES (:t, 'EVP-X-1', 'T', 'P')",
         "uq_evidence_package_number"),
        ("INSERT INTO evidence_packages (tenant_id, package_number, title, purpose, status) "
         "VALUES (:t, 'EVP-X-3', 'T', 'P', 'SEALED')", "ck_evpkg_sealed"),
        ("INSERT INTO evidence_packages (tenant_id, package_number, title, purpose, status, sealed_at, manifest, "
         "manifest_sha256) VALUES (:t, 'EVP-X-4', 'T', 'P', 'SEALED', now(), '{}'::jsonb, 'not-a-hash')",
         "ck_evpkg_sealed"),
        ("INSERT INTO evidence_package_items (tenant_id, package_id, kind, ref_id, captured_at) "
         "VALUES (:t, :p, 'PICTURE', gen_random_uuid(), now())", "ck_evitem_kind"),
        ("INSERT INTO evidence_holds (tenant_id, kind, ref_id, reason) VALUES (:t, 'CLIP', gen_random_uuid(), ' ')",
         "ck_evhold_reason"),
        ("INSERT INTO evidence_holds (tenant_id, kind, ref_id, reason, released_at) "
         "VALUES (:t, 'CLIP', gen_random_uuid(), 'A reason.', now())", "ck_evhold_release"),
        ("INSERT INTO evidence_custody_events (tenant_id, package_id, step, actor_role) VALUES (:t, :p, 'LOST', 2)",
         "ck_evcust_step"),
        ("INSERT INTO evidence_custody_events (tenant_id, package_id, step, actor_role) VALUES (:t, :p, 'SHARED', 2)",
         "ck_evcust_why"),
        ("INSERT INTO evidence_custody_events (tenant_id, package_id, step, actor_role, reason) "
         "VALUES (:t, :p, 'RELEASED', 2, ' ')", "ck_evcust_why"),
        ("INSERT INTO evidence_custody_events (tenant_id, step, actor_role) VALUES (:t, 'VIEWED', 2)",
         "ck_evcust_what"),
        ("INSERT INTO evidence_custody_events (tenant_id, package_id, kind, step, actor_role) "
         "VALUES (:t, :p, 'CLIP', 'VIEWED', 2)", "ck_evcust_about"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(statement, {"t": w["tenant"], "p": package} if ":p" in statement else {"t": w["tenant"]})
    steps = set(re.findall(r"'([A-Z_]+)'", re.search(r'^STEPS = "(.*)"$', MIGRATION, re.M).group(1)))
    written = set(re.findall(r'_custody\(db, request, token, "([A-Z]+)"', Path(api.__file__).read_text(encoding="utf-8")))
    assert written | {"SHARED", "RELEASED"} == steps, "a step the database allows and nothing writes, or the reverse"


def _needs(route) -> set[str]:
    found: set[str] = set()

    def walk(dep):
        name = getattr(dep.call, "__qualname__", "")
        if "require_permission" in name:
            found.update(c.cell_contents for c in (dep.call.__closure__ or ()) if isinstance(c.cell_contents, str))
        if name == "_a_person":
            found.add("a person")
        for sub in dep.dependencies:
            walk(sub)

    for d in route.dependant.dependencies:
        walk(d)
    return found


def test_every_route_needs_the_permission_and_a_person_and_each_act_needs_its_own():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if (path.startswith(BASE) or path.startswith(HOLDS)) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.replace(":uuid", ""))] = _needs(route) - {"evidence:package:read", "a person"}
                    assert {"evidence:package:read", "a person"} <= _needs(route), path
                    assert "drone" not in path
    manage, export, hold = {"evidence:package:manage"}, {"evidence:package:export"}, {"evidence:hold:manage"}
    assert served == {
        ("GET", BASE): set(), ("POST", BASE): manage, ("GET", f"{BASE}/{{package_id}}"): set(),
        ("GET", f"{BASE}/{{package_id}}/candidates"): set(), ("POST", f"{BASE}/{{package_id}}/items"): manage,
        ("DELETE", f"{BASE}/{{package_id}}/items/{{item_id}}"): manage, ("POST", f"{BASE}/{{package_id}}/seal"): manage,
        ("POST", f"{BASE}/{{package_id}}/export"): export,
        ("GET", f"{BASE}/{{package_id}}/items/{{item_id}}/file"): export,
        ("POST", f"{BASE}/{{package_id}}/disclosures"): export,
        ("POST", f"{BASE}/{{package_id}}/release-holds"): hold,
        ("GET", f"{BASE}/{{package_id}}/custody"): {"evidence:custody:read"},
        ("GET", HOLDS): set(), ("POST", HOLDS): hold, ("POST", f"{HOLDS}/{{hold_id}}/release"): hold,
    }
    code = Path(api.__file__).read_text(encoding="utf-8")
    assert "DELETE FROM evidence_package_items" in code and code.count("DELETE FROM") == 1, \
        "the one thing removed anywhere here is an item from a draft"
    assert "storage_path" not in code and "file_path" not in code, "the API never names where a file is"


async def test_one_person_cannot_export_without_pause(roots, monkeypatch):
    w = await _world()
    async with _client() as c:
        p = await _ready(c, w, roots, seal=True)
        codes = [(await _export(c, w, p["id"], SUPERVISOR))[0].status_code for _ in range(8)]
        other = (await _export(c, w, p["id"], ADMIN))[0].status_code
    assert codes == [200] * 6 + [429] * 2 and other == 200, "counted per person"
    assert len(await _audit(w, "evidence.package.export")) == 7, "a refused export took nothing and is not recorded"
