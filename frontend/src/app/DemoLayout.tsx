/**
 * DemoLayout — wraps demo pages with navigation bar + CTA conversion bar.
 * Provides novel selector, 4 visualization tabs, and bottom CTA.
 */
import { Outlet, useParams, useNavigate, useLocation, Link } from "react-router-dom"
import { DemoProvider } from "./DemoContext"
import { DemoEntityCardDrawer } from "@/components/entity-cards/DemoEntityCardDrawer"
import { getAllDemoNovels } from "@/api/demoNovelMap"

const TABS = [
  { key: "graph", label: "图谱", icon: "🕸️" },
  { key: "map", label: "地图", icon: "🗺️" },
  { key: "timeline", label: "时间线", icon: "⏳" },
  { key: "encyclopedia", label: "百科", icon: "📖" },
] as const

export default function DemoLayout() {
  const { novelSlug = "honglou" } = useParams<{ novelSlug: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const novels = getAllDemoNovels()

  // Determine active tab from URL path
  const pathParts = location.pathname.split("/")
  const activeTab = pathParts[pathParts.length - 1] || "graph"

  return (
    <DemoProvider slug={novelSlug}>
      <div className="flex h-screen flex-col">
        {/* Mobile notice — visible only on very small screens */}
        <div className="flex items-center gap-2 border-b bg-blue-50 px-4 py-2 text-xs text-blue-700 sm:hidden">
          <span>💡</span>
          <span>建议在电脑或平板上体验，可视化效果更佳</span>
        </div>

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
            <span className="hidden sm:inline">AI Reader Demo</span>
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
          <nav className="flex flex-wrap gap-0.5 sm:gap-1">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => navigate(`/demo/${novelSlug}/${tab.key}`)}
                className={`rounded-md px-2 py-1 text-xs font-medium transition sm:px-3 sm:py-1.5 sm:text-sm ${
                  activeTab === tab.key
                    ? "bg-blue-50 text-blue-700"
                    : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                }`}
              >
                <span className="mr-1 hidden sm:inline">{tab.icon}</span>
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
            className="hidden text-sm text-gray-400 hover:text-gray-600 md:block"
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

        {/* Bottom CTA Bar */}
        <footer className="flex items-center justify-between border-t bg-gray-900 px-3 py-2 text-white sm:px-4 sm:py-3">
          <p className="text-xs sm:text-sm">
            <span className="mr-1 hidden sm:inline">💡</span>
            <span className="hidden sm:inline">想分析自己的小说？下载 AI Reader V2，5 分钟开始使用</span>
            <span className="sm:hidden">想分析自己的小说？</span>
          </p>
          <div className="flex gap-3">
            <a
              href="https://github.com/mouseart2025/AI-Reader-V2"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md bg-white px-4 py-1.5 text-sm font-semibold text-gray-900 hover:bg-gray-100 transition"
            >
              免费下载
            </a>
            <a
              href="https://github.com/mouseart2025/AI-Reader-V2"
              target="_blank"
              rel="noopener noreferrer"
              className="hidden rounded-md border border-gray-600 px-4 py-1.5 text-sm text-gray-300 hover:text-white hover:border-gray-400 transition sm:block"
            >
              查看源码
            </a>
          </div>
        </footer>
      </div>
    </DemoProvider>
  )
}
