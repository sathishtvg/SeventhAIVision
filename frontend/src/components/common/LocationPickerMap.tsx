/**
 * Click-to-place location picker.
 *
 * Sites carry latitude/longitude, and until now the only way to set them was
 * to type two decimal numbers into a form. Nobody knows their gatehouse is at
 * 1.35019, 103.98721 — they'd have to go to another map, read the numbers off
 * it, and copy them across, which is both tedious and easy to get wrong (a
 * transposed digit puts the site in the wrong country, and the only symptom is
 * that attendance geofencing silently rejects every check-in).
 *
 * So: drop a pin on a map. The numbers still round-trip to the API unchanged —
 * this replaces how they are *produced*, not what is stored.
 *
 * The geofence radius is drawn as a circle around the pin when one is set,
 * because the radius has the same problem: "200" means nothing until you can
 * see whether it covers the car park.
 */
import { useEffect, useMemo, useState } from 'react'
import { Box, Button, TextField, Typography, Tooltip, CircularProgress } from '@mui/material'
import MyLocationIcon from '@mui/icons-material/MyLocation'
import SearchIcon from '@mui/icons-material/Search'
import { MapContainer, TileLayer, Marker, Circle, useMap, useMapEvents } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const TILE_URL =
  import.meta.env.VITE_MAP_TILE_URL ?? 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTRIBUTION =
  import.meta.env.VITE_MAP_TILE_ATTRIBUTION ?? '&copy; OpenStreetMap contributors'

/** Fallback view when a site has no coordinates yet. Singapore, matching the
 *  product's home market and the seeded demo sites. */
const DEFAULT_CENTER: [number, number] = [1.3521, 103.8198]
const DEFAULT_ZOOM = 11
const PLACED_ZOOM = 16

/** Leaflet's default marker icon resolves its PNGs relative to the CSS, which
 *  breaks under Vite's asset hashing. A divIcon sidesteps the whole problem
 *  and matches the pin styling used on the Site Map page. */
const pinIcon = L.divIcon({
  className: '',
  iconSize: [26, 26],
  iconAnchor: [13, 26],
  html: `<div style="
    width:26px;height:26px;transform:translateY(-2px);
    display:flex;align-items:center;justify-content:center;
  "><div style="
    width:16px;height:16px;border-radius:50% 50% 50% 0;
    transform:rotate(-45deg);
    background:#6C63FF;border:2px solid #fff;
    box-shadow:0 0 8px rgba(108,99,255,0.9);
  "></div></div>`,
})

function ClickToPlace({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(e) {
      onPick(e.latlng.lat, e.latlng.lng)
    },
  })
  return null
}

/** Recentre when the coordinates change from outside (search, locate, or the
 *  dialog opening on an existing site). Deliberately does NOT fire on a click
 *  the user just made — yanking the map out from under the cursor after every
 *  placement makes fine adjustment impossible. */
function Recenter({ lat, lng, trigger }: { lat: number | null; lng: number | null; trigger: number }) {
  const map = useMap()
  useEffect(() => {
    if (lat == null || lng == null) return
    map.setView([lat, lng], Math.max(map.getZoom(), PLACED_ZOOM))
  }, [trigger, lat, lng, map])
  return null
}

/** Keeps Leaflet's internal size in sync — the map is usually mounted inside a
 *  dialog that animates open, so its container has no final size on first
 *  paint and tiles render into a 0x0 box without this. */
function InvalidateOnMount() {
  const map = useMap()
  useEffect(() => {
    const t = setTimeout(() => map.invalidateSize(), 250)
    return () => clearTimeout(t)
  }, [map])
  return null
}

export interface LocationPickerMapProps {
  latitude: number | null
  longitude: number | null
  onChange: (lat: number, lng: number) => void
  /** Draws a radius circle around the pin so the number is legible on the
   *  ground. Omit or pass null to hide it. */
  radiusMeters?: number | null
  height?: number
}

