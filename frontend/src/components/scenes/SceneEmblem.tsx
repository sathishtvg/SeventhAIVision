import { Fragment } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Box } from '@mui/material'
import type { SceneMotif, SceneTone } from '@/motion/scenes'

/**
 * The drawings: one for each kind of thing a page is about, on a 64 by 64
 * grid. Each is a few strokes that draw themselves in turn (the entrance) and
 * one part that goes on moving for as long as the emblem is on screen (the
 * loading): a sweep, a rotor, a ring, a line that scans.
 *
 * How they move is `motion.css` (the `sc-` rules); nothing here animates by
 * itself. Two colours, `--sc-a` and `--sc-b`, set from the theme by the tone.
 * Decorative: hidden from a screen reader, which is told the page's name by
 * the page.
 */

const A = 'var(--sc-a)'
const B = 'var(--sc-b)'

/** The turn a part takes: the parts of an emblem arrive one after another. */
const turn = (n: number) => ({ '--i': n }) as CSSProperties
/** A stroke that draws itself. Its length is called 1, so one rule draws any of them. */
const draw = (n = 0) => ({ pathLength: 1, className: 'sc-draw', style: turn(n) })
/** A part that pops into place. */
const pop = (n = 0) => ({ className: 'sc-pop', style: turn(n) })
/** A bar that grows from its foot. */
const rise = (n = 0) => ({ className: 'sc-rise', style: turn(n) })
/** How far a scanning part travels, there and back. */
const travel = (dx: number, dy: number) => ({ '--dx': `${dx}px`, '--dy': `${dy}px` }) as CSSProperties

const ROUTE = 'M10 52 L24 40 L21 24 L40 29 L54 12'
const HEARTBEAT = 'M8 34 H20 L25 22 L32 46 L38 27 L42 34 H56'
const TRIANGLE = 'M32 8 L58 54 H6 Z'

