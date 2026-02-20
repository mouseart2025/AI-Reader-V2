import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d"
import { fetchFactionsData } from "@/api/client"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { VisualizationLayout } from "@/components/visualization/VisualizationLayout"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { trackEvent } from "@/lib/tracker"

interface OrgNode {
  id: string
  name: string
  type: string
  member_count: number
  x?: number
  y?: number
}

interface OrgRelation {
  source: string | OrgNode
  target: string | OrgNode
  type: string
  chapter: number
}

interface OrgMember {
  person: string
  role: string
  status: string
}

// Color by org type
const ORG_TYPE_COLORS: Record<string, string> = {
  "宗门": "#8b5cf6",
  "国家": "#3b82f6",
  "家族": "#f59e0b",
  "帮派": "#ef4444",
  "商会": "#10b981",
  "军队": "#6366f1",
}

function orgColor(type: string): string {
  for (const [key, color] of Object.entries(ORG_TYPE_COLORS)) {
    if (type.includes(key)) return color
  }
  return "#6b7280"
}

// Relation type to edge style
function relationStyle(type: string): { color: string; dash: number[] } {
  const t = type.toLowerCase()
  if (t.includes("盟") || t.includes("友")) return { color: "#10b981", dash: [] }
  if (t.includes("敌") || t.includes("对")) return { color: "#ef4444", dash: [] }
  if (t.includes("从属") || t.includes("附")) return { color: "#3b82f6", dash: [5, 3] }
  if (t.includes("竞争")) return { color: "#f59e0b", dash: [3, 3] }
  return { color: "#9ca3af", dash: [] }
}

