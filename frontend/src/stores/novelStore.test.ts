import { beforeEach, describe, expect, it, vi } from "vitest"
import type { Novel } from "@/api/types"

vi.mock("@/api/client", () => ({
  fetchNovels: vi.fn(),
}))

import { fetchNovels } from "@/api/client"
import { useNovelStore } from "./novelStore"

const mockedFetchNovels = vi.mocked(fetchNovels)

function makeNovel(id: string, title = `小说${id}`): Novel {
  return {
    id,
    title,
    author: null,
    total_chapters: 10,
    total_words: 10000,
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
    is_sample: false,
    analysis_progress: 0,
    failed_count: 0,
    reading_progress: 0,
    last_opened: null,
  }
}

const initialState = useNovelStore.getState()

beforeEach(() => {
  useNovelStore.setState(initialState, true)
  vi.clearAllMocks()
})

describe("novelStore", () => {
  it("has expected initial state", () => {
    const s = useNovelStore.getState()
    expect(s.novels).toEqual([])
    expect(s.currentNovelId).toBeNull()
    expect(s.loading).toBe(false)
    expect(s.error).toBeNull()
  })

  it("setNovels / setCurrentNovelId / removeNovel transition state", () => {
    const s = useNovelStore.getState()
    s.setNovels([makeNovel("a"), makeNovel("b")])
    s.setCurrentNovelId("a")
    expect(useNovelStore.getState().novels).toHaveLength(2)
    expect(useNovelStore.getState().currentNovelId).toBe("a")

    useNovelStore.getState().removeNovel("a")
    expect(useNovelStore.getState().novels.map((n) => n.id)).toEqual(["b"])
  })

  it("fetchNovels sets loading during the request and novels on success", async () => {
    let resolveReq!: (v: { novels: Novel[] }) => void
    mockedFetchNovels.mockImplementation(
      () => new Promise((resolve) => { resolveReq = resolve }),
    )

    const promise = useNovelStore.getState().fetchNovels()
    expect(useNovelStore.getState().loading).toBe(true)
    expect(useNovelStore.getState().error).toBeNull()

    resolveReq({ novels: [makeNovel("a")] })
    await promise

    const s = useNovelStore.getState()
    expect(s.loading).toBe(false)
    expect(s.novels.map((n) => n.id)).toEqual(["a"])
    expect(s.error).toBeNull()
  })

  it("fetchNovels surfaces Error message on failure", async () => {
    mockedFetchNovels.mockRejectedValue(new Error("网络错误"))

    await useNovelStore.getState().fetchNovels()

    const s = useNovelStore.getState()
    expect(s.loading).toBe(false)
    expect(s.error).toBe("网络错误")
    expect(s.novels).toEqual([])
  })

  it("fetchNovels falls back to a generic message for non-Error rejections", async () => {
    mockedFetchNovels.mockRejectedValue("boom")

    await useNovelStore.getState().fetchNovels()

    expect(useNovelStore.getState().error).toBe("加载失败")
  })
})