export function LocationPickerMap({
  latitude, longitude, onChange, radiusMeters, height = 300,
}: LocationPickerMapProps) {
  const [search, setSearch] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  // Bumped whenever the position changes from something OTHER than a map
  // click, which is what Recenter keys off.
  const [recenterTrigger, setRecenterTrigger] = useState(0)

  const hasPin = latitude != null && longitude != null
  const center = useMemo<[number, number]>(
    () => (hasPin ? [latitude!, longitude!] : DEFAULT_CENTER),
    // Center is the map's *initial* view only; Recenter handles later moves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  const applyExternal = (lat: number, lng: number) => {
    onChange(lat, lng)
    setRecenterTrigger((n) => n + 1)
  }

  const locateMe = () => {
    if (!navigator.geolocation) { setSearchError('This browser has no location support'); return }
    setSearchError('')
    navigator.geolocation.getCurrentPosition(
      (pos) => applyExternal(pos.coords.latitude, pos.coords.longitude),
      () => setSearchError('Could not read your location'),
      { enableHighAccuracy: true, timeout: 8000 },
    )
  }

  /** Address lookup via OpenStreetMap Nominatim. Same public service family as
   *  the tiles, so a deployment that points VITE_MAP_TILE_URL at an internal
   *  tile server can point this at its own geocoder too. Failure is
   *  non-blocking — clicking the map always works. */
  const runSearch = async () => {
    const q = search.trim()
    if (!q) return
    setSearching(true)
    setSearchError('')
    try {
      const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`
      const res = await fetch(url, { headers: { Accept: 'application/json' } })
      if (!res.ok) throw new Error(String(res.status))
      const hits = await res.json()
      if (!Array.isArray(hits) || hits.length === 0) {
        setSearchError('No match — click the map to place the pin instead')
        return
      }
      applyExternal(Number(hits[0].lat), Number(hits[0].lon))
    } catch {
      setSearchError('Search unavailable — click the map to place the pin instead')
    } finally {
      setSearching(false)
    }
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
        <TextField
          size="small"
          fullWidth
          label="Find an address or place"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void runSearch() } }}
        />
        <Tooltip title="Search">
          <span>
            <Button
              size="small"
              variant="outlined"
              onClick={() => void runSearch()}
              disabled={searching || !search.trim()}
              sx={{ minWidth: 44, height: 40 }}
            >
              {searching ? <CircularProgress size={16} /> : <SearchIcon fontSize="small" />}
            </Button>
          </span>
        </Tooltip>
        <Tooltip title="Use my current location">
          <Button size="small" variant="outlined" onClick={locateMe} sx={{ minWidth: 44, height: 40 }}>
            <MyLocationIcon fontSize="small" />
          </Button>
        </Tooltip>
      </Box>

      <Box
        sx={{
          height,
          borderRadius: 1,
          overflow: 'hidden',
          border: '1px solid rgba(255,255,255,0.12)',
          '& .leaflet-container': { height: '100%', width: '100%', background: '#0b1020' },
        }}
      >
        <MapContainer center={center} zoom={hasPin ? PLACED_ZOOM : DEFAULT_ZOOM} scrollWheelZoom>
          <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
          <InvalidateOnMount />
          <Recenter lat={latitude} lng={longitude} trigger={recenterTrigger} />
          <ClickToPlace onPick={onChange} />
          {hasPin && (
            <>
              <Marker
                position={[latitude!, longitude!]}
                icon={pinIcon}
                draggable
                eventHandlers={{
                  dragend: (e) => {
                    const p = (e.target as L.Marker).getLatLng()
                    onChange(p.lat, p.lng)
                  },
                }}
              />
              {radiusMeters != null && radiusMeters > 0 && (
                <Circle
                  center={[latitude!, longitude!]}
                  radius={radiusMeters}
                  pathOptions={{ color: '#00D9C0', fillColor: '#00D9C0', fillOpacity: 0.12, weight: 1.5 }}
                />
              )}
            </>
          )}
        </MapContainer>
      </Box>

      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mt: 0.75, gap: 1 }}>
        <Typography variant="caption" color="text.secondary">
          {hasPin
            ? 'Click the map or drag the pin to adjust.'
            : 'Click the map to place this site’s location.'}
        </Typography>
        {hasPin && (
          <Typography variant="caption" sx={{ fontFamily: 'monospace', color: 'text.disabled' }}>
            {latitude!.toFixed(5)}, {longitude!.toFixed(5)}
          </Typography>
        )}
      </Box>
      {searchError && (
        <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 0.5 }}>
          {searchError}
        </Typography>
      )}
    </Box>
  )
}
