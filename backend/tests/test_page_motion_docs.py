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


def test_each_page_has_its_own_scene_and_it_is_bounded_as_the_design_says():
    design, audit = _flat(_read(DESIGN)), _flat(_read(AUDIT))
    assert "## 4. Each page's own scene (added 13 October 2026)" in _read(DESIGN)
    for said in ("**the first click or key ends it at once**", "**A page where seconds matter is never covered**",
                 "**Settings has a switch, \"Page animations\"**", "and only those being fetched for the first time"):
        assert said in design, said
    assert "Each page's own scene: an entrance related to the page, then a loading related to it" in audit
    assert "have not been watched at the frame rate of a real screen" in audit

    scenes = _read(SRC / "motion" / "scenes.ts")
    # The pages that are never covered are the ones the design names.
    urgent = sorted(re.findall(r"'(/[\w-]+)': scene\([^\n]*urgent: true", scenes))
    assert urgent == ["/action-center", "/alarms", "/emergency", "/man-down", "/response-desk"], urgent
    assert re.search(r"drone-patrols\\/\[\^/\]\+\$/, scene\([^\n]*urgent: true", scenes), "a drone patrol in flight"
    assert "export const SCENE_MS = 520" in scenes and "export const SCENE_EXIT_MS = 190" in scenes

    player = _read(SRC / "components" / "scenes" / "PageScene.tsx")
    # It is drawn over the page and takes nothing from it; a key or a click ends it; it reads real requests.
    assert "aria-hidden" in player and "if (reduced || !wanted) return null" in player
    assert "window.addEventListener('pointerdown', cut, true)" in player and "window.addEventListener('keydown', cut, true)" in player
    assert "useIsFetching({ predicate: firstFetch })" in player
    assert "query.state.data === undefined" in player
    sheet = _read(SRC / "motion" / "motion.css")
    assert ".sav-scene { position: relative; height: 0; z-index: 2; pointer-events: none; }" in sheet
    assert ".sav-scene--cover + .page-enter {" in sheet
    # The shell draws it just before the page area, which is what the style sheet's rule relies on.
    shell = _read(SRC / "components" / "layout" / "AppShell.tsx")
    assert shell.index("<PageScene key=") < shell.index("className={pageEnterClass(location.pathname)}")
    assert "Page animations" in _read(SRC / "components" / "states" / "MotionPreferences.tsx")


def test_the_last_phases_are_what_the_documents_say_and_the_matrix_is_there():
    audit, design = _flat(_read(AUDIT)), _flat(_read(DESIGN))
    # The matrix is written from the pages by a web test that fails when the two disagree.
    matrix = _read(REPO_ROOT / "PAGE_AWARE_ANIMATION_MATRIX.md")
    assert "Written by `frontend/src/motion/matrix.test.ts` from the pages themselves" in matrix
    rows = [line for line in matrix.splitlines() if line.startswith("| `")]
    # One row for each file under the pages folder - however many pages are added later (the fleet's came on 15 October).
    page_files = [p for p in (SRC / "pages").rglob("*.tsx") if ".test." not in p.name]
    assert len(rows) == len(page_files) >= 116, (len(rows), len(page_files))
    missing = [line.split("|")[1].strip() for line in rows if "| NO |" in line or "| NONE |" in line]
    assert missing == ["`intel/IntelNav.tsx`"], missing
    assert "`PAGE_AWARE_ANIMATION_MATRIX.md`" in audit and "`PAGE_AWARE_ANIMATION_MATRIX.md`" in design

    # Rule 4 is done where it is safe and nowhere else.
    assert "another record's rows must never stand under this one's name" in audit
    pages = {p.relative_to(SRC / "pages").as_posix(): _read(p) for p in (SRC / "pages").rglob("*.tsx") if ".test." not in p.name}
    kept = [name for name, text in pages.items() if "isPlaceholderData: " in text]
    assert len(kept) >= 41, len(kept)
    for name in kept:
        assert "RefreshingRow />}" in pages[name] or "RefreshingLine />}" in pages[name], name
    # One record is never kept: a day of one camera's recordings, an investigation, a patrol in flight.
    for name in ("Playback.tsx", "investigations/Investigation.tsx", "drones/DronePatrol.tsx", "evidencePackages/EvidencePackage.tsx"):
        assert "keepPreviousData" not in pages[name], name

    # Nothing is drawn as live by what it last said: the clock is read again whether or not anything was fetched.
    assert '"Moving" was the last thing a tracker said, for ever.' in audit
    now = _read(SRC / "motion" / "useNow.ts")
    assert "setInterval(() => setNow(Date.now()), everyMs)" in now
    # The GPS page's fleet goes by the clock too: since 15 October 2026 through the fleet map's statusNow.
    assert "statusNow(v, limits, now)" in pages["GPS.tsx"]
    assert "(now - new Date(v.last_fix_at).getTime()) / 60_000" in _read(SRC / "components" / "fleet" / "fleetMapFormat.ts")
    assert "freshAt(now?.recorded_at, TELEMETRY_STALE_MS, useNow())" in pages["drones/DronePatrol.tsx"]

    # The tick on a patrol is the server's word and nothing more.
    patrol = pages["PatrolExecution.tsx"]
    assert "setSnapped(r.ok && camera ? camera.id : null)" in patrol
    assert patrol.count("finish.mutate()") == 1 and patrol.count("onAdvance()") == 1
    assert "Animation cannot advance a patrol or skip an answer" in design

    # An export says "ready" when the browser has the file, and never a percentage.
    export = pages["Export.tsx"]
    assert export.index("await onExport()") < export.index("setReady(true)")
    card = export.split("function ExportCard(", 1)[1].split("\n}\n", 1)[0]
    # A spinner while it works, and no bar that would have to show how far along it is.
    assert "LinearProgress" not in card and "determinate" not in card
    assert "There is no percentage: the server reports" in card

    # Tidying: no control animates "all" of itself, in the theme or anywhere else.
    everywhere = [p for p in _sources() if ".test." not in p.name]
    assert [p.relative_to(SRC).as_posix() for p in everywhere if re.search(r"transition: ?['\"`]all\b", _read(p))] == []
    for said in ("have been looked at stopped, not in motion", "Clickable cards that are not buttons cannot be reached by keyboard"):
        assert said in audit, said


