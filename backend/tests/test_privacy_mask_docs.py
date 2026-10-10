"""Privacy zone masking: the document, the design and the code say the same thing.

The claim somebody relies on is that there is no way for a camera's unmasked
picture to leave the platform. A test of four paths proves the four; it does
not prove there is no fifth. So this also counts the places in the code where a
camera's picture is read at all, and fails when there is one it does not know -
which is the day somebody adds a way out and has to say whether it is masked.

  A — The figures and the rules in the document are the code's
  B — The four places, each painted before what it guards against
  C — Every place a camera's picture is read is one of the known ones
  D — The routes, what each needs, and the two audit lines
  E — The screens: the words, and no player of HLS that has not asked first
  F — The files the document names exist, and the record names what changed

Read from the working tree, so it runs with the repository-inspection suites.
"""
from __future__ import annotations

import inspect
import re

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app import ingestion_main as ingestion
from app.routers import pdpa, streams
from app.services import privacy_mask
from app.services import vpatrol_snapshot as snap
from tests._repo import REPO_ROOT, requires_repo_tree
from tests.test_drone_clients import _served, client_calls

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "PRIVACY_MASKING.md"
DESIGN = REPO_ROOT / "docs" / "plans" / "2026-10-09-privacy-zone-masking-design.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
BACKEND = REPO_ROOT / "backend" / "app"
WEB = REPO_ROOT / "frontend" / "src"
PHONE = REPO_ROOT / "mobile" / "src"
#: The existing files this changed, as the document, the design and the record each list them.
CHANGED = {
    "backend/app/ingestion_main.py", "backend/app/routers/streams.py", "backend/app/routers/pdpa.py",
    "backend/app/services/vpatrol_snapshot.py", "backend/app/services/hls_stream.py", "frontend/src/pages/Zones.tsx",
    "frontend/src/pages/LiveWall.tsx", "frontend/src/pages/intel/Situation.tsx",
}


#: The existing phone files the phone's part changed, a day later.
PHONE_CHANGED = {
    "mobile/src/screens/ZoneDrawScreen.tsx", "mobile/src/screens/CameraLiveScreen.tsx",
    "mobile/src/screens/LiveWallScreen.tsx", "mobile/src/navigation/index.tsx",
}


def _flat(text_: str) -> str:
    return re.sub(r"\s+", " ", text_)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _section(number: int) -> str:
    return _flat(_doc().split(f"## {number}. ", 1)[1].split("\n## ", 1)[0])


def _sources(root, suffixes) -> dict[str, str]:
    """Every source file under a root that is not a test, by its path from the root."""
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="ignore")
            for p in root.rglob("*") if p.suffix in suffixes and "node_modules" not in p.parts
            and ".test." not in p.name and "__tests__" not in p.parts and "__pycache__" not in p.parts}


def _needs(route) -> set[str]:
    found: set[str] = set()

    def walk(dep):
        if "require_permission" in getattr(dep.call, "__qualname__", ""):
            found.update(c.cell_contents for c in (dep.call.__closure__ or ()) if isinstance(c.cell_contents, str))
        for sub in dep.dependencies:
            walk(sub)

    for d in route.dependant.dependencies:
        walk(d)
    return found


def _privacy_routes() -> dict[tuple[str, str], set[str]]:
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if path.startswith("/api/v1/privacy/") and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path)] = _needs(route)
    return served


# ─── A. The figures and the rules ────────────────────────────────────────────

