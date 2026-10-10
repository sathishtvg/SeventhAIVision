# Page-aware animation and loading — gap analysis

Phase 0 of the brief *Premium Page-Aware Animations & Intelligent Loading
Experience*. Written on 10 October 2026 from the code as it is on `main`
(`c620eb3`), before anything was changed. It says what the web app already
does, what is missing, what limits the work, and the order it will be done in.

Every number here was counted from `frontend/src` (311 source files, tests
left out), not estimated.

## 1. What this covers

The **web app** (`frontend/`), which is also what the Windows app shows: the
desktop app is a copy of the web build, so it gains all of this at its next
build and needs no work of its own. The **phone app** (`mobile/`) is a separate
React Native code base with its own navigation and is not part of this brief.

## 2. What is there already

The app is React 19 with MUI 9 (emotion), React Router 7 (`<Routes>`, not the
data router), TanStack Query 5 and zustand. There is **no animation library**:
no Framer Motion, no Motion. There is, though, a motion layer that was built
piece by piece:

| What | Where | State |
|---|---|---|
| Easing and duration variables (`--ease-out`, `--t-fast` 150 ms, `--t-base` 220 ms, `--t-slow` 350 ms, `--t-spring` 400 ms) | `src/index.css` | Sound values, used by a handful of utility classes only |
| MUI transition durations (100 / 150 / 200 / 250 / 350 ms) and four easings | `src/theme/glassmorphism.ts` | A second copy of the same idea |
| `fadeUpSx(index)` — staggered card entrance, 60 ms a step | `src/lib/motion.ts` | Used on 13 pages |
| `useCountUp(value)` — a KPI counting up from its last value; honours reduced motion; safe in a background tab | `src/lib/motion.ts` | Used on 11 pages |
| Route entrance: the page area is re-keyed by path and fades up over 300 ms | `components/layout/AppShell.tsx` | The same for every page |
| `SkeletonRows` — table placeholder rows pinned to the real row height | `components/common/SkeletonRows.tsx` | Used on 3 pages of the 76 that have a table |
| `BrandLoader` — the in-app twin of the boot splash | `components/common/BrandLoader.tsx` | Used on 3 pages |
| `ErrorBoundary` — a page that cannot be drawn is replaced by a message | `components/common/ErrorBoundary.tsx` | Wraps every page |
| Boot splash, handed over by timers so a background tab is not left on it | `index.html`, `src/main.tsx` | Good; left alone |
| Reduced motion: one global rule that cuts every animation and transition to nothing | `src/index.css` | Complete for CSS, and blunt: it follows the operating system only |
| Toasts for live events, with sound | `components/layout/ToastContainer.tsx`, `store/alertSound.ts` | Left alone |

So this is **not** a project without animation. It is a project with a good
start and no single place that owns it.

### Corrected while building phase 1: half of that table was never running

The table above was read from the source. Checking the **built** style sheet
for a rule that should have been in it showed that **`src/index.css` is
imported by nothing, and never has been**: the built sheet held Leaflet's
styles and not one line of the app's own. So, in the product as it ran:

- there was **no page entrance** — the shell put the `page-enter` class on the
  page area, and no rule for it was loaded;
- the staggered card entrance **never played** — `fadeUpSx` names the
  `fade-up` keyframes, which exist only in that file; 13 pages asked for it;
- there was **no reduced-motion rule in force** — only `useCountUp` checked
  the setting, in code;
- the timing variables did not exist at run time (no code reads them, which is
  why nothing broke), and `pulse-ring` (top bar), `nav-item-enter` and
  `fade-in` named rules that were not there.

`src/App.css` is likewise the build tool's template, imported by nothing.

`index.css` is **left as it is and still not loaded**: besides the motion
rules it sets a page background that is dark whatever the theme, and loading
it whole would break light mode. What the app needs of it is in
`src/motion/motion.css`, which `main.tsx` imports, and a test holds that it
does. The three names in the last bullet are left without rules, as they have
always been in effect: turning on a pulse nobody has ever seen is a decision,
not a repair.

## 3. What was found

