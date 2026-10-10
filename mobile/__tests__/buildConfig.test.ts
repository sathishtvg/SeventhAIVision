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
const fs = require('fs') as { readFileSync: (file: string, encoding: 'utf8') => string }
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

  it('sends Expo\'s build service the phone app and nothing else of the repository', () => {
    const ignore = fs.readFileSync('../.easignore', 'utf8').split(/\r?\n/)
      .filter((line: string) => line.trim() && !line.startsWith('#'))
    expect(ignore.slice(0, 2)).toEqual(['/*', '!/mobile/'])
    expect(ignore).toEqual(expect.arrayContaining(['/mobile/node_modules/', '/mobile/.expo/', '/mobile/android/', '/mobile/ios/']))
    // The Firebase file a build needs is not ruled out here: it is kept out of git by not being committed.
    expect(ignore.join('\n')).not.toMatch(/google-services/)
    expect(app.expo.android).toMatchObject({ package: 'ai.seventh.vision', googleServicesFile: './google-services.json' })
  })
})
