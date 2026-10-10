"""Page-aware motion and loading: what the two documents say, held to the code.

`PAGE_AWARE_ANIMATION_GAP_ANALYSIS.md` is the audit and
`docs/plans/2026-10-10-page-aware-motion-design.md` the design the owner agreed
to. How the pieces behave is held by the web tests (`src/motion/motion.test.ts`,
`src/components/states/states.test.tsx`). What is held here is what a web test
cannot see or would not think to look for:

  - the motion rules are in a style sheet the app really loads. The audit
    found that the old one, `src/index.css`, had never been imported by
    anything, so its page entrance, its keyframes and its reduced-motion rule
    had never run - and that loading it whole would break light mode;
  - no animation library was added, which the design says and the package
    file must go on saying;
  - nothing claims progress or success the server did not report, and a
    failure is an alert where a notice is only a status;
  - the documents say what was found and what was decided.

Read from the working tree, so it runs with the repository-inspection suites.
"""
from __future__ import annotations

import json
import re

from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

WEB = REPO_ROOT / "frontend"
SRC = WEB / "src"
AUDIT = REPO_ROOT / "PAGE_AWARE_ANIMATION_GAP_ANALYSIS.md"
DESIGN = REPO_ROOT / "docs" / "plans" / "2026-10-10-page-aware-motion-design.md"


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _sources():
    return [p for p in SRC.rglob("*") if p.suffix in (".ts", ".tsx") and "__preview__" not in p.parts]


def test_the_motion_rules_are_in_a_style_sheet_the_app_loads():
    entry = _read(SRC / "main.tsx")
    assert "import '@/motion/motion.css'" in entry
    # The old sheet and the build tool's template are imported by nothing, as the audit says - and must stay so
    # until somebody has dealt with what the old sheet would do to light mode.
    importers = sorted(p.relative_to(SRC).as_posix() for p in _sources()
                       if re.search(r"import\s+['\"][^'\"]*(?:index|App)\.css['\"]", _read(p)))
    assert importers == [], importers
    assert "index.css" not in _read(WEB / "index.html")
    audit = _flat(_read(AUDIT))
    assert "**`src/index.css` is imported by nothing, and never has been**" in audit
    assert "loading it whole would break light mode" in audit
    old = _read(SRC / "index.css")
    assert re.search(r"^#root \{[^}]*radial-gradient\([^}]*#0d0a2e", old, re.M | re.S), "the background that is dark whatever the theme"

    sheet = _read(SRC / "motion" / "motion.css")
    # It is about motion and nothing else: it paints no page and resets nothing.
    assert not re.search(r"^(html|body|#root|\*)[ ,{]", sheet, re.M)
    for rule in ("@keyframes fade-up {", ".page-enter--operational", "@starting-style {",
                 "@media (prefers-reduced-motion: reduce) {", ":root[data-motion='reduced'] *,", ".sav-new {", ".sav-refreshing ~ * {"):
        assert rule in sheet, rule
    # The three names the audit says were left without rules are still without them.
    for left in ("pulse-ring", "nav-item-enter", ".fade-in"):
        assert left not in sheet, left
        assert left.lstrip(".") in audit
    # The shell gives the page area the entrance for its kind of page.
    assert "className={pageEnterClass(location.pathname)}" in _read(SRC / "components" / "layout" / "AppShell.tsx")


def test_no_animation_library_was_added_and_the_timings_are_said_once():
    package = json.loads(_read(WEB / "package.json"))
    used = set(package["dependencies"]) | set(package["devDependencies"])
    assert not used & {"framer-motion", "motion", "gsap", "react-spring", "@react-spring/web", "animejs", "lottie-react"}
    assert "**No animation library.**" in _read(DESIGN) and "**No new library.**" in _read(AUDIT)
    # No lazy routes, so nothing for a route-level loading screen to wait on: the audit says why there is none.
    lazy = sorted(p.relative_to(SRC).as_posix() for p in _sources() if re.search(r"\blazy\(\s*\(\)\s*=>\s*import\(", _read(p)))
    assert lazy == ["pages/Dashboard.tsx"], lazy
    assert "lazy(" not in _read(SRC / "App.tsx"), "the route table loads every page directly"
    assert "a `RouteLoadingFallback` would have nothing to do" in _flat(_read(AUDIT))
    assert "imports the client portal with `lazy`" in _flat(_read(AUDIT))

    tokens = _read(SRC / "motion" / "tokens.ts")
    said = {name: int(ms) for name, ms in re.findall(r"^  (\w+): (\d+),$", tokens.split("export const duration = {", 1)[1].split("}", 1)[0], re.M)}
    assert said == {"fast": 150, "standard": 220, "panel": 280, "exit": 190, "page": 320, "slow": 350}
    design = _flat(_read(DESIGN))
    assert "fast 150 ms, standard 220 ms, panel 280 ms, page 320 ms, stagger 40 ms" in design
    theme = _read(SRC / "theme" / "glassmorphism.ts")
    assert "standard: motionTime.standard," in theme and "leavingScreen: motionTime.exit," in theme
    assert not re.search(r"duration:\s*\{[^}]*\b250\b", theme), "a second copy of a timing"


