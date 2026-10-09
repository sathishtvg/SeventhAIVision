/**
 * The four newer phone screens, mounted: a person's own reading, the published
 * briefings, the work orders given to them, and their tasks on a case.
 *
 * The unit tests cover the words and what goes on the wire; this covers each
 * screen putting them in front of the person — and the things it must not do:
 * show a count without the server's note that it is not an appraisal, draw a
 * section a reviewer left out, or offer an act the server has not offered.
 */
import React from 'react'
import { fireEvent, render, waitFor } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

jest.mock('@/api/client', () => ({ apiClient: {} }))

const mockReading = jest.fn()
jest.mock('@/api/workforce', () => ({
  ...jest.requireActual('@/api/workforce'), getMyReading: (...a: unknown[]) => mockReading(...a),
}))
const mockBriefings = jest.fn()
const mockBriefing = jest.fn()
jest.mock('@/api/briefings', () => ({
  listPublishedBriefings: (...a: unknown[]) => mockBriefings(...a), getBriefing: (...a: unknown[]) => mockBriefing(...a),
}))
const mockOrders = jest.fn()
const mockStart = jest.fn()
const mockComplete = jest.fn()
jest.mock('@/api/maintenance', () => ({
  listMyOrders: (...a: unknown[]) => mockOrders(...a), startOrder: (...a: unknown[]) => mockStart(...a),
  completeOrder: (...a: unknown[]) => mockComplete(...a),
}))
const mockCases = jest.fn()
const mockCase = jest.fn()
const mockFinish = jest.fn()
jest.mock('@/api/cases', () => ({
  listMyCases: (...a: unknown[]) => mockCases(...a), getCase: (...a: unknown[]) => mockCase(...a),
  finishTask: (...a: unknown[]) => mockFinish(...a),
}))

import { BriefingsScreen } from '@/screens/BriefingsScreen'
import { MyCaseTasksScreen } from '@/screens/MyCaseTasksScreen'
import { MyReadingScreen } from '@/screens/MyReadingScreen'
import { MyWorkOrdersScreen } from '@/screens/MyWorkOrdersScreen'

const NOTE = 'These are counts of what is recorded, each beside how much there was to do. They are not an appraisal.'
const READING = {
  period: { days: 28, from: '2026-09-11T00:00:00Z', to: '2026-10-09T00:00:00Z' },
  sections: [
    { key: 'SHIFTS', title: 'Shifts', counted_from: 'Counted from the roster and check-ins.', personal: true },
    { key: 'PATROLS', title: 'Patrols', counted_from: 'Counted from tours and checkpoint scans.', personal: true },
    { key: 'HANDOVERS', title: 'Handovers', counted_from: 'Counted from shift handovers.', personal: true },
  ],
  // No figures came for handovers: the section is not drawn as noughts.
  figures: {
    SHIFTS: { shifts: 4, worked: 3, late: 1, late_minutes: 12, not_started: 1 },
    PATROLS: { tours_done: 2, tours_missed: 3, walked: 2, checkpoints_scanned: 16, checkpoints_total: 20 },
  },
  not_read: [],
  note: NOTE,
  recommendations: [{ key: 'r1', code: 'CERTIFICATE_LAPSING', statement: 'A first-aid certificate lapses within 30 days.',
                      consider: 'Booking the renewal course.', answer: null }],
  recommendations_note: 'Advice for a manager to consider. Nothing is decided by it.',
}

const SUMMARY = { id: 'b1', site: { id: 's1', name: 'Factory A' }, briefing_date: '2026-10-08', revision: 2, state: 'PUBLISHED',
                  replaced_by: null, published_at: '2026-10-09T00:10:00Z', published_by_name: 'Siti Rahman',
                  note: 'Gate 2 is shut for repairs until Friday.', left_out: [{ key: 'WORKFORCE', title: 'Workforce' }] }
const BRIEFING = {
  ...SUMMARY,
  sections: [
    { key: 'ALERTS', title: 'Alerts', left_out: false, lines: [{ text: '12 alerts were raised.', as_at: 'PERIOD' }] },
    { key: 'MAINTENANCE', title: 'Maintenance', left_out: false,
      lines: [{ text: '3 work orders are open.', as_at: 'DRAFTING' }] },
    // A reader is never sent a section that was left out; were one to arrive, it is still not drawn.
    { key: 'WORKFORCE', title: 'Workforce', left_out: true, lines: [{ text: '2 shifts were not started.', as_at: 'PERIOD' }] },
  ],
}

const ORDER = { id: 'w1', number: 'WO-0001', site_name: 'Factory A', asset_code: 'CAM-01', asset_name: 'Loading bay camera',
                title: 'Loading bay: no picture', description: 'No picture since Tuesday.', kind: 'CORRECTIVE', priority: 'HIGH',
                state: 'OPEN', due_at: '2026-10-12T02:00:00Z', started_at: null, overdue: false, assigned_to_me: true,
                may: { start: true, complete: true } }
