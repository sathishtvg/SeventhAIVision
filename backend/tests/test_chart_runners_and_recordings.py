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
    for template in ("api-deployment.yaml", "ingestion-deployment.yaml", "scheduler-deployment.yaml"):
        mounts, volumes = _workload(template, _with_minio(False))
        assert set(mounts) == {"/data/evidence", "/data/recordings"}, template
        assert volumes == {"evidence": f"{NAME}-evidence", "recordings": f"{NAME}-recordings"}, template


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
