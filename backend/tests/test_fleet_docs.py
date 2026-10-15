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

from tests._repo import REPO_ROOT, requires_repo_tree

# The documents, Compose and the web are not in the api image: these run where the working tree is.
pytestmark = requires_repo_tree

ROOT = REPO_ROOT
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
                 "without the protection against a wobbling fix",
                 "No screen reads those journeys, and no tracker set up here sends through it"):
        assert said in doc, said
    design = _flat(_read(DESIGN))
    assert "**Why an administrator could not delete a vehicle:**" in design
    assert "A geofence never opens a barrier, admits anybody or confirms who is driving" in design


def test_the_map_document_says_where_tiles_come_from_and_that_positions_are_never_on_the_live_connection():
    doc = _flat(_read(DOC))
    for said in ("VITE_MAP_TILE_URL", "usage policy does not allow heavy use", "None is chosen or paid for here.",
                 "tells the screens only which vehicles moved, never where", "GET /api/v1/fleet/map?ids="):
        assert said in doc, said
    service = _read(ROOT / "backend" / "app" / "services" / "fleet_tracking.py")
    assert '"payload": {"vehicle_ids": vehicle_ids},' in service


def test_the_journey_rules_the_document_gives_are_the_ones_the_code_uses():
    from app.services import fleet_journeys as fj
    doc = _flat(_read(DOC))
    assert (fj.MOST_INACCURATE_M, fj.FASTEST_KMH, fj.STOP_MINUTES, fj.STOP_RADIUS_M) == (100.0, 250.0, 3.0, 50.0)
    assert (fj.GAP_MINUTES, fj.MOST_DRAWN, fj.MOST_DAYS) == (15.0, 5000, 7)
    for said in ("worse than 100 m accurate", "faster than 250 km/h", "three minutes or more within 50 m",
                 "more than 15 minutes ends a trip", "at most 5,000 points", "at most seven days",
                 "never at a place worked out between two", "fleet.history.exported"):
        assert said in doc, said
    speeds = _read(ROOT / "frontend" / "src" / "components" / "fleet" / "journeyFormat.ts")
    assert "export const PLAY_SPEEDS = [1, 10, 60] as const" in speeds
    assert "at 1, 10 or 60 times real speed" in doc


def test_the_retention_the_document_describes_is_the_one_the_code_keeps():
    from app.services import fleet_retention as fr
    doc = _flat(_read(DOC))
    assert fr.LEAST_DAYS == 30 and "each at least 30 days" in doc
    assert [k.label for k in fr.KINDS] == ["Fleet vehicle positions", "Archived fleet vehicles"]
    for k in fr.KINDS:
        assert f"**{k.label}**, counted from" in doc, k.label
    for said in ("Setting a period removes nothing at once: the scheduler removes once a day.",
                 "is kept whatever its age", "The alerts and incidents themselves are not touched."):
        assert said in doc, said


def test_the_geofence_rules_the_document_gives_are_the_ones_the_code_uses():
    from app.services import fleet_alerts as fa
    from app.services import fleet_geofences as fg
    doc = _flat(_read(DOC))
    assert (fg.LEAST_RADIUS_M, fg.MOST_RADIUS_M, fg.LEAST_POINTS, fg.MOST_POINTS) == (25.0, 50_000.0, 3, 200)
    assert (fg.LEAST_MARGIN_M, fg.HOLD.total_seconds(), fg.AGAIN_AFTER.total_seconds(), fg.SILENT.total_seconds(),
            fg.OVER_FIXES) == (15.0, 30, 120, 900, 2)
    for said in ("a circle of 25 m to 50 km, a polygon of 3 to 200 corners", "at least 15 m",
                 "when 30 seconds pass with no fix saying otherwise", "not raised again within 2 minutes",
                 "more than 15 minutes across a change", "over the limit at two fixes running",
                 "raises nothing", "A position sent late never changes the side."):
        assert said in doc, said
    for code in (*fg.ALERT_CODE.values(), "gps.offline", "gps.stale"):
        assert f"`{code}`" in doc, code
    assert fa.DEFAULTS[fa.OFFLINE_KEY]["on"] and not fa.DEFAULTS[fa.STALE_KEY]["on"]
    assert "(on until switched off)" in doc and "if switched on" in doc
    assert fa.PUSHED_ANYWAY == ("high", "critical") and "when the alert is below high" in doc
    assert "**A geofence never opens a barrier, admits anybody or confirms who is driving.**" in doc


def test_the_evidence_the_document_describes_is_what_the_code_looks_for_and_never_confirmed():
    from app.services import fleet_evidence as fe
    doc = _flat(_read(DOC))
    assert (fe.DEFAULT_WINDOW_S, fe.LEAST_WINDOW_S, fe.MOST_WINDOW_S) == (120, 30, 900)
    assert "within two minutes either side of it (30 seconds to 15 minutes" in doc
    for code, _label in fe.SECTIONS.values():
        assert f"(needs `{code}`)" in doc, code
    assert list(fe.MATCH_WORDS.values()) == ["Plate matches", "Near in time only"]
    assert "**Plate matches**" in doc and "**Near in time only**" in doc
    assert "Nothing is ever called confirmed" in doc
    assert "confirmed" not in " ".join(fe.MATCH_WORDS.values()).lower()
