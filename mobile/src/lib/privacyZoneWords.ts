/**
 * What a privacy zone does, in the words the phone uses - the same words the
 * web uses (frontend/src/components/privacy/privacyZoneWords.ts), held to them
 * by a test. A zone cannot be undone for what is recorded after it, so what it
 * does is said before it is drawn, wherever it is drawn.
 */

/** What a zone does, said before one is drawn. */
export const WHAT_A_ZONE_DOES =
  'Within about ten seconds, what is under a zone is blacked out of the live view, of what the AI is given, of '
  + 'recordings and of the images a patrol keeps. What is recorded from then on cannot be unmasked. Footage recorded '
  + 'before the zone was drawn is not changed.'

/** What deleting a zone changes, said before it is deleted. */
export const WHAT_DELETING_DOES =
  'Within about ten seconds the camera is shown, analysed and recorded without it. What was recorded while the zone '
  + 'was there stays masked.'

/** Shown on a live picture that has a zone, so that a black block is read as meant and not as a fault. */
export const MASKED_LABEL = 'Privacy zone'

/** Who draws and deletes a privacy zone. */
export const PRIVACY_PERMISSION = 'privacy:manage'

/**
 * Whether this person is known to manage privacy. Unlike the menu, which shows
 * a row while the permissions are still loading, this waits until they are
 * known: what it opens masks a camera for good.
 */
export function managesPrivacy(permissions: string[] | null | undefined): boolean {
  return !!permissions && permissions.includes(PRIVACY_PERMISSION)
}

/** A zone's line in the list: who drew it, and when. */
export function drawnLine(zone: { created_by_name: string | null; created_at: string }): string {
  const when = new Date(zone.created_at).toLocaleString([], {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
  return zone.created_by_name ? `Drawn by ${zone.created_by_name} · ${when}` : `Drawn ${when}`
}
