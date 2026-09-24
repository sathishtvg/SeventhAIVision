/**
 * The Content-Security-Policy served with every app:// response.
 *
 * WHAT IT FIXES. Electron warned on every launch: "This renderer process has
 * either no Content-Security-Policy set or a policy with unsafe-eval". Without
 * one, any script that found its way into the bundle could send whatever it
 * read to wherever it liked — and this renderer holds a signed-in session for a
 * security system, with camera feeds and evidence behind it.
 *
 * SCRIPTS ARE ALLOWED BY HASH, NOT BY 'unsafe-inline'. The shipped index.html
 * carries one inline script (the boot splash), and allowing inline script
 * wholesale would give back most of what the policy exists to prevent. The
 * hashes are computed from the file actually being served, at startup, so a
 * rebuild that changes the splash cannot silently blank the window — which a
 * hash hard-coded here eventually would.
 *
 * STYLES DO NEED 'unsafe-inline'. MUI/emotion injects <style> elements at
 * render time; there is no hash for styles that do not exist until then.
 * Stated here rather than left to be discovered: it is the one loose directive.
 *
 * THE ALLOWED ORIGINS ARE THE USER'S OWN SERVER, read from the store, plus
 * Nominatim for the address lookup the map screens genuinely use. Hard-coding
 * localhost would break every real installation, which points at a server on
 * the LAN.
 *
 * IMAGES MAY COME FROM ANY https HOST. A tenant types its own logo URL into
 * Settings and it is rendered in the sidebar, on the login screen and on the
 * client portal; the Demo tenant's points at a stock-photo CDN. There is no
 * list of hosts to write down, because the customer chooses it after the app
 * ships. Narrowing this to 'self' does not protect anything either: an image
 * cannot execute, the URL is already chosen by the tenant's own admin, and the
 * only thing a foreign host learns is that a logo was fetched. Map tiles fall
 * under the same allowance. The server's own origin is still listed separately
 * because a LAN install is often plain http.
 */
const fs = require('fs')
const path = require('path')
const crypto = require('crypto')

/** sha256-base64 of every inline <script> in the served index.html. */
function inlineScriptHashes(distRoot) {
  try {
    const html = fs.readFileSync(path.join(distRoot, 'index.html'), 'utf8')
    const withoutSrc = /<script(?![^>]*\ssrc=)[^>]*>([\s\S]*?)<\/script>/gi
    const hashes = []
    let m
    while ((m = withoutSrc.exec(html)) !== null) {
      // CRLF -> LF before hashing. The browser normalises line endings in a
      // script body before computing its hash; the file on disk here has CRLF
      // because it was written on Windows. Hashing the bytes as they sit gives
      // a hash the browser never produces, and the only symptom is the boot
      // splash silently refusing to run — verified against Chromium's own
      // expected hash rather than assumed.
      const body = m[1].split('\r\n').join('\n')
      hashes.push(`'sha256-${crypto.createHash('sha256').update(body, 'utf8').digest('base64')}'`)
    }
    return hashes.join(' ')
  } catch {
    // No dist yet (running from source before a build). 'self' still loads the
    // bundle; the splash just will not animate, which beats a blank window.
    return ''
  }
}

/** OpenStreetMap's address lookup, used by the site and patrol map screens.
 *  Its tiles are images and so are covered by the https: allowance below. */
const OSM_SEARCH = 'https://nominatim.openstreetmap.org'

// The policy is rebuilt only when its inputs change. protocol.handle runs this
// for every app:// request — each script, stylesheet and icon — and hashing
// index.html that many times is a file read and a sha256 per asset for a string
// that does not change. serverUrl is the only input that moves at runtime, when
// the settings window points the app at a different server.
let cached = { key: null, policy: null }

function buildCsp(serverUrl, distRoot) {
  const api = String(serverUrl || '').replace(/\/+$/, '')
  const key = `${api}|${distRoot}`
  if (cached.key === key) return cached.policy
  // The live feed is a websocket to the same host, which connect-src treats as
  // a separate origin.
  const wsApi = api ? api.replace(/^http/, 'ws') : ''
  const scripts = inlineScriptHashes(distRoot)

  const policy = [
    "default-src 'self'",
    `script-src 'self' ${scripts}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: https: ${api}`,
    `media-src 'self' blob: ${api}`,
    `connect-src 'self' ${api} ${wsApi} ${OSM_SEARCH}`,
    "font-src 'self' data:",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
  ]
    .map((d) => d.replace(/\s+/g, ' ').trim())
    .join('; ')

  cached = { key, policy }
  return policy
}

module.exports = { buildCsp, inlineScriptHashes }