def test_the_figures_in_the_document_are_the_codes():
    doc = _flat(_doc())
    assert (privacy_mask.LEAST_POINTS, privacy_mask.MOST_POINTS, privacy_mask.MOST_ZONES) == (3, 64, 20)
    assert (privacy_mask.REFRESH_SECONDS, privacy_mask.RETRY_SECONDS) == (10.0, 2.0)
    assert "3 to 64 points, each inside the picture, covering some of it" in doc
    assert "at most 20 zones on one camera" in doc
    assert "reads the zones again every **10 seconds**" in doc and "tries again every 2 seconds" in doc
    assert "`privacy_zones` (migration `0010`)" in doc and "No migration was needed." in doc
    # It was built on the table that was there: nothing after the two migrations that made it touches it.
    touching = sorted(p.name[:4] for p in (REPO_ROOT / "backend" / "alembic" / "versions").glob("*.py")
                      if "privacy_zones" in p.read_text(encoding="utf-8"))
    assert touching == ["0010", "0041"], touching
    # The five rules, as headings; and each is how the service says it too.
    rules = re.findall(r"^\d\. \*\*(.+?)\*\*", _doc().split("## 1. The rules", 1)[1].split("\n## ", 1)[0], re.M | re.S)
    assert [_flat(r) for r in rules] == [
        "A mask is painted into the frame.", "It cannot be undone for what is recorded after it.",
        "Nothing unmasked leaves because the zones could not be read.",
        "A zone that cannot be read as a polygon masks the whole picture.",
        "Drawing and deleting are a person's acts, and each is on the record."]
    said = _flat(inspect.getdoc(privacy_mask) or "")
    for rule in ("A MASK IS PAINTED INTO THE FRAME.", "NOTHING UNMASKED LEAVES BECAUSE THE ZONES COULD NOT BE READ.",
                 "A ZONE THAT CANNOT BE READ AS A POLYGON MASKS THE WHOLE PICTURE."):
        assert rule in said, rule


def test_the_design_holds_the_two_decisions_and_the_document_points_to_it():
    design = _flat(DESIGN.read_text(encoding="utf-8"))
    assert "Agreed with the owner on 9 October 2026." in design
    assert "**Everything is masked.**" in design and "**A masked camera has no HLS live view.**" in design
    assert "`docs/plans/2026-10-09-privacy-zone-masking-design.md`" in _doc()
    listed = set(re.findall(r"`((?:backend|frontend)/[A-Za-z_/.]+)`", design.split("## Existing files changed", 1)[1]))
    # The design named six. Building it touched two more: stopping an HLS session, and a second player of HLS.
    assert listed < CHANGED and CHANGED - listed == {"backend/app/services/hls_stream.py", "frontend/src/pages/intel/Situation.tsx"}


# ─── B. The four places ──────────────────────────────────────────────────────

def test_each_of_the_four_places_paints_before_what_it_guards_against():
    where = _section(2)
    for path in ("backend/app/ingestion_main.py", "backend/app/routers/streams.py", "backend/app/services/vpatrol_snapshot.py",
                 "backend/app/services/privacy_mask.py"):
        assert f"`{path}`" in where, path
    # What the AI workers are given: painted before the frame is encoded, buffered or published; and nothing
    # of those three happens before the zones have been read.
    loop = inspect.getsource(ingestion.run_camera_loop)
    kept, painted = loop.index("if not await mask.keep_up():"), loop.index("frame = mask.paint(frame)")
    assert kept < painted < loop.index("_encode_jpeg_b64, frame") < loop.index("_frame_buffers[cam_key].append") < loop.index(
        "redis_client.xadd(")
    assert loop.count("_encode_jpeg_b64") == 1 and loop.count(".xadd(") == 1
    # The live view: the only encoder of a viewer's frame is given the zones, and the route reads them first.
    assert inspect.getsource(streams._masked_jpeg).strip().endswith("return _encode_jpeg(privacy_mask.paint(frame, zones))")
    live = inspect.getsource(streams._mjpeg_frames)
    assert "_masked_jpeg, frame, mask.zones" in live and "_encode_jpeg" not in live.replace("_masked_jpeg", "")
    route = inspect.getsource(streams.live_stream)
    assert route.index("if not await mask.refresh():") < route.index("_mjpeg_frames(rtsp_url, mask)")
    assert "HTTP_503_SERVICE_UNAVAILABLE" in route
    # A recording: every frame the writer is given has been painted, and the task waits for the zones.
    recorder = inspect.getsource(streams._record_to_file)
    assert recorder.count("writer.write(") == 1 and "writer.write(mask.paint(frame))" in recorder
    task = inspect.getsource(streams._recording_task)
    assert task.index("while not await mask.refresh() and not stop_event.is_set():") < task.index("if mask.loaded:") < task.index(
        "_record_to_file, file_path, rtsp_url, stop_event, mask")
    # A patrol's image: painted before it is hashed, and not kept if it could not be.
    capture = inspect.getsource(snap.capture)
    assert capture.index("await _mask(db,") < capture.index("abs_path.unlink(missing_ok=True)\n        await _record_failure(db, session_camera_id, \"SNAPSHOT_FAILED\", unmasked)") \
        < capture.index("hashlib.sha256(data)")
    assert "db.begin_nested()" in inspect.getsource(snap._mask)
    # HLS cannot be painted: both of its routes refuse a masked camera after they have checked who is asking.
    for handler in (streams.hls_playlist, streams.hls_segment):
        code = inspect.getsource(handler)
        assert code.index("_authorize_stream_access(") < code.index("_no_hls_for_a_masked_camera(token, camera_id, stream_id)")
    refuse = inspect.getsource(streams._no_hls_for_a_masked_camera)
    assert "HTTP_409_CONFLICT" in refuse and "HTTP_503_SERVICE_UNAVAILABLE" in refuse and "await stop_stream(stream_id)" in refuse
    assert "answer 409 for a camera with an active zone" in where and "a session already running for it is stopped" in where


