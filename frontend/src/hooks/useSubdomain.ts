/**
 * Extracts the tenant subdomain from the current page's hostname.
 *
 * Recognised patterns (mirrors the nginx map directive in docker/nginx.conf):
 *   acme.seventh.ai          → "acme"   (production)
 *   acme.localhost            → "acme"   (local dev with .localhost subdomains)
 *   acme.1.2.3.4.nip.io      → "acme"   (nip.io wildcard DNS for dev/staging)
 *
 * Returns null for bare localhost, bare IP, bare seventh.ai, or any other
 * pattern that doesn't carry a recognisable tenant prefix.
 */
function extractSubdomain(hostname: string): string | null {
  const seventh = hostname.match(/^([a-z0-9][a-z0-9-]*)\.seventh\.ai$/)
  if (seventh) return seventh[1]

  const local = hostname.match(/^([a-z0-9][a-z0-9-]*)\.localhost$/)
  if (local) return local[1]

  const nipIo = hostname.match(/^([a-z0-9][a-z0-9-]*)\.[\d.]+\.nip\.io$/)
  if (nipIo) return nipIo[1]

  return null
}

/**
 * Returns the tenant subdomain present in the current URL, or null.
 *
 * Pure function — safe to call outside React. The value is stable for the
 * lifetime of the page (the hostname never changes without a full navigation),
 * so callers can store the result in a useState initialiser without stale-closure
 * concerns.
 */
export function getSubdomain(): string | null {
  if (typeof window === 'undefined') return null
  return extractSubdomain(window.location.hostname)
}
