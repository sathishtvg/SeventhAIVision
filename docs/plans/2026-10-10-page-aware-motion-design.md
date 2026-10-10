# Page-aware motion and loading — design

Agreed with the owner on 10 October 2026, in three parts, after the audit in
`PAGE_AWARE_ANIMATION_GAP_ANALYSIS.md`. The owner's one decision: **every page
is converted**, not only the pages where the audit found a gap.

## 1. The motion system

- **`frontend/src/motion/` owns timing.** Tokens: fast 150 ms, standard
  220 ms, panel 280 ms, page 320 ms, stagger 40 ms; three easings. They are the
  values the app already used, written once. A test holds the CSS variables in
  `src/motion/motion.css` and the durations in the MUI theme equal to them.
  (It was to have been `index.css`; building it showed that file is loaded by
  nothing - the gap analysis says what that meant.)
  `fadeUpSx` and `useCountUp` move there; `lib/motion.ts` goes on exporting
  them, so no import breaks.
- **No animation library.** MUI's `Fade`, `Collapse` and `TransitionGroup`,
  and CSS. Nothing in the brief needs shared-layout or gesture animation.
- **Placeholders wait before they show**, set once for the whole app: a
  skeleton or spinner is in the page from the first frame, so nothing moves
  when it is replaced, and becomes visible only after about 150 ms. An answer
  that arrives sooner is never preceded by a flash. **Real content is never
  held back** to let a placeholder finish — there is no minimum display time.
- **One look for placeholders**: the shimmer.
- **A motion setting of the app's own**: follow the system, reduced, or full —
  kept with the user's theme choice. Reduced leaves brief fades and no
  movement. No state is ever carried by motion alone.
- **Housekeeping**: `pulse` (five copies) and the sign-in keyframes (three
  copies) become one each; `transition: all` becomes the properties meant.
- **The shell picks the entrance by kind of page**: operational screens (wall,
  playback, patrol in progress, command centre) fade in without movement;
  dashboards fade up; tables and forms fade. An entrance is CSS on a page that
  is already interactive. Nothing is delayed in code for it.

## 2. Loading, and what "converted" means

Five rules for every page, checked against the code by a test that can only
count down:

1. **Loading** shows a placeholder shaped like the content. Pages with only a
   spinner, or nothing, get one.
2. **A failed request says it failed** — wherever a page chooses between
   loading, nothing and the rows, there is a fourth answer: could not load,
   why, and *Try again*. (Before: 68 pages showed "No … found" when the server
   had not answered.)
3. **Empty** is said plainly, and only when the server answered with nothing.
4. **A changed filter keeps the rows that were there**, dimmed, with a
   progress line and "Refreshing" for a screen reader, until the new rows
   arrive. For lists only: something that shows one record at a time never
   shows the last record while the next loads.
5. **Saving, exporting and buffering** are told apart from loading. A
   percentage appears only when the server gives one; success only when the
   server has confirmed it.

For the whole app:

- **A safety net**: a request that fails anywhere raises one notice, so
  nothing fails silently where a page does not look.
- **What is new is marked and said**: rows arriving after the first load are
  tinted briefly in their severity's colour and announced in a polite live
  region ("2 new alerts"). Order, time and identity stay the server's.

Only a page's main content is converted; small helper requests (the sites for
a dropdown) are left. Authentication, roles and tenant separation are not
touched.

## 3. By group, order, and checking

| Group | On top of the five rules |
|---|---|
| Command centre, alerts, incidents | New rows marked and announced; cards enter and leave; status changes cross-fade. No acknowledge, escalate or emergency control waits |
| Platform owner | Counters from real values; staggered widgets; drawer and dialog transitions; saving / saved / failed |
| Dashboard, sites | Staggered cards; offline and degraded always visible |
| Video | Connecting, buffering, failed on the player and the wall's tiles. The alert glow becomes a steady border and a badge — the glow made the browser repaint the video. A tick when a snapshot or bookmark is really saved |
| Investigation, evidence | Searching state; results as they arrive; thumbnails fade in. "Verified" only when it is |
| Virtual patrol | Active camera and step; snapshot tick; checklist feedback. Animation cannot advance a patrol or skip an answer |
| Drone | Mission states; "telemetry is stale". Nothing drawn as live without fresh data |
| Maps, dispatch | Markers when real positions arrive; a pulse only for a fresh position |
| Reports, exports | Chart placeholders; honest status text; "ready" when the file is |
| Sign-in, navigation, settings | Calm entrance; consistent drawers, dialogs, menus; visible focus |

**Order**: the brief's phases 1–7. Each ends with type check, lint, the web
tests and a production build, then commit, CI and merge.

**Checking**: tests for no-flash and never-delayed content, error and retry,
empty against failed, kept rows, new-row announcements, reduced motion, and
that a patrol cannot advance early; the page-by-page matrix generated from the
code; the motion looked at in a browser.

**How the conversion is done**: the fourth answer of rule 2 is the same shape
on every page, so it is inserted by a script that reads each page's syntax
tree — where a page branches on a query's loading flag, it adds the failed
branch, bound to that query's own error and retry — and every page it could
not read with certainty is listed and done by hand. The result is held by the
type check, the lint, the tests and the build, and read page by page.

## Checked against the design skill

The owner asked for the design portions to be done with the project's design
skill (`ui-ux-pro-max`). Its guidance was queried for each concern, and where
it returned a match the match was applied:

| Its rule | What it changed or confirmed |
|---|---|
| Loading feedback matches the wait and does not flash; a stable skeleton with `aria-busy` | The wait before a placeholder is seen; every skeleton marks its region busy |
| Exits are quicker than entrances, about two thirds | A separate `exit` timing (190 ms against 280 ms); the theme's leaving time uses it |
| Stagger 30-50 ms an item | 40 ms, and after the tenth item the rest arrive together |
| Animate one or two things in a view, not everything | One group in a view takes turns (the figures, or the cards); rows of a table never do |
| Arriving slows down, leaving speeds up | The three easings and where each is used |
| Never rely on an animation ending for correctness | A new row's mark is taken off by a timer, not by its fade finishing |
| One contextual status message; do not make every badge a live region | One announcer for the whole app. "Refreshing" and "Snapshot saved" speak through it; they had each been given a live region of their own, and were changed |
| An error is announced, and offers a way to recover | `ErrorState` is an alert with *Try again*; a time-out says it was a time-out |
| An empty state says something helpful and offers an action | `EmptyState` takes a hint and an action |
| Small text has 4.5:1 contrast | Measured in a browser for every state, in both themes: the lowest is 4.86. The words of a status are in the text colour and its colour is carried by the icon - green and amber words on a light page measured about 3:1 and were changed |
| Toasts go away in 3-5 seconds and do not take focus | The failure notice: 5 seconds, a status, not an alert |
| Reduced motion is respected | The rule in `motion.css`, and the app's own setting |

Where it had nothing: page transitions and animation in React returned no
match, so those follow its general rules (cross-fade for content replaced in
place; transform and opacity only) and not a specific entry. Its preference
for spring curves was not taken: the brief asks for no bounce.

## Not in this work

Lazy routes; a progress bar for exports (the server reports no progress); the
phone app; any change to who may see or do what.
