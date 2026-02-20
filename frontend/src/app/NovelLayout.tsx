import { lazy, Suspense, useEffect, useState } from "react"
import { Outlet, useLocation, useNavigate } from "react-router-dom"
import { fetchNovel } from "@/api/client"
import type { Novel } from "@/api/types"

const FloatingChatPanel = lazy(() =>
  import("@/components/chat/FloatingChatPanel").then((m) => ({ default: m.FloatingChatPanel })),
)
import { GuidedTourBubble } from "@/components/shared/GuidedTourBubble"
import { ThemeToggle } from "@/components/shared/ThemeToggle"
import { Button } from "@/components/ui/button"
import { useTourStore, TOUR_STEPS, TOTAL_TOUR_STEPS } from "@/stores/tourStore"

const NAV_TABS = [
  { key: "analysis", label: "分析", path: "/analysis" },
  { key: "read", label: "阅读", path: "/read" },
  { key: "graph", label: "关系图", path: "/graph" },
  { key: "map", label: "地图", path: "/map" },
  { key: "timeline", label: "时间线", path: "/timeline" },
  { key: "encyclopedia", label: "百科", path: "/encyclopedia" },
  { key: "chat", label: "问答", path: "/chat" },
  { key: "conflicts", label: "冲突", path: "/conflicts" },
  { key: "export", label: "导出", path: "/export" },
] as const

// Map tour steps 1-3 to nav tab keys
const TOUR_TAB_MAP: Record<number, string> = {
  1: "graph",
  2: "map",
  3: "chat",
}

/**
 * Layout wrapper for all novel-scoped pages.
 * Provides a unified top navigation bar and floating chat panel.
 */
export function NovelLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [novel, setNovel] = useState<Novel | null>(null)
  const { currentStep, dismissed, nextStep, dismiss } = useTourStore()

  // Extract novelId from the URL: /read/abc123 → abc123
  const segments = location.pathname.split("/").filter(Boolean)
  const novelId = segments.length >= 2 ? segments[1] : undefined

  // Detect active tab from path prefix
  const activeKey = NAV_TABS.find((t) => location.pathname.startsWith(t.path))?.key ?? ""

  useEffect(() => {
    if (!novelId) return
    fetchNovel(novelId).then(setNovel).catch(() => {})
  }, [novelId])

  // Show tour bubble on a specific tab?
  const tourTabKey = novel?.is_sample && !dismissed && currentStep >= 1 && currentStep <= 3
    ? TOUR_TAB_MAP[currentStep]
    : null

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
            <div key={tab.key} className="relative">
              <Button
                variant={activeKey === tab.key ? "default" : "ghost"}
                size="xs"
                onClick={() => navigate(`${tab.path}/${novelId}`)}
              >
                {tab.label}
              </Button>
              {tourTabKey === tab.key && (
                <GuidedTourBubble
                  step={currentStep}
                  totalSteps={TOTAL_TOUR_STEPS}
                  message={TOUR_STEPS[currentStep].message}
                  onNext={nextStep}
                  onDismiss={dismiss}
                  position="bottom"
                />
              )}
            </div>
          ))}
        </nav>

        <ThemeToggle />

        <span className="text-[10px] text-muted-foreground/50 tabular-nums">
          v{__APP_VERSION__}
        </span>
      </header>

      {/* Page content */}
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>

      <Suspense><FloatingChatPanel /></Suspense>
    </div>
  )
}
