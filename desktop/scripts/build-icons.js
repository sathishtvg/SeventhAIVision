'use strict'
/**
 * Generates assets/tray-icon.png (32x32) and assets/icon.ico (16, 32, 48 px)
 * using only Node.js built-ins — no external packages needed.
 *
 * Run: node scripts/build-icons.js
 */
const zlib = require('zlib')
const fs   = require('fs')
const path = require('path')

// ── PNG encoder (pure Node.js) ───────────────────────────────────────────────

function crc32(buf) {
  if (!crc32._table) {
    crc32._table = new Uint32Array(256)
    for (let i = 0; i < 256; i++) {
      let c = i
      for (let j = 0; j < 8; j++) c = (c & 1) ? 0xedb88320 ^ (c >>> 1) : c >>> 1
      crc32._table[i] = c
    }
  }
  let c = 0xffffffff
  for (let i = 0; i < buf.length; i++) c = crc32._table[(c ^ buf[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function pngChunk(type, data) {
  const len  = Buffer.alloc(4);  len.writeUInt32BE(data.length, 0)
  const t    = Buffer.from(type, 'ascii')
  const crc  = Buffer.alloc(4);  crc.writeUInt32BE(crc32(Buffer.concat([t, data])), 0)
  return Buffer.concat([len, t, data, crc])
}

function makePNG(pixels, size) {
  const sig  = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4)
  ihdr[8] = 8; ihdr[9] = 6  // 8-bit depth, RGBA

  // One filter byte (None) per scanline
  const raw = Buffer.alloc(size * (size * 4 + 1))
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0
    pixels.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4)
  }

  return Buffer.concat([
    sig,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    pngChunk('IEND', Buffer.alloc(0)),
  ])
}

// ── Icon drawing ─────────────────────────────────────────────────────────────

function drawShieldIcon(size) {
  const buf = Buffer.alloc(size * size * 4)

  const setPixel = (x, y, r, g, b, a = 255) => {
    if (x < 0 || x >= size || y < 0 || y >= size) return
    const i = (y * size + x) * 4
    const fa = a / 255
    buf[i]   = Math.round(buf[i]   * (1 - fa) + r * fa)
    buf[i+1] = Math.round(buf[i+1] * (1 - fa) + g * fa)
    buf[i+2] = Math.round(buf[i+2] * (1 - fa) + b * fa)
    buf[i+3] = Math.min(255, buf[i+3] + a)
  }

  // Background: #020617 (OLED dark)
  for (let i = 0; i < size * size; i++) {
    buf[i*4]=2; buf[i*4+1]=6; buf[i*4+2]=23; buf[i*4+3]=255
  }

  const pad = Math.max(1, Math.round(size * 0.10))
  const cx  = size / 2
  const cy  = size / 2

  // ── Shield body (violet #6C63FF) ────────────────────────────────────────
  for (let y = pad; y < size - pad; y++) {
    for (let x = pad; x < size - pad; x++) {
      const nx = (x - cx) / (size * 0.40)   // normalised -1..1
      const ny = (y - cy) / (size * 0.44)

      let inShield = false
      const r = 0.28  // corner radius

      if (ny <= 0) {
        // Upper half: rectangle with rounded top corners
        const ax = Math.abs(nx)
        if (ax <= 0.78 && ny >= -1.0) {
          if (ax > 0.78 - r && ny < -1.0 + r) {
            const dx = ax - (0.78 - r), dy = ny - (-1.0 + r)
            inShield = Math.sqrt(dx*dx + dy*dy) <= r
          } else {
            inShield = true
          }
        }
      } else {
        // Lower half: taper to point
        const halfW = 0.78 * Math.max(0, 1 - ny / 1.05)
        inShield = Math.abs(nx) <= halfW
      }

      if (inShield) setPixel(x, y, 108, 99, 255)
    }
  }

  // ── Checkmark (teal #00D9C0) ────────────────────────────────────────────
  const cs = size * 0.20          // checkmark scale
  const ox = cx - cs * 0.05      // centre-x offset
  const oy = cy + cs * 0.05      // centre-y offset
  const thick = Math.max(1, Math.round(size * 0.048))

  const drawDot = (x, y) => {
    for (let dy = -thick; dy <= thick; dy++)
      for (let dx = -thick; dx <= thick; dx++)
        if (dx*dx + dy*dy <= thick*thick)
          setPixel(Math.round(x+dx), Math.round(y+dy), 0, 217, 192)
  }

  const steps = size * 3
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    let px, py
    if (t < 0.38) {
      // Left (down) arm
      px = ox - cs * 0.46 + cs * 0.44 * (t / 0.38)
      py = oy - cs * 0.02 + cs * 0.44 * (t / 0.38)
    } else {
      // Right (up) arm
      px = ox - cs * 0.02 + cs * 0.80 * ((t - 0.38) / 0.62)
      py = oy + cs * 0.42 - cs * 0.88 * ((t - 0.38) / 0.62)
    }
    drawDot(px, py)
  }

  return buf
}

// ── ICO builder (embeds PNGs directly) ──────────────────────────────────────

function makeICO(pngBufs) {
  const count   = pngBufs.length
  const dirSize = 6 + count * 16
  const hdr     = Buffer.alloc(6)
  hdr.writeUInt16LE(0, 0); hdr.writeUInt16LE(1, 2); hdr.writeUInt16LE(count, 4)

  const entries = []
  let offset = dirSize
  for (const png of pngBufs) {
    const w = png.readUInt32BE(16)
    const h = png.readUInt32BE(20)
    const e = Buffer.alloc(16)
    e[0] = w >= 256 ? 0 : w
    e[1] = h >= 256 ? 0 : h
    e[2] = 0; e[3] = 0
    e.writeUInt16LE(1,  4)
    e.writeUInt16LE(32, 6)
    e.writeUInt32LE(png.length, 8)
    e.writeUInt32LE(offset, 12)
    entries.push(e)
    offset += png.length
  }

  return Buffer.concat([hdr, ...entries, ...pngBufs])
}

// ── Main ─────────────────────────────────────────────────────────────────────

const assetsDir = path.join(__dirname, '..', 'assets')
fs.mkdirSync(assetsDir, { recursive: true })

console.log('Generating Seventh AI Vision icon assets...')

const sizes   = [16, 32, 48, 256]
const pngs    = sizes.map(s => { const p = drawShieldIcon(s); return makePNG(p, s) })

// Tray icon — 32×32 PNG (index 1 in sizes array)
fs.writeFileSync(path.join(assetsDir, 'tray-icon.png'), pngs[1])
console.log('  ✓ assets/tray-icon.png  (32\xd732)')

// App icon — multi-resolution .ico (must include 256 for electron-builder)
fs.writeFileSync(path.join(assetsDir, 'icon.ico'), makeICO(pngs))
console.log('  ✓ assets/icon.ico       (16, 32, 48, 256 px)')

// 256-px PNG for installer banner / future use
fs.writeFileSync(path.join(assetsDir, 'icon-256.png'), pngs[3])
console.log('  ✓ assets/icon-256.png   (256\xd7256)')

console.log('Done.')
