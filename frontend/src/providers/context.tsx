/**
 * DataProvider React Context
 * 根据平台自动注入 DesktopDataProvider / DemoDataProvider / ApiDataProvider
 */

import { createContext, useContext, useMemo, type ReactNode } from "react"
import { usePlatform } from "@/hooks/usePlatform"
import { DesktopDataProvider } from "./DesktopDataProvider"
import { DemoDataProvider } from "./DemoDataProvider"
import { ApiDataProvider } from "./ApiDataProvider"
import type { NovelDataProvider } from "./types"

const DataProviderContext = createContext<NovelDataProvider | null>(null)

/**
 * 获取当前平台的数据提供者
 * @throws 如果在 DataProviderProvider 外部使用
 */
export function useDataProvider(): NovelDataProvider {
  const provider = useContext(DataProviderContext)
  if (!provider) {
    throw new Error("useDataProvider must be used within DataProviderProvider")
  }
  return provider
}

/**
 * 数据提供者根组件 — 根据运行平台自动选择 Provider
 *
 * - Tauri WebView → DesktopDataProvider (asset:// 协议)
 * - Demo 网站 → DemoDataProvider (CDN JSON.gz)
 * - Web 本地开发 → ApiDataProvider (REST API)
 */
export function DataProviderProvider({ children }: { children: ReactNode }) {
  const { isTauri, isDemo } = usePlatform()

  const provider = useMemo(() => {
    // DesktopDataProvider 内部使用动态 import() 加载 Tauri API
    // 类定义本身不依赖 Tauri，因此静态导入是安全的
    if (isTauri) return new DesktopDataProvider()
    if (isDemo) return new DemoDataProvider()
    return new ApiDataProvider()
  }, [isTauri, isDemo])

  return (
    <DataProviderContext.Provider value={provider}>
      {children}
    </DataProviderContext.Provider>
  )
}
