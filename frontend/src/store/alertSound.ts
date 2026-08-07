/**
 * Control-room audible alerts (Gap 83).
 *
 * Generates siren/beep tones with the Web Audio API — no bundled audio files.
 * Critical → rising-falling two-tone siren (~4s). High → three short beeps.
 * Mute state persists in localStorage so a control-room PC keeps its setting.
 *
 * Browsers block audio until a user gesture; the Electron shell sets
 * autoplayPolicy so the control room gets sound with no interaction.
 */
import { create } from 'zustand'

const SOUND_KEY = 'seventh_ai_sound_enabled'

interface AlertSoundState {
  soundEnabled: boolean
  toggleSound: () => void
}

export const useAlertSoundStore = create<AlertSoundState>((set) => ({
  soundEnabled: localStorage.getItem(SOUND_KEY) !== 'false',
  toggleSound: () =>
    set((s) => {
      const next = !s.soundEnabled
      localStorage.setItem(SOUND_KEY, String(next))
      if (!next) stopAlertSound()
      return { soundEnabled: next }
    }),
}))

/** Pure — which severities are audible. Exported for tests.
 * Medium is included so an operator watching the wall hears that *something*
 * happened; its tone is deliberately a single low blip, clearly distinct from
 * high's triple beep and critical's siren, so severity is identifiable by ear
 * alone without reading the screen. */
export function shouldPlaySound(severity: string, enabled: boolean): boolean {
  return enabled && (severity === 'medium' || severity === 'high' || severity === 'critical')
}

const MODULE_SPOKEN: Record<string, string> = {
  lpr: 'licence plate', face: 'face recognition', intrusion: 'intrusion',
  ppe: 'P P E', crowd: 'crowd density', fire_smoke: 'fire or smoke',
  weapon: 'weapon', behavior: 'suspicious behaviour', tampering: 'camera tampering',
  abandoned: 'abandoned object', fall: 'person fallen',
}

/** Pure — the sentence the operator hears. Exported so the wording is
 * testable without a speech engine. Severity first: it's the word that
 * decides whether they stop what they're doing. */
export function announcementText(
  severity: string, moduleType?: string | null, siteName?: string | null,
): string {
  const parts = [`${severity} alert`]
  const mod = moduleType ? MODULE_SPOKEN[moduleType] ?? moduleType.replace(/_/g, ' ') : null
  if (mod) parts.push(mod)
  if (siteName) parts.push(`at ${siteName}`)
  return parts.join('. ') + '.'
}

/**
 * Speak the alert so an operator facing the camera wall — not the screen —
 * knows what happened and where without looking. Runs after the tone rather
 * than over it, so the siren still does its job of grabbing attention first.
 *
 * Uses the browser's built-in speech synthesis: no audio files to ship, no
 * network round-trip, and it already exists in Electron. Silently does
 * nothing where the API is absent.
 */
export function announceAlert(
  severity: string, moduleType?: string | null, siteName?: string | null,
) {
  const { soundEnabled } = useAlertSoundStore.getState()
  if (!shouldPlaySound(severity, soundEnabled)) return
  const synth = typeof window !== 'undefined' ? window.speechSynthesis : undefined
  if (!synth) return
  try {
    // Critical repeats its siren for ~4s; start speaking once that has had
    // time to register rather than talking over it.
    const delayMs = severity === 'critical' ? 4200 : 900
    window.setTimeout(() => {
      const u = new SpeechSynthesisUtterance(announcementText(severity, moduleType, siteName))
      u.rate = 1.0
      u.volume = 0.9
      // A queued backlog of stale announcements is worse than missing one —
      // during an alert storm the operator wants the latest, not a recital.
      synth.cancel()
      synth.speak(u)
    }, delayMs)
  } catch { /* speech unavailable — the tone already fired */ }
}

let _ctx: AudioContext | null = null
let _activeNodes: { osc: OscillatorNode; gain: GainNode }[] = []

function _audioCtx(): AudioContext | null {
  try {
    if (!_ctx) _ctx = new AudioContext()
    if (_ctx.state === 'suspended') void _ctx.resume()
    return _ctx
  } catch {
    return null // no audio device / very old browser — silently skip
  }
}

function _tone(ctx: AudioContext, startAt: number, duration: number,
               fromHz: number, toHz: number, volume = 0.18) {
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = 'square'
  osc.frequency.setValueAtTime(fromHz, startAt)
  osc.frequency.linearRampToValueAtTime(toHz, startAt + duration)
  gain.gain.setValueAtTime(0, startAt)
  gain.gain.linearRampToValueAtTime(volume, startAt + 0.02)
  gain.gain.setValueAtTime(volume, startAt + duration - 0.03)
  gain.gain.linearRampToValueAtTime(0, startAt + duration)
  osc.connect(gain)
  gain.connect(ctx.destination)
  osc.start(startAt)
  osc.stop(startAt + duration)
  _activeNodes.push({ osc, gain })
  osc.onended = () => {
    _activeNodes = _activeNodes.filter((n) => n.osc !== osc)
  }
}

/** Stop anything currently sounding (used by the mute toggle). */
export function stopAlertSound() {
  for (const { osc, gain } of _activeNodes) {
    try {
      gain.gain.cancelScheduledValues(0)
      gain.gain.value = 0
      osc.stop()
    } catch { /* already stopped */ }
  }
  _activeNodes = []
}

/**
 * Play the tone pattern for a severity. Reads the mute flag itself so
 * callers can invoke unconditionally.
 */
export function playAlertSound(severity: string) {
  const { soundEnabled } = useAlertSoundStore.getState()
  if (!shouldPlaySound(severity, soundEnabled)) return
  const ctx = _audioCtx()
  if (!ctx) return
  const t0 = ctx.currentTime

  if (severity === 'critical') {
    // Two-tone siren: 4 rise/fall sweeps ≈ 4s
    for (let i = 0; i < 4; i++) {
      _tone(ctx, t0 + i * 1.0, 0.5, 620, 1150, 0.2)
      _tone(ctx, t0 + i * 1.0 + 0.5, 0.5, 1150, 620, 0.2)
    }
  } else if (severity === 'high') {
    // High: three short attention beeps
    for (let i = 0; i < 3; i++) {
      _tone(ctx, t0 + i * 0.28, 0.16, 880, 880, 0.16)
    }
  } else {
    // Medium: one low blip — noticeable, but clearly not a "drop everything"
    // sound, so it can't be confused with high or critical by ear.
    _tone(ctx, t0, 0.22, 440, 440, 0.12)
  }
}
