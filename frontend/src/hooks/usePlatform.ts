import { useMemo } from "react"
import type { PlatformInfo } from "@/providers/types"

/**
 * 平台检测 hook — 统一替代组件内直接检测 window.__TAURI__
 *
 * @example
 * const { isTauri, isDemo, isWeb } = usePlatform()
 */
export function usePlatform(): PlatformInfo {
  return useMemo(() => {
    const isTauri =
      typeof window !== "undefined" &&
      "__TAURI__" in window
    const isDemo =
      !isTauri &&
      typeof window !== "undefined" &&
      window.location.pathname.startsWith("/demo")
    const isWeb = !isTauri && !isDemo
    return { isTauri, isDemo, isWeb }
  }, [])
}
