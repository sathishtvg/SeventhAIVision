"""The enterprise expansion as it stands: every phase built, with its document, and the eleven chains recorded.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re

from app.main import app  # noqa: F401 — imported first, as every module of the suite does
from tests import test_expansion_chains as chains
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
#: The brief's chains, as the brief wrote them.
BRIEF = ["CCTV → AI → Incident", "Drone → AI → Incident", "Virtual Patrol → AI → Incident", "Incident → Guard Dispatch",
         "Incident → Investigation", "Investigation → Evidence", "Evidence → Case", "Case → Report",
         "Visitor → Access → CCTV → Alert", "Device Health → Maintenance", "Risk → AI Recommendation → Human Decision"]


def _flat(text: str) -> str:
    return " ".join(text.split())


def _built() -> str:
    return GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]


def test_every_phase_is_built_and_the_document_it_names_exists():
    rows = re.findall(r"^\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^|]*) \|$", _built().split("###", 1)[0], re.M)
    assert [int(n) for n, *_ in rows] == list(range(15))
    for number, phase, state, where in rows:
        assert re.search(r"\*\*(Built|Done) 2026-10-\d\d\*\*", state), (number, phase, state)
        for document in re.findall(r"`([A-Z_]+\.md)`", where):
            assert (REPO_ROOT / document).exists(), document
    # Every document the brief asked for is there.
    for document in ("LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md", "SMART_INVESTIGATION_ARCHITECTURE.md",
                     "EVIDENCE_CHAIN_OF_CUSTODY.md", "GIS_SECURITY_ARCHITECTURE.md", "GUARD_RESPONSE_ARCHITECTURE.md",
                     "DIGITAL_OCCURRENCE_BOOK.md", "SECURITY_SOP_ARCHITECTURE.md", "VISITOR_CONTRACTOR_SECURITY.md",
                     "DEVICE_HEALTH_ARCHITECTURE.md", "SECURITY_RISK_ARCHITECTURE.md", "SECURITY_ANALYTICS_ARCHITECTURE.md",
                     "SECURITY_CASE_MANAGEMENT.md", "ENTERPRISE_SECURITY_HARDENING.md"):
        assert (REPO_ROOT / document).exists(), document
    # Each migration of the expansion follows the one before it, with none missing.
    versions = sorted(p.name[:4] for p in (REPO_ROOT / "backend" / "alembic" / "versions").glob("01[45]*.py") if p.name[:4] >= "0143")
    # 0143 to 0156 are the fourteen phases; 0157 and 0158 are the owner's decisions that followed.
    assert versions == [f"{n:04d}" for n in range(143, 159)]
    migrations = sorted(int(m) for m in set(re.findall(r"migrations? `(0\d{3})`", " ".join(state for _, _, state, _ in rows))))
    assert migrations[0] == 143 and migrations[-1] == 156


def test_the_eleven_chains_are_recorded_each_with_the_test_that_follows_it():
    assert list(chains.CHAINS) == [BRIEF[0], BRIEF[1], BRIEF[2], *BRIEF[3:]] and len(BRIEF) == 11
    section = _built().split("### Phase 14", 1)[1]
    table = re.findall(r"^\| ([^|`]+→[^|]+) \| ([^|]+) \| ([^|]+) \|$", section, re.M)
    assert sorted(row[0].strip() for row in table) == sorted(BRIEF)
    source = (REPO_ROOT / "backend" / "tests" / "test_expansion_chains.py").read_text(encoding="utf-8")
    named = None
    by_chain = {}
    # A row either names its test, or says "the same" as the one above it.
    for chain, followed_in, _ in table:
        test = re.search(r"`(test_[a-z_]+)`", followed_in)
        named = test.group(1) if test else named
        assert test or followed_in.strip() == "the same", (chain, followed_in)
        by_chain[chain.strip()] = named
    # The table is in the order of the first test, then the others; the tests are the ones the module maps.
    assert by_chain == chains.CHAINS
    for name in set(by_chain.values()):
        assert f"async def {name}(" in source, name
    flat = _flat(section)
    for said in ("Every step that mattered is in the audit log and was a signed-in person's.",
                 "**Where a chain is joined only when asked.**", "the layer still names nobody",
                 "**Existing files changed in phase 14:** none.", "nothing ran on hardware", "there is no performance figure"):
        assert said in flat, said
    # What the module says it holds, it holds: a person at every step, and the visitor chain stopped where it stops.
    assert "_people_only(w, [" in source and source.count("await _people_only(") == 3
    assert '"Lim Mei Ling" not in str(events)' in source and 'all(e["source_table"] == "alerts" for e in events)' in source
    assert '"Lim Mei Ling" not in str(handed)' in source and "await _hand_over(c, w, True)" in source
    assert source.count('== []') >= 6, "nothing was opened, raised or decided by the platform alone"


def test_what_is_left_for_the_owner_is_in_one_place_and_nothing_existing_was_changed_to_finish():
    built = _flat(_built())
    # Each phase names the existing files it touched; from phase 9 on, the same four or none.
    for phase in range(9, 14):
        part = _built().split(f"### Phase {phase}", 1)[1].split("### Phase", 1)[0]
        listed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                            part.split("**Existing files changed", 1)[1].split("\n\n", 1)[0])
        assert set(listed) <= {"backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
                               "frontend/src/hooks/usePermission.ts"}, (phase, listed)
    hardening = _flat((REPO_ROOT / "ENTERPRISE_SECURITY_HARDENING.md").read_text(encoding="utf-8"))
    assert "## 9. Found, and what the owner decided" in hardening and "left for the owner" in built
    for decision in ("visitor and access events", "occurrence book"):
        assert decision in built.lower(), decision
    # What was left for the owner was decided, and what was changed for each decision is recorded once more here.
    after = _built().split("### After phase 14", 1)[1]
    assert "decided on 2026-10-09" in _flat(after) and len(re.findall(r"^\d+\. ", after, re.M)) == 7
    changed = set(re.findall(r"`((?:backend|frontend|docker|helm|mobile|desktop)/[A-Za-z0-9_/.\-]+)`", after))
    for path in changed:
        assert (REPO_ROOT / path).exists(), path
    assert {"backend/app/routers/exports.py", "backend/app/routers/data_compliance.py", "backend/app/routers/pdpa.py",
            "backend/app/scheduler_main.py", "backend/app/services/intel_runner.py", "backend/app/core/config_keys.py",
            "frontend/src/pages/Tenants.tsx"} <= changed
