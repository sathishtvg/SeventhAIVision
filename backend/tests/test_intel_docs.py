"""The intelligence layer's documents say what the code does.

A document that drifts from the code is worse than none in a system whose claim
is that a person, not the software, takes the decision: these check the claims
that can be checked against the repository.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import importlib
import inspect
import json
import re
import uuid

import pytest
import yaml

from app.main import app
from app.services import intel_actions, intel_correlation, intel_decisions, intel_recommend, intel_risk
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

ARCHITECTURE = REPO_ROOT / "AI_SECURITY_INTELLIGENCE_ARCHITECTURE.md"
CORRELATION = REPO_ROOT / "AI_EVENT_CORRELATION.md"
RISK = REPO_ROOT / "AI_RISK_ENGINE.md"
WORKFLOW = REPO_ROOT / "AI_DECISION_WORKFLOW.md"
DECISION_MODEL = REPO_ROOT / "AI_HUMAN_DECISION_MODEL.md"
PREFIX = "/security-intelligence"


def _served() -> dict[tuple[str, str], set[str]]:
    """(METHOD, path) -> the permissions its dependencies require, read from the
    application's own route table."""
    def permissions(route) -> set[str]:
        found: set[str] = set()

        def walk(dep):
            if "require_permission" in getattr(dep.call, "__qualname__", ""):
                found.update(c.cell_contents for c in (dep.call.__closure__ or ())
                             if isinstance(c.cell_contents, str))
            for sub in dep.dependencies:
                walk(sub)

        for d in route.dependant.dependencies:
            walk(d)
        return found

    out = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        inner = [r] if contexts is None else (contexts() if callable(contexts) else contexts)
        for route in inner:
            if PREFIX in getattr(route, "path", "") and hasattr(route, "dependant"):
                for method in route.methods - {"HEAD"}:
                    out[(method, route.path.removeprefix("/api/v1"))] = permissions(route)
    return out


