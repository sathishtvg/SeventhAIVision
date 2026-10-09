"""Device health, assets and maintenance: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.main import app
from app.routers import maintenance as orders_api
from app.routers import security_assets as assets_api
from app.services import device_health as health
from app.services import maintenance as work
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "DEVICE_HEALTH_ARCHITECTURE.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0150_security_assets.py"
ASSETS, ORDERS = "/api/v1/security-assets", "/api/v1/maintenance"
CODES = ("asset:read", "asset:manage", "maintenance:read", "maintenance:manage")
CHANGED = ["backend/app/main.py", "backend/app/core/config_keys.py", "backend/app/scheduler_main.py",
           "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx", "frontend/src/hooks/usePermission.ts"]
NOW = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(start: str, end: str) -> str:
    return _doc().split(start, 1)[1].split(end, 1)[0]


def _routes(base: str):
    """Every route under a prefix, with the permissions it asks for."""
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not (path == base or path.startswith(base + "/")) or not getattr(route, "endpoint", None):
                continue
            needs: set[str] = set()

            def walk(dep, found=needs):
                if "require_permission" in getattr(dep.call, "__qualname__", ""):
                    found.update(c.cell_contents for c in (dep.call.__closure__ or ())
                                 if isinstance(c.cell_contents, str))
                for sub in dep.dependencies:
                    walk(sub)

            for d in route.dependant.dependencies:
                walk(d)
            for method in route.methods - {"HEAD"}:
                shown = re.sub(r"\{[a-z_]+:uuid\}", "{id}", path.removeprefix(base)) or "/"
                yield method, shown, frozenset(needs)


def test_nothing_is_claimed_that_nothing_measures_and_the_document_says_so():
    doc = _flat(_doc())
    for said in ("A reading is made of what the platform is told, and of nothing else.",
                 "What is not known is `NOT_KNOWN`, not `OK`.",
                 "A sensor that reads a dangerous value is a working sensor.",
                 "Frame rate, latency, packet loss, the quality of the picture and gaps in a recording are measured by "
                 "nothing here."):
        assert said in doc, said
    assert len(health.NOT_MEASURED) == 5 and "(`not_measured`)" in doc
    for path in (Path(health.__file__), Path(assets_api.__file__), MIGRATION):
        code = path.read_text(encoding="utf-8").split('"""', 2)[2]
        for name in ("fps", "frame_rate", "latency_ms", "packet_loss", "jitter", "bitrate"):
            assert name not in code, f"{path.name} has a {name}"
    router = Path(assets_api.__file__).read_text(encoding="utf-8")
    # Every answer that carries a reading carries the list of what is not measured.
    assert router.count('"not_measured": list(device_health.NOT_MEASURED)') >= 3
    not_done = _flat(_doc().split("## 10. What this does not do", 1)[1])
    assert "It measures nothing new." in not_done and "they still are" in not_done
    page = (WEB / "pages" / "assets" / "AssetsMaintenance.tsx").read_text(encoding="utf-8")
    dialogs = (WEB / "components" / "assets" / "AssetDialogs.tsx").read_text(encoding="utf-8")
    assert "<NotMeasured items={data.not_measured} />" in page and dialogs.count("<NotMeasured items=") >= 2
    assert "No reading is not a good reading" in page and ">No reading<" in page


