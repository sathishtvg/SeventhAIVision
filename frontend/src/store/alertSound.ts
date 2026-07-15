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

/** Pure — which severities are audible. Exported for tests. */
export function shouldPlaySound(severity: string, enabled: boolean): boolean {
  return enabled && (severity === 'high' || severity === 'critical')
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
  } else {
    // High: three short attention beeps
    for (let i = 0; i < 3; i++) {
      _tone(ctx, t0 + i * 0.28, 0.16, 880, 880, 0.16)
    }
  }
}
