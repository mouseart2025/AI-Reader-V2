import { describe, expect, it } from "vitest"
import { resolveDemoAssetUrl } from "./demoNovelMap"

describe("resolveDemoAssetUrl", () => {
  it("把 /demo-data/ 前缀换成站点 base（demo 部署在 /demo/ 下）", () => {
    expect(resolveDemoAssetUrl("/demo-data/xiyouji/terrain.jpg", "/demo/")).toBe(
      "/demo/demo-data/xiyouji/terrain.jpg",
    )
  })

  it("根部署（base=/）时不变", () => {
    expect(resolveDemoAssetUrl("/demo-data/xiyouji/terrain.jpg", "/")).toBe(
      "/demo-data/xiyouji/terrain.jpg",
    )
  })

  it("base 缺少结尾斜杠时自动补", () => {
    expect(resolveDemoAssetUrl("/demo-data/a.png", "/demo")).toBe("/demo/demo-data/a.png")
  })

  it("非 demo-data 路径（例如桌面端资源）原样返回", () => {
    expect(resolveDemoAssetUrl("resources/terrain.png", "/demo/")).toBe("resources/terrain.png")
    expect(resolveDemoAssetUrl("/api/novels/x/map/terrain", "/demo/")).toBe("/api/novels/x/map/terrain")
  })

  it("空值返回 null", () => {
    expect(resolveDemoAssetUrl(null, "/demo/")).toBeNull()
    expect(resolveDemoAssetUrl(undefined, "/demo/")).toBeNull()
  })
})
