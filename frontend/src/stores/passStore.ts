import { create } from "zustand"
import {
  cancelPass,
  deletePass,
  fetchPasses,
  pausePass,
  resumePass,
  startPass,
} from "@/api/client"
import { isTauri, getSidecarWsUrl, sidecarWsQuery } from "@/api/sidecarBridge"
import type { AnalysisPass, AnalysisStats, PassStatus, PassWsMessage } from "@/api/types"

interface PassProgress {
  chapter: number
  total: number
  done: number
  stats: AnalysisStats
}

interface PassState {
  novelId: string | null
  passes: AnalysisPass[]
  /** pass_id → 实时进度(WS pass_progress 驱动;无 WS 时由 REST current_chapter 兜底) */
  progressById: Record<string, PassProgress>
  loading: boolean
  error: string | null
  ws: WebSocket | null
  /** Internal: monotonic connection generation(防旧连接的回调污染新状态) */
  _connGen: number
  _reconnectAttempt: number
  _reconnectTimer: ReturnType<typeof setTimeout> | null

  load: (novelId: string) => Promise<void>
  connectWs: (novelId: string) => void
  disconnectWs: () => void
  start: (novelId: string, modelOverride?: string | null) => Promise<void>
  pause: (passId: string) => Promise<void>
  resume: (passId: string) => Promise<void>
  cancel: (passId: string) => Promise<void>
  remove: (passId: string) => Promise<void>
  clearError: () => void
}

const MAX_RECONNECT_ATTEMPTS = 5

function hasActivePass(passes: AnalysisPass[]): boolean {
  return passes.some((p) => p.status === "running" || p.status === "paused")
}

export const usePassStore = create<PassState>((set, get) => ({
  novelId: null,
  passes: [],
  progressById: {},
  loading: false,
  error: null,
  ws: null,
  _connGen: 0,
  _reconnectAttempt: 0,
  _reconnectTimer: null,

  load: async (novelId) => {
    set({ loading: true, error: null, novelId })
    try {
      const { passes } = await fetchPasses(novelId)
      set({ passes, loading: false })
      // 有活动 pass 时确保 WS 已连接(页面刷新/重新进入的场景)
      if (hasActivePass(passes)) get().connectWs(novelId)
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) })
    }
  },

  connectWs: (novelId) => {
    const state = get()
    if (state._reconnectTimer) clearTimeout(state._reconnectTimer)
    const gen = state._connGen + 1
    if (state.ws) state.ws.close()

    set({ novelId, _connGen: gen, ws: null, _reconnectTimer: null })

    const wsBase = isTauri
      ? getSidecarWsUrl()
      : `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`
    const ws = new WebSocket(`${wsBase}/ws/analysis/${novelId}${sidecarWsQuery()}`)

    ws.onmessage = (event) => {
      try {
        if (get()._connGen !== gen) return
        const msg = JSON.parse(event.data) as PassWsMessage
        if (msg.novel_id && msg.novel_id !== novelId) return
        const pid = "pass_id" in msg ? msg.pass_id : null
        if (!pid) return

        if (msg.type === "pass_progress") {
          set({
            progressById: {
              ...get().progressById,
              [pid]: {
                chapter: msg.chapter,
                total: msg.total,
                done: msg.done,
                stats: msg.stats,
              },
            },
          })
        } else if (msg.type === "pass_status") {
          const status = msg.status as PassStatus
          set({
            passes: get().passes.map((p) =>
              p.id === pid ? { ...p, status } : p,
            ),
          })
          // 终态:刷新列表(history 埋点/完成时间等以服务端为准)
          if (status !== "running" && status !== "paused") {
            void get().load(novelId)
          }
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      if (get()._connGen !== gen) return
      set({ ws: null })
      const s = get()
      if (!hasActivePass(s.passes)) return
      const attempt = s._reconnectAttempt
      if (attempt >= MAX_RECONNECT_ATTEMPTS) return
      set({ _reconnectAttempt: attempt + 1 })
      const timer = setTimeout(() => {
        const cur = get()
        if (cur._connGen !== gen || !cur.novelId) return
        // REST 对齐状态;有活动 pass 时 load 内部会重新 connectWs
        void cur.load(cur.novelId)
      }, 1000 * Math.pow(2, attempt))
      set({ _reconnectTimer: timer })
    }

    set({ ws, _reconnectAttempt: 0 })
  },

  disconnectWs: () => {
    const state = get()
    if (state._reconnectTimer) clearTimeout(state._reconnectTimer)
    set({
      _connGen: state._connGen + 1,
      _reconnectTimer: null,
      novelId: null,
      progressById: {},
    })
    if (state.ws) {
      state.ws.close()
      set({ ws: null })
    }
  },

  start: async (novelId, modelOverride) => {
    set({ error: null })
    try {
      await startPass(novelId, { model_override: modelOverride ?? null })
      await get().load(novelId)
      get().connectWs(novelId)
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) })
      throw e
    }
  },

  pause: async (passId) => {
    const { novelId } = get()
    if (!novelId) return
    try {
      await pausePass(novelId, passId)
      set({
        passes: get().passes.map((p) =>
          p.id === passId ? { ...p, status: "paused" } : p,
        ),
      })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) })
    }
  },

  resume: async (passId) => {
    const { novelId } = get()
    if (!novelId) return
    try {
      await resumePass(novelId, passId)
      set({
        passes: get().passes.map((p) =>
          p.id === passId ? { ...p, status: "running" } : p,
        ),
      })
      get().connectWs(novelId)
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) })
    }
  },

  cancel: async (passId) => {
    const { novelId } = get()
    if (!novelId) return
    try {
      await cancelPass(novelId, passId)
      set({
        passes: get().passes.map((p) =>
          p.id === passId ? { ...p, status: "cancelled" } : p,
        ),
      })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) })
    }
  },

  remove: async (passId) => {
    const { novelId } = get()
    if (!novelId) return
    try {
      await deletePass(novelId, passId)
      set({ passes: get().passes.filter((p) => p.id !== passId) })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) })
    }
  },

  clearError: () => set({ error: null }),
}))