const IN_HAND = { ...ORDER, id: 'w2', number: 'WO-0002', title: 'Gate 2 motor', description: null, state: 'IN_PROGRESS',
                  may: { start: false, complete: true } }
const NOT_OFFERED = { ...ORDER, id: 'w3', number: 'WO-0003', title: 'Fence light', description: null,
                      may: { start: false, complete: false } }

const TASK = { id: 't1', title: 'Ask the haulier for the driver register', detail: 'For the night of the 5th.',
               assigned_to_user_id: 'me', due_at: '2026-10-12T00:00:00Z', state: 'OPEN', created_by_name: 'Siti Rahman',
               may_finish: true }
const CASES = [
  { id: 'c1', case_number: 'CASE-0001', title: 'Forced gate', status: 'OPEN', site_name: 'Factory A', tasks_open: 2 },
  { id: 'c2', case_number: 'CASE-0002', title: 'Missing key', status: 'CLOSED', site_name: 'Factory A', tasks_open: 0 },
  { id: 'c3', case_number: 'CASE-0003', title: 'Tailgating', status: 'OPEN', site_name: null, tasks_open: 0 },
]
const CASE = { id: 'c1', case_number: 'CASE-0001', title: 'Forced gate', status: 'OPEN', site: { id: 's1', name: 'Factory A' },
               tasks_open: 2, tasks: [TASK, { ...TASK, id: 't2', title: 'Pull the gate log', may_finish: false }] }

const refusal = (detail: string, status = 409) => ({ response: { status, data: { detail } } })

let qc: QueryClient
function mount(screen: React.ReactElement) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{screen}</QueryClientProvider>)
}

beforeEach(() => {
  jest.clearAllMocks()
  mockReading.mockResolvedValue(READING)
  mockBriefings.mockResolvedValue([SUMMARY])
  mockBriefing.mockResolvedValue(BRIEFING)
  mockOrders.mockResolvedValue([ORDER, NOT_OFFERED, IN_HAND])
  mockStart.mockResolvedValue(ORDER)
  mockComplete.mockResolvedValue(ORDER)
  mockCases.mockResolvedValue(CASES)
  mockCase.mockResolvedValue(CASE)
  mockFinish.mockResolvedValue(CASE)
})

afterEach(() => qc?.clear())

jest.setTimeout(120_000)

describe('my reading', () => {
  it('puts the server\'s note that it is not an appraisal above the counts, and each count beside what there was to do', async () => {
    const s = mount(<MyReadingScreen />)
    expect((await s.findByTestId('reading-note')).props.children).toBe(NOTE)
    expect(mockReading).toHaveBeenCalledWith(28)
    expect(s.getByText('3 of 4 shifts worked')).toBeTruthy()
    expect(s.getByText('2 of 5 assigned tours done; 3 missed')).toBeTruthy()
    expect(s.getByText('Counted from the roster and check-ins.')).toBeTruthy()
    expect(s.queryByTestId('section-HANDOVERS')).toBeNull()
  })

  it('shows what is recommended as advice for a manager, with what it says to consider', async () => {
    const s = mount(<MyReadingScreen />)
    expect(await s.findByText('A first-aid certificate lapses within 30 days.')).toBeTruthy()
    expect(s.getByText('Advice for a manager to consider. Nothing is decided by it.')).toBeTruthy()
    expect(s.getByText('To consider: Booking the renewal course.')).toBeTruthy()
  })

  it('reads again over the period the person picks, and says a refusal in the server\'s words', async () => {
    const s = mount(<MyReadingScreen />)
    await s.findByTestId('reading-note')
    mockReading.mockRejectedValueOnce(refusal('days is one of 7, 28, 90', 422))
    fireEvent.press(s.getByText('Last 7 days'))
    await waitFor(() => expect(mockReading).toHaveBeenLastCalledWith(7))
    expect(await s.findByText('days is one of 7, 28, 90')).toBeTruthy()
  })
})

describe('daily briefings', () => {
  it('lists what was published, and opens one whole', async () => {
    const s = mount(<BriefingsScreen />)
    expect(await s.findByText('Factory A')).toBeTruthy()
    expect(s.getByText('Revision 2')).toBeTruthy()
    fireEvent.press(s.getByText('Factory A'))
    await waitFor(() => expect(mockBriefing).toHaveBeenCalledWith('b1'))
    expect(await s.findByText('12 alerts were raised.')).toBeTruthy()
    expect(s.getByText('Published by Siti Rahman')).toBeTruthy()
    expect(s.getByText('Gate 2 is shut for repairs until Friday.')).toBeTruthy()
  })

  it('says what a line is true of when that is not simply the day, and names a left-out section without drawing it', async () => {
    const s = mount(<BriefingsScreen />)
    fireEvent.press(await s.findByText('Factory A'))
    await s.findByTestId('briefing-MAINTENANCE')
    expect(s.getAllByText(/when it was drafted/).length).toBeGreaterThan(0)
    expect(s.queryByTestId('briefing-WORKFORCE')).toBeNull()
    expect(s.queryByText('2 shifts were not started.')).toBeNull()
    expect(s.getByTestId('left-out').props.children.join('')).toBe('Left out by the reviewer: Workforce.')
    fireEvent.press(s.getByText('All briefings'))
    expect(await s.findByText('Revision 2')).toBeTruthy()
  })

  it('says so when nothing has been published', async () => {
    mockBriefings.mockResolvedValue([])
    const s = mount(<BriefingsScreen />)
    expect(await s.findByText('No briefing has been published yet.')).toBeTruthy()
  })
})

