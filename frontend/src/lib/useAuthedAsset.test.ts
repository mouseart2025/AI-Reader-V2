import { renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

// Mock the sidecar bridge so each test can pick web vs desktop mode.
const bridge = vi.hoisted(() => ({
  isTauri: false,
  baseUrl: "http://localhost:61234",
  token: "test-token",
}))
vi.mock("@/api/sidecarBridge", () => ({
  get isTauri() {
    return bridge.isTauri
  },
  getSidecarBaseUrl: () => bridge.baseUrl,
  sidecarAuthHeaders: () => ({ Authorization: `Bearer ${bridge.token}` }),
}))

import { useAuthedAssetUrl } from "./useAuthedAsset"

describe("useAuthedAssetUrl", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    bridge.isTauri = false
    let n = 0
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => `blob:mock-${++n}`),
    })
  })

  it("web 直跑：相对路径原样返回，不 fetch", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch")
    const { result } = renderHook(() => useAuthedAssetUrl("/api/novels/n1/map/terrain/v16"))
    expect(result.current).toBe("/api/novels/n1/map/terrain/v16")
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("null 输入返回 null", () => {
    const { result } = renderHook(() => useAuthedAssetUrl(null))
    expect(result.current).toBeNull()
  })

  it("桌面端：带 Bearer 令牌向 sidecar 取字节，返回 blob URL", async () => {
    bridge.isTauri = true
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(new Blob(["png-bytes"])),
    })
    vi.stubGlobal("fetch", fetchMock)

    const { result } = renderHook(() =>
      useAuthedAssetUrl("/api/novels/n1/map/terrain/v99"),
    )
    await waitFor(() => expect(result.current).toBe("blob:mock-1"))
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:61234/api/novels/n1/map/terrain/v99",
      { headers: { Authorization: "Bearer test-token" } },
    )
  })

  it("桌面端：同路径第二次渲染命中会话缓存，不再 fetch", async () => {
    bridge.isTauri = true
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(new Blob(["png-bytes"])),
    })
    vi.stubGlobal("fetch", fetchMock)

    const first = renderHook(() => useAuthedAssetUrl("/api/novels/n1/map/terrain/v100"))
    await waitFor(() => expect(first.result.current).toMatch(/^blob:/))
    first.unmount()

    const second = renderHook(() => useAuthedAssetUrl("/api/novels/n1/map/terrain/v100"))
    await waitFor(() => expect(second.result.current).toMatch(/^blob:/))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("桌面端：sidecar 返回错误时回退 null（缺失图层是可用状态）", async () => {
    bridge.isTauri = true
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404 })
    vi.stubGlobal("fetch", fetchMock)
    const { result } = renderHook(() =>
      useAuthedAssetUrl("/api/novels/n1/map/terrain/v404"),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(result.current).toBeNull()
  })
})
