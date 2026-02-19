import { cn } from "@/lib/utils"
import type { Scene } from "@/api/types"
import { Button } from "@/components/ui/button"

// ── Scene border colors (one per scene, cycling) ─

export const SCENE_BORDER_COLORS = [
  "border-l-blue-500",
  "border-l-emerald-500",
  "border-l-amber-500",
  "border-l-purple-500",
  "border-l-rose-500",
  "border-l-cyan-500",
  "border-l-indigo-500",
  "border-l-lime-500",
]

// ── Tone / event-type styling ───────────────────

export const TONE_STYLES: Record<string, string> = {
  "战斗": "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  "紧张": "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
  "悲伤": "bg-slate-100 text-slate-700 dark:bg-slate-800/50 dark:text-slate-300",
  "欢乐": "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300",
  "平静": "bg-sky-50 text-sky-600 dark:bg-sky-900/30 dark:text-sky-300",
}

export const EVENT_TYPE_STYLES: Record<string, string> = {
  "对话": "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  "战斗": "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  "旅行": "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  "描写": "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300",
  "回忆": "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
}

// ── Scene Card ──────────────────────────────────

export function SceneCard({
  scene,
  active,
  borderColor,
  onClick,
}: {
  scene: Scene
  active: boolean
  borderColor: string
  onClick: () => void
}) {
  return (
    <button
      className={cn(
        "mb-2 w-full rounded-lg border-l-3 border p-3 text-left transition-colors",
        borderColor,
        active
          ? "border-r-primary border-y-primary bg-primary/5"
          : "border-r-border border-y-border hover:border-r-primary/50 hover:border-y-primary/50",
      )}
      onClick={onClick}
    >
      {/* Header: scene number + time */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
            {scene.index + 1}
          </span>
          <span className="truncate text-sm font-medium">{scene.title}</span>
        </div>
        {scene.time_of_day && (
          <span className="shrink-0 text-[10px] text-muted-foreground">
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
          <span className="inline-block rounded bg-green-100 px-1 py-0.5 text-[11px] text-green-700 dark:bg-green-900/30 dark:text-green-300">
            {scene.location}
          </span>
        </div>
      )}

      {/* Key dialogue (first line only, if available) */}
      {scene.key_dialogue && scene.key_dialogue.length > 0 && (
        <p className="mt-1.5 truncate text-xs italic text-muted-foreground">
          {scene.key_dialogue[0]}
        </p>
      )}

      {/* Characters with roles */}
      {scene.character_roles && scene.character_roles.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {scene.character_roles.slice(0, 6).map((cr) => (
            <span
              key={cr.name}
              className={cn(
                "text-[11px]",
                cr.role === "主" ? "font-medium text-foreground" : "text-muted-foreground",
              )}
            >
              {cr.name}
              {cr.role === "主" && <span className="text-[9px]">(主)</span>}
            </span>
          ))}
          {scene.character_roles.length > 6 && (
            <span className="text-[11px] text-muted-foreground">
              +{scene.character_roles.length - 6}
            </span>
          )}
        </div>
      ) : scene.characters.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {scene.characters.slice(0, 5).map((c) => (
            <span key={c} className="text-[11px] text-muted-foreground">
              {c}
            </span>
          ))}
          {scene.characters.length > 5 && (
            <span className="text-[11px] text-muted-foreground">
              +{scene.characters.length - 5}
            </span>
          )}
        </div>
      )}

      {/* Bottom: event_type + dialogue count + emotional tone */}
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {scene.event_type && (
          <span className={cn(
            "rounded px-1 py-0.5 text-[10px]",
            EVENT_TYPE_STYLES[scene.event_type] ?? "bg-muted text-muted-foreground",
          )}>
            {scene.event_type}
          </span>
        )}
        {scene.dialogue_count > 0 && (
          <span className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
            {scene.dialogue_count} 对话
          </span>
        )}
        {scene.emotional_tone && scene.emotional_tone !== "平静" && (
          <span className={cn(
            "rounded px-1 py-0.5 text-[10px]",
            TONE_STYLES[scene.emotional_tone] ?? "bg-muted text-muted-foreground",
          )}>
            {scene.emotional_tone}
          </span>
        )}
      </div>
    </button>
  )
}

// ── Scene Panel (right sidebar for ReadingPage) ─

export function ScenePanel({
  scenes,
  activeSceneIndex,
  analysisStatus,
  loading,
  onSceneClick,
  onClose,
  onGoAnalysis,
}: {
  scenes: Scene[]
  activeSceneIndex: number
  analysisStatus?: string
  loading?: boolean
  onSceneClick: (scene: Scene, index: number) => void
  onClose: () => void
  onGoAnalysis?: () => void
}) {
  const isAnalyzed = analysisStatus === "completed"

  return (
    <div className="flex h-full w-72 shrink-0 flex-col border-l">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-sm font-medium">剧本场景</span>
        <Button variant="ghost" size="icon-xs" onClick={onClose} title="收起">
          <XIcon className="size-4" />
        </Button>
      </div>

      {/* Not analyzed prompt */}
      {!isAnalyzed && !loading ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
          <div className="rounded-full bg-muted p-3">
            <AnalysisIcon className="size-5 text-muted-foreground" />
          </div>
          <p className="text-sm text-muted-foreground">
            当前章节尚未分析，无法生成场景数据
          </p>
          {onGoAnalysis && (
            <Button size="sm" onClick={onGoAnalysis}>
              前往分析
            </Button>
          )}
        </div>
      ) : (
        <>
          {/* Scene count */}
          <div className="border-b px-3 py-1.5">
            <span className="text-xs text-muted-foreground">
              {loading ? "加载中..." : `${scenes.length} 个场景`}
            </span>
          </div>

          {/* Scene list */}
          <div className="flex-1 overflow-y-auto p-2">
            {loading ? (
              <p className="text-sm text-muted-foreground">加载场景...</p>
            ) : scenes.length === 0 ? (
              <p className="text-sm text-muted-foreground">未检测到场景</p>
            ) : (
              scenes.map((scene, i) => (
                <SceneCard
                  key={i}
                  scene={scene}
                  active={i === activeSceneIndex}
                  borderColor={SCENE_BORDER_COLORS[i % SCENE_BORDER_COLORS.length]}
                  onClick={() => onSceneClick(scene, i)}
                />
              ))
            )}
          </div>
        </>
      )}
    </div>
  )
}

function AnalysisIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M3 3v16a2 2 0 0 0 2 2h16" />
      <path d="m7 11 4-4 4 4 4-4" />
    </svg>
  )
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  )
}
