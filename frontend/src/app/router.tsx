import { lazy, Suspense } from "react"
import { createBrowserRouter, Navigate } from "react-router-dom"
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
    children: [
      { index: true, element: <Navigate to="graph" replace /> },
      { path: "graph", element: <SuspenseWrapper><DemoGraphPage /></SuspenseWrapper> },
      { path: "map", element: <SuspenseWrapper><DemoMapPage /></SuspenseWrapper> },
      { path: "timeline", element: <SuspenseWrapper><DemoTimelinePage /></SuspenseWrapper> },
      { path: "encyclopedia", element: <SuspenseWrapper><DemoEncyclopediaPage /></SuspenseWrapper> },
    ],
  },
  // Redirect bare /demo to default novel
  { path: "/demo", element: <Navigate to="/demo/honglou/graph" replace /> },
])