def _documented(document) -> dict[tuple[str, str], set[str]]:
    """The rows of a document's operation table: | METHOD | `path` | permission |."""
    rows = {}
    for line in document.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`\s*\|([^|]*)\|", line)
        if m:
            rows[(m.group(1), m.group(2).strip().split("?")[0])] = set(
                re.findall(r"`([a-z_0-9]+(?::[a-z_0-9]+)+)`", m.group(3)))
    return rows


def test_the_architecture_document_lists_exactly_the_operations_that_are_served():
    served, documented = _served(), _documented(ARCHITECTURE)
    assert served, "the route walk found nothing to compare"
    assert sorted(set(served) - set(documented)) == [], "served, but not in the architecture document"
    assert sorted(set(documented) - set(served)) == [], "in the architecture document, but not served"


@pytest.mark.parametrize("document", [ARCHITECTURE, CORRELATION, RISK, WORKFLOW], ids=lambda p: p.name)
def test_a_document_states_the_permission_the_code_requires(document):
    served, documented = _served(), _documented(document)
    assert documented, f"{document.name}: found no operation table to check"
    assert sorted(set(documented) - set(served)) == [], f"in {document.name}, but not served"
    wrong = [f"{method} {path}: code {sorted(served[(method, path)])}, document {sorted(written)}"
             for (method, path), written in sorted(documented.items()) if written != served[(method, path)]]
    assert wrong == []


def test_every_operation_needs_a_permission():
    assert [key for key, needed in _served().items() if not needed] == []


def test_the_runner_service_is_extra_and_can_reach_nothing_it_does_not_need():
    """It publishes no port and mounts nothing: it reads the database and
    writes its own tables, and the document says so."""
    services = yaml.safe_load((REPO_ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    runner = services["intelligence-runner"]
    assert runner["command"] == ["python", "-m", "app.intelligence_main"]
    assert "ports" not in runner and "volumes" not in runner
    assert not runner.get("privileged") and runner.get("network_mode") != "host"
    assert set(runner["depends_on"]) == {"postgres", "redis"}
    for name, other in services.items():
        if name != "intelligence-runner":
            assert "intelligence-runner" not in (other.get("depends_on") or {}), \
                f"{name} must not wait for the runner: everything else works without it"


# ─── The correlation rules, as written and as coded ──────────────────────────

def _rule_rows() -> dict[str, list[str]]:
    """Method -> the rows of the document's rules table that are about it. A
    row whose first cell is not a method name continues the method above."""
    rows: dict[str, list[str]] = {}
    method = None
    for line in CORRELATION.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 6 or (cells[1] and set(cells[1]) <= {"-"}):
            continue
        m = re.fullmatch(r"`([A-Z_]+)`", cells[1])
        if m:
            method = m.group(1)
        if method and (m or cells[1] == ""):
            rows.setdefault(method, []).append(line)
    return rows


def _coded_confidences() -> dict[str, set[str]]:
    """Method -> the confidences the code gives it, where they are written as a
    number (the neighbouring-camera rule by distance is a formula, checked by
    its own test)."""
    source = (REPO_ROOT / "backend" / "app" / "services" / "intel_correlation.py").read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    for call in re.findall(r"add\(\"([A-Z_]+)\"(.*?)\)\n", source, flags=re.S):
        for number in re.findall(r",\s*(0\.\d+)(?:,|\s*$)", call[1].rstrip(")")):
            found.setdefault(call[0], set()).add(f"{float(number):.2f}")
    return found


def _added_by_0139(name: str) -> set[str]:
    """What migration 0139 added to one of the layer's lists of allowed values."""
    migration = (REPO_ROOT / "backend" / "alembic" / "versions" / "0139_security_drone_looks.py").read_text("utf-8")
    added = re.search(rf'^{name} = {name}_BEFORE \+ "(.*?)"$', migration, re.M)
    assert added, f"0139 does not extend {name}"
    return set(re.findall(r"'([A-Za-z_]+)'", added.group(1)))


def test_the_correlation_document_names_every_method_the_database_accepts():
    migration = (REPO_ROOT / "backend" / "alembic" / "versions" / "0134_security_situations.py").read_text("utf-8")
    accepted = set(re.findall(r"'([A-Z_]+)'", re.search(r"METHODS = \((.*?)\)\n", migration, re.S).group(1)))
    assert len(accepted) >= 10, "the migration's list of methods was not found"
    # Phase 10 made the list one longer: what came back from a look a person asked for.
    added = _added_by_0139("METHODS")
    assert added == {"DRONE_LOOK"}
    assert set(_rule_rows()) == accepted | added


def test_the_correlation_document_states_the_confidences_the_code_uses():
    rows, coded = _rule_rows(), _coded_confidences()
    assert len(coded) >= 8, f"found too few rules in the code to compare: {sorted(coded)}"
    for method, numbers in coded.items():
        written = " ".join(rows.get(method, []))
        for number in numbers:
            assert number in written, f"{method}: the code uses {number}, the document does not say so"


def test_the_correlation_document_states_the_thresholds_the_code_uses():
    text_ = CORRELATION.read_text(encoding="utf-8")
    c = intel_correlation
    assert f"≥ {c.MIN_LINK}" in text_
    assert f"within {int(c.ADJACENT_METERS)} m" in text_
    assert f"quiet for {int(c.QUIET.total_seconds() // 60)} minutes" in text_
    assert f"{c.MEMBERS} most recent events" in text_
    for window, phrase in ((c.IDENTITY_WINDOW, "30 min"), (c.REPEAT_WINDOW, "5 min"), (c.PATROL_WINDOW, "60 min"),
                           (c.SOS_WINDOW, "10 min")):
        assert int(window.total_seconds() // 60) == int(phrase.split()[0]) and phrase in text_


# ─── The risk rules, as written and as coded ─────────────────────────────────

RISK_SOURCE = REPO_ROOT / "backend" / "app" / "services" / "intel_risk.py"
SIGNED = r"(?<![\d.])([+-]\d+)(?![\d%.])"


def _factor_rows(heading: str) -> dict[str, str]:
    """Factor -> the points column of its rows in the table under a heading of
    the risk document, the typographic minus made a plain one. A row whose
    first cell is empty continues the factor above."""
    text_ = RISK.read_text(encoding="utf-8").replace("\u2212", "-")
    section = text_.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, str] = {}
    factor = None
    for line in section.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 5 or (cells[1] and set(cells[1]) <= {"-"}):
            continue
        m = re.fullmatch(r"`([A-Z_]+)`", cells[1])
        if m:
            factor = m.group(1)
        if factor and (m or cells[1] == ""):
            rows[factor] = rows.get(factor, "") + " " + cells[3]
    return rows


def _coded_points(part: str) -> dict[str, set[int]]:
    """Factor -> the points the code gives it where they are written as a
    number, in the normality half or the risk half of the engine."""
    source = RISK_SOURCE.read_text(encoding="utf-8")
    normality, rest = source.split("# ─── Risk ───", 1)
    halves = {"normality": normality, "risk": rest.split("# ─── Against the database", 1)[0]}
    found: dict[str, set[int]] = {}
    for factor, points in re.findall(r'add\("([A-Z_]+)",\s*(-?\d+),', halves[part]):
        found.setdefault(factor, set()).add(int(points))
    return found


def test_the_risk_document_lists_every_factor_with_the_points_the_code_gives():
    rows, coded = _factor_rows("Risk"), _coded_points("risk")
    assert len(coded) >= 10, f"found too few factors in the code to compare: {sorted(coded)}"
    assert set(rows) == set(intel_risk.FACTORS)
    # The factors whose points come from a table rather than a number in the call.
    for factor, table in (("SEVERITY", intel_risk.SEVERITY_POINTS), ("CRITICALITY", intel_risk.CRITICALITY_POINTS),
                          ("ZONE", intel_risk.ZONE_POINTS), ("ZONE", intel_risk.DRONE_ZONE_POINTS)):
        coded.setdefault(factor, set()).update(v for v in table.values() if v)
    drone = re.search(r'\{"CRITICAL": (\d+), "HIGH": (\d+)\}\.get\(lvl', RISK_SOURCE.read_text(encoding="utf-8"))
    # …and what a second look is worth, either way, which is a named constant.
    coded["DRONE"] = {int(drone.group(1)), int(drone.group(2)), intel_risk.LOOK_POINTS, -intel_risk.LOOK_POINTS}
    for factor in intel_risk.FACTORS:
        assert coded.get(factor), f"{factor}: found no points in the code to compare"
        stated = {int(n) for n in re.findall(SIGNED, rows[factor])}
        assert stated == coded[factor], f"{factor}: the document says {sorted(stated)}, the code {sorted(coded[factor])}"


def test_the_risk_document_states_how_normality_is_scored():
    rows, coded = _factor_rows("Normality"), _coded_points("normality")
    assert set(rows) == set(coded) | {"HABIT"} and len(coded) == 3
    for factor, points in coded.items():
        stated = {int(n) for n in re.findall(SIGNED, rows[factor])}
        assert stated == points, f"{factor}: the document says {sorted(stated)}, the code {sorted(points)}"
    text_ = RISK.read_text(encoding="utf-8")
    assert f"the last {intel_risk.BASELINE_WEEKS} weeks" in text_
    assert f"fewer than {intel_risk.BASELINE_FLOOR} weeks" in text_


def test_the_risk_document_states_the_bands_and_the_confidence_rule():
    text_ = RISK.read_text(encoding="utf-8")
    starts = {"INFO": 0, **intel_risk.THRESHOLDS}
    assert tuple(starts) == intel_risk.LEVELS
    for (name, start), following in zip(starts.items(), [*intel_risk.THRESHOLDS.values(), 101]):
        assert f"| `{name}` | {start}–{following - 1} |" in text_
    rule = re.search(r"max\(([\d.]+), 1\.0 - ([\d.]+) \* len\(unknowns\)\)", RISK_SOURCE.read_text(encoding="utf-8"))
    assert rule, "the risk-confidence rule was not found in the code"
    assert f"1 − {rule.group(2)} for each thing not known, never below {rule.group(1)}" in text_
    assert f"`{intel_risk.ENGINE_VERSION}`" in text_


def test_the_risk_document_gives_every_label_the_code_can_give():
    source = re.sub(r'""".*?"""', "", inspect.getsource(intel_risk.classify), flags=re.S)
    labels = {s.strip() for s in re.findall(r'"([A-Z][a-z][^"]*)"', source)}
    assert len(labels) >= 12, f"found too few labels in the code to compare: {sorted(labels)}"
    text_ = RISK.read_text(encoding="utf-8")
    assert [label for label in sorted(labels) if label not in text_] == []
    assert "— with activity seen nearby" in text_
    # And the code for each, which later stages branch on.
    returned = set(re.findall(r'return "([A-Z_]+)",', source))
    assert returned == set(intel_risk.KINDS), "a kind the code never gives, or gives without listing"
    assert [kind for kind in intel_risk.KINDS if f"`{kind}`" not in text_] == []
    migration = (REPO_ROOT / "backend" / "alembic" / "versions" / "0135_security_assessments.py").read_text("utf-8")
    accepted = set(re.findall(r"'([A-Z_]+)'", re.search(r"KINDS = \((.*?)\)\n", migration, re.S).group(1)))
    assert accepted == set(intel_risk.KINDS), "the database and the engine disagree about the kinds"


# ─── The recommendation rules, as written and as the engine gives them ───────

def _workflow_section(heading: str) -> str:
    return WORKFLOW.read_text(encoding="utf-8").split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def _documented_rules() -> dict[tuple[str, str, bool], list[tuple[str, str]] | None]:
    """(kind, risk level, more than one kind of source) -> the steps the
    document's rules table gives, in order. A row that names a kind stands over
    the "any other" row for the same level."""
    named: dict = {}
    other: dict = {}
    for line in _workflow_section("The rules").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) != 6 or not re.search(r"`[A-Z_]+` \d\.\d\d", cells[4]):
            continue
        steps = re.findall(r"`([A-Z_]+)` (\d\.\d\d)", cells[4])
        levels = intel_risk.LEVELS if cells[2] == "any" else tuple(re.findall(r"`([A-Z]+)`", cells[2]))
        agreed = {"—": (False, True), "one kind": (False,), "two or more": (True,)}[cells[3]]
        kind = re.fullmatch(r"`([A-Z_]+)`", cells[1])
        assert kind or cells[1] == "any other", f"unreadable kind in the rules table: {cells[1]}"
        assert levels and set(levels) <= set(intel_risk.LEVELS), f"unreadable risk in the rules table: {cells[2]}"
        for level in levels:
            for both in agreed:
                if kind:
                    named[(kind.group(1), level, both)] = steps
                else:
                    other[(level, both)] = steps
    assert {k for k, _, _ in named} <= set(intel_risk.KINDS), "the rules table names a kind the engine does not have"
    return {(kind, level, both): named.get((kind, level, both), other.get((level, both)))
            for kind in intel_risk.KINDS for level in intel_risk.LEVELS for both in (False, True)}


def test_the_workflow_document_gives_the_steps_the_engine_gives():
    """The rules table is not a description of the engine: it is checked against
    it, for every kind at every level, at a site where everything is possible
    and with nothing holding a confidence down."""
    everything = intel_recommend.Availability(
        has_site=True, cameras=({"id": "c", "name": "Gate 1", "state": "online", "relation": "reported"},),
        guards_on_shift=3, drones_at_site=1, drones_ready=1, site_contact=True)
    documented = _documented_rules()
    assert len(documented) == len(intel_risk.KINDS) * len(intel_risk.LEVELS) * 2
    wrong = []
    for (kind, level, both), steps in sorted(documented.items()):
        assert steps, f"the document has no row for {kind} at {level}"
        factors = [{"factor": "SEVERITY", "points": 45, "detail": "x."}]
        if both:
            factors.append({"factor": "CORROBORATION", "points": 10, "detail": "y."})
        given = [(r.action, f"{r.confidence:.2f}") for r in intel_recommend.recommend(
            {"kind": kind, "risk_level": level, "risk_score": 60, "risk_factors": factors,
             "detection_confidence": None, "correlation_confidence": None, "risk_confidence": 1.0}, everything)]
        if given != steps:
            wrong.append(f"{kind} {level} {'two or more' if both else 'one kind'}: engine {given}, document {steps}")
    assert wrong == []


def test_the_workflow_document_says_which_steps_look_and_which_act():
    rows = dict(re.findall(r"^\| `([A-Z_]+)` \|.*\| (looks|acts) \|$", _workflow_section("The nine steps"), re.M))
    assert rows == {a: "looks" if a in intel_recommend.LOOKING else "acts" for a in intel_recommend.ACTIONS}
    migration = (REPO_ROOT / "backend" / "alembic" / "versions" / "0136_security_recommendations.py").read_text("utf-8")
    accepted = set(re.findall(r"'([A-Z_]+)'", re.search(r"ACTIONS = \((.*?)\)\n", migration, re.S).group(1)))
    assert accepted == set(intel_recommend.ACTIONS), "the database and the engine disagree about the steps"


def test_the_workflow_document_quotes_every_reason_a_step_cannot_be_taken():
    source = inspect.getsource(intel_recommend.recommend)
    block = source.split("def why_not", 1)[1].split("def facts_for", 1)[0]
    messages = re.findall(r'"([A-Z][^"]+\.)"', re.sub(r'""".*?"""', "", block, flags=re.S))
    assert len(messages) >= 9, f"found too few messages in the code to compare: {messages}"
    said = _workflow_section("What cannot be done is said")
    assert [m for m in messages if m not in said] == []


def test_the_workflow_document_states_priorities_and_adjustments_as_coded():
    text_ = WORKFLOW.read_text(encoding="utf-8")
    for level, priority in intel_recommend.PRIORITY_OF.items():
        assert f"| `{level}` | `{priority}` |" in text_
    assert f"`{intel_recommend.ENGINE_VERSION}`" in text_
    # The adjustments the table does not show, each run rather than read.
    base = {"kind": "ACTIVITY", "risk_level": "HIGH", "risk_score": 60, "detection_confidence": None,
            "correlation_confidence": None, "risk_confidence": 1.0,
            "risk_factors": [{"factor": "SEVERITY", "points": 45, "detail": "x."}]}
    cam = {"id": "c", "name": "Gate 1", "state": "online", "relation": "reported"}

    def conf(action, assessment=base, **avail):
        site = {**dict(has_site=True, cameras=(cam,), guards_on_shift=2, site_contact=True), **avail}
        recs = intel_recommend.recommend(assessment, intel_recommend.Availability(**site))
        return next((f"{r.confidence:.2f}" for r in recs if r.action == action), None)

    assert "`VERIFY`, 0.15 less" in text_ and conf("VERIFY", cameras=()) == "0.70" and conf("VIEW_CAMERA") == "0.85"
    assert "`CONTACT_SITE` is added at 0.70" in text_ and conf("CONTACT_SITE", guards_on_shift=0) == "0.70"
    wrong = {**base, "risk_factors": base["risk_factors"] + [{"factor": "HISTORY", "points": -25, "detail": "z."}]}
    again = {**base, "risk_factors": base["risk_factors"] + [{"factor": "PERSISTENCE", "points": 10, "detail": "z."}]}
    assert "`INVESTIGATE` is added at 0.75" in text_ and conf("INVESTIGATE", wrong) == "0.75"
    assert "`INVESTIGATE` is added at 0.70" in text_ and conf("INVESTIGATE", again) == "0.70"
    stuck = dict(cameras=({**cam, "state": "offline"},), guards_on_shift=0, site_contact=False,
                 incident={"id": "i", "status": "open"})
    assert "`ESCALATE` is added at 0.70" in text_ and conf("ESCALATE", **stuck) == "0.70"
    assert "`MONITOR` at 0.60" in text_ and conf("MONITOR", {**base, "risk_level": "MEDIUM"}, **stuck) == "0.60"


# ─── Decisions and actions, as written and as coded ──────────────────────────

def _cells(document, heading: str, width: int) -> list[list[str]]:
    """The rows of the first table under a heading, as stripped cells."""
    section = document.read_text(encoding="utf-8").split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.split("|")][1:-1]
        if len(cells) == width and not (cells[0] and set(cells[0]) <= {"-"}) and line.startswith("|"):
            rows.append(cells)
    return rows[1:]      # without the header


def test_the_workflow_document_says_what_each_decision_carries_out_as_the_planner_does():
    alerts = [{"id": uuid.uuid4(), "status": "open"}, {"id": uuid.uuid4(), "status": "acknowledged"}]
    incident = {"id": uuid.uuid4(), "status": "open", "is_auto_created": True}
    situation = {"closed_at": None, "incident_confirmed_at": None}
    everything = {"intel:decide", "intel:override", "alert:acknowledge", "incident:create", "incident:dispatch",
                  "incident:assign", "incident:resolve"}
    rows = {r[0].strip("`"): r[1:] for r in _cells(WORKFLOW, "What each decision carries out", 3)}
    assert set(rows) == set(intel_decisions.DECISIONS) and len(rows) == len(intel_decisions.DECISIONS), \
        "every decision the code has, once"
    for decision, written in rows.items():
        for cell, has_incident in zip(written, (None, incident)):
            f = intel_decisions.Facts(assessment={"id": uuid.uuid4(), "risk_level": "LOW", "risk_score": 20},
                                      current=[], alerts=alerts, incident=has_incident)
            c = intel_decisions.check(decision, situation=situation, f=f, mine=everything, roles=None, role_id=2)
            if cell.startswith("refused"):
                assert c.refusal is not None and c.refusal[0] == 409, f"{decision}: the document says refused"
                continue
            assert c.refusal is None, f"{decision}: refused ({c.refusal}), the document says {cell}"
            assert [s.action for s in c.steps] == re.findall(r"`([A-Z_]+)`", cell), f"{decision}: {cell}"
            assert (cell == "—") == (c.steps == [])


