"""The chart: recordings shared whatever the evidence store is, and a drone runner.

Two things the chart got wrong, found the first time Helm rendered it
(2026-10-06):

  A — With MinIO enabled the chart made no recordings claim and mounted no
      recordings volume. Evidence had moved to the object store; recordings
      are files, and had not. The recorder in the ingestion pod wrote them to
      its own disk, where the API could not play them, the scheduler's sweep
      could not find them, and a restart lost them.

  B — The chart had no deployment for the drone runner, the process that
      flies. On a cluster installed from it the Drone Patrol screens worked
      and nothing ever flew or reported.

Helm is not installed where this suite runs, so the templates are rendered by
the stand-in in test_intel_deploy.py, which knows exactly the constructs they
use. CI's *Helm chart* job renders the same configurations with Helm itself.
"""
from __future__ import annotations

import re

import pytest
import yaml

from tests._repo import REPO_ROOT, requires_repo_tree
from tests.test_intel_deploy import BACKEND, CHART, COMPOSE, _stand_in, _values

pytestmark = requires_repo_tree

TEMPLATES = CHART / "templates"
NAME = "t-seventh-ai-vision"


def _with_minio(on: bool) -> dict:
    values = _values()
    return {**values, "minio": {**values["minio"], "enabled": on}}


def _render(template: str, values: dict) -> list[dict]:
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    return [d for d in yaml.safe_load_all(_stand_in(text, values)) if d]


