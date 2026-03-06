/**
 * DemoLayout — wraps demo pages with navigation bar + CTA conversion bar.
 * Provides novel selector, 7 visualization tabs, mobile gate, and upgrade banner.
 * Dark theme to match the landing page design (slate-950 + blue-500).
 */
import { useCallback, useEffect, useRef, useState } from "react"
import { Outlet, useParams, useNavigate, useLocation } from "react-router-dom"
import { DemoProvider } from "./DemoContext"
import { DemoEntityCardDrawer } from "@/components/entity-cards/DemoEntityCardDrawer"
import { getAllDemoNovels } from "@/api/demoNovelMap"

const TABS = [
  { key: "reading", label: "阅读", icon: "📃" },
  { key: "graph", label: "图谱", icon: "🕸️" },
  { key: "map", label: "地图", icon: "🗺️" },
  { key: "timeline", label: "时间线", icon: "⏳" },
  { key: "encyclopedia", label: "百科", icon: "📖" },
  { key: "factions", label: "势力", icon: "⚔️" },
  { key: "export", label: "导出", icon: "💾" },
] as const

/** Compute landing page URL from Vite base path */
const LANDING_URL = (import.meta.env.BASE_URL ?? "/").replace(/\/demo\/?$/, "/") || "/"

export default function DemoLayout() {
  const { novelSlug = "honglou" } = useParams<{ novelSlug: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const novels = getAllDemoNovels()

  // Embed mode: render only the page content, no chrome
  const isEmbed = new URLSearchParams(location.search).get("embed") === "1"

  // Determine active tab from URL path
  const pathParts = location.pathname.split("/")
  const activeTab = pathParts[pathParts.length - 1] || "reading"

  // Dynamic document title
  const novelTitle = novels.find((n) => n.slug === novelSlug)?.title ?? novelSlug
  const tabLabel = TABS.find((t) => t.key === activeTab)?.label ?? ""
  useEffect(() => {
    document.title = `${novelTitle} · ${tabLabel} — AI Reader Demo`
  }, [novelTitle, tabLabel])

  // Story 4.1: Track tab switches for upgrade banner
  const [tabSwitchCount, setTabSwitchCount] = useState(0)
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const prevTab = useRef(activeTab)

  useEffect(() => {
    if (activeTab !== prevTab.current) {
      setTabSwitchCount((c) => c + 1)
      prevTab.current = activeTab
    }
  }, [activeTab])

  const showUpgradeBanner = tabSwitchCount >= 2 && !bannerDismissed

  const dismissBanner = useCallback(() => setBannerDismissed(true), [])

  // Escape key to dismiss banner
  useEffect(() => {
    if (!showUpgradeBanner) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismissBanner()
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [showUpgradeBanner, dismissBanner])

  if (isEmbed) {
    return (
      <DemoProvider slug={novelSlug}>
        <div className="h-screen w-screen">
          <Outlet />
        </div>
      </DemoProvider>
    )
  }

  return (
    <DemoProvider slug={novelSlug}>
      {/* Story 4.3: Mobile gate — shown on < md screens instead of full demo */}
      <div className="flex h-screen flex-col items-center justify-center bg-slate-950 p-6 text-center md:hidden">
        <span className="mb-4 text-5xl">📚</span>
        <h1 className="mb-2 text-xl font-bold text-white">AI Reader V2 Demo</h1>
        <p className="mb-6 text-sm text-slate-400">
          交互式分析 Demo 需要桌面浏览器获得最佳体验
        </p>
        {/* Screenshot placeholders */}
        <div className="mb-6 flex gap-3 overflow-x-auto pb-2">
          <div className="flex-shrink-0 rounded-lg border border-slate-700/50 bg-slate-900 p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-slate-500">🕸️ 关系图</span>
          </div>
          <div className="flex-shrink-0 rounded-lg border border-slate-700/50 bg-slate-900 p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-slate-500">🗺️ 世界地图</span>
          </div>
          <div className="flex-shrink-0 rounded-lg border border-slate-700/50 bg-slate-900 p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-slate-500">⏳ 时间线</span>
          </div>
        </div>
        <p className="mb-4 text-xs text-slate-500">在桌面浏览器打开此链接获得完整交互体验</p>
        <div className="flex gap-3">
          <a
            href="https://github.com/mouseart2025/AI-Reader-V2"
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md bg-blue-500 px-6 py-2 text-sm font-semibold text-white hover:bg-blue-600 transition"
          >
            GitHub 下载
          </a>
          <a
            href={LANDING_URL}
            className="rounded-md border border-slate-600 px-6 py-2 text-sm font-semibold text-slate-300 hover:border-blue-500 hover:text-white transition"
          >
            返回首页
          </a>
        </div>
      </div>

      {/* Full demo layout — hidden on mobile, shown on md+ */}
      <div className="hidden md:flex h-screen flex-col bg-slate-950">
        {/* Top Navigation */}
        <header className="flex items-center gap-2 border-b border-slate-800 bg-slate-900/80 px-3 py-2 backdrop-blur sm:gap-4 sm:px-4">
          {/* Logo — links to landing page */}
          <a
            href={LANDING_URL}
            className="flex items-center gap-2 text-sm font-semibold text-slate-300 hover:text-blue-400 transition"
          >
            <span className="text-lg">📚</span>
            <span>AI Reader Demo</span>
          </a>

          {/* Novel Selector */}
          <select
            value={novelSlug}
            onChange={(e) => navigate(`/demo/${e.target.value}/${activeTab}`)}
            className="rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-200 focus:border-blue-500 focus:outline-none"
          >
            {novels.map((n) => (
              <option key={n.slug} value={n.slug}>
                {n.title}
              </option>
            ))}
          </select>

          {/* Tab Navigation */}
          <nav className="flex gap-1">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => navigate(`/demo/${novelSlug}/${tab.key}`)}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === tab.key
                    ? "bg-blue-500/20 text-blue-400"
                    : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`}
              >
                <span className="mr-1">{tab.icon}</span>
                {tab.label}
              </button>
            ))}
          </nav>

          <div className="flex-1" />

          {/* GitHub link */}
          <a
            href="https://github.com/mouseart2025/AI-Reader-V2"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-slate-500 hover:text-slate-300 transition"
          >
            GitHub ↗
          </a>
        </header>

        {/* Main Content */}
        <main className="flex-1 overflow-hidden">
          <Outlet />
        </main>

        {/* Entity Card Drawer (demo mode — builds profiles from static data) */}
        <DemoEntityCardDrawer />

        {/* Story 4.1: Upgrade Banner — appears after >= 2 tab switches */}
        {showUpgradeBanner && (
          <div
            role="complementary"
            aria-label="安装引导"
            className="flex items-center justify-between border-t border-slate-700/50 bg-slate-900 px-4 py-3 text-white animate-slide-up"
          >
            <p className="text-sm text-slate-300">
              <span className="mr-1">💡</span>
              想分析自己的小说？下载 AI Reader V2，5 分钟开始使用
            </p>
            <div className="flex items-center gap-3">
              <a
                href="https://github.com/mouseart2025/AI-Reader-V2"
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md bg-blue-500 px-4 py-1.5 text-sm font-semibold text-white hover:bg-blue-600 transition"
              >
                免费下载
              </a>
              <a
                href={LANDING_URL.replace(/\/$/, "") + "/#download"}
                className="hidden rounded-md border border-slate-600 px-4 py-1.5 text-sm text-slate-300 hover:text-white hover:border-slate-400 transition lg:block"
              >
                快速开始
              </a>
              <button
                onClick={dismissBanner}
                className="ml-2 text-slate-500 hover:text-white transition"
                aria-label="关闭"
              >
                ✕
              </button>
            </div>
          </div>
        )}
      </div>
    </DemoProvider>
  )
}
