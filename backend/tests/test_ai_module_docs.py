"""The AI module: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection suites.
"""
from __future__ import annotations

import re
from importlib import util

from app.main import app
from app.services import ai_module
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "AI_MODULE.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0170_ai_module.py"
PREFIXES = ("/api/v1/ai/", "/api/v1/platform/tenants/{tenant_id}/ai-license")
ROLE_COLUMNS = [2, 8, 3, 4, 6, 5, 7, 1]
CODES = {"ai:configure", "ai:insights:read", "ai:insights:decide", "ai:assistant:use"}


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _granted() -> dict[str, set[int]]:
    spec = util.spec_from_file_location("ai_migration_0170", MIGRATION)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out: dict[str, set[int]] = {}
    for role, code in module.ROLES:
        out.setdefault(code, set()).add(role)
    return out


def _served() -> dict[tuple[str, str], set[str]]:
    """(METHOD, path as the document writes it) -> the permissions it needs."""
    out = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not path.startswith(PREFIXES) or not getattr(route, "endpoint", None):
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
            for method in route.methods - {"HEAD"}:
                out[(method, path.removeprefix("/api/v1"))] = needs
    return out


def test_the_permission_table_is_what_the_migration_grants():
    header = re.search(r"^\| Permission \| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    assert [int(n) for n in re.findall(r" (\d) \|", header)] == ROLE_COLUMNS
    granted = _granted()
    assert set(granted) == CODES
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|[^|]*\|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(ROLE_COLUMNS, cells) if "✓" in cell} == holders, code
    assert not {1, 7} & set().union(*granted.values()), "the platform owner and the client hold none"


