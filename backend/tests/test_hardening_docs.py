"""Retention, subject reports and the sweep: the document says what the code does.

Reads files outside backend/, so the module runs with the repository-inspection
suites. The sweep's own lists are the ones the sweep itself is run against
(tests/test_expansion_hardening.py), so the document is held to the same lists.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import app  # noqa: F401 — the routes below are read from it
from app.routers import data_governance as api
from app.services import retention_statement as statement
from app.services import subject_records as subjects
from tests._repo import REPO_ROOT, requires_repo_tree
from tests.test_expansion_hardening import (
    ADD_ONLY, CLIENT_READS, DELETES, EITHER, MAY_DELETE, MAY_UPDATE_ANY, NOT_A_PERSONS, NOT_AUDITED, READS, ROUTERS,
    TAKEN_OUT, WORDS, expansion_tables, routes,
)

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "ENTERPRISE_SECURITY_HARDENING.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
APP = REPO_ROOT / "backend" / "app"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0156_data_governance.py"
CODES = ("retention:read", "subject:report")
CHANGED = ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
           "frontend/src/hooks/usePermission.ts"]
NUMBERS = {"Six": 6, "eleven": 11, "Twelve": 12, "Four": 4, "thirty-six": 36}


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(start: str, end: str) -> str:
    return _doc().split(start, 1)[1].split(end, 1)[0]


def _code(path) -> str:
    """A module without its opening description."""
    return Path(path).read_text(encoding="utf-8").split('"""', 2)[2]


def _as_written(path: str) -> str:
    """A route's address as the document writes it: without the prefix, an id by what it is the id of."""
    return re.sub(r"\{([a-z]+)_id:uuid\}", r"{\1}", path.removeprefix("/api/v1"))


