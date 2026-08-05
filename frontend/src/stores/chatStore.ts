import { create } from "zustand"
import type { ChatMessage, ChatWsIncoming, Conversation } from "@/api/types"
import {
  createConversation,
  deleteConversation,
  fetchConversations,
  fetchMessages,
} from "@/api/client"
import { isTauri, getSidecarWsUrl } from "@/api/sidecarBridge"

interface ChatState {
  // Panel state
  panelOpen: boolean
  panelHeight: number
  firstVisit: boolean

  // Conversations
  conversations: Conversation[]
  activeConversationId: string | null
  messages: ChatMessage[]

  // Streaming state
  streaming: boolean
  streamingContent: string
  streamingSources: number[]
  // Latest agent forensic step (issue #26 agent QA status frames); cleared
  // on first token / done / error
  streamingStatus: string
  // Conversation the in-flight stream belongs to (issue #55: stream state
  // must not leak into other conversations when the user switches mid-stream)
  streamingConversationId: string | null

  // WebSocket
  ws: WebSocket | null
  wsConnected: boolean

  // Actions
  togglePanel: () => void
  openPanel: () => void
  closePanel: () => void
  setPanelHeight: (h: number) => void
  markVisited: () => void
  addLocalMessage: (role: "user" | "assistant", content: string) => void

  loadConversations: (novelId: string) => Promise<void>
  newConversation: (novelId: string) => Promise<string>
  selectConversation: (conversationId: string) => Promise<void>
  removeConversation: (conversationId: string) => Promise<void>

  connectWs: (sessionId: string) => void
  disconnectWs: () => void
  sendQuestion: (novelId: string, question: string) => void

  clearMessages: () => void

  // Internal
  _appendStreamToken: (token: string) => void
  _finishStream: (sources: number[]) => void
  _addMessage: (msg: ChatMessage) => void
}

// Module-level state (not in Zustand to avoid renders)
let _msgIdCounter = 0
function nextMsgId() { return ++_msgIdCounter }

let _sessionId: string | null = null
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null
let _reconnectAttempt = 0
const _MAX_RECONNECT = 5
// Pending message to send after reconnection
let _pendingPayload: string | null = null

const FIRST_VISIT_KEY = "ai-reader-chat-first-visit"

