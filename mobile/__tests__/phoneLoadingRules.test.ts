/**
 * The web's loading rules, on every screen of the phone - read from the
 * screens' own source, so that a screen added later cannot quietly go back to
 * a spinner in the middle of nothing, or to "No alerts" when the server was
 * never reached. These hold:
 *
 *   1. loading   no screen fills itself with a lone spinner while it fetches;
 *                it shows a placeholder shaped like what is coming
 *   2. failed    every screen that fetches says when it failed, and can ask
 *                again - with the shared `ErrorState`, or its own words and
 *                button where it had them before
 *   4. kept      a list kept on screen while another filter is fetched says so
 *                (`RefreshingLine`), and never marks the kept rows as new
 *   5. live      a list whose rows are marked as they arrive uses `NewMark`;
 *                a camera's view says "Connecting" and "No picture"; and a
 *                tick is shown only where the server has said it is done
 */
// The app is not typed for Node: its readers, by what they are used for. The tests are run from mobile/.
const fs = require('fs') as {
  readFileSync: (file: string, encoding: 'utf8') => string
  readdirSync: (dir: string) => string[]
}
const read = (file: string) => fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n')

const DIR = 'src/screens/'
const SCREENS = fs.readdirSync(DIR)
  .filter((f) => f.endsWith('Screen.tsx'))
  .sort()
  .map((f) => ({ name: f.replace(/\.tsx$/, ''), src: read(DIR + f) }))