def test_what_happens_when_the_zones_cannot_be_read_is_what_the_document_says():
    table = _doc().split("## 3. Keeping up, and failing safe", 1)[1].split("\n## ", 1)[0]
    rows = dict(re.findall(r"^\| ([^|]+?) \| ([^|]+?) \|$", table, re.M)[1:])
    assert list(rows) == ["Ingestion, before its first read", "The live view, as it is opened", "The recorder, before its first read",
                          "A patrol snapshot", "HLS", "Any loop, after a read that succeeded"]
    assert rows["The live view, as it is opened"].startswith("503") and rows["HLS"].startswith("503")
    assert rows["Any loop, after a read that succeeded"] == "The last zones read are kept"
    # The keeper is where that is so: it has nothing before its first read, and a failed read assigns nothing.
    refresh = inspect.getsource(privacy_mask.Keeper.refresh)
    assert refresh.count("self._zones =") == 1 and refresh.index("self._zones =") < refresh.index("except Exception")
    assert "raise NotLoaded" in inspect.getsource(privacy_mask.Keeper) and "return paint(frame, self.zones)" in inspect.getsource(
        privacy_mask.Keeper.paint)
    # And a zone that cannot be read as a polygon masks everything.
    assert "frame[:] = 0" in inspect.getsource(privacy_mask.paint)


# ─── C. Every place a camera's picture is read ───────────────────────────────

def test_a_cameras_picture_is_read_in_the_known_places_and_nowhere_else():
    """A new place here is a new way for a picture to leave. Whoever adds one
    says in PRIVACY_MASKING.md whether it is masked, and then adds it below."""
    code = _sources(BACKEND, (".py",))
    # Frames decoded by OpenCV. The camera probe reads one frame and gives back its size, not the picture.
    assert sorted(p for p, text_ in code.items() if "VideoCapture" in text_) == [
        "ingestion_main.py", "routers/cameras.py", "routers/streams.py"]
    assert "ret, _ = cap.read()" in code["routers/cameras.py"] and "imencode" not in code["routers/cameras.py"]
    # Frames turned into a picture somebody is given.
    assert sorted(p for p, text_ in code.items() if "imencode" in text_) == ["ingestion_main.py", "routers/streams.py"]
    # ffmpeg, which reads a camera without this code seeing the frame: HLS (refused when masked), the patrol
    # snapshot (painted afterwards), and two that are only given files and frames that are masked already.
    assert sorted(p for p, text_ in code.items() if "ffmpeg" in text_) == [
        "main.py", "routers/streams.py", "services/hls_stream.py", "services/video_compat.py", "services/vpatrol_snapshot.py"]
    assert "create_subprocess_exec" not in code["main.py"] and "create_subprocess_exec" not in code["routers/streams.py"]
    # What reads the zones: the router that keeps them, and the service that applies them. Nothing else has its own idea of one.
    readers = sorted(p for p, text_ in code.items() if "privacy_zones" in text_)
    assert readers == ["routers/pdpa.py", "services/privacy_mask.py"], readers
    # The AI worker is given frames; it reads no camera and no zone.
    worker = _sources(REPO_ROOT / "ai-worker" / "worker", (".py",))
    assert worker and not [p for p, text_ in worker.items() if "VideoCapture" in text_ or "privacy" in text_.lower()]