def _listed(section: str) -> set[tuple[str, str]]:
    return {(m, p) for m, p in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]+)` \|", section, re.M)}


def test_the_document_gives_every_period_with_where_it_comes_from_and_what_removes_it():
    section = _section("## 2. The retention statement", "## 3.")
    rows = re.findall(r"^\| `([A-Z_]+)` \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$", section, re.M)
    assert [r[0] for r in rows] == [p.key for p in statement.PERIODS] and len(rows) == NUMBERS["Six"]
    assert "Six kinds of record have a period" in _flat(section)
    for (key, label, period, removed_by, hold), p in zip(rows, statement.PERIODS):
        assert label.strip() == p.label and removed_by.strip() == p.removed_by, key
        assert hold.strip() == ("Stops it" if p.hold else "None applies"), key
        assert (f"`{p.setting_key}`" in period) == (p.setting_key is not None), key
    flat = _flat(section)
    assert re.findall(r"`([A-Z_]+)`", flat.split("**A source** is one of ", 1)[1].split(".", 1)[0]) == list(statement.SOURCES)
    by = {p.key: p for p in statement.PERIODS}
    assert "The audit log is moved, not deleted" in flat and "Not deleted" in by["AUDIT"].how
    assert "became an incident" in by["DRONE_FOOTAGE"].kept_past and "of a flight still in progress" in flat
    assert "Fixed in the code" in dict((r[0], r[2]) for r in rows)["DRONE_RECEIPTS"]
    assert statement.SOURCE_WORDS["FIXED"] == "Fixed in the code"
    # What the statement reads is the jobs' own: the settings' names, the defaults' constants, the holds' kinds.
    service = _code(statement.__file__)
    for theirs in ("settings.EVIDENCE_RETENTION_DAYS", "continuous_recording.DEFAULT_RETENTION_DAYS",
                   "drone_retention.DEFAULT_TELEMETRY_DAYS", "drone_runner.RECEIPT_RETENTION.days",
                   "settings.AUDIT_RETENTION_YEARS", "evidence_hold.FRAMES_AND_CLIPS", "evidence_hold.RECORDINGS",
                   "evidence_hold.DRONE_MEDIA"):
        assert theirs in service, theirs
    assert not re.search(r"INSERT INTO|UPDATE \w+ SET|DELETE FROM", service + _code(subjects.__file__)), "both read only"
    # Kept, with no period: every table of the expansion, in the groups the document counts.
    kept = [t for k in statement.KEPT for t in k.tables]
    assert sorted(kept) == sorted(expansion_tables()) and len(kept) == NUMBERS["thirty-six"]
    assert "The thirty-six tables the expansion added, in eleven groups" in flat and len(statement.KEPT) == NUMBERS["eleven"]
    taken = {t for k in statement.KEPT for t in (k.taken_away or {})}
    assert taken == set(MAY_DELETE) | {"visitor_authorizations"} and "Three lose a row by a person's own step" in flat
    assert "an authorisation goes with its visitor when a data-subject erasure removes the visitor" in flat
    assert "Reading the statement is not audited: it names no person." in flat
    reading = _code(api.__file__).split("async def read_retention", 1)[1].split("class FindBody", 1)[0]
    assert "intel_audit" not in reading
    doc = _flat(_doc())
    for said in ("The statement reads; it sets nothing.", "No period is a statement too.",
                 "It says what is configured, not what the law requires."):
        assert said in doc, said
    assert "what the law requires" in statement.NOT_LAW and "removed by no job" in statement.EVERYTHING_ELSE
    assert "the job's is the one in force" in statement.INSTALLATION_NOTE


def test_the_document_says_what_a_subject_report_reads_and_who_may_ask():
    section = _section("## 3. A subject report", "## 4.")
    asked = re.findall(r"^\| ([^|`]+) \| `(GET|POST) ([^`]+)` \|", section, re.M)
    assert [(m, p) for _, m, p in asked] == [("GET", "/subjects/staff/{user}"), ("GET", "/subjects/visitor/{visitor}"),
                                              ("POST", "/subjects/written")]
    flat = _flat(section)
    columns = sum(len(h.columns) for h in subjects.STAFF)
    assert f"Each of the {columns} columns, in {len(subjects.STAFF)} tables, that refers to a member of staff" in flat
    assert (columns, len(subjects.STAFF)) == (71, 32)
    assert "`ABOUT`" in flat and "`BY`" in flat and (subjects.ABOUT, subjects.BY) == ("ABOUT", "BY")
    parts = {part for h in subjects.STAFF for part, _ in h.columns.values()}
    assert parts == {"ABOUT", "BY"}
    # Being sent, hosting and being given a task concern the person; opening, closing and deciding are steps they took.
    named = {(h.table, column): part for h in subjects.STAFF for column, (part, _) in h.columns.items()}
    assert named[("incident_responses", "guard_user_id")] == named[("visitor_authorizations", "host_user_id")] == "ABOUT"
    assert named[("case_tasks", "assigned_to_user_id")] == named[("workforce_advice_answers", "subject_user_id")] == "ABOUT"
    assert all(part == "BY" for (_, column), part in named.items() if re.search(r"_by_user_id$|actor_user_id$", column))
    service = _code(subjects.__file__)
    assert "A plate is the same plate however it was spaced" in flat and "regexp_replace(upper(label)" in service
    assert "`%` and `_` are read as themselves" in flat and subjects.like("50%_") == "%50\\%\\_%"
    assert "investigation.search" in service and "investigation.trail" in service and "resource_type = 'investigation_search'" in service
    for said in ("It finds; it reports nothing, and is not audited.", "Nothing of what a record says is in it."):
        assert said in flat, said
    for words in ("the account, its sessions and its audit entries", "attendance, rosters, leave, pay, training and violations",
                  "what the cameras saw", "a name written in a title or a note"):
        assert words in flat, words
    assert len(subjects.NOT_READ) == 4 and subjects.TEXT_IS_TEXT.endswith("It does not identify anybody.")
    router = _code(api.__file__)
    # A person, the organisation's own, not held to particular sites; and what is typed is in the body.
    own = router.split("def _the_organisations_own", 1)[1].split("@router.get", 1)[0]
    assert own.count("raise HTTPException(403") == 3 and "token.via_api_key" in own and "token.support_session_id" in own
    assert "if allowed is not None:" in own
    for name in ("find_subject", "staff_report", "visitor_report", "written_report"):
        assert "_the_organisations_own(token, allowed)" in router.split(f"async def {name}", 1)[1].split("\n\n\n", 1)[0], name
    assert "Query(" not in router, "nothing that is asked about is in an address"
    doc = _flat(_doc())
    for said in ("A name is text.", "A subject report is the organisation's own to ask for", "It covers every site",
                 "A name typed is sent in the body of a request, never in its address."):
        assert said in doc, said
    assert 'paced("30/minute", "subject-report"' in router and "thirty a minute for one person" in doc
    assert router.count('"subject.report"') == 1 and "Audited: `subject.report`." in doc
    # The words asked about are kept with the report; a person's name is not.
    reported = router.split("async def _reported", 1)[1].split("@router.get", 1)[0]
    assert '"words": answer["subject"].get("text")' in reported and '"name"' not in reported


def test_the_sweep_s_tables_are_the_document_s():
    section = _section("### Tables — 36", "### Routes")
    tables = expansion_tables()
    assert len(tables) == 36
    rows = dict(re.findall(r"^\| ([A-Z][^|]+) \| ([^|]+) \|", section, re.M))
    named = {what: re.findall(r"`([a-z_]+)`", cell) for what, cell in rows.items()}
    assert sorted(named["Delete a row"]) == sorted(MAY_DELETE)
    assert sorted(named["Change any column"]) == sorted(MAY_UPDATE_ANY)
    assert sorted(named["Add and read, and nothing else"]) == sorted(ADD_ONLY)
    assert rows["Add and read, and nothing else"].startswith(f"{len(ADD_ONLY)}: ")
    rest = len(tables) - len(MAY_DELETE) - len(MAY_UPDATE_ANY) - len(ADD_ONLY)
    assert rows["Change named columns only"].strip() == f"The other {rest}" and rest == 19
    assert not (set(MAY_DELETE) | set(MAY_UPDATE_ANY)) & ADD_ONLY
    flat = _flat(section)
    assert "Twelve tables have a trigger that holds a settled thing still" in flat
    assert "forced on its owner too" in flat and "named `tenant_isolation_…`" in flat
    assert "reads no row of any of the 36" in flat and "With no organisation in scope, nothing is read." in flat
    # Every migration of the expansion that creates a table forces row level security on it and names its policy so.
    versions = REPO_ROOT / "backend" / "alembic" / "versions"
    for path in sorted(versions.glob("01*.py")):
        if 143 <= int(path.name[:4]) <= 156:
            source = path.read_text(encoding="utf-8")
            if "CREATE TABLE" in source:
                assert "FORCE ROW LEVEL SECURITY" in source and "tenant_isolation_" in source, path.name
            upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
            assert "TRUNCATE" not in upgrade and "BYPASSRLS" not in source, path.name


def test_the_sweep_s_routes_and_every_exception_are_the_document_s():
    section = _section("### Routes — 176, of 16 routers", "## 5. API")
    found = routes()
    by = {(r["method"], r["path"]): r for r in found}
    assert len(found) == 176 and len(ROUTERS) == 16 and {r["module"] for r in found} == set(ROUTERS)
    counts = {m: sum(1 for r in found if r["method"] == m) for m in ("GET", "POST", "PATCH", "PUT", "DELETE")}
    flat = _flat(section)
    assert (f"{counts['GET']} `GET`, {counts['POST']} `POST`, {counts['PATCH']} `PATCH`, {counts['PUT']} `PUT`, "
            f"{counts['DELETE']} `DELETE`.") in flat

    def written(keys) -> set[tuple[str, str]]:
        return {(m, _as_written(p)) for m, p in keys}

    # The one that asks for either of two, the two words, the one that removes a row.
    (either,) = EITHER
    assert f"`{either[0]} {_as_written(either[1])}`" in flat
    assert "`{key}` of a report and `{kind}` of a device" in flat and sorted(WORDS.values()) == ["key", "kind"]
    (delete,) = DELETES
    assert f"**One route removes a row**: `DELETE {_as_written(delete[1])}`." in flat
    # Not audited, and why.
    not_audited = section.split("**Every write is audited**, but these:", 1)[1].split("**Every write is a person's**", 1)[0]
    assert _listed(not_audited) == written(NOT_AUDITED) and len(NOT_AUDITED) == 5
    for (method, path), why in NOT_AUDITED.items():
        row = re.search(rf"^\| `{method}` \| `{re.escape(_as_written(path))}` \| (.+) \|$", not_audited, re.M)
        assert row and row.group(1).strip() == why, (path, why)
    # A person's, but one; four that are not a GET and change nothing.
    (not_persons,) = NOT_A_PERSONS
    assert f"but `{not_persons[0]} {_as_written(not_persons[1])}`" in flat
    reads = flat.split("Four requests are not a `GET` and change nothing: ", 1)[1].split(". Each carries", 1)[0]
    assert {tuple(x.split(" ", 1)) for x in re.findall(r"`(POST [^`]+)`", reads)} == written(READS) and len(READS) == NUMBERS["Four"]
    # Records leaving the platform.
    taken = section.split("**Records leaving the platform are the organisation's own to take.**", 1)[1]
    assert _listed(taken) == written(TAKEN_OUT) and len(TAKEN_OUT) == 7
    assert all(by[key]["no_support"] and by[key]["audited"] for key in TAKEN_OUT)
    # The client role's one permission, and what it opens.
    assert list(CLIENT_READS) == ["dob:read"] and "holds one existing permission that does, `dob:read`" in flat
    assert "the occurrence book's search, one entry and the kinds of entry" in flat and len(CLIENT_READS["dob:read"]) == 3
    for said in ("**A permission on every one.**", "**A body takes the fields it declares and no other.**",
                 "**No route hands back where a file is kept.**", "as a guard, a viewer, an operator and the client role"):
        assert said in flat, said
    doc = _flat(_doc())
    assert "It found nothing to change in what the expansion added." in doc
    assert "**An exception is named, with its reason.**" in doc and "**Nothing that already worked was changed to pass it.**" in doc


def test_the_document_lists_the_routes_and_who_holds_what_and_the_migration_and_the_web_agree():
    section = _section("## 5. API", "**Permissions**")
    table = {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
             for method, path, needs in re.findall(r"^\| `(GET|POST)` \| `([^`]*)` \|([^|]*)\|$", section, re.M)}
    served = {(r["method"], _as_written(r["path"]).removeprefix("/data-governance")): r["needs"]
              for r in routes() if r["module"] == "data_governance"}
    assert table == served and len(served) == 5
    assert not [m for m, _ in served if m not in ("GET", "POST")]
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), \"([a-z]+:[a-z]+)\"\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"retention:read": {2, 3, 6, 8}, "subject:report": {2, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code
    assert "Super Admin, an operator, a guard and the client role hold neither of the new permissions." in _flat(_doc())
    upgrade = migration.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert not re.search(r"CREATE TABLE|ALTER TABLE|GRANT |REVOKE |CREATE POLICY|CREATE TRIGGER", upgrade)

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'((?:retention|subject):[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(CODES)
    web_table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", web_table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {role: {code for code, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert not re.search(pattern, src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0])


def test_the_screen_is_in_the_menu_changes_no_period_and_sends_a_name_in_the_body():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/data-retention)',.*permission: '([a-z:]+)'", sidebar) == [("/data-retention", "retention:read")]
    assert sidebar.index("title: 'Reports & Billing'") < sidebar.index("path: '/data-retention'") < sidebar.index("title: 'Configuration'")
    for kept in ("path: '/audit',", "path: '/settings',", "path: '/export',"):
        assert kept in sidebar, f"the existing screen at {kept} keeps its entry"
    assert 'path="data-retention"' in (WEB / "App.tsx").read_text(encoding="utf-8")
    doc = _flat(_doc())
    assert "**Data Retention** (`/data-retention`), under Reports & Billing" in doc and "Nothing on it changes a period." in doc
    assert "The phone is not changed in this phase." in doc
    page = (WEB / "pages" / "governance" / "DataRetention.tsx").read_text(encoding="utf-8")
    assert "usePermission('subject:report')" in page and "{mayAsk && <Tab value=\"person\"" in page
    for never in ("upsertSetting", "apiClient", "localStorage"):
        assert never not in page, never
    assert "{p.period.note" in page and "{data.not_law}" in page and "{r.text_is_text}" in page and "{r.what_it_is}" in page
    assert "'concerns them' : 'a step they took'" in page
    client = (WEB / "api" / "dataGovernance.ts").read_text(encoding="utf-8")
    assert "`${BASE}/subjects/find`, { kind, words }" in client and "`${BASE}/subjects/written`, { text }" in client
    assert "params:" not in client, "nothing that is asked about goes in an address"
    for name, values in (("Source", statement.SOURCES), ("Part", (subjects.ABOUT, subjects.BY)),
                         ("SubjectKind", ("STAFF", "VISITOR", "TEXT"))):
        found = set(re.findall(r"'([A-Z_]+)'", client.split(f"export type {name} =", 1)[1].split("\n", 1)[0]))
        assert found == set(values), name
    mobile = REPO_ROOT / "mobile" / "src"
    assert not list(mobile.rglob("*overnance*")) and not list(mobile.rglob("*etention*"))