def test_the_workflow_document_names_the_function_and_permission_each_step_goes_through():
    rows = {r[0].strip("`"): r[1:] for r in _cells(WORKFLOW, "Through the platform's own functions", 3)}
    assert set(rows) == set(intel_actions.THROUGH) | {"INCIDENT_CONFIRM"}
    # The permission the planner asks for each step, gathered from every decision it can plan.
    alerts = [{"id": uuid.uuid4(), "status": "open"}]
    needs: dict = {}
    # A drone is asked only as the officer chose: both choices, and none.
    choices = (None, {"event_id": str(uuid.uuid4())}, {"mission_id": str(uuid.uuid4())})
    for decision in intel_decisions.DECISIONS:
        for incident in (None, {"id": uuid.uuid4(), "status": "open"}):
            for drone in choices:
                for step in intel_decisions.plan(decision, alerts=alerts, incident=incident, drone=drone):
                    needs[step.action] = step.permission
    assert set(needs) == set(rows), "a step the planner never plans, or plans without the document listing it"
    for step, (through, needed) in rows.items():
        if step == "INCIDENT_CONFIRM":
            assert needs[step] is None and through.startswith("—") and needed == "—"
            continue
        path = through.strip("`")
        assert path == intel_actions.THROUGH[step] and needed.strip("`") == needs[step]
        module, name = path.rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module), name)), f"{path} does not exist"
    results = {r[0].strip("`") for r in _cells(WORKFLOW, "How a step ends", 2)}
    migration = (REPO_ROOT / "backend" / "alembic" / "versions" / "0137_security_decisions.py").read_text("utf-8")
    assert results == set(re.findall(r"'([A-Z_]+)'", re.search(r'RESULTS = "(.*?)"', migration).group(1)))


