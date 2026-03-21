import { describe, it, expect, vi } from "vitest"

// Mock isTauri before importing novelPath
vi.mock("@/api/sidecarBridge", () => ({ isTauri: false }))

describe("novelPath (web mode)", () => {
  it("generates web paths", async () => {
    const { novelPath } = await import("./novelPaths")
    expect(novelPath("abc", "read")).toBe("/read/abc")
    expect(novelPath("abc", "graph")).toBe("/graph/abc")
    expect(novelPath("abc", "read", "chapter=5")).toBe("/read/abc?chapter=5")
  })
})

describe("novelPath (Tauri mode)", () => {
  it("generates Tauri paths with tab alias normalization", async () => {
    // Reset module cache and re-mock for Tauri mode
    vi.resetModules()
    vi.doMock("@/api/sidecarBridge", () => ({ isTauri: true }))
    const { novelPath } = await import("./novelPaths")

    expect(novelPath("abc", "reading")).toBe("/novel/abc/reading")
    expect(novelPath("abc", "graph")).toBe("/novel/abc/graph")
  })

  it("normalizes 'read' to 'reading' in Tauri mode (Bug #1 fix)", async () => {
    vi.resetModules()
    vi.doMock("@/api/sidecarBridge", () => ({ isTauri: true }))
    const { novelPath } = await import("./novelPaths")

    // This was the bug: "read" generated /novel/abc/read → 404
    // Now it should normalize to "reading"
    expect(novelPath("abc", "read")).toBe("/novel/abc/reading")
    expect(novelPath("abc", "read", "chapter=5")).toBe("/novel/abc/reading?chapter=5")
  })
})
