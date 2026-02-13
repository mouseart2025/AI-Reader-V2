import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import Markdown from "react-markdown"
import { useChatStore } from "@/stores/chatStore"
import { cn } from "@/lib/utils"

export function FloatingChatPanel() {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()

  const {
    panelOpen,
    panelHeight,
    closePanel,
    setPanelHeight,
    conversations,
    activeConversationId,
    messages,
    streaming,
    streamingContent,
    loadConversations,
    newConversation,
    selectConversation,
    connectWs,
    disconnectWs,
    sendQuestion,
  } = useChatStore()

  const [input, setInput] = useState("")
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const dragStartRef = useRef<{ y: number; h: number } | null>(null)

  // Load conversations on open
  useEffect(() => {
    if (panelOpen && novelId) {
      loadConversations(novelId)
    }
  }, [panelOpen, novelId, loadConversations])

  // Connect WebSocket
  useEffect(() => {
    if (panelOpen && novelId) {
      const sessionId = `panel-${novelId}`
      connectWs(sessionId)
      return () => disconnectWs()
    }
  }, [panelOpen, novelId, connectWs, disconnectWs])

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, streamingContent])

  // Escape to close
  useEffect(() => {
    if (!panelOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePanel()
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [panelOpen, closePanel])

  // Cmd/Ctrl+K to toggle
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault()
        useChatStore.getState().togglePanel()
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])

  const handleSend = useCallback(async () => {
    if (!input.trim() || !novelId || streaming) return

    let convId = activeConversationId
    if (!convId) {
      convId = await newConversation(novelId)
    }

    sendQuestion(novelId, input.trim())
    setInput("")
  }, [input, novelId, streaming, activeConversationId, newConversation, sendQuestion])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend],
  )

  // Drag to resize
  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      dragStartRef.current = { y: e.clientY, h: panelHeight }
      const onMove = (ev: MouseEvent) => {
        if (!dragStartRef.current) return
        const delta = dragStartRef.current.y - ev.clientY
        setPanelHeight(dragStartRef.current.h + delta)
      }
      const onUp = () => {
        dragStartRef.current = null
        window.removeEventListener("mousemove", onMove)
        window.removeEventListener("mouseup", onUp)
      }
      window.addEventListener("mousemove", onMove)
      window.addEventListener("mouseup", onUp)
    },
    [panelHeight, setPanelHeight],
  )

  if (!panelOpen) return null

  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-50 flex flex-col border-t bg-background shadow-2xl"
      style={{ height: panelHeight }}
    >
      {/* Drag handle */}
      <div
        className="flex-shrink-0 h-2 cursor-ns-resize bg-muted/50 hover:bg-muted"
        onMouseDown={handleDragStart}
      />

      {/* Header */}
      <div className="flex items-center gap-3 border-b px-4 py-2 flex-shrink-0">
        <span className="text-sm font-medium">智能问答</span>

        {/* Conversation selector */}
        {conversations.length > 0 && (
          <select
            className="text-xs border rounded px-2 py-1 bg-background"
            value={activeConversationId ?? ""}
            onChange={(e) => {
              if (e.target.value) selectConversation(e.target.value)
            }}
          >
            <option value="">选择对话...</option>
            {conversations.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title} ({c.message_count ?? 0})
              </option>
            ))}
          </select>
        )}

        <button
          className="text-xs text-primary hover:underline"
          onClick={() => novelId && newConversation(novelId)}
        >
          + 新对话
        </button>

        <div className="flex-1" />

        {/* Expand to full page */}
        <button
          className="text-xs text-muted-foreground hover:text-foreground"
          onClick={() => {
            closePanel()
            navigate(`/chat/${novelId}`)
          }}
        >
          全屏
        </button>

        <button
          className="text-muted-foreground hover:text-foreground text-sm"
          onClick={closePanel}
        >
          ✕
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !streaming && (
          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
            输入问题开始对话
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={cn(
              "max-w-[80%] text-sm",
              msg.role === "user" ? "ml-auto" : "mr-auto",
            )}
          >
            <div
              className={cn(
                "rounded-lg px-3 py-2",
                msg.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted",
              )}
            >
              {msg.role === "user"
                ? <p className="whitespace-pre-wrap">{msg.content}</p>
                : <div className="prose prose-sm dark:prose-invert max-w-none break-words"><Markdown>{msg.content}</Markdown></div>
              }
            </div>
            {msg.role === "assistant" && msg.sources.length > 0 && (
              <div className="mt-1 flex items-center gap-1 flex-wrap">
                <span className="text-[10px] text-muted-foreground">来源:</span>
                {msg.sources.map((ch) => (
                  <span
                    key={ch}
                    className="text-[10px] px-1 py-0.5 rounded bg-muted text-muted-foreground"
                  >
                    第{ch}章
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}

        {/* Streaming message */}
        {streaming && streamingContent && (
          <div className="max-w-[80%] mr-auto">
            <div className="rounded-lg px-3 py-2 bg-muted">
              <div className="prose prose-sm dark:prose-invert max-w-none break-words"><Markdown>{streamingContent}</Markdown></div>
              <span className="inline-block w-1.5 h-4 bg-foreground/50 animate-pulse ml-0.5" />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 border-t px-4 py-2">
        <div className="flex gap-2">
          <textarea
            className="flex-1 resize-none rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            rows={1}
            placeholder="输入问题... (Enter 发送, Shift+Enter 换行)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={streaming}
          />
          <button
            className={cn(
              "rounded-lg px-4 py-2 text-sm font-medium",
              input.trim() && !streaming
                ? "bg-primary text-primary-foreground hover:bg-primary/90"
                : "bg-muted text-muted-foreground cursor-not-allowed",
            )}
            onClick={handleSend}
            disabled={!input.trim() || streaming}
          >
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
