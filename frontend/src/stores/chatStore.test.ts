import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Conversation } from "@/api/types"

vi.mock("@/api/client", () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  fetchConversations: vi.fn(),
  fetchMessages: vi.fn(),
}))

import {
  createConversation,
  deleteConversation,
  fetchConversations,
  fetchMessages,
} from "@/api/client"
import { useChatStore } from "./chatStore"

const mockedFetchConversations = vi.mocked(fetchConversations)
const mockedCreateConversation = vi.mocked(createConversation)
const mockedFetchMessages = vi.mocked(fetchMessages)
const mockedDeleteConversation = vi.mocked(deleteConversation)

class MockWebSocket {
  static readonly OPEN = 1
  static instances: MockWebSocket[] = []

  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 0
  sent: string[] = []
  url: string

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = 3
  }

  emitOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }

  emitMessage(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
}

function makeConversation(id: string): Conversation {
  return {
    id,
    novel_id: "n1",
    title: `会话${id}`,
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
  }
}

const initialState = useChatStore.getState()

beforeEach(() => {
  localStorage.clear()
  useChatStore.setState(initialState, true)
  MockWebSocket.instances = []
  vi.stubGlobal("WebSocket", MockWebSocket)
  vi.clearAllMocks()
})

afterEach(() => {
  useChatStore.getState().disconnectWs()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function lastWs(): MockWebSocket {
  const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1]
  if (!ws) throw new Error("no WebSocket instance created")
  return ws
}

describe("chatStore", () => {
  it("panel actions and height clamping", () => {
    const s = useChatStore.getState()
    expect(s.panelOpen).toBe(false)

    s.togglePanel()
    expect(useChatStore.getState().panelOpen).toBe(true)
    useChatStore.getState().closePanel()
    expect(useChatStore.getState().panelOpen).toBe(false)
    useChatStore.getState().openPanel()
    expect(useChatStore.getState().panelOpen).toBe(true)

    useChatStore.getState().setPanelHeight(100)
    expect(useChatStore.getState().panelHeight).toBe(200)
    useChatStore.getState().setPanelHeight(9999)
    expect(useChatStore.getState().panelHeight).toBe(800)
    useChatStore.getState().setPanelHeight(400)
    expect(useChatStore.getState().panelHeight).toBe(400)
  })

  it("markVisited persists the first-visit flag", () => {
    useChatStore.getState().markVisited()

    expect(useChatStore.getState().firstVisit).toBe(false)
    expect(localStorage.getItem("ai-reader-chat-first-visit")).toBe("0")
  })

  it("addLocalMessage appends a local message; clearMessages resets", () => {
    useChatStore.getState().addLocalMessage("user", "你好")
    useChatStore.getState().addLocalMessage("assistant", "你好！有什么可以帮你？")

    let s = useChatStore.getState()
    expect(s.messages).toHaveLength(2)
    expect(s.messages[0]).toMatchObject({ role: "user", content: "你好" })
    expect(s.messages[1].id).toBeGreaterThan(s.messages[0].id)

    useChatStore.getState().clearMessages()
    s = useChatStore.getState()
    expect(s.messages).toEqual([])
    expect(s.streaming).toBe(false)
  })

  it("loadConversations populates the list and ignores failures", async () => {
    mockedFetchConversations.mockResolvedValue({
      conversations: [makeConversation("c1")],
    })
    await useChatStore.getState().loadConversations("n1")
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(["c1"])

    mockedFetchConversations.mockRejectedValue(new Error("boom"))
    await useChatStore.getState().loadConversations("n1")
    // 失败静默处理，保留旧列表
    expect(useChatStore.getState().conversations).toHaveLength(1)
  })

  it("newConversation prepends and activates the new conversation", async () => {
    useChatStore.setState({ conversations: [makeConversation("c1")] })
    mockedCreateConversation.mockResolvedValue(makeConversation("c2"))

    const id = await useChatStore.getState().newConversation("n1")

    const s = useChatStore.getState()
    expect(id).toBe("c2")
    expect(s.conversations.map((c) => c.id)).toEqual(["c2", "c1"])
    expect(s.activeConversationId).toBe("c2")
    expect(s.messages).toEqual([])
  })

  it("selectConversation loads messages; failure keeps an empty list", async () => {
    mockedFetchMessages.mockResolvedValue({
      messages: [
        { id: 1, conversation_id: "c1", role: "user", content: "问题", sources: [], created_at: "2026-01-01" },
      ],
      conversation: makeConversation("c1"),
    })
    await useChatStore.getState().selectConversation("c1")
    let s = useChatStore.getState()
    expect(s.activeConversationId).toBe("c1")
    expect(s.messages).toHaveLength(1)

    mockedFetchMessages.mockRejectedValue(new Error("boom"))
    await useChatStore.getState().selectConversation("c2")
    s = useChatStore.getState()
    expect(s.activeConversationId).toBe("c2")
    expect(s.messages).toEqual([])
  })

  it("removeConversation deletes and clears active state when active", async () => {
    useChatStore.setState({
      conversations: [makeConversation("c1"), makeConversation("c2")],
      activeConversationId: "c1",
      messages: [
        { id: 1, conversation_id: "c1", role: "user", content: "x", sources: [], created_at: "2026-01-01" },
      ],
    })
    mockedDeleteConversation.mockResolvedValue({ ok: true })

    await useChatStore.getState().removeConversation("c1")

    const s = useChatStore.getState()
    expect(s.conversations.map((c) => c.id)).toEqual(["c2"])
    expect(s.activeConversationId).toBeNull()
    expect(s.messages).toEqual([])
  })

  it("streams a full answer over WebSocket: token → sources → done", () => {
    useChatStore.setState({ activeConversationId: "c1" })
    useChatStore.getState().connectWs("sess1")
    const ws = lastWs()
    ws.emitOpen()
    expect(useChatStore.getState().wsConnected).toBe(true)

    useChatStore.getState().sendQuestion("n1", "孙悟空是谁？")
    let s = useChatStore.getState()
    expect(s.streaming).toBe(true)
    expect(s.streamingConversationId).toBe("c1")
    expect(s.messages).toHaveLength(1)
    expect(s.messages[0]).toMatchObject({ role: "user", content: "孙悟空是谁？" })

    const payload = JSON.parse(ws.sent[0])
    expect(payload).toMatchObject({ novel_id: "n1", question: "孙悟空是谁？", conversation_id: "c1" })

    ws.emitMessage({ type: "status", content: "检索相关章节" })
    expect(useChatStore.getState().streamingStatus).toBe("检索相关章节")

    ws.emitMessage({ type: "token", content: "孙悟空是" })
    ws.emitMessage({ type: "token", content: "唐僧的徒弟。" })
    s = useChatStore.getState()
    expect(s.streamingContent).toBe("孙悟空是唐僧的徒弟。")
    expect(s.streamingStatus).toBe("") // 第一个 token 到达后清除状态

    ws.emitMessage({ type: "sources", chapters: [1, 3] })
    ws.emitMessage({ type: "done" })

    s = useChatStore.getState()
    expect(s.streaming).toBe(false)
    expect(s.streamingConversationId).toBeNull()
    expect(s.messages).toHaveLength(2)
    expect(s.messages[1]).toMatchObject({
      role: "assistant",
      content: "孙悟空是唐僧的徒弟。",
      sources: [1, 3],
      conversation_id: "c1",
    })
  })

  it("does not leak a finished stream into a conversation the user switched to", () => {
    useChatStore.setState({ activeConversationId: "c1" })
    useChatStore.getState().connectWs("sess1")
    const ws = lastWs()
    ws.emitOpen()

    useChatStore.getState().sendQuestion("n1", "问题")
    ws.emitMessage({ type: "token", content: "回答内容" })

    // 用户在流式回答进行中切换到另一个会话
    useChatStore.setState({ activeConversationId: "c2", messages: [] })
    ws.emitMessage({ type: "done" })

    const s = useChatStore.getState()
    expect(s.streaming).toBe(false)
    // 回答没有追加到当前可见会话（后端已持久化，重新选择时会加载）
    expect(s.messages).toEqual([])
  })

  it("shows an error frame as an assistant message on the owning conversation", () => {
    useChatStore.setState({ activeConversationId: "c1" })
    useChatStore.getState().connectWs("sess1")
    const ws = lastWs()
    ws.emitOpen()

    useChatStore.getState().sendQuestion("n1", "问题")
    ws.emitMessage({ type: "error", message: "LLM 服务不可用" })

    const s = useChatStore.getState()
    expect(s.streaming).toBe(false)
    expect(s.messages).toHaveLength(2)
    expect(s.messages[1]).toMatchObject({
      role: "assistant",
      content: "[错误] LLM 服务不可用",
    })
  })

  it("queues the question and reconnects when the socket is not open", () => {
    useChatStore.setState({ activeConversationId: "c1" })

    useChatStore.getState().sendQuestion("n1", "离线问题")

    // 本地立即显示用户消息并进入流式状态
    let s = useChatStore.getState()
    expect(s.messages).toHaveLength(1)
    expect(s.streaming).toBe(true)

    // 自动建立新连接，onopen 后补发排队的 payload
    const ws = lastWs()
    expect(ws.url).toContain("/ws/chat/")
    ws.emitOpen()

    s = useChatStore.getState()
    expect(ws.sent).toHaveLength(1)
    expect(JSON.parse(ws.sent[0])).toMatchObject({ novel_id: "n1", question: "离线问题" })
    expect(s.streaming).toBe(true)
    expect(s.wsConnected).toBe(true)
  })

  it("disconnectWs prevents auto-reconnect", () => {
    vi.useFakeTimers()
    useChatStore.getState().connectWs("sess1")
    const ws = lastWs()
    ws.emitOpen()

    useChatStore.getState().disconnectWs()
    ws.onclose?.()

    vi.advanceTimersByTime(60000)
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(useChatStore.getState().wsConnected).toBe(false)
  })
})