def _migration_set(name: str) -> set[str]:
    migration = (REPO_ROOT / "backend" / "alembic" / "versions" / "0137_security_decisions.py").read_text("utf-8")
    block = re.search(rf"^{name} = \(?(.*?)\)?\n(?=\S)", migration, re.S | re.M).group(1)
    return set(re.findall(r"'([A-Z_]+)'", block))


def test_the_database_and_the_code_agree_about_decisions_reasons_steps_and_statuses():
    assert _migration_set("DECISIONS") == set(intel_decisions.DECISIONS)
    assert _migration_set("BASES") == set(intel_decisions.BASES)
    assert _migration_set("REASONS") == set(intel_decisions.REASONS)
    assert _migration_set("STATUSES") == set(intel_decisions.STATUSES)
    assert _added_by_0139("ACTIONS") == {"DRONE_HOLD", "DRONE_LAUNCH"}
    assert _migration_set("ACTIONS") | _added_by_0139("ACTIONS") == \
        set(intel_actions.THROUGH) | {"INCIDENT_CONFIRM", "NONE"}
    # What a drone step can point at, and nothing a step could steer by.
    assert _added_by_0139("TARGETS") == {"drone_event", "drone_look", "drone_mission", "drone_flight"}
    assert _migration_set("RISK_LEVELS") == set(intel_risk.LEVELS)


