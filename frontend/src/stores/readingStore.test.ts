import { beforeEach, describe, expect, it } from "vitest"
import type { Chapter } from "@/api/types"
import { useReadingStore } from "./readingStore"

const initialState = useReadingStore.getState()

beforeEach(() => {
  useReadingStore.setState(initialState, true)
})

describe("readingStore", () => {
  it("has expected initial state", () => {
    const s = useReadingStore.getState()
    expect(s.chapters).toEqual([])
    expect(s.currentChapter).toBeNull()
    expect(s.currentChapterNum).toBe(1)
    expect(s.entities).toEqual([])
    expect(s.aliasMap).toEqual({})
    expect(s.sidebarOpen).toBe(true)
    expect(s.tocSearch).toBe("")
  })

  it("setters update individual fields", () => {
    const s = useReadingStore.getState()
    s.setChapters([{ chapter_num: 1, title: "第一章" } as unknown as Chapter])
    s.setCurrentChapterNum(3)
    s.setAliasMap({ 猴哥: "孙悟空" })
    s.setTocSearch("天宫")

    const after = useReadingStore.getState()
    expect(after.chapters).toHaveLength(1)
    expect(after.currentChapterNum).toBe(3)
    expect(after.aliasMap).toEqual({ 猴哥: "孙悟空" })
    expect(after.tocSearch).toBe("天宫")
  })

  it("toggleSidebar flips sidebarOpen; setSidebarOpen sets it explicitly", () => {
    useReadingStore.getState().toggleSidebar()
    expect(useReadingStore.getState().sidebarOpen).toBe(false)
    useReadingStore.getState().toggleSidebar()
    expect(useReadingStore.getState().sidebarOpen).toBe(true)

    useReadingStore.getState().setSidebarOpen(false)
    expect(useReadingStore.getState().sidebarOpen).toBe(false)
  })

  it("reset clears chapter state but keeps sidebar visibility", () => {
    const s = useReadingStore.getState()
    s.setChapters([{ chapter_num: 1, title: "第一章" } as unknown as Chapter])
    s.setCurrentChapterNum(5)
    s.setAliasMap({ 猴哥: "孙悟空" })
    s.setTocSearch("abc")
    s.setSidebarOpen(false)

    useReadingStore.getState().reset()

    const after = useReadingStore.getState()
    expect(after.chapters).toEqual([])
    expect(after.currentChapter).toBeNull()
    expect(after.currentChapterNum).toBe(1)
    expect(after.aliasMap).toEqual({})
    expect(after.tocSearch).toBe("")
    // sidebarOpen 不在 reset 范围内（跨章节保持用户偏好）
    expect(after.sidebarOpen).toBe(false)
  })
})
