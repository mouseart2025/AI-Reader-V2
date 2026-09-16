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