### 3.1 The motion layer is scattered
- 33 differently named keyframes. `pulse` is defined five times, in five
  pages; the sign-in page's three (`float-orb-a`, `float-orb-b`,
  `shimmer-bar`) are copied into all three sign-in pages.
- Durations are written as literals where they are used (63 `transition:`
  declarations in 26 files) and 14 of them are `transition: all`, which
  animates properties nobody meant to animate.
- Two sources of truth for timing (CSS variables and the MUI theme), which
  had already drifted: the same "standard" transition was 220 ms in one and
  250 ms in the other.

### 3.2 The route entrance is the same everywhere
Every page fades up 12 px over 300 ms: a settings table, the full-screen
camera wall and a patrol in progress alike. The wall and the patrol should
appear, not arrive. There is no exit animation, which is right — an exit
would hold navigation back — and the entrance replays when the path changes
and not when a filter in the query string does, which is also right.

### 3.3 Loading is uneven, and it flashes
Of the 116 pages, 110 fetch data (516 queries between them).

| While it loads, the page shows | Pages |
|---|---|
| a skeleton of some kind | 86 |
| a spinner and nothing else | 13 |
| nothing recognisable | 10 — `ClientPortal`, `LiveWall`, `Playback`, `PostOrders`, `Recordings`, `Roles`, `Roster`, `Shifts`, `Sites`, `intel/IntelNav` |

Nothing waits before showing a skeleton. On a local network most first loads
finish in under 100 ms, so the skeleton is drawn for a frame or two and
replaced: a flash, on almost every first visit to a page. (Later visits are
instant — query results are kept fresh for 30 seconds.)

### 3.4 A failed request looks like an empty list — the one finding that matters operationally
**68 of the 110 pages that fetch never read the error of a query**, and only 7
offer a way to try again. `Alerts` is typical: it shows placeholder rows while
loading, and otherwise the rows or "No alerts found". When the request
*fails*, loading is over and there are no rows, so the page says **"No alerts
found"**. An operator cannot tell a quiet site from a server that did not
answer. This is not an animation matter, but it is squarely inside "loading,
empty, error and success states", and it is the first thing to put right.

### 3.5 Background refresh is invisible, and filters blank the page
46 pages poll. Only 3 show that a refresh is in progress, and only 7 keep the
rows that were there while a changed filter loads; the rest drop to
placeholders and back on every change of filter or page number.

### 3.6 Live events change lists without saying what changed
A live event invalidates the queries it concerns and the lists redraw. A new
alert is announced by toast and sound, but in the list itself nothing marks
which row is new. In the whole app there are **two** `role="status"` /
`aria-live` regions, so a screen-reader user is told almost nothing about what
arrives.

### 3.7 Video says nothing while it connects
`HlsPlayer` handles network and media errors internally and shows no
connecting, buffering or failed state; the wall's tiles likewise. A tile that
has not connected and a tile showing a dark room look alike. The wall's alert
highlight is a box-shadow that animates forever over a video tile, which makes
the browser repaint around the video on every frame — the costliest kind of
animation in the app, in the place that can least afford it.

### 3.8 Things shown as live that may not be
- `GPS`: a vehicle whose status is "moving" pulses, whatever the age of its
  last position.
- Drone pages show the time a drone was last heard from, and nothing says
  when telemetry has gone stale.

### 3.9 Saving and exporting
Saves report through inline alerts (123 files) — consistent enough, and left
alone. Exports (`Export`, `Reports`) are single requests with a spinner on the
button; there is **no long-running export job and no progress from the
server** anywhere, so there is nothing real to draw a progress bar from.

### 3.10 Cost
- `backdrop-filter` blur is part of the theme (cards, drawers, dialogs) and is
  written by hand in 12 more files. It is the look of the product; it is also
  expensive on long lists and must never be put over video.
- All pages are in one bundle, loaded eagerly. No route is loaded lazily, so
  a `RouteLoadingFallback` would have nothing to do. (One page, the dashboard,
  imports the client portal with `lazy`; the route table imports the same
  page directly, so the build cannot split it off and says so on every build.)

## 4. What limits the work

