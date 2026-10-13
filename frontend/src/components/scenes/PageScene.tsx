import { useCallback, useEffect, useState } from 'react'
import { Box, Typography } from '@mui/material'
import { useIsFetching } from '@tanstack/react-query'
import { useReducedMotion } from '@/motion'
import { usePageScenes } from '@/motion/scenePreference'
import { SCENE_EXIT_MS, SCENE_MS, sceneOf } from '@/motion/scenes'
import { SceneEmblem } from './SceneEmblem'

export interface PageSceneProps {
  /** The page being entered. The shell gives this a `key` of the same path, so each page gets a new scene. */
  pathname: string
  /** Never cover the page: the screen is in full-screen focus, where the page is all there is. */
  docked?: boolean
}

/**
 * A page's own scene, played as the page is entered, and its own loading
 * after it (`@/motion/scenes` says which drawing and which words).
 *
 * It sits in the shell just above the page area and is drawn over it:
 *
 *   entrance  the emblem draws itself in the middle of the page area with the
 *             page's name, then clears, and the page arrives as it clears.
 *   loading   if the page is still fetching when the entrance is over, the
 *             emblem goes on, small, at the top of the page area beside
 *             "Loading …", until the page's first answers are in. That is
 *             read from the requests really in flight; nothing is timed.
 *
 * It never takes a click or a key (the page beneath has them from its first
 * frame) and the first click or key ends it at once. A page where seconds
 * matter is not covered: its emblem plays at the top while the page is
 * already in view. Nothing is drawn for somebody who asked for less motion or
 * switched page animations off in Settings.
 *
 * The entrance is timed by the style sheet and ends hidden whether or not
 * this component ever hears that it ended.
 */
export function PageScene({ pathname, docked = false }: PageSceneProps) {
  const reduced = useReducedMotion()
  const [wanted] = usePageScenes()
  if (reduced || !wanted) return null
  return <Scene pathname={pathname} docked={docked} />
}

const firstFetch = (query: { state: { data: unknown } }) => query.state.data === undefined

function Scene({ pathname, docked }: Required<PageSceneProps>) {
  const scene = sceneOf(pathname)
  const atTop = docked || scene.urgent === true
  // What is being fetched for the first time: a request that already has an answer on screen is a refresh, and
  // the shell's own polls are refreshes from their second run on. Neither is "the page is loading".
  const fetching = useIsFetching({ predicate: firstFetch })
  /** The entrance has played through, or was cut short. */
  const [over, setOver] = useState(false)
  const [cutShort, setCutShort] = useState(false)
  /** The page's first answers are in. After that, what it fetches is its own business. */
  const [settled, setSettled] = useState(false)
  if (over && !settled && fetching === 0) setSettled(true)

  useEffect(() => {
    const played = setTimeout(() => setOver(true), SCENE_MS + SCENE_EXIT_MS)
    const cut = () => { setCutShort(true); setOver(true) }
    window.addEventListener('pointerdown', cut, true)
    window.addEventListener('keydown', cut, true)
    return () => {
      clearTimeout(played)
      window.removeEventListener('pointerdown', cut, true)
      window.removeEventListener('keydown', cut, true)
    }
  }, [])

  // How much room there is below this point, so the emblem sits in the middle of the page area.
  const measure = useCallback((el: HTMLDivElement | null) => {
    if (el) el.style.setProperty('--sav-scene-h', `${Math.max(0, window.innerHeight - el.getBoundingClientRect().top)}px`)
  }, [])

  // `cover` is kept after the entrance is over: the page's own arrival was timed against it and is left to finish.
  // Cut short, the page is shown at once.
  const stage = cutShort ? 'cut' : atTop ? 'top' : 'cover'
  const showTop = atTop ? !over || !settled : over && !settled

  return (
    <Box ref={measure} className={`sav-scene sav-scene--${stage}`} aria-hidden data-scene={scene.motif}>
      {stage === 'cover' && !over && (
        <div className="sav-scene__cover">
          <Box
            className="sav-scene__mark"
            // The colour of the light behind the emblem: the emblem's own.
            sx={{ color: scene.tone === 'danger' ? 'error.main' : scene.tone === 'warn' ? 'warning.main' : 'primary.main' }}
          >
            <SceneEmblem motif={scene.motif} tone={scene.tone} size={112} />
            {scene.name && (
              <Typography variant="h6" sx={{ fontWeight: 800, letterSpacing: '0.02em', color: 'text.primary' }}>
                {scene.name}
              </Typography>
            )}
          </Box>
        </div>
      )}
      {showTop && (
        <Box
          // After the entrance it waits before it is seen, as every placeholder does: a page that answers at once
          // shows no "Loading".
          className={over ? 'sav-scene__top sav-waits' : 'sav-scene__top'}
          sx={{
            position: 'absolute', top: 2, left: '50%', translate: '-50% 0',
            display: 'flex', alignItems: 'center', gap: 0.75, px: 1.25, py: 0.25,
            borderRadius: 999, border: 1, borderColor: 'divider', bgcolor: 'background.paper', color: 'text.secondary',
            whiteSpace: 'nowrap',
          }}
        >
          <SceneEmblem motif={scene.motif} tone={scene.tone} size={18} />
          <Typography variant="caption" sx={{ fontWeight: 600, lineHeight: 1.4 }}>
            {over ? `${scene.loading}…` : scene.name || scene.loading}
          </Typography>
        </Box>
      )}
    </Box>
  )
}
