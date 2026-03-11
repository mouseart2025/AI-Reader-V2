/**
 * DesktopLayout — 桌面版布局，顶栏 Tab 选择器 + 小说切换
 * 通过 sidecar 连接本地 FastAPI 后端，直接复用生产页面
 */

import { useEffect, useState } from "react"
import { Outlet, useParams, useNavigate, useLocation } from "react-router-dom"
import { fetchNovels } from "@/api/client"
import { ensureSidecar } from "@/api/sidecarBridge"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import type { Novel } from "@/api/types"
import {
  BookOpen,
  GitBranch,
  Map,
  Clock,
  BookMarked,
  Users,
  Download,
  ArrowLeft,
  Settings,
  FlaskConical,
  MessageCircle,
  AlertTriangle,
} from "lucide-react"

const TABS = [
  { key: "analysis", label: "分析", Icon: FlaskConical },
  { key: "reading", label: "阅读", Icon: BookOpen },
  { key: "graph", label: "图谱", Icon: GitBranch },
  { key: "map", label: "地图", Icon: Map },
  { key: "timeline", label: "时间线", Icon: Clock },
  { key: "encyclopedia", label: "百科", Icon: BookMarked },
  { key: "factions", label: "势力", Icon: Users },
  { key: "chat", label: "问答", Icon: MessageCircle },
  { key: "conflicts", label: "冲突", Icon: AlertTriangle },
  { key: "export", label: "导出", Icon: Download },
] as const

export default function DesktopLayout() {
  const { novelId = "" } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const location = useLocation()

  const [sidecarReady, setSidecarReady] = useState(false)
  const [sidecarError, setSidecarError] = useState<string | null>(null)
  const [novels, setNovels] = useState<Novel[]>([])

  // Start sidecar on mount
  useEffect(() => {
    ensureSidecar()
      .then(() => setSidecarReady(true))
      .catch((err) => setSidecarError(err instanceof Error ? err.message : String(err)))
  }, [])

  // Load novel list once sidecar is ready
  useEffect(() => {
    if (!sidecarReady) return
    fetchNovels()
      .then((res) => setNovels(res.novels))
      .catch(() => {})
  }, [sidecarReady])

  // Determine active tab from URL
  const pathParts = location.pathname.split("/")
  const activeTab = pathParts[pathParts.length - 1] || "reading"

  // Dynamic document title
  const currentNovel = novels.find((n) => String(n.id) === novelId)
  const novelTitle = currentNovel?.title ?? novelId
  const tabLabel = TABS.find((t) => t.key === activeTab)?.label ?? ""
  useEffect(() => {
    if (novelTitle && tabLabel) {
      document.title = `${novelTitle} · ${tabLabel} — AI Reader`
    }
  }, [novelTitle, tabLabel])

  // Sidecar loading screen
  if (!sidecarReady) {
    return (
      <div className="flex h-screen flex-col items-center justify-center bg-background text-foreground">
        {sidecarError ? (
          <div className="text-center">
            <p className="text-lg font-semibold text-red-400">后端启动失败</p>
            <p className="mt-2 text-sm text-muted-foreground">{sidecarError}</p>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 rounded-md bg-blue-500 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 transition"
            >
              重试
            </button>
          </div>
        ) : (
          <div className="text-center">
            <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
            <p className="text-sm text-muted-foreground">正在启动分析引擎...</p>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      {/* Top Navigation */}
      <header className="flex items-center gap-2 border-b border-border bg-card/80 px-3 py-2 backdrop-blur sm:gap-4 sm:px-4">
        {/* Back to bookshelf */}
        <button
          onClick={() => navigate("/")}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition"
        >
          <ArrowLeft className="size-4" />
          <span>书架</span>
        </button>

        {/* Novel Selector */}
        {novels.length > 0 ? (
          <select
            value={novelId}
            onChange={(e) => navigate(`/novel/${e.target.value}/${activeTab}`)}
            className="rounded-md border border-border bg-muted px-3 py-1.5 text-sm text-foreground focus:border-blue-500 focus:outline-none"
          >
            {novels.map((n) => (
              <option key={n.id} value={n.id}>
                {n.title}
              </option>
            ))}
          </select>
        ) : (
          <span className="px-3 py-1.5 text-sm text-foreground">{novelTitle}</span>
        )}

        {/* Tab Navigation */}
        <nav className="flex gap-1">
          {TABS.map((tab) => {
            const isActive = activeTab === tab.key
            return (
              <button
                key={tab.key}
                onClick={() => navigate(`/novel/${novelId}/${tab.key}`)}
                className={`flex items-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  isActive
                    ? "bg-blue-500/20 text-blue-400"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                <tab.Icon className="size-4" />
                {tab.label}
              </button>
            )
          })}
        </nav>

        <div className="flex-1" />

        {/* Settings */}
        <button
          onClick={() => navigate("/settings")}
          className="text-muted-foreground hover:text-foreground transition"
        >
          <Settings className="size-5" />
        </button>
      </header>

      {/* Main Content */}
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>

      {/* Entity Card Drawer */}
      <EntityCardDrawer novelId={novelId} />
    </div>
  )
}
