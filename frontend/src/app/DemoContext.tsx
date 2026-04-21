/**
 * DemoContext — provides preloaded demo data to all demo child routes.
 * Data is loaded once when a novel slug changes, then shared via context.
 */
import { createContext, useContext, useCallback, useEffect, useState, type ReactNode } from "react"
import { preloadAllDemoData, clearDemoCache, loadDemoChapterContent, type DemoDataBundle, type DemoChapterContent } from "@/api/demoDataAdapter"
import { getDemoNovel, type DemoNovelInfo } from "@/api/demoNovelMap"
import { useI18n } from "@/i18n"

export interface DemoContextValue {
  slug: string
  novelInfo: DemoNovelInfo
  data: DemoDataBundle
  /** Load a single chapter's content (lazy, with cache + preload-next) */
  loadChapterContent: (chapterNum: number) => Promise<DemoChapterContent>
}

export const DemoCtx = createContext<DemoContextValue | null>(null)

export function useDemoData(): DemoContextValue {
  const ctx = useContext(DemoCtx)
  if (!ctx) throw new Error("useDemoData must be used within DemoProvider")
  return ctx
}

interface DemoProviderProps {
  slug: string
  children: ReactNode
}

export function DemoProvider({ slug, children }: DemoProviderProps) {
  const { t } = useI18n()
  const [data, setData] = useState<DemoDataBundle | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const novelInfo = getDemoNovel(slug)

  const loadChapter = useCallback(
    (chapterNum: number) => loadDemoChapterContent(slug, chapterNum),
    [slug],
  )

  useEffect(() => {
    if (!novelInfo) return
    setLoading(true)
    setError(null)
    clearDemoCache()

    preloadAllDemoData(slug)
      .then((bundle) => {
        setData(bundle)
        setLoading(false)
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : t("demo.loadDataFailed"))
        setLoading(false)
      })
  }, [slug, novelInfo, t])

  if (!novelInfo) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950">
        <div className="text-center">
          <p className="text-lg font-semibold text-red-400">{t("demo.unknownNovelTitle")}</p>
          <p className="mt-2 text-sm text-slate-400">
            {t("demo.invalidNovelSlug", { slug })}
          </p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950">
        <div className="text-center">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
          <p className="text-sm text-slate-400">
            {t("demo.loadingData", { title: novelInfo.title })}
          </p>
        </div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950">
        <div className="text-center">
          <p className="text-lg font-semibold text-red-400">{t("demo.loadFailed")}</p>
          <p className="mt-2 text-sm text-slate-400">{error}</p>
        </div>
      </div>
    )
  }

  return <DemoCtx.Provider value={{ slug, novelInfo, data, loadChapterContent: loadChapter }}>{children}</DemoCtx.Provider>
}
