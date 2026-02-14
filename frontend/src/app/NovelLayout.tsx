import { useEffect, useState } from "react"
import { Outlet, useLocation, useNavigate } from "react-router-dom"
import { fetchNovel } from "@/api/client"
import type { Novel } from "@/api/types"
import { FloatingChatPanel } from "@/components/chat/FloatingChatPanel"
import { Button } from "@/components/ui/button"

const NAV_TABS = [
  { key: "analysis", label: "分析", path: "/analysis" },
  { key: "read", label: "阅读", path: "/read" },
  { key: "graph", label: "关系图", path: "/graph" },
  { key: "map", label: "地图", path: "/map" },
  { key: "timeline", label: "时间线", path: "/timeline" },
  { key: "encyclopedia", label: "百科", path: "/encyclopedia" },
  { key: "chat", label: "问答", path: "/chat" },
] as const

/**
 * Layout wrapper for all novel-scoped pages.
 * Provides a unified top navigation bar and floating chat panel.
 */
export function NovelLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [novel, setNovel] = useState<Novel | null>(null)

  // Extract novelId from the URL: /read/abc123 → abc123
  const segments = location.pathname.split("/").filter(Boolean)
  const novelId = segments.length >= 2 ? segments[1] : undefined

  // Detect active tab from path prefix
  const activeKey = NAV_TABS.find((t) => location.pathname.startsWith(t.path))?.key ?? ""

  useEffect(() => {
    if (!novelId) return
    fetchNovel(novelId).then(setNovel).catch(() => {})
  }, [novelId])

  // Don't render the nav bar if novelId is missing (shouldn't happen in practice)
  if (!novelId) return <Outlet />

  return (
    <div className="flex h-screen flex-col">
      {/* Unified top navigation */}
      <header className="flex items-center gap-4 border-b px-4 py-1.5">
        <button
          className="text-muted-foreground text-sm hover:underline whitespace-nowrap"
          onClick={() => navigate("/")}
        >
          &larr; {novel?.title ?? "..."}
        </button>

        <div className="flex-1" />

        <nav className="flex gap-0.5">
          {NAV_TABS.map((tab) => (
            <Button
              key={tab.key}
              variant={activeKey === tab.key ? "default" : "ghost"}
              size="xs"
              onClick={() => navigate(`${tab.path}/${novelId}`)}
            >
              {tab.label}
            </Button>
          ))}
        </nav>

        <span className="text-[10px] text-muted-foreground/50 tabular-nums">
          v{__APP_VERSION__}
        </span>
      </header>

      {/* Page content */}
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>

      <FloatingChatPanel />
    </div>
  )
}
