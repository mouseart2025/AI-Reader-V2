/**
 * Demo data adapter — loads static .json.gz files from public/demo-data/
 * Uses native DecompressionStream with pako fallback for older browsers.
 */
import { getDemoNovel, DEMO_FILES, type DemoEndpoint } from "./demoNovelMap"

/** In-memory cache keyed by "slug/endpoint" */
const cache = new Map<string, unknown>()

/** Decompress gzipped response and parse JSON.
 *  Handles both cases:
 *  - Browser already decompressed (Vite dev server / CDN Content-Encoding: gzip)
 *  - Raw .gz file that needs manual decompression
 */
async function decompressGzipResponse<T>(response: Response): Promise<T> {
  const arrayBuffer = await response.arrayBuffer()
  const bytes = new Uint8Array(arrayBuffer)

  // Gzip magic number: 0x1f 0x8b
  // If missing, browser already decompressed → parse as JSON directly
  if (bytes.length < 2 || bytes[0] !== 0x1f || bytes[1] !== 0x8b) {
    const text = new TextDecoder().decode(bytes)
    return JSON.parse(text) as T
  }

  // Native DecompressionStream (Chrome 80+, Firefox 113+, Safari 16.4+)
  if ("DecompressionStream" in window) {
    const ds = new DecompressionStream("gzip")
    const blob = new Blob([bytes])
    const decompressedStream = blob.stream().pipeThrough(ds)
    const reader = decompressedStream.getReader()
    const chunks: Uint8Array[] = []
    let totalLength = 0

    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(value)
      totalLength += value.length
    }

    const merged = new Uint8Array(totalLength)
    let offset = 0
    for (const chunk of chunks) {
      merged.set(chunk, offset)
      offset += chunk.length
    }

    const text = new TextDecoder().decode(merged)
    return JSON.parse(text) as T
  }

  // Fallback: dynamic import pako (only loaded when needed)
  const { inflate } = await import("pako")
  const inflated = inflate(bytes, { to: "string" })
  return JSON.parse(inflated) as T
}

/**
 * Load demo data for a specific novel and endpoint.
 * Results are cached in memory to avoid re-fetching.
 */
export async function loadDemoData<T>(slug: string, endpoint: DemoEndpoint): Promise<T> {
  const cacheKey = `${slug}/${endpoint}`
  if (cache.has(cacheKey)) {
    return cache.get(cacheKey) as T
  }

  const novel = getDemoNovel(slug)
  if (!novel) {
    throw new Error(`Unknown demo novel: ${slug}`)
  }

  const fileName = DEMO_FILES[endpoint]
  const basePath = import.meta.env.BASE_URL ?? "/"
  const url = `${basePath}${novel.dataPath.replace(/^\//, "")}/${fileName}`

  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`Failed to load demo data: ${url} (${response.status})`)
  }

  const data = await decompressGzipResponse<T>(response)
  cache.set(cacheKey, data)
  return data
}

/**
 * Preload all demo data for a novel. Returns a DemoDataBundle.
 */
export interface DemoDataBundle {
  novel: Record<string, unknown>
  chapters: unknown[]
  graph: Record<string, unknown>
  map: Record<string, unknown>
  timeline: Record<string, unknown>
  encyclopedia: Record<string, unknown>
  encyclopediaStats: Record<string, unknown>
  factions: Record<string, unknown>
  worldStructure: Record<string, unknown>
}

export async function preloadAllDemoData(slug: string): Promise<DemoDataBundle> {
  const [novel, chapters, graph, map, timeline, encyclopedia, encyclopediaStats, factions, worldStructure] =
    await Promise.all([
      loadDemoData<Record<string, unknown>>(slug, "novel"),
      loadDemoData<unknown[]>(slug, "chapters"),
      loadDemoData<Record<string, unknown>>(slug, "graph"),
      loadDemoData<Record<string, unknown>>(slug, "map"),
      loadDemoData<Record<string, unknown>>(slug, "timeline"),
      loadDemoData<Record<string, unknown>>(slug, "encyclopedia"),
      loadDemoData<Record<string, unknown>>(slug, "encyclopedia-stats"),
      loadDemoData<Record<string, unknown>>(slug, "factions"),
      loadDemoData<Record<string, unknown>>(slug, "world-structure"),
    ])

  return { novel, chapters, graph, map, timeline, encyclopedia, encyclopediaStats, factions, worldStructure }
}

/** Clear cached data (useful when switching novels) */
export function clearDemoCache(): void {
  cache.clear()
}