# ─── D. The routes ───────────────────────────────────────────────────────────

def test_the_routes_are_the_ones_served_and_each_needs_what_the_document_says():
    table = _doc().split("| Route | Needs | What it does |", 1)[1].split("\n\n", 1)[0]
    said = {tuple(route.split(" ", 1)): {needs} for route, needs in re.findall(r"^\| `([A-Z]+ [^`]+)` \| `([a-z:]+)` \|", table, re.M)}
    served = _privacy_routes()
    assert said == served, (set(said) ^ set(served), said, served)
    assert len(served) == 5
    # Drawing and deleting are a person's, on a camera they may see, and audited under the names the document gives.
    for handler, action in ((pdpa.create_privacy_zone, "privacy.zone.create"), (pdpa.delete_privacy_zone, "privacy.zone.delete")):
        code = inspect.getsource(handler)
        assert code.index("_a_person(token)") < code.index("site_scope_clause(") < code.index(f'"{action}"') < code.index(
            "await db.commit()")
        assert f"`{action}`" in _doc()
        # The tenant is the transaction's: nothing is read after the commit.
        assert "db.execute" not in code.split("await db.commit()", 1)[1]
    assert '"polygon"' not in inspect.getsource(pdpa.create_privacy_zone).split("intel_audit.record(", 1)[1].split(")", 1)[0]
    assert "It does not hold the polygon" in _flat(_doc())
    assert "EXISTS (SELECT 1 FROM drones d WHERE d.camera_id = c.id)" in inspect.getsource(pdpa.create_privacy_zone)
    assert "not a drone's camera" in _section(4)


# ─── E. The screens ──────────────────────────────────────────────────────────

def test_no_player_plays_a_fixed_camera_through_hls_without_asking_first():
    web = _sources(WEB, (".ts", ".tsx"))
    players = sorted(p for p, text_ in web.items() if "hls/index.m3u8" in text_)
    # The drone screen plays a drone's camera, which cannot have a zone (the API refuses one).
    assert players == ["pages/LiveWall.tsx", "pages/drones/DronePatrol.tsx", "pages/intel/Situation.tsx"], players
    for path in ("pages/LiveWall.tsx", "pages/intel/Situation.tsx"):
        assert "useMaskedCameras()" in web[path] and "mayPlayHls(" in web[path], path
    wall = web["pages/LiveWall.tsx"]
    assert wall.count("<HlsPlayer") == wall.count("{playHls && hlsUrl ? (") == 2
    hook = web["hooks/useMaskedCameras.ts"]
    assert "mayPlayHls: (id: string) => known && !ids.has(id)" in hook, "until it is known, the answer is the masked view"
    # The phone plays no HLS at all: its live view is the masked one.
    assert not [p for p, text_ in _sources(PHONE, (".ts", ".tsx")).items() if "/hls/" in text_]
    assert "Its live view is the MJPEG one, which is masked; it plays no HLS." in _flat(_doc())