1. **No page's business is rebuilt.** What a page fetches, what it lets
   somebody do and who may do it stay as they are; what changes is how it
   shows that it is loading, failed, empty or changed. (This document first
   recommended leaving the 86 pages that already have skeletons alone. The
   owner decided on 10 October 2026 that **every page is converted**; the
   design note says to what, and how it is held.)
2. **No new library.** MUI already brings `Fade`, `Collapse`, `Grow`, `Slide`
   and `TransitionGroup`; CSS does the rest. Motion for React would add a
   second system and tens of kilobytes to a bundle that is already one piece,
   for nothing that is needed here (no shared-layout or gesture animation).
3. **No lazy routes.** Splitting the bundle would change how every page first
   loads. It may be worth doing; it is a different piece of work.
4. **No "keep the old rows" switch for the whole app.** Showing site A's
   cameras while site B's load is safe only where the page says it is
   refreshing. It is turned on page by page, with that indicator.
5. **Security work is never held back.** No alert, acknowledgement, emergency
   control, video frame or patrol validation waits for an animation. Entrances
   are CSS on elements that are already interactive; nothing is delayed in
   JavaScript to make room for motion.
6. **Nothing is invented.** No progress percentage without a number from the
   server, no "verified" before verification, no moving marker without a fresh
   position.
7. Authentication, roles, tenant separation and row-level security are not
   touched. The platform owner (`seventhaivision`) and a customer's
   organisation stay as separate as they are.
8. Emotion mis-parses a CSS variable inside the `animation` shorthand and
   silently drops the lot (already found, see `lib/motion.ts`): animations are
   written with the long property names.
9. Tests run in jsdom, where nothing really animates: tests hold *behaviour*
   (what is shown when, what is announced, what reduced motion changes), not
   pixels. Motion itself is checked in a browser.

## 5. What is recommended

**One motion module that owns timing**, built on what is there: tokens in one
TypeScript file that the CSS variables and the theme are held equal to by a
test; the existing `fadeUpSx` and `useCountUp` kept and re-exported from it.

**A loading approach built on the queries the app already has** — no second
data layer:
- a delay before any placeholder is shown, and a minimum time once it is, so
  fast answers do not flash and slow ones do not flicker;
- states told apart: first load, refreshing, saving, failed, empty;
- an `ErrorState` with a retry wherever a list can fail, and — so that no
  failure is ever silent on the 68 pages that do not look — one handler on the
  query cache that says so;
- rows kept while a filter changes, on the pages where that is safe, with a
  visible "refreshing".

**Page-aware entrances**: the shell picks the entrance by the kind of page —
operational screens (wall, playback, patrol, command centre) fade in with no
movement; dashboards fade up and stagger their cards; tables and forms fade.

**What is new is marked, and said**: rows that arrive after the first load get
a brief tint in the colour of their severity and are announced in a polite
live region. Order, timestamps and identity are the server's and are not
touched.

**Video speaks for itself**: connecting, buffering and failed states on the
player and the wall's tiles; the alert highlight changed to something the
browser can draw without repainting the video.

**A motion setting of the app's own** — follow the system, reduced, or full —
kept with the user's other display preferences, on top of the operating
system's.

## 6. Order of work

| Phase | What | Touches |
|---|---|---|
| 0 | This document | — |
| 1 | Motion tokens, the motion setting, reduced motion in one place; the shared states: skeletons (table, cards, chart, KPI), `EmptyState`, `ErrorState`, `ProgressState`, `SaveStatus`, `AnimatedCounter`, new-row marking, the delayed-loading hook | New files; `index.css`, `ColorMode` |
| 2 | Shell: page-aware entrance, the silent-failure handler, navigation and drawer transitions | `AppShell`, `main.tsx`, `Sidebar` |
| 3 | Platform owner's pages, the dashboard, sites and cameras, analytics | Groups B, C |
| 4 | Command centre, alerts and incidents, the wall, playback, recordings | Groups A, D, E; `HlsPlayer` |
| 5 | Investigation, evidence, virtual patrol, drone patrol | Groups F, G, H |
| 6 | Maps and dispatch, reports and exports, sign-in, settings and forms, and every page not in a group above | Groups I, J, K and the rest |
| 7 | Performance pass, accessibility pass, the page-by-page matrix, documentation | — |

