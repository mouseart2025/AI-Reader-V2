import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

const mockIsTauri = { value: false }
vi.mock("@/api/sidecarBridge", () => ({
  get isTauri() {
    return mockIsTauri.value
  },
  getSidecarToken: () => "tok-123",
}))

vi.mock("@tauri-apps/plugin-dialog", () => ({
  save: vi.fn(),
}))
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}))

describe("saveExportFile", () => {
  beforeEach(() => {
    mockIsTauri.value = false
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("Web 端走浏览器下载，不弹原生对话框", async () => {
    const { saveExportFile } = await import("@/lib/saveExport")
    const dialog = await import("@tauri-apps/plugin-dialog")

    const click = vi.fn()
    vi.spyOn(document, "createElement").mockReturnValue({
      click,
      set href(_v: string) {},
      set download(_v: string) {},
    } as unknown as HTMLElement)
    const appendChild = vi.spyOn(document.body, "appendChild").mockReturnValue({} as HTMLElement)
    const removeChild = vi.spyOn(document.body, "removeChild").mockReturnValue({} as HTMLElement)

    const result = await saveExportFile({ url: "/api/x", filename: "a.air" })

    expect(result.saved).toBe(true)
    expect(result.path).toBeUndefined()
    expect(click).toHaveBeenCalledTimes(1)
    expect(dialog.save).not.toHaveBeenCalled()
    expect(appendChild).toHaveBeenCalled()
    expect(removeChild).toHaveBeenCalled()
  })

  it("桌面端弹保存对话框并把路径交给原生命令", async () => {
    mockIsTauri.value = true
    const { saveExportFile } = await import("@/lib/saveExport")
    const dialog = await import("@tauri-apps/plugin-dialog")
    const core = await import("@tauri-apps/api/core")

    vi.mocked(dialog.save).mockResolvedValue("/Users/me/Documents/红楼.air")
    vi.mocked(core.invoke).mockResolvedValue("/Users/me/Documents/红楼.air")

    const result = await saveExportFile({
      url: "http://127.0.0.1:1234/api/x",
      filename: "红楼.air",
      filters: [{ name: "AIR", extensions: ["air"] }],
    })

    expect(result).toEqual({ saved: true, path: "/Users/me/Documents/红楼.air" })
    expect(core.invoke).toHaveBeenCalledWith("save_export", {
      url: "http://127.0.0.1:1234/api/x",
      path: "/Users/me/Documents/红楼.air",
      token: "tok-123",
    })
  })

  it("用户取消保存对话框时返回 saved=false 且不写文件", async () => {
    mockIsTauri.value = true
    const { saveExportFile } = await import("@/lib/saveExport")
    const dialog = await import("@tauri-apps/plugin-dialog")
    const core = await import("@tauri-apps/api/core")

    vi.mocked(dialog.save).mockResolvedValue(null)

    const result = await saveExportFile({
      url: "http://127.0.0.1:1234/api/x",
      filename: "红楼.air",
    })

    expect(result).toEqual({ saved: false })
    expect(core.invoke).not.toHaveBeenCalled()
  })
})
