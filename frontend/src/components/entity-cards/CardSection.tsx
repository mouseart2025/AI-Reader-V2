import { useState } from "react"
import { cn } from "@/lib/utils"

interface CardSectionProps {
  title: string
  count?: number
  defaultLimit?: number
  children: React.ReactNode[]
  emptyText?: string
}

export function CardSection({
  title,
  count,
  defaultLimit = 5,
  children,
  emptyText = "暂无数据",
}: CardSectionProps) {
  const [expanded, setExpanded] = useState(false)
  const total = count ?? children.length
  const showToggle = total > defaultLimit
  const visible = expanded ? children : children.slice(0, defaultLimit)

  return (
    <div className="border-b py-3 last:border-b-0">
      <h4 className="text-muted-foreground mb-2 text-xs font-medium uppercase tracking-wide">
        {title}
        {total > 0 && (
          <span className="text-muted-foreground/60 ml-1">({total})</span>
        )}
      </h4>
      {total === 0 ? (
        <p className="text-muted-foreground text-sm">{emptyText}</p>
      ) : (
        <>
          <div className="space-y-1.5">{visible}</div>
          {showToggle && (
            <button
              className="text-primary mt-2 text-xs hover:underline"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded
                ? "收起"
                : `共 ${total} 项 ▸ 查看全部`}
            </button>
          )}
        </>
      )}
    </div>
  )
}

export function ChapterTag({ chapter }: { chapter: number }) {
  return (
    <span className="text-muted-foreground inline-block rounded bg-muted px-1.5 py-0.5 text-[10px]">
      Ch.{chapter}
    </span>
  )
}

export function EntityLink({
  name,
  type,
  onClick,
}: {
  name: string
  type: string
  onClick: (name: string, type: string) => void
}) {
  const colorMap: Record<string, string> = {
    person: "text-blue-600 dark:text-blue-400",
    location: "text-green-700 dark:text-green-400",
    item: "text-orange-600 dark:text-orange-400",
    org: "text-purple-600 dark:text-purple-400",
    concept: "text-gray-600 dark:text-gray-400",
  }
  return (
    <button
      className={cn(
        "hover:underline",
        colorMap[type] ?? "text-primary",
      )}
      onClick={() => onClick(name, type)}
    >
      {name}
    </button>
  )
}
