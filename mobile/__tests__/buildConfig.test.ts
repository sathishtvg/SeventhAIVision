/**
 * How a build of the phone app is configured.
 *
 * WHY THIS MATTERS
 *   Android refuses plain http in a release build, and for what guards are
 *   given that is right. One profile exists to try the app against a
 *   development machine on the same network, and it alone may allow http. If
 *   that leaked into the build a customer is given, the app would talk to its
 *   server unencrypted and nothing would look wrong.
 */
// The app is not typed for Node, and this is the one test that reads a file: Node's reader, by what it is used for.
// The tests are run from mobile/, so the repository's root is one folder up.
const fs = require('fs') as { readFileSync: (file: string, encoding: 'utf8') => string; existsSync: (file: string) => boolean }
const bytesOf = require('fs').readFileSync as (file: string) => { readUInt32BE: (at: number) => number; [at: number]: number }
// The package Expo's build tool reads .easignore with. It is here because Expo's own tools bring it.
const makeIgnore = require('ignore') as () => { add: (rules: string) => { ignores: (path: string) => boolean } }
const dynamic = require('../app.config.js') as (ctx: { config: Record<string, unknown> }) => { plugins: unknown[] }
const app = require('../app.json') as { expo: { plugins: unknown[]; android: { package: string; googleServicesFile: string } } }
const eas = require('../eas.json') as { build: Record<string, { extends?: string; distribution?: string; env?: Record<string, string>
                                                                  android?: { buildType?: string } }> }

const nameOf = (plugin: unknown) => (Array.isArray(plugin) ? plugin[0] : plugin) as string
const configured = (allowHttp?: string) => {
  const before = process.env.APP_ALLOW_HTTP
  if (allowHttp === undefined) delete process.env.APP_ALLOW_HTTP
  else process.env.APP_ALLOW_HTTP = allowHttp
  try {
    return dynamic({ config: JSON.parse(JSON.stringify(app.expo)) })
  } finally {
    if (before === undefined) delete process.env.APP_ALLOW_HTTP
    else process.env.APP_ALLOW_HTTP = before
  }
}

