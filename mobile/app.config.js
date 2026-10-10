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

module.exports = ({ config }) => {
  if (process.env.APP_ALLOW_HTTP !== '1') return config
  const others = (config.plugins ?? []).filter((plugin) => (Array.isArray(plugin) ? plugin[0] : plugin) !== BUILD_PROPERTIES)
  return {
    ...config,
    plugins: [...others, [BUILD_PROPERTIES, { android: { usesCleartextTraffic: true } }]],
  }
}
