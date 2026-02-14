import { create } from "zustand"
import type { Chapter, ChapterContent, ChapterEntity } from "@/api/types"

interface ReadingState {
  chapters: Chapter[]
  currentChapter: ChapterContent | null
  currentChapterNum: number
  entities: ChapterEntity[]
  aliasMap: Record<string, string>
  sidebarOpen: boolean
  tocSearch: string

  setChapters: (chapters: Chapter[]) => void
  setCurrentChapter: (chapter: ChapterContent | null) => void
  setCurrentChapterNum: (num: number) => void
  setEntities: (entities: ChapterEntity[]) => void
  setAliasMap: (aliasMap: Record<string, string>) => void
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  setTocSearch: (search: string) => void
  reset: () => void
}

export const useReadingStore = create<ReadingState>((set) => ({
  chapters: [],
  currentChapter: null,
  currentChapterNum: 1,
  entities: [],
  aliasMap: {},
  sidebarOpen: true,
  tocSearch: "",

  setChapters: (chapters) => set({ chapters }),
  setCurrentChapter: (chapter) => set({ currentChapter: chapter }),
  setCurrentChapterNum: (num) => set({ currentChapterNum: num }),
  setEntities: (entities) => set({ entities }),
  setAliasMap: (aliasMap) => set({ aliasMap }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setTocSearch: (search) => set({ tocSearch: search }),
  reset: () =>
    set({
      chapters: [],
      currentChapter: null,
      currentChapterNum: 1,
      entities: [],
      aliasMap: {},
      tocSearch: "",
    }),
}))