export const useChatStore = create<ChatState>((set, get) => ({
  panelOpen: false,
  panelHeight: 400,
  firstVisit: localStorage.getItem(FIRST_VISIT_KEY) !== "0",
  conversations: [],
  activeConversationId: null,
  messages: [],
  streaming: false,
  streamingContent: "",
  streamingSources: [],
  streamingStatus: "",
  streamingConversationId: null,
  ws: null,
  wsConnected: false,

  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  openPanel: () => set({ panelOpen: true }),
  closePanel: () => set({ panelOpen: false }),
  setPanelHeight: (h) => set({ panelHeight: Math.max(200, Math.min(h, 800)) }),
  markVisited: () => {
    localStorage.setItem(FIRST_VISIT_KEY, "0")
    set({ firstVisit: false })
  },
  addLocalMessage: (role, content) => {
    const msg: ChatMessage = {
      id: nextMsgId(),
      conversation_id: "__local__",
      role,
      content,
      sources: [],
      created_at: new Date().toISOString(),
    }
    set((s) => ({ messages: [...s.messages, msg] }))
  },

  clearMessages: () => set({ messages: [], activeConversationId: null, streaming: false, streamingContent: "", streamingStatus: "", streamingConversationId: null }),

  loadConversations: async (novelId) => {
    try {
      const data = await fetchConversations(novelId)
      set({ conversations: data.conversations })
    } catch {
      /* ignore */
    }
  },

  newConversation: async (novelId) => {
    const conv = await createConversation(novelId)
    set((s) => ({
      conversations: [conv, ...s.conversations],
      activeConversationId: conv.id,
      messages: [],
    }))
    return conv.id
  },

  selectConversation: async (conversationId) => {
    set({ activeConversationId: conversationId, messages: [] })
    try {
      const data = await fetchMessages(conversationId)
      set({ messages: data.messages })
    } catch {
      /* ignore */
    }
  },

  removeConversation: async (conversationId) => {
    await deleteConversation(conversationId)
    set((s) => ({
      conversations: s.conversations.filter((c) => c.id !== conversationId),
      activeConversationId:
        s.activeConversationId === conversationId ? null : s.activeConversationId,
      messages:
        s.activeConversationId === conversationId ? [] : s.messages,
    }))
  },

  connectWs: (sessionId) => {
    const existing = get().ws
    if (existing && existing.readyState <= 1) return

    // Clear any pending reconnect timer
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer)
      _reconnectTimer = null
    }

    _sessionId = sessionId

    const wsBase = isTauri
      ? getSidecarWsUrl()
      : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`
    const ws = new WebSocket(`${wsBase}/ws/chat/${sessionId}`)

    ws.onopen = () => {
      set({ wsConnected: true })
      _reconnectAttempt = 0

      // Send any pending message that was queued during reconnection
      if (_pendingPayload) {
        const payload = _pendingPayload
        _pendingPayload = null
        ws.send(payload)
        set({ streaming: true, streamingContent: "", streamingSources: [], streamingStatus: "" })
      }
    }

    ws.onclose = () => {
      set({ wsConnected: false, ws: null })

      // Auto-reconnect if we have a session ID and haven't been intentionally disconnected
      if (_sessionId && _reconnectAttempt < _MAX_RECONNECT) {
        const delay = Math.min(1000 * 2 ** _reconnectAttempt, 16000)
        _reconnectAttempt++
        _reconnectTimer = setTimeout(() => {
          if (_sessionId) {
            get().connectWs(_sessionId)
          }
        }, delay)
      }
    }

    ws.onerror = () => {
      set({ wsConnected: false })
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as ChatWsIncoming
        switch (msg.type) {
          case "token":
            get()._appendStreamToken(msg.content)
            break
          case "status":
            // Agent forensic step (issue #26) — shown in the thinking bubble
            set({ streamingStatus: msg.content })
            break
          case "sources":
            // Sources received before "done"
            set({ streamingSources: msg.chapters })
            break
          case "done": {
            const state = get()
            const streamConvId = state.streamingConversationId
            state._finishStream(state.streamingSources)
            // Only append to the visible list if the user is still on the
            // conversation this stream belongs to; otherwise the message is
            // already persisted by the backend and will load on re-select.
            if (streamConvId && streamConvId === state.activeConversationId) {
              const assistantMsg: ChatMessage = {
                id: nextMsgId(),
                conversation_id: streamConvId,
                role: "assistant",
                content: state.streamingContent,
                sources: state.streamingSources,
                created_at: new Date().toISOString(),
              }
              state._addMessage(assistantMsg)
            }
            set({ streamingConversationId: null, streamingStatus: "" })
            break
          }
          case "error": {
            const errContent = msg.message || "请求出错，请稍后重试"
            const state = get()
            const streamConvId = state.streamingConversationId
            // Show error as an assistant message so user sees feedback —
            // but only on the conversation the failed stream belongs to.
            if (!streamConvId || streamConvId === state.activeConversationId) {
              const errMsg: ChatMessage = {
                id: nextMsgId(),
                conversation_id: streamConvId ?? state.activeConversationId ?? "",
                role: "assistant",
                content: `[错误] ${errContent}`,
                sources: [],
                created_at: new Date().toISOString(),
              }
              set((s) => ({ messages: [...s.messages, errMsg] }))
            }
            set({ streaming: false, streamingContent: "", streamingConversationId: null, streamingStatus: "" })
            break
          }
        }
      } catch {
        /* ignore parse errors */
      }
    }

    set({ ws })
  },

  disconnectWs: () => {
    // Clear reconnect state to prevent auto-reconnect
    _sessionId = null
    _pendingPayload = null
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer)
      _reconnectTimer = null
    }
    _reconnectAttempt = 0

    const ws = get().ws
    if (ws) ws.close()
    set({ ws: null, wsConnected: false })
  },

  sendQuestion: (novelId, question) => {
    const { ws, activeConversationId } = get()

    // Add user message locally first so it always appears
    const userMsg: ChatMessage = {
      id: nextMsgId(),
      conversation_id: activeConversationId ?? "",
      role: "user",
      content: question,
      sources: [],
      created_at: new Date().toISOString(),
    }

    const payload = JSON.stringify({
      novel_id: novelId,
      question,
      conversation_id: activeConversationId,
    })

    if (!ws || ws.readyState !== WebSocket.OPEN) {
      // Queue the message and reconnect — it will be sent on open
      _pendingPayload = payload
      set((s) => ({
        messages: [...s.messages, userMsg],
        streaming: true,
        streamingContent: "",
        streamingSources: [],
        streamingStatus: "",
        streamingConversationId: activeConversationId,
      }))
      // Force reconnect
      _reconnectAttempt = 0
      get().connectWs(_sessionId || `fullpage-${novelId}`)
      return
    }

    set((s) => ({
      messages: [...s.messages, userMsg],
      streaming: true,
      streamingContent: "",
      streamingSources: [],
      streamingStatus: "",
      streamingConversationId: activeConversationId,
    }))

    ws.send(payload)
  },

  _appendStreamToken: (token) =>
    set((s) => ({ streamingContent: s.streamingContent + token, streamingStatus: "" })),

  _finishStream: (_sources) =>
    set({ streaming: false }),

  _addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),
}))
