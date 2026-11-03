/**
 * Singaporean, PR or foreigner, and what CPF still needs (3 November 2026). Kept apart from CpfPreviewCard so
 * that file exports only a component.
 */
export type Residency = 'citizen' | 'pr' | 'foreigner'

/** Singaporean, PR or foreigner, from the work pass type the account keeps. */
export function residencyOf(workPassType: string | null | undefined): Residency | '' {
  if (workPassType === 'citizen') return 'citizen'
  if (workPassType === 'pr') return 'pr'
  if (workPassType === 'ep' || workPassType === 'sp' || workPassType === 'wp') return 'foreigner'
  return ''
}

/** What is still needed before CPF can be worked out, or null when nothing is. */
export function missingForCpf(p: {
  workPassType: string; dateOfBirth: string; prSince: string; monthlySalary: string
}): string | null {
  const residency = residencyOf(p.workPassType)
  if (!residency) return 'Choose the residency to see the CPF.'
  if (residency === 'foreigner') return null
  const missing = [
    !p.dateOfBirth && 'date of birth',
    residency === 'pr' && !p.prSince && "'PR since' date",
    !(Number(p.monthlySalary) > 0) && 'monthly salary',
  ].filter(Boolean)
  return missing.length ? `Enter the ${missing.join(', ')} to see the CPF.` : null
}
