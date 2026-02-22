import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react"
import * as d3Selection from "d3-selection"
import * as d3Zoom from "d3-zoom"
import * as d3Drag from "d3-drag"
import * as d3Shape from "d3-shape"
import "d3-transition"
import type {
  LayerType,
  LocationConflict,
  MapLayoutItem,
  MapLocation,
  PortalInfo,
  RegionBoundary,
  TrajectoryPoint,
} from "@/api/types"
import { generateHullTerritories } from "@/lib/hullTerritoryGenerator"
import { distortPolygonEdges, type Point } from "@/lib/edgeDistortion"

// ── Canvas defaults ────────────────────────────────
const DEFAULT_CANVAS = { width: 1600, height: 900 }

// ── Tier zoom mapping (D3 scale thresholds) ────────
const TIER_MIN_SCALE: Record<string, number> = {
  continent: 0.3,
  kingdom: 0.5,
  region: 0.8,
  city: 1.2,
  site: 2.0,
  building: 3.0,
}

// ── Tier priority weights (higher = more important) ──
const TIER_WEIGHT: Record<string, number> = {
  continent: 6,
  kingdom: 5,
  region: 4,
  city: 3,
  site: 2,
  building: 1,
}

// ── Label collision detection (AABB) ─────────────────
interface LabelRect {
  x: number; y: number; w: number; h: number
  name: string; priority: number
}

function computeLabelCollisions(rects: LabelRect[]): Set<string> {
  // Sort by priority descending — higher priority labels claim space first
  const sorted = [...rects].sort((a, b) => b.priority - a.priority)
  const visible = new Set<string>()

  // Grid-based spatial index for O(n) average collision detection (vs O(n²) brute force)
  const cellSize = 60
  const grid = new Map<number, LabelRect[]>()

  const cellKeyAt = (cx: number, cy: number) => cx * 100003 + cy

  const getCellRange = (r: LabelRect) => ({
    x0: Math.floor(r.x / cellSize),
    x1: Math.floor((r.x + r.w) / cellSize),
    y0: Math.floor(r.y / cellSize),
    y1: Math.floor((r.y + r.h) / cellSize),
  })

  for (const r of sorted) {
    const { x0, x1, y0, y1 } = getCellRange(r)
    let overlaps = false

    outer:
    for (let cx = x0; cx <= x1; cx++) {
      for (let cy = y0; cy <= y1; cy++) {
        const cell = grid.get(cellKeyAt(cx, cy))
        if (!cell) continue
        for (const p of cell) {
          if (
            r.x < p.x + p.w && r.x + r.w > p.x &&
            r.y < p.y + p.h && r.y + r.h > p.y
          ) {
            overlaps = true
            break outer
          }
        }
      }
    }

    if (!overlaps) {
      visible.add(r.name)
      for (let cx = x0; cx <= x1; cx++) {
        for (let cy = y0; cy <= y1; cy++) {
          const key = cellKeyAt(cx, cy)
          let cell = grid.get(key)
          if (!cell) { cell = []; grid.set(key, cell) }
          cell.push(r)
        }
      }
    }
  }
  return visible
}

const TIER_TEXT_SIZE: Record<string, number> = {
  continent: 26,
  kingdom: 20,
  region: 14,
  city: 11,
  site: 9,
  building: 8,
}

const TIER_ICON_SIZE: Record<string, number> = {
  continent: 40,
  kingdom: 30,
  region: 24,
  city: 18,
  site: 14,
  building: 10,
}

const TIER_DOT_RADIUS: Record<string, number> = {
  continent: 5,
  kingdom: 4.5,
  region: 4,
  city: 3,
  site: 2.5,
  building: 2,
}

const TIER_FONT_WEIGHT: Record<string, number> = {
  continent: 700,
  kingdom: 600,
  region: 400,
  city: 400,
  site: 400,
  building: 400,
}

const TIERS = ["continent", "kingdom", "region", "city", "site", "building"] as const

const TIER_LABELS: Record<string, string> = {
  continent: "大洲",
  kingdom: "国",
  region: "区域",
  city: "城镇",
  site: "地点",
  building: "建筑",
}

function getVisibleTiers(scale: number): string {
  const visible = TIERS.filter((t) => scale >= (TIER_MIN_SCALE[t] ?? 99))
  if (visible.length === 0) return ""
  return visible.map((t) => TIER_LABELS[t] ?? t).join("/")
}

// ── Type colors ─────────────────────────────────
const CELESTIAL_KW = [
  "天宫", "天庭", "天门", "天界", "三十三天", "大罗天", "离恨天",
  "兜率宫", "凌霄殿", "蟠桃园", "瑶池", "灵霄宝殿", "九天应元府",
]
const UNDERWORLD_KW = [
  "地府", "冥界", "幽冥", "阴司", "阴曹", "黄泉",
  "奈何桥", "阎罗殿", "森罗殿", "枉死城",
]

function locationColor(type: string, name?: string): string {
  if (name) {
    if (CELESTIAL_KW.some((kw) => name.includes(kw))) return "#f59e0b"
    if (UNDERWORLD_KW.some((kw) => name.includes(kw))) return "#7c3aed"
  }
  const t = type.toLowerCase()
  if (t.includes("国") || t.includes("域") || t.includes("界")) return "#3b82f6"
  if (t.includes("城") || t.includes("镇") || t.includes("都") || t.includes("村"))
    return "#10b981"
  if (t.includes("山") || t.includes("洞") || t.includes("谷") || t.includes("林"))
    return "#84cc16"
  if (t.includes("宗") || t.includes("派") || t.includes("门")) return "#8b5cf6"
  if (t.includes("海") || t.includes("河") || t.includes("湖")) return "#06b6d4"
  return "#6b7280"
}

// ── Layer background colors ─────────────────────────
const LAYER_BG_COLORS: Record<LayerType, string> = {
  overworld: "#eee5d0",
  sky: "#0f172a",
  underground: "#1a0a2e",
  sea: "#0a2540",
  pocket: "#1c1917",
  spirit: "#1a0a2e",
}

