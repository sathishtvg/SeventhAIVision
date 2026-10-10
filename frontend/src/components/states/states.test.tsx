import userEvent from '@testing-library/user-event'
import { Table, TableBody } from '@mui/material'
import { act, render, screen, within } from '@/test/utils'
import { ColorModeProvider } from '@/context/ColorMode'
import { MOTION_ATTRIBUTE, announce, useAnnouncer } from '@/motion'
import { NOTICE_GAP_MS, isQuiet, useLoadFailures } from '@/store/loadFailures'
import {
  AnimatedCounter, AnimatedList, EmptyState, ErrorState, LiveAnnouncer, LoadFailureNotice, LoadState, MotionPreferences,
  ProgressState, RefreshingLine, SaveStatus, StaleBadge, SuccessTick, TableEmptyRow, TableErrorRow, TableRefreshingRow,
  TableSkeleton, VideoLoadingState, ageText, errorText, isFresh, stateOf,
} from '@/components/states'

const refused = (status: number, detail?: unknown) => ({ response: { status, data: { detail } }, message: `Request failed with status code ${status}` })
const inTable = (row: React.ReactElement) => <Table><TableBody>{row}</TableBody></Table>

afterEach(() => {
  document.documentElement.removeAttribute(MOTION_ATTRIBUTE)
  useAnnouncer.setState({ text: '', count: 0 })
  useLoadFailures.setState({ open: false, raisedAt: -Infinity })
  localStorage.clear()
})

describe('a failed request says what kind of failure it was', () => {
  it('tells the server not reached from the server too slow from the server refusing from the server broken', () => {
    expect(errorText({ code: 'ERR_NETWORK', message: 'Network Error' })).toBe('The server could not be reached. Check the connection.')
    expect(errorText({ code: 'ECONNABORTED', message: 'timeout of 15000ms exceeded' })).toBe('The server took too long to answer.')
    expect(errorText(refused(429))).toMatch(/Too many requests/)
    expect(errorText(refused(403, 'You are not assigned to this site.'))).toBe('You are not assigned to this site.')
    expect(errorText(refused(403))).toBe('You do not have permission to see this.')
    expect(errorText(refused(404))).toBe('This is no longer there.')
    expect(errorText(refused(422, [{ msg: 'from must be before to' }, { msg: 'site is required' }]))).toBe('from must be before to; site is required')
    expect(errorText(undefined)).toBe('Something went wrong.')
  })

  it('does not show a person what a broken server said to itself', () => {
    const said = errorText(refused(500, 'Traceback (most recent call last): asyncpg.exceptions.UndefinedColumnError'))
    expect(said).toBe('The server had a problem answering. It has been recorded; try again in a moment.')
    expect(said).not.toMatch(/Traceback|asyncpg/)
  })
})