const fetches = (src: string) => /\buse(Query|Queries|InfiniteQuery)\s*[(<]/.test(src)

/**
 * Screens that said they had failed, with a button to ask again, before the
 * shared state existed - their own words are kept. Each must still show
 * failing (`isError`) and asking again (`refetch()`).
 */
const OWN_FAILURE = [
  'ActionCenterScreen', 'AttendanceScreen', 'DefectLogScreen', 'HandoverScreen', 'KeyRegisterScreen',
  'LostFoundScreen', 'MyScheduleScreen', 'PostOrdersScreen', 'TrainingScreen',
]

/** Screens that fetch and show nothing of their own when it fails, with the reason. */
const NOTHING_TO_FAIL: Record<string, string> = {
  // The counts above the leave list; the list itself (`LeaveTab`) says when it could not be read.
  LeaveScreen: 'a summary of the list below it',
}

/** A spinner that fills a screen, where the screen is waiting for something other than data. */
const SPINNER_ALLOWED: Record<string, string> = {
  VisitorsScreen: "waiting for the phone's own answer about the camera permission",
}

const CENTRE_SPINNER = /<View style=\{styles\.cent(?:er|re)\}>\s*<ActivityIndicator color=\{colors\.\w+\}(?: size="large")? \/>\s*<\/View>/

it('reads every screen', () => {
  expect(SCREENS.length).toBeGreaterThanOrEqual(57)
})

it('rule 1: no screen fills itself with a lone spinner while it fetches', () => {
  const offenders = SCREENS
    .filter((s) => CENTRE_SPINNER.test(s.src) && !(s.name in SPINNER_ALLOWED))
    .map((s) => s.name)
  expect(offenders).toEqual([])
  // What is allowed is still there - or the list is stale.
  for (const name of Object.keys(SPINNER_ALLOWED)) {
    expect(CENTRE_SPINNER.test(SCREENS.find((s) => s.name === name)!.src)).toBe(true)
  }
})

it('rule 2: every screen that fetches says when it failed, and can ask again', () => {
  const silent = SCREENS
    .filter((s) => fetches(s.src))
    .filter((s) => !s.src.includes('<ErrorState') && !OWN_FAILURE.includes(s.name) && !(s.name in NOTHING_TO_FAIL))
    .map((s) => s.name)
  expect(silent).toEqual([])

  for (const name of OWN_FAILURE) {
    const src = SCREENS.find((s) => s.name === name)!.src
    expect([name, /\bisError\b/.test(src)]).toEqual([name, true])
    expect([name, /refetch\(\)/.test(src)]).toEqual([name, true])
  }

  // Every shared failed state that can be asked again is given the way to.
  for (const s of SCREENS) {
    for (const part of s.src.split('<ErrorState').slice(1)) {
      const element = part.slice(0, part.indexOf('/>'))
      expect([s.name, element.includes('onRetry=')]).toEqual([s.name, true])
    }
  }
})

it('rule 2: "not there" stays "not found" on the screens that open one record by its id', () => {
  for (const name of ['AlertDetailScreen', 'IncidentDetailScreen', 'SituationDetailScreen']) {
    const src = SCREENS.find((s) => s.name === name)!.src
    expect([name, src.includes('isNotFound(')]).toEqual([name, true])
  }
})

it('rule 4: a list kept on screen while another is fetched says so, and its kept rows are never new', () => {
  const kept = SCREENS.filter((s) => s.src.includes('placeholderData: keepPreviousData'))
  expect(kept.length).toBeGreaterThanOrEqual(11)
  for (const s of kept) {
    expect([s.name, s.src.includes('<RefreshingLine />')]).toEqual([s.name, true])
    expect([s.name, /isPlaceholderData: \w+/.test(s.src)]).toEqual([s.name, true])
    if (s.src.includes('useNewItems(')) {
      const call = s.src.slice(s.src.indexOf('useNewItems('))
      expect([s.name, /settled: !\w+Refreshing/.test(call.slice(0, call.indexOf('})')))]).toEqual([s.name, true])
    }
  }
})

it('rule 5: a list that marks what arrives shows the mark', () => {
  const marking = SCREENS.filter((s) => s.src.includes('useNewItems('))
  expect(marking.map((s) => s.name)).toEqual(expect.arrayContaining([
    'ActionCenterScreen', 'AlertsScreen', 'DashboardScreen', 'DispatchScreen', 'DroneEventsScreen',
    'EmergencyScreen', 'IncidentsScreen', 'SituationsScreen',
  ]))
  for (const s of marking) expect([s.name, s.src.includes('<NewMark')]).toEqual([s.name, true])
})

it("rule 5: a camera's live view says what it is doing, and asks for its own address and no other", () => {
  const views = SCREENS.filter((s) => s.src.includes('/live?token='))
  expect(views.map((s) => s.name)).toEqual(['CameraLiveScreen', 'LiveWallScreen', 'ZoneDrawScreen'])
  for (const s of views) {
    // A picture that failed was hidden, or the page replaced with words of its own: now it is said, over the view.
    expect([s.name, /onerror=/i.test(s.src)]).toEqual([s.name, false])
    expect([s.name, s.src.includes('PICTURE_WATCH_SCRIPT') || s.src.includes('TILE_WATCH_SCRIPT')]).toEqual([s.name, true])
    expect([s.name, s.src.includes('<VideoLoadingState')]).toEqual([s.name, true])
  }
})

it('rule 5: "recorded" and "saved" are said only once the server has said so', () => {
  const scan = SCREENS.find((s) => s.name === 'PatrolScanScreen')!.src
  const scanSuccess = scan.slice(scan.indexOf('const scanMut = useMutation({'))
  const onSuccess = scanSuccess.slice(scanSuccess.indexOf('onSuccess:'), scanSuccess.indexOf('onError:'))
  // Said in the server's answer, for a verified scan - never for one queued offline.
  expect(onSuccess).toContain('if (data.verified) sayRecorded(')
  expect(scan.match(/sayRecorded\(/g)).toHaveLength(1)

  const run = SCREENS.find((s) => s.name === 'VirtualPatrolRunScreen')!.src
  const snap = run.slice(run.indexOf('const snapshotMut = useMutation({'))
  expect(snap.slice(snap.indexOf('onSuccess:'), snap.indexOf('onError:'))).toContain("if (result.ok) saySaved('Snapshot saved')")
  expect(run.match(/saySaved\(/g)).toHaveLength(1)
})

it("the dashboard's figures count up to the server's, and its cards take turns", () => {
  const dash = SCREENS.find((s) => s.name === 'DashboardScreen')!.src
  expect(dash).toContain('<AnimatedNumber value={value}')
  expect(dash).toContain('<FadeInView index={index}>')
  expect(dash.match(/index=\{\d\}/g)).toEqual(['index={0}', 'index={1}', 'index={2}', 'index={3}'])
})
