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
import { useI18n } from "@/i18n"

const TABS = [
  { key: "reading", labelKey: "nav.reading", icon: "📃" },
  { key: "graph", labelKey: "nav.graph", icon: "🕸️" },
  { key: "map", labelKey: "nav.map", icon: "🗺️" },
  { key: "timeline", labelKey: "nav.timeline", icon: "⏳" },
  { key: "encyclopedia", labelKey: "nav.encyclopedia", icon: "📖" },
  { key: "factions", labelKey: "nav.factions", icon: "⚔️" },
  { key: "export", labelKey: "nav.export", icon: "💾" },
] as const

/** Compute landing page URL from Vite base path */
const LANDING_URL = (import.meta.env.BASE_URL ?? "/").replace(/\/demo\/?$/, "/") || "/"

export default function DemoLayout() {
  const { novelSlug = "honglou" } = useParams<{ novelSlug: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const { t } = useI18n()
  const novels = getAllDemoNovels()

  // Embed mode: render only the page content, no chrome
  const isEmbed = new URLSearchParams(location.search).get("embed") === "1"

  // Determine active tab from URL path
  const pathParts = location.pathname.split("/")
  const activeTab = pathParts[pathParts.length - 1] || "reading"

  // Dynamic document title
  const novelTitle = novels.find((n) => n.slug === novelSlug)?.title ?? novelSlug
  const activeTabConfig = TABS.find((tab) => tab.key === activeTab)
  const tabLabel = activeTabConfig ? t(activeTabConfig.labelKey) : ""
  useEffect(() => {
    document.title = `${novelTitle} · ${tabLabel} — ${t("demo.documentTitleSuffix")}`
  }, [novelTitle, tabLabel, t])

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
        <h1 className="mb-2 text-xl font-bold text-white">{t("demo.appTitle")}</h1>
        <p className="mb-6 text-sm text-slate-400">
          {t("demo.mobileBestDesktop")}
        </p>
        {/* Screenshot placeholders */}
        <div className="mb-6 flex gap-3 overflow-x-auto pb-2">
          <div className="flex-shrink-0 rounded-lg border border-slate-700/50 bg-slate-900 p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-slate-500">{t("demo.previewRelationGraph")}</span>
          </div>
          <div className="flex-shrink-0 rounded-lg border border-slate-700/50 bg-slate-900 p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-slate-500">{t("demo.previewWorldMap")}</span>
          </div>
          <div className="flex-shrink-0 rounded-lg border border-slate-700/50 bg-slate-900 p-4 w-48 h-32 flex items-center justify-center">
            <span className="text-xs text-slate-500">{t("demo.previewTimeline")}</span>
          </div>
        </div>
        <p className="mb-4 text-xs text-slate-500">{t("demo.openOnDesktop")}</p>
        <div className="flex gap-3">
          <a
            href="https://github.com/mouseart2025/AI-Reader-V2"
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md bg-blue-500 px-6 py-2 text-sm font-semibold text-white hover:bg-blue-600 transition"
          >
            {t("demo.githubDownload")}
          </a>
          <a
            href={LANDING_URL}
            className="rounded-md border border-slate-600 px-6 py-2 text-sm font-semibold text-slate-300 hover:border-blue-500 hover:text-white transition"
          >
            {t("demo.returnHome")}
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
            <span>{t("demo.logoTitle")}</span>
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
                {t(tab.labelKey)}
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
            {t("demo.githubLink")}
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
            aria-label={t("demo.upgradeBannerLabel")}
            className="flex items-center justify-between border-t border-slate-700/50 bg-slate-900 px-4 py-3 text-white animate-slide-up"
          >
            <p className="text-sm text-slate-300">
              <span className="mr-1">💡</span>
              {t("demo.upgradePrompt")}
            </p>
            <div className="flex items-center gap-3">
              <a
                href="https://github.com/mouseart2025/AI-Reader-V2"
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md bg-blue-500 px-4 py-1.5 text-sm font-semibold text-white hover:bg-blue-600 transition"
              >
                {t("demo.freeDownload")}
              </a>
              <a
                href={LANDING_URL.replace(/\/$/, "") + "/#download"}
                className="hidden rounded-md border border-slate-600 px-4 py-1.5 text-sm text-slate-300 hover:text-white hover:border-slate-400 transition lg:block"
              >
                {t("demo.quickStart")}
              </a>
              <button
                onClick={dismissBanner}
                className="ml-2 text-slate-500 hover:text-white transition"
                aria-label={t("common.close")}
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
