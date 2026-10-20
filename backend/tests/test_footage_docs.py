"""Footage Review: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from importlib import util

from app.main import app
from app.services import footage, footage_retention, retention_statement
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "FOOTAGE_REVIEW.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0163_footage_videos.py"
PREFIX = "/api/v1/footage"
ROLE_COLUMNS = [2, 8, 3, 4, 6, 5, 7, 1]


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _migration():
    spec = util.spec_from_file_location("footage_migration_0163", MIGRATION)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _granted() -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for role, code in _migration().ROLES:
        out.setdefault(code, set()).add(role)
    return out


def _served() -> dict[tuple[str, str], set[str]]:
    """(METHOD, path as the document writes it) -> the permissions it needs."""
    out = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not path.startswith(PREFIX) or not getattr(route, "endpoint", None):
                continue
            needs: set[str] = set()

            def walk(dep):
                if "require_permission" in getattr(dep.call, "__qualname__", ""):
                    needs.update(c.cell_contents for c in (dep.call.__closure__ or ())
                                 if isinstance(c.cell_contents, str))
                for sub in dep.dependencies:
                    walk(sub)

            for d in route.dependant.dependencies:
                walk(d)
            written = path.removeprefix("/api/v1").replace("{video_id:uuid}", "{id}")
            for method in route.methods - {"HEAD"}:
                out[(method, written)] = needs
    return out


def test_the_permission_table_is_what_the_migration_grants():
    header = re.search(r"^\| Permission \| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    assert [int(n) for n in re.findall(r" (\d) \|", header)] == ROLE_COLUMNS
    granted = _granted()
    assert set(granted) == {"footage:upload", "footage:view", "footage:review", "footage:delete"}
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|[^|]*\|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(ROLE_COLUMNS, cells) if "✓" in cell} == holders, code
    assert 1 not in set().union(*granted.values()), "the platform owner holds none"


def test_the_web_gives_each_role_exactly_what_the_migration_grants():
    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(footage:[a-z]+)'"
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    granted = _granted()
    expected = {role: {code for code, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert web == expected
    everything = set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0]))
    assert everything == set(granted)
    platform = src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    assert "footage:" not in platform
    # Admin and manager are ALL_PERMISSIONS less a list: the manager's list leaves out deleting.
    excluded = {role: re.search(rf"\n  {role}: ALL_PERMISSIONS\.filter\((.*?)\)\),", table, re.S).group(1)
                for role in (2, 8)}
    assert "footage:" not in excluded[2] and "'footage:delete'" in excluded[8]
    assert granted["footage:delete"] == {2}


#: Routes a browser's <img> or <video> asks for: the permission is checked from the token in the address.
MEDIA = {("GET", "/footage/{id}/frames/{second}.jpg"), ("GET", "/footage/{id}/video")}


def test_the_api_table_is_every_route_with_what_it_needs():
    rows = re.findall(r"^\| `([A-Z]+)` \| `(/footage[^`]*)` \| (.+) \|$", _doc(), re.M)
    written = {(m, p): set(re.findall(r"`([a-z:]+)`", needs)) for m, p, needs in rows}
    served = _served()
    assert set(written) == set(served)
    for route, needs in written.items():
        if route in MEDIA:
            assert served[route] == set() and needs == {"footage:view"}, route
            assert "from the token in the address" in dict(((m, p), n) for m, p, n in rows)[route]
        else:
            assert needs == served[route], route
    media = (REPO_ROOT / "backend" / "app" / "routers" / "footage.py").read_text(encoding="utf-8")
    assert '"footage:view" not in await held_by(db, who.role_id, ("footage:view",))' in media


def test_the_numbers_in_the_document_are_the_codes():
    doc = " ".join(_doc().split())
    assert footage.PART_SIZE == 16 * 1024 ** 2 and "part size of **16 MB**" in doc
    assert footage.MAX_BYTES == 4 * 1024 ** 3 and "**4 GB**" in doc
    assert footage.MAX_SECONDS == 6 * 3600 and "**6 hours**" in doc
    assert footage.RETENTION_DAYS == 30 and "kept **30 days** from when its upload began" in doc
    assert footage.ABANDONED_AFTER.days == 1 and "for **a day** is removed" in doc
    assert sorted(footage.EXTENSIONS) == [".avi", ".mkv", ".mov", ".mp4"] and "MP4, MOV, MKV and AVI" in doc
    assert footage.JOBS_STREAM == "footage_jobs" and "`footage_jobs`" in doc
    assert "client_max_body_size 17m" in doc
    nginx = (REPO_ROOT / "docker" / "nginx.conf").read_text(encoding="utf-8")
    block = nginx.split("location ^~ /api/v1/footage/ {", 1)[1].split("}", 1)[0]
    assert "client_max_body_size 17m;" in block and 17 * 1024 ** 2 > footage.PART_SIZE
    by = {p.key: p for p in retention_statement.PERIODS}
    assert by["FOOTAGE_REVIEW"].tables == ("footage_videos",) and "`FOOTAGE_REVIEW`" in doc
    assert footage_retention.REMOVED_BY == "The scheduler, once a day"


def test_the_screen_is_in_the_menu_and_the_route_table():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/footage[^']*)',.*permission: '([a-z:]+)'", sidebar) == [("/footage", "footage:view")]
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    assert 'path="footage"' in routes
    scenes = (WEB / "motion" / "scenes.ts").read_text(encoding="utf-8")
    assert "'/footage': scene(" in scenes


def test_the_state_of_each_phase_is_said():
    phases = dict(re.findall(r"^\| (\d) \| [^|]+\| (Built|Not yet) \|$", _doc(), re.M))
    assert phases == {"1": "Built", "2": "Built", "3": "Built", "4": "Not yet", "5": "Not yet", "6": "Not yet"}


def test_the_events_are_as_the_document_says():
    doc = " ".join(_doc().split())
    worker = REPO_ROOT / "ai-worker" / "worker" / "footage"
    events = (worker / "events.py").read_text(encoding="utf-8")
    assert re.search(r"^GAP = 3$", events, re.M) and "no more than **3 seconds** without any lie between them" in doc
    detect = (worker / "detect.py").read_text(encoding="utf-8")
    assert 'SCORE = float(os.environ.get("FOOTAGE_DETECT_SCORE", "0.35"))' in detect and "score of at least 0.35" in doc
    assert "PEOPLE = {0}" in detect and "VEHICLES = {1, 2, 3, 5, 7}" in detect
    assert "**A count is how many were seen in one frame, never who.**" in doc
    dockerfile = (REPO_ROOT / "docker" / "footage-worker.Dockerfile").read_text(encoding="utf-8")
    assert "ARG DETECTOR_EXPORT_BASE=python:3.11-slim" in dockerfile and "COPY ai-worker/yolov8s.pt" in dockerfile
    assert "--build-arg DETECTOR_EXPORT_BASE=docker-ai-worker-behavior:latest" in doc and "**AGPL-3.0**" in doc


def test_the_worker_and_the_model_are_as_the_document_says():
    import yaml

    doc = " ".join(_doc().split())
    compose = yaml.safe_load((REPO_ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    worker = compose["footage-worker"]
    assert worker["profiles"] == ["footage"] and "ports" not in worker, "the worker publishes nothing"
    assert worker["build"]["dockerfile"] == "docker/footage-worker.Dockerfile"
    assert "--profile footage up -d --no-deps footage-worker" in doc
    dockerfile = (REPO_ROOT / "docker" / "footage-worker.Dockerfile").read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile and "EXPOSE 8090" in dockerfile
    lines = (REPO_ROOT / "ai-worker" / "requirements-footage.txt").read_text(encoding="utf-8").splitlines()
    packages = {line.split("==")[0].split("[")[0].strip().lower() for line in lines if line and not line.startswith("#")}
    assert "onnxruntime" in packages and not {"torch", "torchvision", "ultralytics"} & packages, "ONNX Runtime, no PyTorch"
    fetch = (REPO_ROOT / "ai-worker" / "worker" / "footage" / "fetch_model.py").read_text(encoding="utf-8")
    revision = re.search(r'REVISION = "([0-9a-f]{40})"', fetch).group(1)
    assert f"revision `{revision[:7]}`" in doc and "onnx-community/siglip2-base-patch16-224-ONNX" in doc
    assert "**Apache-2.0**" in doc and "port **8090**" in doc and "**one frame a second**" in doc
    model = (REPO_ROOT / "ai-worker" / "worker" / "footage" / "model.py").read_text(encoding="utf-8")
    assert "LOGIT_SCALE = math.exp(4.724453449249268)" in model and "LOGIT_BIAS = -16.771724700927734" in model
    from app.services import footage_search as finder

    assert (finder.STRONG, finder.POSSIBLE, finder.WEAK) == (0.5, 0.1, 0.01)
    assert "**Strong match** at 0.5 or more, **Possible match** at 0.1, **Weak match** at 0.01" in doc
    assert finder.STRENGTH_WORDS == {"HIGH": "Strong match", "MEDIUM": "Possible match", "LOW": "Weak match"}
