import { create } from "zustand"
import { fetchNovels as apiFetchNovels } from "@/api/client"
import type { Novel } from "@/api/types"

interface NovelState {
  novels: Novel[]
  currentNovelId: string | null
  loading: boolean
  error: string | null
  setCurrentNovelId: (id: string | null) => void
  setNovels: (novels: Novel[]) => void
  fetchNovels: () => Promise<void>
  removeNovel: (id: string) => void
}

export const useNovelStore = create<NovelState>((set) => ({
  novels: [],
  currentNovelId: null,
  loading: false,
  error: null,
  setCurrentNovelId: (id) => set({ currentNovelId: id }),
  setNovels: (novels) => set({ novels }),
  fetchNovels: async () => {
    set({ loading: true, error: null })
    try {
      const data = await apiFetchNovels()
      set({ novels: data.novels, loading: false })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "加载失败", loading: false })
    }
  },
  removeNovel: (id) =>
    set((state) => ({ novels: state.novels.filter((n) => n.id !== id) })),
}))
