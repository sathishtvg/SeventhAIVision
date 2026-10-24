"""The chart's own database runs the project's image, and is set up the way docker-compose sets it up.

Found 24 October 2026: the chart named plain postgres:16, which has neither pgvector nor pg_partman (the first
migration failed); it gave the app role the superuser's password while the API connected with its own; it put the
data at the root of a new volume (initdb refuses lost+found); and the image would have made the dev test database.
Rendered by the stand-in in test_intel_deploy.py; CI's Helm job renders the chart with Helm itself.
"""
from __future__ import annotations

import shutil
import subprocess

import yaml

from tests._repo import REPO_ROOT, requires_repo_tree
from tests.test_intel_deploy import CHART, _stand_in, _values

pytestmark = requires_repo_tree

NAME = "t-seventh-ai-vision"
DOCKERFILE = REPO_ROOT / "docker" / "postgres.Dockerfile"
SH = shutil.which("sh") or "/bin/sh"  # by its full path: the second run below has a PATH with nothing on it


def _render(template: str, values: dict) -> list[dict]:
    text = (CHART / "templates" / template).read_text(encoding="utf-8")
    return [d for d in yaml.safe_load_all(_stand_in(text, values)) if d]


def test_the_chart_runs_the_image_built_from_the_projects_dockerfile():
    image = _values()["images"]["postgres"]
    assert image["repository"] == "seventh-ai-vision/postgres" and image["repository"] != "postgres"
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM --platform=linux/amd64 pgvector/pgvector:pg16" in dockerfile and "postgresql-16-partman" in dockerfile
    for script in ("10-init-rls.sh", "15-create-test-db.sh", "20-init-partman.sql"):
        assert script in dockerfile, script
    values_text = (CHART / "values.yaml").read_text(encoding="utf-8")
    assert "docker build -f docker/postgres.Dockerfile -t <registry>/seventh-ai-vision/postgres:1.0.0 ." in values_text


def test_the_app_role_is_made_with_the_password_the_api_connects_with():
    values = _values()
    (statefulset,) = _render("postgres-statefulset.yaml", values)
    env = {e["name"]: e for e in statefulset["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["POSTGRES_APP_USER"]["value"] == values["postgres"]["username"]
    assert env["POSTGRES_APP_PASSWORD"]["valueFrom"]["secretKeyRef"] == {"name": f"{NAME}-secret", "key": "POSTGRES_APP_PASSWORD"}
    assert env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "POSTGRES_PASSWORD"
    secret = (CHART / "templates" / "secret.yaml").read_text(encoding="utf-8")
    assert "POSTGRES_APP_PASSWORD: {{ .Values.postgres.password | quote }}" in secret
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert ".Values.postgres.username .Values.postgres.password" in helpers, "DATABASE_URL connects as the app role"
    init_rls = (REPO_ROOT / "docker" / "postgres" / "init-rls.sh").read_text(encoding="utf-8")
    assert "PASSWORD '${POSTGRES_APP_PASSWORD}'" in init_rls


def test_the_data_has_a_folder_of_its_own_and_no_test_database_is_made():
    (statefulset,) = _render("postgres-statefulset.yaml", _values())
    container = statefulset["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    mount = next(m["mountPath"] for m in container["volumeMounts"] if m["name"] == "postgres-data")
    assert env["PGDATA"].startswith(mount + "/") and env["PGDATA"] != mount
    assert env["CREATE_TEST_DB"] == "0"


def test_the_test_database_script_stops_when_told_and_goes_on_otherwise():
    script = REPO_ROOT / "docker" / "postgres" / "create-test-db.sh"
    told = subprocess.run([SH, str(script)], env={"CREATE_TEST_DB": "0", "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True)
    assert told.returncode == 0 and "no test database" in told.stdout
    # Untold, it goes on to psql (absent or failing here, with no server): it did not stop early.
    untold = subprocess.run([SH, str(script)], env={"PATH": "/nonexistent", "POSTGRES_DB": "x", "POSTGRES_USER": "x"},
                            capture_output=True, text=True)
    assert "no test database" not in untold.stdout and untold.returncode != 0
