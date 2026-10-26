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
    assert phases == {str(n): "Built" for n in range(1, 10)}
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


def test_the_numbers_of_the_roster_and_patrol_features_are_the_codes():
    from app.services import ai_guard_trends, ai_patrol_miss, ai_patrol_timing, ai_roster_fit
    doc = " ".join(_doc().split())
    assert ai_roster_fit.AHEAD.days == 7 and ai_roster_fit.MIN_GAIN == 0.20 and ai_roster_fit.GUARD_ROLES == (3, 4, 5)
    assert "next **7 days**" in doc and "at least **20** points lower" in doc
    assert ai_patrol_miss.HISTORY.days == 120 and ai_patrol_miss.AHEAD.total_seconds() == 24 * 3600
    assert (ai_patrol_miss.MIN_ROWS, ai_patrol_miss.MIN_MISSED, ai_patrol_miss.MIN_DONE) == (30, 5, 5)
    assert "It needs **30** finished patrols, **5** missed or failed and **5** completed" in doc
    assert ai_patrol_miss.FAILED == {"virtual": ("MISSED",), "tour": ("missed", "incomplete")}
    assert ai_patrol_timing.HISTORY.days == 90 and ai_patrol_timing.BAND == 4 and ai_patrol_timing.MIN_PROBLEMS == 10
    assert ai_patrol_timing.BASES == {"HIGH": 0.15, "MEDIUM": 0.25, "LOW": 0.35}
    assert "**15**, **25** or **35** points (High, Medium, Low), with at least **10** problems" in doc
    assert (ai_guard_trends.RECENT.days, ai_guard_trends.BEFORE.days) == (28, 56)
    assert (ai_guard_trends.MIN_EACH, ai_guard_trends.MIN_DROP) == (8, 0.15)
    assert ai_guard_trends.BASES == {"HIGH": 0.164, "MEDIUM": 0.196, "LOW": 0.258}
    assert "z of at least 1.64, 1.96 or 2.58 by sensitivity" in doc
    for module in (ai_patrol_miss,):
        assert set(module.FACTOR_TITLES) == set(module.FACTORS)


def test_the_numbers_of_the_incident_and_payroll_features_are_the_codes():
    from app.services import ai_incident_helper, ai_payroll_check
    doc = " ".join(_doc().split())
    assert ai_incident_helper.HISTORY.days == 365 and ai_incident_helper.MIN_INCIDENTS == 20
    assert ai_incident_helper.OPEN_WINDOW.total_seconds() == 24 * 3600
    assert ai_incident_helper.BASES == {"HIGH": 0.50, "MEDIUM": 0.60, "LOW": 0.75}
    assert "**50%**, **60%** or **75%** of the similar incidents' weight agrees" in doc
    assert "It needs **20** incidents with words" in doc
    assert ai_payroll_check.HISTORY_SLIPS == 12 and ai_payroll_check.MIN_HISTORY == 4
    assert ai_payroll_check.BASES == {"HIGH": 0.25, "MEDIUM": 0.35, "LOW": 0.45}
    assert "**2.5**, **3.5** or **4.5** deviations" in doc and "at least **4** finalised payslips" in doc


def test_the_assistant_is_as_the_document_says():
    from app.routers import ai_assistant
    from app.services import ai_chat, ai_functions
    doc = " ".join(_doc().split())
    assert ai_chat.HOSTED_MODEL == "claude-opus-5-5" and "`AI_HOSTED_MODEL` (default `claude-opus-5-5`)" in doc
    assert ai_chat.HOSTED_EFFORT == "medium" and "`AI_HOSTED_EFFORT` (default `medium`)" in doc
    assert ai_chat.LOCAL_MODEL == "qwen2.5:7b-instruct" and "`AI_LOCAL_MODEL` (default `qwen2.5:7b-instruct`)" in doc
    assert ai_chat.MAX_ROUNDS == 8 and "At most **8** rounds" in doc
    assert "at most **20** messages a minute" in doc
    src = (REPO_ROOT / "backend" / "app" / "routers" / "ai_assistant.py").read_text(encoding="utf-8")
    assert '@limiter.limit("20/minute", key_func=_per_person)' in src
    assert set(ai_chat.LANGUAGES.values()) == {"English", "Malay", "Tamil", "Chinese (Simplified)"}
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert '"anthropic>=1.17,<2"' in pyproject
    lock = (REPO_ROOT / "backend" / "requirements-lock.txt").read_text(encoding="utf-8")
    assert "anthropic==1.17.0" in lock
    assert len(ai_functions.NEVER_OFFERED) == 11 and ai_assistant.LOCAL_HISTORY == 12


def test_the_confirm_card_is_as_the_document_says():
    from app.services import ai_actions, ai_functions
    doc = " ".join(_doc().split())
    assert ai_actions.LIFETIME.total_seconds() == 30 * 60 and "within **30 minutes** expires" in doc
    assert ai_functions.never_offered("POST", "/api/v1/users", {"role_id": 5}) is None
    assert ai_functions.never_offered("PATCH", "/api/v1/users/x", {"role_id": 5}) == "changing someone's role"
    assert "`ai.assistant.action`" in doc