def test_the_phone_uses_the_routes_the_web_uses_and_says_what_the_web_says():
    spec = app.openapi()
    calls = set(client_calls(PHONE / "api" / "privacyZones.ts"))
    assert calls == {("GET", "/api/v1/privacy/zones"), ("POST", "/api/v1/privacy/zones"),
                     ("DELETE", "/api/v1/privacy/zones/{}"), ("GET", "/api/v1/privacy/masked-cameras")}, calls
    assert not [f"{m} {p}" for m, p in calls if not _served(m, p, spec)]
    assert "with no route added for it" in _flat(_doc()) and len(_privacy_routes()) == 5
    client = (PHONE / "api" / "privacyZones.ts").read_text(encoding="utf-8")
    # The list is asked for by camera, which the route takes; and a corner is sent as an x and a y and nothing else.
    taken = {p["name"] for p in spec["paths"]["/api/v1/privacy/zones"]["get"]["parameters"]}
    assert "params: { camera_id: cameraId }" in client and "camera_id" in taken
    assert "polygon: zone.polygon.map((p) => ({ x: p.x, y: p.y }))" in client
    body = spec["components"]["schemas"]["PrivacyZoneCreate"]
    assert {"camera_id", "name", "polygon"} <= set(body["properties"]) and body["required"] == ["camera_id", "polygon"]

    # What a zone does is said in the same words wherever one is drawn.
    def words(path) -> dict[str, str]:
        text_ = path.read_text(encoding="utf-8")
        return {name: re.sub(r"'\s*\+\s*'", "", re.sub(r"\s*\n\s*", " ", value)).strip()
                for name, value in re.findall(r"export const (WHAT_A_ZONE_DOES|WHAT_DELETING_DOES|MASKED_LABEL) =\s*(.+?)(?:\n\n|\s*\Z)", text_, re.S)}

    on_the_web = words(WEB / "components" / "privacy" / "privacyZoneWords.ts")
    on_the_phone = words(PHONE / "lib" / "privacyZoneWords.ts")
    assert set(on_the_phone) == {"WHAT_A_ZONE_DOES", "WHAT_DELETING_DOES", "MASKED_LABEL"}
    assert on_the_phone == {name: on_the_web[name] for name in on_the_phone}

    # Drawing and deleting are offered to somebody known to manage privacy - not while that is still loading -
    # and each asks before it is done.
    phone_words = (PHONE / "lib" / "privacyZoneWords.ts").read_text(encoding="utf-8")
    assert "return !!permissions && permissions.includes(PRIVACY_PERMISSION)" in phone_words
    assert "PRIVACY_PERMISSION = 'privacy:manage'" in phone_words
    draw = (PHONE / "screens" / "ZoneDrawScreen.tsx").read_text(encoding="utf-8")
    assert "const mayMask = managesPrivacy(useAuthStore((s) => s.permissions))" in draw
    assert "mayMask ? ['restricted', 'crowd', 'privacy'] : ['restricted', 'crowd']" in draw
    assert draw.index("Alert.alert('Mask this part of the picture?', WHAT_A_ZONE_DOES, [") < draw.index(
        "{ text: 'Mask it', style: 'destructive', onPress: () => save() }")
    assert "onPress={onSave}" in draw and "onPress={() => save()}" not in draw, "the button asks; only the answer saves"
    live = (PHONE / "screens" / "CameraLiveScreen.tsx").read_text(encoding="utf-8")
    assert "const canOpenPrivacy = mayMask && navigation.getState?.()?.routeNames?.includes('CameraPrivacyZones')" in live
    zones = (PHONE / "screens" / "CameraPrivacyZonesScreen.tsx").read_text(encoding="utf-8")
    assert zones.index("Alert.alert('Delete this privacy zone?'") < zones.index(
        "{ text: 'Delete the zone', style: 'destructive', onPress: () => remove.mutate(zone.id) }")
    assert zones.count("remove.mutate(") == 1, "a zone is deleted from the answer to the question, and nowhere else"
    navigation = (PHONE / "navigation" / "index.tsx").read_text(encoding="utf-8")
    assert '<CamerasStack.Screen name="CameraPrivacyZones" component={CameraPrivacyZonesScreen}' in navigation
    section = _section(5)
    for said in ("a third type, Privacy", "asks once more", "Deleting asks first and says what it changes.",
                 "While a person's permissions are still loading the phone offers neither button"):
        assert said in section, said


