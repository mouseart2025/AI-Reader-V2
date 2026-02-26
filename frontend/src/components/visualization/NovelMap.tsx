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
import rough from "roughjs"
import type { RoughSVG } from "roughjs/bin/svg"
import { generateHullTerritories } from "@/lib/hullTerritoryGenerator"
import { generateTerrainHints } from "@/lib/terrainHints"
import type { Point } from "@/lib/edgeDistortion"
import {
  convexHull,
  expandHull,
  distortCoastline,
  coastlineToPath,
  type Point as CoastPoint,
} from "@/lib/coastlineGenerator"

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
  iconScreenX: number; iconScreenY: number
  labelW: number; labelH: number
  iconSize: number; fontSize: number
}

interface LabelPlacement {
  anchor: string
  offsetX: number   // screen-space dx from icon center
  offsetY: number   // screen-space dy from icon center
  textAnchor: string // "middle" | "start" | "end"
}

const ANCHOR_CANDIDATES: {
  name: string
  textAnchor: string
  getOffset: (iconH: number, fh: number) => { dx: number; dy: number }
}[] = [
  { name: "bottom",       textAnchor: "middle", getOffset: (iconH, fh) => ({ dx: 0, dy: iconH / 2 + fh * 0.9 }) },
  { name: "right",        textAnchor: "start",  getOffset: (iconH) => ({ dx: iconH / 2 + 4, dy: 0 }) },
  { name: "top-right",    textAnchor: "start",  getOffset: (iconH, fh) => ({ dx: iconH / 2 + 2, dy: -(fh * 0.5 + 2) }) },
  { name: "top",          textAnchor: "middle", getOffset: (iconH, fh) => ({ dx: 0, dy: -(iconH / 2 + fh * 0.3 + 4) }) },
  { name: "top-left",     textAnchor: "end",    getOffset: (iconH, fh) => ({ dx: -(iconH / 2 + 2), dy: -(fh * 0.5 + 2) }) },
  { name: "left",         textAnchor: "end",    getOffset: (iconH) => ({ dx: -(iconH / 2 + 4), dy: 0 }) },
  { name: "bottom-left",  textAnchor: "end",    getOffset: (iconH, fh) => ({ dx: -(iconH / 2 + 2), dy: fh * 0.5 + 2 }) },
  { name: "bottom-right", textAnchor: "start",  getOffset: (iconH, fh) => ({ dx: iconH / 2 + 2, dy: fh * 0.5 + 2 }) },
]

/** Compute AABB in screen-space for a label at a given anchor offset */
function computeAnchorRect(
  iconSX: number, iconSY: number,
  dx: number, dy: number,
  labelW: number, labelH: number,
  textAnchor: string,
): { x: number; y: number; w: number; h: number } {
  const cx = iconSX + dx
  const cy = iconSY + dy
  let x: number
  if (textAnchor === "middle") {
    x = cx - labelW / 2
  } else if (textAnchor === "start") {
    x = cx
  } else {
    // "end"
    x = cx - labelW
  }
  return { x, y: cy - labelH / 2, w: labelW, h: labelH }
}

function computeLabelLayout(rects: LabelRect[]): Map<string, LabelPlacement> {
  const sorted = [...rects].sort((a, b) => b.priority - a.priority)
  const result = new Map<string, LabelPlacement>()

  const cellSize = 60
  const grid = new Map<number, { x: number; y: number; w: number; h: number }[]>()

  const cellKeyAt = (cx: number, cy: number) => cx * 100003 + cy

  const getCellRange = (r: { x: number; y: number; w: number; h: number }) => ({
    x0: Math.floor(r.x / cellSize),
    x1: Math.floor((r.x + r.w) / cellSize),
    y0: Math.floor(r.y / cellSize),
    y1: Math.floor((r.y + r.h) / cellSize),
  })

  const checkCollision = (rect: { x: number; y: number; w: number; h: number }): boolean => {
    const { x0, x1, y0, y1 } = getCellRange(rect)
    for (let cx = x0; cx <= x1; cx++) {
      for (let cy = y0; cy <= y1; cy++) {
        const cell = grid.get(cellKeyAt(cx, cy))
        if (!cell) continue
        for (const p of cell) {
          if (
            rect.x < p.x + p.w && rect.x + rect.w > p.x &&
            rect.y < p.y + p.h && rect.y + rect.h > p.y
          ) return true
        }
      }
    }
    return false
  }

  const registerRect = (rect: { x: number; y: number; w: number; h: number }) => {
    const { x0, x1, y0, y1 } = getCellRange(rect)
    for (let cx = x0; cx <= x1; cx++) {
      for (let cy = y0; cy <= y1; cy++) {
        const key = cellKeyAt(cx, cy)
        let cell = grid.get(key)
        if (!cell) { cell = []; grid.set(key, cell) }
        cell.push(rect)
      }
    }
  }

  for (const r of sorted) {
    let placed = false
    for (const anchor of ANCHOR_CANDIDATES) {
      const { dx, dy } = anchor.getOffset(r.iconSize, r.fontSize)
      const rect = computeAnchorRect(
        r.iconScreenX, r.iconScreenY,
        dx, dy,
        r.labelW, r.labelH,
        anchor.textAnchor,
      )
      if (!checkCollision(rect)) {
        registerRect(rect)
        result.set(r.name, {
          anchor: anchor.name,
          offsetX: dx,
          offsetY: dy,
          textAnchor: anchor.textAnchor,
        })
        placed = true
        break
      }
    }
    if (!placed) {
      // All 8 anchors collide — label stays hidden
    }
  }
  return result
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
  rivers?: { points: number[][]; width: number }[]
  trajectoryPoints?: TrajectoryPoint[]
  allTrajectoryPoints?: TrajectoryPoint[]  // full trajectory (for background dashed path)
  currentLocation?: string | null
  stayDurations?: Map<string, number>
  playing?: boolean
  playIndex?: number
  canvasSize?: { width: number; height: number }
  spatialScale?: string
  focusLocation?: string | null
  locationConflicts?: LocationConflict[]
  collapsedChildCount?: Map<string, number>
  onLocationClick?: (name: string) => void
  onLocationDragEnd?: (name: string, x: number, y: number) => void
  onPortalClick?: (targetLayerId: string) => void
  onToggleExpand?: (parentName: string) => void
}