def test_reading_an_ic_is_as_the_document_says():
    from app.routers import ai_assistant
    from app.services import ai_ic
    doc = " ".join(_doc().split())
    assert ai_assistant.IC_DRAFT_LIFE_MINUTES == 60 and "for **60 minutes**" in doc
    assert "**The photos are never kept**" in doc and "`ai_ic_drafts`" in doc
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert '"rapidocr-onnxruntime>=1.4,<2"' in pyproject and "`rapidocr-onnxruntime>=1.4,<2`" in doc
    lock = (REPO_ROOT / "backend" / "requirements-lock.txt").read_text(encoding="utf-8")
    assert "rapidocr-onnxruntime==1.4.4" in lock
    src = (REPO_ROOT / "backend" / "app" / "routers" / "ai_assistant.py").read_text(encoding="utf-8")
    assert '"user:create" not in await ai_functions.held_permissions' in src, "the doc says: may create staff"
    # The photos are never written anywhere: the reader has no file or database write.
    reader = (REPO_ROOT / "backend" / "app" / "services" / "ai_ic.py").read_text(encoding="utf-8")
    assert not any(w in reader for w in ("open(", "write_bytes", "INSERT", "tempfile", "save("))
    assert set(ai_ic.FIELDS) == {"nric_fin", "full_name", "date_of_birth", "sex", "race", "nationality",
                                 "country_of_birth", "address"}
    panel = (WEB / "components" / "ai" / "AssistantPanel.tsx").read_text(encoding="utf-8")
    assert "usePermission('user:create')" in panel and "f.key === 'ic_reading' && f.mine" in panel


def test_every_ai_call_the_web_and_the_phone_make_is_served():
    """A path renamed on one side is a button that does nothing on the other (the client drift audit)."""
    from app.routers.ai_assistant import ChatBody
    from tests.test_drone_clients import _served as served
    from tests.test_drone_clients import client_calls

    spec = app.openapi()
    clients = {WEB / "api" / "aiAssistant.ts": 8, WEB / "api" / "ai.ts": 7, WEB / "api" / "platformAiLicense.ts": 2,
               REPO_ROOT / "mobile" / "src" / "api" / "aiAssistant.ts": 5}
    for client, at_least in clients.items():
        calls = client_calls(client)
        assert len(calls) >= at_least, f"only {len(calls)} calls found in {client}"
        assert all(p.startswith("/api/v1/") for _, p in calls), calls
        assert not [c for c in calls if not served(*c, spec)], client
    # The web reads the reply as a stream with fetch; its path is written out in full.
    web = (WEB / "api" / "aiAssistant.ts").read_text(encoding="utf-8")
    assert "/api/v1/ai/assistant/chat`" in web and served("POST", "/api/v1/ai/assistant/chat", spec)
    # The phone asks for the reply whole, and sends only what the chat takes.
    phone = (REPO_ROOT / "mobile" / "src" / "api" / "aiAssistant.ts").read_text(encoding="utf-8")
    sent = re.search(r"'/api/v1/ai/assistant/chat', \{(.*?)\}, \{ timeout", phone, re.S).group(1)
    names = set(re.findall(r"(\w+)(?=[,:\s])", sent.replace("...(conversationId ? { conversation_id: conversationId } : {})", "conversation_id")))
    assert {"message", "channel", "stream", "conversation_id"} <= names
    assert {"message", "channel", "stream", "conversation_id"} <= set(ChatBody.model_fields)
    assert "stream: false" in phone and "channel: 'PHONE'" in phone


def test_the_phone_is_as_the_document_says():
    from app.services import ai_chat
    doc = " ".join(_doc().split())
    mobile = REPO_ROOT / "mobile" / "src"
    client = (mobile / "api" / "aiAssistant.ts").read_text(encoding="utf-8")
    assert "export const ASSISTANT_TIMEOUT_MS = 120_000" in client and "up to **120 seconds**" in doc
    assert "(`stream: false`)" in doc and "`channel` `PHONE`" in doc
    nav = (mobile / "navigation" / "index.tsx").read_text(encoding="utf-8")
    assert '<MoreStack.Screen name="Assistant"' in nav
    assert "  Assistant: scene(" in (mobile / "motion" / "scenes.ts").read_text(encoding="utf-8")
    menu = (mobile / "screens" / "MoreMenuScreen.tsx").read_text(encoding="utf-8")
    assert "permission: 'ai:assistant:use'" in menu and "i.screen !== 'Assistant' || assistantIsMine(ai.data)" in menu
    prompt = ai_chat.system_prompt(name=None, role="guard", organisation="X", locale="ms", tz="Asia/Singapore",
                                   now=__import__("datetime").datetime(2026, 10, 26), can_change=True)
    assert "Reply in the language the person writes in; when that is unclear, in Malay." in prompt
    assert "It replies in the language the person writes in" in doc