After each phase: type check, lint, the web tests, a production build.

The design that was agreed is in
`docs/plans/2026-10-10-page-aware-motion-design.md`.

## 7. What will stay as it is, said now

- Exports show honest status text, not a percentage: the server reports no
  progress. A real progress bar needs a job with progress on the server side.
- No route-level loading screen: there are no lazy routes to wait for.
- The glass look (blur) stays; it is only kept away from video and long lists.
- The phone app is not part of this.

## 8. Page groups

How the brief's groups map onto the pages that exist.

| Brief | Pages |
|---|---|
| A. Command centre | `CommandCentre`, `ActionCenter`, `intel/Situations`, `intel/Situation`, `board/OperationsBoard`, `response/ResponseDesk` |
| B. Platform owner | `PlatformDashboard`, `Tenants`, `TenantUsage`, `PlatformBilling`, `PlatformInvoices`, `PlatformAnalytics`, `ErrorCentre`, `SupportSessions` |
| C. Dashboard and sites | `Dashboard`, `Sites`, `Cameras`, `Zones`, `securityMap/SitePlaces`, `IoT`, `AccessControl`, `DeviceProtocols` |
| D. Video | `LiveWall`, `Playback`, `Recordings`, `BWC`, `components/common/HlsPlayer`, `VideoPlayer` |
| E. Events, alerts, incidents | `Alerts`, `Incidents`, `Detections`, `Alarms`, `Violations`, `ManDown`, `Notifications`, `cases/Cases` |
| F. Investigation and evidence | `investigations/*`, `Evidence`, `evidencePackages/*` |
| G. Virtual patrol | `VirtualPatrol`, `PatrolExecution` |
| H. Drone patrol | `drones/*` (ten pages) |
| I. Maps and dispatch | `MapView`, `GPS`, `securityMap/SecurityMap`, `Heatmap`, `GuardOps` |
| J. Reports and exports | `Analytics`, `Reports`, `Export`, `ScheduledReports`, `workforce/WorkforceReadings` |
| K. Sign-in, navigation, settings | `Login`, `ForgotPassword`, `ResetPassword`, `Sidebar`, `TopBar`, `Settings`, `Users`, `Roles` |
| Everything else (registers, rosters, payroll, training and the rest) | Converted to the same five rules as every other page, in phase 6 |

The page-by-page matrix of what was done is written in phase 7, from the code
as it then is.

## 9. Where the conversion stands

Kept up to date as the phases land. The exact list of pages not yet done is
`frontend/src/motion/conversion.ts`, which a test holds to the pages
themselves, so it cannot say more than is true.

| Rule | Done | How |
|---|---|---|
| Every page arrives in the way its kind of page should; placeholders wait before they show; less motion for whoever asks; no request fails without a word | All pages | Phases 1-2, in the shell and the style sheet - no page was edited for it |
| 2. A failed request says it failed | 102 of the 110 pages that fetch (226 places in 116 files, pages and the components they use) | A script over each page's syntax tree, then by hand where it could not be sure |
| 1. A placeholder shaped like the content, on the pages that had a spinner or nothing | Not yet | Phases 3-6 |
| 4. A changed filter keeps the last rows | Not yet | Phases 3-6 |
| What is new is marked and said | Not yet | Phases 4-5 |
| Video, patrol, drone, map, export states | Not yet | Phases 4-6 |

What rule 2 found beyond what the audit had counted:

- **A placeholder that never ends.** Seven places waited with
  `loading || !data`. When the request had failed there was no data and never
  would be, so the placeholder stayed for ever - an invoice, a payroll run, a
  roster batch, the AI layer's status and its decision policy, a handover, a
  man-down event. Each now answers "failed" first.
- **A patrol in flight replaced by an error.** The drone patrol page fetches
  its session every five seconds while the drone flies, and showed an error in
  place of the whole page if any one of those fetches failed. It is replaced
  now only when there is no session to show at all.
- 42 places showed a request's error in their own way (a red alert with the
  reason, and no way to try again). They show the shared state, which has one.