def test_the_windows_app_and_the_phone_are_what_the_documents_say():
    audit, design = _flat(_read(AUDIT)), _flat(_read(DESIGN))
    # The Windows app is the web in a shell: the build that carries this work is named, and its identity is the same.
    desktop = json.loads(_read(REPO_ROOT / "desktop" / "package.json"))
    assert tuple(int(n) for n in desktop["version"].split(".")) >= (1, 0, 8)
    assert "**Desktop 1.0.8 has all of the above.**" in audit
    assert "### Desktop 1.0.8" in _read(REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md")

    # The phone: its screens' scenes, put round every screen by the navigators, with no library added for them.
    assert "## 5. The phone (added 14 October 2026)" in _read(DESIGN)
    assert "**Every screen of the phone has its own scene**" in audit
    phone = REPO_ROOT / "mobile"
    navigation = _read(phone / "src" / "navigation" / "index.tsx")
    assert navigation.count("screenLayout={sceneLayout}") == 6
    scene = _read(phone / "src" / "motion" / "ScreenScene.tsx")
    assert 'pointerEvents="none"' in scene and "onTouchStart={over ? undefined : cut}" in scene
    assert "useIsFetching({ predicate: firstFetch })" in scene and "useNativeDriver: true" in scene
    store = _read(phone / "src" / "motion" / "motionStore.ts")
    assert "isReduceMotionEnabled().catch(() => true)" in store, "a phone that cannot say is taken to want less motion"
    package = json.loads(_read(phone / "package.json"))
    used = set(package["dependencies"]) | set(package.get("devDependencies", {}))
    assert not used & {"react-native-reanimated", "moti", "lottie-react-native", "react-native-svg"}
    # The rest of the phone, asked for the same day: the web's loading rules, with the phone's own copies of its
    # states, kept to by a test that reads every screen.
    assert "**The phone keeps the web's loading rules too.**" in audit
    assert "the phone keeps the web's rules as well" in design
    states = phone / "src" / "components" / "states"
    for part in ("ErrorState.tsx", "Skeleton.tsx", "Live.tsx", "Animated.tsx", "liveHtml.ts", "errorText.ts"):
        assert (states / part).is_file(), part
    rules = _read(phone / "__tests__" / "phoneLoadingRules.test.ts")
    for rule in ("rule 1:", "rule 2:", "rule 4:", "rule 5:"):
        assert rule in rules, rule
    # A camera's view looks only at whether the picture is there, and asks for its own address and no other.
    live = _read(states / "liveHtml.ts")
    assert "naturalWidth" in live and not re.search(r"fetch\(|XMLHttpRequest|getImageData", live)
    # What was not done on the phone is said.
    for said in ("it has not been seen on a device or the emulator, only in tests",
                 "The phone build made on 14 October has the scenes and not the rules above"):
        assert said in audit, said

