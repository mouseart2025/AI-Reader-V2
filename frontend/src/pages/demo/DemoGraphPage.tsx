/**
 * DemoGraphPage — interactive force-directed graph using static demo data.
 * Reuses react-force-graph-2d with category filtering and path finding.
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
    // Remove isolated nodes
    const connected = new Set<string>()
    edges.forEach((e) => {
      connected.add(typeof e.source === "string" ? e.source : e.source.id)
      connected.add(typeof e.target === "string" ? e.target : e.target.id)
    })
    const nodes = graphData.nodes.filter((n) => connected.has(n.id))
    return { nodes, links: edges }
  }, [graphData, minChapters, minEdgeWeight, hiddenCategories])

  const nodeColor = useCallback(
    (node: GraphNode) => orgColorMap.get(node.org) || "#6b7280",
    [orgColorMap],
  )

  const nodeVal = useCallback((node: GraphNode) => Math.sqrt(node.chapter_count) * 1.5, [])

  const linkColor = useCallback((edge: GraphEdge) => {
    const cat = edge.category ?? "other"
    const base = CATEGORY_COLORS[cat] ?? "#6b7280"
    return edge.weight <= 1 ? base + "40" : base + "80"
  }, [])

  const linkWidth = useCallback((edge: GraphEdge) => Math.min(edge.weight * 0.5, 4), [])

  const linkLineDash = useCallback(
    (edge: GraphEdge) => (edge.weight <= 1 ? [4, 2] : null),
    [],
  )

  const openCard = useEntityCardStore((s) => s.openCard)

  const handleNodeClick = useCallback((node: GraphNode) => {
    setHoverNode(node.id)
    openCard(node.name, "person")
  }, [openCard])

  const toggleCategory = useCallback((cat: string) => {
    setHiddenCategories((prev) => {
      const next = new Set(prev)
      if (next.has(cat)) next.delete(cat)
      else next.add(cat)
      return next
    })
  }, [])

  return (
    <div className={`flex h-full flex-col ${isEmbed ? "bg-slate-950" : ""}`}>
      {/* Filter bar — hidden in embed mode */}
      {!isEmbed && (
        <div className="flex flex-wrap items-center gap-3 border-b bg-white/80 px-4 py-2">
          <span className="text-xs text-gray-500">
            {filtered.nodes.length} 人物 / {filtered.links.length} 关系
          </span>
          <label className="flex items-center gap-1 text-xs">
            <span className="text-gray-500">出场≥</span>
            <input
              type="range"
              min={1}
              max={Math.min(30, Math.max(...graphData.nodes.map((n) => n.chapter_count), 1))}
              value={minChapters}
              onChange={(e) => setMinChapters(Number(e.target.value))}
              className="w-20"
            />
            <span className="w-6 text-center font-mono">{minChapters}</span>
          </label>
          <label className="flex items-center gap-1 text-xs">
            <span className="text-gray-500">关系≥</span>
            <input
              type="range"
              min={1}
              max={graphData.max_edge_weight ?? 10}
              value={minEdgeWeight}
              onChange={(e) => setMinEdgeWeight(Number(e.target.value))}
              className="w-20"
            />
            <span className="w-6 text-center font-mono">{minEdgeWeight}</span>
          </label>
          <div className="flex gap-1">
            {ALL_CATEGORIES.map((cat) => (
              <button
                key={cat}
                onClick={() => toggleCategory(cat)}
                className="rounded px-2 py-0.5 text-xs font-medium transition"
                style={{
                  backgroundColor: hiddenCategories.has(cat) ? "#f3f4f6" : CATEGORY_COLORS[cat] + "20",
                  color: hiddenCategories.has(cat) ? "#9ca3af" : CATEGORY_COLORS[cat],
                  border: `1px solid ${hiddenCategories.has(cat) ? "#e5e7eb" : CATEGORY_COLORS[cat] + "40"}`,
                }}
              >
                {CATEGORY_LABELS[cat]}
              </button>
            ))}
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
          backgroundColor={isEmbed ? "#020617" : undefined}
          nodeLabel={(n: GraphNode) => `${n.name}${n.aliases?.length ? ` (${n.aliases.join(", ")})` : ""} — 出场 ${n.chapter_count} 回`}
          nodeVal={nodeVal}
          nodeColor={nodeColor}
          linkColor={linkColor}
          linkWidth={linkWidth}
          linkLineDash={linkLineDash}
          linkLabel={(e: GraphEdge) => `${e.relation_type} (${e.weight})`}
          onNodeClick={handleNodeClick}
          onNodeHover={(n: GraphNode | null) => setHoverNode(n?.id ?? null)}
          cooldownTicks={100}
          d3AlphaDecay={0.02}
          d3VelocityDecay={0.3}
          nodeCanvasObject={(node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
            const size = Math.sqrt(node.chapter_count) * 1.5
            const r = Math.max(size, 3)
            const isHover = hoverNode === node.id

            ctx.beginPath()
            ctx.arc(node.x!, node.y!, r, 0, 2 * Math.PI)
            ctx.fillStyle = nodeColor(node)
            ctx.globalAlpha = isHover ? 1 : 0.85
            ctx.fill()
            ctx.globalAlpha = 1

            if (isHover) {
              ctx.strokeStyle = nodeColor(node)
              ctx.lineWidth = 2
              ctx.stroke()
            }

            // Label for visible nodes
            if (globalScale > 0.5 && r > 4) {
              ctx.font = `${Math.max(10 / globalScale, 3)}px sans-serif`
              ctx.textAlign = "center"
              ctx.textBaseline = "top"
              ctx.fillStyle = isEmbed ? "#e2e8f0" : "#374151"
              ctx.fillText(node.name, node.x!, node.y! + r + 2)
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
            <div className="pointer-events-none absolute right-4 top-4 w-64 rounded-lg border bg-white/95 p-3 shadow-lg backdrop-blur">
              <p className="font-semibold">{node.name}</p>
              {node.org && <p className="text-xs text-gray-500">{node.org}</p>}
              <p className="mt-1 text-xs text-gray-500">出场 {node.chapter_count} 回 · {nodeEdges.length} 条关系</p>
              {node.aliases && node.aliases.length > 0 && (
                <p className="mt-1 text-xs text-gray-400">别名：{node.aliases.join("、")}</p>
              )}
            </div>
          )
        })()}
      </div>
    </div>
  )
}
