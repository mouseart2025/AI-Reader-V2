/**
 * DemoReadingPage — full reading experience with lazy-loaded chapter content,
 * entity highlighting, scene/screenplay panel, chapter navigation, reading progress,
 * and font size control.
 * Layout matches production ReadingPage: left sidebar + centered article + optional right scene panel.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { useDemoData } from "@/app/DemoContext"
import { loadDemoChapterContent, type DemoChapterContent } from "@/api/demoDataAdapter"
import { highlightText } from "@/lib/entityHighlight"
import { useEntityCardStore } from "@/stores/entityCardStore"
import type { ChapterEntity, Scene } from "@/api/types"

interface ChapterMeta {
  chapter_num: number
  title: string
  word_count: number
  analysis_status: string
}

const FONT_SIZES = [
  { label: "小", value: "text-sm", size: 14 },
  { label: "中", value: "text-base", size: 16 },
  { label: "大", value: "text-lg", size: 18 },
  { label: "特大", value: "text-xl", size: 20 },
] as const

const ENTITY_TYPE_LABELS = [
  { type: "person", label: "人物", color: "bg-blue-500" },
  { type: "location", label: "地点", color: "bg-green-500" },
  { type: "item", label: "物品", color: "bg-orange-500" },
  { type: "org", label: "组织", color: "bg-purple-500" },
  { type: "concept", label: "概念", color: "bg-gray-500" },
]

const SCENE_BORDER_COLORS = [
  "border-l-blue-500",
  "border-l-emerald-500",
  "border-l-amber-500",
  "border-l-purple-500",
  "border-l-rose-500",
  "border-l-cyan-500",
  "border-l-indigo-500",
  "border-l-lime-500",
]

const TONE_STYLES: Record<string, string> = {
  "战斗": "bg-red-900/30 text-red-300",
  "紧张": "bg-amber-900/30 text-amber-300",
  "悲伤": "bg-slate-800/50 text-slate-300",
  "欢乐": "bg-yellow-900/30 text-yellow-300",
  "平静": "bg-sky-900/30 text-sky-300",
}

const EVENT_TYPE_STYLES: Record<string, string> = {
  "对话": "bg-blue-900/30 text-blue-300",
  "战斗": "bg-red-900/30 text-red-300",
  "旅行": "bg-green-900/30 text-green-300",
  "描写": "bg-violet-900/30 text-violet-300",
  "回忆": "bg-orange-900/30 text-orange-300",
}

const FILTER_TONES = ["战斗", "紧张", "悲伤", "欢乐", "平静"] as const

export default function DemoReadingPage() {
  const { data, novelInfo, slug } = useDemoData()
  const chapters = data.chapters as ChapterMeta[]
  const [searchParams, setSearchParams] = useSearchParams()
  const openCard = useEntityCardStore((s) => s.openCard)

  // Current chapter from URL or default to 1
  const chapterNumFromUrl = Number(searchParams.get("chapter")) || 1
  const [currentChapterNum, setCurrentChapterNum] = useState(chapterNumFromUrl)

  // Chapter content state
  const [chapterContent, setChapterContent] = useState<DemoChapterContent | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // UI state
  const [fontSizeIdx, setFontSizeIdx] = useState(1) // default "中"
  const [highlightEnabled, setHighlightEnabled] = useState(true)
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set())
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [showSettings, setShowSettings] = useState(false)
  const [readProgress, setReadProgress] = useState(0)

  // Scene panel state — default open since demo data is always fully analyzed
  const [scenePanelOpen, setScenePanelOpen] = useState(true)
  const [activeSceneIndex, setActiveSceneIndex] = useState(-1)
  const [filterChar, setFilterChar] = useState("")
  const [filterTone, setFilterTone] = useState<string | null>(null)

  const contentRef = useRef<HTMLDivElement>(null)
  const chapterListRef = useRef<HTMLDivElement>(null)
  const settingsRef = useRef<HTMLDivElement>(null)

  const currentChapter = chapters.find((c) => c.chapter_num === currentChapterNum)
  const scenes: Scene[] = chapterContent?.scenes ?? []

  // Filter scenes by character and tone
  const filteredScenes = useMemo(() => {
    let result = scenes
    if (filterChar.trim()) {
      const q = filterChar.trim()
      result = result.filter((s) =>
        s.characters.some((c) => c.includes(q)) ||
        s.character_roles?.some((cr) => cr.name.includes(q)),
      )
    }
    if (filterTone) {
      result = result.filter((s) => s.emotional_tone === filterTone)
    }
    return result
  }, [scenes, filterChar, filterTone])

  // Build paragraph -> scene index map
  const paraSceneMap = useMemo(() => {
    const map = new Map<number, number>()
    for (const scene of scenes) {
      if (!scene.paragraph_range) continue
      for (let p = scene.paragraph_range[0]; p <= scene.paragraph_range[1]; p++) {
        map.set(p, scene.index)
      }
    }
    return map
  }, [scenes])

  // Sync URL when chapter changes
  useEffect(() => {
    const urlNum = Number(searchParams.get("chapter")) || 1
    if (urlNum !== currentChapterNum) {
      setSearchParams({ chapter: String(currentChapterNum) }, { replace: true })
    }
  }, [currentChapterNum, searchParams, setSearchParams])

  // Sync from URL changes (e.g. browser back/forward)
  useEffect(() => {
    const urlNum = Number(searchParams.get("chapter")) || 1
    if (urlNum !== currentChapterNum) {
      setCurrentChapterNum(urlNum)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  // Load chapter content
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    loadDemoChapterContent(slug, currentChapterNum)
      .then((content) => {
        if (!cancelled) {
          setChapterContent(content)
          setLoading(false)
          contentRef.current?.scrollTo(0, 0)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载章节内容失败")
          setLoading(false)
        }
      })

    return () => { cancelled = true }
  }, [slug, currentChapterNum])

  // Scroll active chapter into view in sidebar
  useEffect(() => {
    const el = chapterListRef.current?.querySelector(`[data-chapter="${currentChapterNum}"]`)
    el?.scrollIntoView({ block: "nearest" })
  }, [currentChapterNum])

  // Reading progress tracking
  useEffect(() => {
    const container = contentRef.current
    if (!container) return
    const onScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container
      const max = scrollHeight - clientHeight
      setReadProgress(max > 0 ? Math.min(scrollTop / max, 1) : 0)
    }
    container.addEventListener("scroll", onScroll, { passive: true })
    return () => container.removeEventListener("scroll", onScroll)
  }, [chapterContent])

  // Keyboard navigation
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === "ArrowLeft") { e.preventDefault(); goToPrev() }
      else if (e.key === "ArrowRight") { e.preventDefault(); goToNext() }
      else if (e.key === "h" || e.key === "H") { setHighlightEnabled((v) => !v) }
      else if (e.key === "s" || e.key === "S") { setScenePanelOpen((v) => !v) }
      else if (e.key === "Escape") { setShowSettings(false); setScenePanelOpen(false) }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentChapterNum, chapters.length])

  // Close settings on click outside
  useEffect(() => {
    if (!showSettings) return
    const onClick = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setShowSettings(false)
      }
    }
    document.addEventListener("mousedown", onClick)
    return () => document.removeEventListener("mousedown", onClick)
  }, [showSettings])

  const goToPrev = useCallback(() => {
    setCurrentChapterNum((n) => Math.max(1, n - 1))
  }, [])

  const goToNext = useCallback(() => {
    setCurrentChapterNum((n) => Math.min(chapters.length, n + 1))
  }, [chapters.length])

  // Filter entities by hidden types
  const visibleEntities = useMemo(() => {
    if (!chapterContent?.entities || !highlightEnabled) return []
    return chapterContent.entities.filter((e) => !hiddenTypes.has(e.type)) as ChapterEntity[]
  }, [chapterContent?.entities, highlightEnabled, hiddenTypes])

  // Entity click handler
  const handleEntityClick = useCallback((name: string, type: string) => {
    if (type === "concept") return
    openCard(name, type as "person" | "location" | "item" | "org")
  }, [openCard])

  // Scroll to scene paragraph
  const scrollToScene = useCallback((scene: Scene, index: number) => {
    setActiveSceneIndex(index)
    if (!scene.paragraph_range || !contentRef.current) return
    const paraEl = contentRef.current.querySelector(`[data-para="${scene.paragraph_range[0]}"]`)
    if (paraEl) paraEl.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [])

  // Reset scene filter when chapter changes
  useEffect(() => {
    setActiveSceneIndex(-1)
    setFilterChar("")
    setFilterTone(null)
  }, [currentChapterNum])

  // Toggle entity type visibility
  const toggleType = useCallback((type: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }, [])

  // Split content into paragraphs
  const paragraphs = useMemo(() => {
    if (!chapterContent?.content) return []
    return chapterContent.content.split("\n").filter((p) => p.trim())
  }, [chapterContent?.content])

  // Render a single paragraph with optional highlighting
  const renderPara = useCallback((text: string) => {
    return visibleEntities.length > 0
      ? highlightText(text, visibleEntities, handleEntityClick)
      : text
  }, [visibleEntities, handleEntityClick])

  const fontSize = FONT_SIZES[fontSizeIdx]

  return (
    <div className="flex h-full bg-slate-950">
      {/* ── Left Sidebar (TOC) ── */}
      {sidebarOpen && (
        <div className="hidden w-64 shrink-0 flex-col border-r border-slate-800 bg-slate-900 lg:flex" ref={chapterListRef}>
          <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
            <h3 className="text-xs font-semibold text-slate-500">
              {novelInfo.title} · {chapters.length} 回
            </h3>
            <button
              onClick={() => setSidebarOpen(false)}
              className="rounded p-0.5 text-slate-500 hover:text-white"
              title="收起目录"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-3.5"><path d="m11 17-5-5 5-5"/><path d="m18 17-5-5 5-5"/></svg>
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            <div className="space-y-0.5">
              {chapters.map((c) => (
                <button
                  key={c.chapter_num}
                  data-chapter={c.chapter_num}
                  onClick={() => setCurrentChapterNum(c.chapter_num)}
                  className={`w-full rounded px-2 py-1.5 text-left text-xs transition ${
                    currentChapterNum === c.chapter_num
                      ? "bg-blue-500/20 text-blue-400 font-medium"
                      : "text-slate-400 hover:bg-slate-800"
                  }`}
                >
                  <span className="text-slate-500">第{c.chapter_num}回</span>{" "}
                  {c.title}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── Main Content Column ── */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Toolbar */}
        <header className="flex items-center gap-3 border-b border-slate-800 px-4 py-1.5">
          {/* Sidebar toggle (when closed) */}
          {!sidebarOpen && (
            <button
              onClick={() => setSidebarOpen(true)}
              className="hidden rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white lg:block"
              title="展开目录"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4"><path d="m13 17 5-5-5-5"/><path d="m6 17 5-5-5-5"/></svg>
            </button>
          )}

          {/* Prev */}
          <button
            onClick={goToPrev}
            disabled={currentChapterNum <= 1}
            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white disabled:opacity-30"
            title="上一回 (←)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4"><path d="m15 18-6-6 6-6"/></svg>
          </button>

          {/* Chapter selector */}
          <select
            value={currentChapterNum}
            onChange={(e) => setCurrentChapterNum(Number(e.target.value))}
            className="max-w-[280px] rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm text-slate-200 focus:border-blue-500 focus:outline-none"
          >
            {chapters.map((c) => (
              <option key={c.chapter_num} value={c.chapter_num}>
                第{c.chapter_num}回 {c.title}
              </option>
            ))}
          </select>

          {/* Next */}
          <button
            onClick={goToNext}
            disabled={currentChapterNum >= chapters.length}
            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white disabled:opacity-30"
            title="下一回 (→)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4"><path d="m9 18 6-6-6-6"/></svg>
          </button>

          {currentChapter && (
            <span className="hidden text-xs text-slate-500 sm:inline">
              {currentChapter.word_count.toLocaleString()} 字
            </span>
          )}

          <div className="flex-1" />

          {/* Highlight toggle */}
          <button
            onClick={() => setHighlightEnabled((v) => !v)}
            className={`rounded px-2 py-1 text-xs transition ${
              highlightEnabled
                ? "bg-blue-500/20 text-blue-400"
                : "text-slate-500 hover:text-slate-300"
            }`}
            title="实体高亮 (H)"
          >
            高亮
          </button>

          {/* Scene panel toggle */}
          <button
            onClick={() => setScenePanelOpen((v) => !v)}
            className={`rounded px-2 py-1 text-xs transition ${
              scenePanelOpen
                ? "bg-emerald-500/20 text-emerald-400"
                : "text-slate-500 hover:text-slate-300"
            }`}
            title="剧本场景 (S)"
          >
            剧本
          </button>

          {/* Settings dropdown (font size + entity filter) */}
          <div className="relative" ref={settingsRef}>
            <button
              onClick={() => setShowSettings((v) => !v)}
              className={`rounded p-1 transition ${showSettings ? "bg-slate-800 text-white" : "text-slate-400 hover:bg-slate-800 hover:text-white"}`}
              title="阅读设置"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>

            {showSettings && (
              <div className="absolute right-0 top-full z-40 mt-1 w-64 rounded-lg border border-slate-700 bg-slate-900 p-3 shadow-lg space-y-3">
                {/* Font size */}
                <div>
                  <span className="mb-1 block text-xs text-slate-500">字号</span>
                  <div className="flex gap-1">
                    {FONT_SIZES.map((fs, i) => (
                      <button
                        key={fs.label}
                        onClick={() => setFontSizeIdx(i)}
                        className={`rounded px-2.5 py-1 text-xs transition ${
                          fontSizeIdx === i
                            ? "bg-blue-500/20 text-blue-400"
                            : "text-slate-500 hover:text-slate-300 bg-slate-800"
                        }`}
                      >
                        {fs.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Entity highlight filter */}
                <div>
                  <span className="mb-1 block text-xs text-slate-500">实体高亮</span>
                  <div className="flex flex-wrap gap-1">
                    {ENTITY_TYPE_LABELS.map(({ type, label, color }) => {
                      const hidden = hiddenTypes.has(type)
                      return (
                        <button
                          key={type}
                          onClick={() => toggleType(type)}
                          className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition ${
                            hidden
                              ? "bg-slate-800 text-slate-600 line-through"
                              : "bg-slate-800 text-slate-300"
                          }`}
                        >
                          <span className={`inline-block size-2 rounded-full ${color} ${hidden ? "opacity-30" : ""}`} />
                          {label}
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        </header>

        {/* Scroll progress bar */}
        <div className="relative h-0.5 w-full bg-transparent">
          <div
            className="absolute left-0 top-0 h-full bg-blue-500 transition-[width] duration-150"
            style={{ width: `${readProgress * 100}%` }}
          />
        </div>

        {/* Chapter content scroll area */}
        <div ref={contentRef} className="flex-1 overflow-y-auto">
          <article className="mx-auto max-w-3xl px-8 py-8">
            {/* Chapter title */}
            <h2 className="mb-8 text-center text-xl font-bold text-white">
              第{currentChapterNum}回 {currentChapter?.title}
            </h2>

            {/* Loading skeleton */}
            {loading && (
              <div className="space-y-4">
                {Array.from({ length: 12 }).map((_, i) => (
                  <div key={i} className="h-4 animate-pulse rounded bg-slate-800" style={{ width: `${70 + Math.random() * 30}%` }} />
                ))}
              </div>
            )}

            {/* Error */}
            {!loading && error && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-center">
                <p className="text-sm text-red-400">{error}</p>
                <button
                  onClick={() => { setLoading(true); loadDemoChapterContent(slug, currentChapterNum).then(setChapterContent).catch(() => setError("重试失败")) }}
                  className="mt-2 text-xs text-blue-400 hover:underline"
                >
                  重试
                </button>
              </div>
            )}

            {/* Chapter content */}
            {!loading && !error && paragraphs.length > 0 && (
              <div>
                {paragraphs.map((para, i) => {
                  const sceneIdx = scenePanelOpen ? paraSceneMap.get(i) : undefined
                  const isActive = sceneIdx === activeSceneIndex
                  const borderColor = sceneIdx != null
                    ? SCENE_BORDER_COLORS[sceneIdx % SCENE_BORDER_COLORS.length]
                    : ""
                  return (
                    <p
                      key={i}
                      data-para={i}
                      className={`mb-4 leading-relaxed text-slate-200 ${fontSize.value} ${
                        sceneIdx != null ? `border-l-3 pl-3 ${borderColor}` : ""
                      } ${isActive ? "bg-slate-800/50 rounded-r" : ""}`}
                      style={{ textIndent: sceneIdx != null ? undefined : "2em" }}
                    >
                      {renderPara(para)}
                    </p>
                  )
                })}
              </div>
            )}

            {/* Chapter navigation at bottom */}
            {!loading && !error && (
              <div className="mt-12 flex items-center justify-between border-t border-slate-800 pt-6 pb-8">
                <button
                  onClick={goToPrev}
                  disabled={currentChapterNum <= 1}
                  className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:border-blue-500 hover:text-white disabled:opacity-30 transition"
                >
                  上一回
                </button>
                <span className="text-xs text-slate-500">
                  {currentChapterNum} / {chapters.length}
                </span>
                <button
                  onClick={goToNext}
                  disabled={currentChapterNum >= chapters.length}
                  className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:border-blue-500 hover:text-white disabled:opacity-30 transition"
                >
                  下一回
                </button>
              </div>
            )}
          </article>
        </div>
      </div>

      {/* ── Right Scene Panel ── */}
      {scenePanelOpen && (
        <div className="hidden w-72 shrink-0 flex-col border-l border-slate-800 bg-slate-900 lg:flex">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
            <span className="text-sm font-medium text-slate-200">剧本场景</span>
            <button
              onClick={() => setScenePanelOpen(false)}
              className="rounded p-0.5 text-slate-500 hover:text-white"
              title="收起 (S)"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
            </button>
          </div>

          {scenes.length === 0 ? (
            <div className="flex flex-1 items-center justify-center px-4">
              <p className="text-sm text-slate-500">当前章节无场景数据</p>
            </div>
          ) : (
            <>
              {/* Scene count + filters */}
              <div className="space-y-1.5 border-b border-slate-800 px-3 py-1.5">
                <span className="text-xs text-slate-500">
                  {filteredScenes.length}/{scenes.length} 个场景
                </span>
                <input
                  type="text"
                  placeholder="搜索角色..."
                  value={filterChar}
                  onChange={(e) => setFilterChar(e.target.value)}
                  className="h-6 w-full rounded border border-slate-700 bg-slate-800 px-2 text-xs text-slate-200 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                />
                <div className="flex flex-wrap gap-1">
                  {FILTER_TONES.map((tone) => (
                    <button
                      key={tone}
                      className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                        filterTone === tone
                          ? (TONE_STYLES[tone] ?? "bg-slate-700 text-slate-200")
                          : "bg-slate-800 text-slate-500 hover:bg-slate-700"
                      }`}
                      onClick={() => setFilterTone(filterTone === tone ? null : tone)}
                    >
                      {tone}
                    </button>
                  ))}
                </div>
              </div>

              {/* Scene list */}
              <div className="flex-1 overflow-y-auto p-2">
                {filteredScenes.length === 0 ? (
                  <p className="text-sm text-slate-500">无匹配场景</p>
                ) : (
                  filteredScenes.map((scene) => {
                    const borderColor = SCENE_BORDER_COLORS[scene.index % SCENE_BORDER_COLORS.length]
                    const isActive = scene.index === activeSceneIndex
                    return (
                      <button
                        key={scene.index}
                        className={`mb-2 w-full rounded-lg border-l-3 border p-3 text-left transition-colors ${borderColor} ${
                          isActive
                            ? "border-r-blue-500/50 border-y-blue-500/50 bg-blue-500/10"
                            : "border-r-slate-700 border-y-slate-700 hover:border-r-slate-600 hover:border-y-slate-600"
                        }`}
                        onClick={() => scrollToScene(scene, scene.index)}
                      >
                        {/* Header: number + title + time */}
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-500/10 text-xs font-medium text-blue-400">
                              {scene.index + 1}
                            </span>
                            <span className="truncate text-sm font-medium text-slate-200">{scene.title}</span>
                          </div>
                          {scene.time_of_day && (
                            <span className="shrink-0 text-[10px] text-slate-500">
                              {scene.time_of_day === "早" ? "晨" :
                               scene.time_of_day === "午" ? "午" :
                               scene.time_of_day === "晚" ? "暮" :
                               scene.time_of_day === "夜" ? "夜" : ""}
                            </span>
                          )}
                        </div>

                        {/* Location */}
                        {scene.location && (
                          <div className="mt-1.5">
                            <span className="inline-block rounded bg-green-900/30 px-1 py-0.5 text-[11px] text-green-300">
                              {scene.location}
                            </span>
                          </div>
                        )}

                        {/* Key dialogue */}
                        {scene.key_dialogue && scene.key_dialogue.length > 0 && (
                          <p className="mt-1.5 truncate text-xs italic text-slate-500">
                            {scene.key_dialogue[0]}
                          </p>
                        )}

                        {/* Characters with roles */}
                        {scene.character_roles && scene.character_roles.length > 0 ? (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {scene.character_roles.slice(0, 6).map((cr) => (
                              <span
                                key={cr.name}
                                className={`text-[11px] ${cr.role === "主" ? "font-medium text-slate-200" : "text-slate-500"}`}
                              >
                                {cr.name}
                                {cr.role === "主" && <span className="text-[9px]">(主)</span>}
                              </span>
                            ))}
                            {scene.character_roles.length > 6 && (
                              <span className="text-[11px] text-slate-500">
                                +{scene.character_roles.length - 6}
                              </span>
                            )}
                          </div>
                        ) : scene.characters.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {scene.characters.slice(0, 5).map((c) => (
                              <span key={c} className="text-[11px] text-slate-500">{c}</span>
                            ))}
                            {scene.characters.length > 5 && (
                              <span className="text-[11px] text-slate-500">
                                +{scene.characters.length - 5}
                              </span>
                            )}
                          </div>
                        )}

                        {/* Bottom badges: event_type + dialogue count + emotional tone */}
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {scene.event_type && (
                            <span className={`rounded px-1 py-0.5 text-[10px] ${
                              EVENT_TYPE_STYLES[scene.event_type] ?? "bg-slate-800 text-slate-500"
                            }`}>
                              {scene.event_type}
                            </span>
                          )}
                          {scene.dialogue_count > 0 && (
                            <span className="rounded bg-slate-800 px-1 py-0.5 text-[10px] text-slate-500">
                              {scene.dialogue_count} 对话
                            </span>
                          )}
                          {scene.emotional_tone && scene.emotional_tone !== "平静" && (
                            <span className={`rounded px-1 py-0.5 text-[10px] ${
                              TONE_STYLES[scene.emotional_tone] ?? "bg-slate-800 text-slate-500"
                            }`}>
                              {scene.emotional_tone}
                            </span>
                          )}
                        </div>
                      </button>
                    )
                  })
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
