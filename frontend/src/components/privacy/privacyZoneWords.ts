/**
 * What a privacy zone does and does not do, in the words the screen uses. Kept
 * apart from the panel so that what is said before a zone is drawn and what
 * the documents say can be held to each other by a test.
 */

/** What a zone does, said before one is drawn. */
export const WHAT_A_ZONE_DOES =
  'Within about ten seconds, what is under a zone is blacked out of the live view, of what the AI is given, of '
  + 'recordings and of the images a patrol keeps. What is recorded from then on cannot be unmasked. Footage recorded '
  + 'before the zone was drawn is not changed.'

/** What a zone does not do. */
export const ZONE_LIMITS = [
  'A zone is fixed to the picture, not to the scene: if the camera is turned, it no longer covers the same place.',
  'A camera with a zone has no HLS live view. It is shown through the standard view, which is masked.',
  'The AI sees nothing inside a zone, so a restricted zone or a crowd zone that overlaps one is blind there.',
  'Only what passes through this platform is masked. A recorder at the site that records the camera itself is not.',
]

/** What deleting a zone changes, said before it is deleted. */
export const WHAT_DELETING_DOES =
  'Within about ten seconds the camera is shown, analysed and recorded without it. What was recorded while the zone '
  + 'was there stays masked.'

/** Shown on a live picture that has a zone, so that a black block is read as meant and not as a fault. */
export const MASKED_LABEL = 'Privacy zone'
