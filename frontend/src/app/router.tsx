import { createBrowserRouter } from "react-router-dom"
import BookshelfPage from "@/pages/BookshelfPage"
import ReadingPage from "@/pages/ReadingPage"
import GraphPage from "@/pages/GraphPage"
import MapPage from "@/pages/MapPage"
import TimelinePage from "@/pages/TimelinePage"
import FactionsPage from "@/pages/FactionsPage"
import ChatPage from "@/pages/ChatPage"
import EncyclopediaPage from "@/pages/EncyclopediaPage"
import AnalysisPage from "@/pages/AnalysisPage"
import SettingsPage from "@/pages/SettingsPage"

export const router = createBrowserRouter([
  { path: "/", element: <BookshelfPage /> },
  { path: "/read/:novelId", element: <ReadingPage /> },
  { path: "/graph/:novelId", element: <GraphPage /> },
  { path: "/map/:novelId", element: <MapPage /> },
  { path: "/timeline/:novelId", element: <TimelinePage /> },
  { path: "/factions/:novelId", element: <FactionsPage /> },
  { path: "/chat/:novelId", element: <ChatPage /> },
  { path: "/encyclopedia/:novelId", element: <EncyclopediaPage /> },
  { path: "/analysis/:novelId", element: <AnalysisPage /> },
  { path: "/settings", element: <SettingsPage /> },
])