export interface NovelMapHandle {
  fitToLocations: () => void
  getSvgElement: () => SVGSVGElement | null
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
      rivers,
      terrainUrl,
      trajectoryPoints,
      allTrajectoryPoints,
      currentLocation,
      stayDurations,
      playing: isPlaying,
      playIndex: currentPlayIndex,
      canvasSize: canvasSizeProp,
      focusLocation,
      locationConflicts,
      collapsedChildCount,
      onLocationClick,
      onLocationDragEnd,
      onPortalClick,
      onToggleExpand,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null)
    const svgRef = useRef<SVGSVGElement | null>(null)
    const roughCanvasRef = useRef<RoughSVG | null>(null)
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
    const onToggleExpandRef = useRef(onToggleExpand)
    onToggleExpandRef.current = onToggleExpand

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

    // Terrain texture hints
    const terrainHints = useMemo(
      () => generateTerrainHints(locations, layout, { width: canvasW, height: canvasH }, darkBg),
      [locations, layout, canvasW, canvasH, darkBg],
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

      // Vignette radial gradient
      const vignetteGrad = defs.append("radialGradient")
        .attr("id", "vignette")
        .attr("cx", "50%").attr("cy", "50%").attr("r", "65%")
      vignetteGrad.append("stop").attr("offset", "0%").attr("stop-color", "transparent")
      vignetteGrad.append("stop").attr("offset", "75%").attr("stop-color", "transparent")
      vignetteGrad.append("stop").attr("offset", "100%")
        .attr("stop-color", darkBg ? "rgba(0,0,0,0.7)" : "rgba(10,8,5,0.55)")

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
      } else {
        // Layer-specific atmospheric textures for dark backgrounds
        const effectiveLayer = layoutMode === "hierarchy"
          ? "underground"
          : (layerType ?? "underground")
        renderLayerAtmosphere(viewport, defs, effectiveLayer, canvasW, canvasH)
      }

      // Terrain image placeholder
      viewport.append("g").attr("id", "terrain")
      viewport.append("g").attr("id", "coastline-ocean")
      viewport.append("g").attr("id", "coastline")
      viewport.append("g").attr("id", "rivers")

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

      // Initialize rough.js canvas
      roughCanvasRef.current = rough.svg(svg.node()!)

      // Vignette overlay (outside viewport, fixed position — not affected by zoom)
      svg.append("rect")
        .attr("id", "vignette-overlay")
        .attr("width", "100%")
        .attr("height", "100%")
        .attr("fill", "url(#vignette)")
        .style("pointer-events", "none")
        .style("opacity", darkBg ? "0.3" : "0.5")

      setMapReady(true)

      return () => {
        container.selectAll("svg").remove()
        svgRef.current = null
        roughCanvasRef.current = null
        zoomRef.current = null
        setMapReady(false)
        setPopup(null)
      }
    }, [canvasW, canvasH, layoutMode, layerType, bgColor, darkBg])

    // ── Terrain image (Whittaker biome bottom layer) ──────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady || !terrainUrl) return
      const svg = d3Selection.select(svgRef.current)
      const terrainG = svg.select("#terrain")
      // Remove previous terrain image if any (keep hint symbols via class check)
      terrainG.selectAll("image.terrain-img").remove()

      // Higher opacity on dark backgrounds where colors get washed out
      const terrainOpacity = darkBg ? 0.55 : 0.40

      // Insert terrain PNG as first child (below terrain hint symbols)
      terrainG
        .insert("image", ":first-child")
        .attr("class", "terrain-img")
        .attr("href", terrainUrl)
        .attr("x", 0)
        .attr("y", 0)
        .attr("width", canvasW)
        .attr("height", canvasH)
        .attr("opacity", terrainOpacity)
        .attr("preserveAspectRatio", "none")
        .style("pointer-events", "none")
    }, [mapReady, terrainUrl, canvasW, canvasH, darkBg])

    // ── Render terrain texture hints ─────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const terrainG = svg.select("#terrain")
      terrainG.selectAll("use").remove()

      const { symbolDefs, hints } = terrainHints
      if (hints.length === 0) return

      // Add symbol definitions to <defs>
      const defs = svg.select("defs")
      // Remove old terrain symbols before adding new ones
      defs.selectAll("symbol[id^='terrain-']").remove()

      for (const def of symbolDefs) {
        const sym = defs
          .append("symbol")
          .attr("id", def.id)
          .attr("viewBox", def.viewBox)
        sym.html(def.pathData)
      }

      // Render <use> elements into #terrain group
      for (const hint of hints) {
        const def = symbolDefs.find((d) => d.id === hint.symbolId)
        const sz = hint.size
        const useEl = terrainG
          .append("use")
          .attr("href", `#${hint.symbolId}`)
          .attr("x", hint.x - sz / 2)
          .attr("y", hint.y - sz / 2)
          .attr("width", sz)
          .attr("height", sz)
          .attr("opacity", hint.opacity)
          .attr(
            "transform",
            `rotate(${hint.rotation}, ${hint.x}, ${hint.y})`,
          )
          .style("pointer-events", "none")

        if (def?.strokeOnly) {
          useEl
            .attr("fill", "none")
            .attr("stroke", hint.color)
            .attr("stroke-width", 1.2 + sz / 20)
        } else {
          useEl.attr("fill", hint.color)
        }
      }
    }, [mapReady, terrainHints])

    // ── Render rivers (rough.js hand-drawn) ──────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const riversG = svg.select("#rivers")
      riversG.selectAll("*").remove()

      if (!rivers || rivers.length === 0) return
      const rc = roughCanvasRef.current
      if (!rc) return

      const riverColor = darkBg
        ? "rgba(126,184,216,0.65)"
        : "rgba(80,120,155,0.65)"

      for (const river of rivers) {
        if (river.points.length < 2) continue
        const pts = river.points
        // Build smooth quadratic bezier path (matching demo)
        let d = `M ${pts[0][0]} ${pts[0][1]}`
        for (let i = 1; i < pts.length - 1; i++) {
          const xc = (pts[i][0] + pts[i + 1][0]) / 2
          const yc = (pts[i][1] + pts[i + 1][1]) / 2
          d += ` Q ${pts[i][0]} ${pts[i][1]} ${xc} ${yc}`
        }
        d += ` L ${pts[pts.length - 1][0]} ${pts[pts.length - 1][1]}`

        const node = rc.path(d, {
          roughness: 0.8,
          bowing: 2.0,
          seed: 42,
          stroke: riverColor,
          strokeWidth: river.width * 1.5,
          fill: "none",
        })
        node.style.pointerEvents = "none"
        ;(riversG.node() as Element).appendChild(node)
      }
    }, [mapReady, rivers, darkBg])

    // ── Render coastline + ocean fill (rough.js) ──────────
    useEffect(() => {
      if (!svgRef.current || !mapReady || !roughCanvasRef.current) return
      const svg = d3Selection.select(svgRef.current)
      const oceanG = svg.select("#coastline-ocean")
      const coastG = svg.select("#coastline")
      oceanG.selectAll("*").remove()
      coastG.selectAll("*").remove()

      const allPoints: CoastPoint[] = layout
        .filter((item) => !item.is_portal)
        .map((item) => [item.x, item.y] as CoastPoint)
      if (allPoints.length < 3) return

      // Generate coastline
      const hull = convexHull(allPoints)
      const expanded = expandHull(hull, Math.min(canvasW, canvasH) * 0.08)
      const noisy = distortCoastline(expanded, 42)
      const pathD = coastlineToPath(noisy)

      // Ocean fill (outside coastline, using evenodd fill rule)
      const oceanPath = `M 0 0 L ${canvasW} 0 L ${canvasW} ${canvasH} L 0 ${canvasH} Z ${pathD}`
      oceanG
        .append("path")
        .attr("d", oceanPath)
        .attr("fill", darkBg ? "rgba(20,30,50,0.3)" : "rgba(140,170,195,0.20)")
        .attr("fill-rule", "evenodd")
        .style("pointer-events", "none")

      // Rough.js coastline border
      const rc = roughCanvasRef.current
      const coastNode = rc.path(pathD, {
        roughness: 1.5,
        bowing: 1.0,
        seed: 42,
        stroke: darkBg ? "rgba(100,130,160,0.4)" : "#6B5B3E",
        strokeWidth: 2,
        fill: "none",
      })
      coastNode.style.pointerEvents = "none"
      ;(coastG.node() as Element).appendChild(coastNode)
    }, [mapReady, layout, canvasW, canvasH, darkBg])

    // ── Render regions (text-only labels, no polygon boundaries) ───
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const regionsG = svg.select("#regions")
      const labelsG = svg.select("#region-labels")
      regionsG.selectAll("*").remove()
      labelsG.selectAll("*").remove()

      // Access <defs> for arc path definitions; clean up old arcs
      const defs = svg.select("defs")
      defs.selectAll("path[id^='region-arc-']").remove()

      if (!regionBoundaries || regionBoundaries.length === 0) return

      for (const rb of regionBoundaries) {
        const [cx, cy] = rb.center

        // 1. Compute horizontal span from polygon
        let minX = Infinity, maxX = -Infinity
        for (const [px] of rb.polygon) {
          if (px < minX) minX = px
          if (px > maxX) maxX = px
        }
        const span = maxX - minX

        // 2. Arc sizing — wide enough for text with generous spacing
        const fontSize = 26
        const letterSpacing = 14 // wider spacing for map feel
        const nameLen = rb.region_name.length
        const charWidth = fontSize + letterSpacing
        const textWidth = nameLen * charWidth
        const arcWidth = Math.max(textWidth * 1.5, Math.min(span * 0.75, 500))
        const halfArc = arcWidth / 2

        // 3. Bend direction: top half bends down, bottom half bends up
        //    15% sagitta for clearly visible curvature
        const bendDown = cy < canvasH / 2
        const sagitta = arcWidth * 0.15 * (bendDown ? 1 : -1)

        // 4. Quadratic Bezier arc path
        const pathId = `region-arc-${hashString(rb.region_name)}`
        const startX = cx - halfArc
        const endX = cx + halfArc
        const controlY = cy + sagitta

        defs.append("path")
          .attr("id", pathId)
          .attr("d", `M${startX},${cy} Q${cx},${controlY} ${endX},${cy}`)

        // 5. Text along arc
        const text = labelsG
          .append("text")
          .attr("fill", darkBg ? "#ffffff" : "#8b7355")
          .attr("opacity", darkBg ? 0.55 : 0.45)
          .attr("font-size", `${fontSize}px`)
          .attr("font-weight", "300")
          .attr("letter-spacing", `${letterSpacing}px`)
          .attr("filter", "url(#hand-drawn)")
          .style("pointer-events", "none")

        text
          .append("textPath")
          .attr("startOffset", "50%")
          .attr("text-anchor", "middle")
          .text(rb.region_name)
          .each(function () {
            this.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", `#${pathId}`)
            this.setAttribute("href", `#${pathId}`)
          })
      }
    }, [mapReady, regionBoundaries, canvasW, canvasH, darkBg])

    // ── Render territories (rough.js hand-drawn hulls) ──────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const terrG = svg.select("#territories")
      const terrLabelsG = svg.select("#territory-labels")
      terrG.selectAll("*").remove()
      terrLabelsG.selectAll("*").remove()

      // Access <defs> for arc paths; clean up old territory arcs
      const defs = svg.select("defs")
      defs.selectAll("path[id^='terr-arc-']").remove()

      if (territories.length === 0) return

      const rc = roughCanvasRef.current
      const isDense = territories.length > 15

      // Per-level rendering parameters
      const STROKE_WIDTH = [3.0, 2.2, 1.5, 1.0]
      const FILL_OP = darkBg
        ? [0.22, 0.15, 0.10, 0.06]
        : [0.15, 0.10, 0.07, 0.04]
      const LABEL_SIZE = [16, 13, 11, 10]
      const LABEL_OP = isDense
        ? [0.20, 0.12, 0.08, 0.06]
        : [0.35, 0.25, 0.20, 0.15]
      const LABEL_SPACING = ["3px", "1px", "0", "0"]

      const clamp = (level: number) => Math.min(level, 3)

      for (const terr of territories) {
        const li = clamp(terr.level)
        const pathData = polygonToPath(terr.polygon)

        const strokeColor = darkBg ? terr.color : "#8b7355"
        const fillColor = darkBg ? terr.color : "#c4a97d"

        if (rc) {
          // Rough.js hand-drawn territory
          const node = rc.path(pathData, {
            roughness: 1.2,
            bowing: 1.0,
            seed: hashString(terr.name) % 100,
            stroke: strokeColor,
            strokeWidth: STROKE_WIDTH[li],
            fill: fillColor,
            fillStyle: "hachure",
            fillWeight: 0.6,
            hachureAngle: -41 + li * 30,
            hachureGap: 6 + li * 2,
          })
          // rough.js hachure is inherently sparse — boost opacity
          node.style.opacity = String(FILL_OP[li] * 3)
          ;(terrG.node() as Element).appendChild(node)
        } else {
          // Fallback: plain path (no rough.js)
          terrG
            .append("path")
            .attr("d", pathData)
            .attr("fill", fillColor)
            .attr("fill-opacity", FILL_OP[li])
            .attr("stroke", strokeColor)
            .attr("stroke-width", STROKE_WIDTH[li])
            .attr("stroke-linejoin", "round")
        }

        // Label at centroid — curved arc for level 0-1, flat for deeper levels
        const centroid = polygonCentroid(terr.polygon)
        const [tcx, tcy] = centroid

        if (li <= 1 && terr.name.length >= 2) {
          // Compute territory horizontal span
          let tMinX = Infinity, tMaxX = -Infinity
          for (const [px] of terr.polygon) {
            if (px < tMinX) tMinX = px
            if (px > tMaxX) tMaxX = px
          }
          const tSpan = tMaxX - tMinX

          const tFontSize = LABEL_SIZE[li]
          const tLetterSpacing = li === 0 ? 10 : 4
          const tCharWidth = tFontSize + tLetterSpacing
          const tTextWidth = terr.name.length * tCharWidth
          const tArcWidth = Math.max(tTextWidth * 1.5, Math.min(tSpan * 0.7, 400))
          const tHalfArc = tArcWidth / 2

          const tBendDown = tcy < canvasH / 2
          const tSagitta = tArcWidth * 0.12 * (tBendDown ? 1 : -1)

          const tPathId = `terr-arc-${hashString(terr.name)}`
          const tStartX = tcx - tHalfArc
          const tEndX = tcx + tHalfArc
          const tControlY = tcy + tSagitta

          defs.append("path")
            .attr("id", tPathId)
            .attr("d", `M${tStartX},${tcy} Q${tcx},${tControlY} ${tEndX},${tcy}`)

          const tText = terrLabelsG
            .append("text")
            .attr("fill", darkBg ? terr.color : "#6b5c4a")
            .attr("opacity", LABEL_OP[li])
            .attr("font-size", `${tFontSize}px`)
            .attr("font-weight", "300")
            .attr("letter-spacing", `${tLetterSpacing}px`)
            .attr("filter", "url(#hand-drawn)")
            .style("pointer-events", "none")

          tText
            .append("textPath")
            .attr("startOffset", "50%")
            .attr("text-anchor", "middle")
            .text(terr.name)
            .each(function () {
              this.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", `#${tPathId}`)
              this.setAttribute("href", `#${tPathId}`)
            })
        } else {
          terrLabelsG
            .append("text")
            .attr("x", tcx)
            .attr("y", tcy)
            .attr("text-anchor", "middle")
            .attr("dominant-baseline", "central")
            .attr("fill", darkBg ? terr.color : "#6b5c4a")
            .attr("opacity", LABEL_OP[li])
            .attr("font-size", `${LABEL_SIZE[li]}px`)
            .attr("font-weight", "300")
            .attr("letter-spacing", LABEL_SPACING[li])
            .attr("filter", "url(#hand-drawn)")
            .style("pointer-events", "none")
            .text(terr.name)
        }
      }
    }, [mapReady, territories, canvasW, canvasH, darkBg])

    // ── Render trajectory (progressive drawing + pulse marker) ──
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const trajG = svg.select("#trajectory")
      trajG.selectAll("*").remove()

      // Use full trajectory for background, visible slice for foreground
      const allPts = allTrajectoryPoints ?? trajectoryPoints
      if (!allPts || allPts.length === 0) return

      // Resolve all trajectory point coordinates
      const allCoords: Point[] = []
      const allChapters: number[] = []
      for (const pt of allPts) {
        const item = layoutMap.get(pt.location)
        if (item) {
          allCoords.push([item.x, item.y])
          allChapters.push(pt.chapter)
        }
      }

      // Resolve visible trajectory point coordinates
      const visPts = trajectoryPoints ?? []
      const visCoords: Point[] = []
      for (const pt of visPts) {
        const item = layoutMap.get(pt.location)
        if (item) visCoords.push([item.x, item.y])
      }

      if (allCoords.length < 2) return

      const lineGen = d3Shape
        .line<Point>()
        .x((d) => d[0])
        .y((d) => d[1])
        .curve(d3Shape.curveCardinal.tension(0.5))

      // Background path: full trajectory, dashed, low opacity
      trajG
        .append("path")
        .attr("class", "traj-bg")
        .attr("d", lineGen(allCoords)!)
        .attr("fill", "none")
        .attr("stroke", "#f59e0b")
        .attr("stroke-width", 3)
        .attr("stroke-opacity", 0.2)
        .attr("stroke-dasharray", "8,6")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")

      // Foreground path: visible trajectory, solid, high opacity
      if (visCoords.length >= 2) {
        trajG
          .append("path")
          .attr("class", "traj-fg")
          .attr("d", lineGen(visCoords)!)
          .attr("fill", "none")
          .attr("stroke", "#f59e0b")
          .attr("stroke-width", 3)
          .attr("stroke-opacity", 0.85)
          .attr("stroke-linecap", "round")
          .attr("stroke-linejoin", "round")
      }

      // Draw waypoint circles + chapter labels
      // Track which locations have been labeled to avoid duplicate labels at same location
      const labeledLocs = new Set<string>()
      for (let i = 0; i < allCoords.length; i++) {
        const coord = allCoords[i]
        const pt = allPts[i]
        const isVisible = i < visPts.length
        const isCurrent = isPlaying && i === (currentPlayIndex ?? 0)
        const stay = stayDurations?.get(pt.location) ?? 0
        const baseR = Math.min(4 + stay * 1.5, 12)

        // Waypoint circle
        trajG
          .append("circle")
          .attr("class", "traj-dot")
          .attr("cx", coord[0])
          .attr("cy", coord[1])
          .attr("r", baseR)
          .attr("data-base-r", baseR)
          .attr("fill", isVisible ? "#d97706" : "#f59e0b")
          .attr("fill-opacity", isVisible ? 1 : 0.25)
          .attr("stroke", isVisible ? "#fff" : "#f59e0b")
          .attr("stroke-width", 1.5)
          .attr("stroke-opacity", isVisible ? 1 : 0.3)
          .append("title")
          .text(stay > 1 ? `${pt.location} — 停留 ${stay} 章` : pt.location)

        // Chapter label (only first occurrence at each location)
        if (!labeledLocs.has(pt.location)) {
          labeledLocs.add(pt.location)
          trajG
            .append("text")
            .attr("class", "traj-label")
            .attr("x", coord[0])
            .attr("y", coord[1] - baseR - 3)
            .attr("text-anchor", "middle")
            .attr("font-size", 9)
            .attr("fill", darkBg ? "#fbbf24" : "#92400e")
            .attr("fill-opacity", isVisible ? 0.7 : 0.2)
            .style("pointer-events", "none")
            .text(`Ch.${allChapters[i]}`)
        }

        // Pulse marker at current playback position
        if (isCurrent) {
          // Inner glow circle
          trajG
            .append("circle")
            .attr("class", "traj-pulse-inner")
            .attr("cx", coord[0])
            .attr("cy", coord[1])
            .attr("r", 6)
            .attr("data-base-r", 6)
            .attr("fill", "#f59e0b")
            .attr("stroke", "#fff")
            .attr("stroke-width", 2)

          // Outer pulsing ring
          const pulseOuter = trajG
            .append("circle")
            .attr("class", "traj-pulse-outer")
            .attr("cx", coord[0])
            .attr("cy", coord[1])
            .attr("r", 14)
            .attr("data-base-r", 14)
            .attr("fill", "none")
            .attr("stroke", "#f59e0b")
            .attr("stroke-width", 2)

          // SVG animate for radius pulse
          pulseOuter
            .append("animate")
            .attr("attributeName", "r")
            .attr("values", "10;18;10")
            .attr("dur", "1.5s")
            .attr("repeatCount", "indefinite")

          // SVG animate for opacity pulse
          pulseOuter
            .append("animate")
            .attr("attributeName", "opacity")
            .attr("values", "0.6;0.1;0.6")
            .attr("dur", "1.5s")
            .attr("repeatCount", "indefinite")
        }
      }
    }, [mapReady, trajectoryPoints, allTrajectoryPoints, layoutMap, darkBg, stayDurations, isPlaying, currentPlayIndex])

    // ── Auto-pan to follow playback ────────────────────
    useEffect(() => {
      if (!isPlaying || !svgRef.current || !zoomRef.current) return
      if (!trajectoryPoints || trajectoryPoints.length === 0) return
      const idx = currentPlayIndex ?? 0
      if (idx >= trajectoryPoints.length) return

      const pt = trajectoryPoints[idx]
      const item = layoutMap.get(pt.location)
      if (!item) return

      const svgNode = svgRef.current
      const svgW = svgNode.clientWidth || 800
      const svgH = svgNode.clientHeight || 600
      const t = transformRef.current

      // Current screen position of the trajectory point
      const screenX = item.x * t.k + t.x
      const screenY = item.y * t.k + t.y

      // If the point is within 20% of viewport edge, pan to center it
      const marginX = svgW * 0.2
      const marginY = svgH * 0.2
      if (
        screenX < marginX || screenX > svgW - marginX ||
        screenY < marginY || screenY > svgH - marginY
      ) {
        const svg = d3Selection.select(svgNode)
        svg
          .transition()
          .duration(300)
          .call(
            zoomRef.current.transform,
            d3Zoom.zoomIdentity
              .translate(svgW / 2 - item.x * t.k, svgH / 2 - item.y * t.k)
              .scale(t.k),
          )
      }
    }, [isPlaying, currentPlayIndex, trajectoryPoints, layoutMap])

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

        // Transparent hit-area circle for reliable click/hover detection
        locG
          .append("circle")
          .attr("class", "loc-hitarea")
          .attr("cx", item.x)
          .attr("cy", item.y)
          .attr("r", Math.max(iconSize / 2 + 6, 14))
          .attr("fill", "transparent")

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

        // Click handler (single-click → entity card)
        locG.on("click", (event: MouseEvent) => {
          event.stopPropagation()
          onClickRef.current?.(item.name)
        })

        // Double-click → toggle expand/collapse children
        locG.on("dblclick", (event: MouseEvent) => {
          event.stopPropagation()
          event.preventDefault()
          onToggleExpandRef.current?.(item.name)
        })

        // Collapsed-children badge ("+N")
        const childN = collapsedChildCount?.get(item.name)
        if (childN && childN > 0) {
          const badgeR = 7
          const bx = item.x + iconSize / 2 + 2
          const by = item.y - iconSize / 2 - 2
          locG
            .append("circle")
            .attr("class", "collapse-badge")
            .attr("cx", bx)
            .attr("cy", by)
            .attr("r", badgeR)
            .attr("fill", "#3b82f6")
            .attr("stroke", darkBg ? "#1e293b" : "#ffffff")
            .attr("stroke-width", 1.5)
            .style("cursor", "pointer")
          locG
            .append("text")
            .attr("class", "collapse-badge-text")
            .attr("x", bx)
            .attr("y", by + 3.5)
            .attr("text-anchor", "middle")
            .attr("font-size", "8px")
            .attr("font-weight", 700)
            .attr("fill", "#ffffff")
            .style("pointer-events", "none")
            .text(`+${childN}`)
        }
      }

      // Setup drag on location groups
      setupDrag(svg)
    }, [
      mapReady, layout, locMap, locations, iconDefs,
      visibleLocationNames, revealedLocationNames, currentLocation, darkBg,
      collapsedChildCount,
    ])

    // ── Setup drag behavior ──────────────────────────
    const setupDrag = useCallback(
      (svg: d3Selection.Selection<SVGSVGElement, unknown, null, undefined>) => {
        const locationItems = svg.selectAll<SVGGElement, unknown>(".location-item")

        let hasDragged = false

        const drag = d3Drag
          .drag<SVGGElement, unknown>()
          .clickDistance(5)
          .on("start", function (event: d3Drag.D3DragEvent<SVGGElement, unknown, unknown>) {
            // Prevent zoom during drag
            event.sourceEvent.stopPropagation()
            hasDragged = false
            d3Selection.select(this).raise().style("cursor", "grabbing")
          })
          .on("drag", function (event: d3Drag.D3DragEvent<SVGGElement, unknown, unknown>) {
            hasDragged = true
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

            // Update hit-area circle position
            g.select(".loc-hitarea")
              .attr("cx", canvasX)
              .attr("cy", canvasY)
          })
          .on("end", function (event: d3Drag.D3DragEvent<SVGGElement, unknown, unknown>) {
            d3Selection.select(this).style("cursor", "pointer")
            if (!hasDragged) return // Click, not drag — let the click handler fire

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
          .style("opacity", tierOpacity)
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

      // Trajectory counter-scale: keep stroke-width, circle radius, and labels constant
      svg.selectAll<SVGPathElement, unknown>(".traj-bg, .traj-fg")
        .attr("stroke-width", 3 / k)
      svg.selectAll<SVGCircleElement, unknown>(".traj-dot, .traj-pulse-inner, .traj-pulse-outer")
        .each(function () {
          const el = d3Selection.select(this)
          const baseR = parseFloat(el.attr("data-base-r") ?? "5")
          el.attr("r", baseR / k)
            .attr("stroke-width", (el.classed("traj-pulse-inner") ? 2 : 1.5) / k)
        })
      svg.selectAll<SVGTextElement, unknown>(".traj-label")
        .attr("font-size", 9 / k)

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

        // Estimate label dimensions in screen pixels
        const labelW = name.length * fontSize + 4
        const labelH = fontSize + 4
        const iconScreenX = x * k
        const iconScreenY = y * k

        // Default position (bottom) for the initial rect — computeLabelLayout will try all anchors
        const defaultDy = iconSize / 2 + fontSize * 0.9
        labelRects.push({
          x: iconScreenX - labelW / 2,
          y: iconScreenY + defaultDy - labelH / 2,
          w: labelW,
          h: labelH,
          name,
          priority: tierW * 1000 + mention,
          iconScreenX,
          iconScreenY,
          labelW,
          labelH,
          iconSize,
          fontSize,
        })
      })

      const labelLayout = computeLabelLayout(labelRects)

      // Apply label placement (position + text-anchor + visibility)
      svg.selectAll<SVGGElement, unknown>(".location-item").each(function () {
        const g = d3Selection.select(this)
        const name = g.attr("data-name") ?? ""
        const label = g.select(".loc-label")
        const placement = labelLayout.get(name)
        if (placement) {
          const x = parseFloat(g.attr("data-x"))
          const y = parseFloat(g.attr("data-y"))
          // Convert screen-space offset to world-space (labels are counter-scaled by 1/k)
          label
            .attr("x", x + placement.offsetX / k)
            .attr("y", y + placement.offsetY / k)
            .attr("text-anchor", placement.textAnchor)
            .style("display", "")
        } else {
          label.style("display", "none")
        }
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

    useImperativeHandle(ref, () => ({
      fitToLocations,
      getSvgElement: () => svgRef.current,
    }), [fitToLocations])

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

        {/* Vignette is now rendered as SVG overlay (#vignette-overlay) for both light and dark modes */}

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

/** Deterministic pseudo-random [0,1) from integer seed. */
function pseudoRandom(seed: number): number {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453
  return x - Math.floor(x)
}

// ── Layer Atmosphere Rendering ─────────────────────────

type D3Group = d3Selection.Selection<SVGGElement, unknown, null, undefined>
type D3Defs = d3Selection.Selection<SVGDefsElement, unknown, null, undefined>

/**
 * Render layer-specific atmospheric SVG textures for dark background layers.
 * Called from the SVG init useEffect when darkBg is true.
 */
function renderLayerAtmosphere(
  viewport: D3Group,
  defs: D3Defs,
  effectiveLayerType: string,
  w: number,
  h: number,
): void {
  const atmoG = viewport.append("g")
    .attr("id", "layer-atmosphere")
    .style("pointer-events", "none")

  switch (effectiveLayerType) {
    case "sky":
      renderSkyAtmosphere(atmoG, defs, w, h)
      break
    case "underground":
      renderUndergroundAtmosphere(atmoG, defs, w, h)
      break
    case "sea":
      renderSeaAtmosphere(atmoG, defs, w, h)
      break
    case "pocket":
      renderPocketAtmosphere(atmoG, defs, w, h)
      break
    case "spirit":
      renderSpiritAtmosphere(atmoG, defs, w, h)
      break
    default:
      // hierarchy mode fallback — use underground theme
      renderUndergroundAtmosphere(atmoG, defs, w, h)
      break
  }
}

/** Sky (天界) — starfield + nebula glow */
function renderSkyAtmosphere(
  g: D3Group, defs: D3Defs, w: number, h: number,
): void {
  // Deep blue radial gradient background
  const grad = defs.append("radialGradient")
    .attr("id", "sky-bg-grad")
    .attr("cx", "50%").attr("cy", "50%").attr("r", "70%")
  grad.append("stop").attr("offset", "0%").attr("stop-color", "#0f1f3a")
  grad.append("stop").attr("offset", "100%").attr("stop-color", "#060d1a")

  g.append("rect")
    .attr("width", w).attr("height", h)
    .attr("fill", "url(#sky-bg-grad)")
    .attr("opacity", 0.6)

  // Small stars (~150)
  for (let i = 0; i < 150; i++) {
    const sx = pseudoRandom(i * 3 + 1) * w
    const sy = pseudoRandom(i * 3 + 2) * h
    const sr = 0.5 + pseudoRandom(i * 3 + 3) * 0.5
    const so = 0.3 + pseudoRandom(i * 3 + 4) * 0.3
    g.append("circle")
      .attr("cx", sx).attr("cy", sy)
      .attr("r", sr).attr("fill", "#ffffff").attr("opacity", so)
  }

  // Bright stars (~20) with optional cross flare
  for (let i = 0; i < 20; i++) {
    const bx = pseudoRandom(i * 5 + 500) * w
    const by = pseudoRandom(i * 5 + 501) * h
    const br = 1.5 + pseudoRandom(i * 5 + 502)
    const bo = 0.7 + pseudoRandom(i * 5 + 503) * 0.2
    g.append("circle")
      .attr("cx", bx).attr("cy", by)
      .attr("r", br).attr("fill", "#ffffff").attr("opacity", bo)

    // Cross flare on ~30% of bright stars
    if (pseudoRandom(i * 5 + 504) < 0.3) {
      const fl = br * 3
      g.append("path")
        .attr("d", `M${bx - fl},${by} L${bx + fl},${by} M${bx},${by - fl} L${bx},${by + fl}`)
        .attr("stroke", "#ffffff").attr("stroke-width", 0.5)
        .attr("opacity", bo * 0.5)
    }
  }

  // Nebula glow — 2 large faint circles
  const nebulaPositions = [
    { cx: w * 0.3, cy: h * 0.4, r: 180, o: 0.04 },
    { cx: w * 0.7, cy: h * 0.6, r: 150, o: 0.05 },
  ]
  for (const nb of nebulaPositions) {
    g.append("circle")
      .attr("cx", nb.cx).attr("cy", nb.cy)
      .attr("r", nb.r).attr("fill", "#1e3a5f").attr("opacity", nb.o)
  }
}

/** Underground (冥界/地下) — rock texture + dark mist + cracks */
function renderUndergroundAtmosphere(
  g: D3Group, defs: D3Defs, w: number, h: number,
): void {
  // Rock texture via feTurbulence
  const rockFilter = defs.append("filter").attr("id", "rock-noise")
  rockFilter.append("feTurbulence")
    .attr("type", "fractalNoise")
    .attr("baseFrequency", "0.04")
    .attr("numOctaves", "3")
    .attr("stitchTiles", "stitch")
  rockFilter.append("feColorMatrix")
    .attr("type", "saturate").attr("values", "0")
  rockFilter.append("feBlend")
    .attr("in", "SourceGraphic").attr("mode", "multiply")

  g.append("rect")
    .attr("width", w).attr("height", h)
    .attr("fill", "#2a1a3e")
    .attr("filter", "url(#rock-noise)")
    .attr("opacity", 0.12)

  // Purple mist radial gradient (dark edges)
  const mistGrad = defs.append("radialGradient")
    .attr("id", "underground-mist")
    .attr("cx", "50%").attr("cy", "50%").attr("r", "60%")
  mistGrad.append("stop").attr("offset", "0%").attr("stop-color", "transparent")
  mistGrad.append("stop").attr("offset", "100%").attr("stop-color", "rgba(30,10,50,0.3)")

  g.append("rect")
    .attr("width", w).attr("height", h)
    .attr("fill", "url(#underground-mist)")

  // Random cracks — 6 short lines
  for (let i = 0; i < 6; i++) {
    const x1 = pseudoRandom(i * 4 + 700) * w
    const y1 = pseudoRandom(i * 4 + 701) * h
    const x2 = x1 + (pseudoRandom(i * 4 + 702) - 0.5) * 80
    const y2 = y1 + (pseudoRandom(i * 4 + 703) - 0.5) * 80
    g.append("line")
      .attr("x1", x1).attr("y1", y1)
      .attr("x2", x2).attr("y2", y2)
      .attr("stroke", "#3a2050").attr("stroke-width", 1)
      .attr("opacity", 0.15)
  }
}

/** Sea (海底) — deep blue gradient + wave lines + bubbles */
function renderSeaAtmosphere(
  g: D3Group, defs: D3Defs, w: number, h: number,
): void {
  // Top-to-bottom deep blue gradient
  const seaGrad = defs.append("linearGradient")
    .attr("id", "sea-grad")
    .attr("x1", "0%").attr("y1", "0%")
    .attr("x2", "0%").attr("y2", "100%")
  seaGrad.append("stop").attr("offset", "0%").attr("stop-color", "#0a2540")
  seaGrad.append("stop").attr("offset", "100%").attr("stop-color", "#061a30")

  g.append("rect")
    .attr("width", w).attr("height", h)
    .attr("fill", "url(#sea-grad)")
    .attr("opacity", 0.5)

  // Horizontal wave lines — 4 wavy paths using quadratic curves
  for (let i = 0; i < 4; i++) {
    const baseY = h * (0.15 + i * 0.22)
    const amp = 8 + pseudoRandom(i + 800) * 6
    const segments = 8
    const segW = w / segments
    let d = `M0,${baseY}`
    for (let s = 0; s < segments; s++) {
      const cx = s * segW + segW / 2
      const cy = baseY + (s % 2 === 0 ? -amp : amp)
      const ex = (s + 1) * segW
      d += ` Q${cx},${cy} ${ex},${baseY}`
    }
    g.append("path")
      .attr("d", d)
      .attr("fill", "none")
      .attr("stroke", "#1a4a6a")
      .attr("stroke-width", 1.5)
      .attr("opacity", 0.15)
  }

  // Bubble scatter — 35 small circles
  for (let i = 0; i < 35; i++) {
    const bx = pseudoRandom(i * 3 + 900) * w
    const by = pseudoRandom(i * 3 + 901) * h
    const br = 2 + pseudoRandom(i * 3 + 902) * 4
    const bo = 0.08 + pseudoRandom(i * 3 + 903) * 0.07
    g.append("circle")
      .attr("cx", bx).attr("cy", by)
      .attr("r", br)
      .attr("fill", "none")
      .attr("stroke", "#1a5a7a")
      .attr("stroke-width", 0.8)
      .attr("opacity", bo)
  }
}

/** Pocket (副本/洞府) — dark brown texture + vortex hint + light spots */
function renderPocketAtmosphere(
  g: D3Group, defs: D3Defs, w: number, h: number,
): void {
  // Brown noise texture
  const pocketFilter = defs.append("filter").attr("id", "pocket-noise")
  pocketFilter.append("feTurbulence")
    .attr("type", "fractalNoise")
    .attr("baseFrequency", "0.03")
    .attr("numOctaves", "3")
    .attr("stitchTiles", "stitch")
  pocketFilter.append("feColorMatrix")
    .attr("type", "saturate").attr("values", "0")
  pocketFilter.append("feBlend")
    .attr("in", "SourceGraphic").attr("mode", "multiply")

  g.append("rect")
    .attr("width", w).attr("height", h)
    .attr("fill", "#2a1f15")
    .attr("filter", "url(#pocket-noise)")
    .attr("opacity", 0.10)

  // Central vortex hint — radial gradient
  const vortexGrad = defs.append("radialGradient")
    .attr("id", "pocket-vortex")
    .attr("cx", "50%").attr("cy", "50%").attr("r", "45%")
  vortexGrad.append("stop").attr("offset", "0%").attr("stop-color", "#2a1f15")
  vortexGrad.append("stop").attr("offset", "100%").attr("stop-color", "transparent")

  g.append("rect")
    .attr("width", w).attr("height", h)
    .attr("fill", "url(#pocket-vortex)")
    .attr("opacity", 0.15)

  // Scattered light spots — 10 small circles
  for (let i = 0; i < 10; i++) {
    const sx = pseudoRandom(i * 3 + 1100) * w
    const sy = pseudoRandom(i * 3 + 1101) * h
    const sr = 3 + pseudoRandom(i * 3 + 1102) * 5
    const so = 0.06 + pseudoRandom(i * 3 + 1103) * 0.04
    g.append("circle")
      .attr("cx", sx).attr("cy", sy)
      .attr("r", sr)
      .attr("fill", "#5a4030")
      .attr("opacity", so)
  }
}

/** Spirit (灵界) — purple mist texture + glow orbs + soul flames */
function renderSpiritAtmosphere(
  g: D3Group, defs: D3Defs, w: number, h: number,
): void {
  // Purple mist turbulence
  const spiritFilter = defs.append("filter").attr("id", "spirit-noise")
  spiritFilter.append("feTurbulence")
    .attr("type", "fractalNoise")
    .attr("baseFrequency", "0.02")
    .attr("numOctaves", "2")
    .attr("stitchTiles", "stitch")
  spiritFilter.append("feColorMatrix")
    .attr("type", "saturate").attr("values", "0")
  spiritFilter.append("feBlend")
    .attr("in", "SourceGraphic").attr("mode", "multiply")

  g.append("rect")
    .attr("width", w).attr("height", h)
    .attr("fill", "#2a1040")
    .attr("filter", "url(#spirit-noise)")
    .attr("opacity", 0.10)

  // Purple radial glow orbs — 3
  const glowPositions = [
    { cx: w * 0.25, cy: h * 0.35, r: 160, o: 0.08 },
    { cx: w * 0.65, cy: h * 0.55, r: 120, o: 0.06 },
    { cx: w * 0.5, cy: h * 0.8, r: 140, o: 0.07 },
  ]
  for (const gl of glowPositions) {
    g.append("circle")
      .attr("cx", gl.cx).attr("cy", gl.cy)
      .attr("r", gl.r)
      .attr("fill", "#2a1040")
      .attr("opacity", gl.o)
  }

  // Soul flame scatter — 12 small ellipses
  for (let i = 0; i < 12; i++) {
    const fx = pseudoRandom(i * 3 + 1300) * w
    const fy = pseudoRandom(i * 3 + 1301) * h
    const rx = 2 + pseudoRandom(i * 3 + 1302) * 3
    const ry = rx * (1.3 + pseudoRandom(i * 3 + 1303) * 0.4)
    const fo = 0.06 + pseudoRandom(i * 3 + 1304) * 0.06
    g.append("ellipse")
      .attr("cx", fx).attr("cy", fy)
      .attr("rx", rx).attr("ry", ry)
      .attr("fill", "#7c3aed")
      .attr("opacity", fo)
  }
}
