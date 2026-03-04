/**
 * DemoLayout — wraps demo pages with navigation bar + CTA conversion bar.
 * Provides novel selector, 7 visualization tabs, mobile gate, and upgrade banner.
 */
import { useCallback, useEffect, useRef, useState } from "react"
import { Outlet, useParams, useNavigate, useLocation, Link } from "react-router-dom"
import { DemoProvider } from "./DemoContext"
import { DemoEntityCardDrawer } from "@/components/entity-cards/DemoEntityCardDrawer"
import { getAllDemoNovels } from "@/api/demoNovelMap"

const TABS = [
  { key: "graph", label: "图谱", icon: "🕸️" },
  { key: "map", label: "地图", icon: "🗺️" },
  { key: "timeline", label: "时间线", icon: "⏳" },
  { key: "encyclopedia", label: "百科", icon: "📖" },
  { key: "factions", label: "势力", icon: "⚔️" },
  { key: "reading", label: "阅读", icon: "📃" },
  { key: "export", label: "导出", icon: "💾" },
] as const

export default function DemoLayout() {
  const { novelSlug = "honglou" } = useParams<{ novelSlug: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const novels = getAllDemoNovels()

  // Embed mode: render only the page content, no chrome
  const isEmbed = new URLSearchParams(location.search).get("embed") === "1"

  // Determine active tab from URL path
  const pathParts = location.pathname.split("/")
  const activeTab = pathParts[pathParts.length - 1] || "graph"

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
      <div className="flex h-screen flex-col items-center justify-center bg-gray-50 p-6 text-center md:hidden">
        <span className="mb-4 text-5xl">📚</span>
        <h1 className="mb-2 text-xl font-bold text-gray-800">AI Reader V2 Demo</h1>
        <p className="mb-6 text-sm text-gray-500">
          交互式分析 Demo 需要桌面浏览器获得最佳体验
        </p>
        {/* Screenshot placeholders */}
        <div className="mb-6 flex gap-3 overflow-x-auto pb-2">
          <div className="flex-shrink-0 rounded-lg border-2 border-dashed border-gray-300 bg-white p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-gray-400">🕸️ 关系图</span>
          </div>
          <div className="flex-shrink-0 rounded-lg border-2 border-dashed border-gray-300 bg-white p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-gray-400">🗺️ 世界地图</span>
          </div>
          <div className="flex-shrink-0 rounded-lg border-2 border-dashed border-gray-300 bg-white p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-gray-400">⏳ 时间线</span>
          </div>
        </div>
        <p className="mb-4 text-xs text-gray-400">在桌面浏览器打开此链接获得完整交互体验</p>
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
            href={import.meta.env.BASE_URL?.replace(/\/demo\/?$/, "/") || "/"}
            className="rounded-md border border-gray-300 px-6 py-2 text-sm font-semibold text-gray-600 hover:border-blue-500 hover:text-blue-600 transition"
          >
            返回首页
          </a>
        </div>
      </div>

      {/* Full demo layout — hidden on mobile, shown on md+ */}
      <div className="hidden md:flex h-screen flex-col">
        {/* Top Navigation */}
        <header className="flex items-center gap-2 border-b bg-white px-3 py-2 shadow-sm sm:gap-4 sm:px-4">
          {/* Logo */}
          <Link
            to="/"
            className="flex items-center gap-2 text-sm font-semibold text-gray-700 hover:text-blue-600"
            onClick={(e) => {
              e.preventDefault()
              window.location.href = import.meta.env.BASE_URL?.replace(/\/demo\/?$/, "/") || "/"
            }}
          >
            <span className="text-lg">📚</span>
            <span>AI Reader Demo</span>
          </Link>

          {/* Novel Selector */}
          <select
            value={novelSlug}
            onChange={(e) => navigate(`/demo/${e.target.value}/${activeTab}`)}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
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
                    ? "bg-blue-50 text-blue-700"
                    : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
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
            className="text-sm text-gray-400 hover:text-gray-600"
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
            className="flex items-center justify-between border-t bg-slate-800 px-4 py-3 text-white animate-slide-up"
          >
            <p className="text-sm">
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
                href={import.meta.env.BASE_URL?.replace(/\/demo\/?$/, "/#download") || "/#download"}
                className="hidden rounded-md border border-gray-600 px-4 py-1.5 text-sm text-gray-300 hover:text-white hover:border-gray-400 transition lg:block"
              >
                快速开始
              </a>
              <button
                onClick={dismissBanner}
                className="ml-2 text-gray-400 hover:text-white transition"
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
