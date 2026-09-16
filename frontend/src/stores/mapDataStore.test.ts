import { beforeEach, describe, expect, it } from "vitest"
import type { MapData } from "@/api/types"
import { mapCacheKey, useMapDataStore, type MapCacheEntry } from "./mapDataStore"

function entry(): MapCacheEntry {
  return { data: {} as unknown as MapData, layers: null }
}

describe("mapDataStore", () => {
  beforeEach(() => {
    useMapDataStore.getState().clear()
  })

  it("builds cache keys from novel/range/layer", () => {
    expect(mapCacheKey("n1", 1, 50, "overworld")).toBe("n1:1:50:overworld")
  })

  it("get/set roundtrip; missing keys return undefined", () => {
    const s = useMapDataStore.getState()
    expect(s.get("n1:1:50:overworld")).toBeUndefined()
    const e = entry()
    s.set("n1:1:50:overworld", e)
    expect(useMapDataStore.getState().get("n1:1:50:overworld")).toBe(e)
  })

  it("invalidateNovel removes only that novel's keys (all ranges/layers)", () => {
    const s = useMapDataStore.getState()
    s.set(mapCacheKey("n1", 1, 50, "overworld"), entry())
    s.set(mapCacheKey("n1", 1, 50, "underworld"), entry())
    s.set(mapCacheKey("n1", 51, 100, "overworld"), entry())
    const other = entry()
    s.set(mapCacheKey("n2", 1, 50, "overworld"), other)

    useMapDataStore.getState().invalidateNovel("n1")

    const after = useMapDataStore.getState()
    expect(after.get(mapCacheKey("n1", 1, 50, "overworld"))).toBeUndefined()
    expect(after.get(mapCacheKey("n1", 1, 50, "underworld"))).toBeUndefined()
    expect(after.get(mapCacheKey("n1", 51, 100, "overworld"))).toBeUndefined()
    expect(after.get(mapCacheKey("n2", 1, 50, "overworld"))).toBe(other)
  })

  it("evicts the least recently used key beyond 10 entries", () => {
    const s = useMapDataStore.getState()
    for (let i = 0; i < 10; i++) s.set(`k${i}`, entry())
    s.set("k10", entry())

    const after = useMapDataStore.getState()
    expect(after.cache.size).toBe(10)
    expect(after.get("k0")).toBeUndefined() // oldest evicted
    expect(after.get("k1")).toBeDefined()
    expect(after.get("k10")).toBeDefined()
  })

  it("get() touches the key, protecting it from LRU eviction", () => {
    const s = useMapDataStore.getState()
    for (let i = 0; i < 10; i++) s.set(`k${i}`, entry())
    s.get("k0") // touch → k1 becomes oldest
    s.set("k10", entry())

    const after = useMapDataStore.getState()
    expect(after.get("k0")).toBeDefined()
    expect(after.get("k1")).toBeUndefined()
  })
})
