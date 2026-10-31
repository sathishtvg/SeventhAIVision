/**
 * Which build of the phone app this is, shown at the foot of the More menu and of the sign-in screen, so the owner
 * can tell an updated app from an old one. The version number stays 1.0.0; the build stamp (app.config.js) is what
 * changes from build to build.
 */
// The app is not typed for Node, and this test reads two screens' source: Node's reader, by what it is used for.
const fs = require('fs') as { readFileSync: (file: string, encoding: 'utf8') => string }

import { buildDay, versionLine } from '@/lib/appVersion'

describe('the version the app shows', () => {
  it('says the version, the build and the day it was built', () => {
    expect(versionLine({ version: '1.0.0', extra: { build: { commit: '0219501', date: '2026-10-31' } } }))
      .toBe('Version 1.0.0 · build 0219501 · 31 Oct 2026')
  })

  it('says so when a build was not stamped, and writes the day the same in every locale', () => {
    expect(versionLine({ version: '1.0.0', extra: {} })).toBe('Version 1.0.0 · build not stamped')
    expect(buildDay('2026-01-05')).toBe('5 Jan 2026')
  })

  it('is at the foot of the More menu and of the sign-in screen', () => {
    for (const screen of ['src/screens/MoreMenuScreen.tsx', 'src/screens/LoginScreen.tsx']) {
      expect(fs.readFileSync(screen, 'utf8')).toContain('testID="app-version">{versionLine()}</Text>')
    }
  })
})
