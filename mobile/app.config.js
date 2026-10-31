/**
 * The phone app's configuration as Expo reads it: app.json, with one thing
 * decided when a build is made.
 *
 * PLAIN HTTP IS REFUSED UNLESS A BUILD ASKS FOR IT. Android refuses
 * unencrypted traffic in a release build, and that is right for what guards
 * are given: their server is reached over https. A build made to be tried
 * against a development machine on the same network has no certificate to
 * offer, so the `lan-test` profile in eas.json sets APP_ALLOW_HTTP=1 and this
 * allows http for that build. No other profile sets it, and a build made
 * without it is exactly what app.json describes.
 */
const BUILD_PROPERTIES = 'expo-build-properties'

/**
 * WHICH BUILD THIS IS, stamped into every build as `extra.build` and shown at the foot of the More menu and the
 * sign-in screen (src/lib/appVersion.ts): the app's version stays 1.0.0, so the build is told apart by the commit
 * Expo's build server was given (EAS_BUILD_GIT_COMMIT_HASH; git where there is one) and the day. Nothing native.
 */
function buildStamp() {
  let commit = process.env.EAS_BUILD_GIT_COMMIT_HASH || ''
  if (!commit) {
    try {
      commit = require('child_process').execSync('git rev-parse --short=7 HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
        .toString().trim()
    } catch {
      commit = ''
    }
  }
  return { commit: commit.slice(0, 7), date: new Date().toISOString().slice(0, 10) }
}

module.exports = ({ config }) => {
  const stamped = { ...config, extra: { ...(config.extra ?? {}), build: buildStamp() } }
  if (process.env.APP_ALLOW_HTTP !== '1') return stamped
  const others = (config.plugins ?? []).filter((plugin) => (Array.isArray(plugin) ? plugin[0] : plugin) !== BUILD_PROPERTIES)
  return {
    ...stamped,
    plugins: [...others, [BUILD_PROPERTIES, { android: { usesCleartextTraffic: true } }]],
  }
}