def test_the_decision_model_document_lists_the_reasons_bases_and_statuses_the_code_has():
    reasons = {r[0].strip("`"): r[1] for r in _cells(DECISION_MODEL, "Override", 2) if r[0].strip("`") in
               intel_decisions.REASONS}
    assert set(reasons) == set(intel_decisions.REASONS)
    for code, label in intel_decisions.REASONS.items():
        assert reasons[code].startswith(label), f"{code}: the code says “{label}”"
    bases = {r[0].strip("`") for r in _cells(DECISION_MODEL, "Override", 2)} & set(intel_decisions.BASES)
    assert bases == set(intel_decisions.BASES)
    statuses = [r[0].strip("`") for r in _cells(DECISION_MODEL, "Where a situation stands", 2)]
    assert statuses == list(intel_decisions.STATUSES)
    states = {r[0].strip("`") for r in _cells(DECISION_MODEL, "Incidents: three things that are not the same", 2)}
    assert states == {"NONE", "PRELIMINARY", "CONFIRMED"}


def test_the_decision_model_document_gives_the_default_and_the_three_policies_as_they_behave():
    section = DECISION_MODEL.read_text(encoding="utf-8").split("\n## The decision policy\n", 1)[1].split("\n## ", 1)[0]
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", section, re.S)]
    assert blocks[1] == intel_decisions.DEFAULT_POLICY, "the default as written is the default as coded"
    intel_decisions.validate_policy(blocks[0]["roles"])
    policies = {r[0]: json.loads(r[2].strip("`")) for r in _cells(DECISION_MODEL, "The decision policy", 3)}
    assert set(policies) == {"A", "B", "C"}
    guard = {name: [intel_decisions.authority(roles, 5, level, "INVESTIGATE")[0] for level in intel_risk.LEVELS]
             for name, roles in policies.items()}
    for roles in policies.values():
        intel_decisions.validate_policy(roles)
    assert guard["A"] == [None] * 5, "the command centre controls: a guard decides nothing"
    assert guard["B"] == ["ALONE", "ALONE", "ALONE", None, None], "a guard handles low and medium"
    assert guard["C"] == ["ALONE", "ALONE", "ALONE", "WITH_APPROVAL", "WITH_APPROVAL"], "high risk needs approval"
    for name, role in intel_decisions.POLICY_ROLES.items():
        assert f"{role.lower()} (`{name}`)" in section


def test_the_decision_model_document_lists_every_audit_entry_the_code_writes():
    written = set()
    for path in ("app/routers/security_decisions.py", "app/services/intel_actions.py"):
        source = (REPO_ROOT / "backend" / path).read_text(encoding="utf-8")
        written |= set(re.findall(r'"(intel\.[a-z_.]+)"', source))
        written |= {m + "<step>" for m in re.findall(r'f"(intel\.action\.)\{', source)}
        if re.search(r"f\"intel\.decision\.\{'approve' if", source):
            written |= {"intel.decision.approve", "intel.decision.reject"}
    assert len(written) >= 7, f"found too few audit entries in the code to compare: {sorted(written)}"
    said = DECISION_MODEL.read_text(encoding="utf-8").split("\n## Audit\n", 1)[1].split("\n## ", 1)[0]
    assert [name for name in sorted(written) if f"`{name}`" not in said] == []
