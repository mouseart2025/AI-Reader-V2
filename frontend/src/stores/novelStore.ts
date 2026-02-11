import { create } from "zustand"

interface Novel {
  id: string
  title: string
  author: string
  totalChapters: number
}

interface NovelState {
  novels: Novel[]
  currentNovelId: string | null
  setCurrentNovelId: (id: string | null) => void
  setNovels: (novels: Novel[]) => void
}

export const useNovelStore = create<NovelState>((set) => ({
  novels: [],
  currentNovelId: null,
  setCurrentNovelId: (id) => set({ currentNovelId: id }),
  setNovels: (novels) => set({ novels }),
}))
