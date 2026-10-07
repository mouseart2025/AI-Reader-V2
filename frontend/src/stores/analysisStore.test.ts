import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { AnalysisStats, AnalysisTask } from "@/api/types"

vi.mock("@/api/client", () => ({
  getLatestAnalysisTask: vi.fn(),
}))

import { getLatestAnalysisTask } from "@/api/client"
import { useAnalysisStore } from "./analysisStore"

const mockedPoll = vi.mocked(getLatestAnalysisTask)

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

  emitClose() {
    this.onclose?.()
  }
}

function makeTask(overrides: Partial<AnalysisTask> = {}): AnalysisTask {
  return {
    id: "t1",
    novel_id: "n1",
    status: "running",
    chapter_start: 1,
    chapter_end: 10,
    current_chapter: 5,
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
    ...overrides,
  }
}

const initialState = useAnalysisStore.getState()

beforeEach(() => {
  useAnalysisStore.setState(initialState, true)
  MockWebSocket.instances = []
  vi.stubGlobal("WebSocket", MockWebSocket)
  vi.clearAllMocks()
})

afterEach(() => {
  useAnalysisStore.getState().disconnectWs()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function lastWs(): MockWebSocket {
  const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1]
  if (!ws) throw new Error("no WebSocket instance created")
  return ws
}

describe("analysisStore", () => {
  it("has expected initial state", () => {
    const s = useAnalysisStore.getState()
    expect(s.task).toBeNull()
    expect(s.progress).toBe(0)
    expect(s.stats).toEqual({ entities: 0, relations: 0, events: 0 })
    expect(s.failedChapters).toEqual([])
    expect(s.retryProgress).toBeNull()
    expect(s.ws).toBeNull()
  })

  it("resetProgress clears progress-related fields", () => {
    const s = useAnalysisStore.getState()
    s.setTask(makeTask())
    useAnalysisStore.setState({
      progress: 50,
      currentChapter: 5,
      failedChapters: [{ chapter: 2, error: "boom" }],
      stageLabel: "抽取中",
    })

    useAnalysisStore.getState().resetProgress()

    const after = useAnalysisStore.getState()
    expect(after.progress).toBe(0)
    expect(after.currentChapter).toBe(0)
    expect(after.failedChapters).toEqual([])
    expect(after.stageLabel).toBeNull()
    // task 不属于进度信息，保留
    expect(after.task).not.toBeNull()
  })

  it("connectWs opens a WebSocket on the analysis endpoint", () => {
    useAnalysisStore.getState().connectWs("n1")

    expect(lastWs().url).toContain("/ws/analysis/n1")
    expect(useAnalysisStore.getState().ws).toBe(lastWs())
  })

  it("handles progress/stage messages and ignores messages for other novels", () => {
    useAnalysisStore.getState().connectWs("n1")
    const ws = lastWs()

    ws.emitMessage({ type: "stage", stage_label: "构建上下文", llm_model: "qwen3:8b", llm_provider: "ollama" })
    let s = useAnalysisStore.getState()
    expect(s.stageLabel).toBe("构建上下文")
    expect(s.llmModel).toBe("qwen3:8b")
    expect(s.llmProvider).toBe("ollama")

    const stats: AnalysisStats = { entities: 10, relations: 4, events: 7 }
    ws.emitMessage({ type: "progress", novel_id: "n1", chapter: 3, total: 10, done: 3, stats })
    s = useAnalysisStore.getState()
    expect(s.progress).toBe(30)
    expect(s.currentChapter).toBe(3)
    expect(s.totalChapters).toBe(10)
    expect(s.stats).toEqual(stats)
    expect(s.stageLabel).toBeNull()

    // novel_id 不匹配的消息被丢弃（纵深防御）
    ws.emitMessage({ type: "progress", novel_id: "other", chapter: 9, total: 10, done: 9, stats })
    s = useAnalysisStore.getState()
    expect(s.currentChapter).toBe(3)
    expect(s.progress).toBe(30)
  })

  it("tracks failed chapters and clears them on retry_success", () => {
    useAnalysisStore.getState().connectWs("n1")
    const ws = lastWs()

    ws.emitMessage({ type: "chapter_done", chapter: 2, status: "failed", error: "解析失败", error_type: "parse_error" })
    ws.emitMessage({ type: "chapter_done", chapter: 5, status: "failed", error: "超时", error_type: "timeout" })
    let s = useAnalysisStore.getState()
    expect(s.failedChapters).toHaveLength(2)
    expect(s.failedChapters[0]).toMatchObject({ chapter: 2, error: "解析失败", error_type: "parse_error" })

    // 同一章重复失败只保留最新记录
    ws.emitMessage({ type: "chapter_done", chapter: 2, status: "failed", error: "再次失败" })
    s = useAnalysisStore.getState()
    expect(s.failedChapters).toHaveLength(2)
    expect(s.failedChapters.find((fc) => fc.chapter === 2)?.error).toBe("再次失败")

    ws.emitMessage({ type: "chapter_done", chapter: 2, status: "retry_success" })
    s = useAnalysisStore.getState()
    expect(s.failedChapters.map((fc) => fc.chapter)).toEqual([5])
  })

  it("updates task status from task_status messages", () => {
    useAnalysisStore.getState().setTask(makeTask())
    useAnalysisStore.getState().connectWs("n1")

    lastWs().emitMessage({ type: "task_status", status: "paused" })

    expect(useAnalysisStore.getState().task?.status).toBe("paused")
  })

  it("auto-clears terminal map_prebuild status after the delay", () => {
    vi.useFakeTimers()
    useAnalysisStore.getState().connectWs("n1")
    const ws = lastWs()

    ws.emitMessage({ type: "map_prebuild", status: "running", stage: "layout" })
    expect(useAnalysisStore.getState().mapPrebuildStatus).toBe("running")

    ws.emitMessage({ type: "map_prebuild", status: "done", stage: "layout" })
    expect(useAnalysisStore.getState().mapPrebuildStatus).toBe("done")

    vi.advanceTimersByTime(5000)
    const s = useAnalysisStore.getState()
    expect(s.mapPrebuildStatus).toBeNull()
    expect(s.mapPrebuildStage).toBeNull()
  })

  it("ignores messages from stale (superseded) connections", () => {
    useAnalysisStore.getState().connectWs("n1")
    const first = lastWs()
    useAnalysisStore.getState().connectWs("n1")
    const second = lastWs()

    expect(first).not.toBe(second)
    expect(first.readyState).toBe(3) // 旧连接被关闭
    expect(useAnalysisStore.getState().ws).toBe(second)

    first.emitMessage({ type: "progress", chapter: 8, total: 10, done: 8, stats: { entities: 1, relations: 1, events: 1 } })
    expect(useAnalysisStore.getState().currentChapter).toBe(0)

    second.emitMessage({ type: "progress", chapter: 8, total: 10, done: 8, stats: { entities: 1, relations: 1, events: 1 } })
    expect(useAnalysisStore.getState().currentChapter).toBe(8)
  })

  it("does not reconnect after close when the task is completed", () => {
    vi.useFakeTimers()
    useAnalysisStore.getState().setTask(makeTask({ status: "completed" }))
    useAnalysisStore.getState().connectWs("n1")
    lastWs().emitClose()

    expect(useAnalysisStore.getState()._reconnectTimer).toBeNull()
    vi.advanceTimersByTime(30000)
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(mockedPoll).not.toHaveBeenCalled()
  })

  it("reconnects with REST status sync after close while running", async () => {
    vi.useFakeTimers()
    mockedPoll.mockResolvedValue({
      task: makeTask({ status: "running" }),
      stats: null,
      timing: null,
      failed_chapters: [],
    } as unknown as Awaited<ReturnType<typeof getLatestAnalysisTask>>)

    useAnalysisStore.getState().setTask(makeTask({ status: "running" }))
    useAnalysisStore.getState().connectWs("n1")
    lastWs().emitClose()

    expect(useAnalysisStore.getState().ws).toBeNull()
    expect(useAnalysisStore.getState()._reconnectTimer).not.toBeNull()

    await vi.advanceTimersByTimeAsync(1000)

    expect(mockedPoll).toHaveBeenCalledWith("n1")
    // 轮询确认仍在运行后重新建立 WS
    expect(MockWebSocket.instances.length).toBeGreaterThanOrEqual(2)
    expect(useAnalysisStore.getState().ws).toBe(lastWs())
  })

  it("disconnectWs tears down the connection and invalidates handlers", () => {
    useAnalysisStore.getState().connectWs("n1")
    const ws = lastWs()

    useAnalysisStore.getState().disconnectWs()

    const s = useAnalysisStore.getState()
    expect(s.ws).toBeNull()
    expect(s._novelId).toBeNull()
    expect(ws.readyState).toBe(3)

    // 断开后旧连接的消息不再生效
    ws.emitMessage({ type: "progress", chapter: 4, total: 10, done: 4, stats: { entities: 0, relations: 0, events: 0 } })
    expect(useAnalysisStore.getState().currentChapter).toBe(0)
  })
})
