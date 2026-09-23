/**
 * Who sees which screen on the phone.
 *
 * THE MENU HAD NO GATE AT ALL. Every role got all eight tabs and all twenty-six
 * More entries, so a security guard's phone offered Emergency Broadcast,
 * Notification Logs, AI thresholds in Settings, and "Live Attendance — who is
 * on duty and who is missing". The server refused the ones it should, but a
 * menu full of doors that open onto an error is its own problem: the guard
 * cannot tell the feature they are not allowed to use from the one that is
 * broken, and the things they actually need are eleven rows down.
 *
 * TWO GATES, BECAUSE ONE IS NOT ENOUGH.
 *
 * `permission` is the objective one: the code the screen's own endpoint
 * requires. If the user does not hold it the server answers 403, so showing the
 * row only promises something the app cannot deliver.
 *
 * `audience` is the judgement, and it is needed because permissions alone do
 * not answer the question that prompted this. A guard holds `attendance:read`,
 * and Live Attendance requires exactly that -- so no permission gate can hide
 * it, yet a roll-call of who is missing is a supervisor's job, not the job of
 * the person standing at the gate. 'ops' means an oversight or configuration
 * screen; it is hidden from field roles and shown to everyone else.
 *
 * FIELD ROLES ARE {4, 5}, taken from the backend rather than invented here:
 * attendance.py and leave.py both use that set to mean "sees only their own
 * record". A second definition living here would drift from it.
 *
 * FAIL OPEN ON PERMISSIONS, NOT ON AUDIENCE. Permissions arrive from
 * /auth/me/permissions a moment after sign-in; until they do, every permission
 * check passes, because hiding a guard's own tools on a slow or offline
 * connection is worse than briefly showing one row too many -- and that is
 * exactly today's behaviour, so nothing regresses while it loads. The audience
 * gate needs no network: the role is in the token already.
 */

/** Roles that see only their own record — Operator and Security Guard.
 *  Mirrors _GUARD_ROLES in backend attendance.py and leave.py. */
export const FIELD_ROLES = new Set([4, 5])

export type Audience = 'field' | 'ops'

export interface Gated {
  /** Permission code the screen's endpoint requires, if it requires one. */
  permission?: string
  /** 'ops' screens are oversight or configuration and are hidden from field
   *  roles. Anything without this is shown to everyone who holds the
   *  permission. */
  audience?: Audience
}

export function isFieldRole(roleId: number | null | undefined): boolean {
  return roleId != null && FIELD_ROLES.has(roleId)
}

/**
 * Whether this role, holding these permissions, should see this entry.
 *
 * `permissions` is null until the fetch lands; see the note above on why that
 * passes rather than hides.
 */
export function canSee(
  item: Gated,
  permissions: string[] | null,
  roleId: number | null | undefined,
): boolean {
  if (item.audience === 'ops' && isFieldRole(roleId)) return false
  if (!item.permission) return true
  if (permissions === null) return true
  return permissions.includes(item.permission)
}

/** Filter a list of gated entries, keeping order. */
export function visible<T extends Gated>(
  items: T[],
  permissions: string[] | null,
  roleId: number | null | undefined,
): T[] {
  return items.filter((i) => canSee(i, permissions, roleId))
}