def test_what_was_found_and_left_is_as_the_document_says_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 7. Files", "## 8."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src)/[A-Za-z_/.]+)` \|", _section("## 8. Tests", "## 9."), re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("No table is created", 1)[0])
    assert changed == CHANGED
    left = _section("## 9. Found, and left for the owner", "## 10.")
    assert len(re.findall(r"^\d+\. \*\*", left, re.M)) == 6
    # Each is still as it is said to be: this fails, and says so, the day one of them is put right.
    exports = (APP / "routers" / "exports.py").read_text(encoding="utf-8")
    assert 'str(row[k])' in exports and "/api/v1/export" in exports
    compliance = (APP / "routers" / "data_compliance.py").read_text(encoding="utf-8")
    export = compliance.split("async def dsr_export", 1)[1]
    assert "write_audit_log" not in compliance and "intel_audit" not in compliance
    assert "include_evidence_urls" in export and "LIMIT 100" in export and "user_id" not in export.split("FROM evidence", 1)[1].split('"""', 1)[0]
    privacy = (APP / "routers" / "pdpa.py").read_text(encoding="utf-8")
    assert '@router.get("/zones/camera/{camera_id}")\n' in privacy and "get_raw_db" in privacy
    compose = (REPO_ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    # Each is given to one service: the scheduler's and the drone runner's, not the API's.
    assert len(re.findall(r"^\s+AUDIT_RETENTION_YEARS:", compose, re.M)) == 1
    assert len(re.findall(r"^\s+DRONE_TELEMETRY_RETENTION_DAYS:", compose, re.M)) == 1
    for said in ("`UPDATE` and `DELETE` on `occurrence_book_entries`", "`/api/v1/export/…`",
                 "`POST /data-compliance/dsr-export/{user}`", "`AUDIT_RETENTION_YEARS`", "`DRONE_TELEMETRY_RETENTION_DAYS`",
                 "`GET /api/v1/privacy/zones/camera/{camera_id}`", "Nothing the expansion added has a retention period."):
        assert said in _flat(left), said
    not_done = _flat(_doc().split("## 10. What this does not do", 1)[1])
    for said in ("It sets no period and removes nothing.", "It does not hand a person's records over.",
                 "It identifies nobody.", "It erases nothing.", "The sweep is of what the expansion added.",
                 "The phone is not part of it.", "It has run on test data only."):
        assert said in not_done, said
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 13 | Compliance and hardening | **Built 2026-10-09** — migration `0156`" in built
    phase = built.split("### Phase 13", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 13, by additions only:**", 1)[1]
                      .split("No table is created", 1)[0]) == CHANGED
    for said in ("36 tables and 176 routes", "the 71 columns", "left for the owner", "no period is set for the expansion's records"):
        assert said in _flat(phase), said
