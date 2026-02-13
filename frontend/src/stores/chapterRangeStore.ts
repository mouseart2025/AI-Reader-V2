import { create } from "zustand"

interface ChapterRangeState {
  chapterStart: number
  chapterEnd: number
  analyzedFirst: number
  analyzedLast: number
  totalChapters: number
  /** The novelId this range belongs to — used to detect novel switches */
  novelId: string | null

  setRange: (start: number, end: number) => void
  setAnalyzedRange: (first: number, last: number) => void
  setTotalChapters: (total: number) => void
  resetToFull: () => void
  /** Reset all range state for a new novel */
  resetForNovel: (novelId: string) => void
}

export const useChapterRangeStore = create<ChapterRangeState>((set, get) => ({
  chapterStart: 1,
  chapterEnd: 1,
  analyzedFirst: 0,
  analyzedLast: 0,
  totalChapters: 0,
  novelId: null,

  setRange: (start, end) => set({ chapterStart: start, chapterEnd: end }),

  setAnalyzedRange: (first, last) => {
    const state = get()
    set({
      analyzedFirst: first,
      analyzedLast: last,
      // If range is at defaults, snap to full analyzed range
      chapterStart: state.chapterStart <= 1 ? first : state.chapterStart,
      chapterEnd: state.chapterEnd <= 1 ? last : state.chapterEnd,
    })
  },

  setTotalChapters: (total) => set({ totalChapters: total }),

  resetToFull: () => {
    const { analyzedFirst, analyzedLast } = get()
    set({ chapterStart: analyzedFirst, chapterEnd: analyzedLast })
  },

  resetForNovel: (novelId) => {
    const state = get()
    if (state.novelId === novelId) return
    set({
      novelId,
      chapterStart: 1,
      chapterEnd: 1,
      analyzedFirst: 0,
      analyzedLast: 0,
      totalChapters: 0,
    })
  },
}))
