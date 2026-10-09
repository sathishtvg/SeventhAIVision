"""The occurrence book: the document says what the code does, and the web and the phone say what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import app
from app.routers import occurrence_book as api
from app.routers.dob import VALID_ENTRY_TYPES
from app.services import shift_summary as summary
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "DIGITAL_OCCURRENCE_BOOK.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
PHONE = REPO_ROOT / "mobile" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0147_occurrence_book_review.py"
BASE = "/api/v1/occurrence-book"
CHANGED = ["backend/app/main.py", "backend/app/routers/dob.py", "frontend/src/App.tsx",
           "frontend/src/components/layout/Sidebar.tsx", "frontend/src/hooks/usePermission.ts",
           "frontend/src/pages/GuardOps.tsx", "mobile/src/api/dob.ts", "mobile/src/screens/OccurrenceBookScreen.tsx",
           "mobile/src/screens/DashboardScreen.tsx"]


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(start: str, end: str) -> str:
    return _doc().split(start, 1)[1].split(end, 1)[0]


def _routes():
    """Every route this router serves, with the permissions it asks for."""
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not path.startswith(BASE) or not getattr(route, "endpoint", None):
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
                yield method, re.sub(r"\{[a-z_]+:uuid\}", "{id}", path.removeprefix(BASE)), frozenset(needs)


def test_an_entry_is_never_edited_or_removed_and_the_document_says_so():
    code = Path(api.__file__).read_text(encoding="utf-8")
    assert "UPDATE occurrence_book_entries" not in code and "DELETE FROM" not in code
    assert code.count("INSERT INTO occurrence_book_entries") == 1, "an entry is written here only as a correction"
    existing = (REPO_ROOT / "backend" / "app" / "routers" / "dob.py").read_text(encoding="utf-8")
    assert "occurrence_entry" not in existing and "UPDATE occurrence_book_entries" not in existing
    doc = _flat(_doc())
    for said in ("An entry is never edited and never removed.", "`POST /api/v1/dob` is as it was, and takes two more kinds",
                 "The new router writes an entry only as a correction.",
                 "The existing `GET /api/v1/dob` is unchanged and still lists every entry."):
        assert said in doc, said
    # What it did not do is said too, with what was done about it since.
    assert "was still permitted to update and delete rows of `occurrence_book_entries`" in doc
    assert "migration `0157` took the two rights away" in doc
    later = (REPO_ROOT / "backend" / "alembic" / "versions" / "0157_occurrence_book_append_only.py").read_text(encoding="utf-8")
    assert 'op.execute("REVOKE UPDATE, DELETE ON occurrence_book_entries FROM svc_app")' in later
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "occurrence_book_entries TO" not in migration and "ALTER TABLE occurrence_book_entries" not in migration, \
        "this phase changes nothing of the existing table"


def test_the_document_gives_what_the_book_is_searched_by_and_the_two_new_kinds():
    section = _section("## 2. The book, searched", "## 3.")
    named = set(re.findall(r"`([a-z_]+)`", section.split("An entry is read", 1)[0]))
    code = Path(api.__file__).read_text(encoding="utf-8")
    search = code.split("async def search_entries(", 1)[1].split("db: AsyncSession", 1)[0]
    taken = set(re.findall(r"^    ([a-z_]+):", search, re.M)) - {"limit", "offset"}
    assert named == taken == {"q", "entry_type", "site_id", "author_user_id", "shift_id", "date_from", "date_until",
                              "review", "corrected"}
    assert "`delivery` and `unusual_activity`" in section and {"delivery", "unusual_activity"} <= VALID_ENTRY_TYPES
    assert set(api.KIND_ORDER) == VALID_ENTRY_TYPES, "every kind has a place in the order the screen lists them"
    assert "ESCAPE" in code and "looked for, not treated as a wildcard" in _flat(section)


def test_the_document_gives_the_outcomes_of_a_review_and_who_reads_them():
    section = _section("## 3. Review", "## 4.")
    assert re.findall(r"^\| `([A-Z_]+)` \|", section, re.M) == list(api.OUTCOMES)
    assert set(re.findall(r"`([a-z_]+)`", section.split("Where an entry's review stands", 1)[1].split(".", 1)[0])) \
        == set(api.REVIEW_STATES)
    assert api.MAX_BULK == 200 and "at most 200" in section
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "ck_dobreview_note" in migration and "outcome = 'NOTED' OR (note IS NOT NULL" in migration
    doc = _flat(_doc())
    assert "A review is by somebody other than who wrote the entry" in doc
    assert "A client and a viewer read the entries themselves, as before, and nothing of the reviews." in doc
    code = Path(api.__file__).read_text(encoding="utf-8")
    assert "An entry is reviewed by somebody other than who wrote it." in code and "_keeps_the_book" in code


def test_the_document_says_how_a_shifts_summary_is_drafted_and_that_no_model_writes_it():
    section = _section("## 6. A shift's summary", "## 7.")
    headings = re.findall(r"^\| ([A-Z][A-Z ]+) \|", section, re.M)
    written = summary.write({
        "shift": {"start": "2026-10-07T00:00:00+00:00", "end": "2026-10-07T12:00:00+00:00", "ended": True, "timezone": "UTC"},
        "entries": {"total": 0, "by_type": {}, "of_note_total": 0, "of_note": []},
        "incidents": {"opened": 0, "resolved": 0, "open_now": 0, "still_open": []},
        "alerts": {"raised": 0, "open_now": 0, "by_severity": {}},
        "patrols": {"sessions": 0, "completed": 0, "checkpoints_scanned": 0, "checkpoints_total": 0},
        "dispatches": {"sent": 0, "arrived": 0, "declined": 0}, "visitors": {"arrived": 0, "left": 0, "turned_away": 0},
        "site": {"keys_outstanding": 0, "keys_overdue": 0, "lost_found_held": 0, "equipment_out_count": 0,
                 "open_defects_count": 0},
        "instructions": [], "follow_ups": [{"at": "2026-10-07T01:00:00+00:00", "type": "general", "body": "x", "note": "y"}],
    }).split("\n")
    assert headings == [line for line in written if line.isupper()], "the headings of the document are the summary's own"
    assert (summary.MAX_LISTED, summary.QUOTE_CHARS, summary.METHOD) == (10, 200, "TEMPLATE")
    doc = _flat(section)
    assert "At most 10 of any one thing are listed" in doc and "cut at 200 characters and never reworded" in doc
    assert set(summary.NOTABLE_TYPES) == {"incident", "unusual_activity", "sos", "alarm_activation"}
    assert "an incident, unusual activity, an SOS or an alarm, or anything marked high or critical" in doc
    assert "counted by the same code the handover uses" in doc
    source = Path(summary.__file__).read_text(encoding="utf-8")
    assert "from app.services.handover import gather_site_position" in source
    # No language model: none is imported, none is called, and the database allows no other method.
    for name in ("anthropic", "openai", "llm", "httpx", "requests"):
        assert not re.search(rf"^\s*(import|from) .*{name}|\b{name}\(", source, re.M | re.I), name
    assert "INSERT INTO" not in source and "UPDATE " not in source, "it reads; it writes nothing"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "CHECK (method = 'TEMPLATE')" in migration
    assert "No language model writes it (decision E2), and the database allows no other method." in _flat(_doc())
    states = re.findall(r"^\| `(DRAFT|CONFIRMED|DISCARDED)` \|", section, re.M)
    assert states == ["DRAFT", "CONFIRMED", "DISCARDED"] and "'DRAFT','CONFIRMED','DISCARDED'" in migration
    assert "(`uq_shiftsummary_live`)" in doc and "uq_shiftsummary_live" in migration
    assert "shift_summary_settled" in migration and "A trigger refuses every change" in doc
    assert "final_text, state, confirmed_by_user_id, confirmed_at, updated_at" in migration, \
        "what the platform drafted, and the facts, are not among what the application may change"


def test_the_document_says_how_an_instruction_lives_and_is_carried_forward():
    section = _flat(_section("## 5. Instructions in force", "## 6."))
    for table in ("site_instructions", "site_instruction_reads"):
        assert f"`{table}`" in section and f"CREATE TABLE {table}" in MIGRATION.read_text(encoding="utf-8")
    assert f"`{api.INSTRUCTION_EVENT}`" in section and api.INSTRUCTION_EVENT == "site_instruction_issued"
    assert "that is how an instruction is carried from one handover to the next" in section
    source = Path(summary.__file__).read_text(encoding="utf-8")
    assert "FROM site_instructions" in source and "INSTRUCTIONS IN FORCE" in source
    assert "closed_at, closed_by_user_id, close_note" in MIGRATION.read_text(encoding="utf-8")
    assert "It is not removed." in _flat(_doc())


def test_the_document_lists_every_route_what_each_needs_and_every_audited_act():
    served = {(method, path): needs for method, path, needs in _routes()}
    section = _section("## 7. API", "**Permission**")
    table = {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
             for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$",
                                                   section, re.M)}
    assert table == served and len(served) == 16
    assert "There is no `DELETE`. The one `PATCH` corrects a draft." in _flat(section)
    assert [p for m, p in served if m == "PATCH"] == ["/shift-summaries/{id}"]
    assert not [m for m, _ in served if m in ("DELETE", "PUT")]
    code = Path(api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(dob\.[a-z_.]+)"', code)) | {"dob.summary.confirm", "dob.summary.discard"}
    named = set(re.findall(r"`(dob\.[a-z_.]+)`", section))
    assert named == written and len(named) == 7
    assert "dob.summary.{'confirm' if state == 'CONFIRMED' else 'discard'}" in code


def test_the_document_says_who_holds_the_permission_and_the_migration_the_web_and_the_phone_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted = {int(role) for role, _ in re.findall(r"\((\d), '(dob:review)'\)", migration)}
    assert granted == {2, 3, 8}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    cells = re.search(r"^\| `dob:review` \|(.*)\|$", _doc(), re.M).group(1).split("|")
    assert {role for role, cell in zip(roles, cells) if "✓" in cell} == granted

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    assert "'dob:review'" in src.split("const PLATFORM_PERMISSIONS")[0]
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: "'dob:review'" in re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)
           for role in (3, 4, 5, 6, 7)}
    assert web == {3: True, 4: False, 5: False, 6: False, 7: False}
    assert "dob:review" not in src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    # The phone asks the server what the person holds; its cards are for whoever reads handovers.
    cards = (PHONE / "components" / "HandoverCards.tsx").read_text(encoding="utf-8")
    assert "canSee({ permission: 'handover:read' }" in cards


def test_the_screen_is_in_the_menu_and_the_existing_ones_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/occurrence-book)',.*permission: '([a-z:]+)'", sidebar) == [("/occurrence-book", "dob:read")]
    for kept in ("path: '/guard-ops',", "path: '/handovers',"):
        assert kept in sidebar, "the existing screens keep their entries"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="occurrence-book"', 'path="guard-ops"', 'path="handovers"'):
        assert path in routes
    assert "(`/occurrence-book`)" in _doc()
    client = (WEB / "api" / "occurrenceBook.ts").read_text(encoding="utf-8")
    assert "const BASE = '/api/v1/occurrence-book'" in client and "'/api/v1/dob'" not in client, \
        "the new client writes no entry"
    page = (WEB / "pages" / "occurrenceBook" / "BookEntries.tsx").read_text(encoding="utf-8")
    assert "An entry is never changed." in page
    picker = (WEB / "pages" / "GuardOps.tsx").read_text(encoding="utf-8")
    assert "'unusual_activity', 'delivery'," in picker


def test_the_phone_calls_routes_that_exist_and_offers_the_new_kinds():
    calls = (PHONE / "api" / "occurrenceBook.ts").read_text(encoding="utf-8")
    assert "const BASE = '/api/v1/occurrence-book'" in calls
    served = {(method, path): needs for method, path, needs in _routes()}
    for method, path in (("GET", "/instructions"), ("POST", "/instructions/{id}/read"), ("GET", "/shift-summaries"),
                         ("POST", "/shift-summaries"), ("PATCH", "/shift-summaries/{id}"),
                         ("POST", "/shift-summaries/{id}/confirm")):
        assert served[(method, path)] == {"handover:read"}, path
        tail = path.replace("{id}", "${id}")
        assert f"`${{BASE}}{tail}`" in calls, path
    assert "/close" not in calls and "/review" not in calls and "/correct" not in calls, \
        "a phone reads and confirms; it does not review or close"
    kinds = (PHONE / "api" / "dob.ts").read_text(encoding="utf-8")
    offered = set(re.findall(r"'([a-z_]+)'", kinds.split("export const ENTRY_TYPES = [", 1)[1].split("]", 1)[0]))
    assert {"delivery", "unusual_activity"} <= offered <= VALID_ENTRY_TYPES
    dashboard = (PHONE / "screens" / "DashboardScreen.tsx").read_text(encoding="utf-8")
    assert "<InstructionsCard />" in dashboard and "<ShiftSummaryCard />" in dashboard


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 9. Files", "## 10."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src|mobile/src)/[A-Za-z_/.]+)` \|",
                           _doc().split("## 10. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("---", 1)[0])
    assert changed == CHANGED
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 5 | Occurrence book and handover | **Built 2026-10-07**" in built and "DIGITAL_OCCURRENCE_BOOK.md" in built
    phase = built.split("### Phase 5", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 5, by additions only:**", 1)[1]
                      .split("The existing endpoint", 1)[0]) == CHANGED
    assert "permitted to update and delete rows of `occurrence_book_entries`" in _flat(phase)
