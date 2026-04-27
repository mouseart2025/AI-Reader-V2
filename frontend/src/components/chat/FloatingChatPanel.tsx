import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import Markdown from "react-markdown"
import { MessageCircle } from "lucide-react"
import { novelPath } from "@/lib/novelPaths"
import { useChatStore } from "@/stores/chatStore"
import { getQuickQuestions, matchSystemFaq } from "@/lib/systemFaq"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

const BUBBLE_CLICKED_KEY = "ai-reader-chat-bubble-clicked"

export function FloatingChatPanel() {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const { t } = useI18n()

  const {
    panelOpen,
    panelHeight,
    firstVisit,
    closePanel,
    setPanelHeight,
    markVisited,
    addLocalMessage,
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
    clearMessages,
  } = useChatStore()

  const [input, setInput] = useState("")
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const dragStartRef = useRef<{ y: number; h: number } | null>(null)
  const [showQuickQuestions, setShowQuickQuestions] = useState(true)
  const prevNovelIdRef = useRef(novelId)
  const quickQuestions = getQuickQuestions()

  // Clear messages when switching between novels
  useEffect(() => {
    if (prevNovelIdRef.current !== novelId) {
      clearMessages()
      setShowQuickQuestions(true)
      prevNovelIdRef.current = novelId
    }
  }, [novelId, clearMessages])

  // Load conversations on open (only when we have a novel)
  useEffect(() => {
    if (panelOpen && novelId) {
      loadConversations(novelId)
    }
  }, [panelOpen, novelId, loadConversations])

  // Connect WebSocket (only when we have a novel)
  useEffect(() => {
    if (panelOpen && novelId) {
      const sessionId = `panel-${novelId}`
      connectWs(sessionId)
      return () => disconnectWs()
    }
  }, [panelOpen, novelId, connectWs, disconnectWs])

  // Show welcome message on first visit
  useEffect(() => {
    if (panelOpen && firstVisit && messages.length === 0) {
      addLocalMessage(
        "assistant",
        t("chat.panel.welcome")
      )
      markVisited()
    }
  }, [panelOpen, firstVisit, messages.length, addLocalMessage, markVisited, t])

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

  const handleSend = useCallback(async (text?: string) => {
    const question = (text ?? input).trim()
    if (!question || streaming) return

    setShowQuickQuestions(false)

    // Branch 1: Try FAQ match
    const faqResult = matchSystemFaq(question)
    if (faqResult && faqResult.confidence >= 0.8) {
      addLocalMessage("user", question)
      // Small delay to feel natural
      setTimeout(() => {
        addLocalMessage("assistant", faqResult.answer)
      }, 200)
      if (!text) setInput("")
      return
    }

    // Branch 2: Has novelId → WebSocket novel QA
    if (novelId) {
      let convId = activeConversationId
      if (!convId) {
        convId = await newConversation(novelId)
      }
      sendQuestion(novelId, question)
      if (!text) setInput("")
      return
    }

    // Branch 3: No novelId, FAQ low confidence → friendly tip
    if (faqResult) {
      // Low confidence FAQ still useful when no novel context
      addLocalMessage("user", question)
      setTimeout(() => {
        addLocalMessage("assistant", faqResult.answer)
      }, 200)
    } else {
      addLocalMessage("user", question)
      setTimeout(() => {
        addLocalMessage(
          "assistant",
          t("chat.panel.noNovelTip")
        )
      }, 200)
    }
    if (!text) setInput("")
  }, [input, novelId, streaming, activeConversationId, newConversation, sendQuestion, addLocalMessage, t])

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

  const [bubbleClicked, setBubbleClicked] = useState(
    () => localStorage.getItem(BUBBLE_CLICKED_KEY) === "1"
  )

  const handleBubbleClick = useCallback(() => {
    localStorage.setItem(BUBBLE_CLICKED_KEY, "1")
    setBubbleClicked(true)
    useChatStore.getState().openPanel()
  }, [])

  if (!panelOpen) {
    return (
      <button
        onClick={handleBubbleClick}
        className="fixed bottom-6 right-6 z-50 flex items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg hover:bg-primary/90 transition h-12 w-12"
        title={t("chat.aiAssistantShortcut")}
      >
        <MessageCircle className="h-5 w-5" />
        {!bubbleClicked && (
          <span className="absolute inset-0 rounded-full bg-primary animate-ping opacity-30" />
        )}
      </button>
    )
  }

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
        <span className="text-sm font-medium">{t("chat.aiAssistant")}</span>

        {/* Conversation selector — only when novel is open */}
        {novelId && conversations.length > 0 && (
          <select
            className="text-xs border rounded px-2 py-1 bg-background"
            value={activeConversationId ?? ""}
            onChange={(e) => {
              if (e.target.value) selectConversation(e.target.value)
            }}
          >
            <option value="">{t("chat.selectConversation")}</option>
            {conversations.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title} ({c.message_count ?? 0})
              </option>
            ))}
          </select>
        )}

        {novelId && (
          <button
            className="text-xs text-primary hover:underline"
            onClick={() => newConversation(novelId)}
          >
            {t("chat.newConversation")}
          </button>
        )}

        <div className="flex-1" />

        {/* Mode indicator */}
        {!novelId && (
          <span className="text-[10px] text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
            {t("chat.systemHelpMode")}
          </span>
        )}

        {/* Expand to full page */}
        {novelId && (
          <button
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => {
              closePanel()
              navigate(novelPath(novelId!, "chat"))
            }}
          >
            {t("chat.fullPage")}
          </button>
        )}

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
            {novelId ? t("chat.emptyNovelPrompt") : t("chat.emptySystemPrompt")}
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
                <span className="text-[10px] text-muted-foreground">{t("chat.sources")}</span>
                {msg.sources.map((ch) => (
                  <span
                    key={ch}
                    className="text-[10px] px-1 py-0.5 rounded bg-muted text-muted-foreground"
                  >
                    {t("chat.chapterRef", { chapter: ch })}
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

      {/* Quick questions */}
      {showQuickQuestions && messages.length <= 1 && (
        <div className="flex-shrink-0 border-t px-4 py-2">
          <div className="flex flex-wrap gap-1.5">
            {quickQuestions.map((q) => (
              <button
                key={q}
                className="rounded-full border px-3 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition"
                onClick={() => handleSend(q)}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input */}
      <div className="flex-shrink-0 border-t px-4 py-2">
        <div className="flex gap-2">
          <textarea
            className="flex-1 resize-none rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            rows={1}
            placeholder={novelId ? t("chat.panel.inputNovelPlaceholder") : t("chat.panel.inputSystemPlaceholder")}
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
            onClick={() => handleSend()}
            disabled={!input.trim() || streaming}
          >
            {t("chat.send")}
          </button>
        </div>
      </div>
    </div>
  )
}
