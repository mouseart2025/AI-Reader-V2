/**
 * BookshelfCard — 桌面版书架小说卡片
 * 展示标题、统计摘要、来源标识，点击导航到小说阅读页
 * 导入小说可删除
 */

import type { NovelListItem } from "@/providers/types"
import { Trash2 } from "lucide-react"

interface BookshelfCardProps {
  novel: NovelListItem & { source?: "preinstalled" | "imported" }
  onClick: () => void
  onDelete?: (slug: string, title: string) => void
}

export function BookshelfCard({ novel, onClick, onDelete }: BookshelfCardProps) {
  const stats = novel.stats
  const isImported = novel.source === "imported"

  return (
    <div className="group relative">
      <button
        onClick={onClick}
        className="w-full cursor-pointer rounded-lg border border-slate-700/50 bg-slate-900 p-4 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-slate-600 hover:bg-slate-800"
      >
        <h3 className="mb-1 text-lg font-bold text-slate-100">{novel.title}</h3>
        {novel.author && (
          <p className="mb-3 text-sm text-slate-400">{novel.author}</p>
        )}

        {stats && (
          <p className="mb-3 text-xs text-slate-400">
            {stats.characters}人物 · {stats.locations}地点 · {novel.totalChapters}章
          </p>
        )}
        {!stats && (
          <p className="mb-3 text-xs text-slate-400">{novel.totalChapters}章</p>
        )}

        <div className="border-t border-slate-700/50 pt-2">
          <span className="text-xs text-slate-500">
            {isImported ? "导入数据" : "预装数据"}
          </span>
        </div>
      </button>

      {/* Delete button — only for imported novels */}
      {isImported && onDelete && (
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete(novel.slug, novel.title)
          }}
          className="absolute right-2 top-2 rounded-md p-1.5 text-slate-500 opacity-0 transition hover:bg-red-500/20 hover:text-red-400 group-hover:opacity-100"
          title="删除"
        >
          <Trash2 className="size-4" />
        </button>
      )}
    </div>
  )
}
