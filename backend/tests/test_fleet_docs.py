"""The GPS fleet documents say what the code and the Docker stack do.

docs/FLEET_GPS_PROVIDERS.md is what somebody setting up tracking reads; the design
note is docs/plans/2026-10-14-gps-fleet-tracking-design.md. These hold that the
settings it names are the ones the server reads and Compose passes, that Traccar
is pinned and kept off the core services, that its own screen stays on the
machine, and that no secret is ever answered.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_tree

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "FLEET_GPS_PROVIDERS.md"
DESIGN = ROOT / "docs" / "plans" / "2026-10-14-gps-fleet-tracking-design.md"
COMPOSE = ROOT / "docker" / "docker-compose.yml"
SETTINGS = ("TRACCAR_FORWARD_SECRET", "TRACCAR_URL", "TRACCAR_API_TOKEN", "TRACCAR_API_USER", "TRACCAR_API_PASSWORD")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_settings_the_document_names_are_the_ones_read_and_passed():
    doc, compose = _read(DOC), _read(COMPOSE)
    config = _read(ROOT / "backend" / "app" / "core" / "config.py")
    example = _read(ROOT / ".env.example")
    for name in SETTINGS:
        assert name in doc, name
        assert re.search(rf"^\s+{name}: str = \"\"$", config, re.M), name
        assert f'{name}: "${{{name}:-}}"' in compose, name
        assert re.search(rf"^{name}=", example, re.M), name
    assert "TRACCAR_DB_PASSWORD" in doc and "TRACCAR_DB_PASSWORD" in compose


def test_traccar_is_pinned_opt_in_small_and_its_own_screen_stays_on_the_machine():
    compose = _read(COMPOSE)
    block = compose[compose.index("  traccar:\n"):compose.index("  frontend:\n")]
    assert 'profiles: ["fleet"]' in block, "not one of the core services"
    assert "image: traccar/traccar:6.16.0-alpine" in block
    assert '"127.0.0.1:8082:8082"' in block and '"5055:5055"' in block
    assert '"-Xmx256m"' in block and "mem_limit: 640m" in block
    assert 'FORWARD_HEADER: "X-Fleet-Secret: ${TRACCAR_FORWARD_SECRET:-}"' in block
    assert "FORWARD_TYPE: json" in block and 'FORWARD_RETRY_ENABLE: "true"' in block
    init = _read(ROOT / "docker" / "traccar" / "init-db.sh")
    assert ":'pw'" in init and "-c \"CREATE ROLE" not in init, "the password is quoted by psql, not pasted into SQL"
    doc = _flat(_read(DOC))
    assert "docker compose --profile fleet up -d --no-deps traccar-db traccar" in doc
    assert "http://127.0.0.1:8082" in doc


def test_no_answer_carries_a_secret_and_the_forwarding_secret_is_compared_safely():
    router = _read(ROOT / "backend" / "app" / "routers" / "fleet_tracking.py")
    assert 'hmac.compare_digest(request.headers.get("X-Fleet-Secret", "").encode(), expected.encode())' in router
    assert 'k not in ("secret_encrypted", "forward_secret_hash")' in router
    assert router.count('"forward_secret": forward_secret') == 2, "only when made, and when replaced"
    migration = _read(ROOT / "backend" / "alembic" / "versions" / "0160_fleet_tracking.py")
    # The register of devices and the count of unknown ones: the application's role can neither read nor write them.
    assert ('    for table in ("fleet_device_registry", "fleet_unknown_devices"):\n'
            '        op.execute(f"REVOKE ALL ON {table} FROM svc_app")') in migration
    doc = _flat(_read(DOC))
    for said in ("never sent to a browser", "kept only as a hash and shown once",
                 "the application's own database role cannot read it"):
        assert said in doc, said


def test_what_is_not_done_is_said():
    doc = _flat(_read(DOC))
    for said in ("Only Traccar. No commercial provider's API is connected.",
                 "The Seventh AI phone app does not report its own position as a tracker",
                 "Those are phases 3 to 5 of the design."):
        assert said in doc, said
    design = _flat(_read(DESIGN))
    assert "**Why an administrator could not delete a vehicle:**" in design
    assert "A geofence never opens a barrier, admits anybody or confirms who is driving" in design
