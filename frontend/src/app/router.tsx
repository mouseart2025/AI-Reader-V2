import { lazy, Suspense } from "react"
import { createBrowserRouter, Navigate, useRouteError, Link } from "react-router-dom"
import { NovelLayout } from "./NovelLayout"

const BookshelfPage = lazy(() => import("@/pages/BookshelfPage"))
const ReadingPage = lazy(() => import("@/pages/ReadingPage"))
const GraphPage = lazy(() => import("@/pages/GraphPage"))
const MapPage = lazy(() => import("@/pages/MapPage"))
const TimelinePage = lazy(() => import("@/pages/TimelinePage"))
const FactionsPage = lazy(() => import("@/pages/FactionsPage"))
const ChatPage = lazy(() => import("@/pages/ChatPage"))
const EncyclopediaPage = lazy(() => import("@/pages/EncyclopediaPage"))
const AnalysisPage = lazy(() => import("@/pages/AnalysisPage"))
const ConflictsPage = lazy(() => import("@/pages/ConflictsPage"))
const ExportPage = lazy(() => import("@/pages/ExportPage"))
const SettingsPage = lazy(() => import("@/pages/SettingsPage"))

// Demo pages (lazy-loaded, only included when visiting /demo routes)
const DemoLayout = lazy(() => import("@/app/DemoLayout"))
const DemoGraphPage = lazy(() => import("@/pages/demo/DemoGraphPage"))
const DemoMapPage = lazy(() => import("@/pages/demo/DemoMapPage"))
const DemoTimelinePage = lazy(() => import("@/pages/demo/DemoTimelinePage"))
const DemoEncyclopediaPage = lazy(() => import("@/pages/demo/DemoEncyclopediaPage"))
const DemoFactionsPage = lazy(() => import("@/pages/demo/DemoFactionsPage"))
const DemoReadingPage = lazy(() => import("@/pages/demo/DemoReadingPage"))
const DemoExportPage = lazy(() => import("@/pages/demo/DemoExportPage"))

function SuspenseWrapper({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="text-muted-foreground flex min-h-screen items-center justify-center text-sm">
          加载中...
        </div>
      }
    >
      {children}
    </Suspense>
  )
}

/** Error boundary for demo routes — provides a helpful message and homepage escape */
function DemoErrorBoundary() {
  const error = useRouteError()
  const landingUrl = (import.meta.env.BASE_URL ?? "/").replace(/\/demo\/?$/, "/") || "/"
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-950 px-6 text-center">
      <span className="mb-4 text-5xl">😵</span>
      <h1 className="mb-2 text-xl font-bold text-white">Demo 页面加载出错</h1>
      <p className="mb-6 text-sm text-slate-400">
        {error instanceof Error ? error.message : "页面未找到或加载失败"}
      </p>
      <div className="flex gap-3">
        <a
          href={landingUrl}
          className="rounded-md bg-blue-500 px-6 py-2 text-sm font-semibold text-white hover:bg-blue-600 transition"
        >
          返回首页
        </a>
        <Link
          to="/demo/honglou/graph"
          className="rounded-md border border-slate-600 px-6 py-2 text-sm font-semibold text-slate-300 hover:border-blue-500 hover:text-white transition"
        >
          打开红楼梦 Demo
        </Link>
      </div>
    </div>
  )
}

export const router = createBrowserRouter([
  { path: "/", element: <SuspenseWrapper><BookshelfPage /></SuspenseWrapper> },
  {
    element: <NovelLayout />,
    children: [
      { path: "/analysis/:novelId", element: <SuspenseWrapper><AnalysisPage /></SuspenseWrapper> },
      { path: "/read/:novelId", element: <SuspenseWrapper><ReadingPage /></SuspenseWrapper> },
      { path: "/graph/:novelId", element: <SuspenseWrapper><GraphPage /></SuspenseWrapper> },
      { path: "/map/:novelId", element: <SuspenseWrapper><MapPage /></SuspenseWrapper> },
      { path: "/timeline/:novelId", element: <SuspenseWrapper><TimelinePage /></SuspenseWrapper> },
      { path: "/factions/:novelId", element: <SuspenseWrapper><FactionsPage /></SuspenseWrapper> },
      { path: "/encyclopedia/:novelId", element: <SuspenseWrapper><EncyclopediaPage /></SuspenseWrapper> },
      { path: "/chat/:novelId", element: <SuspenseWrapper><ChatPage /></SuspenseWrapper> },
      { path: "/conflicts/:novelId", element: <SuspenseWrapper><ConflictsPage /></SuspenseWrapper> },
      { path: "/export/:novelId", element: <SuspenseWrapper><ExportPage /></SuspenseWrapper> },
    ],
  },
  { path: "/settings", element: <SuspenseWrapper><SettingsPage /></SuspenseWrapper> },
  // Demo routes — standalone interactive demo with static JSON data
  {
    path: "/demo/:novelSlug",
    element: <SuspenseWrapper><DemoLayout /></SuspenseWrapper>,
    errorElement: <DemoErrorBoundary />,
    children: [
      { index: true, element: <Navigate to="graph" replace /> },
      { path: "graph", element: <SuspenseWrapper><DemoGraphPage /></SuspenseWrapper> },
      { path: "map", element: <SuspenseWrapper><DemoMapPage /></SuspenseWrapper> },
      { path: "timeline", element: <SuspenseWrapper><DemoTimelinePage /></SuspenseWrapper> },
      { path: "encyclopedia", element: <SuspenseWrapper><DemoEncyclopediaPage /></SuspenseWrapper> },
      { path: "factions", element: <SuspenseWrapper><DemoFactionsPage /></SuspenseWrapper> },
      { path: "reading", element: <SuspenseWrapper><DemoReadingPage /></SuspenseWrapper> },
      { path: "export", element: <SuspenseWrapper><DemoExportPage /></SuspenseWrapper> },
    ],
  },
  // Redirect bare /demo to default novel
  { path: "/demo", element: <Navigate to="/demo/honglou/graph" replace /> },
])
