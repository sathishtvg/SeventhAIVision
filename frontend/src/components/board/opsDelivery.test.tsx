/**
 * Sent by email (4 November 2026, Phase 4 of the known limits): who a briefing is emailed to, the reports' schedules,
 * and what became of each email.
 *
 * What these pin: a schedule says when it sends in words; a briefing list is saved with the people and roles chosen;
 * a schedule is made with the report, the timing and who it goes to, and paused or started again; what was sent says,
 * person by person, sent or left out with why.
 */
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { BriefingEmails, DeliveryTab } from '@/components/board/OpsDelivery'
import { whenWords } from '@/components/board/deliveryFormat'
import * as api from '@/api/opsDelivery'
import type { DeliveryOptions, Schedule } from '@/api/opsDelivery'

vi.mock('@/api/opsDelivery', () => ({
  getDeliveryOptions: vi.fn(), getBriefingLists: vi.fn(), setBriefingList: vi.fn(), getBriefingEmails: vi.fn(),
  listSchedules: vi.fn(), makeSchedule: vi.fn(), changeSchedule: vi.fn(), removeSchedule: vi.fn(), getScheduleEmails: vi.fn(),
}))

const OPTIONS: DeliveryOptions = {
  reports: [{ key: 'access', title: 'Door events', periodic: true, needs: ['access:read'] },
            { key: 'device-health', title: 'Device health, as it is read now', periodic: false, needs: ['device:read'] }],
  frequencies: ['DAILY', 'WEEKLY', 'MONTHLY'],
  people: [{ id: 'u1', name: 'Tan Wei Ming', role: 'Manager' }, { id: 'u2', name: 'Lim Mei Ling', role: 'Supervisor' }],
  roles: [{ id: 2, name: 'Admin' }, { id: 8, name: 'Manager' }],
  sites: [{ id: 's1', name: 'Factory A' }],
  every_site_allowed: true,
}
const SCHEDULE: Schedule = {
  id: 'x1', report_key: 'access', report_title: 'Door events', site_id: 's1', site_name: 'Factory A', frequency: 'WEEKLY',
  hour: 7, weekday: 0, month_day: null, users: [{ id: 'u1', name: 'Tan Wei Ming' }], roles: [], is_active: true,
  next_run_at: '2026-11-08T23:00:00Z', last_run_at: null, created_at: '2026-11-04T00:00:00Z', created_by_name: 'Tan Wei Ming',
  sent: 3, failed: 0, left_out: 1,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getDeliveryOptions).mockResolvedValue(OPTIONS)
  vi.mocked(api.getBriefingLists).mockResolvedValue({ lists: [], note: 'A published briefing is emailed to the people on its list.' })
  vi.mocked(api.listSchedules).mockResolvedValue({ schedules: [SCHEDULE], note: 'Each person gets the report as they could take it out.' })
  vi.mocked(api.setBriefingList).mockResolvedValue({ lists: [], note: '' })
  vi.mocked(api.makeSchedule).mockResolvedValue(SCHEDULE)
  vi.mocked(api.changeSchedule).mockResolvedValue({ ...SCHEDULE, is_active: false })
})

describe('when a schedule sends', () => {
  it('is said in words', () => {
    const base = { hour: 7, weekday: null, month_day: null }
    expect(whenWords({ ...base, frequency: 'DAILY' })).toBe('Every day at 07:00, for the day before')
    expect(whenWords({ ...base, frequency: 'WEEKLY', weekday: 0 })).toBe('Every Monday at 07:00, for the week before')
    expect(whenWords({ ...base, frequency: 'MONTHLY', month_day: 1 })).toBe('On the 1st of each month at 07:00, for the month before')
    expect(whenWords({ ...base, frequency: 'MONTHLY', month_day: 22 })).toContain('22nd')
    expect(whenWords({ ...base, frequency: 'MONTHLY', month_day: 13 })).toContain('13th')
  })
})

describe('Sent by email', { timeout: 60_000 }, () => {
  it('saves who the briefing is emailed to, by role and by name', async () => {
    render(<DeliveryTab />)
    const lists = await screen.findByTestId('briefing-lists')
    expect(within(lists).getByText('Every briefing')).toBeInTheDocument()
    fireEvent.click(within(lists).getByRole('button', { name: 'Change' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Manager' }))
    fireEvent.mouseDown(within(dialog).getByLabelText('People, by name'))
    fireEvent.click(await screen.findByText('Lim Mei Ling (Supervisor)'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setBriefingList).toHaveBeenCalled())
    expect(vi.mocked(api.setBriefingList).mock.calls[0][0]).toEqual({ site_id: null, user_ids: ['u2'], role_ids: [8] })
  })

  it('lists a schedule with when it sends and what it sent, and pauses it', async () => {
    render(<DeliveryTab />)
    const row = await screen.findByTestId('schedule-row')
    expect(row).toHaveTextContent('Door events')
    expect(row).toHaveTextContent('Every Monday at 07:00, for the week before')
    expect(row).toHaveTextContent('3 sent, 1 left out')
    fireEvent.click(within(row).getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(api.changeSchedule).toHaveBeenCalledWith('x1', { is_active: false }))
  })

  it('makes a schedule with the report, its timing and who it goes to', async () => {
    render(<DeliveryTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'A schedule' }))
    const dialog = await screen.findByRole('dialog')
    const save = within(dialog).getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    fireEvent.mouseDown(within(dialog).getByLabelText('Report'))
    fireEvent.click(await screen.findByRole('option', { name: 'Door events' }))
    fireEvent.mouseDown(within(dialog).getByLabelText('How often'))
    fireEvent.click(await screen.findByRole('option', { name: 'Every month' }))
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Admin' }))
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(api.makeSchedule).toHaveBeenCalled())
    expect(vi.mocked(api.makeSchedule).mock.calls[0][0]).toEqual({
      report_key: 'access', site_id: null, frequency: 'MONTHLY', hour: 7, weekday: null, month_day: 1, user_ids: [],
      role_ids: [2] })
  })

  it('says person by person what became of a briefing’s email', async () => {
    vi.mocked(api.getBriefingEmails).mockResolvedValue({ emails: [
      { id: 'e1', user_id: 'u1', name: 'Tan Wei Ming', email: 'tan@x.test', status: 'SENT', attempts: 1,
        sent_at: '2026-11-04T01:00:00Z', last_error: null, created_at: '2026-11-04T00:59:00Z', period_start: null,
        period_end: null, rows_sent: null, revised: false, subject: 'Daily briefing' },
      { id: 'e2', user_id: 'u3', name: 'Ali Hassan', email: null, status: 'LEFT_OUT', attempts: 0, sent_at: null,
        last_error: 'Not shown Factory B', created_at: '2026-11-04T00:59:00Z', period_start: null, period_end: null,
        rows_sent: null, revised: false, subject: 'Daily briefing' },
    ] })
    render(<BriefingEmails briefingId="b1" />)
    const rows = await screen.findAllByTestId('email-row')
    expect(rows[0]).toHaveTextContent('Sent')
    expect(rows[1]).toHaveTextContent('Left out')
    expect(rows[1]).toHaveTextContent('Not shown Factory B')
  })
})
