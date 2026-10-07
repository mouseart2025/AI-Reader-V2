/**
 * DesktopNovelCard — 桌面版书架小说卡片（设计改版）
 *
 * 设计语言：
 * - 每本书由书名哈希出确定性色相，以 3px 书脊色条 + 顶部同色微光呈现——
 *   一整屏卡片因此可扫读、可辨识，而不是一排相同的灰块
 * - 状态用色彩分层：分析完成=emerald、分析中=blue 脉动+进度、失败=amber、
 *   未分析=muted；分析/阅读两条 2px 细进度轨沉在卡片底部，平时不抢眼
 * - 排版层级：标题(15px semibold, clamp-2) > 统计(xs tabular) > 状态(xs)
 */

import type { Novel } from "@/api/types"
import { CheckCircle2, Trash2 } from "lucide-react"

/** 书名 → 0-360 确定性色相 */
function titleHue(title: string): number {
  let hash = 0
  for (let i = 0; i < title.length; i++) {
    hash = (hash * 31 + title.charCodeAt(i)) | 0
  }
  return Math.abs(hash) % 360
}

function formatWordCount(count: number): string {
  if (count >= 10000) return `${(count / 10000).toFixed(1).replace(/\.0$/, "")} 万字`
  return `${count} 字`
}

interface DesktopNovelCardProps {
  novel: Novel
  analysisStatus: "running" | "paused" | null
  onClick: () => void
  onDelete: () => void
}

export function DesktopNovelCard({ novel, analysisStatus, onClick, onDelete }: DesktopNovelCardProps) {
  const hue = titleHue(novel.title)
  const spine = `hsl(${hue}, 70%, 60%)`
  const glow = `linear-gradient(180deg, hsl(${hue}, 70%, 60%, 0.16), transparent 60%)`
  const progress = novel.analysis_progress
  const failed = novel.failed_count > 0
  const done = progress >= 1 && !failed
  const running = analysisStatus === "running"

  return (
    <div className="group relative">
      <button
        onClick={onClick}
        className="flex h-full w-full cursor-pointer flex-col overflow-hidden rounded-xl border border-border bg-card text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-slate-500/60 hover:shadow-lg hover:shadow-black/30"
      >
        {/* 书脊色条 + 顶部同色微光 */}
        <div className="absolute inset-y-0 left-0 w-[3px]" style={{ background: spine }} />
        <div className="absolute inset-x-0 top-0 h-16" style={{ background: glow }} />

        <div className="relative flex-1 px-4 pt-3.5 pb-2 pl-5">
          {/* 状态 badge（分析中/已暂停） */}
          {running && (
            <div className="absolute top-2.5 right-3 flex items-center gap-1.5 rounded-full bg-blue-500/15 px-2 py-0.5">
              <span className="inline-block size-1.5 animate-pulse rounded-full bg-blue-400" />
              <span className="text-[10px] font-medium text-blue-400">分析中</span>
            </div>
          )}
          {analysisStatus === "paused" && (
            <div className="absolute top-2.5 right-3 flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2 py-0.5">
              <span className="inline-block size-1.5 rounded-full bg-amber-400" />
              <span className="text-[10px] font-medium text-amber-400">已暂停</span>
            </div>
          )}

          <h3 className="line-clamp-2 pr-2 text-[15px] leading-snug font-semibold text-foreground">
            {novel.title}
          </h3>
          {novel.author && (
            <p className="mt-1 truncate text-xs text-muted-foreground">{novel.author}</p>
          )}
          <p className="mt-2.5 text-xs text-muted-foreground/80 tabular-nums">
            {novel.total_chapters} 章 · {formatWordCount(novel.total_words)}
          </p>

          {/* 状态行：色彩分层 */}
          <div className="mt-2 flex h-4 items-center text-xs">
            {done ? (
              <span className="flex items-center gap-1 text-emerald-400">
                <CheckCircle2 className="size-3.5" />
                分析完成
              </span>
            ) : progress >= 1 && failed ? (
              <span className="flex items-center gap-1 text-amber-400">
                <CheckCircle2 className="size-3.5" />
                分析完成（{novel.failed_count} 章失败）
              </span>
            ) : running ? (
              <span className="text-blue-400 tabular-nums">
                分析 {Math.round(progress * 100)}%
                {failed && <span className="ml-1 text-amber-400">({novel.failed_count} 章失败)</span>}
              </span>
            ) : progress > 0 ? (
              <span className="text-muted-foreground tabular-nums">
                分析 {Math.round(progress * 100)}%
                {failed && <span className="ml-1 text-amber-400">({novel.failed_count} 章失败)</span>}
              </span>
            ) : (
              <span className="text-muted-foreground/60">未分析</span>
            )}
          </div>
        </div>

        {/* 底部细进度轨：分析（仅未完成时显示——满格无信息量）+ 阅读（已开始阅读时可见） */}
        <div className="relative mt-auto">
          {progress < 1 && (
            <div className="h-0.5 w-full bg-slate-700/30">
              <div
                className="h-full bg-blue-500/80 transition-all"
                style={{ width: `${Math.min(progress * 100, 100)}%` }}
              />
            </div>
          )}
          {novel.reading_progress > 0 && (
            <div className="h-0.5 w-full bg-slate-700/30">
              <div
                className="h-full bg-indigo-400/70"
                style={{ width: `${Math.min(novel.reading_progress * 100, 100)}%` }}
              />
            </div>
          )}
        </div>
      </button>

      {/* Delete button — hover 显现 */}
      <button
        onClick={(e) => {
          e.stopPropagation()
          onDelete()
        }}
        className="absolute right-2 bottom-2 rounded-md p-1.5 text-muted-foreground/50 opacity-0 transition hover:bg-red-500/15 hover:text-red-400 group-hover:opacity-100"
        title="删除"
      >
        <Trash2 className="size-3.5" />
      </button>
    </div>
  )
}