export default function FactionsPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const { chapterStart, chapterEnd, setAnalyzedRange } = useChapterRangeStore()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  const [orgs, setOrgs] = useState<OrgNode[]>([])
  const [relations, setRelations] = useState<OrgRelation[]>([])
  const [members, setMembers] = useState<Record<string, OrgMember[]>>({})
  const [loading, setLoading] = useState(true)
  const [hoverNode, setHoverNode] = useState<string | null>(null)
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null)
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set(["all"]))
  const [expandedOrgs, setExpandedOrgs] = useState<Set<string>>(new Set())

  const graphRef = useRef<ForceGraphMethods<OrgNode, OrgRelation>>(undefined)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })

  // Resize observer
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      setDimensions({ width, height })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // Load data
  useEffect(() => {
    if (!novelId) return
    let cancelled = false
    setLoading(true)
    trackEvent("view_factions")

    fetchFactionsData(novelId, chapterStart, chapterEnd)
      .then((data) => {
        if (cancelled) return
        const range = data.analyzed_range as number[]
        if (range && range[0] > 0) {
          setAnalyzedRange(range[0], range[1])
        }
        setOrgs((data.orgs as OrgNode[]) ?? [])
        setRelations((data.relations as OrgRelation[]) ?? [])
        setMembers((data.members as Record<string, OrgMember[]>) ?? {})
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [novelId, chapterStart, chapterEnd, setAnalyzedRange])

  const toggleTypeFilter = useCallback((type: string) => {
    setFilterTypes((prev) => {
      const next = new Set(prev)
      if (type === "all") return new Set(["all"])
      next.delete("all")
      if (next.has(type)) {
        next.delete(type)
        return next.size === 0 ? new Set(["all"]) : next
      }
      next.add(type)
      return next
    })
  }, [])

  const toggleOrgExpand = useCallback((orgName: string) => {
    setExpandedOrgs((prev) => {
      const next = new Set(prev)
      if (next.has(orgName)) next.delete(orgName)
      else next.add(orgName)
      return next
    })
  }, [])

  // Available org types for filter
  const availableTypes = useMemo(() => {
    const types = new Set<string>()
    for (const org of orgs) {
      for (const [key] of Object.entries(ORG_TYPE_COLORS)) {
        if (org.type.includes(key)) {
          types.add(key)
          break
        }
      }
    }
    return Array.from(types).sort()
  }, [orgs])

  // Filtered orgs
  const filteredOrgs = useMemo(() => {
    if (filterTypes.has("all")) return orgs
    return orgs.filter((o) => {
      for (const t of filterTypes) {
        if (o.type.includes(t)) return true
      }
      return false
    })
  }, [orgs, filterTypes])

  const filteredOrgIds = useMemo(
    () => new Set(filteredOrgs.map((o) => o.id)),
    [filteredOrgs],
  )

  const filteredRelations = useMemo(
    () =>
      relations.filter((r) => {
        const src = typeof r.source === "string" ? r.source : r.source.id
        const tgt = typeof r.target === "string" ? r.target : r.target.id
        return filteredOrgIds.has(src) && filteredOrgIds.has(tgt)
      }),
    [relations, filteredOrgIds],
  )

  // Connected nodes on hover
  const connectedNodes = useMemo(() => {
    if (!hoverNode) return new Set<string>()
    const connected = new Set<string>([hoverNode])
    for (const r of relations) {
      const src = typeof r.source === "string" ? r.source : r.source.id
      const tgt = typeof r.target === "string" ? r.target : r.target.id
      if (src === hoverNode) connected.add(tgt)
      if (tgt === hoverNode) connected.add(src)
    }
    return connected
  }, [hoverNode, relations])

  const graphData = useMemo(
    () => ({ nodes: filteredOrgs, links: filteredRelations }),
    [filteredOrgs, filteredRelations],
  )

  const handleNodeClick = useCallback(
    (node: OrgNode) => {
      setSelectedOrg(selectedOrg === node.name ? null : node.name)
    },
    [selectedOrg],
  )

  return (
    <VisualizationLayout>
      <div className="flex h-full flex-col">
        {/* Toolbar */}
        {availableTypes.length > 0 && (
          <div className="flex items-center gap-2 border-b px-4 py-2 flex-shrink-0">
            <span className="text-xs text-muted-foreground mr-1">类型筛选</span>
            <Button
              variant={filterTypes.has("all") ? "default" : "outline"}
              size="xs"
              onClick={() => toggleTypeFilter("all")}
            >
              全部
            </Button>
            {availableTypes.map((t) => (
              <Button
                key={t}
                variant={filterTypes.has(t) ? "default" : "outline"}
                size="xs"
                onClick={() => toggleTypeFilter(t)}
              >
                <span
                  className="inline-block size-2 rounded-full mr-1"
                  style={{ backgroundColor: ORG_TYPE_COLORS[t] || "#6b7280" }}
                />
                {t}
              </Button>
            ))}
            <div className="flex-1" />
            <span className="text-xs text-muted-foreground">
              {filteredOrgs.length} / {orgs.length} 组织
            </span>
          </div>
        )}

        <div className="flex flex-1 overflow-hidden">
        {/* Graph area */}
        <div className="relative flex-1" ref={containerRef}>
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
              <p className="text-muted-foreground">Loading factions...</p>
            </div>
          )}

          {/* Legend */}
          <div className="absolute top-3 left-3 z-10 rounded-lg border bg-background/90 p-2">
            <p className="text-muted-foreground mb-1 text-[10px]">组织类型</p>
            {Object.entries(ORG_TYPE_COLORS).map(([type, color]) => (
              <div key={type} className="flex items-center gap-1.5 text-xs">
                <span
                  className="inline-block size-2.5 rounded-full"
                  style={{ backgroundColor: color }}
                />
                {type}
              </div>
            ))}
            <div className="mt-2 border-t pt-1.5">
              <p className="text-muted-foreground mb-1 text-[10px]">关系</p>
              <div className="flex items-center gap-1.5 text-xs">
                <span className="inline-block w-4 h-0.5 bg-green-500" />盟友
              </div>
              <div className="flex items-center gap-1.5 text-xs">
                <span className="inline-block w-4 h-0.5 bg-red-500" />敌对
              </div>
              <div className="flex items-center gap-1.5 text-xs">
                <span className="inline-block w-4 h-0.5 border-t border-dashed border-blue-500" />从属
              </div>
            </div>
            <p className="text-muted-foreground mt-1.5 text-[10px]">单击选中·双击查看卡片</p>
          </div>

          <ForceGraph2D
            ref={graphRef}
            graphData={graphData}
            width={dimensions.width}
            height={dimensions.height}
            nodeLabel={(node: OrgNode) =>
              `${node.name} (${node.type}, ${node.member_count}人)`
            }
            nodeVal={(node: OrgNode) => Math.max(3, Math.sqrt(node.member_count) * 3)}
            nodeCanvasObject={(node: OrgNode, ctx, globalScale) => {
              const size = Math.max(5, Math.sqrt(node.member_count) * 2.5)
              const color =
                hoverNode && !connectedNodes.has(node.id)
                  ? "#d1d5db"
                  : orgColor(node.type)

              // Draw hexagon for org nodes
              ctx.beginPath()
              for (let i = 0; i < 6; i++) {
                const angle = (Math.PI / 3) * i - Math.PI / 6
                const px = node.x! + size * Math.cos(angle)
                const py = node.y! + size * Math.sin(angle)
                if (i === 0) ctx.moveTo(px, py)
                else ctx.lineTo(px, py)
              }
              ctx.closePath()
              ctx.fillStyle = color
              ctx.fill()

              // Selected ring
              if (selectedOrg === node.name) {
                ctx.strokeStyle = color
                ctx.lineWidth = 2 / globalScale
                ctx.stroke()
              }

              // Label
              if (globalScale > 1 || node.member_count >= 3) {
                const fontSize = Math.max(10, 12 / globalScale)
                ctx.font = `${fontSize}px sans-serif`
                ctx.textAlign = "center"
                ctx.textBaseline = "top"
                ctx.fillStyle = hoverNode && !connectedNodes.has(node.id) ? "#9ca3af" : "#374151"
                ctx.fillText(node.name, node.x!, node.y! + size + 2)
              }
            }}
            linkCanvasObject={(link: OrgRelation, ctx) => {
              const src = typeof link.source === "string" ? null : link.source
              const tgt = typeof link.target === "string" ? null : link.target
              if (!src || !tgt) return

              const style = relationStyle(link.type)
              ctx.beginPath()
              if (style.dash.length > 0) {
                ctx.setLineDash(style.dash)
              } else {
                ctx.setLineDash([])
              }

              // Dim if hovering on unrelated
              if (hoverNode) {
                const srcId = src.id
                const tgtId = tgt.id
                if (!connectedNodes.has(srcId) || !connectedNodes.has(tgtId)) {
                  ctx.strokeStyle = "#e5e7eb"
                  ctx.lineWidth = 0.5
                } else {
                  ctx.strokeStyle = style.color
                  ctx.lineWidth = 1.5
                }
              } else {
                ctx.strokeStyle = style.color
                ctx.lineWidth = 1.5
              }

              ctx.moveTo(src.x!, src.y!)
              ctx.lineTo(tgt.x!, tgt.y!)
              ctx.stroke()
              ctx.setLineDash([])

              // Mid-point label
              const mx = (src.x! + tgt.x!) / 2
              const my = (src.y! + tgt.y!) / 2
              ctx.font = "8px sans-serif"
              ctx.textAlign = "center"
              ctx.textBaseline = "middle"
              ctx.fillStyle = style.color
              ctx.fillText(link.type, mx, my - 5)
            }}
            linkWidth={0}
            onNodeClick={handleNodeClick}
            onNodeHover={(node: OrgNode | null) => setHoverNode(node?.id ?? null)}
            cooldownTicks={80}
            d3AlphaDecay={0.025}
            d3VelocityDecay={0.3}
          />
        </div>

        {/* Right: Members panel */}
        <div className="w-72 flex-shrink-0 border-l overflow-auto">
          <div className="p-3">
            <h3 className="text-sm font-medium mb-2">组织成员</h3>
            <p className="text-[10px] text-muted-foreground mb-3">
              点击展开/折叠成员列表
            </p>

            <div className="space-y-1">
              {filteredOrgs.map((org) => {
                const orgMembers = members[org.name] ?? []
                const isExpanded = expandedOrgs.has(org.name) || selectedOrg === org.name

                return (
                  <div key={org.id}>
                    <button
                      className={cn(
                        "w-full flex items-center gap-2 text-xs px-2 py-1.5 rounded-md transition-colors",
                        selectedOrg === org.name
                          ? "bg-primary/10 font-medium"
                          : "hover:bg-muted/50",
                      )}
                      onClick={() => {
                        toggleOrgExpand(org.name)
                        setSelectedOrg(selectedOrg === org.name ? null : org.name)
                      }}
                    >
                      <span
                        className="inline-block size-2.5 rounded flex-shrink-0"
                        style={{ backgroundColor: orgColor(org.type) }}
                      />
                      <span className="flex-1 text-left truncate">{org.name}</span>
                      <span className="text-muted-foreground flex-shrink-0">
                        {orgMembers.length}人
                      </span>
                      <span className="text-muted-foreground text-[10px]">
                        {isExpanded ? "▾" : "▸"}
                      </span>
                    </button>

                    {isExpanded && orgMembers.length > 0 && (
                      <div className="ml-4 mt-0.5 mb-1 space-y-0.5">
                        {orgMembers.map((m) => (
                          <div
                            key={`${org.id}-${m.person}-${m.role}`}
                            className="flex items-center gap-2 text-xs px-2 py-1 rounded-md hover:bg-muted/50"
                          >
                            <button
                              className="text-blue-600 hover:underline flex-1 text-left truncate"
                              onClick={() => openEntityCard(m.person, "person")}
                            >
                              {m.person}
                            </button>

                            {m.role && (
                              <span className="text-muted-foreground flex-shrink-0">
                                {m.role}
                              </span>
                            )}

                            <span
                              className={cn(
                                "text-[10px] px-1 py-0.5 rounded flex-shrink-0",
                                m.status === "离开" || m.status === "叛出" || m.status === "阵亡"
                                  ? "bg-red-50 text-red-600 dark:bg-red-950/30"
                                  : "bg-green-50 text-green-600 dark:bg-green-950/30",
                              )}
                            >
                              {m.status || "在籍"}
                            </span>
                          </div>
                        ))}
                        <button
                          className="text-[10px] text-muted-foreground hover:text-foreground px-2"
                          onClick={() => openEntityCard(org.name, "org")}
                        >
                          查看组织详情
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        {novelId && <EntityCardDrawer novelId={novelId} />}
        </div>
      </div>
    </VisualizationLayout>
  )
}