describe('my work orders', () => {
  it('offers a step only where the server offers it, with what is in hand first', async () => {
    const s = mount(<MyWorkOrdersScreen />)
    expect(await s.findByText('WO-0002 · Gate 2 motor')).toBeTruthy()
    expect(s.getAllByTestId('work-order')).toHaveLength(3)
    expect(s.getAllByText('I have started')).toHaveLength(1)
    expect(s.getAllByText('It is done')).toHaveLength(2)
    expect(s.getAllByText('Loading bay camera (CAM-01) · Factory A')).toHaveLength(3)
    fireEvent.press(s.getByText('I have started'))
    await waitFor(() => expect(mockStart).toHaveBeenCalledWith('w1'))
    await waitFor(() => expect(mockOrders).toHaveBeenCalledTimes(2))
  })

  it('records what was done only once it is said in words', async () => {
    const s = mount(<MyWorkOrdersScreen />)
    await s.findByText('WO-0002 · Gate 2 motor')
    fireEvent.press(s.getAllByText('It is done')[0])
    fireEvent.press(s.getByText('Record it as done'))
    expect(mockComplete).not.toHaveBeenCalled()
    fireEvent.changeText(s.getByLabelText('What was done'), 'Motor brushes replaced.')
    fireEvent.changeText(s.getByLabelText('Parts used, if any'), 'Brush set')
    fireEvent.press(s.getByText('Record it as done'))
    await waitFor(() => expect(mockComplete).toHaveBeenCalledWith('w2', 'Motor brushes replaced.', 'Brush set'))
  })

  it('says a refusal in the server\'s words and keeps what was typed', async () => {
    mockComplete.mockRejectedValue(refusal('That work order is over already.'))
    const s = mount(<MyWorkOrdersScreen />)
    await s.findByText('WO-0002 · Gate 2 motor')
    fireEvent.press(s.getAllByText('It is done')[0])
    fireEvent.changeText(s.getByLabelText('What was done'), 'Motor brushes replaced.')
    fireEvent.press(s.getByText('Record it as done'))
    expect(await s.findByText('That work order is over already.')).toBeTruthy()
    expect(s.getByLabelText('What was done').props.value).toBe('Motor brushes replaced.')
  })

  it('says so when no work is given', async () => {
    mockOrders.mockResolvedValue([])
    const s = mount(<MyWorkOrdersScreen />)
    expect(await s.findByText('No work is given to you.')).toBeTruthy()
  })
})

describe('my case tasks', () => {
  it('reads only the open cases with something to do, and shows only the tasks this person may finish', async () => {
    const s = mount(<MyCaseTasksScreen />)
    expect(await s.findByText('Ask the haulier for the driver register')).toBeTruthy()
    expect(mockCase).toHaveBeenCalledTimes(1)
    expect(mockCase).toHaveBeenCalledWith('c1')
    expect(s.getAllByTestId('case-task')).toHaveLength(1)
    expect(s.queryByText('Pull the gate log')).toBeNull()
    expect(s.getByText('CASE-0001 · Forced gate')).toBeTruthy()
  })

  it('drops a task only with why', async () => {
    const s = mount(<MyCaseTasksScreen />)
    fireEvent.press(await s.findByText('It will not be done'))
    fireEvent.press(s.getByText('Drop the task'))
    expect(mockFinish).not.toHaveBeenCalled()
    fireEvent.changeText(s.getByLabelText('Why it is dropped'), 'The client declined.')
    fireEvent.press(s.getByText('Drop the task'))
    await waitFor(() => expect(mockFinish).toHaveBeenCalledWith('c1', 't1', 'drop', 'The client declined.'))
  })

  it('may be marked done without a word, and says a refusal in the server\'s words', async () => {
    mockFinish.mockRejectedValueOnce(refusal('That task is finished already.'))
    const s = mount(<MyCaseTasksScreen />)
    fireEvent.press(await s.findByText('It is done'))
    fireEvent.press(s.getByText('Record it as done'))
    await waitFor(() => expect(mockFinish).toHaveBeenCalledWith('c1', 't1', 'done', ''))
    expect(await s.findByText('That task is finished already.')).toBeTruthy()
  })

  it('says so when no task is waiting', async () => {
    mockCases.mockResolvedValue([])
    const s = mount(<MyCaseTasksScreen />)
    expect(await s.findByText('No case task is waiting on you.')).toBeTruthy()
    expect(mockCase).not.toHaveBeenCalled()
  })
})
