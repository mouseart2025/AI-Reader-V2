import { create } from "zustand"
import { getLatestAnalysisTask } from "@/api/client"
import type {
  AnalysisCostStats,
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
  costStats: AnalysisCostStats | null
  stageLabel: string | null
  failedChapters: FailedChapter[]
  ws: WebSocket | null
  /** Internal: track connected novelId for reconnect */
  _novelId: string | null
  /** Internal: reconnect attempt count */
  _reconnectAttempt: number
  /** Internal: reconnect timer */
  _reconnectTimer: ReturnType<typeof setTimeout> | null
  /** Internal: monotonic connection generation — prevents stale onclose/onmessage from affecting newer connections */
  _connGen: number

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
  costStats: null,
  stageLabel: null,
  failedChapters: [],
  ws: null,
  _novelId: null,
  _reconnectAttempt: 0,
  _reconnectTimer: null,
  _connGen: 0,

  setTask: (task) => set({ task }),

  resetProgress: () =>
    set({
      progress: 0,
      currentChapter: 0,
      totalChapters: 0,
      stats: { ...initialStats },
      costStats: null,
      stageLabel: null,
      failedChapters: [],
    }),

  connectWs: (novelId: string) => {
    const state = get()
    // Clear any pending reconnect timer
    if (state._reconnectTimer) {
      clearTimeout(state._reconnectTimer)
    }
    // Bump generation — all handlers from previous connections become stale
    const gen = state._connGen + 1
    // Close existing connection (no need to set _intentionalClose; gen check suffices)
    if (state.ws) {
      state.ws.close()
    }

    set({ _novelId: novelId, _reconnectAttempt: 0, _connGen: gen, ws: null })

    const proto = location.protocol === "https:" ? "wss:" : "ws:"
    const wsUrl = `${proto}//${location.host}/ws/analysis/${novelId}`
    const ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      // Stale connection opened after a newer one was created — close it
      if (get()._connGen !== gen) { ws.close(); return }
      set({ _reconnectAttempt: 0 })
    }

    ws.onmessage = (event) => {
      try {
        // Stale connection still delivering messages — ignore
        if (get()._connGen !== gen) return
        const msg: AnalysisWsMessage = JSON.parse(event.data)

        // Defence-in-depth: discard messages not matching expected novel
        if (msg.novel_id && msg.novel_id !== novelId) return

        const s = get()

        if (msg.type === "stage") {
          set({ stageLabel: msg.stage_label })
        } else if (msg.type === "progress") {
          set({
            currentChapter: msg.chapter,
            totalChapters: msg.total,
            progress: Math.round((msg.done / msg.total) * 100),
            stats: msg.stats,
            stageLabel: null,
            ...(msg.cost ? { costStats: msg.cost } : {}),
          })
        } else if (msg.type === "processing") {
          set({
            currentChapter: msg.chapter,
            totalChapters: msg.total,
            stageLabel: null,
          })
        } else if (msg.type === "chapter_done") {
          set({ stageLabel: null })
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
          if (msg.cost) {
            set({ costStats: msg.cost })
          }
          if (msg.status !== "running") {
            set({ stageLabel: null })
          }
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      // Stale connection closing — ignore entirely to avoid clobbering newer state
      if (get()._connGen !== gen) return

      set({ ws: null })
      const s = get()

      // Don't reconnect if task is no longer active
      const taskStatus = s.task?.status
      if (taskStatus !== "running" && taskStatus !== "paused") return

      // Auto-reconnect with exponential backoff
      const attempt = s._reconnectAttempt
      if (attempt >= MAX_RECONNECT_ATTEMPTS) {
        // Exceeded max attempts — poll REST once to sync state
        if (s._novelId) _pollTaskStatus(s._novelId, set)
        return
      }

      const delay = RECONNECT_BASE_DELAY_MS * Math.pow(2, attempt)
      const timer = setTimeout(() => {
        const current = get()
        // Stale timer from a superseded connection
        if (current._connGen !== gen) return
        const status = current.task?.status
        if (status !== "running" && status !== "paused") return
        if (!current._novelId) return

        set({ _reconnectAttempt: attempt + 1 })
        // Fetch latest task status via REST before reconnecting WS
        _pollTaskStatus(current._novelId, set).then(() => {
          // Re-check generation after async gap — user may have navigated away
          if (get()._connGen !== gen) return
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
    // Bump generation to invalidate all handlers from the current connection
    const gen = state._connGen + 1
    set({ _connGen: gen, _reconnectTimer: null, _novelId: null })
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
) {
  try {
    const { task, stats: latestStats } = await getLatestAnalysisTask(novelId)
    if (task) {
      set({ task })
      // Update progress from task's current_chapter
      if (task.status === "running" || task.status === "paused") {
        const total = task.chapter_end - task.chapter_start + 1
        const done = task.current_chapter - task.chapter_start + 1
        set({
          currentChapter: task.current_chapter,
          totalChapters: total,
          progress: Math.round((done / total) * 100),
          ...(latestStats ? { stats: latestStats } : {}),
        })
      } else if (task.status === "completed") {
        set({ progress: 100, ...(latestStats ? { stats: latestStats } : {}) })
      }
    }
  } catch {
    // REST poll failed too — leave UI as-is
  }
}
