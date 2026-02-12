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

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const host = window.location.host
    const ws = new WebSocket(`${protocol}//${host}/ws/chat/${sessionId}`)

    ws.onopen = () => set({ wsConnected: true })
    ws.onclose = () => set({ wsConnected: false, ws: null })
    ws.onerror = () => set({ wsConnected: false })

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

    if (!ws || ws.readyState !== WebSocket.OPEN) {
      // Show user message + error feedback instead of silently dropping
      const errMsg: ChatMessage = {
        id: Date.now() + 1,
        conversation_id: activeConversationId ?? "",
        role: "assistant",
        content: "[连接断开] 无法发送消息，正在尝试重新连接...",
        sources: [],
        created_at: new Date().toISOString(),
      }
      set((s) => ({
        messages: [...s.messages, userMsg, errMsg],
      }))
      // Attempt to reconnect
      get().connectWs(`fullpage-${novelId}`)
      return
    }

    set((s) => ({
      messages: [...s.messages, userMsg],
      streaming: true,
      streamingContent: "",
      streamingSources: [],
    }))

    ws.send(
      JSON.stringify({
        novel_id: novelId,
        question,
        conversation_id: activeConversationId,
      }),
    )
  },

  _appendStreamToken: (token) =>
    set((s) => ({ streamingContent: s.streamingContent + token })),

  _finishStream: (_sources) =>
    set({ streaming: false }),

  _addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),
}))