def test_the_screen_says_what_the_document_says_a_zone_does_and_does_not_do():
    words = (WEB / "components" / "privacy" / "privacyZoneWords.ts").read_text(encoding="utf-8")
    limits = _section(7)
    for on_screen, in_document in (
        ("fixed to the picture, not to the scene", "A zone is fixed to the picture, not to the scene."),
        ("has no HLS live view", "A camera with a zone has no HLS live view."),
        ("The AI sees nothing inside a zone", "The AI sees nothing inside a zone."),
        ("A recorder at the site that records the camera itself", "A recorder at the site that records the camera itself"),
    ):
        assert on_screen in words and in_document in limits, on_screen
    assert "Within about ten seconds" in words and "cannot be unmasked" in words
    assert "It takes effect within about ten seconds, not at once." in limits
    for limit in ("Drone camera feeds are not masked.", "Nobody is shown the unmasked picture.",
                  "A zone is not edited or switched off from the screen.", "It has run on test streams."):
        assert limit in limits, limit
    # The tab is for whoever manages privacy, and is the third on the page that was there.
    page = (WEB / "pages" / "Zones.tsx").read_text(encoding="utf-8")
    assert "usePermission('privacy:manage')" in page and '{managesPrivacy && <Tab label="Privacy Zones" />}' in page
    assert "**Privacy Zones**, for whoever holds `privacy:manage`" in _flat(_doc())


# ─── F. The files, and the record ────────────────────────────────────────────

def test_the_files_the_document_names_exist_and_the_record_names_what_changed():
    files = _doc().split("## 6. Files", 1)[1].split("\n## ", 1)[0]
    new = re.findall(r"^- `([^`]+)`$", files, re.M)
    assert len(new) == 14 and all((REPO_ROOT / p).exists() for p in new), [p for p in new if not (REPO_ROOT / p).exists()]
    assert len([p for p in new if p.startswith("mobile/")]) == 6
    changed = set(re.findall(r"`((?:backend|frontend)/[A-Za-z_/.]+)`", files.split("**Existing files changed:**", 1)[1]))
    assert changed == CHANGED
    for path in CHANGED:
        text_ = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert re.search(r"privacy_mask|useMaskedCameras|PrivacyZonesPanel|stop_stream", text_), path
    built = GAPS.read_text(encoding="utf-8").split("### Privacy zones, applied", 1)[1]
    recorded = set(re.findall(r"`((?:backend|frontend)/[A-Za-z_/.]+)`", built.split("**Existing files changed", 1)[1].split("\n\n", 1)[0]))
    assert recorded == CHANGED
    flat = _flat(built)
    assert "decided the same day that it be built" in flat and "`PRIVACY_MASKING.md`" in flat
    assert "No migration" in flat and "No permission was added" in flat
    # The phone's part, a day later: the document, the design's addendum and the record name the same four files.
    for text_, heading in ((files, "**Existing phone files changed:**"), (built, "**Existing phone files changed (4):**"),
                           (DESIGN.read_text(encoding="utf-8").split("## Addendum, 10 October 2026: the phone", 1)[1],
                            "Existing phone files changed:")):
        listed = set(re.findall(r"`(mobile/[A-Za-z_/.]+)`", text_.split(heading, 1)[1].split("\n\n", 1)[0]))
        assert listed == PHONE_CHANGED, (heading, listed)
    for path in PHONE_CHANGED:
        assert re.search(r"privacyZone|useMaskedCameras|CameraPrivacyZones", (REPO_ROOT / path).read_text(encoding="utf-8")), path
    assert "chose that it draw, list and delete" in flat and "The phone has 57 screens." in flat
    assert len(list((PHONE / "screens").glob("*Screen.tsx"))) == 57
