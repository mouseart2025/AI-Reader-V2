import { create } from "zustand"
import { getLatestAnalysisTask } from "@/api/client"
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
  /** Internal: track connected novelId for reconnect */
  _novelId: string | null
  /** Internal: reconnect attempt count */
  _reconnectAttempt: number
  /** Internal: reconnect timer */
  _reconnectTimer: ReturnType<typeof setTimeout> | null
  /** Internal: whether disconnect was intentional */
  _intentionalClose: boolean

  setTask: (task: AnalysisTask | null) => void
  resetProgress: () => void
  connectWs: (novelId: string) => void
  disconnectWs: () => void
}

const initialStats: AnalysisStats = { entities: 0, relations: 0, events: 0 }
const MAX_RECONNECT_ATTEMPTS = 5
const RECONNECT_BASE_DELAY_MS = 1000

export const useAnalysisStore = create<AnalysisState>((set, get) => ({
  task: null,
  progress: 0,
  currentChapter: 0,
  totalChapters: 0,
  stats: { ...initialStats },
  failedChapters: [],
  ws: null,
  _novelId: null,
  _reconnectAttempt: 0,
  _reconnectTimer: null,
  _intentionalClose: false,

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
    const state = get()
    // Clear any pending reconnect timer
    if (state._reconnectTimer) {
      clearTimeout(state._reconnectTimer)
    }
    // Close existing connection
    if (state.ws) {
      state._intentionalClose = true
      state.ws.close()
    }

    set({ _novelId: novelId, _reconnectAttempt: 0, _intentionalClose: false })

    const proto = location.protocol === "https:" ? "wss:" : "ws:"
    const wsUrl = `${proto}//${location.host}/ws/analysis/${novelId}`
    const ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      // Reset reconnect counter on successful connection
      set({ _reconnectAttempt: 0 })
    }

    ws.onmessage = (event) => {
      try {
        const msg: AnalysisWsMessage = JSON.parse(event.data)
        const s = get()

        if (msg.type === "progress") {
          set({
            currentChapter: msg.chapter,
            totalChapters: msg.total,
            progress: Math.round((msg.done / msg.total) * 100),
            stats: msg.stats,
          })
        } else if (msg.type === "processing") {
          set({
            currentChapter: msg.chapter,
            totalChapters: msg.total,
          })
        } else if (msg.type === "chapter_done") {
          if (msg.status === "failed") {
            set({
              failedChapters: [
                ...s.failedChapters,
                { chapter: msg.chapter, error: msg.error ?? "Unknown error" },
              ],
            })
          }
        } else if (msg.type === "task_status") {
          const task = s.task
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
      const s = get()

      // Don't reconnect if the close was intentional or task is no longer active
      if (s._intentionalClose) return
      const taskStatus = s.task?.status
      if (taskStatus !== "running" && taskStatus !== "paused") return

      // Auto-reconnect with exponential backoff
      const attempt = s._reconnectAttempt
      if (attempt >= MAX_RECONNECT_ATTEMPTS) {
        // Exceeded max attempts — poll REST once to sync state
        _pollTaskStatus(s._novelId!, set, get)
        return
      }

      const delay = RECONNECT_BASE_DELAY_MS * Math.pow(2, attempt)
      const timer = setTimeout(() => {
        const current = get()
        // Re-check: task might have completed or user might have disconnected
        if (current._intentionalClose) return
        const status = current.task?.status
        if (status !== "running" && status !== "paused") return
        if (!current._novelId) return

        set({ _reconnectAttempt: attempt + 1 })
        // Fetch latest task status via REST before reconnecting WS
        _pollTaskStatus(current._novelId, set, get).then(() => {
          const afterPoll = get()
          const st = afterPoll.task?.status
          if (st === "running" || st === "paused") {
            afterPoll.connectWs(afterPoll._novelId!)
          }
        })
      }, delay)

      set({ _reconnectTimer: timer })
    }

    set({ ws })
  },

  disconnectWs: () => {
    const state = get()
    if (state._reconnectTimer) {
      clearTimeout(state._reconnectTimer)
    }
    set({ _intentionalClose: true, _reconnectTimer: null, _novelId: null })
    if (state.ws) {
      state.ws.close()
      set({ ws: null })
    }
  },
}))

/**
 * Poll the REST API for latest task status. Used when WS reconnect fails
 * or after reconnecting to sync state that was missed while disconnected.
 */
async function _pollTaskStatus(
  novelId: string,
  set: (partial: Partial<AnalysisState>) => void,
  get: () => AnalysisState,
) {
  try {
    const { task } = await getLatestAnalysisTask(novelId)
    if (task) {
      set({ task })
      // Update progress from task's current_chapter
      const prev = get()
      if (task.status === "running" || task.status === "paused") {
        const total = task.chapter_end - task.chapter_start + 1
        const done = task.current_chapter - task.chapter_start + 1
        set({
          currentChapter: task.current_chapter,
          totalChapters: total,
          progress: Math.round((done / total) * 100),
        })
      } else if (task.status === "completed") {
        set({ progress: 100 })
      }
    }
  } catch {
    // REST poll failed too — leave UI as-is
  }
}
