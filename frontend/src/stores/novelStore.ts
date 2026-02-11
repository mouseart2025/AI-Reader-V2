import { create } from "zustand"
import type { Novel } from "@/api/types"

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
