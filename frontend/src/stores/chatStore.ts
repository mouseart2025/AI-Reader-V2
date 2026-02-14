import { create } from "zustand"
import type { ChatMessage, ChatWsIncoming, Conversation } from "@/api/types"
import {
  createConversation,
  deleteConversation,
  fetchConversations,
  fetchMessages,
} from "@/api/client"

interface ChatState {
  // Panel state
  panelOpen: boolean
  panelHeight: number

  // Conversations
  conversations: Conversation[]
  activeConversationId: string | null
  messages: ChatMessage[]

  // Streaming state
  streaming: boolean
  streamingContent: string
  streamingSources: number[]

  // WebSocket
  ws: WebSocket | null
  wsConnected: boolean

  // Actions
  togglePanel: () => void
  openPanel: () => void
  closePanel: () => void
  setPanelHeight: (h: number) => void

  loadConversations: (novelId: string) => Promise<void>
  newConversation: (novelId: string) => Promise<string>
  selectConversation: (conversationId: string) => Promise<void>
  removeConversation: (conversationId: string) => Promise<void>

  connectWs: (sessionId: string) => void
  disconnectWs: () => void
  sendQuestion: (novelId: string, question: string) => void

  // Internal
  _appendStreamToken: (token: string) => void
  _finishStream: (sources: number[]) => void
  _addMessage: (msg: ChatMessage) => void
}

// Module-level state for reconnection (not in Zustand to avoid renders)
let _sessionId: string | null = null
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null
let _reconnectAttempt = 0
const _MAX_RECONNECT = 5
// Pending message to send after reconnection
let _pendingPayload: string | null = null

export const useChatStore = create<ChatState>((set, get) => ({
  panelOpen: false,
  panelHeight: 400,
  conversations: [],
  activeConversationId: null,
  messages: [],
  streaming: false,
  streamingContent: "",
  streamingSources: [],
  ws: null,
  wsConnected: false,

  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  openPanel: () => set({ panelOpen: true }),
  closePanel: () => set({ panelOpen: false }),
  setPanelHeight: (h) => set({ panelHeight: Math.max(200, Math.min(h, 800)) }),

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

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const host = window.location.host
    const ws = new WebSocket(`${protocol}//${host}/ws/chat/${sessionId}`)

    ws.onopen = () => {
      set({ wsConnected: true })
      _reconnectAttempt = 0

      // Send any pending message that was queued during reconnection
      if (_pendingPayload) {
        const payload = _pendingPayload
        _pendingPayload = null
        ws.send(payload)
        set({ streaming: true, streamingContent: "", streamingSources: [] })
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
          case "sources":
            // Sources received before "done"
            set({ streamingSources: msg.chapters })
            break
          case "done": {
            const state = get()
            const assistantMsg: ChatMessage = {
              id: Date.now(),
              conversation_id: state.activeConversationId ?? "",
              role: "assistant",
              content: state.streamingContent,
              sources: state.streamingSources,
              created_at: new Date().toISOString(),
            }
            state._finishStream(state.streamingSources)
            state._addMessage(assistantMsg)
            break
          }
          case "error": {
            const errContent = msg.message || "请求出错，请稍后重试"
            const state = get()
            // Show error as an assistant message so user sees feedback
            const errMsg: ChatMessage = {
              id: Date.now(),
              conversation_id: state.activeConversationId ?? "",
              role: "assistant",
              content: `[错误] ${errContent}`,
              sources: [],
              created_at: new Date().toISOString(),
            }
            set((s) => ({
              streaming: false,
              streamingContent: "",
              messages: [...s.messages, errMsg],
            }))
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
      id: Date.now(),
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
    }))

    ws.send(payload)
  },

  _appendStreamToken: (token) =>
    set((s) => ({ streamingContent: s.streamingContent + token })),

  _finishStream: (_sources) =>
    set({ streaming: false }),

  _addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),
}))