def _workload(template: str, values: dict) -> tuple[dict, dict]:
    """({path: (volume, read-only?)}, {volume: claim}) of a deployment's one container."""
    (deployment,) = _render(template, values)
    pod = deployment["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    mounts = {m["mountPath"]: (m["name"], bool(m.get("readOnly"))) for m in container.get("volumeMounts") or []}
    volumes = {v["name"]: v["persistentVolumeClaim"]["claimName"] for v in pod.get("volumes") or []}
    return mounts, volumes


# ─── A. Recordings ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("minio", [False, True])
def test_the_recordings_claim_exists_whatever_the_evidence_store_is(minio):
    claims = {d["metadata"]["name"] for d in _render("pvc.yaml", _with_minio(minio))}
    assert f"{NAME}-recordings" in claims, "recordings are files: they need a disk with or without MinIO"
    assert (f"{NAME}-evidence" in claims) is (not minio), "evidence needs a disk only when there is no object store"


@pytest.mark.parametrize("minio", [False, True])
@pytest.mark.parametrize("template,read_only", [("api-deployment.yaml", False), ("ingestion-deployment.yaml", False),
                                                ("scheduler-deployment.yaml", True)])
def test_the_recorder_the_player_and_the_verifier_share_one_recordings_disk(template, read_only, minio):
    """The recorder writes, the API plays, the scheduler verifies and purges:
    three pods, one set of files. With MinIO enabled none of them had it."""
    mounts, volumes = _workload(template, _with_minio(minio))
    assert "/data/recordings" in mounts, f"{template}: recordings would be on this pod's own disk"
    volume, ro = mounts["/data/recordings"]
    assert volumes[volume] == f"{NAME}-recordings", "mounted, but not from the shared claim"
    assert ro is read_only, "the scheduler only reads recordings; the recorder and the API's player need more"
    assert ("/data/evidence" in mounts) is (not minio)
    assert set(volumes) == {m for m, _ in mounts.values()}, "every volume defined is mounted, and the other way round"


def test_without_minio_the_three_pods_are_given_what_they_were_given_before():
    # Since 24 October 2026 the API and the scheduler also share the attendance photos claim (check-in selfies and
    # registered faces); the recorder's pod is given exactly what it was.
    # The API alone is also given the staff files, the guardhouse photos and the face model.
    photos = {"/data/attendance_photos": "attendance-photos"}
    api_only = {"/data/employee_docs": "employee-docs", "/data/guardhouse": "guardhouse",
                "/home/appuser/.insightface": "face-models"}
    for template, extra in (("api-deployment.yaml", {**photos, **api_only}), ("ingestion-deployment.yaml", {}),
                            ("scheduler-deployment.yaml", photos)):
        mounts, volumes = _workload(template, _with_minio(False))
        assert set(mounts) == {"/data/evidence", "/data/recordings"} | set(extra), template
        expected = {"evidence": f"{NAME}-evidence", "recordings": f"{NAME}-recordings"}
        expected.update({volume: f"{NAME}-{volume}" for volume in extra.values()})
        assert volumes == expected, template


# ─── B. The drone runner ─────────────────────────────────────────────────────

def _drone_runner(values: dict) -> dict | None:
    rendered = _render("drone-runner-deployment.yaml", values)
    return rendered[0] if rendered else None


def test_the_chart_runs_the_drone_runner_with_the_command_and_settings_compose_gives_it():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["drone-runner"]
    deployment = _drone_runner(_values())
    assert deployment["kind"] == "Deployment" and deployment["metadata"]["name"] == f"{NAME}-drone-runner"
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    assert spec["selector"]["matchLabels"] == spec["template"]["metadata"]["labels"]
    (container,) = spec["template"]["spec"]["containers"]
    assert container["command"] == compose["command"] == ["python", "-m", "app.drone_runner_main"]
    assert (BACKEND / "drone_runner_main.py").is_file()

    passed = {e["name"]: e["value"] for e in container["env"]}
    in_compose = {name for name in compose["environment"] if name.startswith("DRONE_")}
    assert set(passed) == in_compose, "the chart and compose give the runner the same settings"
    # Each is one the code reads, and the chart's default is the code's own.
    code = "".join(p.read_text(encoding="utf-8") for p in (BACKEND / "drone_runner_main.py",
                                                         BACKEND / "services" / "drone_retention.py"))
    for name, value in passed.items():
        default = re.search(rf'os\.environ\.get\("{name}", "([^"]+)"\)', code)
        assert default, f"the chart sets {name}, which nothing reads"
        assert value == default.group(1), f"{name}: the chart says {value}, the code {default.group(1)}"
    assert all(isinstance(v, str) for v in passed.values()), "strings, as a cluster requires"


def test_the_drone_runner_is_given_the_evidence_disk_and_nothing_to_be_reached_on():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["drone-runner"]
    assert compose["volumes"] == ["evidence_data:/data/evidence"] and "ports" not in compose
    mounts, volumes = _workload("drone-runner-deployment.yaml", _with_minio(False))
    assert mounts == {"/data/evidence": ("evidence", False)}, "drone footage is evidence: written and purged here"
    assert volumes == {"evidence": f"{NAME}-evidence"}
    # With an object store there is no evidence disk to mount.
    assert _workload("drone-runner-deployment.yaml", _with_minio(True)) == ({}, {})
    manifest = "\n".join(line for line in (TEMPLATES / "drone-runner-deployment.yaml").read_text("utf-8").splitlines()
                         if not line.lstrip().startswith("#"))
    for withheld in ("ports:", "containerPort", "hostNetwork", "privileged", "kind: Service"):
        assert withheld not in manifest


def test_every_value_the_drone_runners_template_reads_is_in_values_and_it_can_be_left_out():
    values = _values()
    template = (TEMPLATES / "drone-runner-deployment.yaml").read_text(encoding="utf-8")
    read = set(re.findall(r"\.Values\.droneRunner\.([A-Za-z]+)", template))
    assert read == set(values["droneRunner"])
    assert values["droneRunner"]["enabled"] is True
    off = {**values, "droneRunner": {**values["droneRunner"], "enabled": False}}
    assert _drone_runner(off) is None
    opened = len(re.findall(r"\{\{-?\s*(?:if|with|range)\b", template))
    assert opened == len(re.findall(r"\{\{-?\s*end\s*-?\}\}", template))


# ─── C. The API's own files and the face model (24 October 2026) ─────────────

@pytest.mark.parametrize("minio", [False, True])
def test_every_folder_the_api_writes_has_a_claim_whatever_the_evidence_store(minio):
    """compose gives the API a volume for each folder it writes (employee_docs, attendance_photos, guardhouse) and
    for the face model; the chart gave it none of them, so they lived in the pod."""
    values = _with_minio(minio)
    claims = {d["metadata"]["name"] for d in _render("pvc.yaml", values)}
    mounts, volumes = _workload("api-deployment.yaml", values)
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["api"]["volumes"]
    for entry in compose:
        source, path = entry.split(":")[:2]
        if source in ("evidence_data", "backups_data"):
            # Evidence: test A above (a claim only without MinIO). The database backups: the chart has no volume for
            # them in any pod - on a cluster they are the database service's matter, flagged to the owner 24 Oct 2026.
            continue
        assert path in mounts, f"{path} ({source}) has no volume in the chart's API"
        assert volumes[mounts[path][0]] in claims and not mounts[path][1], path


def test_the_face_model_is_fetched_once_before_the_api_starts_and_can_be_left_to_the_operator():
    values = _values()
    pod = _render("api-deployment.yaml", values)[0]["spec"]["template"]["spec"]
    inits = {c["name"]: c for c in pod["initContainers"]}
    prepare = inits["prepare-face-models"]
    script = prepare["command"][2]
    assert prepare["volumeMounts"] == [{"name": "face-models", "mountPath": "/home/appuser/.insightface"}]
    assert "flock 9" in script and "ensure_available('models', 'buffalo_l')" in script
    assert script.index("ensure_available") < script.index("touch models/buffalo_l/.complete"), "marked only once done"
    assert "if [ ! -f models/buffalo_l/.complete ]" in script and "rm -rf models/buffalo_l " in script
    off = {**values, "api": {**values["api"], "prepareFaceModels": False}}
    pod = _render("api-deployment.yaml", off)[0]["spec"]["template"]["spec"]
    assert [c["name"] for c in pod["initContainers"]] == ["alembic-migrate"]
    assert "/home/appuser/.insightface" in {m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
