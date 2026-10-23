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
            written = path.removeprefix("/api/v1").replace("{video_id:uuid}", "{id}").replace(":uuid}", "}")
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
    assert phases == {str(n): "Built" for n in range(1, 7)}


def test_the_two_stages_are_as_the_document_says():
    doc = " ".join(_doc().split())
    worker = REPO_ROOT / "ai-worker" / "worker" / "footage"
    fight = (worker / "fight.py").read_text(encoding="utf-8")
    pose = (worker / "pose.py").read_text(encoding="utf-8")
    fighting = re.findall(r'^    "([^"]+)",$', fight.split("FIGHTING = (", 1)[1].split(")", 1)[0], re.M)
    alikes = re.findall(r'^    "([^"]+)",$', fight.split("LOOK_ALIKES = (", 1)[1].split(")", 1)[0], re.M)
    assert (len(fighting), len(alikes)) == (5, 7) and "five fixed descriptions of fighting" in doc and "seven of what" in doc
    for words in fighting[:3] + alikes[:3]:
        assert f'"{words}"' in doc, words
    assert re.search(r"^SHARE = 0.5$", fight, re.M) and "share is at least **0.5**" in doc
    assert re.search(r"^LEAST_PEOPLE = 2$", fight, re.M) and "at least **two people**" in doc
    assert re.search(r"^FPS = 5$", fight, re.M) and "five frames a second" in doc
    assert re.search(r"^MOMENTS = 3$", fight, re.M) and "at least **3** such moments" in doc
    assert re.search(r"^REACH = 1.2$", pose, re.M) and "**1.2** times their mean height" in doc
    assert re.search(r"^FAST = 1.5$", pose, re.M) and "faster than **1.5** body heights a second" in doc
    assert '"assault"' not in fight.replace('never "assault"', "") and "never \"assault\"" in doc
    assert "**How well it works is not yet measured.**" in doc
    dockerfile = (REPO_ROOT / "docker" / "footage-worker.Dockerfile").read_text(encoding="utf-8")
    assert "yolov8s-pose.pt" in dockerfile and "234314cd8baf62616791aceb9ea6ad5c19f26cf6c0d8f3a1bfce1e23b186cfb3" in dockerfile


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


def test_a_saved_recording_is_as_the_document_says():
    doc = " ".join(_doc().split())
    privacy = " ".join((REPO_ROOT / "PRIVACY_MASKING.md").read_text(encoding="utf-8").split())
    assert "A saved recording it analyses is a byte-for-byte copy of the recording" in privacy
    assert "`FOOTAGE_REVIEW.md`" in privacy and "(`PRIVACY_MASKING.md`)" in doc
    migration = (REPO_ROOT / "backend" / "alembic" / "versions" / "0166_footage_from_recordings.py").read_text(encoding="utf-8")
    assert "REFERENCES recordings(id) ON DELETE SET NULL" in migration and "**The 30 days are the copy's.**" in doc
    router = (REPO_ROOT / "backend" / "app" / "routers" / "footage.py").read_text(encoding="utf-8")
    assert 'require_permission("footage:upload")), Depends(require_permission("recording:read"))' in router
    service = (REPO_ROOT / "backend" / "app" / "services" / "footage.py").read_text(encoding="utf-8")
    assert "shutil.copyfile(source, target)" in service and "ffmpeg" not in service, "a plain copy: nothing decoded"


GUIDE = REPO_ROOT / "docs" / "FOOTAGE_REVIEW_DEPLOYMENT.md"
CHART = REPO_ROOT / "helm" / "seventh-ai-vision"


def test_the_deployment_package_is_what_the_guide_lists():
    import json

    import yaml

    guide = GUIDE.read_text(encoding="utf-8")
    flat = " ".join(guide.split())
    listed = re.findall(r"^\| `([^`]+)` \| ", guide.split("## Part A", 1)[0], re.M)
    assert len(listed) == 8 and all((REPO_ROOT / p).exists() for p in listed), listed
    assert "**It is prepared and has never been run:**" in flat and "**This maps; it does not certify.**" in flat
    gcc = yaml.safe_load((CHART / "values-gcc.yaml").read_text(encoding="utf-8"))
    assert gcc["config"]["storageBackend"] == "s3" and gcc["minio"]["enabled"] is False
    assert "ap-southeast-1" in gcc["config"]["s3EndpointUrl"] and gcc["footageWorker"]["networkPolicy"]["enabled"] is True
    assert gcc["ingress"]["tls"]["enabled"] is True and "eks.amazonaws.com/role-arn" in gcc["serviceAccount"]["annotations"]
    assert all("<" in v["repository"] for v in gcc["images"].values()), "the agency's registry, not ours"
    lifecycle = json.loads((REPO_ROOT / "scripts" / "aws" / "footage" / "s3-lifecycle.json").read_text(encoding="utf-8"))
    assert [r["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] for r in lifecycle["Rules"]] == [1]
    assert not any("Expiration" in r for r in lifecycle["Rules"]), "no bucket rule deletes footage: a hold must keep it"
    assert "**There is deliberately no lifecycle rule that deletes footage:**" in flat
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["footageWorker"]["enabled"] is True and values["images"]["footageWorker"]["repository"]
    ingress = (CHART / "templates" / "footage-ingress.yaml").read_text(encoding="utf-8")
    assert 'proxy-body-size: "17m"' in ingress and "path: /api/v1/footage/" in ingress
    deployment = (CHART / "templates" / "footage-worker-deployment.yaml").read_text(encoding="utf-8")
    assert "readOnlyRootFilesystem: true" in deployment and 'drop: ["ALL"]' in deployment and "runAsNonRoot: true" in deployment
    assert "app.kubernetes.io/component: api" in (CHART / "templates" / "footage-worker-networkpolicy.yaml").read_text(encoding="utf-8")
    assert "FOOTAGE_WORKER_URL" in (CHART / "templates" / "configmap.yaml").read_text(encoding="utf-8")


def test_the_chart_renders_the_worker_both_ways_where_helm_is_installed():
    import shutil
    import subprocess

    import yaml

    helm = shutil.which("helm")
    if helm is None:
        import pytest

        pytest.skip("helm is not installed here; CI has it")
    for extra, network_policy, mount in (([], False, True), (["-f", str(CHART / "values-gcc.yaml")], True, False)):
        out = subprocess.run([helm, "template", "ci", str(CHART), *extra], capture_output=True, text=True, check=True).stdout
        docs = [d for d in yaml.safe_load_all(out) if d]
        names = {(d["kind"], d["metadata"]["name"]) for d in docs}
        assert ("Deployment", "ci-seventh-ai-vision-footage-worker") in names
        assert ("Ingress", "ci-seventh-ai-vision-footage") in names
        assert (("NetworkPolicy", "ci-seventh-ai-vision-footage-worker") in names) is network_policy
        worker = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("footage-worker"))
        mounts = [m["mountPath"] for m in worker["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]]
        assert ("/data/evidence" in mounts) is mount
