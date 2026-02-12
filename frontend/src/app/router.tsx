import { lazy, Suspense } from "react"
import { createBrowserRouter } from "react-router-dom"
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
const SettingsPage = lazy(() => import("@/pages/SettingsPage"))

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
    ],
  },
  { path: "/settings", element: <SuspenseWrapper><SettingsPage /></SuspenseWrapper> },
])
