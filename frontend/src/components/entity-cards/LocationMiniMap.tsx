import type { LocationProfile } from "@/api/types"

interface LocationMiniMapProps {
  profile: LocationProfile
  onEntityClick: (name: string, type: string) => void
}

const MAX_VISIBLE = 7

export function LocationMiniMap({ profile, onEntityClick }: LocationMiniMapProps) {
  if (!profile.parent) return null

  const siblings = (profile.siblings ?? []).filter((s) => s !== profile.name)
  const allChildren = [profile.name, ...siblings]
  const total = allChildren.length

  // Collapse when too many siblings: show current + neighbors + "+N" badge
  let displayItems: { name: string; isCurrent: boolean; isOverflow?: boolean; count?: number }[]
  if (total > MAX_VISIBLE) {
    const currentIdx = 0 // profile.name is always first
    const nearby = allChildren.slice(currentIdx, currentIdx + MAX_VISIBLE - 1)
    const remaining = total - nearby.length
    displayItems = nearby.map((name) => ({ name, isCurrent: name === profile.name }))
    if (remaining > 0) {
      displayItems.push({ name: `+${remaining}`, isCurrent: false, isOverflow: true, count: remaining })
    }
  } else {
    displayItems = allChildren.map((name) => ({ name, isCurrent: name === profile.name }))
  }

  const displayTotal = displayItems.length
  const svgW = Math.max(200, displayTotal * 32)
  const svgH = displayTotal > 4 ? 140 : 120

  // Layout: parent centered on top, children evenly spaced below
  const parentX = svgW / 2
  const parentY = 24
  const childY = svgH - 28
  const childSpacing = Math.min(40, (svgW - 40) / Math.max(displayTotal - 1, 1))
  const childStartX = (svgW - childSpacing * (displayTotal - 1)) / 2

  return (
    <svg
      viewBox={`0 0 ${svgW} ${svgH}`}
      className="w-full border rounded bg-muted/30"
      style={{ maxHeight: `${svgH}px` }}
    >
      {/* Parent node */}
      <text
        x={parentX}
        y={parentY}
        textAnchor="middle"
        className="fill-muted-foreground text-[10px] cursor-pointer hover:fill-foreground"
        onClick={() => onEntityClick(profile.parent!, "location")}
      >
        {profile.parent}
      </text>

      {/* Connection lines + child nodes */}
      {displayItems.map((item, i) => {
        const cx = displayTotal === 1 ? svgW / 2 : childStartX + i * childSpacing
        return (
          <g key={item.name}>
            <line
              x1={parentX}
              y1={parentY + 6}
              x2={cx}
              y2={childY - 10}
              className="stroke-muted-foreground/40"
              strokeWidth={1}
            />
            {item.isOverflow ? (
              <>
                <circle cx={cx} cy={childY - 4} r={6} className="fill-muted-foreground/20" />
                <text
                  x={cx} y={childY - 1}
                  textAnchor="middle"
                  className="fill-muted-foreground text-[8px]"
                >
                  {item.name}
                </text>
              </>
            ) : (
              <>
                <circle
                  cx={cx}
                  cy={childY - 4}
                  r={4}
                  className={item.isCurrent ? "fill-green-500" : "fill-muted-foreground/50"}
                />
                <text
                  x={cx}
                  y={childY + 10}
                  textAnchor="middle"
                  className={`text-[9px] cursor-pointer hover:fill-foreground ${item.isCurrent ? "fill-foreground font-bold" : "fill-muted-foreground"}`}
                  onClick={() => onEntityClick(item.name, "location")}
                >
                  {item.name.length > 5 ? item.name.slice(0, 5) + "\u2026" : item.name}
                </text>
              </>
            )}
          </g>
        )
      })}
    </svg>
  )
}
