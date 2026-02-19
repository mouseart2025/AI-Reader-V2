import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d"
import { fetchGraphData } from "@/api/client"
import { trackEvent } from "@/lib/tracker"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { VisualizationLayout } from "@/components/visualization/VisualizationLayout"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface GraphNode {
  id: string
  name: string
  type: string
  chapter_count: number
  org: string
  aliases?: string[]
  x?: number
  y?: number
}

interface GraphEdge {
  source: string | GraphNode
  target: string | GraphNode
  relation_type: string
  all_types?: string[]
  weight: number
  chapters: number[]
}

// Color palette for organizations
const ORG_COLORS = [
  "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#ec4899", "#06b6d4", "#84cc16", "#f97316", "#6366f1",
]

// Exact-match sets for normalized relation types
const _INTIMATE_TYPES = new Set(["夫妻", "恋人"])
const _FAMILY_TYPES = new Set([
  "父子", "父女", "母子", "母女", "兄弟", "兄妹", "姐弟", "姐妹",
  "叔侄", "祖孙", "婆媳", "表亲", "堂亲",
])
const _SOCIAL_TYPES = new Set([
  "朋友", "同门", "同学", "同事", "搭档", "盟友", "邻居", "同僚",
])
const _HIERARCHICAL_TYPES = new Set(["师徒", "主仆", "君臣", "上下级"])

function edgeColor(type: string): string {
  if (_INTIMATE_TYPES.has(type)) return "#ec4899"
  if (_FAMILY_TYPES.has(type)) return "#f59e0b"
  if (_SOCIAL_TYPES.has(type)) return "#10b981"
  if (type === "敌对") return "#ef4444"
  if (_HIERARCHICAL_TYPES.has(type)) return "#8b5cf6"
  const t = type
  if (t.includes("夫") || t.includes("妻") || t.includes("恋") || t.includes("情")) return "#ec4899"
  if (t.includes("父") || t.includes("母") || t.includes("兄") || t.includes("姐") || t.includes("弟") || t.includes("妹")) return "#f59e0b"
  if (t.includes("友") || t.includes("盟") || t.includes("同门")) return "#10b981"
  if (t.includes("敌") || t.includes("仇")) return "#ef4444"
  if (t.includes("师") || t.includes("主") || t.includes("君") || t.includes("臣")) return "#8b5cf6"
  return "#6b7280"
}

/**
 * BFS to find shortest path between two nodes in the graph.
 */
function bfsPath(nodeIds: Set<string>, edges: GraphEdge[], startId: string, endId: string): string[] {
  if (startId === endId) return [startId]
  if (!nodeIds.has(startId) || !nodeIds.has(endId)) return []

  const adj = new Map<string, string[]>()
  for (const id of nodeIds) adj.set(id, [])
  for (const e of edges) {
    const src = typeof e.source === "string" ? e.source : e.source.id
    const tgt = typeof e.target === "string" ? e.target : e.target.id
    adj.get(src)?.push(tgt)
    adj.get(tgt)?.push(src)
  }

  const visited = new Set<string>([startId])
  const parent = new Map<string, string>()
  const queue = [startId]

  while (queue.length > 0) {
    const current = queue.shift()!
    for (const neighbor of adj.get(current) ?? []) {
      if (visited.has(neighbor)) continue
      visited.add(neighbor)
      parent.set(neighbor, current)
      if (neighbor === endId) {
        const path: string[] = []
        let node: string | undefined = endId
        while (node !== undefined) {
          path.unshift(node)
          node = parent.get(node)
        }
        return path
      }
      queue.push(neighbor)
    }
  }
  return []
}