def test_the_web_gives_each_role_exactly_what_the_migration_grants():
    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(ai:[a-z:]+)'"
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    granted = _granted()
    assert web == {role: {c for c, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == CODES
    assert "ai:" not in src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    excluded = {role: re.search(rf"\n  {role}: ALL_PERMISSIONS\.filter\((.*?)\)\),", table, re.S).group(1)
                for role in (2, 8)}
    assert all("ai:" not in e for e in excluded.values()), "admin and manager hold all four"


def test_the_api_table_is_every_route_with_what_it_needs():
    rows = re.findall(r"^\| `([A-Z]+)` \| `(/(?:ai|platform)[^`]*)` \| (.+) \|$", _doc(), re.M)
    written = {(m, p): set(re.findall(r"`([a-z:_]+)`", needs)) for m, p, needs in rows}
    assert written == _served()


def test_the_features_table_is_the_catalogue():
    rows = re.findall(r"^\| `([a-z_]+)` \| ([^|]+) \| ([\d, ]+) \|$", _doc(), re.M)
    assert [(k, t.strip(), [int(x) for x in r.split(",")]) for k, t, r in rows] == [
        (f.key, f.title, list(f.allowed_roles)) for f in ai_module.FEATURES.values()]


def test_the_state_of_each_phase_is_said_and_matches_what_is_ready():
    phases = dict(re.findall(r"^\| (\d) \| [^|]+\| (Built|Not yet) \|$", _doc(), re.M))
    assert phases == {**{str(n): "Built" for n in range(1, 4)}, **{str(n): "Not yet" for n in range(4, 10)}}
    for f in ai_module.FEATURES.values():
        assert f.ready == (phases[str(f.phase)] == "Built"), f.key


def test_the_screens_are_in_the_menu_and_the_route_table():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/ai-[^']*)',.*permission: '([a-z:]+)'", sidebar) == [
        ("/ai-insights", "ai:insights:read"), ("/ai-settings", "ai:configure")]
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    assert 'path="ai-settings"' in routes and 'path="ai-insights"' in routes
    scenes = (WEB / "motion" / "scenes.ts").read_text(encoding="utf-8")
    assert "'/ai-settings': scene(" in scenes and "'/ai-insights': scene(" in scenes
    tenants = (WEB / "pages" / "Tenants.tsx").read_text(encoding="utf-8")
    assert '<Tab label="AI Assistant & Learning" />' in tenants and "<AiLicensePanel" in tenants


def test_no_table_is_called_security_and_no_path_mentions_drone():
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.findall(r"CREATE TABLE (\w+)", migration) == ["ai_module_licenses", "ai_feature_settings"]
    learning = (MIGRATION.parent / "0171_ai_learning.py").read_text(encoding="utf-8")
    assert re.findall(r"CREATE TABLE (\w+)", learning) == ["ai_models", "ai_suggestions"]
    assert all("drone" not in p for _, p in _served())


def test_the_numbers_in_the_document_are_the_codes():
    from app.services import ai_late_risk, ai_learning, ai_overtime_fatigue, ai_runner
    doc = " ".join(_doc().split())
    assert ai_learning.BASE_THRESHOLD == {"HIGH": 0.25, "MEDIUM": 0.40, "LOW": 0.60}
    assert "**25%** (High), **40%** (Medium) or **60%** (Low)" in doc
    assert (ai_learning.MIN_DECISIONS, ai_learning.DECISION_WINDOW) == (8, 20)
    assert "at least **8** decided warnings, the last **20**" in doc
    assert (ai_learning.MAX_RAISE, ai_learning.MAX_LOWER) == (0.20, 0.10)
    assert ai_runner.RETRAIN_AFTER.total_seconds() == 20 * 3600 and "more than **20 hours** old" in doc
    assert ai_late_risk.HISTORY.days == 180 and "the last **180 days**" in doc
    assert ai_late_risk.AHEAD.total_seconds() == 36 * 3600 and "next **36 hours**" in doc
    assert (ai_late_risk.MIN_ROWS, ai_late_risk.MIN_LATE, ai_late_risk.MIN_ON_TIME) == (30, 5, 5)
    assert "It needs **30** finished shifts, **5** late or missed and **5** on time" in doc
    assert ai_overtime_fatigue.HISTORY.days == 90 and ai_overtime_fatigue.MIN_SHIFTS == 20
    assert ai_overtime_fatigue.BASES == {"HIGH": 0.80, "MEDIUM": 0.90, "LOW": 1.00}
    assert "**80%**, **90%** or **100%** of the **72-hour** monthly limit" in doc
    assert ai_overtime_fatigue.ROSTER_DEFAULTS == {"roster.min_rest_hours": 11, "roster.max_consecutive_days": 6}
    sched = (REPO_ROOT / "backend" / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    assert 'AI_MODULE_INTERVAL = int(os.environ.get("AI_MODULE_INTERVAL_SECONDS", "3600"))' in sched


def test_the_numbers_of_the_alert_and_device_features_are_the_codes():
    from app.services import ai_device_failure, ai_false_alarm
    doc = " ".join(_doc().split())
    assert ai_false_alarm.HISTORY.days == 90 and ai_false_alarm.OPEN_WINDOW.total_seconds() == 24 * 3600
    assert (ai_false_alarm.MIN_ROWS, ai_false_alarm.MIN_FALSE, ai_false_alarm.MIN_REAL) == (40, 8, 8)
    assert "It needs **40** alerts with a verdict, **8** false and **8** real" in doc
    assert ai_false_alarm.BASES == {"HIGH": 0.60, "MEDIUM": 0.75, "LOW": 0.90}
    assert "**60%**, **75%** or **90%** (High, Medium, Low)" in doc
    assert (ai_false_alarm.REVIEW_MIN, ai_false_alarm.REVIEW_AT) == (10, 0.70)
    assert "at least **10** decided, **70%** or more false" in doc
    assert ai_false_alarm.FALSE == ("false_positive",) and ai_false_alarm.REAL == ("resolved", "acknowledged")
    assert ai_device_failure.HISTORY.days == 120 and ai_device_failure.AHEAD.days == 7
    assert (ai_device_failure.MIN_ROWS, ai_device_failure.MIN_DOWN, ai_device_failure.MIN_UP) == (60, 5, 5)
    assert "It needs **60** device-days, **5** that went down and **5** that did not" in doc
    for module in (ai_false_alarm, ai_device_failure):
        assert set(module.FACTOR_TITLES) == set(module.FACTORS)
