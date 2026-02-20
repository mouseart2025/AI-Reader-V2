import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import Markdown from "react-markdown"
import { exportConversationUrl } from "@/api/client"
import { useChatStore } from "@/stores/chatStore"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export default function ChatPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const [input, setInput] = useState("")
  const [sidebarOpen, setSidebarOpen] = useState(true)

  const {
    conversations,
    activeConversationId,
    messages,
    streaming,
    streamingContent,
    loadConversations,
    newConversation,
    selectConversation,
    removeConversation,
    connectWs,
    disconnectWs,
    sendQuestion,
  } = useChatStore()

  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Load conversations
  useEffect(() => {
    if (!novelId) return
    loadConversations(novelId)
  }, [novelId, loadConversations])

  // Connect WebSocket
  useEffect(() => {
    if (!novelId) return
    connectWs(`fullpage-${novelId}`)
    return () => disconnectWs()
  }, [novelId, connectWs, disconnectWs])

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, streamingContent])

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

  function renderContent(text: string, role: "user" | "assistant") {
    if (role === "user") {
      return <p className="whitespace-pre-wrap">{text}</p>
    }
    return <div className="prose prose-sm dark:prose-invert max-w-none break-words"><Markdown>{text}</Markdown></div>
  }

  return (
    <div className="flex h-full">
      {/* Sidebar: conversation list */}
      {sidebarOpen && (
        <div className="w-64 flex-shrink-0 border-r flex flex-col">
          <div className="px-3 py-2">
            <Button
              variant="outline"
              size="xs"
              className="w-full"
              onClick={() => novelId && newConversation(novelId)}
            >
              + 新建对话
            </Button>
          </div>

          <div className="flex-1 overflow-auto">
            {conversations.map((conv) => (
              <div
                key={conv.id}
                className={cn(
                  "group flex items-center gap-2 px-3 py-2 text-sm cursor-pointer hover:bg-muted/50",
                  activeConversationId === conv.id && "bg-muted",
                )}
                onClick={() => selectConversation(conv.id)}
              >
                <span className="flex-1 truncate">{conv.title}</span>
                <span className="text-[10px] text-muted-foreground">
                  {conv.message_count ?? 0}
                </span>
                <button
                  className="text-muted-foreground hover:text-red-500 opacity-0 group-hover:opacity-100 text-xs"
                  onClick={(e) => {
                    e.stopPropagation()
                    removeConversation(conv.id)
                  }}
                >
                  ✕
                </button>
              </div>
            ))}

            {conversations.length === 0 && (
              <p className="text-muted-foreground text-xs text-center py-4">
                暂无对话
              </p>
            )}
          </div>
        </div>
      )}

      {/* Main chat area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 border-b px-4 py-2 flex-shrink-0">
          <button
            className="text-muted-foreground hover:text-foreground text-sm"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            {sidebarOpen ? "◁" : "▷"}
          </button>
          <span className="text-sm font-medium">智能问答</span>
          {activeConversationId && (
            <span className="text-xs text-muted-foreground">
              {conversations.find((c) => c.id === activeConversationId)?.title}
            </span>
          )}
          <div className="flex-1" />
          {activeConversationId && messages.length > 0 && (
            <Button
              variant="ghost"
              size="xs"
              className="text-muted-foreground text-[11px]"
              onClick={() => window.open(exportConversationUrl(activeConversationId), "_blank")}
            >
              导出
            </Button>
          )}
          <span className="text-[10px] text-muted-foreground">
            Cmd/Ctrl+K 快速问答
          </span>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-auto px-6 py-4 space-y-4">
          {messages.length === 0 && !streaming && (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-2">
              <p className="text-lg">向小说提问</p>
              <p className="text-sm">基于已分析的章节内容回答</p>
              <div className="mt-4 flex flex-wrap gap-2 max-w-md justify-center">
                {[
                  "主角是谁？有什么能力？",
                  "主要角色之间的关系是什么？",
                  "故事发生在什么地方？",
                  "目前剧情发展到了什么阶段？",
                ].map((q) => (
                  <button
                    key={q}
                    className="text-xs border rounded-full px-3 py-1.5 hover:bg-muted transition-colors"
                    onClick={() => setInput(q)}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              className={cn(
                "max-w-[75%]",
                msg.role === "user" ? "ml-auto" : "mr-auto",
              )}
            >
              <div className="flex items-start gap-2">
                {msg.role === "assistant" && (
                  <div className="size-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <span className="text-xs text-primary">AI</span>
                  </div>
                )}
                <div
                  className={cn(
                    "rounded-lg px-4 py-2.5",
                    msg.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted",
                  )}
                >
                  {renderContent(msg.content, msg.role)}
                </div>
              </div>

              {msg.role === "assistant" && msg.sources.length > 0 && (
                <div className="mt-1 ml-9 flex items-center gap-1.5 flex-wrap">
                  <span className="text-[10px] text-muted-foreground">来源:</span>
                  {msg.sources.map((ch) => (
                    <button
                      key={ch}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-muted hover:bg-muted/80 text-muted-foreground"
                      onClick={() => navigate(`/read/${novelId}?chapter=${ch}`)}
                    >
                      第{ch}章
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}

          {/* Streaming */}
          {streaming && streamingContent && (
            <div className="max-w-[75%] mr-auto">
              <div className="flex items-start gap-2">
                <div className="size-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <span className="text-xs text-primary">AI</span>
                </div>
                <div className="rounded-lg px-4 py-2.5 bg-muted">
                  <div className="prose prose-sm dark:prose-invert max-w-none break-words"><Markdown>{streamingContent}</Markdown></div>
                  <span className="inline-block w-1.5 h-4 bg-foreground/50 animate-pulse ml-0.5" />
                </div>
              </div>
            </div>
          )}

          {streaming && !streamingContent && (
            <div className="max-w-[75%] mr-auto">
              <div className="flex items-start gap-2">
                <div className="size-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <span className="text-xs text-primary">AI</span>
                </div>
                <div className="rounded-lg px-4 py-2.5 bg-muted">
                  <span className="text-sm text-muted-foreground animate-pulse">
                    正在思考...
                  </span>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="flex-shrink-0 border-t px-6 py-3">
          <div className="flex gap-3 max-w-3xl mx-auto">
            <textarea
              className="flex-1 resize-none rounded-lg border bg-background px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              rows={2}
              placeholder="输入问题... (Enter 发送, Shift+Enter 换行)"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={streaming}
            />
            <Button
              className="self-end"
              size="sm"
              onClick={handleSend}
              disabled={!input.trim() || streaming}
            >
              发送
            </Button>
          </div>
        </div>
      </div>

      {novelId && <EntityCardDrawer novelId={novelId} />}
    </div>
  )
}
