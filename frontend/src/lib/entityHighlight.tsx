import type { ChapterEntity } from "@/api/types"
import { cn } from "@/lib/utils"

// ── Entity highlighting colors ───────────────────

export const ENTITY_COLORS: Record<string, string> = {
  person: "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/40",
  location: "text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-950/40",
  item: "text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-950/40",
  org: "text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-950/40",
  concept: "text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-800/50",
}

// ── Highlight chapter text with entities ─────────

export function highlightText(
  text: string,
  entities: ChapterEntity[],
  onEntityClick?: (name: string, type: string) => void,
) {
  if (entities.length === 0) return text

  // Filter out single-character entities (common nouns like 书/饭/茶)
  const filtered = entities.filter((e) => e.name.length >= 2)
  if (filtered.length === 0) return text

  // Sort by name length desc so longer names match first
  const sorted = [...filtered].sort((a, b) => b.name.length - a.name.length)

  // Build regex pattern escaping special chars
  const pattern = sorted
    .map((e) => e.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|")
  const regex = new RegExp(`(${pattern})`, "g")
  const entityMap = new Map(sorted.map((e) => [e.name, e.type]))

  const parts = text.split(regex)
  return parts.map((part, i) => {
    const type = entityMap.get(part)
    if (type) {
      return (
        <span
          key={i}
          className={cn(
            "cursor-pointer rounded-sm px-0.5 transition-colors hover:opacity-80",
            ENTITY_COLORS[type] ?? "",
          )}
          title={`${part} (${type})`}
          onClick={() => onEntityClick?.(part, type)}
        >
          {part}
        </span>
      )
    }
    return part
  })
}
