/**
 * DemoReadingPage — shows chapter list with reading experience preview.
 * Demo data doesn't include full chapter text, so we show chapter metadata
 * and a preview of the entity highlight feature.
 */
import { useMemo, useState } from "react"
import { useDemoData } from "@/app/DemoContext"
import { useEntityCardStore } from "@/stores/entityCardStore"

interface Chapter {
  chapter_num: number
  title: string
  word_count: number
  analysis_status: string
}

interface EncEntry {
  name: string
  type: string
  first_chapter: number
  chapter_count: number
}

const TYPE_COLORS: Record<string, string> = {
  person: "#3b82f6",
  location: "#10b981",
  item: "#f59e0b",
  org: "#8b5cf6",
  concept: "#6b7280",
}

const TYPE_LABELS: Record<string, string> = {
  person: "人物",
  location: "地点",
  item: "物品",
  org: "组织",
  concept: "概念",
}

export default function DemoReadingPage() {
  const { data, novelInfo } = useDemoData()
  const chapters = data.chapters as Chapter[]
  const encyclopedia = data.encyclopedia as { entries: EncEntry[] }
  const [selectedChapter, setSelectedChapter] = useState(1)
  const openCard = useEntityCardStore((s) => s.openCard)

  // Get entities that first appear in the selected chapter
  const chapterEntities = useMemo(() => {
    return encyclopedia.entries
      .filter((e) => e.first_chapter === selectedChapter)
      .sort((a, b) => b.chapter_count - a.chapter_count)
      .slice(0, 20)
  }, [encyclopedia.entries, selectedChapter])

  // Get all entities appearing by this chapter
  const activeEntities = useMemo(() => {
    return encyclopedia.entries
      .filter((e) => e.first_chapter <= selectedChapter)
      .sort((a, b) => b.chapter_count - a.chapter_count)
      .slice(0, 15)
  }, [encyclopedia.entries, selectedChapter])

  const currentChapter = chapters.find((c) => c.chapter_num === selectedChapter)

  return (
    <div className="flex h-full flex-col">
      {/* Chapter selector bar */}
      <div className="flex items-center gap-3 border-b bg-white/80 px-4 py-2">
        <span className="text-xs text-gray-500">章节</span>
        <select
          value={selectedChapter}
          onChange={(e) => setSelectedChapter(Number(e.target.value))}
          className="rounded-md border border-gray-300 bg-white px-2 py-1 text-sm focus:border-blue-500 focus:outline-none"
        >
          {chapters.map((c) => (
            <option key={c.chapter_num} value={c.chapter_num}>
              第{c.chapter_num}回 {c.title}
            </option>
          ))}
        </select>
        {currentChapter && (
          <span className="text-xs text-gray-400">{currentChapter.word_count.toLocaleString()} 字</span>
        )}
      </div>

      {/* Content area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Main reading area */}
        <div className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto max-w-2xl">
            {/* Chapter title */}
            <h2 className="mb-6 text-center text-xl font-bold text-gray-800">
              第{selectedChapter}回 {currentChapter?.title}
            </h2>

            {/* Demo notice */}
            <div className="mb-6 rounded-lg border border-blue-200 bg-blue-50 p-4">
              <p className="text-sm text-blue-700">
                <span className="mr-1 font-semibold">Demo 模式</span>
                — 在线体验不包含原文内容。安装完整版后，阅读页将显示完整章节文本，并自动高亮所有已识别的实体名称。
              </p>
            </div>

            {/* Simulated entity highlight preview */}
            <div className="mb-6">
              <h3 className="mb-3 text-sm font-semibold text-gray-600">实体高亮预览</h3>
              <div className="rounded-lg border bg-gray-50 p-4 leading-relaxed text-gray-700">
                <p className="mb-2 text-sm text-gray-400">完整版中，文本中的实体名称将按类型着色高亮：</p>
                <div className="flex flex-wrap gap-2">
                  {activeEntities.map((e) => (
                    <button
                      key={e.name}
                      onClick={() => openCard(e.name, e.type as "person" | "location" | "item" | "org")}
                      className="rounded px-2 py-1 text-sm font-medium transition hover:opacity-80"
                      style={{
                        backgroundColor: (TYPE_COLORS[e.type] || "#6b7280") + "15",
                        color: TYPE_COLORS[e.type] || "#6b7280",
                        borderBottom: `2px solid ${TYPE_COLORS[e.type] || "#6b7280"}`,
                      }}
                    >
                      {e.name}
                    </button>
                  ))}
                </div>
                <div className="mt-3 flex gap-4">
                  {Object.entries(TYPE_LABELS).map(([type, label]) => (
                    <span key={type} className="flex items-center gap-1 text-xs text-gray-400">
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{ backgroundColor: TYPE_COLORS[type] }}
                      />
                      {label}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* New entities in this chapter */}
            {chapterEntities.length > 0 && (
              <div className="mb-6">
                <h3 className="mb-3 text-sm font-semibold text-gray-600">
                  本回新登场 ({chapterEntities.length})
                </h3>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {chapterEntities.map((e) => (
                    <button
                      key={e.name}
                      onClick={() => openCard(e.name, e.type as "person" | "location" | "item" | "org")}
                      className="flex items-center gap-2 rounded-lg border bg-white p-2 text-left text-sm hover:border-blue-300 transition"
                    >
                      <span
                        className="inline-block h-2 w-2 rounded-full flex-shrink-0"
                        style={{ backgroundColor: TYPE_COLORS[e.type] || "#6b7280" }}
                      />
                      <span className="truncate">{e.name}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Full version CTA */}
            <div className="mt-8 rounded-lg border-2 border-dashed border-gray-300 bg-gray-50 p-6 text-center">
              <p className="mb-2 text-sm font-semibold text-gray-600">完整版支持全部 {chapters.length} 回章节浏览</p>
              <p className="mb-4 text-xs text-gray-400">
                实体高亮 · 场景面板 · 章节间导航 · 书签 · 键盘快捷键
              </p>
              <a
                href="https://github.com/mouseart2025/AI-Reader-V2"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block rounded-md bg-blue-500 px-6 py-2 text-sm font-semibold text-white hover:bg-blue-600 transition"
              >
                免费下载完整版
              </a>
            </div>
          </div>
        </div>

        {/* Chapter list sidebar (desktop only) */}
        <aside className="hidden w-64 overflow-y-auto border-l bg-gray-50 p-3 lg:block">
          <h3 className="mb-2 text-xs font-semibold text-gray-500">{novelInfo.title} · {chapters.length} 回</h3>
          <div className="space-y-0.5">
            {chapters.map((c) => (
              <button
                key={c.chapter_num}
                onClick={() => setSelectedChapter(c.chapter_num)}
                className={`w-full rounded px-2 py-1.5 text-left text-xs transition ${
                  selectedChapter === c.chapter_num
                    ? "bg-blue-50 text-blue-700 font-medium"
                    : "text-gray-600 hover:bg-gray-100"
                }`}
              >
                <span className="text-gray-400">第{c.chapter_num}回</span> {c.title}
              </button>
            ))}
          </div>
        </aside>
      </div>
    </div>
  )
}
