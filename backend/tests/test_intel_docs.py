"""The intelligence layer's documents say what the code does.

A document that drifts from the code is worse than none in a system whose claim
is that a person, not the software, takes the decision: these check the claims
that can be checked against the repository.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import inspect
import re

import pytest
import yaml

from app.main import app
from app.services import intel_correlation, intel_risk
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

ARCHITECTURE = REPO_ROOT / "AI_SECURITY_INTELLIGENCE_ARCHITECTURE.md"
CORRELATION = REPO_ROOT / "AI_EVENT_CORRELATION.md"
RISK = REPO_ROOT / "AI_RISK_ENGINE.md"
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


@pytest.mark.parametrize("document", [ARCHITECTURE, CORRELATION, RISK], ids=lambda p: p.name)
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


def test_the_correlation_document_names_every_method_the_database_accepts():
    migration = (REPO_ROOT / "backend" / "alembic" / "versions" / "0134_security_situations.py").read_text("utf-8")
    accepted = set(re.findall(r"'([A-Z_]+)'", re.search(r"METHODS = \((.*?)\)\n", migration, re.S).group(1)))
    assert len(accepted) >= 10, "the migration's list of methods was not found"
    assert set(_rule_rows()) == accepted


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
    coded["DRONE"] = {int(drone.group(1)), int(drone.group(2))}
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
