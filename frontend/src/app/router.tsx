import { lazy } from "react"
import { createBrowserRouter, Navigate } from "react-router-dom"
import { NovelLayout } from "./NovelLayout"
import { SuspenseWrapper, DemoErrorBoundary } from "./routerComponents"

// Platform detection at module level (not a hook — used by createBrowserRouter)
const isTauri = typeof window !== "undefined" && ("__TAURI__" in window || "__TAURI_INTERNALS__" in window)

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
// const MapEnginePOC = lazy(() => import("@/pages/MapEnginePOC"))  // WebGL POC — hidden until stable

// Desktop pages (lazy-loaded, only included in Tauri)
const DesktopBookshelfPage = lazy(() => import("@/desktop/BookshelfPage"))
const DesktopLayout = lazy(() => import("@/app/DesktopLayout"))

// Demo pages (lazy-loaded, only included when visiting /demo routes)
const DemoLayout = lazy(() => import("@/app/DemoLayout"))
const DemoGraphPage = lazy(() => import("@/pages/demo/DemoGraphPage"))
const DemoMapPage = lazy(() => import("@/pages/demo/DemoMapPage"))
const DemoTimelinePage = lazy(() => import("@/pages/demo/DemoTimelinePage"))
const DemoEncyclopediaPage = lazy(() => import("@/pages/demo/DemoEncyclopediaPage"))
const DemoFactionsPage = lazy(() => import("@/pages/demo/DemoFactionsPage"))
const DemoReadingPage = lazy(() => import("@/pages/demo/DemoReadingPage"))
const DemoExportPage = lazy(() => import("@/pages/demo/DemoExportPage"))

export const router = createBrowserRouter([
  // Platform-specific routes
  ...(isTauri
    ? [
        // Desktop: bookshelf at root
        { path: "/", element: <SuspenseWrapper><DesktopBookshelfPage /></SuspenseWrapper> },
        // Desktop: novel detail with production pages (full backend via sidecar)
        {
          path: "/novel/:novelId",
          element: <SuspenseWrapper><DesktopLayout /></SuspenseWrapper>,
          children: [
            { index: true, element: <Navigate to="reading" replace /> },
            { path: "analysis", element: <SuspenseWrapper><AnalysisPage /></SuspenseWrapper> },
            { path: "reading", element: <SuspenseWrapper><ReadingPage /></SuspenseWrapper> },
            { path: "graph", element: <SuspenseWrapper><GraphPage /></SuspenseWrapper> },
            { path: "map", element: <SuspenseWrapper><MapPage /></SuspenseWrapper> },
            { path: "timeline", element: <SuspenseWrapper><TimelinePage /></SuspenseWrapper> },
            { path: "encyclopedia/*", element: <SuspenseWrapper><EncyclopediaPage /></SuspenseWrapper> },
            { path: "factions", element: <SuspenseWrapper><FactionsPage /></SuspenseWrapper> },
            { path: "chat", element: <SuspenseWrapper><ChatPage /></SuspenseWrapper> },
            { path: "conflicts", element: <SuspenseWrapper><ConflictsPage /></SuspenseWrapper> },
            { path: "export", element: <SuspenseWrapper><ExportPage /></SuspenseWrapper> },
          ],
        },
        // Desktop: settings page
        { path: "/settings", element: <SuspenseWrapper><SettingsPage /></SuspenseWrapper> },
      ]
    : [
        // Web: existing routes
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
      ]),
  // Demo routes — shared across both modes
  {
    path: "/demo/:novelSlug",
    element: <SuspenseWrapper><DemoLayout /></SuspenseWrapper>,
    errorElement: <DemoErrorBoundary />,
    children: [
      { index: true, element: <Navigate to="reading" replace /> },
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
  { path: "/demo", element: <Navigate to="/demo/honglou/reading" replace /> },
  // 技术 POC 页面 — 隐藏直到 WebGL 渲染器稳定
  // { path: "/poc/map-engine", element: <SuspenseWrapper><MapEnginePOC /></SuspenseWrapper> },
])
