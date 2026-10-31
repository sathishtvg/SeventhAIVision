import { buildDay, desktopVersion, versionLine } from './appVersion'

const WINDOWS = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'seventh-ai-vision-desktop/1.0.13 Chrome/138.0.0.0 Electron/37.2.0 Safari/537.36'
const BROWSER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'

// The stamp itself is put in by vite.config.ts when the console is built, not under the test runner (vitest.config.ts).
describe('the version at the foot of the sidebar', () => {
  it('says the Windows app\'s own version inside the desktop app, and the web build anywhere', () => {
    const build = { commit: '0219501', date: '2026-10-31' }
    expect(versionLine(build, WINDOWS)).toBe('Windows 1.0.13 · build 0219501 · 31 Oct 2026')
    expect(versionLine(build, BROWSER)).toBe('Web build 0219501 · 31 Oct 2026')
    expect(desktopVersion(BROWSER)).toBeNull()
  })

  it('writes the day the same in every locale, and says when no commit was stamped', () => {
    expect(buildDay('2026-01-05')).toBe('5 Jan 2026')
    expect(versionLine({ commit: '', date: '2026-10-31' }, BROWSER)).toBe('Web build not stamped · 31 Oct 2026')
  })
})
