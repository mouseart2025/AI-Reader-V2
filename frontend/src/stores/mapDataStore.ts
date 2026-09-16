import { create } from "zustand"
import type { MapData, MapLayerInfo } from "@/api/types"

export interface MapCacheEntry {
  data: MapData
  layers: MapLayerInfo[] | null
}

export function mapCacheKey(
  novelId: string,
  chapterStart: number,
  chapterEnd: number,
  layerId: string,
): string {
  return `${novelId}:${chapterStart}:${chapterEnd}:${layerId}`
}

interface MapDataState {
  cache: Map<string, MapCacheEntry>
  get: (key: string) => MapCacheEntry | undefined
  set: (key: string, value: MapCacheEntry) => void
  invalidateNovel: (novelId: string) => void
  clear: () => void
}

const MAX_ENTRIES = 10

// Session-level in-memory cache for map payloads (large JSON, expensive to
// re-fetch + re-render). Not persisted; LRU-evicted beyond MAX_ENTRIES.
export const useMapDataStore = create<MapDataState>((set, get) => ({
  cache: new Map<string, MapCacheEntry>(),

  get: (key) => {
    const cache = get().cache
    const entry = cache.get(key)
    if (!entry) return undefined
    // LRU touch — re-insert to move key to most-recent position
    cache.delete(key)
    cache.set(key, entry)
    return entry
  },

  set: (key, value) => {
    const cache = get().cache
    cache.delete(key)
    cache.set(key, value)
    if (cache.size > MAX_ENTRIES) {
      const oldest = cache.keys().next().value
      if (oldest !== undefined) cache.delete(oldest)
    }
  },

  invalidateNovel: (novelId) => {
    const cache = get().cache
    const prefix = `${novelId}:`
    for (const key of [...cache.keys()]) {
      if (key.startsWith(prefix)) cache.delete(key)
    }
  },

  clear: () => {
    set({ cache: new Map<string, MapCacheEntry>() })
  },
}))
