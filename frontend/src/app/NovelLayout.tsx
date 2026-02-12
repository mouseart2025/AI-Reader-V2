import { Outlet } from "react-router-dom"
import { FloatingChatPanel } from "@/components/chat/FloatingChatPanel"

/**
 * Layout wrapper for novel-scoped pages (reading, graph, map, etc.).
 * Renders the floating chat panel at the bottom.
 */
export function NovelLayout() {
  return (
    <>
      <Outlet />
      <FloatingChatPanel />
    </>
  )
}