const MOTIFS: Record<SceneMotif, ReactNode> = {
  // A wall of camera tiles lighting up, and a line scanning down it.
  wall: (
    <>
      {[0, 1, 2].flatMap((row) => [0, 1, 2].map((col) => (
        <rect key={`${row}${col}`} x={8 + col * 17} y={11 + row * 15} width={14} height={11} rx={2} stroke={A} {...pop(row + col)} />
      )))}
      <line x1={6} y1={9} x2={58} y2={9} stroke={B} className="sc-scan" style={travel(0, 46)} />
    </>
  ),
  // A console screen with its status lines, and one light that is live.
  console: (
    <>
      <rect x={7} y={9} width={50} height={34} rx={4} stroke={A} {...draw(0)} />
      <path d="M32 43 V52 M22 53 H42" stroke={A} {...draw(2)} />
      {[0, 1, 2].map((row) => (
        <Fragment key={row}>
          <circle cx={15} cy={18 + row * 8} r={1.8} fill={B} stroke="none" {...pop(2 + row)} />
          <line x1={21} y1={18 + row * 8} x2={row === 1 ? 36 : 42} y2={18 + row * 8} stroke={A} {...draw(3 + row)} />
        </Fragment>
      ))}
      <circle cx={50} cy={18} r={3.5} stroke={B} className="sc-ring" />
    </>
  ),
  // Two figures and a chart whose line is drawn.
  dashboard: (
    <>
      <rect x={7} y={8} width={22} height={15} rx={3} stroke={A} {...pop(0)} />
      <rect x={35} y={8} width={22} height={15} rx={3} stroke={A} {...pop(1)} />
      <rect x={7} y={29} width={50} height={27} rx={3} stroke={A} {...draw(2)} />
      <polyline points="13,49 22,42 30,46 40,36 51,39" stroke={B} {...draw(4)} />
      <circle cx={51} cy={39} r={3} stroke={B} className="sc-ring" />
    </>
  ),
  // A bell that swings, and its sound.
  bell: (
    <>
      <g className="sc-swing">
        <path d="M32 9 C23 9 18 16 18 24 V35 L13 43 H51 L46 35 V24 C46 16 41 9 32 9 Z" stroke={A} {...draw(0)} />
        <path d="M27 48 a5 5 0 0 0 10 0" stroke={A} {...draw(3)} />
      </g>
      <path d="M9 23 C9 17 11 13 14 10 M55 23 C55 17 53 13 50 10" stroke={B} className="sc-pulse" />
    </>
  ),
  // A warning sign, and a ring going out from it.
  warning: (
    <>
      <path d={TRIANGLE} stroke={A} {...draw(0)} />
      <line x1={32} y1={25} x2={32} y2={39} stroke={B} strokeWidth={3.5} {...draw(3)} />
      <circle cx={32} cy={46} r={2.2} fill={B} stroke="none" {...pop(5)} />
      <path d={TRIANGLE} stroke={A} className="sc-ring" />
    </>
  ),
  // A sweep going round, and something found.
  radar: (
    <>
      <circle cx={32} cy={32} r={24} stroke={A} {...draw(0)} />
      <circle cx={32} cy={32} r={13} stroke={A} opacity={0.55} {...draw(2)} />
      <path d="M32 32 L32 8 A24 24 0 0 1 52.8 20 Z" fill={B} stroke="none" opacity={0.35} className="sc-sweep" />
      <circle cx={43} cy={23} r={2.5} fill={B} stroke="none" className="sc-pulse" />
      <circle cx={32} cy={32} r={2} fill={A} stroke="none" {...pop(3)} />
    </>
  ),
  // A mast sending out.
  broadcast: (
    <>
      <path d="M32 31 V56 M23 56 L32 36 L41 56" stroke={A} {...draw(0)} />
      <circle cx={32} cy={26} r={3.5} fill={B} stroke="none" {...pop(2)} />
      <path d="M24 18 a11 11 0 0 0 0 16 M40 18 a11 11 0 0 1 0 16" stroke={B} className="sc-pulse" style={turn(0)} />
      <path d="M17 11 a21 21 0 0 0 0 30 M47 11 a21 21 0 0 1 0 30" stroke={B} className="sc-pulse" style={turn(1)} />
    </>
  ),
  // A heartbeat, traced again and again.
  pulse: (
    <>
      <circle cx={32} cy={32} r={25} stroke={A} opacity={0.45} {...draw(0)} />
      <path d={HEARTBEAT} stroke={A} {...draw(1)} />
      <path d={HEARTBEAT} stroke={B} pathLength={1} className="sc-trace" />
    </>
  ),
  // Lines of a record, and a glass moving over them.
  search: (
    <>
      {[0, 1, 2, 3].map((row) => (
        <line key={row} x1={9} y1={15 + row * 10} x2={row % 2 ? 32 : 42} y2={15 + row * 10} stroke={A} {...draw(row)} />
      ))}
      <g className="sc-scan" style={travel(-12, -10)}>
        <circle cx={41} cy={39} r={10} stroke={B} />
        <line x1={48.5} y1={46.5} x2={57} y2={55} stroke={B} strokeWidth={3.5} />
      </g>
    </>
  ),
  // A folder, with a sheet standing in it.
  casefile: (
    <>
      <path d="M7 20 H23 L28 26 H57 V55 H7 Z" stroke={A} {...draw(0)} />
      <path d="M19 20 V10 H45 V26" stroke={B} {...draw(2)} />
      <line x1={25} y1={16} x2={39} y2={16} stroke={B} {...draw(4)} />
      <line x1={15} y1={38} x2={38} y2={38} stroke={A} {...draw(3)} />
      <line x1={15} y1={46} x2={30} y2={46} stroke={B} className="sc-pulse" />
    </>
  ),
  // A strip of film, and the place it is being played from.
  film: (
    <>
      <rect x={6} y={17} width={52} height={30} rx={3} stroke={A} {...draw(0)} />
      {[0, 1, 2, 3, 4].map((n) => (
        <Fragment key={n}>
          <rect x={11 + n * 10} y={20.5} width={3} height={3} rx={0.8} fill={A} stroke="none" {...pop(n)} />
          <rect x={11 + n * 10} y={40.5} width={3} height={3} rx={0.8} fill={A} stroke="none" {...pop(n)} />
        </Fragment>
      ))}
      <path d="M28 27.5 L37 32 L28 36.5 Z" stroke={B} fill={B} {...pop(4)} />
      <line x1={11} y1={12} x2={11} y2={52} stroke={B} className="sc-scan" style={travel(42, 0)} />
    </>
  ),
  // Bars growing, and the trend across them.
  chart: (
    <>
      <path d="M9 8 V55 H57" stroke={A} {...draw(0)} />
      {[[16, 16], [26, 26], [36, 20], [46, 34]].map(([x, height], n) => (
        <rect key={x} x={x} y={51 - height} width={6} height={height} rx={1.5} fill={A} stroke="none" opacity={0.85} {...rise(n + 1)} />
      ))}
      <polyline points="19,30 29,21 39,26 49,12" stroke={B} {...draw(5)} />
      <circle cx={49} cy={12} r={3} stroke={B} className="sc-ring" />
    </>
  ),
  // A clock whose hand goes round.
  clock: (
    <>
      <circle cx={32} cy={32} r={24} stroke={A} {...draw(0)} />
      <path d="M32 11 V14 M53 32 H50 M32 53 V50 M11 32 H14" stroke={A} {...draw(2)} />
      <line x1={32} y1={32} x2={32} y2={19} stroke={A} strokeWidth={3} {...draw(3)} />
      <line x1={32} y1={32} x2={45} y2={32} stroke={B} className="sc-sweep" />
      <circle cx={32} cy={32} r={2.2} fill={B} stroke="none" {...pop(3)} />
    </>
  ),
  // A round with its checkpoints, walked again and again.
  route: (
    <>
      <path d={ROUTE} stroke={A} opacity={0.5} {...draw(0)} />
      <path d={ROUTE} stroke={B} pathLength={1} className="sc-trace" />
      {[[10, 52], [24, 40], [21, 24], [40, 29], [54, 12]].map(([cx, cy], n) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={n === 4 ? 4 : 3} fill={n === 4 ? B : A} stroke="none" {...pop(n + 1)} />
      ))}
    </>
  ),
  // A drone from above, hovering, its rotors turning.
  drone: (
    <g className="sc-bob">
      <path d="M27 29 L17 21 M37 29 L47 21 M27 35 L17 43 M37 35 L47 43" stroke={A} {...draw(0)} />
      <rect x={25} y={27} width={14} height={10} rx={4} stroke={A} {...pop(1)} />
      <circle cx={32} cy={32} r={1.8} fill={B} stroke="none" {...pop(2)} />
      {[[17, 21], [47, 21], [17, 43], [47, 43]].map(([cx, cy]) => (
        <ellipse key={`${cx}-${cy}`} cx={cx} cy={cy} rx={9} ry={2.6} stroke={B} className="sc-spin" />
      ))}
    </g>
  ),
  // A shield, checked, and a line reading down it.
  shield: (
    <>
      <path d="M32 6 L54 14 V30 C54 44 45 53 32 58 C19 53 10 44 10 30 V14 Z" stroke={A} {...draw(0)} />
      <path d="M22 32 L29 39 L43 24" stroke={B} strokeWidth={3} {...draw(4)} />
      <line x1={15} y1={17} x2={49} y2={17} stroke={B} opacity={0.6} className="sc-scan" style={travel(0, 26)} />
    </>
  ),
  // A camera, its lens, and the light that says it is on.
  camera: (
    <>
      <rect x={7} y={20} width={35} height={25} rx={4} stroke={A} {...draw(0)} />
      <path d="M42 28 L57 20 V45 L42 37" stroke={A} {...draw(2)} />
      <circle cx={24} cy={32.5} r={7} stroke={B} {...pop(3)} />
      <circle cx={24} cy={32.5} r={7} stroke={B} className="sc-ring" />
      <circle cx={13} cy={26} r={1.8} fill={B} stroke="none" className="sc-pulse" />
    </>
  ),
  // A barrier whose arm lifts.
  gate: (
    <>
      <path d="M5 55 H59" stroke={A} {...draw(0)} />
      <rect x={10} y={24} width={9} height={31} rx={2} stroke={A} {...draw(1)} />
      <g className="sc-lift">
        <line x1={17} y1={30} x2={56} y2={30} stroke={B} strokeWidth={3.5} />
        <path d="M27 30 v4 M37 30 v4 M47 30 v4" stroke={B} />
      </g>
    </>
  ),
  // A pass, with a person on it.
  badge: (
    <>
      <rect x={7} y={13} width={50} height={38} rx={5} stroke={A} {...draw(0)} />
      <circle cx={22} cy={28} r={6} stroke={B} {...pop(2)} />
      <path d="M13 44 C13 38 17 36 22 36 C27 36 31 38 31 44" stroke={B} {...draw(3)} />
      <line x1={37} y1={25} x2={51} y2={25} stroke={A} {...draw(3)} />
      <line x1={37} y1={33} x2={51} y2={33} stroke={A} {...draw(4)} />
      <line x1={37} y1={41} x2={46} y2={41} stroke={B} className="sc-pulse" />
    </>
  ),
  // A register, ticked off line by line.
  ledger: (
    <>
      <rect x={12} y={10} width={40} height={47} rx={4} stroke={A} {...draw(0)} />
      <rect x={24} y={6} width={16} height={8} rx={2.5} stroke={A} {...pop(1)} />
      {[0, 1, 2].map((row) => (
        <Fragment key={row}>
          <path d={`M18 ${25 + row * 11} l3 3 l5 -6`} stroke={B} {...draw(2 + row)} />
          {row < 2 && <line x1={31} y1={25 + row * 11} x2={46} y2={25 + row * 11} stroke={A} {...draw(3 + row)} />}
        </Fragment>
      ))}
      <line x1={31} y1={47} x2={41} y2={47} stroke={A} className="sc-pulse" />
    </>
  ),
  // A bill, and its total.
  receipt: (
    <>
      <path d="M14 7 H50 V57 L44 53 L38 57 L32 53 L26 57 L20 53 L14 57 Z" stroke={A} {...draw(0)} />
      <line x1={21} y1={18} x2={43} y2={18} stroke={A} {...draw(2)} />
      <line x1={21} y1={26} x2={43} y2={26} stroke={A} {...draw(3)} />
      <line x1={21} y1={34} x2={35} y2={34} stroke={A} {...draw(4)} />
      <line x1={30} y1={44} x2={43} y2={44} stroke={B} strokeWidth={3.5} className="sc-pulse" />
    </>
  ),
  // A document with its figures.
  report: (
    <>
      <path d="M14 6 H39 L50 17 V58 H14 Z" stroke={A} {...draw(0)} />
      <path d="M39 6 V17 H50" stroke={A} {...draw(2)} />
      {[[21, 9], [29, 15], [37, 22]].map(([x, height], n) => (
        <rect key={x} x={x} y={50 - height} width={5.5} height={height} rx={1.2} fill={B} stroke="none" {...rise(n + 3)} />
      ))}
      <line x1={21} y1={22} x2={32} y2={22} stroke={A} className="sc-pulse" />
    </>
  ),
  // Three controls being set.
  sliders: (
    <>
      {[[18, 24, 10], [32, 42, -10], [46, 30, 10]].map(([y, x, dx], n) => (
        <Fragment key={y}>
          <line x1={9} y1={y} x2={55} y2={y} stroke={A} {...draw(n)} />
          <circle cx={x} cy={y} r={5} fill={B} stroke="none" className="sc-scan" style={travel(dx, 0)} />
        </Fragment>
      ))}
    </>
  ),
  // Buildings going up, lights on.
  building: (
    <>
      <path d="M5 56 H59" stroke={A} {...draw(0)} />
      <rect x={10} y={30} width={13} height={26} rx={1.5} stroke={A} {...rise(1)} />
      <rect x={26} y={12} width={15} height={44} rx={1.5} stroke={A} {...rise(2)} />
      <rect x={44} y={36} width={11} height={20} rx={1.5} stroke={A} {...rise(3)} />
      {[[31, 20], [36, 20], [31, 28], [36, 28], [31, 36], [36, 36]].map(([cx, cy], n) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={1.4} fill={B} stroke="none" className="sc-pulse" style={turn(n % 3)} />
      ))}
    </>
  ),
}

export interface SceneEmblemProps {
  motif: SceneMotif
  tone?: SceneTone
  /** In pixels, square. */
  size?: number
}

export function SceneEmblem({ motif, tone = 'brand', size = 104 }: SceneEmblemProps) {
  return (
    <Box
      component="svg"
      viewBox="0 0 64 64"
      aria-hidden
      focusable="false"
      className="sc"
      data-motif={motif}
      sx={(theme) => ({
        width: size, height: size, flexShrink: 0, display: 'block', overflow: 'visible',
        fill: 'none', strokeLinecap: 'round', strokeLinejoin: 'round',
        // Drawn small, beside the word "Loading", the strokes are thickened so they are still seen.
        strokeWidth: size < 40 ? 4 : 2.5,
        '--sc-a': tone === 'danger' ? theme.palette.error.main : tone === 'warn' ? theme.palette.warning.main : theme.palette.primary.main,
        '--sc-b': tone === 'danger' ? theme.palette.warning.main : tone === 'warn' ? theme.palette.primary.main : theme.palette.secondary.main,
      })}
    >
      {MOTIFS[motif]}
    </Box>
  )
}
