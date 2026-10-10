/**
 * Makes assets/notification-icon.png from assets/icon.png.
 *
 *   node scripts/make-notification-icon.js      (run from mobile/)
 *
 * WHY THERE IS SUCH A FILE
 *   app.json has named ./assets/notification-icon.png as the notification
 *   icon since the project began, and the file was never there. Nothing
 *   noticed until a build was made on SDK 57, whose notifications plugin
 *   stops the build when the icon it is given cannot be read (10 October
 *   2026). __tests__/buildConfig.test.ts now holds that every file app.json
 *   names exists.
 *
 * WHAT ANDROID WANTS
 *   The small icon in the status bar is drawn as a silhouette: Android keeps
 *   the shape and throws the colours away, then tints it (app.json gives the
 *   tint, the brand's #6C63FF). So the picture is white on nothing, 96 pixels
 *   square, which the plugin scales down for each screen density. The app's
 *   own icon given as it is would be a white square.
 *
 * WHAT IT IS
 *   The "7" of the app's icon and nothing else: every pixel of icon.png is
 *   kept by how white it is, so the disc and the background fall away and the
 *   figure's soft edge is kept. It is then fitted to the square with a margin.
 *   Run it again if the app's icon changes.
 *
 * jimp-compact is what Expo's own tools read pictures with; it is installed
 * with them.
 */
const path = require('path')
const Jimp = require('jimp-compact')

const SIZE = 96
const MARGIN = 12
// In icon.png the figure is white (255), the disc behind it #6C63FF (red 108, green 99).
// Whiteness runs from the disc's brighter channel to full white.
const FROM = 112
const TO = 255

async function main() {
  const assets = path.join(__dirname, '..', 'assets')
  const icon = await Jimp.read(path.join(assets, 'icon.png'))
  const { width, height, data } = icon.bitmap

  let left = width, top = height, right = -1, bottom = -1
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const at = (y * width + x) * 4
      const white = Math.min(data[at], data[at + 1])
      const alpha = Math.max(0, Math.min(1, (white - FROM) / (TO - FROM)))
      data[at] = data[at + 1] = data[at + 2] = 255
      data[at + 3] = Math.round(alpha * 255)
      if (alpha > 0.5) {
        if (x < left) left = x
        if (x > right) right = x
        if (y < top) top = y
        if (y > bottom) bottom = y
      }
    }
  }
  if (right < 0) throw new Error('icon.png has nothing white in it to make an icon from')

  const figure = icon.crop(left, top, right - left + 1, bottom - top + 1)
  figure.scaleToFit(SIZE - 2 * MARGIN, SIZE - 2 * MARGIN, Jimp.RESIZE_BICUBIC)
  // Scaling can leave a faint colour in a transparent pixel. It is white throughout: only how solid it is varies.
  const scaled = figure.bitmap
  for (let at = 0; at < scaled.data.length; at += 4) scaled.data[at] = scaled.data[at + 1] = scaled.data[at + 2] = 255

  const out = new Jimp(SIZE, SIZE, 0x00000000)
  out.composite(figure, Math.round((SIZE - scaled.width) / 2), Math.round((SIZE - scaled.height) / 2))
  const file = path.join(assets, 'notification-icon.png')
  await out.writeAsync(file)
  console.log(`${file}: ${SIZE}x${SIZE}, the figure ${scaled.width}x${scaled.height}`)
}

main().catch((err) => { console.error(err.message); process.exit(1) })
