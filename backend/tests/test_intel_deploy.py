"""AI security intelligence, phase 15: how the runner is deployed.

The runner is one process that reads and records. Wherever it is deployed —
Compose on one machine, the Helm chart on a cluster — it must be the same
command, and it must be given nothing it could act with: no port to be reached
on, no volume to read evidence from. These read the two deployment files and
hold them to that.

`helm` is not installed where this suite runs (the development machine, the
api image). The chart is checked as text here and rendered by a stand-in; Helm
itself lints and renders it on every push in CI's *Helm chart* job.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
import yaml

from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

CHART = REPO_ROOT / "helm" / "seventh-ai-vision"
TEMPLATE = CHART / "templates" / "intelligence-runner-deployment.yaml"
SCHEDULER = CHART / "templates" / "scheduler-deployment.yaml"
COMPOSE = REPO_ROOT / "docker" / "docker-compose.yml"
BACKEND = REPO_ROOT / "backend" / "app"

HELM = shutil.which("helm")


def _values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _manifest() -> str:
    """The template without its comments: what a cluster would be given."""
    return "\n".join(line for line in _template().splitlines() if not line.lstrip().startswith("#"))


def test_the_chart_runs_the_same_command_compose_does():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["intelligence-runner"]
    assert compose["command"] == ["python", "-m", "app.intelligence_main"]
    assert 'command: ["python", "-m", "app.intelligence_main"]' in _manifest()
    assert (BACKEND / "intelligence_main.py").is_file()


def test_the_runner_is_given_nothing_to_act_with_in_either_deployment():
    manifest = _manifest()
    for withheld in ("ports:", "containerPort", "volumeMounts:", "volumes:", "persistentVolumeClaim",
                     "hostNetwork", "privileged", "kind: Service"):
        assert withheld not in manifest, f"the chart gives the runner {withheld}"
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["intelligence-runner"]
    for withheld in ("ports", "volumes", "privileged", "network_mode", "devices", "cap_add"):
        assert withheld not in compose, f"compose gives the runner {withheld}"


def test_the_chart_deploys_one_runner_under_its_own_name_and_can_leave_it_out():
    template, manifest = _template(), _manifest()
    assert template.lstrip().startswith("{{- if .Values.intelligenceRunner.enabled }}")
    assert template.rstrip().endswith("{{- end }}")
    assert manifest.count("kind: Deployment") == 1 and "replicas: 1" in manifest
    assert '{{ include "seventh-ai-vision.fullname" . }}-intelligence-runner' in manifest
    assert manifest.count("app.kubernetes.io/component: intelligence-runner") == 3   # its own, its selector, its pods
    # Every {{ if }}, {{ with }} is closed: the commonest way a template stops rendering.
    opened = len(re.findall(r"\{\{-?\s*(?:if|with|range)\b", template))
    assert opened == len(re.findall(r"\{\{-?\s*end\s*-?\}\}", template))


def test_the_runner_is_configured_like_the_other_backend_processes():
    """The same image, the same working directory, the same config and secret
    as the scheduler — so it connects to the database as the role the rest of
    the backend does, and a setting changed for one is changed for both."""
    manifest, scheduler = _manifest(), SCHEDULER.read_text(encoding="utf-8")
    for shared in ('image: "{{ .Values.images.backend.repository }}:{{ .Values.images.backend.tag }}"',
                   "imagePullPolicy: {{ .Values.images.backend.pullPolicy }}",
                   "workingDir: /app/backend",
                   'name: {{ include "seventh-ai-vision.fullname" . }}-config',
                   'name: {{ include "seventh-ai-vision.fullname" . }}-secret',
                   'serviceAccountName: {{ include "seventh-ai-vision.serviceAccountName" . }}'):
        assert shared in manifest and shared in scheduler, shared


def test_every_value_the_template_reads_is_in_values_and_every_setting_it_passes_is_one_the_code_reads():
    runner = _values()["intelligenceRunner"]
    read = set(re.findall(r"\.Values\.intelligenceRunner\.([A-Za-z]+)", _template()))
    assert read == set(runner) == {"enabled", "tickSeconds", "backfillMinutes", "resources", "nodeSelector",
                                   "tolerations"}
    assert runner["enabled"] is True and runner["tickSeconds"] == 3 and runner["backfillMinutes"] == 60
    assert set(runner["resources"]) == {"requests", "limits"}

    code = "".join(p.read_text(encoding="utf-8") for p in (BACKEND / "intelligence_main.py",
                                                         BACKEND / "services" / "intel_events.py"))
    passed = set(re.findall(r"- name: ([A-Z_]+)", _manifest()))
    assert passed == {"INTEL_RUNNER_TICK_SECONDS", "INTEL_BACKFILL_MINUTES"}
    for name in passed:
        assert f'os.environ.get("{name}"' in code, f"the chart sets {name}, which nothing reads"
    # And the chart's defaults are the code's own, so an unset value changes nothing.
    assert 'os.environ.get("INTEL_RUNNER_TICK_SECONDS", "3")' in code
    assert 'os.environ.get("INTEL_BACKFILL_MINUTES", "60")' in code


# ─── Rendered without Helm ───────────────────────────────────────────────────
#
# Neither the development machine nor the api image this suite runs in has
# Helm. (CI renders the chart with the real thing, in a job of its own, since
# 2026-10-06.) What follows is not Helm: it is a
# stand-in for exactly the constructs these two templates use — if / if not /
# with / end, include, toYaml | nindent, a value, a value | quote — strict about
# anything else. It exists so that a wrong indent or an unclosed block is found
# here and not on a cluster. It is first held to the scheduler's template, which
# has been deployed for a long time, so that passing it means something.

_BLOCK = re.compile(r"^\s*\{\{-? (if not|if|with) \.Values\.([\w.]+) -?\}\}\s*$")
_END = re.compile(r"^\s*\{\{-? end -?\}\}\s*$")
_INCLUDE_LINES = re.compile(r'^\s*\{\{- include "seventh-ai-vision\.(labels|selectorLabels)" \. \| nindent (\d+) \}\}\s*$')
_TO_YAML = re.compile(r"^\s*\{\{- toYaml (\.Values\.[\w.]+|\.) \| nindent (\d+) \}\}\s*$")
_NAMES = {"fullname": "t-seventh-ai-vision", "serviceAccountName": "t-seventh-ai-vision"}


def _value(values: dict, path: str):
    for key in path.split("."):
        values = values[key]
    return values


def _stand_in(template: str, values: dict) -> str:
    out, blocks = [], []          # blocks: (shown, the value a `with` made current)
    for line in template.splitlines():
        block, end = _BLOCK.match(line), _END.match(line)
        if block:
            kind, value = block.group(1), _value(values, block.group(2))
            blocks.append((bool(value) != (kind == "if not"), value))
            continue
        if end:
            blocks.pop()
            continue
        if not all(shown for shown, _ in blocks):
            continue
        labels, dumped = _INCLUDE_LINES.match(line), _TO_YAML.match(line)
        if labels:
            out.append(" " * int(labels.group(2)) + f"app.kubernetes.io/name: {labels.group(1)}")
        elif dumped:
            value = blocks[-1][1] if dumped.group(1) == "." else _value(values, dumped.group(1)[len(".Values."):])
            pad = " " * int(dumped.group(2))
            out.extend(pad + row for row in yaml.safe_dump(value, default_flow_style=False).rstrip().splitlines())
        else:
            line = re.sub(r'\{\{ include "seventh-ai-vision\.(\w+)" \. \}\}', lambda m: _NAMES[m.group(1)], line)
            line = re.sub(r"\{\{ \.Values\.([\w.]+) \| quote \}\}",
                          lambda m: json.dumps(str(_value(values, m.group(1)))), line)
            line = re.sub(r"\{\{ \.Values\.([\w.]+) \}\}", lambda m: str(_value(values, m.group(1))), line)
            assert "{{" not in line, f"the stand-in does not know this construct: {line.strip()}"
            out.append(line)
    assert not blocks, "a block was left open"
    return "\n".join(out) + "\n"


def test_the_stand_in_renders_the_schedulers_long_deployed_template():
    document = yaml.safe_load(_stand_in(SCHEDULER.read_text(encoding="utf-8"), _values()))
    container = document["spec"]["template"]["spec"]["containers"][0]
    assert document["kind"] == "Deployment" and container["command"] == ["python", "-m", "app.scheduler_main"]
    assert container["resources"] == _values()["scheduler"]["resources"]
    assert {m["mountPath"] for m in container["volumeMounts"]} == {"/data/evidence", "/data/recordings"}


def test_the_runners_template_renders_to_one_deployment_with_nothing_it_could_act_with():
    values = _values()
    document = yaml.safe_load(_stand_in(_template(), values))
    assert (document["apiVersion"], document["kind"]) == ("apps/v1", "Deployment")
    assert document["metadata"]["name"] == "t-seventh-ai-vision-intelligence-runner"
    spec = document["spec"]
    assert spec["replicas"] == 1
    # What selects the pods is on the pods: otherwise the deployment owns nothing.
    assert spec["selector"]["matchLabels"] == spec["template"]["metadata"]["labels"]
    assert spec["selector"]["matchLabels"]["app.kubernetes.io/component"] == "intelligence-runner"
    pod = spec["template"]["spec"]
    assert set(pod) == {"serviceAccountName", "containers"}, "no volumes, no host network, nothing but its container"
    (container,) = pod["containers"]
    assert set(container) == {"name", "image", "imagePullPolicy", "command", "workingDir", "envFrom", "env",
                              "resources"}, "no ports and no mounts"
    assert container["command"] == ["python", "-m", "app.intelligence_main"]
    assert container["image"] == f'{values["images"]["backend"]["repository"]}:{values["images"]["backend"]["tag"]}'
    assert container["env"] == [{"name": "INTEL_RUNNER_TICK_SECONDS", "value": "3"},
                                {"name": "INTEL_BACKFILL_MINUTES", "value": "60"}], "strings, as a cluster requires"
    assert container["resources"] == values["intelligenceRunner"]["resources"]
    assert [list(source) for source in container["envFrom"]] == [["configMapRef"], ["secretRef"]]

    # Told where to run, it says so; told not to run, there is nothing at all.
    placed = {**values, "intelligenceRunner": {**values["intelligenceRunner"], "nodeSelector": {"pool": "backend"},
                                               "tolerations": [{"key": "backend", "operator": "Exists"}]}}
    pod = yaml.safe_load(_stand_in(_template(), placed))["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"pool": "backend"} and pod["tolerations"] == [{"key": "backend", "operator": "Exists"}]
    off = {**values, "intelligenceRunner": {**values["intelligenceRunner"], "enabled": False}}
    assert yaml.safe_load(_stand_in(_template(), off)) is None


@pytest.mark.skipif(HELM is None, reason="helm CLI not installed here")
def test_helm_renders_the_runner_and_leaves_it_out_when_told_to():
    on = subprocess.run([HELM, "template", "t", str(CHART)], capture_output=True, text=True)
    assert on.returncode == 0, on.stderr
    assert "t-seventh-ai-vision-intelligence-runner" in on.stdout and "app.intelligence_main" in on.stdout
    off = subprocess.run([HELM, "template", "t", str(CHART), "--set", "intelligenceRunner.enabled=false"],
                         capture_output=True, text=True)
    assert off.returncode == 0, off.stderr
    assert "intelligence-runner" not in off.stdout