describe('a build of the phone app', () => {
  it('is exactly what app.json describes unless a build asks for http', () => {
    expect(configured()).toEqual(app.expo)
    expect(configured('0')).toEqual(app.expo)
    expect(configured('true')).toEqual(app.expo)
    // As app.json has it, the build-properties plugin sets nothing: plain http stays refused.
    expect(app.expo.plugins.filter((p) => nameOf(p) === 'expo-build-properties')).toEqual(['expo-build-properties'])
  })

  it('allows plain http only when APP_ALLOW_HTTP is 1, and changes nothing else', () => {
    const lan = configured('1')
    expect(lan.plugins.filter((p) => nameOf(p) === 'expo-build-properties')).toEqual([
      ['expo-build-properties', { android: { usesCleartextTraffic: true } }]])
    // Every other plugin is there, as it was and in its order.
    const others = (plugins: unknown[]) => plugins.filter((p) => nameOf(p) !== 'expo-build-properties')
    expect(others(lan.plugins)).toEqual(others(app.expo.plugins))
    expect({ ...lan, plugins: undefined }).toEqual({ ...app.expo, plugins: undefined })
  })

  it('has one profile that asks for it: an APK for testing on the same network, and not the one for the store', () => {
    const asking = Object.entries(eas.build).filter(([, profile]) => profile.env?.APP_ALLOW_HTTP !== undefined).map(([name]) => name)
    expect(asking).toEqual(['lan-test'])
    const lan = eas.build['lan-test']
    expect(lan.extends).toBe('preview')
    expect(eas.build.preview).toEqual({ distribution: 'internal', android: { buildType: 'apk' } })
    expect(lan.env).toEqual({ APP_ALLOW_HTTP: '1', EXPO_PUBLIC_API_URL: expect.stringMatching(/^http:\/\/(192\.168|10)\.\d+\.\d+(\.\d+)?:8000$/) })
    // The store's build is an app bundle with no address built in and no http.
    expect(eas.build.production).toEqual({ distribution: 'store', android: { buildType: 'app-bundle' } })
  })

  it('names no file that is not there, but the one that is deliberately kept out of the repository', () => {
    // app.json named ./assets/notification-icon.png from the start, and there was no such file. A build stops on
    // that (10 October 2026: "ENOENT ... notification-icon.png", thirty seconds into the first cloud build).
    const named: string[] = []
    const walk = (value: unknown) => {
      if (typeof value === 'string') { if (value.startsWith('./')) named.push(value) }
      else if (Array.isArray(value)) value.forEach(walk)
      else if (value && typeof value === 'object') Object.values(value).forEach(walk)
    }
    walk(app.expo)
    expect(named.sort()).toEqual(['./assets/adaptive-icon.png', './assets/icon.png', './assets/notification-icon.png',
      './google-services.json'])
    for (const file of named.filter((name) => name !== './google-services.json')) {
      expect([file, fs.existsSync(file)]).toEqual([file, true])
    }
    // Android draws the small icon as a silhouette and tints it: white on nothing, 96 pixels square.
    const icon = bytesOf('./assets/notification-icon.png')
    expect([icon[1], icon[2], icon[3]].map((code) => String.fromCharCode(code)).join('')).toBe('PNG')
    expect({ width: icon.readUInt32BE(16), height: icon.readUInt32BE(20), depth: icon[24], colourAndAlpha: icon[25] === 6 })
      .toEqual({ width: 96, height: 96, depth: 8, colourAndAlpha: true })
    const notifications = app.expo.plugins.find((p) => nameOf(p) === 'expo-notifications') as [string, { icon: string; color: string }]
    expect(notifications[1]).toMatchObject({ icon: './assets/notification-icon.png', color: '#6C63FF' })
  })

  it('sends Expo\'s build service the phone app and nothing else of the repository', () => {
    const text = fs.readFileSync('../.easignore', 'utf8')
    const rules = text.split(/\r?\n/).filter((line: string) => line.trim() && !line.startsWith('#'))
    expect(rules.slice(0, 2)).toEqual(['/*', '!/mobile'])
    // The Firebase file a build needs is not ruled out here: it is kept out of git by not being committed.
    expect(rules.join('\n')).not.toMatch(/google-services/)
    expect(app.expo.android).toMatchObject({ package: 'ai.seventh.vision', googleServicesFile: './google-services.json' })

    // What matters is what the build tool makes of the rules, so it is asked the way the tool asks: with the
    // package the tool uses, its two rules of its own first, and each folder by its name alone - never with a
    // slash after it. Written with one (`!/mobile/`), the rule that keeps the phone app never matched, and
    // what would have been uploaded was nothing (10 October 2026).
    expect(rules.filter((rule: string) => rule.endsWith('/'))).toEqual([])
    const own = makeIgnore().add('\n.git\nnode_modules\n')
    const ours = makeIgnore().add(text)
    const sent = (path: string) => {
      // The tool walks down from the top: a file is reached only if every folder above it was let through.
      const parts = path.split('/')
      return parts.every((_, n) => {
        const upTo = parts.slice(0, n + 1).join('/')
        return !own.ignores(upTo) && !ours.ignores(upTo)
      })
    }
    for (const needed of ['mobile', 'mobile/package.json', 'mobile/package-lock.json', 'mobile/app.json', 'mobile/app.config.js',
      'mobile/eas.json', 'mobile/App.tsx', 'mobile/google-services.json', 'mobile/assets/icon.png',
      'mobile/src/screens/LoginScreen.tsx', 'mobile/src/api/client.ts', 'mobile/babel.config.js', 'mobile/tsconfig.json']) {
      expect([needed, sent(needed)]).toEqual([needed, true])
    }
    for (const kept of ['backend', 'backend/app/main.py', 'frontend/src/App.tsx', 'desktop/package.json', 'docker/docker-compose.yml',
      'docs/DEMO-SCRIPT.md', 'backups/dev.dump', 'mobile-screenshots/one.png', '.git', '.github/workflows/ci.yml', '.claude',
      '.env', 'README.md', '.easignore',
      'mobile/node_modules', 'mobile/node_modules/expo/package.json', 'mobile/.expo', 'mobile/.expo/devices.json',
      'mobile/android', 'mobile/android/build.gradle', 'mobile/ios', 'mobile/dist', 'mobile/coverage',
      'mobile/.git.bak-nested-repo', 'mobile/.git.bak-nested-repo/config', 'mobile/.env', 'mobile/.env.local',
      'mobile/release.keystore', 'mobile/upload.jks']) {
      expect([kept, sent(kept)]).toEqual([kept, false])
    }
  })
})