def test_the_document_gives_the_states_and_how_each_kind_of_device_is_read():
    section = _section("## 2. A reading", "## 3.")
    states, kinds = section.split("How each kind is read", 1)
    assert re.findall(r"^\| `([A-Z_]+)` \|", states, re.M) == list(health.STATES)
    assert re.findall(r"^\| `([A-Z_]+)` \|", kinds, re.M) == list(health.KINDS)
    flat = _flat(section)
    # What the table says of each kind is what the code reads.
    fine = {"is_active": True, "n_streams": 1, "n_online": 1, "n_degraded": 0, "last_frame_at": NOW,
            "last_event_type": None, "last_event_at": None, "disconnects_24h": 0}
    assert health.camera({**fine, "n_online": 0}, NOW)["state"] == "DOWN" and "Every stream is offline" in flat
    assert health.camera({**fine, "n_streams": 0, "n_online": 0}, NOW)["state"] == "NOT_KNOWN" and "It has no stream" in flat
    assert health.UNSTEADY_AT == 5 and "it disconnected 5 times or more in 24 hours" in flat
    assert health.camera({**fine, "disconnects_24h": 5}, NOW)["state"] == "DEGRADED"
    probe = {"is_active": True, "last_probe_status": "ok", "last_probe_at": NOW - timedelta(days=2)}
    assert health.STALE_PROBE_S == 86400 and "not probed for more than a day" in flat
    assert health.recorder(probe, NOW)["state"] == "NOT_KNOWN"
    assert health.SILENT_AFTER == 2 and "No reading for more than twice its expected interval" in flat
    quiet = {"is_active": True, "status": "online", "last_contact_at": NOW - timedelta(days=30)}
    assert health.panel(quiet, NOW)["state"] == "OK" and "a quiet panel is not read as down" in flat
    assert "no threshold here turns it into a state" in flat
    source = Path(health.__file__).read_text(encoding="utf-8")
    assert "battery_level" in source and not re.search(r"battery_(level|health)\"?\]? *[<>]", source)
    assert "A recorder belongs to the organisation and not to a site" in flat
    assert "if allowed is not None or site_id is not None:\n                continue" in source
    assert "(`since_is_when_first_read`)" in flat and '"since_is_when_first_read"' in source
    assert "(`services/device_health.py`)" in flat


