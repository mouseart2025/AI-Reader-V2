import { beforeEach, describe, expect, it } from "vitest"
import type { EntityProfile } from "@/api/types"
import { useEntityCardStore } from "./entityCardStore"

const initialState = useEntityCardStore.getState()

beforeEach(() => {
  // profileCache 是可变的 Map，每次测试换一个新的避免相互污染
  useEntityCardStore.setState({ ...initialState, profileCache: new Map() }, true)
})

function makeProfile(name: string): EntityProfile {
  return { name, type: "person" } as unknown as EntityProfile
}

describe("entityCardStore", () => {
  it("has expected initial state", () => {
    const s = useEntityCardStore.getState()
    expect(s.open).toBe(false)
    expect(s.loading).toBe(false)
    expect(s.profile).toBeNull()
    expect(s.error).toBeNull()
    expect(s.breadcrumbs).toEqual([])
    expect(s.conceptPopup).toBeNull()
    expect(s.reloadNonce).toBe(0)
  })

  it("openCard opens the drawer in loading state with a single breadcrumb", () => {
    useEntityCardStore.getState().openCard("孙悟空", "person")

    const s = useEntityCardStore.getState()
    expect(s.open).toBe(true)
    expect(s.loading).toBe(true)
    expect(s.profile).toBeNull()
    expect(s.error).toBeNull()
    expect(s.breadcrumbs).toEqual([{ name: "孙悟空", type: "person" }])
  })

  it("navigateTo appends breadcrumbs and caps them at 10 entries", () => {
    useEntityCardStore.getState().openCard("第0个", "person")
    for (let i = 1; i <= 10; i++) {
      useEntityCardStore.getState().navigateTo(`第${i}个`, "person")
    }

    const s = useEntityCardStore.getState()
    expect(s.breadcrumbs).toHaveLength(10)
    // 最早的面包屑被挤出
    expect(s.breadcrumbs[0].name).toBe("第1个")
    expect(s.breadcrumbs[9].name).toBe("第10个")
    expect(s.loading).toBe(true)
  })

  it("goBack truncates breadcrumbs after the given index", () => {
    useEntityCardStore.getState().openCard("甲", "person")
    useEntityCardStore.getState().navigateTo("乙", "person")
    useEntityCardStore.getState().navigateTo("丙", "location")

    useEntityCardStore.getState().goBack(0)

    const s = useEntityCardStore.getState()
    expect(s.breadcrumbs).toEqual([{ name: "甲", type: "person" }])
    expect(s.loading).toBe(true)
  })

  it("refresh clears the profile cache and bumps reloadNonce", () => {
    const s = useEntityCardStore.getState()
    s.setCachedProfile("person", "孙悟空", makeProfile("孙悟空"))
    const nonce = s.reloadNonce

    useEntityCardStore.getState().refresh()

    const after = useEntityCardStore.getState()
    expect(after.profileCache.size).toBe(0)
    expect(after.reloadNonce).toBe(nonce + 1)
    expect(after.loading).toBe(true)
    expect(after.profile).toBeNull()
  })

  it("replaceCurrent swaps the last breadcrumb and clears the cache", () => {
    useEntityCardStore.getState().openCard("甲", "person")
    useEntityCardStore.getState().navigateTo("乙", "person")
    useEntityCardStore.getState().setCachedProfile("person", "乙", makeProfile("乙"))

    useEntityCardStore.getState().replaceCurrent("丙", "person")

    const s = useEntityCardStore.getState()
    expect(s.breadcrumbs).toEqual([
      { name: "甲", type: "person" },
      { name: "丙", type: "person" },
    ])
    expect(s.profileCache.size).toBe(0)
  })

  it("close resets the drawer state", () => {
    useEntityCardStore.getState().openCard("孙悟空", "person")
    useEntityCardStore.getState().setProfile(makeProfile("孙悟空"))

    useEntityCardStore.getState().close()

    const s = useEntityCardStore.getState()
    expect(s.open).toBe(false)
    expect(s.profile).toBeNull()
    expect(s.breadcrumbs).toEqual([])
    expect(s.loading).toBe(false)
  })

  it("concept popup opens and closes", () => {
    const data = { name: "修仙", definition: "修道成仙", category: "功法", related: ["筑基"] }
    useEntityCardStore.getState().openConceptPopup(data)
    expect(useEntityCardStore.getState().conceptPopup).toEqual(data)

    useEntityCardStore.getState().closeConceptPopup()
    expect(useEntityCardStore.getState().conceptPopup).toBeNull()
  })

  it("profile cache roundtrips and evicts the oldest entry beyond 50", () => {
    const s = useEntityCardStore.getState()
    expect(s.getCachedProfile("person", "不存在")).toBeUndefined()

    for (let i = 0; i < 50; i++) {
      useEntityCardStore.getState().setCachedProfile("person", `角色${i}`, makeProfile(`角色${i}`))
    }
    expect(useEntityCardStore.getState().profileCache.size).toBe(50)

    useEntityCardStore.getState().setCachedProfile("person", "新角色", makeProfile("新角色"))

    const after = useEntityCardStore.getState()
    expect(after.profileCache.size).toBe(50)
    expect(after.getCachedProfile("person", "角色0")).toBeUndefined()
    expect(after.getCachedProfile("person", "角色1")).toBeDefined()
    expect(after.getCachedProfile("person", "新角色")).toBeDefined()
  })

  it("re-setting an existing cache key does not trigger eviction", () => {
    for (let i = 0; i < 50; i++) {
      useEntityCardStore.getState().setCachedProfile("person", `角色${i}`, makeProfile(`角色${i}`))
    }
    useEntityCardStore.getState().setCachedProfile("person", "角色0", makeProfile("角色0-v2"))

    const after = useEntityCardStore.getState()
    expect(after.profileCache.size).toBe(50)
    expect(after.getCachedProfile("person", "角色0")?.name).toBe("角色0-v2")
  })
})