function getMapBgColor(layoutMode: string, layerType?: string): string {
  if (layoutMode === "hierarchy") return "#1a1a2e"
  return LAYER_BG_COLORS[(layerType ?? "overworld") as LayerType] ?? "#f0ead6"
}

function isDarkBackground(layoutMode: string, layerType?: string): boolean {
  return layoutMode === "hierarchy" || (layerType != null && layerType !== "overworld")
}

// ── Portal colors ──────────────────────────────────
const PORTAL_COLORS: Record<string, string> = {
  sky: "#f59e0b",
  underground: "#7c3aed",
  sea: "#06b6d4",
  pocket: "#a0845c",
  spirit: "#7c3aed",
  overworld: "#3b82f6",
}

// ── Icon names ──────────────────────────────────────
const ICON_NAMES = [
  "capital", "city", "town", "village", "camp",
  "mountain", "forest", "water", "desert", "island",
  "temple", "palace", "cave", "tower", "gate",
  "portal", "ruins", "sacred", "generic",
] as const

// ── Props ───────────────────────────────────────────
export interface NovelMapProps {
  locations: MapLocation[]
  layout: MapLayoutItem[]
  layoutMode: "constraint" | "hierarchy" | "layered" | "geographic"
  layerType?: string
  terrainUrl: string | null
  visibleLocationNames: Set<string>
  revealedLocationNames?: Set<string>
  regionBoundaries?: RegionBoundary[]
  portals?: PortalInfo[]
  trajectoryPoints?: TrajectoryPoint[]
  currentLocation?: string | null
  canvasSize?: { width: number; height: number }
  spatialScale?: string
  focusLocation?: string | null
  locationConflicts?: LocationConflict[]
  onLocationClick?: (name: string) => void
  onLocationDragEnd?: (name: string, x: number, y: number) => void
  onPortalClick?: (targetLayerId: string) => void
}

export interface NovelMapHandle {
  fitToLocations: () => void
}

// ── Popup state ─────────────────────────────────────
interface PopupState {
  x: number
  y: number
  content: "location" | "portal"
  name: string
  locType?: string
  parent?: string
  mentionCount?: number
  targetLayer?: string
  targetLayerName?: string
}

