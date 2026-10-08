/**
 * Resolve an API-relative asset path (e.g. `/api/novels/.../map/terrain/v16`)
 * into a URL an `<img>` / SVG `<image>` can actually load.
 *
 * Why this exists: `<image href>` cannot carry the sidecar's Bearer token, and
 * in the desktop app the page origin is not the sidecar at all (vite dev
 * server in dev, tauri://localhost in production), so a bare relative path
 * resolves to an origin that either 401s or has no `/api` at all — the
 * request dies before it reaches the backend. Fetching the bytes with
 * `sidecarAuthHeaders()` and handing out a blob URL sidesteps both problems;
 * the blob URL is also same-origin, so canvas reads (the relief sampler) are
 * untainted without depending on CORS headers.
 *
 * Web direct-run keeps the relative path untouched: no token there, and the
 * dev proxy serves `/api` fine.
 */

import { useEffect, useState } from "react"
import { getSidecarBaseUrl, isTauri, sidecarAuthHeaders } from "@/api/sidecarBridge"

// Session cache keyed by the asset path. Terrain URLs are versioned
// (`terrain/v16`), so a recipe bump is a new key and never serves stale bytes;
// the number of distinct assets per session is small enough that not revoking
// is the right trade.
const objectUrlCache = new Map<string, string>()

export function useAuthedAssetUrl(path: string | null): string | null {
  // [loadedPath, loadedUrl] paired so a stale blob from the previous path is
  // never returned while the new one is still in flight.
  const [loaded, setLoaded] = useState<{ path: string; url: string } | null>(null)

  useEffect(() => {
    if (!path || !isTauri) return
    let cancelled = false
    const cached = objectUrlCache.get(path)
    const job = cached
      ? Promise.resolve(cached)
      : fetch(`${getSidecarBaseUrl()}${path}`, { headers: sidecarAuthHeaders() })
          .then((res) => {
            if (!res.ok) throw new Error(`asset ${path}: HTTP ${res.status}`)
            return res.blob()
          })
          .then((blob) => {
            const objectUrl = URL.createObjectURL(blob)
            objectUrlCache.set(path, objectUrl)
            return objectUrl
          })
    job
      .then((url) => {
        if (!cancelled) setLoaded({ path, url })
      })
      .catch(() => {
        // No asset is a working configuration: callers already treat a null
        // URL as "render without this layer".
        if (!cancelled) setLoaded(null)
      })
    return () => {
      cancelled = true
    }
  }, [path])

  if (!path) return null
  if (!isTauri) return path
  return loaded && loaded.path === path ? loaded.url : null
}