def test_the_shared_states_are_there_and_none_claims_what_the_server_has_not_said():
    exported = set(re.findall(r"\b([A-Z]\w+)\b", " ".join(re.findall(r"^export \{([^}]*)\}", _read(SRC / "components" / "states" / "index.ts"), re.M))))
    assert {"ErrorState", "TableErrorRow", "EmptyState", "TableEmptyRow", "RefreshingLine", "TableRefreshingRow", "LoadState",
            "TableSkeleton", "DashboardCardSkeleton", "ChartSkeleton", "CardGridSkeleton", "KpiSkeleton", "DetailSkeleton",
            "ProgressState", "SaveStatus", "SuccessTick", "AnimatedCounter", "AnimatedList", "VideoLoadingState", "StaleBadge",
            "LiveAnnouncer", "LoadFailureNotice", "MotionPreferences"} <= exported

    progress = _read(SRC / "components" / "states" / "Progress.tsx")
    # A percentage is written, and a bar is the measuring kind, only for a number that was given.
    assert "const known = typeof value === 'number' && Number.isFinite(value)" in progress
    assert progress.count("percent !== null") == 2 and "{status === 'done' && action}" in progress
    # A failure is an alert; the app-wide notice is a status that does not take the keyboard.
    assert 'role="alert"' in _read(SRC / "components" / "states" / "ErrorState.tsx")
    notice = _read(SRC / "components" / "states" / "LoadFailureNotice.tsx")
    assert 'role="status"' in notice and 'role="alert"' not in notice and "autoHideDuration={5000}" in notice
    assert "onError: (error) => useLoadFailures.getState().report(error)" in _read(SRC / "main.tsx")
    # One announcer for the whole app: only it, and the two states that report on one piece of work, are live regions.
    live = sorted(p.name for p in (SRC / "components" / "states").glob("*.tsx")
                  if ".test." not in p.name and "aria-live" in _read(p))
    assert live == ["Live.tsx", "Progress.tsx"], live
    # A placeholder waits by a style rule. No code holds content back for it: nothing in the states sets a timer
    # except to say "refreshing" late and to work out an age.
    timers = sorted(p.name for p in (SRC / "components" / "states").glob("*.ts*")
                    if ".test." not in p.name and re.search(r"\bset(Timeout|Interval)\(", _read(p)))
    assert timers == ["Live.tsx", "Refreshing.tsx"], timers


def test_the_documents_say_what_was_found_and_what_was_decided():
    audit, design = _flat(_read(AUDIT)), _flat(_read(DESIGN))
    for found in ("Of the 116 pages, 110 fetch data (516 queries between them).",
                  "**68 of the 110 pages that fetch never read the error of a query**",
                  "the page says **\"No alerts found\"**",
                  "there is **no long-running export job and no progress from the server**"):
        assert found in audit, found
    # What the alerts page did is what the audit says it did, until it is converted - and then the page says so itself.
    alerts = _read(SRC / "pages" / "Alerts.tsx")
    assert "No alerts found" in alerts
    assert "The owner decided on 10 October 2026 that **every page is converted**" in audit
    assert "**every page is converted**" in design and "## Checked against the design skill" in _read(DESIGN)
    for rule in ("**A failed request says it failed**", "**A changed filter keeps the rows that were there**",
                 "**Real content is never held back**", "No acknowledge, escalate or emergency control waits"):
        assert rule in design, rule
    # The phone app and the route-splitting are said not to be part of it, and the web is what was touched.
    assert "The **phone app** (`mobile/`) is a separate React Native code base" in audit
    assert "Lazy routes; a progress bar for exports" in design
