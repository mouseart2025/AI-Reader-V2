import { create } from "zustand"
import type {
  AnalysisStats,
  AnalysisTask,
  AnalysisWsMessage,
} from "@/api/types"

interface FailedChapter {
  chapter: number
  error: string
}

interface AnalysisState {
  task: AnalysisTask | null
  progress: number // 0-100
  currentChapter: number
  totalChapters: number
  stats: AnalysisStats
  failedChapters: FailedChapter[]
  ws: WebSocket | null

  setTask: (task: AnalysisTask | null) => void
  resetProgress: () => void
  connectWs: (novelId: string) => void
  disconnectWs: () => void
}

const initialStats: AnalysisStats = { entities: 0, relations: 0, events: 0 }

export const useAnalysisStore = create<AnalysisState>((set, get) => ({
  task: null,
  progress: 0,
  currentChapter: 0,
  totalChapters: 0,
  stats: { ...initialStats },
  failedChapters: [],
  ws: null,

  setTask: (task) => set({ task }),

  resetProgress: () =>
    set({
      progress: 0,
      currentChapter: 0,
      totalChapters: 0,
      stats: { ...initialStats },
      failedChapters: [],
    }),

  connectWs: (novelId: string) => {
    const existing = get().ws
    if (existing) {
      existing.close()
    }

    const proto = location.protocol === "https:" ? "wss:" : "ws:"
    const wsUrl = `${proto}//${location.host}/ws/analysis/${novelId}`
    const ws = new WebSocket(wsUrl)

    ws.onmessage = (event) => {
      try {
        const msg: AnalysisWsMessage = JSON.parse(event.data)
        const state = get()

        if (msg.type === "progress") {
          set({
            currentChapter: msg.chapter,
            totalChapters: msg.total,
            progress: Math.round((msg.done / msg.total) * 100),
            stats: msg.stats,
          })
        } else if (msg.type === "chapter_done") {
          if (msg.status === "failed") {
            set({
              failedChapters: [
                ...state.failedChapters,
                { chapter: msg.chapter, error: msg.error ?? "Unknown error" },
              ],
            })
          }
        } else if (msg.type === "task_status") {
          const task = state.task
          if (task) {
            set({ task: { ...task, status: msg.status as AnalysisTask["status"] } })
          }
          if (msg.stats) {
            set({ stats: msg.stats })
          }
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      set({ ws: null })
    }

    set({ ws })
  },

  disconnectWs: () => {
    const ws = get().ws
    if (ws) {
      ws.close()
      set({ ws: null })
    }
  },
}))
