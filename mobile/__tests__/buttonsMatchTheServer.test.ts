/**
 * A button is shown only to people the server will let use it.
 *
 * 26 October 2026: the owner's phone said "Failed to start patrol session" - the server refused a guard the patrol
 * the phone offered. An audit of every call the phone makes against the guard role found more buttons like it. The
 * owner chose what guards may do (backend migration 0169); each of these buttons is now shown only with the right
 * permission, and the emergency "Send" tab only to the roles that hold broadcast:send.
 */
// The app is not typed for Node, and this test reads source files: Node's reader, by what it is used for.
const fs = require('fs') as { readFileSync: (file: string, encoding: 'utf8') => string }

const screen = (name: string) => fs.readFileSync(`src/screens/${name}.tsx`, 'utf8')

test('pre-registering a visitor needs visitor:register', () => {
  expect(screen('VisitorsScreen')).toMatch(/can\('visitor:register'\) && \(\s*<Pressable style=\{styles\.addBtn\}/)
})

test('a note or a status change on an incident needs incident:update', () => {
  const src = screen('IncidentDetailScreen')
  expect(src).toContain("{nextStatus && can('incident:update') && (")
  expect(src).toMatch(/can\('incident:update'\) && \(\s*<Card style=\{styles\.gap\}>\s*<Text style=\{styles\.sectionTitle\}>Add Note/)
})

test("the shift handover is the guard's own, or needs handover:create", () => {
  expect(screen('ShiftScreen')).toMatch(
    /\(s\.guard_user_id === userId \|\| can\('handover:create'\)\) && \(\s*<Pressable[\s\S]{0,600}?>Handover</,
  )
})

test('the emergency Send tab is only for the roles that send broadcasts', () => {
  const src = screen('EmergencyScreen')
  expect(src).toContain('const canSend = user && [1, 2, 3, 8].includes(user.roleId)')
  expect(src).toContain("...(canSend ? [{ key: 'send' as const, label: 'Send' }] : [])")
})