describe('ErrorState', () => {
  it('is an alert that says why and offers to try again', async () => {
    const retry = vi.fn()
    render(<ErrorState error={{ code: 'ERR_NETWORK', message: 'Network Error' }} onRetry={retry} title="Could not load the alerts" />)
    const alert = screen.getByRole('alert')
    expect(within(alert).getByText('Could not load the alerts')).toBeInTheDocument()
    expect(within(alert).getByText('The server could not be reached. Check the connection.')).toBeInTheDocument()
    await userEvent.setup().click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('has no button where there is nothing to try again', () => {
    render(<ErrorState error={refused(403)} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('is a row where the rows would be, across every column', () => {
    render(inTable(<TableErrorRow error={refused(500)} onRetry={() => {}} />))
    const cell = screen.getByRole('alert').closest('td')!
    expect(cell).toHaveAttribute('colspan', '999')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.queryByText(/no .* found/i)).not.toBeInTheDocument()
  })
})

describe('EmptyState', () => {
  it('says what there is none of, what that means, and what to do', () => {
    render(<EmptyState title="No cameras at this site" hint="Add one to see it on the wall." action={<button>Add camera</button>} />)
    expect(screen.getByText('No cameras at this site')).toBeInTheDocument()
    expect(screen.getByText('Add one to see it on the wall.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add camera' })).toBeInTheDocument()
    // It is not an alert: nothing went wrong.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('is a row of a table with the words it is given', () => {
    render(inTable(<TableEmptyRow cols={7}>No alerts found</TableEmptyRow>))
    expect(screen.getByText('No alerts found').closest('td')).toHaveAttribute('colspan', '7')
  })
})

describe('LoadState keeps each state honest', () => {
  const parts = { skeleton: <div>placeholder</div>, emptyState: <div>nothing here</div>, children: <div>the rows</div> }

  it('is not failed while it is still loading, and not empty unless the server said so', () => {
    const { rerender } = render(<LoadState loading error={refused(500)} empty {...parts} />)
    expect(screen.getByText('placeholder')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    rerender(<LoadState loading={false} error={refused(500)} empty {...parts} />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('nothing here')).not.toBeInTheDocument()

    rerender(<LoadState loading={false} empty {...parts} />)
    expect(screen.getByText('nothing here')).toBeInTheDocument()

    rerender(<LoadState loading={false} {...parts} />)
    expect(screen.getByText('the rows')).toBeInTheDocument()
  })

  const query = (over: object) => ({
    data: undefined as { items: number[] } | undefined, error: null as unknown, isLoading: false, isError: false,
    isFetching: false, isPlaceholderData: false, refetch: vi.fn(), ...over,
  })
  const none = (d: { items: number[] }) => d.items.length === 0

  it('reads a query: failed means failed with nothing to show', () => {
    expect(stateOf(query({ isLoading: true, isFetching: true }), none)).toMatchObject({ loading: true, error: undefined, empty: false })
    const failed = stateOf(query({ isError: true, error: refused(500) }), none)
    expect(failed.error).toEqual(refused(500))
    expect(failed.empty).toBe(false)
    expect(stateOf(query({ data: { items: [] } }), none)).toMatchObject({ loading: false, error: undefined, empty: true })
    expect(stateOf(query({ data: { items: [1] } }), none)).toMatchObject({ empty: false, refreshing: false })
  })

  it('leaves the rows that are there when a refresh over them fails, and says so separately', () => {
    const state = stateOf(query({ data: { items: [1, 2] }, isError: true, error: refused(500) }), none)
    expect(state).toMatchObject({ error: undefined, empty: false, refreshFailed: true })
  })

  it('knows the rows are the last list’s while this one’s are fetched, and does not call them empty', () => {
    const state = stateOf(query({ data: { items: [] }, isPlaceholderData: true, isFetching: true }), none)
    expect(state).toMatchObject({ refreshing: true, empty: false, loading: false })
  })

  it('retries through the query', () => {
    const q = query({ isError: true, error: refused(500) })
    stateOf(q).onRetry()
    expect(q.refetch).toHaveBeenCalledTimes(1)
  })
})

describe('placeholders and refreshing', () => {
  it('draws table rows as wide as the table and marks nothing as an alert', () => {
    render(inTable(<TableSkeleton cols={5} rows={3} />))
    expect(document.querySelectorAll('tbody tr')).toHaveLength(3)
    expect(document.querySelectorAll('tbody tr:first-child td')).toHaveLength(5)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says a list is being refreshed once it has been for a moment, through the one announcer', () => {
    vi.useFakeTimers()
    try {
      const { unmount } = render(<><LiveAnnouncer /><RefreshingLine label="Refreshing the alerts" /></>)
      expect(document.querySelector('.sav-refreshing')).toHaveAttribute('aria-busy', 'true')
      // It has no live region of its own to compete with the announcer.
      expect(document.querySelector('.sav-refreshing')).not.toHaveAttribute('aria-live')
      expect(useAnnouncer.getState().count).toBe(0)
      act(() => { vi.advanceTimersByTime(600) })
      expect(screen.getByTestId('live-announcer')).toHaveTextContent('Refreshing the alerts')
      unmount()

      // A refresh that is over sooner says nothing.
      const quick = render(inTable(<TableRefreshingRow label="Refreshing the incidents" />))
      act(() => { vi.advanceTimersByTime(300) })
      quick.unmount()
      act(() => { vi.advanceTimersByTime(1000) })
      expect(useAnnouncer.getState().text).toBe('Refreshing the alerts')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('work in progress never claims what the server has not said', () => {
  it('writes no percentage when none was reported', () => {
    render(<ProgressState status="working" label="Building the evidence package" detail="A long recording can take a few minutes." />)
    expect(screen.getByRole('status')).toHaveTextContent('Building the evidence package')
    expect(screen.getByRole('status')).not.toHaveTextContent('%')
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow')
  })

  it('shows the amount when there is one, and never more than all of it', () => {
    const { rerender } = render(<ProgressState status="working" label="Uploading" value={42.4} />)
    expect(screen.getByRole('status')).toHaveTextContent('42%')
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42')
    rerender(<ProgressState status="working" label="Uploading" value={140} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
  })

  it('offers the result only when it is done, and a retry only when it failed', async () => {
    const download = <button>Download</button>
    const { rerender } = render(<ProgressState status="working" label="Exporting" action={download} onRetry={() => {}} />)
    expect(screen.queryByRole('button', { name: 'Download' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()

    rerender(<ProgressState status="done" label="Export ready" action={download} onRetry={() => {}} />)
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()

    const retry = vi.fn()
    rerender(<ProgressState status="failed" label="Export failed" error={refused(500)} action={download} onRetry={retry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('The server had a problem answering')
    expect(screen.queryByRole('button', { name: 'Download' })).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('can be stopped only where stopping is offered', () => {
    const { rerender } = render(<ProgressState status="working" label="Exporting" />)
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    rerender(<ProgressState status="working" label="Exporting" onCancel={() => {}} />)
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('says saving, saved, or why not - and nothing at rest', () => {
    const { rerender, container } = render(<SaveStatus state="idle" />)
    expect(container).toBeEmptyDOMElement()
    rerender(<SaveStatus state="saving" />)
    expect(screen.getByRole('status')).toHaveTextContent('Saving…')
    rerender(<SaveStatus state="saved" />)
    expect(screen.getByRole('status')).toHaveTextContent('Saved')
    rerender(<SaveStatus state="failed" error={refused(403, 'Only an administrator can change this.')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Not saved: Only an administrator can change this.')
  })

  it('confirms in words, and tells a screen reader through the one announcer', () => {
    const { rerender } = render(<><LiveAnnouncer /><SuccessTick show={false} label="Snapshot saved" /></>)
    expect(screen.queryByText('Snapshot saved')).not.toBeInTheDocument()
    expect(useAnnouncer.getState().count).toBe(0)
    rerender(<><LiveAnnouncer /><SuccessTick show label="Snapshot saved" /></>)
    expect(screen.getAllByText(/Snapshot saved/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByTestId('live-announcer')).toHaveTextContent('Snapshot saved')
    // One live region in all: the tick has none of its own.
    expect(screen.getAllByRole('status')).toHaveLength(1)
  })
})

describe('the one announcer', () => {
  it('says what was last said, politely and as a whole', () => {
    render(<LiveAnnouncer />)
    const region = screen.getByTestId('live-announcer')
    expect(region).toHaveAttribute('role', 'status')
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(region).toHaveAttribute('aria-atomic', 'true')
    act(() => announce('2 new alerts'))
    expect(region).toHaveTextContent('2 new alerts')
  })

  it('says the same words twice as two announcements', () => {
    render(<LiveAnnouncer />)
    const region = screen.getByTestId('live-announcer')
    act(() => announce('1 new alert'))
    const first = region.textContent
    act(() => announce('1 new alert'))
    expect(region.textContent).not.toBe(first)
    expect(region).toHaveTextContent('1 new alert')
  })
})

describe('figures and lists', () => {
  it('never shows a 0 for a figure that has not been loaded', () => {
    const { container, rerender } = render(<AnimatedCounter value={undefined} />)
    expect(container).not.toHaveTextContent('0')
    rerender(<AnimatedCounter value={null} />)
    expect(container).not.toHaveTextContent('0')
  })

  it('gives a screen reader the real figure at once, whatever the count is passing through', () => {
    document.documentElement.setAttribute(MOTION_ATTRIBUTE, 'reduced')
    const { container } = render(<AnimatedCounter value={1284} format={(n) => `${n.toLocaleString('en-GB')} alerts`} />)
    const [counting, real] = Array.from(container.querySelectorAll('span > span'))
    expect(counting).toHaveAttribute('aria-hidden', 'true')
    expect(real).toHaveTextContent('1,284 alerts')
    // With reduced motion the figure shown is the figure, with no counting.
    expect(counting).toHaveTextContent('1,284 alerts')
  })

  it('draws a list in the order it is given', () => {
    document.documentElement.setAttribute(MOTION_ATTRIBUTE, 'reduced')
    const items = [{ id: 'c', name: 'Third raised' }, { id: 'a', name: 'First raised' }, { id: 'b', name: 'Second raised' }]
    render(<AnimatedList items={items} idOf={(i) => i.id}>{(item) => <div data-testid="card">{item.name}</div>}</AnimatedList>)
    expect(screen.getAllByTestId('card').map((el) => el.textContent)).toEqual(['Third raised', 'First raised', 'Second raised'])
  })
})

describe('live, or not', () => {
  it('goes stale on screen when it goes stale, without anything being fetched', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date('2026-10-10T08:00:00Z'))
      render(<StaleBadge at="2026-10-10T07:59:50Z" staleAfterMs={30_000} />)
      expect(screen.getByText('Live')).toBeInTheDocument()
      act(() => { vi.advanceTimersByTime(60_000) })
      expect(screen.queryByText('Live')).not.toBeInTheDocument()
      expect(screen.getByText(/^Stale · 1 min ago$/)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('says when there has been no reading at all', () => {
    render(<StaleBadge at={null} />)
    expect(screen.getByText('No data yet')).toBeInTheDocument()
  })

  it('calls nothing fresh that has no time, a bad time or an old one', () => {
    const now = Date.parse('2026-10-10T08:00:00Z')
    expect(isFresh('2026-10-10T07:59:45Z', 30_000, now)).toBe(true)
    expect(isFresh('2026-10-10T07:59:00Z', 30_000, now)).toBe(false)
    expect(isFresh(null, 30_000, now)).toBe(false)
    expect(isFresh('not a time', 30_000, now)).toBe(false)
    expect([5_000, 90_000, 7_200_000, 200_000_000].map(ageText)).toEqual(['5 s ago', '2 min ago', '2 h ago', '2 d ago'])
  })

  it('says what a video tile is doing when it has no picture', async () => {
    const retry = vi.fn()
    const { rerender } = render(<VideoLoadingState state="connecting" detail="Gate 2" />)
    expect(screen.getByRole('status')).toHaveTextContent('Connecting')
    expect(screen.getByRole('status')).toHaveTextContent('Gate 2')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    rerender(<VideoLoadingState state="buffering" onRetry={retry} />)
    expect(screen.getByRole('status')).toHaveTextContent('Buffering')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    rerender(<VideoLoadingState state="offline" />)
    expect(screen.getByRole('alert')).toHaveTextContent('Camera offline')

    rerender(<VideoLoadingState state="failed" onRetry={retry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('No picture')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })
})

describe('the motion setting', () => {
  it('follows the device until the person chooses, is written where the style sheet reads it, and is kept', async () => {
    const user = userEvent.setup()
    render(<ColorModeProvider><MotionPreferences /></ColorModeProvider>)
    expect(screen.getByRole('radio', { name: /Follow this device/ })).toBeChecked()
    expect(document.documentElement.hasAttribute(MOTION_ATTRIBUTE)).toBe(false)

    await user.click(screen.getByRole('radio', { name: /Reduced/ }))
    expect(document.documentElement.getAttribute(MOTION_ATTRIBUTE)).toBe('reduced')
    expect(JSON.parse(localStorage.getItem('seventh-ai-ui-prefs:anon')!)).toMatchObject({ motion: 'reduced' })

    await user.click(screen.getByRole('radio', { name: /Full/ }))
    expect(document.documentElement.getAttribute(MOTION_ATTRIBUTE)).toBe('full')

    await user.click(screen.getByRole('radio', { name: /Follow this device/ }))
    expect(document.documentElement.hasAttribute(MOTION_ATTRIBUTE)).toBe(false)
  })

  it('is read back on the next visit, and a value it does not know is the device’s', () => {
    localStorage.setItem('seventh-ai-ui-prefs:anon', JSON.stringify({ mode: 'dark', motion: 'reduced' }))
    const first = render(<ColorModeProvider><MotionPreferences /></ColorModeProvider>)
    expect(screen.getByRole('radio', { name: /Reduced/ })).toBeChecked()
    expect(document.documentElement.getAttribute(MOTION_ATTRIBUTE)).toBe('reduced')
    first.unmount()

    localStorage.setItem('seventh-ai-ui-prefs:anon', JSON.stringify({ mode: 'dark', motion: 'sideways' }))
    render(<ColorModeProvider><MotionPreferences /></ColorModeProvider>)
    expect(screen.getByRole('radio', { name: /Follow this device/ })).toBeChecked()
  })

  it('leaves the theme choice as it was when the motion is changed, and the motion when the theme is', async () => {
    localStorage.setItem('seventh-ai-ui-prefs:anon', JSON.stringify({ mode: 'light', brandColor: '#00A8A8' }))
    render(<ColorModeProvider><MotionPreferences /></ColorModeProvider>)
    await userEvent.setup().click(screen.getByRole('radio', { name: /Reduced/ }))
    expect(JSON.parse(localStorage.getItem('seventh-ai-ui-prefs:anon')!)).toEqual({ mode: 'light', brandColor: '#00A8A8', motion: 'reduced' })
  })
})

describe('no request for data fails without a word', () => {
  it('raises one notice for failures that come together, and another only after a while', () => {
    const { report } = useLoadFailures.getState()
    report(refused(500), 1_000)
    expect(useLoadFailures.getState().open).toBe(true)
    useLoadFailures.getState().close()
    report({ code: 'ERR_NETWORK' }, 2_000)
    report(refused(503), 30_000)
    expect(useLoadFailures.getState().open).toBe(false)
    report(refused(503), 1_000 + NOTICE_GAP_MS)
    expect(useLoadFailures.getState().open).toBe(true)
  })

  it('says nothing of being signed out or of a request the page no longer wanted', () => {
    expect(isQuiet(refused(401))).toBe(true)
    expect(isQuiet({ code: 'ERR_CANCELED' })).toBe(true)
    expect(isQuiet({ name: 'AbortError' })).toBe(true)
    expect(isQuiet(refused(500))).toBe(false)
    expect(isQuiet({ code: 'ERR_NETWORK' })).toBe(false)
    useLoadFailures.getState().report(refused(401), 5_000)
    expect(useLoadFailures.getState().open).toBe(false)
  })

  it('is a status, not an alert: it does not take the keyboard', () => {
    render(<LoadFailureNotice />)
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument()
    act(() => useLoadFailures.getState().report(refused(500)))
    const notice = screen.getByText('Some information could not be loaded. What is on screen may be out of date.')
    expect(notice.closest('[role="status"]')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