export default function GraphPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const { chapterStart, chapterEnd, setAnalyzedRange } = useChapterRangeStore()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [loading, setLoading] = useState(true)
  const [hoverNode, setHoverNode] = useState<string | null>(null)

  // Filters
  const [minChapters, setMinChapters] = useState(1)
  const [minEdgeWeight, setMinEdgeWeight] = useState(1)
  const [maxEdgeWeight, setMaxEdgeWeight] = useState(1)
  const [showFilters, setShowFilters] = useState(false)

  // Path finding state
  const [pathStart, setPathStart] = useState<string | null>(null)
  const [pathNodes, setPathNodes] = useState<Set<string>>(new Set())
  const [pathEdges, setPathEdges] = useState<Set<string>>(new Set())
  const [pathSearchA, setPathSearchA] = useState("")
  const [pathSearchB, setPathSearchB] = useState("")
  const [showPathPanel, setShowPathPanel] = useState(false)
  const [pathInfo, setPathInfo] = useState<string[]>([])

  const graphRef = useRef<ForceGraphMethods<GraphNode, GraphEdge>>(undefined)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })

  // Label collision detection state
  const labelRectsRef = useRef<{ x: number; y: number; w: number; h: number }[]>([])
  const lastFrameRef = useRef(0)

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
    trackEvent("view_graph")

    fetchGraphData(novelId, chapterStart, chapterEnd)
      .then((data) => {
        if (cancelled) return
        const range = data.analyzed_range as number[]
        if (range && range[0] > 0) {
          setAnalyzedRange(range[0], range[1])
        }
        setNodes((data.nodes as GraphNode[]) ?? [])
        setEdges((data.edges as GraphEdge[]) ?? [])
        // Apply smart defaults from backend
        const suggested = (data.suggested_min_edge_weight as number) ?? 1
        const maxW = (data.max_edge_weight as number) ?? 1
        setMinEdgeWeight(suggested)
        setMaxEdgeWeight(maxW)
        // Auto-set minChapters for very large graphs
        const nodeCount = (data.nodes as GraphNode[])?.length ?? 0
        if (nodeCount > 200) setMinChapters(3)
        else if (nodeCount > 100) setMinChapters(2)
        else setMinChapters(1)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [novelId, chapterStart, chapterEnd, setAnalyzedRange])

  // Org -> color mapping
  const orgColorMap = useMemo(() => {
    const orgs = [...new Set(nodes.map((n) => n.org).filter(Boolean))]
    const map = new Map<string, string>()
    orgs.forEach((org, i) => map.set(org, ORG_COLORS[i % ORG_COLORS.length]))
    return map
  }, [nodes])

  // Filter nodes AND edges
  const { filteredNodes, filteredEdges, filteredNodeIds, degreeMap } = useMemo(() => {
    const chapterFiltered = nodes.filter((n) => n.chapter_count >= minChapters)
    const chapterIds = new Set(chapterFiltered.map((n) => n.id))

    // Filter edges by BOTH node presence and edge weight
    const validEdges = edges.filter((e) => {
      if (e.weight < minEdgeWeight) return false
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      return chapterIds.has(src) && chapterIds.has(tgt)
    })

    // Compute degree
    const deg = new Map<string, number>()
    for (const e of validEdges) {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      deg.set(src, (deg.get(src) || 0) + 1)
      deg.set(tgt, (deg.get(tgt) || 0) + 1)
    }

    // Remove degree-0 nodes
    const connected = chapterFiltered.filter((n) => (deg.get(n.id) || 0) > 0)
    const connectedIds = new Set(connected.map((n) => n.id))

    return {
      filteredNodes: connected,
      filteredEdges: validEdges.filter((e) => {
        const src = typeof e.source === "string" ? e.source : e.source.id
        const tgt = typeof e.target === "string" ? e.target : e.target.id
        return connectedIds.has(src) && connectedIds.has(tgt)
      }),
      filteredNodeIds: connectedIds,
      degreeMap: deg,
    }
  }, [nodes, edges, minChapters, minEdgeWeight])

  // Highlight connected nodes on hover
  const connectedNodes = useMemo(() => {
    if (!hoverNode) return new Set<string>()
    const connected = new Set<string>([hoverNode])
    for (const e of filteredEdges) {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      if (src === hoverNode) connected.add(tgt)
      if (tgt === hoverNode) connected.add(src)
    }
    return connected
  }, [hoverNode, filteredEdges])

  // Build edge key helper
  const edgeKey = useCallback((e: GraphEdge) => {
    const src = typeof e.source === "string" ? e.source : e.source.id
    const tgt = typeof e.target === "string" ? e.target : e.target.id
    return `${src}--${tgt}`
  }, [])

  // Run path finding
  const findPath = useCallback((startId: string, endId: string) => {
    const path = bfsPath(filteredNodeIds, filteredEdges, startId, endId)
    if (path.length === 0) {
      setPathNodes(new Set())
      setPathEdges(new Set())
      setPathInfo([])
      return
    }

    const pNodes = new Set(path)
    const pEdges = new Set<string>()
    for (let i = 0; i < path.length - 1; i++) {
      pEdges.add(`${path[i]}--${path[i + 1]}`)
      pEdges.add(`${path[i + 1]}--${path[i]}`)
    }

    const nodeMap = new Map(filteredNodes.map((n) => [n.id, n.name]))
    const names = path.map((id) => nodeMap.get(id) ?? id)

    setPathNodes(pNodes)
    setPathEdges(pEdges)
    setPathInfo(names)
  }, [filteredNodeIds, filteredEdges, filteredNodes])

  const handleSearchPath = useCallback(() => {
    const a = pathSearchA.trim()
    const b = pathSearchB.trim()
    if (!a || !b) return
    const nodeA = filteredNodes.find((n) => n.name === a)
    const nodeB = filteredNodes.find((n) => n.name === b)
    if (!nodeA || !nodeB) return
    findPath(nodeA.id, nodeB.id)
  }, [pathSearchA, pathSearchB, filteredNodes, findPath])

  const clearPath = useCallback(() => {
    setPathStart(null)
    setPathNodes(new Set())
    setPathEdges(new Set())
    setPathInfo([])
  }, [])

  // Sort nodes by chapter_count desc so high-importance nodes claim label space first
  const graphData = useMemo(
    () => ({
      nodes: [...filteredNodes].sort((a, b) => b.chapter_count - a.chapter_count),
      links: filteredEdges,
    }),
    [filteredNodes, filteredEdges],
  )

  // Customize D3 forces — scale spacing with graph density
  useEffect(() => {
    const fg = graphRef.current
    if (!fg || filteredNodes.length === 0) return

    const nodeCount = filteredNodes.length

    // Charge: stronger repulsion for dense graphs
    const charge = fg.d3Force("charge")
    if (charge && typeof charge.strength === "function") {
      const baseCharge = nodeCount > 150 ? -60 : nodeCount > 80 ? -35 : -15
      charge.strength((node: GraphNode) => {
        const deg = degreeMap.get(node.id) || 0
        return deg <= 1 ? baseCharge * 0.6 : baseCharge - deg * 3
      })
    }

    // Link distance: spread connected nodes further in dense graphs
    const linkForce = fg.d3Force("link")
    if (linkForce && typeof linkForce.distance === "function") {
      const baseDist = nodeCount > 150 ? 70 : nodeCount > 80 ? 50 : 35
      linkForce.distance(baseDist)
    }

    const containRadius = Math.max(200, Math.sqrt(nodeCount) * 45)
    const containStrength = 0.06
    interface SimNode { x?: number; y?: number; vx?: number; vy?: number }
    let simNodes: SimNode[] = []
    const containment = Object.assign(
      (alpha: number) => {
        for (const node of simNodes) {
          const x = node.x || 0
          const y = node.y || 0
          const dist = Math.sqrt(x * x + y * y)
          if (dist > containRadius) {
            const k = ((dist - containRadius) / dist) * containStrength * alpha
            node.vx = (node.vx || 0) - x * k
            node.vy = (node.vy || 0) - y * k
          }
        }
      },
      { initialize: (nodes: SimNode[]) => { simNodes = nodes } },
    )
    fg.d3Force("containment", containment as never)

    fg.d3ReheatSimulation()
  }, [graphData, degreeMap, filteredNodes.length])

  const handleEngineStop = useCallback(() => {
    graphRef.current?.zoomToFit(400, 60)
  }, [])

  const handleNodeClick = useCallback(
    (node: GraphNode, event: MouseEvent) => {
      if (event.shiftKey) {
        if (!pathStart) {
          setPathStart(node.id)
          setPathNodes(new Set([node.id]))
          setPathEdges(new Set())
          setPathInfo([node.name])
        } else {
          findPath(pathStart, node.id)
          setPathStart(null)
        }
        return
      }
      clearPath()
      openEntityCard(node.name, "person")
    },
    [openEntityCard, pathStart, findPath, clearPath],
  )

  const hasPath = pathNodes.size > 1

  return (
    <VisualizationLayout>
      <div className="relative h-full" ref={containerRef}>
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
            <p className="text-muted-foreground">Loading graph...</p>
          </div>
        )}

        {/* Filter panel toggle */}
        <div className="absolute top-3 left-3 z-10 flex gap-1">
          <Button
            variant="outline"
            size="xs"
            onClick={() => { setShowFilters(!showFilters); setShowPathPanel(false) }}
          >
            筛选
          </Button>
          <Button
            variant={showPathPanel ? "default" : "outline"}
            size="xs"
            onClick={() => { setShowPathPanel(!showPathPanel); setShowFilters(false) }}
          >
            路径查找
          </Button>

          {showFilters && (
            <div className="absolute top-8 left-0 w-56 rounded-lg border bg-background p-3 shadow-lg space-y-3">
              <div>
                <label className="text-muted-foreground text-xs">
                  最少出场章节: {minChapters}
                </label>
                <input
                  type="range"
                  min={1}
                  max={Math.max(10, Math.round(nodes.length > 0 ? nodes[0].chapter_count / 2 : 10))}
                  value={minChapters}
                  onChange={(e) => setMinChapters(Number(e.target.value))}
                  className="w-full h-1.5 mt-1 accent-primary"
                />
              </div>
              <div>
                <label className="text-muted-foreground text-xs">
                  最少关系强度: {minEdgeWeight}
                </label>
                <input
                  type="range"
                  min={1}
                  max={Math.max(5, maxEdgeWeight)}
                  value={minEdgeWeight}
                  onChange={(e) => setMinEdgeWeight(Number(e.target.value))}
                  className="w-full h-1.5 mt-1 accent-primary"
                />
              </div>
              <p className="text-muted-foreground text-[10px] border-t pt-2">
                {filteredNodes.length}/{nodes.length} 人物，{filteredEdges.length}/{edges.length} 关系
              </p>
            </div>
          )}

          {showPathPanel && (
            <div className="absolute top-8 left-0 w-64 rounded-lg border bg-background p-3 shadow-lg space-y-2">
              <p className="text-xs text-muted-foreground">
                Shift+点击两个人物节点，或输入名字搜索
              </p>
              <Input
                placeholder="人物 A"
                value={pathSearchA}
                onChange={(e) => setPathSearchA(e.target.value)}
                className="h-7 text-xs"
              />
              <Input
                placeholder="人物 B"
                value={pathSearchB}
                onChange={(e) => setPathSearchB(e.target.value)}
                className="h-7 text-xs"
              />
              <div className="flex gap-1">
                <Button size="xs" onClick={handleSearchPath}>
                  查找
                </Button>
                <Button variant="ghost" size="xs" onClick={clearPath}>
                  清除
                </Button>
              </div>
              {pathInfo.length > 1 && (
                <div className="border-t pt-2">
                  <p className="text-[10px] text-muted-foreground mb-1">
                    最短路径 ({pathInfo.length - 1} 步)
                  </p>
                  <p className="text-xs">{pathInfo.join(" → ")}</p>
                </div>
              )}
              {pathInfo.length === 0 && pathStart && (
                <p className="text-[10px] text-amber-600">
                  已选择起点，请 Shift+点击终点
                </p>
              )}
            </div>
          )}
        </div>

        {/* Path indicator bar */}
        {(hasPath || pathStart) && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 rounded-full border bg-background/95 px-4 py-1.5 shadow-lg flex items-center gap-2">
            {hasPath ? (
              <>
                <span className="text-xs">{pathInfo.join(" → ")}</span>
                <Button variant="ghost" size="xs" onClick={clearPath}>
                  清除
                </Button>
              </>
            ) : (
              <span className="text-xs text-amber-600">
                Shift+点击第二个人物查找路径
              </span>
            )}
          </div>
        )}

        {/* Legend */}
        <div className="absolute top-3 right-3 z-10 flex flex-col gap-2">
          <div className="rounded-lg border bg-background/90 p-2">
            <p className="text-muted-foreground mb-1 text-[10px]">关系线</p>
            {[
              { label: "亲密", color: "#ec4899", desc: "夫妻/恋人" },
              { label: "亲属", color: "#f59e0b", desc: "父母/兄弟/叔侄" },
              { label: "师承", color: "#8b5cf6", desc: "师徒/主仆/君臣" },
              { label: "友好", color: "#10b981", desc: "朋友/同门/盟友" },
              { label: "敌对", color: "#ef4444", desc: "仇人/对手" },
              { label: "其他", color: "#6b7280", desc: "一般关系" },
            ].map((item) => (
              <div key={item.label} className="flex items-center gap-1.5 text-xs">
                <span
                  className="inline-block h-0.5 w-4 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                <span>{item.label}</span>
                <span className="text-muted-foreground text-[10px]">{item.desc}</span>
              </div>
            ))}
          </div>

          {orgColorMap.size > 0 && (
            <div className="rounded-lg border bg-background/90 p-2">
              <p className="text-muted-foreground mb-1 text-[10px]">组织</p>
              {Array.from(orgColorMap.entries()).map(([org, color]) => (
                <div key={org} className="flex items-center gap-1.5 text-xs">
                  <span
                    className="inline-block size-2.5 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  {org}
                </div>
              ))}
            </div>
          )}
        </div>

        <ForceGraph2D
          ref={graphRef}
          graphData={graphData}
          width={dimensions.width}
          height={dimensions.height}
          nodeLabel={(node: GraphNode) => {
            const aliasStr = node.aliases?.length ? ` (${node.aliases.join("/")})` : ""
            return `${node.name}${aliasStr} — ${node.chapter_count}章${node.org ? ` · ${node.org}` : ""}`
          }}
          nodeVal={(node: GraphNode) => Math.max(2, Math.sqrt(node.chapter_count) * 2)}
          nodeColor={(node: GraphNode) => {
            if (hasPath) {
              if (pathNodes.has(node.id)) return "#f59e0b"
              return "#e5e7eb"
            }
            if (pathStart === node.id) return "#f59e0b"
            if (hoverNode && !connectedNodes.has(node.id)) return "#d1d5db"
            return node.org ? (orgColorMap.get(node.org) ?? "#6b7280") : "#6b7280"
          }}
          nodeCanvasObject={(node: GraphNode, ctx, globalScale) => {
            // Reset collision rects at start of each frame
            const now = performance.now()
            if (now - lastFrameRef.current > 8) {
              labelRectsRef.current = []
              lastFrameRef.current = now
            }

            const isOnPath = hasPath && pathNodes.has(node.id)
            const isPathStart = pathStart === node.id
            const isHovered = hoverNode === node.id
            const isConnected = hoverNode != null && connectedNodes.has(node.id)
            const size = isOnPath
              ? Math.max(5, Math.sqrt(node.chapter_count) * 2)
              : Math.max(3, Math.sqrt(node.chapter_count) * 1.5)

            const color = isOnPath || isPathStart
              ? "#f59e0b"
              : hasPath
                ? "#e5e7eb"
                : hoverNode && !connectedNodes.has(node.id)
                  ? "#d1d5db"
                  : node.org
                    ? (orgColorMap.get(node.org) ?? "#6b7280")
                    : "#6b7280"

            // Draw circle
            ctx.beginPath()
            ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI)
            ctx.fillStyle = color
            ctx.fill()

            // Path node ring
            if (isOnPath || isPathStart) {
              ctx.beginPath()
              ctx.arc(node.x!, node.y!, size + 2, 0, 2 * Math.PI)
              ctx.strokeStyle = "#d97706"
              ctx.lineWidth = 1.5
              ctx.stroke()
            }

            // ── Label rendering (improved readability) ──
            const alwaysShow = isOnPath || isPathStart || isHovered || isConnected
            const chapterThreshold = Math.max(2, Math.round(20 / globalScale))
            const showLabel = alwaysShow || node.chapter_count >= chapterThreshold

            if (showLabel) {
              const fontSize = 13 / globalScale
              const isBold = isOnPath || isHovered
              ctx.font = isBold
                ? `bold ${fontSize}px system-ui, sans-serif`
                : `${fontSize}px system-ui, sans-serif`
              const labelW = ctx.measureText(node.name).width
              const labelH = fontSize * 1.3
              const labelPad = 4 / globalScale

              // Check if label fits inside the circle (large nodes at current zoom)
              const isDimmed = (hasPath && !isOnPath && !isPathStart) ||
                               (hoverNode !== null && !connectedNodes.has(node.id))
              const fitsInside = !isDimmed && (labelW + labelPad) < size * 2 && labelH < size * 1.6

              if (fitsInside) {
                // Register circle footprint for collision protection
                labelRectsRef.current.push({
                  x: node.x! - size, y: node.y! - size,
                  w: size * 2, h: size * 2,
                })
                // Render label centered inside the circle
                ctx.font = `600 ${fontSize}px system-ui, sans-serif`
                ctx.textAlign = "center"
                ctx.textBaseline = "middle"
                // Dark outline for legibility on any color background
                ctx.strokeStyle = "rgba(0,0,0,0.35)"
                ctx.lineWidth = 2.5 / globalScale
                ctx.lineJoin = "round"
                ctx.strokeText(node.name, node.x!, node.y!)
                ctx.fillStyle = "#ffffff"
                ctx.fillText(node.name, node.x!, node.y!)
              } else {
                // Below-node label with background pill
                const labelX = node.x! - labelW / 2
                const labelY = node.y! + size + 2 / globalScale

                // Collision detection
                const rect = { x: labelX, y: labelY, w: labelW, h: labelH }
                let overlaps = false
                if (!alwaysShow) {
                  for (const p of labelRectsRef.current) {
                    if (
                      rect.x < p.x + p.w && rect.x + rect.w > p.x &&
                      rect.y < p.y + p.h && rect.y + rect.h > p.y
                    ) {
                      overlaps = true
                      break
                    }
                  }
                }

                if (!overlaps) {
                  labelRectsRef.current.push(rect)

                  // Background pill for readability
                  const padX = 2 / globalScale
                  const padY = 1 / globalScale
                  ctx.fillStyle = "rgba(255,255,255,0.85)"
                  ctx.beginPath()
                  const rx = labelX - padX
                  const ry = labelY - padY
                  const rw = labelW + padX * 2
                  const rh = labelH + padY * 2
                  const cr = 2 / globalScale
                  ctx.moveTo(rx + cr, ry)
                  ctx.lineTo(rx + rw - cr, ry)
                  ctx.quadraticCurveTo(rx + rw, ry, rx + rw, ry + cr)
                  ctx.lineTo(rx + rw, ry + rh - cr)
                  ctx.quadraticCurveTo(rx + rw, ry + rh, rx + rw - cr, ry + rh)
                  ctx.lineTo(rx + cr, ry + rh)
                  ctx.quadraticCurveTo(rx, ry + rh, rx, ry + rh - cr)
                  ctx.lineTo(rx, ry + cr)
                  ctx.quadraticCurveTo(rx, ry, rx + cr, ry)
                  ctx.fill()

                  // Text color: high contrast
                  ctx.textAlign = "center"
                  ctx.textBaseline = "top"
                  ctx.fillStyle = isOnPath || isPathStart
                    ? "#92400e"
                    : hasPath
                      ? "#9ca3af"
                      : hoverNode && !connectedNodes.has(node.id)
                        ? "#9ca3af"
                        : "#111827"
                  ctx.fillText(node.name, node.x!, labelY)
                }
              }
            }
          }}
          linkColor={(edge: GraphEdge) => {
            const ek = edgeKey(edge)
            if (hasPath) {
              if (pathEdges.has(ek)) return "#f59e0b"
              return "#f3f4f6"
            }
            if (hoverNode) {
              const src = typeof edge.source === "string" ? edge.source : edge.source.id
              const tgt = typeof edge.target === "string" ? edge.target : edge.target.id
              if (!connectedNodes.has(src) || !connectedNodes.has(tgt)) return "#e5e7eb"
            }
            return edgeColor(edge.relation_type)
          }}
          linkWidth={(edge: GraphEdge) => {
            if (hasPath && pathEdges.has(edgeKey(edge))) return 3
            return Math.max(0.5, Math.min(edge.weight * 0.8, 5))
          }}
          linkLineDash={(edge: GraphEdge) => {
            // Dashed lines for weak edges
            if (edge.weight <= 1) return [2, 2]
            return []
          }}
          linkLabel={(edge: GraphEdge) => {
            if (edge.all_types && edge.all_types.length > 1) {
              return `${edge.relation_type} (${edge.all_types.join("/")})`
            }
            return edge.relation_type
          }}
          linkDirectionalParticles={0}
          onNodeClick={handleNodeClick}
          onNodeHover={(node: GraphNode | null) => setHoverNode(node?.id ?? null)}
          cooldownTicks={100}
          d3AlphaDecay={0.02}
          d3VelocityDecay={0.3}
          onEngineStop={handleEngineStop}
        />

        {novelId && <EntityCardDrawer novelId={novelId} />}
      </div>
    </VisualizationLayout>
  )
}