def test_the_document_says_what_is_kept_how_often_and_what_that_cannot_see():
    section = _flat(_section("## 3. What is kept", "## 4."))
    assert "Every 5 minutes the scheduler reads every device of every organisation" in section
    assert health.LOOK_EVERY_S == 300
    scheduler = (REPO_ROOT / "backend" / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    assert 'DEVICE_HEALTH_INTERVAL = int(os.environ.get("DEVICE_HEALTH_INTERVAL_SECONDS", "300"))' in scheduler
    assert "counts = await maintenance.run(AsyncSessionLocal)" in scheduler
    assert "keeps a row in `device_health_changes` for each whose state is not the one last kept" in section
    assert "The log takes no update and no delete." in section
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "device_health_changes TO svc_app" not in migration.replace("GRANT SELECT, INSERT ON {table} TO svc_app", "")
    assert "in the last 7 and 30 days" in section and "for days in (7, 30)" in Path(assets_api.__file__).read_text(encoding="utf-8")
    assert "An outage shorter than the 5 minutes between two looks can pass unrecorded" in section
    assert "An outage shorter than that can pass unrecorded" in health.AVAILABILITY_NOTE
    hour = timedelta(hours=1)
    known = health.down_time([{"state": "DOWN", "observed_at": NOW - 2 * hour}], NOW - 24 * hour, NOW)
    assert known["known_from"] == NOW - 2 * hour and known["down_seconds"] == 7200, "counted only from the first reading kept"
    # Only the pass writes the log; reading a device's health writes nothing.
    assert "device_health.record(" not in Path(assets_api.__file__).read_text(encoding="utf-8")
    assert "device_health.record(db, items, at)" in Path(work.__file__).read_text(encoding="utf-8")


def test_the_document_describes_the_register_as_the_database_holds_it():
    section = _flat(_section("## 4. The asset register", "## 5."))
    migration = MIGRATION.read_text(encoding="utf-8")
    router = Path(assets_api.__file__).read_text(encoding="utf-8")
    assert "`AST-0001`" in section and 'f"AST-{number:04d}"' in router
    assert "(`ck_asset_device_kind`)" in section and "ck_asset_device_kind" in migration
    assert 'CREATE UNIQUE INDEX uq_asset_{column} ON asset_register ({column})' in migration
    links = re.findall(r'\("([a-z_]+_id)", "([a-z_]+)", "([A-Z_]+)"\)', migration.split("DEVICE_LINKS = [", 1)[1].split("]", 1)[0])
    assert {kind: column for column, _, kind in links} == health.LINK, "the register names a device as the health module does"
    assert {kind for _, _, kind in links} == set(health.KINDS)
    for kind in ("a server", "an access controller", "a UPS", "network equipment"):
        assert kind in section, kind
    assert "has no reading, and is shown as having none rather than as working" in section
    assert assets_api.NOT_MONITORED.startswith("The platform does not know this as a device")
    assert "(`POST /register-devices`)" in section
    assert "Its code and its kind do not change." in section
    changes = migration.split("ASSET_CHANGES = (", 1)[1].split(")", 1)[0]
    for held in ("asset_code", "kind", "created_by_user_id", "created_at", "tenant_id"):
        assert not re.search(rf"\b{held}\b", changes), f"the application may not change an asset's {held}"
    assert "it is never removed, and deleting the device leaves the asset" in section
    assert "DELETE FROM asset_register" not in router and "ON DELETE SET NULL," in migration
    upgrade = migration.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["asset_register", "device_health_changes",
                                                          "maintenance_schedules", "maintenance_work_orders"]
    assert set(re.findall(r"ALTER TABLE (\S+)", upgrade)) == {"{table}"}, "no existing table is altered"
    # None of them takes a name inside the intelligence layer's own namespace.
    assert not re.search(r"CREATE TABLE security_", upgrade)
    assert "The table is `asset_register` and not `security_assets`, as the plan had it" in _flat(_doc())
    assert "No existing table is altered." in _doc()
    for table in ("cameras", "nvr_connections", "iot_sensors", "drones", "drone_edge_gateways", "alarm_panels",
                  "site_places", "facility_defects", "sites", "users"):
        assert f"`{table}`" in _doc() and table in migration, table


def test_the_document_gives_the_states_of_a_work_order_and_what_may_follow_what():
    section = _section("## 5. Work orders", "## 6.")
    origins, rest = section.split("| State | Means | Then |", 1)
    assert re.findall(r"^\| `([A-Z_]+)` \|", origins, re.M) == list(work.ORIGINS)
    table = rest.split("Raising, accepting", 1)[0]
    assert re.findall(r"^\| `([A-Z_]+)` \|", table, re.M) == list(work.STATES)
    for state, then in re.findall(r"^\| `([A-Z_]+)` \|[^|]*\|([^|]*)\|$", table, re.M):
        assert set(re.findall(r"`([A-Z_]+)`", then)) == set(work.MOVES[state]) | ({"DONE"} if state == "OPEN" else set()), state
    flat = _flat(section)
    assert "`WO-0001`" in flat and 'f"WO-{number:04d}"' in Path(work.__file__).read_text(encoding="utf-8")
    assert "(`CORRECTIVE`, `PREVENTIVE`, `INSPECTION`)" in flat and work.KINDS == ("CORRECTIVE", "PREVENTIVE", "INSPECTION")
    router = Path(orders_api.__file__).read_text(encoding="utf-8")
    assert "An order completed without having been started is started and completed at the same moment." in flat
    assert "started_at = COALESCE(started_at, now())" in router
    assert "as whoever did the work states it" in flat
    # From health: off until asked for, once for each outage.
    assert "(`maintenance.suggest_from_health`)" in flat and work.SUGGEST_KEY == "maintenance.suggest_from_health"
    assert "`maintenance.suggest_after_hours` or more (4 unless set; 1 to 168)" in flat
    assert work.AFTER_KEY == "maintenance.suggest_after_hours" and work.DEFAULT_AFTER_HOURS == 4
    assert "suggest_after_hours: int = Field(work.DEFAULT_AFTER_HOURS, ge=1, le=168)" in router
    keys = (REPO_ROOT / "backend" / "app" / "core" / "config_keys.py").read_text(encoding="utf-8")
    assert '"maintenance.suggest_from_health": _bool_validator' in keys
    assert '"maintenance.suggest_after_hours": _range_validator(1, 168)' in keys
    service = Path(work.__file__).read_text(encoding="utf-8")
    assert 'if asked["suggest_from_health"]:' in service and "Each outage is put forward once" in flat
    assert "uq_wo_origin" in MIGRATION.read_text(encoding="utf-8")
    assert 'the suggestion says "since at least"' in flat and "has been read as down since at least" in service
    # From a schedule.
    assert "(`lead_days`)" in flat and "m.next_due_on - m.lead_days <=" in service
    assert "When the order is done, the schedule runs again from that day." in flat
    assert "a schedule moved by hand meanwhile is left where the person put it" in flat
    assert "next_due_on = next_due_on + every_days" in service and "'schedule:' || id::text || ':' || next_due_on::text" in service
    assert "A schedule that is switched off, or is for a retired asset, puts nothing forward." in flat
    assert "WHERE m.is_active AND (a.id IS NULL OR a.status <> 'RETIRED')" in service


def test_a_suggestion_is_not_work_until_a_person_accepts_it_in_the_database_and_on_the_screen():
    doc = _flat(_doc())
    migration = MIGRATION.read_text(encoding="utf-8")
    for said in ("The platform suggests; a person raises the work.", "It assigns nobody and tells nobody.",
                 "a suggested order cannot be open, in progress or done without who accepted it",
                 "Suggestions from health are off until an organisation asks for them."):
        assert said in doc, said
    assert "CONSTRAINT ck_wo_accepted" in migration and "OR accepted_at IS NOT NULL" in migration
    assert "CONSTRAINT ck_wo_suggested" in migration
    assert "CREATE TRIGGER maintenance_work_order_over BEFORE UPDATE ON maintenance_work_orders" in migration
    assert "OLD.state IN ('DONE', 'CANCELLED', 'DISMISSED') AND pg_trigger_depth() = 1" in migration
    assert "An order that is over is not changed — a trigger refuses it." in doc
    changes = migration.split("ORDER_CHANGES = (", 1)[1].split(")", 1)[0]
    for held in ("number", "origin", "origin_key", "suggestion_reason", "raised_by_user_id", "raised_at", "kind", "site_id"):
        assert not re.search(rf"\b{held}\b", changes), f"the application may not change an order's {held}"
    # Nothing tells anybody: neither the service nor the router knows how to.
    for path in (Path(work.__file__), Path(orders_api.__file__), Path(health.__file__)):
        code = path.read_text(encoding="utf-8").lower()
        assert "redis" not in code and "response_notify" not in code and "send_expo_push" not in code, path.name
    assert "A suggestion sends no notification" in doc
    # Nothing here knows what is wrong with a device.
    assert "It says nothing about what is wrong with the device, which nothing here knows." in doc
    reading = {"kind": "CAMERA", "kind_label": "Camera", "device_id": "c", "name": "Gate 2", "since": NOW - timedelta(hours=5),
               "since_is_when_first_read": False, "reasons": ["Every stream is offline."]}
    from zoneinfo import ZoneInfo
    assert work.health_reason(reading, NOW, ZoneInfo("UTC")) == (
        "Camera “Gate 2” has been down since 6 Oct 23:00 (5 hours). Every stream is offline.")
    dialogs = (WEB / "components" / "assets" / "OrderDialogs.tsx").read_text(encoding="utf-8")
    for key in ("accept", "dismiss", "start", "complete", "change", "cancel"):
        assert f"o.may.{key}" in dialogs, f"the {key} button is the server's to offer"
    assert "{o.suggestion_reason}" in dialogs and "{o.note}" in dialogs
    page = (WEB / "pages" / "assets" / "AssetsMaintenance.tsx").read_text(encoding="utf-8")
    assert "nobody has asked for that" in page and "{asked.note}" in page
    # A defect is not changed by an order raised for it.
    router = Path(orders_api.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert not re.search(r"(INSERT INTO|UPDATE|DELETE FROM)\s+facility_defects\b", router)
    assert "A facility defect is not changed by an order raised for it." in doc


def test_the_document_lists_every_route_what_each_needs_and_every_audited_act():
    section = _section("## 6. API", "**Permissions**")
    first, second = section.split("Under `/api/v1/maintenance`", 1)

    def table(text: str) -> dict:
        return {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
                for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$",
                                                      text, re.M)}

    for base, text, always, count in ((ASSETS, first, "asset:read", 10), (ORDERS, second, "maintenance:read", 15)):
        served = {(method, path.replace("{kind}", "{kind}")): needs - {always} for method, path, needs in _routes(base)}
        assert all(always in needs for _, _, needs in _routes(base)), f"every route under {base} needs {always}"
        assert table(text) == served and len(served) == count, base
        assert not [m for m, _ in served if m == "DELETE"]
    assert "There is no `DELETE` under either." in section
    written = set()
    for path in (Path(assets_api.__file__), Path(orders_api.__file__)):
        written |= set(re.findall(r'"((?:asset|maintenance)\.[a-z_.]+)"', path.read_text(encoding="utf-8")))
    written -= {work.SUGGEST_KEY, work.AFTER_KEY}
    named = set(re.findall(r"`((?:asset|maintenance)\.[a-z_.]+)`", section))
    assert named == written and len(named) == 15
    assert "set by somebody who is not held to particular sites" in _flat(section)
    assert "if allowed is not None:\n        raise HTTPException(403" in Path(orders_api.__file__).read_text(encoding="utf-8")


def test_the_document_says_who_holds_what_and_the_migration_and_the_web_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '((?:asset|maintenance):[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"asset:read": {2, 3, 4, 6, 8}, "asset:manage": {2, 3, 8}, "maintenance:read": {2, 3, 4, 6, 8},
                       "maintenance:manage": {2, 3, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code
    assert "Super Admin, a guard and the client role hold none of the new permissions." in _flat(_doc())

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'((?:asset|maintenance):[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(CODES)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {role: {code for code, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert not re.search(pattern, src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0])


def test_the_screen_is_in_the_menu_and_the_existing_ones_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/assets-maintenance)',.*permission: '([a-z:]+)'", sidebar) == [
        ("/assets-maintenance", "asset:read")]
    assert sidebar.index("title: 'Sites & Devices'") < sidebar.index("'/assets-maintenance'") < sidebar.index("title: 'People & Vehicles'")
    for kept in ("path: '/defects',", "path: '/equipment',", "path: '/cameras',", "path: '/alarms',", "path: '/iot',"):
        assert kept in sidebar, f"the existing screen at {kept} keeps its entry"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="assets-maintenance"', 'path="defects"', 'path="equipment"'):
        assert path in routes
    doc = _flat(_doc())
    assert "(`/assets-maintenance`), under Sites & Devices, in four parts" in doc
    assert "The phone is not changed in this phase." in doc
    assert "equipment (`/equipment`) and defect (`/defects`) screens are unchanged" in doc
    page = (WEB / "pages" / "assets" / "AssetsMaintenance.tsx").read_text(encoding="utf-8")
    for part in ("Device health", "Asset register", "Work orders", "Schedules"):
        assert f'label="{part}"' in page and f"**{part}**" in _doc(), part
    assert "usePermission('asset:read')" in page and "usePermission('maintenance:read')" in page
    client = (WEB / "api" / "securityAssets.ts").read_text(encoding="utf-8")
    states = set(re.findall(r"'([A-Z_]+)'", client.split("export type HealthState =", 1)[1].split("\n", 1)[0]))
    assert states == set(health.STATES)
    orders = (WEB / "api" / "maintenance.ts").read_text(encoding="utf-8")
    assert set(re.findall(r"'([A-Z_]+)'", orders.split("export type OrderState =", 1)[1].split("\n", 1)[0])) == set(work.STATES)
    assert set(re.findall(r"'([A-Z_]+)'", orders.split("export type Origin =", 1)[1].split("\n", 1)[0])) == set(work.ORIGINS)
    words = (WEB / "components" / "assets" / "assetFormat.ts").read_text(encoding="utf-8")
    assert "NOT_KNOWN: 'Not known'" in words and "NOT_KNOWN: 'info'" in words, "not known has its own word and colour"
    # No existing device, defect or kit endpoint was touched.
    existing = REPO_ROOT / "backend" / "app" / "routers"
    for name in ("cameras.py", "nvr.py", "iot.py", "alarms.py", "defects.py", "equipment.py"):
        source = (existing / name).read_text(encoding="utf-8")
        assert "asset_register" not in source and "maintenance_work_orders" not in source, name
    mobile = REPO_ROOT / "mobile" / "src"
    # The phone does two things with an order given to its holder: one client and one screen, added on 2026-10-09.
    assert [p.name for p in mobile.rglob("*aintenance*")] == ["maintenance.ts"] and not list(mobile.rglob("*ecurityAsset*"))
    assert [p.name for p in mobile.rglob("*WorkOrder*")] == ["MyWorkOrdersScreen.tsx"]


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 8. Files", "## 9."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src)/[A-Za-z_/.]+)` \|", _doc().split("## 9. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("No existing table is altered", 1)[0])
    assert changed == CHANGED
    not_done = _flat(_doc().split("## 10. What this does not do", 1)[1])
    for said in ("It does not probe anything.", "It does not say why a device is down.", "It does not raise, assign or tell.",
                 "It has no parts store, no costs, no vendor contracts and no maintenance SLA.",
                 "The phone does two things with an order.", "the schedules and the register are on the web."):
        assert said in not_done, said
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 8 | Device health, assets, maintenance | **Built 2026-10-08**" in built and "DEVICE_HEALTH_ARCHITECTURE.md" in built
    phase = built.split("### Phase 8", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 8, by additions only:**", 1)[1]
                      .split("Cameras, recorders", 1)[0]) == CHANGED
    assert "are still not measured" in _flat(phase)