// ── Component ───────────────────────────────────────
export const NovelMap = forwardRef<NovelMapHandle, NovelMapProps>(
  function NovelMap(
    {
      locations,
      layout,
      layoutMode,
      layerType,
      visibleLocationNames,
      revealedLocationNames,
      regionBoundaries,
      portals,
      trajectoryPoints,
      currentLocation,
      canvasSize: canvasSizeProp,
      focusLocation,
      locationConflicts,
      onLocationClick,
      onLocationDragEnd,
      onPortalClick,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null)
    const svgRef = useRef<SVGSVGElement | null>(null)
    const zoomRef = useRef<d3Zoom.ZoomBehavior<SVGSVGElement, unknown> | null>(null)
    const transformRef = useRef<d3Zoom.ZoomTransform>(d3Zoom.zoomIdentity)
    const [currentScale, setCurrentScale] = useState(1)
    const [mapReady, setMapReady] = useState(false)
    const [popup, setPopup] = useState<PopupState | null>(null)
    const [iconDefs, setIconDefs] = useState<Map<string, string>>(new Map())

    // Stable refs for callbacks
    const onClickRef = useRef(onLocationClick)
    onClickRef.current = onLocationClick
    const onDragEndRef = useRef(onLocationDragEnd)
    onDragEndRef.current = onLocationDragEnd
    const onPortalClickRef = useRef(onPortalClick)
    onPortalClickRef.current = onPortalClick

    const canvasW = canvasSizeProp?.width ?? DEFAULT_CANVAS.width
    const canvasH = canvasSizeProp?.height ?? DEFAULT_CANVAS.height
    const darkBg = isDarkBackground(layoutMode, layerType)
    const bgColor = getMapBgColor(layoutMode, layerType)

    // Build layout lookup
    const layoutMap = useMemo(() => {
      const m = new Map<string, MapLayoutItem>()
      for (const item of layout) m.set(item.name, item)
      return m
    }, [layout])

    // Build location lookup
    const locMap = useMemo(() => {
      const m = new Map<string, MapLocation>()
      for (const loc of locations) m.set(loc.name, loc)
      return m
    }, [locations])

    // Territory generation
    const territories = useMemo(
      () => generateHullTerritories(locations, layout, { width: canvasW, height: canvasH }),
      [locations, layout, canvasW, canvasH],
    )

    // ── Load SVG icons ──────────────────────────────
    useEffect(() => {
      let cancelled = false
      const defs = new Map<string, string>()

      Promise.all(
        ICON_NAMES.map(async (name) => {
          try {
            const resp = await fetch(`/map-icons/${name}.svg`)
            const text = await resp.text()
            // Extract inner SVG content
            const match = text.match(/<svg[^>]*>([\s\S]*)<\/svg>/i)
            if (match) {
              defs.set(name, match[1])
            }
          } catch {
            // graceful fallback
          }
        }),
      ).then(() => {
        if (!cancelled) setIconDefs(defs)
      })

      return () => { cancelled = true }
    }, [])

    // ── Initialize SVG + d3-zoom ────────────────────
    useEffect(() => {
      if (!containerRef.current) return

      // Create SVG element
      const container = d3Selection.select(containerRef.current)
      container.selectAll("svg").remove()

      const svg = container
        .append("svg")
        .attr("class", "h-full w-full")
        .style("cursor", "grab")
        .style("user-select", "none")

      svgRef.current = svg.node()!

      // Defs for filters
      const defs = svg.append("defs")

      // Parchment noise filter
      const parchmentFilter = defs.append("filter").attr("id", "parchment-noise")
      parchmentFilter
        .append("feTurbulence")
        .attr("type", "fractalNoise")
        .attr("baseFrequency", "0.65")
        .attr("numOctaves", "4")
        .attr("stitchTiles", "stitch")
      parchmentFilter
        .append("feColorMatrix")
        .attr("type", "saturate")
        .attr("values", "0")
      parchmentFilter
        .append("feBlend")
        .attr("in", "SourceGraphic")
        .attr("mode", "multiply")

      // Parchment stain filter (low-frequency large-scale color variation)
      const stainFilter = defs.append("filter").attr("id", "parchment-stain")
      stainFilter
        .append("feTurbulence")
        .attr("type", "fractalNoise")
        .attr("baseFrequency", "0.003")
        .attr("numOctaves", "2")
        .attr("stitchTiles", "stitch")
      stainFilter
        .append("feColorMatrix")
        .attr("type", "saturate")
        .attr("values", "0")
      stainFilter
        .append("feBlend")
        .attr("in", "SourceGraphic")
        .attr("mode", "multiply")

      // Hand-drawn line filter (subtle roughness)
      const handDrawnFilter = defs.append("filter").attr("id", "hand-drawn")
      handDrawnFilter
        .append("feTurbulence")
        .attr("type", "turbulence")
        .attr("baseFrequency", "0.02")
        .attr("numOctaves", "3")
        .attr("result", "noise")
      handDrawnFilter
        .append("feDisplacementMap")
        .attr("in", "SourceGraphic")
        .attr("in2", "noise")
        .attr("scale", "6")
        .attr("xChannelSelector", "R")
        .attr("yChannelSelector", "G")

      // Viewport group (transformed by zoom)
      const viewport = svg.append("g").attr("id", "viewport")

      // Background
      viewport
        .append("rect")
        .attr("id", "bg")
        .attr("width", canvasW)
        .attr("height", canvasH)
        .attr("fill", bgColor)

      // Parchment texture overlay (only for light backgrounds)
      if (!darkBg) {
        viewport
          .append("rect")
          .attr("id", "bg-texture")
          .attr("width", canvasW)
          .attr("height", canvasH)
          .attr("filter", "url(#parchment-noise)")
          .attr("opacity", 0.10)
          .attr("fill", "#8b7355")

        // Large-scale parchment variation
        viewport
          .append("rect")
          .attr("id", "bg-stain")
          .attr("width", canvasW)
          .attr("height", canvasH)
          .attr("filter", "url(#parchment-stain)")
          .attr("opacity", 0.06)
          .attr("fill", "#6b5c4a")
      }

      // Terrain image placeholder
      viewport.append("g").attr("id", "terrain")

      // Layer groups (Z-order)
      viewport.append("g").attr("id", "regions")
      viewport.append("g").attr("id", "region-labels")
      viewport.append("g").attr("id", "territories")
      viewport.append("g").attr("id", "territory-labels")
      viewport.append("g").attr("id", "trajectory")
      viewport.append("g").attr("id", "overview-dots")

      for (const tier of TIERS) {
        viewport.append("g").attr("id", `locations-${tier}`).attr("class", `tier-${tier}`)
      }

      viewport.append("g").attr("id", "portals")
      viewport.append("g").attr("id", "conflict-markers")
      viewport.append("g").attr("id", "focus-overlay")

      // Setup d3-zoom
      const zoom = d3Zoom
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.2, 10])
        .on("zoom", (event: d3Zoom.D3ZoomEvent<SVGSVGElement, unknown>) => {
          viewport.attr("transform", event.transform.toString())
          transformRef.current = event.transform
          setCurrentScale(event.transform.k)
        })

      svg.call(zoom)
      svg.on("dblclick.zoom", null) // disable double-click zoom
      zoomRef.current = zoom

      setMapReady(true)

      return () => {
        container.selectAll("svg").remove()
        svgRef.current = null
        zoomRef.current = null
        setMapReady(false)
        setPopup(null)
      }
    }, [canvasW, canvasH, layoutMode, layerType, bgColor, darkBg])

    // ── Terrain image (disabled: Voronoi boundary darkening conflicts with
    //    territory/region layers, causing visual clutter) ──────────────
    // Pure SVG parchment texture (bg + bg-texture) provides a cleaner base.

    // ── Render regions (text-only labels, no polygon boundaries) ───
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const regionsG = svg.select("#regions")
      const labelsG = svg.select("#region-labels")
      regionsG.selectAll("*").remove()
      labelsG.selectAll("*").remove()

      if (!regionBoundaries || regionBoundaries.length === 0) return

      for (const rb of regionBoundaries) {
        // Text-only label at region center (no polygon border)
        labelsG
          .append("text")
          .attr("x", rb.center[0])
          .attr("y", rb.center[1])
          .attr("text-anchor", "middle")
          .attr("dominant-baseline", "central")
          .attr("fill", darkBg ? "#ffffff" : "#8b7355")
          .attr("opacity", 0.25)
          .attr("font-size", "26px")
          .attr("font-weight", "300")
          .attr("letter-spacing", "6px")
          .style("pointer-events", "none")
          .text(rb.region_name)
      }
    }, [mapReady, regionBoundaries, canvasW, canvasH, darkBg])

    // ── Render territories (nested convex hulls) ──────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const terrG = svg.select("#territories")
      const terrLabelsG = svg.select("#territory-labels")
      terrG.selectAll("*").remove()
      terrLabelsG.selectAll("*").remove()

      if (territories.length === 0) return

      const isDense = territories.length > 15

      // Per-level rendering parameters
      const STROKE_WIDTH = [3.0, 2.2, 1.5, 1.0]
      const STROKE_OP = isDense
        ? [0.20, 0.12, 0.08, 0.06]
        : [0.40, 0.30, 0.20, 0.15]
      const FILL_OP = [0.05, 0.04, 0.03, 0.02]
      const DASH = ["12,6", "8,4", "6,3", "4,3"]
      const DISTORT_SEGS = [20, 16, 12, 10]
      const LABEL_SIZE = [16, 13, 11, 10]
      const LABEL_OP = isDense
        ? [0.20, 0.12, 0.08, 0.06]
        : [0.35, 0.25, 0.20, 0.15]
      const LABEL_SPACING = ["3px", "1px", "0", "0"]

      const clamp = (level: number) => Math.min(level, 3)

      for (const terr of territories) {
        const li = clamp(terr.level)

        // Deterministic seed per territory name for unique hand-drawn ripple
        const seed = hashString(terr.name) % 100

        const distorted = distortPolygonEdges(
          terr.polygon,
          canvasW,
          canvasH,
          DISTORT_SEGS[li],
          seed,
        )
        const pathData = polygonToPath(distorted)

        const strokeColor = darkBg ? terr.color : "#8b7355"
        const fillColor = darkBg ? terr.color : "#c4a97d"

        terrG
          .append("path")
          .attr("d", pathData)
          .attr("fill", fillColor)
          .attr("fill-opacity", FILL_OP[li])
          .attr("stroke", strokeColor)
          .attr("stroke-opacity", STROKE_OP[li])
          .attr("stroke-width", STROKE_WIDTH[li])
          .attr("stroke-dasharray", DASH[li])
          .attr("stroke-linejoin", "round")

        // Label at centroid
        const centroid = polygonCentroid(terr.polygon)

        terrLabelsG
          .append("text")
          .attr("x", centroid[0])
          .attr("y", centroid[1])
          .attr("text-anchor", "middle")
          .attr("dominant-baseline", "central")
          .attr("fill", darkBg ? terr.color : "#6b5c4a")
          .attr("opacity", LABEL_OP[li])
          .attr("font-size", `${LABEL_SIZE[li]}px`)
          .attr("font-weight", "300")
          .attr("letter-spacing", LABEL_SPACING[li])
          .style("pointer-events", "none")
          .text(terr.name)
      }
    }, [mapReady, territories, canvasW, canvasH, darkBg])

    // ── Render trajectory ────────────────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const trajG = svg.select("#trajectory")
      trajG.selectAll("*").remove()

      if (!trajectoryPoints || trajectoryPoints.length === 0) return

      const coords: Point[] = []
      for (const pt of trajectoryPoints) {
        const item = layoutMap.get(pt.location)
        if (item) coords.push([item.x, item.y])
      }

      if (coords.length < 2) return

      // Draw line
      const lineGen = d3Shape
        .line<Point>()
        .x((d) => d[0])
        .y((d) => d[1])
        .curve(d3Shape.curveCardinal.tension(0.5))

      trajG
        .append("path")
        .attr("d", lineGen(coords)!)
        .attr("fill", "none")
        .attr("stroke", "#f59e0b")
        .attr("stroke-width", 3)
        .attr("stroke-opacity", 0.85)
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")

      // Draw points
      for (const coord of coords) {
        trajG
          .append("circle")
          .attr("cx", coord[0])
          .attr("cy", coord[1])
          .attr("r", 5)
          .attr("fill", "#d97706")
          .attr("stroke", "#fff")
          .attr("stroke-width", 1.5)
      }
    }, [mapReady, trajectoryPoints, layoutMap])

    // ── Render overview dots ─────────────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const dotsG = svg.select("#overview-dots")
      dotsG.selectAll("*").remove()

      const revealed = revealedLocationNames ?? new Set<string>()
      const locationItems = layout.filter((item) => !item.is_portal)

      for (const item of locationItems) {
        const loc = locMap.get(item.name)
        const isActive = visibleLocationNames.has(item.name)
        const isRevealed = !isActive && revealed.has(item.name)
        const isCurrent = currentLocation === item.name
        const locRole = loc?.role

        const typeColor = locationColor(loc?.type ?? "", item.name)
        let color: string
        let opacity: number

        if (isCurrent) {
          color = "#f59e0b"
          opacity = 1
        } else if (isActive) {
          color = typeColor
          opacity = 0.8
        } else if (isRevealed) {
          color = "#9ca3af"
          opacity = 0.3
        } else {
          color = typeColor
          opacity = 0.2
        }

        // Role-based adjustments for active locations
        const tier = loc?.tier ?? "city"
        let dotRadius = TIER_DOT_RADIUS[tier] ?? 3
        if (isActive && locRole === "referenced") {
          opacity *= 0.5
          dotRadius *= 0.7
        } else if (isActive && locRole === "boundary") {
          opacity *= 0.6
          dotRadius *= 0.7
        }

        const dot = dotsG
          .append("circle")
          .attr("cx", item.x)
          .attr("cy", item.y)
          .attr("r", dotRadius)
          .attr("fill", color)
          .attr("opacity", opacity)

        if (isCurrent) {
          dot
            .attr("stroke", "#92400e")
            .attr("stroke-width", 1.5)
        }
      }
    }, [mapReady, layout, locMap, visibleLocationNames, revealedLocationNames, currentLocation])

    // ── Render location icons + labels (counter-scaled) ──
    useEffect(() => {
      if (!svgRef.current || !mapReady || iconDefs.size === 0) return
      const svg = d3Selection.select(svgRef.current)
      const revealed = revealedLocationNames ?? new Set<string>()
      const locationItems = layout.filter((item) => !item.is_portal)

      // Clear all tier groups
      for (const tier of TIERS) {
        svg.select(`#locations-${tier}`).selectAll("*").remove()
      }

      for (const item of locationItems) {
        const loc = locMap.get(item.name)
        const tier = (loc?.tier ?? "city") as typeof TIERS[number]
        const tierG = svg.select(`#locations-${tier}`)
        if (tierG.empty()) continue

        const isActive = visibleLocationNames.has(item.name)
        const isRevealed = !isActive && revealed.has(item.name)
        const isCurrent = currentLocation === item.name
        const mention = loc?.mention_count ?? 0
        const locRole = loc?.role

        let color: string
        let opacity: number
        if (isCurrent) {
          color = "#f59e0b"
          opacity = 1
        } else if (isActive) {
          color = locationColor(loc?.type ?? "", item.name)
          opacity = 1
        } else if (isRevealed) {
          color = "#9ca3af"
          opacity = 0.35
        } else {
          color = locationColor(loc?.type ?? "", item.name)
          opacity = 0.2
        }

        // Role-based adjustments for active locations
        let iconScale = 1.0
        let strokeDasharray: string | null = null
        if (isActive && locRole === "referenced") {
          opacity *= 0.5
          iconScale = 0.7
        } else if (isActive && locRole === "boundary") {
          opacity *= 0.6
          strokeDasharray = "3 2"
        }

        const iconName = loc?.icon ?? "generic"
        const baseIconSize = TIER_ICON_SIZE[tier] ?? 20
        const iconSize = baseIconSize * iconScale

        // Location group — counter-scaled at position
        // The group translates to the location point; icon/label use local coords
        const locG = tierG
          .append("g")
          .attr("class", "location-item")
          .attr("data-name", item.name)
          .attr("data-tier", tier)
          .attr("data-x", item.x)
          .attr("data-y", item.y)
          .style("cursor", "pointer")

        // Icon — render as inner SVG group (local coords centered at origin)
        const iconContent = iconDefs.get(iconName)
        if (iconContent) {
          const iconG = locG
            .append("g")
            .attr("class", "loc-icon")
            .attr(
              "transform",
              `translate(${item.x - iconSize / 2}, ${item.y - iconSize / 2}) scale(${iconSize / 48})`,
            )
            .attr("fill", color)
            .attr("opacity", opacity)
          iconG.html(iconContent)
        }

        // Lock indicator for locked locations
        if (loc?.locked) {
          locG
            .append("text")
            .attr("x", item.x + iconSize / 2 + 2)
            .attr("y", item.y - iconSize / 2)
            .attr("font-size", "10px")
            .attr("fill", darkBg ? "#fbbf24" : "#b45309")
            .attr("opacity", opacity)
            .style("pointer-events", "none")
            .text("\uD83D\uDD12")  // lock emoji
        }

        // Dashed border ring for boundary-role locations
        if (strokeDasharray && isActive) {
          locG
            .append("circle")
            .attr("cx", item.x)
            .attr("cy", item.y)
            .attr("r", iconSize / 2 + 3)
            .attr("fill", "none")
            .attr("stroke", color)
            .attr("stroke-width", 1)
            .attr("stroke-dasharray", strokeDasharray)
            .attr("opacity", opacity)
        }

        // Label (hidden by default — collision detection will show visible ones)
        const textColor = isRevealed
          ? "#9ca3af"
          : mention >= 3
            ? darkBg ? "#e5e7eb" : "#374151"
            : "#9ca3af"
        const fontSize = TIER_TEXT_SIZE[tier] ?? 12

        locG
          .append("text")
          .attr("class", "loc-label")
          .attr("x", item.x)
          .attr("y", item.y + iconSize / 2 + fontSize * 0.9)
          .attr("text-anchor", "middle")
          .attr("font-size", `${fontSize}px`)
          .attr("font-weight", TIER_FONT_WEIGHT[tier] ?? 400)
          .attr("fill", textColor)
          .attr("opacity", opacity)
          .attr("stroke", darkBg ? "rgba(0,0,0,0.6)" : "#ffffff")
          .attr("stroke-width", (TIER_FONT_WEIGHT[tier] ?? 400) >= 600 ? 2.5 : 1.5)
          .attr("paint-order", "stroke")
          .style("pointer-events", "none")
          .text(item.name)

        // Click handler
        locG.on("click", (event: MouseEvent) => {
          event.stopPropagation()
          setPopup({
            x: item.x,
            y: item.y,
            content: "location",
            name: item.name,
            locType: loc?.type ?? "",
            parent: loc?.parent ?? "",
            mentionCount: mention,
          })
        })
      }

      // Setup drag on location groups
      setupDrag(svg)
    }, [
      mapReady, layout, locMap, locations, iconDefs,
      visibleLocationNames, revealedLocationNames, currentLocation, darkBg,
    ])

    // ── Setup drag behavior ──────────────────────────
    const setupDrag = useCallback(
      (svg: d3Selection.Selection<SVGSVGElement, unknown, null, undefined>) => {
        const locationItems = svg.selectAll<SVGGElement, unknown>(".location-item")

        const drag = d3Drag
          .drag<SVGGElement, unknown>()
          .on("start", function (event: d3Drag.D3DragEvent<SVGGElement, unknown, unknown>) {
            // Prevent zoom during drag
            event.sourceEvent.stopPropagation()
            d3Selection.select(this).raise().style("cursor", "grabbing")
          })
          .on("drag", function (event: d3Drag.D3DragEvent<SVGGElement, unknown, unknown>) {
            const g = d3Selection.select(this)
            const name = g.attr("data-name")
            if (!name) return
            const tier = g.attr("data-tier") as string
            const iconSize = TIER_ICON_SIZE[tier] ?? 20
            const fontSize = TIER_TEXT_SIZE[tier] ?? 12
            const iconName = locMap.get(name)?.icon ?? "generic"

            // Convert screen dx/dy to canvas coords by dividing by current scale
            const t = transformRef.current
            const canvasX = (event.sourceEvent.offsetX - t.x) / t.k
            const canvasY = (event.sourceEvent.offsetY - t.y) / t.k

            // Update data attributes for counter-scale
            g.attr("data-x", canvasX).attr("data-y", canvasY)
            g.attr("transform",
              `translate(${canvasX},${canvasY}) scale(${1 / t.k}) translate(${-canvasX},${-canvasY})`)

            // Update icon position
            const iconG = g.select(".loc-icon")
            if (!iconG.empty() && iconDefs.has(iconName)) {
              iconG.attr(
                "transform",
                `translate(${canvasX - iconSize / 2}, ${canvasY - iconSize / 2}) scale(${iconSize / 48})`,
              )
            }

            // Update text position
            g.select(".loc-label")
              .attr("x", canvasX)
              .attr("y", canvasY + iconSize / 2 + fontSize * 0.9)
          })
          .on("end", function (event: d3Drag.D3DragEvent<SVGGElement, unknown, unknown>) {
            d3Selection.select(this).style("cursor", "pointer")
            const name = d3Selection.select(this).attr("data-name")
            if (!name) return

            const t = transformRef.current
            const canvasX = (event.sourceEvent.offsetX - t.x) / t.k
            const canvasY = (event.sourceEvent.offsetY - t.y) / t.k

            onDragEndRef.current?.(name, canvasX, canvasY)
          })

        locationItems.call(drag)
      },
      [locMap, iconDefs],
    )

    // ── Render portals ───────────────────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const portalsG = svg.select("#portals")
      portalsG.selectAll("*").remove()

      // Portal items from layout
      const portalItems = layout.filter((item) => item.is_portal)
      const portalInfoMap = new Map<string, PortalInfo>()
      if (portals) {
        for (const p of portals) portalInfoMap.set(p.name, p)
      }

      const allPortals: {
        name: string
        x: number
        y: number
        targetLayer: string
        targetLayerName: string
        color: string
      }[] = []

      for (const item of portalItems) {
        const info = portalInfoMap.get(item.name)
        const targetLayer = info?.target_layer ?? item.target_layer ?? ""
        const color = PORTAL_COLORS[targetLayer] ?? PORTAL_COLORS.overworld
        allPortals.push({
          name: item.name,
          x: item.x,
          y: item.y,
          targetLayer,
          targetLayerName: info?.target_layer_name ?? targetLayer,
          color,
        })
      }

      // Also add portals from props not in layout
      if (portals) {
        const layoutNames = new Set(portalItems.map((p) => p.name))
        for (const p of portals) {
          if (layoutNames.has(p.name)) continue
          const srcItem = layoutMap.get(p.source_location)
          if (!srcItem) continue
          const color = PORTAL_COLORS[p.target_layer] ?? PORTAL_COLORS.overworld
          allPortals.push({
            name: p.name,
            x: srcItem.x,
            y: srcItem.y,
            targetLayer: p.target_layer,
            targetLayerName: p.target_layer_name,
            color,
          })
        }
      }

      for (const portal of allPortals) {
        const portalG = portalsG
          .append("g")
          .style("cursor", "pointer")

        portalG
          .append("text")
          .attr("x", portal.x)
          .attr("y", portal.y)
          .attr("text-anchor", "middle")
          .attr("dominant-baseline", "central")
          .attr("font-size", "20px")
          .attr("fill", portal.color)
          .attr("stroke", darkBg ? "rgba(0,0,0,0.6)" : "rgba(255,255,255,0.8)")
          .attr("stroke-width", 2)
          .attr("paint-order", "stroke")
          .text("⊙")

        portalG.on("click", (event: MouseEvent) => {
          event.stopPropagation()
          setPopup({
            x: portal.x,
            y: portal.y,
            content: "portal",
            name: portal.name,
            targetLayer: portal.targetLayer,
            targetLayerName: portal.targetLayerName,
          })
        })
      }
    }, [mapReady, layout, portals, layoutMap, darkBg])

    // ── Render conflict markers ─────────────────────────
    // Build conflict index: location name -> conflict descriptions
    const conflictIndex = useMemo(() => {
      const idx = new Map<string, string[]>()
      if (!locationConflicts?.length) return idx
      for (const c of locationConflicts) {
        if (!c.entity) continue
        const existing = idx.get(c.entity) ?? []
        existing.push(c.description)
        idx.set(c.entity, existing)
      }
      return idx
    }, [locationConflicts])

    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const conflictG = svg.select("#conflict-markers")
      conflictG.selectAll("*").remove()

      if (conflictIndex.size === 0) return

      for (const item of layout) {
        if (item.is_portal) continue
        const descriptions = conflictIndex.get(item.name)
        if (!descriptions) continue

        // Red dashed pulse ring
        const ring = conflictG
          .append("circle")
          .attr("cx", item.x)
          .attr("cy", item.y)
          .attr("r", 18)
          .attr("fill", "none")
          .attr("stroke", "#ef4444")
          .attr("stroke-width", 1.5)
          .attr("stroke-dasharray", "4 3")
          .attr("opacity", 0.8)

        // Pulse animation: scale the ring
        const animateScale = () => {
          ring
            .attr("r", 18)
            .attr("opacity", 0.8)
            .transition()
            .duration(1200)
            .attr("r", 26)
            .attr("opacity", 0.2)
            .on("end", animateScale)
        }
        animateScale()

        // Click handler: show conflict details in popup
        conflictG
          .append("circle")
          .attr("cx", item.x)
          .attr("cy", item.y)
          .attr("r", 20)
          .attr("fill", "transparent")
          .style("cursor", "pointer")
          .on("click", (event: MouseEvent) => {
            event.stopPropagation()
            const loc = locMap.get(item.name)
            setPopup({
              x: item.x,
              y: item.y,
              content: "location",
              name: item.name,
              locType: loc?.type ?? "",
              parent: loc?.parent ?? "",
              mentionCount: loc?.mention_count ?? 0,
            })
          })
      }
    }, [mapReady, layout, conflictIndex, locMap])

    // ── Zoom-based visibility + counter-scale + collision detection ──
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const k = currentScale

      // Tier visibility — fade in over 30% of threshold range instead of hard cut
      for (const tier of TIERS) {
        const minScale = TIER_MIN_SCALE[tier] ?? 1.2
        const fadeRange = minScale * 0.3
        const tierOpacity = Math.min(1, Math.max(0, (k - minScale + fadeRange) / fadeRange))
        svg
          .select(`#locations-${tier}`)
          .style("display", tierOpacity > 0 ? "" : "none")
          .style("opacity", tierOpacity > 0 ? tierOpacity : null)
      }

      // Counter-scale: keep icons + labels at constant screen size
      svg.selectAll<SVGGElement, unknown>(".location-item").each(function () {
        const g = d3Selection.select(this)
        const x = parseFloat(g.attr("data-x"))
        const y = parseFloat(g.attr("data-y"))
        if (isNaN(x) || isNaN(y)) return
        // Translate to position, scale by 1/k, translate back
        g.attr("transform", `translate(${x},${y}) scale(${1 / k}) translate(${-x},${-y})`)
      })

      // Collision detection — build screen-space label rects
      const labelRects: LabelRect[] = []
      svg.selectAll<SVGGElement, unknown>(".location-item").each(function () {
        const g = d3Selection.select(this)
        // Check if this tier is visible (include fading-in tiers)
        const tier = g.attr("data-tier") ?? "city"
        const minScale = TIER_MIN_SCALE[tier] ?? 1.2
        const fadeRange = minScale * 0.3
        if (k < minScale - fadeRange) return

        const name = g.attr("data-name") ?? ""
        const x = parseFloat(g.attr("data-x"))
        const y = parseFloat(g.attr("data-y"))
        if (isNaN(x) || isNaN(y)) return

        const loc = locMap.get(name)
        const mention = loc?.mention_count ?? 0
        const tierW = TIER_WEIGHT[tier] ?? 1
        const fontSize = TIER_TEXT_SIZE[tier] ?? 12
        const iconSize = TIER_ICON_SIZE[tier] ?? 20

        // Estimate label width in screen pixels (Chinese chars ~= fontSize each)
        const labelW = name.length * fontSize + 4
        const labelH = fontSize + 4
        // Label position in screen-space (counter-scaled, so fontSize stays constant)
        const labelY = y + (iconSize / 2 + fontSize * 0.9) / k
        const screenX = x * k
        const screenY = labelY * k

        labelRects.push({
          x: screenX - labelW / 2,
          y: screenY - labelH / 2,
          w: labelW,
          h: labelH,
          name,
          priority: tierW * 1000 + mention,
        })
      })

      const visibleLabels = computeLabelCollisions(labelRects)

      // Apply visibility to labels
      svg.selectAll<SVGGElement, unknown>(".location-item").each(function () {
        const g = d3Selection.select(this)
        const name = g.attr("data-name") ?? ""
        g.select(".loc-label").style("display", visibleLabels.has(name) ? "" : "none")
      })

      // Territory labels fade at high zoom
      svg
        .select("#territory-labels")
        .style("opacity", k < 2 ? 1 : 0.3)
      svg
        .select("#region-labels")
        .style("opacity", k < 1.5 ? 1 : 0.3)

      // Overview dots fade at high zoom
      svg
        .select("#overview-dots")
        .style("opacity", k > 1.5 ? 0.3 : 1)
    }, [mapReady, currentScale, locMap])

    // ── Fit to locations ─────────────────────────────
    const fitToLocations = useCallback(() => {
      if (!svgRef.current || !zoomRef.current || layout.length === 0) return

      const svg = d3Selection.select(svgRef.current)
      const svgNode = svgRef.current
      const svgWidth = svgNode.clientWidth || svgNode.getBoundingClientRect().width
      const svgHeight = svgNode.clientHeight || svgNode.getBoundingClientRect().height

      if (svgWidth === 0 || svgHeight === 0) return

      // Compute bounding box of all layout items
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (const item of layout) {
        if (item.x < minX) minX = item.x
        if (item.y < minY) minY = item.y
        if (item.x > maxX) maxX = item.x
        if (item.y > maxY) maxY = item.y
      }

      const padding = 60
      const bboxW = maxX - minX || 100
      const bboxH = maxY - minY || 100
      const scale = Math.min(
        (svgWidth - padding * 2) / bboxW,
        (svgHeight - padding * 2) / bboxH,
        5, // max zoom
      )
      const cx = (minX + maxX) / 2
      const cy = (minY + maxY) / 2

      const transform = d3Zoom.zoomIdentity
        .translate(svgWidth / 2, svgHeight / 2)
        .scale(scale)
        .translate(-cx, -cy)

      svg
        .transition()
        .duration(500)
        .call(zoomRef.current.transform, transform)
    }, [layout])

    useImperativeHandle(ref, () => ({ fitToLocations }), [fitToLocations])

    // Auto-fit when layout changes
    useEffect(() => {
      if (mapReady && layout.length > 0) {
        const t = setTimeout(fitToLocations, 200)
        return () => clearTimeout(t)
      }
    }, [mapReady, layout, fitToLocations])

    // ── Focus location: pan + zoom + persistent highlight ──
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const focusG = svg.select<SVGGElement>("#focus-overlay")
      focusG.selectAll("*").remove()

      if (!focusLocation || !zoomRef.current) return
      const item = layout.find((l) => l.name === focusLocation)
      if (!item) return

      const svgEl = svgRef.current
      const svgWidth = svgEl.clientWidth || 800
      const svgHeight = svgEl.clientHeight || 600

      // Zoom to focus location with comfortable scale
      const focusScale = Math.max(transformRef.current.k, 2.5)
      const transform = d3Zoom.zoomIdentity
        .translate(svgWidth / 2, svgHeight / 2)
        .scale(focusScale)
        .translate(-item.x, -item.y)

      svg
        .transition()
        .duration(600)
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .call(zoomRef.current.transform as any, transform)

      // Counter-scaled focus group: constant screen size regardless of zoom
      const k = transformRef.current.k || focusScale
      const focusItem = focusG
        .append("g")
        .attr("transform", `translate(${item.x},${item.y}) scale(${1 / k}) translate(${-item.x},${-item.y})`)

      // Persistent highlight ring (stays until focus clears)
      const ringR = 22
      focusItem
        .append("circle")
        .attr("cx", item.x)
        .attr("cy", item.y)
        .attr("r", ringR)
        .attr("fill", "rgba(245, 158, 11, 0.12)")
        .attr("stroke", "#f59e0b")
        .attr("stroke-width", 2.5)
        .attr("stroke-dasharray", "6,3")

      // Persistent label above the location
      focusItem
        .append("text")
        .attr("x", item.x)
        .attr("y", item.y - ringR - 6)
        .attr("text-anchor", "middle")
        .attr("font-size", 14)
        .attr("font-weight", "bold")
        .attr("fill", "#f59e0b")
        .attr("stroke", darkBg ? "rgba(0,0,0,0.7)" : "#ffffff")
        .attr("stroke-width", 3)
        .attr("paint-order", "stroke")
        .text(focusLocation)
    }, [focusLocation, layout, mapReady, darkBg])

    // Update focus overlay counter-scale when zoom changes
    useEffect(() => {
      if (!svgRef.current || !mapReady || !focusLocation) return
      const svg = d3Selection.select(svgRef.current)
      const focusG = svg.select<SVGGElement>("#focus-overlay")
      const item = layout.find((l) => l.name === focusLocation)
      if (!item) return
      const k = currentScale
      focusG.select("g")
        .attr("transform", `translate(${item.x},${item.y}) scale(${1 / k}) translate(${-item.x},${-item.y})`)
    }, [currentScale, focusLocation, layout, mapReady])

    // ── Keyboard shortcuts ───────────────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return

      function handleKeyDown(e: KeyboardEvent) {
        const tag = (e.target as HTMLElement)?.tagName
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return

        if (e.key === "Home") {
          e.preventDefault()
          fitToLocations()
        } else if ((e.key === "=" || e.key === "+") && svgRef.current && zoomRef.current) {
          e.preventDefault()
          d3Selection
            .select(svgRef.current)
            .transition()
            .duration(200)
            .call(zoomRef.current.scaleBy, 1.3)
        } else if (e.key === "-" && svgRef.current && zoomRef.current) {
          e.preventDefault()
          d3Selection
            .select(svgRef.current)
            .transition()
            .duration(200)
            .call(zoomRef.current.scaleBy, 0.77)
        }
      }

      window.addEventListener("keydown", handleKeyDown)
      return () => window.removeEventListener("keydown", handleKeyDown)
    }, [mapReady, fitToLocations])

    // ── Close popup on SVG click ─────────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      svg.on("click.popup", () => setPopup(null))
      return () => { svg.on("click.popup", null) }
    }, [mapReady])

    // ── Popup screen position ────────────────────────
    const popupScreenPos = useMemo(() => {
      if (!popup) return null
      const t = transformRef.current
      return {
        x: popup.x * t.k + t.x,
        y: popup.y * t.k + t.y,
      }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [popup, currentScale])

    return (
      <div className="relative h-full w-full">
        <div ref={containerRef} className="h-full w-full" />

        {/* Parchment vignette — only on light overworld background */}
        {!darkBg && (
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse at center, transparent 50%, rgba(120,90,50,0.28) 100%)",
            }}
          />
        )}

        {/* Zoom indicator (bottom-left) */}
        <div
          className="pointer-events-none absolute bottom-2 left-2 text-[11px] px-2 py-1"
          style={{ color: "rgba(120,120,120,0.8)" }}
        >
          {getVisibleTiers(currentScale)}
        </div>

        {/* Toolbar (top-right) */}
        <div className="absolute top-3 right-3 flex flex-col gap-1 z-10">
          <button
            type="button"
            title="查看全貌"
            className="rounded border bg-background/90 px-2 py-1 text-sm shadow hover:bg-background"
            onClick={fitToLocations}
          >
            ⌂
          </button>
          <button
            type="button"
            title="放大"
            className="rounded border bg-background/90 px-2 py-1 text-sm shadow hover:bg-background"
            onClick={() => {
              if (svgRef.current && zoomRef.current) {
                d3Selection
                  .select(svgRef.current)
                  .transition()
                  .duration(200)
                  .call(zoomRef.current.scaleBy, 1.5)
              }
            }}
          >
            +
          </button>
          <button
            type="button"
            title="缩小"
            className="rounded border bg-background/90 px-2 py-1 text-sm shadow hover:bg-background"
            onClick={() => {
              if (svgRef.current && zoomRef.current) {
                d3Selection
                  .select(svgRef.current)
                  .transition()
                  .duration(200)
                  .call(zoomRef.current.scaleBy, 0.67)
              }
            }}
          >
            −
          </button>
        </div>

        {/* Popup overlay */}
        {popup && popupScreenPos && (
          <div
            className="absolute z-20 rounded-lg border bg-background shadow-lg p-3"
            style={{
              left: popupScreenPos.x + 12,
              top: popupScreenPos.y - 10,
              maxWidth: 220,
              fontSize: 13,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {popup.content === "location" ? (
              <>
                <div className="font-semibold mb-1">{popup.name}</div>
                <div className="text-muted-foreground text-[11px] mb-1">
                  {popup.locType}
                  {popup.parent ? ` · ${popup.parent}` : ""}
                </div>
                <div className="text-muted-foreground text-[11px] mb-1.5">
                  出现 {popup.mentionCount} 章
                </div>
                {conflictIndex.has(popup.name) && (
                  <div className="text-[11px] text-red-500 mb-1.5 border-t border-red-200 pt-1">
                    {conflictIndex.get(popup.name)!.map((desc, i) => (
                      <div key={i} className="mb-0.5">{desc}</div>
                    ))}
                  </div>
                )}
                <button
                  className="text-[11px] text-blue-500 underline"
                  onClick={() => {
                    onClickRef.current?.(popup.name)
                    setPopup(null)
                  }}
                >
                  查看卡片
                </button>
                <button
                  className="text-[11px] text-muted-foreground ml-3"
                  onClick={() => setPopup(null)}
                >
                  关闭
                </button>
              </>
            ) : (
              <>
                <div className="font-semibold mb-1">{popup.name}</div>
                <div className="text-muted-foreground text-[11px] mb-1.5">
                  通往: {popup.targetLayerName}
                </div>
                <button
                  className="text-[11px] text-blue-500 underline"
                  onClick={() => {
                    onPortalClickRef.current?.(popup.targetLayer!)
                    setPopup(null)
                  }}
                >
                  进入地图
                </button>
                <button
                  className="text-[11px] text-muted-foreground ml-3"
                  onClick={() => setPopup(null)}
                >
                  关闭
                </button>
              </>
            )}
          </div>
        )}
      </div>
    )
  },
)

// ── Helpers ──────────────────────────────────────

function polygonToPath(pts: Point[]): string {
  if (pts.length === 0) return ""
  return "M " + pts.map(([x, y]) => `${x},${y}`).join(" L ") + " Z"
}

function polygonCentroid(pts: Point[]): Point {
  let cx = 0
  let cy = 0
  for (const [x, y] of pts) {
    cx += x
    cy += y
  }
  const n = pts.length || 1
  return [cx / n, cy / n]
}

/** Simple string hash for deterministic per-territory distortion seed. */
function hashString(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0
  }
  return Math.abs(h)
}
