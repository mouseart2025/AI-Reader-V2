import type { LocationProfile } from "@/api/types"

interface LocationMiniMapProps {
  profile: LocationProfile
  onEntityClick: (name: string, type: string) => void
}

export function LocationMiniMap({ profile, onEntityClick }: LocationMiniMapProps) {
  if (!profile.parent) return null

  const siblings = (profile.siblings ?? []).filter((s) => s !== profile.name)
  const allChildren = [profile.name, ...siblings]
  const total = allChildren.length
  const svgW = 200
  const svgH = total > 4 ? 140 : 120

  // Layout: parent centered on top, children evenly spaced below
  const parentX = svgW / 2
  const parentY = 24
  const childY = svgH - 28
  const childSpacing = Math.min(40, (svgW - 40) / Math.max(total - 1, 1))
  const childStartX = (svgW - childSpacing * (total - 1)) / 2

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
      {allChildren.map((name, i) => {
        const cx = total === 1 ? svgW / 2 : childStartX + i * childSpacing
        const isCurrent = name === profile.name
        return (
          <g key={name}>
            <line
              x1={parentX}
              y1={parentY + 6}
              x2={cx}
              y2={childY - 10}
              className="stroke-muted-foreground/40"
              strokeWidth={1}
            />
            <circle
              cx={cx}
              cy={childY - 4}
              r={4}
              className={isCurrent ? "fill-green-500" : "fill-muted-foreground/50"}
            />
            <text
              x={cx}
              y={childY + 10}
              textAnchor="middle"
              className={`text-[9px] cursor-pointer hover:fill-foreground ${isCurrent ? "fill-foreground font-bold" : "fill-muted-foreground"}`}
              onClick={() => onEntityClick(name, "location")}
            >
              {name.length > 5 ? name.slice(0, 5) + "…" : name}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
