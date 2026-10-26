/** Labels and the correction check for the IC scan dialog (IcScanDialog.tsx). */
import type { IcFields } from '@/api/aiAssistant'

export const IC_LABELS: Record<keyof IcFields, string> = {
  nric_fin: 'NRIC/FIN',
  full_name: 'Full name',
  date_of_birth: 'Date of birth (YYYY-MM-DD)',
  sex: 'Sex (M or F)',
  race: 'Race',
  nationality: 'Nationality',
  country_of_birth: 'Country of birth',
  address: 'Address',
}

export const IC_ORDER = Object.keys(IC_LABELS) as Array<keyof IcFields>

/** Whether the person changed anything read from the card, so a correction is sent only when there is one. */
export function changed(read: IcFields, now: IcFields): boolean {
  return IC_ORDER.some((k) => (read[k] ?? '').trim() !== (now[k] ?? '').trim())
}
