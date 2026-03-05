/**
 * DemoGraphPage — interactive force-directed graph using static demo data.
 * Features: category filtering, path finding (BFS), hover dim, node search,
 * label collision detection.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d"
import { useDemoData } from "@/app/DemoContext"
import { useEntityCardStore } from "@/stores/entityCardStore"

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
  category?: string
}

const ORG_COLORS = [
  "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#ec4899", "#06b6d4", "#84cc16", "#f97316", "#6366f1",
]

const CATEGORY_COLORS: Record<string, string> = {
  family: "#f59e0b", intimate: "#ec4899", hierarchical: "#8b5cf6",
  social: "#10b981", hostile: "#ef4444", other: "#6b7280",
}

const CATEGORY_LABELS: Record<string, string> = {
  family: "亲属", intimate: "亲密", hierarchical: "主从",
  social: "友好", hostile: "敌对", other: "其他",
}

const ALL_CATEGORIES = ["family", "intimate", "hierarchical", "social", "hostile", "other"]

// ── BFS path finding ─────────────────────────────

function edgeKey(e: GraphEdge): string {
  const src = typeof e.source === "string" ? e.source : e.source.id
  const tgt = typeof e.target === "string" ? e.target : e.target.id
  return `${src}--${tgt}`
}

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

export default function DemoGraphPage() {
  const [searchParams] = useSearchParams()
  const isEmbed = searchParams.get("embed") === "1"
  const { data } = useDemoData()
  const graphData = data.graph as {
    nodes: GraphNode[]
    edges: GraphEdge[]
    category_counts?: Record<string, number>
    suggested_min_edge_weight?: number
    max_edge_weight?: number
  }

  const [minChapters, setMinChapters] = useState(1)
  const [minEdgeWeight, setMinEdgeWeight] = useState(1)
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(new Set())
  const [hoverNode, setHoverNode] = useState<string | null>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })
  const graphRef = useRef<ForceGraphMethods<GraphNode, GraphEdge>>(undefined)
  const containerRef = useRef<HTMLDivElement>(null)

  // Path finding state
  const [pathStart, setPathStart] = useState<string | null>(null)
  const [pathNodes, setPathNodes] = useState<Set<string>>(new Set())
  const [pathEdges, setPathEdges] = useState<Set<string>>(new Set())
  const [pathInfo, setPathInfo] = useState<string[]>([])
  const [showPathPanel, setShowPathPanel] = useState(false)
  const [pathSearchA, setPathSearchA] = useState("")
  const [pathSearchB, setPathSearchB] = useState("")
  const [searchFocused, setSearchFocused] = useState<"a" | "b" | null>(null)

  // Node search state
  const [nodeSearch, setNodeSearch] = useState("")
  const [showSearchPanel, setShowSearchPanel] = useState(false)

  // Label collision detection refs
  const labelRectsRef = useRef<{ x: number; y: number; w: number; h: number }[]>([])
  const lastFrameRef = useRef(0)

  const hasPath = pathNodes.size > 1

  // Set smart defaults based on data size
  useEffect(() => {
    const nodeCount = graphData.nodes.length
    if (nodeCount > 400) setMinChapters(3)
    else if (nodeCount > 200) setMinChapters(2)
    if (graphData.suggested_min_edge_weight) {
      setMinEdgeWeight(Math.max(1, Math.round(graphData.suggested_min_edge_weight)))
    }
  }, [graphData])

  // Resize observer
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) setDimensions({ width, height })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Organization color map
  const orgColorMap = useMemo(() => {
    const map = new Map<string, string>()
    const orgs = [...new Set(graphData.nodes.map((n) => n.org).filter(Boolean))]
    orgs.forEach((org, i) => map.set(org, ORG_COLORS[i % ORG_COLORS.length]))
    return map
  }, [graphData.nodes])

  // Filtered graph data
  const filtered = useMemo(() => {
    const nodeSet = new Set(
      graphData.nodes.filter((n) => n.chapter_count >= minChapters).map((n) => n.id),
    )
    const edges = graphData.edges.filter((e) => {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      if (!nodeSet.has(src) || !nodeSet.has(tgt)) return false
      if (e.weight < minEdgeWeight) return false
      if (e.category && hiddenCategories.has(e.category)) return false
      return true
    })
    const connected = new Set<string>()
    edges.forEach((e) => {
      connected.add(typeof e.source === "string" ? e.source : e.source.id)
      connected.add(typeof e.target === "string" ? e.target : e.target.id)
    })
    const nodes = graphData.nodes.filter((n) => connected.has(n.id))
    return { nodes, links: edges }
  }, [graphData, minChapters, minEdgeWeight, hiddenCategories])

  const filteredNodeIds = useMemo(() => new Set(filtered.nodes.map((n) => n.id)), [filtered.nodes])

  // Connected nodes for hover dim
  const connectedNodes = useMemo(() => {
    if (!hoverNode) return new Set<string>()
    const connected = new Set<string>([hoverNode])
    for (const e of filtered.links) {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      if (src === hoverNode) connected.add(tgt)
      if (tgt === hoverNode) connected.add(src)
    }
    return connected
  }, [hoverNode, filtered.links])

  // Autocomplete suggestions for path search
  const searchSuggestions = useMemo(() => {
    const query = searchFocused === "a" ? pathSearchA : searchFocused === "b" ? pathSearchB : nodeSearch
    if (!query || query.length < 1) return []
    const q = query.toLowerCase()
    return filtered.nodes
      .filter((n) => n.name.toLowerCase().includes(q))
      .sort((a, b) => b.chapter_count - a.chapter_count)
      .slice(0, 8)
  }, [searchFocused, pathSearchA, pathSearchB, nodeSearch, filtered.nodes])

  // Node search suggestions
  const nodeSearchSuggestions = useMemo(() => {
    if (!nodeSearch || nodeSearch.length < 1 || !showSearchPanel) return []
    const q = nodeSearch.toLowerCase()
    return filtered.nodes
      .filter((n) => n.name.toLowerCase().includes(q))
      .sort((a, b) => b.chapter_count - a.chapter_count)
      .slice(0, 8)
  }, [nodeSearch, showSearchPanel, filtered.nodes])

  const getNodeColor = useCallback(
    (node: GraphNode) => {
      if (hasPath) {
        if (pathNodes.has(node.id)) return "#f59e0b"
        return "#4b5563"
      }
      if (pathStart === node.id) return "#f59e0b"
      if (hoverNode && !connectedNodes.has(node.id)) return "#4b5563"
      return orgColorMap.get(node.org) || "#6b7280"
    },
    [orgColorMap, hasPath, pathNodes, pathStart, hoverNode, connectedNodes],
  )

  const nodeVal = useCallback((node: GraphNode) => Math.sqrt(node.chapter_count) * 1.5, [])

  const getLinkColor = useCallback((edge: GraphEdge) => {
    const ek = edgeKey(edge)
    if (hasPath) {
      if (pathEdges.has(ek)) return "#f59e0b"
      return "#1e293b"
    }
    if (hoverNode) {
      const src = typeof edge.source === "string" ? edge.source : edge.source.id
      const tgt = typeof edge.target === "string" ? edge.target : edge.target.id
      if (!connectedNodes.has(src) || !connectedNodes.has(tgt)) return "#1e293b"
    }
    const cat = edge.category ?? "other"
    const base = CATEGORY_COLORS[cat] ?? "#6b7280"
    return edge.weight <= 1 ? base + "40" : base + "80"
  }, [hasPath, pathEdges, hoverNode, connectedNodes])

  const linkWidth = useCallback((edge: GraphEdge) => {
    if (hasPath && pathEdges.has(edgeKey(edge))) return 3
    return Math.min(edge.weight * 0.5, 4)
  }, [hasPath, pathEdges])

  const linkLineDash = useCallback(
    (edge: GraphEdge) => (edge.weight <= 1 && !(hasPath && pathEdges.has(edgeKey(edge))) ? [4, 2] : null),
    [hasPath, pathEdges],
  )

  const openCard = useEntityCardStore((s) => s.openCard)

  // Path finding
  const findPath = useCallback((startId: string, endId: string) => {
    const path = bfsPath(filteredNodeIds, filtered.links, startId, endId)
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
    const nodeMap = new Map(filtered.nodes.map((n) => [n.id, n.name]))
    setPathNodes(pNodes)
    setPathEdges(pEdges)
    setPathInfo(path.map((id) => nodeMap.get(id) ?? id))
  }, [filteredNodeIds, filtered.links, filtered.nodes])

  const clearPath = useCallback(() => {
    setPathStart(null)
    setPathNodes(new Set())
    setPathEdges(new Set())
    setPathInfo([])
    setPathSearchA("")
    setPathSearchB("")
  }, [])

  const handleNodeClick = useCallback((node: GraphNode, event: MouseEvent) => {
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
    openCard(node.name, "person")
  }, [openCard, pathStart, findPath, clearPath])

  const handleSearchPath = useCallback(() => {
    const a = pathSearchA.trim()
    const b = pathSearchB.trim()
    if (!a || !b) return
    const nodeA = filtered.nodes.find((n) => n.name === a)
    const nodeB = filtered.nodes.find((n) => n.name === b)
    if (!nodeA || !nodeB) return
    findPath(nodeA.id, nodeB.id)
  }, [pathSearchA, pathSearchB, filtered.nodes, findPath])

  const handleNodeSearch = useCallback((node: GraphNode) => {
    setNodeSearch("")
    setShowSearchPanel(false)
    // Zoom to node
    graphRef.current?.centerAt(node.x, node.y, 500)
    graphRef.current?.zoom(4, 500)
    setHoverNode(node.id)
    // Clear highlight after 3s
    setTimeout(() => setHoverNode(null), 3000)
  }, [])

  const toggleCategory = useCallback((cat: string) => {
    setHiddenCategories((prev) => {
      const next = new Set(prev)
      if (next.has(cat)) next.delete(cat)
      else next.add(cat)
      return next
    })
  }, [])

  return (
    <div className="flex h-full flex-col bg-slate-950">
      {/* Filter bar — hidden in embed mode */}
      {!isEmbed && (
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/80 px-4 py-2">
          <span className="text-xs text-slate-400">
            {filtered.nodes.length} 人物 / {filtered.links.length} 关系
          </span>
          <label className="flex items-center gap-1 text-xs">
            <span className="text-slate-400">出场≥</span>
            <input
              type="range"
              min={1}
              max={Math.min(30, Math.max(...graphData.nodes.map((n) => n.chapter_count), 1))}
              value={minChapters}
              onChange={(e) => setMinChapters(Number(e.target.value))}
              className="w-20 accent-blue-500"
            />
            <span className="w-6 text-center font-mono text-slate-300">{minChapters}</span>
          </label>
          <label className="flex items-center gap-1 text-xs">
            <span className="text-slate-400">关系≥</span>
            <input
              type="range"
              min={1}
              max={graphData.max_edge_weight ?? 10}
              value={minEdgeWeight}
              onChange={(e) => setMinEdgeWeight(Number(e.target.value))}
              className="w-20 accent-blue-500"
            />
            <span className="w-6 text-center font-mono text-slate-300">{minEdgeWeight}</span>
          </label>
          <div className="flex gap-1">
            {ALL_CATEGORIES.map((cat) => (
              <button
                key={cat}
                onClick={() => toggleCategory(cat)}
                className="rounded px-2 py-0.5 text-xs font-medium transition"
                style={{
                  backgroundColor: hiddenCategories.has(cat) ? "#1e293b" : CATEGORY_COLORS[cat] + "20",
                  color: hiddenCategories.has(cat) ? "#64748b" : CATEGORY_COLORS[cat],
                  border: `1px solid ${hiddenCategories.has(cat) ? "#334155" : CATEGORY_COLORS[cat] + "40"}`,
                }}
              >
                {CATEGORY_LABELS[cat]}
              </button>
            ))}
          </div>

          <div className="flex-1" />

          {/* Node search button */}
          <div className="relative">
            <button
              onClick={() => { setShowSearchPanel((v) => !v); setShowPathPanel(false) }}
              className={`rounded px-2 py-0.5 text-xs transition ${showSearchPanel ? "bg-blue-500/20 text-blue-400" : "text-slate-400 hover:text-white"}`}
            >
              搜索
            </button>
            {showSearchPanel && (
              <div className="absolute right-0 top-8 z-20 w-56 rounded-lg border border-slate-700 bg-slate-900 p-2 shadow-lg">
                <input
                  autoFocus
                  placeholder="输入人物名..."
                  value={nodeSearch}
                  onChange={(e) => setNodeSearch(e.target.value)}
                  className="w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-500 focus:border-blue-500 focus:outline-none"
                />
                {nodeSearchSuggestions.length > 0 && (
                  <div className="mt-1 max-h-48 overflow-y-auto">
                    {nodeSearchSuggestions.map((n) => (
                      <button
                        key={n.id}
                        onClick={() => handleNodeSearch(n)}
                        className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs text-slate-300 hover:bg-slate-800"
                      >
                        <span className="size-2 rounded-full" style={{ backgroundColor: orgColorMap.get(n.org) || "#6b7280" }} />
                        <span>{n.name}</span>
                        <span className="ml-auto text-slate-500">{n.chapter_count}回</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Path finding button */}
          <div className="relative">
            <button
              onClick={() => { setShowPathPanel((v) => !v); setShowSearchPanel(false) }}
              className={`rounded px-2 py-0.5 text-xs transition ${showPathPanel || hasPath ? "bg-amber-500/20 text-amber-400" : "text-slate-400 hover:text-white"}`}
            >
              路径
            </button>
            {showPathPanel && (
              <div className="absolute right-0 top-8 z-20 w-64 rounded-lg border border-slate-700 bg-slate-900 p-3 shadow-lg space-y-2">
                <p className="text-[10px] text-slate-500">
                  Shift+点击两个节点，或输入名字搜索
                </p>
                <div className="relative">
                  <input
                    placeholder="人物 A"
                    value={pathSearchA}
                    onChange={(e) => setPathSearchA(e.target.value)}
                    onFocus={() => setSearchFocused("a")}
                    onBlur={() => setTimeout(() => setSearchFocused(null), 150)}
                    className="w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-500 focus:border-blue-500 focus:outline-none"
                  />
                  {searchFocused === "a" && searchSuggestions.length > 0 && (
                    <div className="absolute left-0 top-7 z-30 max-h-32 w-full overflow-y-auto rounded border border-slate-700 bg-slate-800 shadow-lg">
                      {searchSuggestions.map((n) => (
                        <button key={n.id} className="w-full px-2 py-1 text-left text-xs text-slate-300 hover:bg-slate-700" onMouseDown={() => setPathSearchA(n.name)}>
                          {n.name}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="relative">
                  <input
                    placeholder="人物 B"
                    value={pathSearchB}
                    onChange={(e) => setPathSearchB(e.target.value)}
                    onFocus={() => setSearchFocused("b")}
                    onBlur={() => setTimeout(() => setSearchFocused(null), 150)}
                    className="w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-500 focus:border-blue-500 focus:outline-none"
                  />
                  {searchFocused === "b" && searchSuggestions.length > 0 && (
                    <div className="absolute left-0 top-7 z-30 max-h-32 w-full overflow-y-auto rounded border border-slate-700 bg-slate-800 shadow-lg">
                      {searchSuggestions.map((n) => (
                        <button key={n.id} className="w-full px-2 py-1 text-left text-xs text-slate-300 hover:bg-slate-700" onMouseDown={() => setPathSearchB(n.name)}>
                          {n.name}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex gap-1">
                  <button onClick={handleSearchPath} className="rounded bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-500 transition">
                    查找
                  </button>
                  <button onClick={clearPath} className="rounded border border-slate-600 px-3 py-1 text-xs text-slate-400 hover:text-white transition">
                    清除
                  </button>
                </div>
                {pathStart && !hasPath && (
                  <p className="text-[10px] text-amber-400">已选起点，请 Shift+点击终点</p>
                )}
                {pathInfo.length > 1 && (
                  <div className="border-t border-slate-700 pt-2">
                    <p className="text-[10px] text-slate-400 mb-1">最短路径 ({pathInfo.length - 1} 步)</p>
                    <p className="text-xs text-amber-300">{pathInfo.join(" → ")}</p>
                  </div>
                )}
                {pathInfo.length === 0 && pathSearchA && pathSearchB && (
                  <p className="text-[10px] text-red-400">未找到路径</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Graph canvas */}
      <div ref={containerRef} className="relative flex-1">
        <ForceGraph2D
          ref={graphRef}
          width={dimensions.width}
          height={dimensions.height}
          graphData={filtered}
          backgroundColor="#020617"
          nodeLabel=""
          nodeVal={nodeVal}
          nodeColor={getNodeColor}
          linkColor={getLinkColor}
          linkWidth={linkWidth}
          linkLineDash={linkLineDash}
          linkLabel={(e: GraphEdge) => `${e.relation_type} (${e.weight})`}
          onNodeClick={handleNodeClick}
          onNodeHover={(n: GraphNode | null) => setHoverNode(n?.id ?? null)}
          cooldownTicks={100}
          d3AlphaDecay={0.02}
          d3VelocityDecay={0.3}
          nodeCanvasObject={(node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
            // Reset collision rects at start of each frame (8ms throttle)
            const now = performance.now()
            if (now - lastFrameRef.current > 8) {
              labelRectsRef.current = []
              lastFrameRef.current = now
            }

            const isOnPath = hasPath && pathNodes.has(node.id)
            const isPathStartNode = pathStart === node.id
            const isHovered = hoverNode === node.id
            const isConnected = hoverNode != null && connectedNodes.has(node.id)

            const size = isOnPath
              ? Math.max(5, Math.sqrt(node.chapter_count) * 2)
              : Math.max(3, Math.sqrt(node.chapter_count) * 1.5)

            const color = getNodeColor(node)

            // Draw circle
            ctx.beginPath()
            ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI)
            ctx.fillStyle = color
            ctx.globalAlpha = isHovered || isOnPath || isPathStartNode ? 1 : 0.85
            ctx.fill()
            ctx.globalAlpha = 1

            // Path/hover ring
            if (isOnPath || isPathStartNode) {
              ctx.beginPath()
              ctx.arc(node.x!, node.y!, size + 2, 0, 2 * Math.PI)
              ctx.strokeStyle = "#d97706"
              ctx.lineWidth = 1.5
              ctx.stroke()
            } else if (isHovered) {
              ctx.strokeStyle = color
              ctx.lineWidth = 2
              ctx.beginPath()
              ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI)
              ctx.stroke()
            }

            // Label rendering with collision detection
            const alwaysShow = isOnPath || isPathStartNode || isHovered || isConnected
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

              const isDimmed = (hasPath && !isOnPath && !isPathStartNode) ||
                               (hoverNode !== null && !connectedNodes.has(node.id))
              const fitsInside = !isDimmed && (labelW + labelPad) < size * 2 && labelH < size * 1.6

              if (fitsInside) {
                // Label inside circle
                labelRectsRef.current.push({
                  x: node.x! - size, y: node.y! - size,
                  w: size * 2, h: size * 2,
                })
                ctx.font = `600 ${fontSize}px system-ui, sans-serif`
                ctx.textAlign = "center"
                ctx.textBaseline = "middle"
                ctx.strokeStyle = "rgba(0,0,0,0.35)"
                ctx.lineWidth = 2.5 / globalScale
                ctx.lineJoin = "round"
                ctx.strokeText(node.name, node.x!, node.y!)
                ctx.fillStyle = "#ffffff"
                ctx.fillText(node.name, node.x!, node.y!)
              } else {
                // Below-node label with pill background
                const labelX = node.x! - labelW / 2
                const labelY = node.y! + size + 2 / globalScale

                const rect = { x: labelX, y: labelY, w: labelW, h: labelH }
                let overlaps = false
                if (!alwaysShow) {
                  for (const p of labelRectsRef.current) {
                    if (rect.x < p.x + p.w && rect.x + rect.w > p.x &&
                        rect.y < p.y + p.h && rect.y + rect.h > p.y) {
                      overlaps = true
                      break
                    }
                  }
                }

                if (!overlaps) {
                  labelRectsRef.current.push(rect)

                  // Background pill
                  const padX = 2 / globalScale
                  const padY = 1 / globalScale
                  ctx.fillStyle = "rgba(15,23,42,0.9)"
                  const rx = labelX - padX
                  const ry = labelY - padY
                  const rw = labelW + padX * 2
                  const rh = labelH + padY * 2
                  const cr = 2 / globalScale
                  ctx.beginPath()
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

                  // Text
                  ctx.textAlign = "center"
                  ctx.textBaseline = "top"
                  ctx.fillStyle = isOnPath || isPathStartNode
                    ? "#fbbf24"
                    : hasPath || (hoverNode && !connectedNodes.has(node.id))
                      ? "#6b7280"
                      : "#e5e7eb"
                  ctx.fillText(node.name, node.x!, labelY)
                }
              }
            }
          }}
        />

        {/* Hover info — hidden in embed mode */}
        {!isEmbed && hoverNode && (() => {
          const node = graphData.nodes.find((n) => n.id === hoverNode)
          if (!node) return null
          const nodeEdges = graphData.edges.filter((e) => {
            const src = typeof e.source === "string" ? e.source : e.source.id
            const tgt = typeof e.target === "string" ? e.target : e.target.id
            return src === hoverNode || tgt === hoverNode
          })
          return (
            <div className="pointer-events-none absolute right-4 top-4 w-64 rounded-lg border border-slate-700/50 bg-slate-900/95 p-3 shadow-lg backdrop-blur">
              <p className="font-semibold text-white">{node.name}</p>
              {node.org && <p className="text-xs text-slate-400">{node.org}</p>}
              <p className="mt-1 text-xs text-slate-400">出场 {node.chapter_count} 回 · {nodeEdges.length} 条关系</p>
              {node.aliases && node.aliases.length > 0 && (
                <p className="mt-1 text-xs text-slate-500">别名：{node.aliases.join("、")}</p>
              )}
            </div>
          )
        })()}

        {/* Shift-click hint */}
        {!isEmbed && !hasPath && !pathStart && (
          <div className="pointer-events-none absolute bottom-4 left-4 text-[10px] text-slate-600">
            Shift+点击两个节点查找最短路径
          </div>
        )}
      </div>
    </div>
  )
}
