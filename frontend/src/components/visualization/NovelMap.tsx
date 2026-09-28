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
  Landmass,
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
import { SPACE_THEME, getSpaceNodeColor, getSpaceGlowRadius, generateStarfield } from "@/lib/mapRenderer/spaceTheme"
import { generateTerrainHints, type TerrainHint } from "@/lib/terrainHints"
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
// Lowered ~4×. The previous thresholds assumed the viewport would be zoomed
// well past the fit zoom, but `fitToLocations` pins k ≈ 0.19 on 西游记, so a
// city needed k ≥ 1.2 and never appeared — the overview showed a dozen marks
// while the mention-count filter had already narrowed the map to 64 places.
// Now the whole continent→city band is visible at fit zoom, and site /
// building still fade in only once you zoom (the intended macro→detail ramp).
const TIER_MIN_SCALE: Record<string, number> = {
  continent: 0.05,
  kingdom: 0.07,
  region: 0.1,
  city: 0.14,
  site: 0.35,
  building: 0.8,
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

// Label size per tier. The previous ladder (26/20/14/11/9/8) put city, site and
// building all in the 8–11px band — on a 1920-wide canvas that is unreadable, and
// because the sizes are static it stayed unreadable at every zoom level.
// Measured on 西游记: 71 of 125 rendered labels were 8px, i.e. 56% of the map's
// text was illegible. The floor is now 12px and the ladder is compressed so the
// tier hierarchy survives without dropping below the legibility threshold.
// Label collision detection (computeLabelLayout) reads these sizes, so it adapts.
const TIER_TEXT_SIZE: Record<string, number> = {
  continent: 26,
  kingdom: 21,
  region: 17,
  city: 14,
  site: 13,
  building: 12,
}

// Icon frame size. The rendered mark is *half* of these numbers: the icon
// files declare a 24-unit viewBox and the group is scaled by size / 48.
// Raised ~1.5× so the marks stay above the terrain symbols in the visual
// hierarchy — a city icon used to draw at 9 px while the tree symbols around
// it drew at 15 px, which inverted the reading order (decoration louder than
// place).
const TIER_ICON_SIZE: Record<string, number> = {
  continent: 56,
  kingdom: 44,
  region: 36,
  city: 28,
  site: 22,
  building: 16,
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

function getVisibleTiers(scale: number, scaleDivisor = 1): string {
  const visible = TIERS.filter((t) => scale >= (TIER_MIN_SCALE[t] ?? 99) / scaleDivisor)
  if (visible.length === 0) return ""
  return visible.map((t) => TIER_LABELS[t] ?? t).join("/")
}

/**
 * Terrain ground-cover LOD key.
 *
 * The ground grid is authored in *screen* pixels (see `CELL_PX`), so a zoom
 * step changes the canvas pitch of the grid, and a pan changes which cells fall
 * inside the viewport. Both therefore invalidate the scatter. Rebuilding on
 * every wheel tick would thrash a few hundred DOM nodes, so the transform is
 * quantised to half-octave zoom steps and ~96 px pan steps: a gesture triggers
 * a handful of rebuilds, and between them the cheap counter-scale effect keeps
 * the symbols at a constant on-screen size.
 */
function terrainLodKey(t: d3Zoom.ZoomTransform): string {
  const z = Math.round(Math.log2(Math.max(t.k, 1e-6)) * 2)
  return `${z}:${Math.round(t.x / 96)}:${Math.round(t.y / 96)}`
}

/** SVG namespace, used when the ground layer builds nodes by hand. */
const SVG_NS = "http://www.w3.org/2000/svg"

// ── Type colors ─────────────────────────────────
const CELESTIAL_KW = [
  "天宫", "天庭", "天门", "天界", "三十三天", "大罗天", "离恨天",
  "兜率宫", "凌霄殿", "蟠桃园", "瑶池", "灵霄宝殿", "九天应元府",
]
const UNDERWORLD_KW = [
  "地府", "冥界", "幽冥", "阴司", "阴曹", "黄泉",
  "奈何桥", "阎罗殿", "森罗殿", "枉死城",
]

function locationColor(type: string, name?: string, darkBg = false): string {
  if (name) {
    if (CELESTIAL_KW.some((kw) => name.includes(kw))) return darkBg ? "#fbbf24" : "#b5761a"
    if (UNDERWORLD_KW.some((kw) => name.includes(kw))) return darkBg ? "#a78bfa" : "#5b3f7a"
  }
  const t = type.toLowerCase()
  // Two ramps. On parchment (`darkBg === false`) the icons are inks — dark,
  // desaturated, printed on paper. On the dark layer backgrounds (sky /
  // underground / sea) they are luminous tints of the same hues. The previous
  // saturated Tailwind values (#3b82f6, #10b981, …) read as foreign UI chrome
  // in both, because a map's palette is its own.
  if (t.includes("国") || t.includes("域") || t.includes("界")) return darkBg ? "#7cc4f0" : "#2f5d7c"
  if (t.includes("城") || t.includes("镇") || t.includes("都") || t.includes("村"))
    return darkBg ? "#6fe0b0" : "#3f6b3a"
  if (t.includes("山") || t.includes("洞") || t.includes("谷") || t.includes("林"))
    return darkBg ? "#c3e07a" : "#5c6e2e"
  if (t.includes("宗") || t.includes("派") || t.includes("门")) return darkBg ? "#c4a7f5" : "#5b3f7a"
  if (t.includes("海") || t.includes("河") || t.includes("湖")) return darkBg ? "#5fd6e8" : "#2a6478"
  return darkBg ? "#cbbfa8" : "#5a4a38"
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
  allLocations?: MapLocation[]   // unfiltered, for stable coastline/territories
  allLayout?: MapLayoutItem[]    // unfiltered, for stable coastline/territories
  layoutMode: "constraint" | "hierarchy" | "layered" | "geographic"
  layerType?: string
  terrainUrl: string | null
  visibleLocationNames: Set<string>
  revealedLocationNames?: Set<string>
  regionBoundaries?: RegionBoundary[]
  portals?: PortalInfo[]
  rivers?: { points: number[][]; width: number }[]
  roads?: { from: string; to: string; points: number[][] }[]
  landmasses?: Landmass[]
  shelves?: [number, number][][]
  /** Depth band per shelf contour, parallel to `shelves`. 0 = nearest the shore. */
  shelfDepth?: number[]
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
  spaceTheme?: boolean
  editMode?: boolean
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
      allLocations,
      allLayout,
      layoutMode,
      layerType,
      visibleLocationNames,
      revealedLocationNames,
      regionBoundaries,
      portals,
      rivers,
      roads,
      landmasses,
      shelves,
      shelfDepth,
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
      spaceTheme: spaceThemeProp,
      editMode,
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
    // Quantised viewport descriptor for the ground-cover LOD — see terrainLodKey.
    const [lodKey, setLodKey] = useState("0:0:0")
    // Latest scattered ground hints. Held in a ref rather than in state so the
    // per-tick counter-scale effect can read the current array without making
    // the rebuild effect depend on the zoom.
    const hintsRef = useRef<TerrainHint[]>([])

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
    const darkBg = spaceThemeProp || isDarkBackground(layoutMode, layerType)
    const bgColor = spaceThemeProp ? SPACE_THEME.bg : getMapBgColor(layoutMode, layerType)

    // Scale factor for tier visibility thresholds on large canvases.
    // Default canvas is 1600px; cosmic scale is 8000px. At fit-to-view,
    // the zoom level is proportionally lower, so we divide thresholds by
    // the canvas-to-default ratio to keep labels visible.
    const tierScaleDivisor = Math.max(1, canvasW / DEFAULT_CANVAS.width)

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

    // Territory generation (uses filtered data — territories represent visible groupings)
    const territories = useMemo(
      () => generateHullTerritories(locations, layout, { width: canvasW, height: canvasH }, landmasses),
      [locations, layout, canvasW, canvasH, landmasses],
    )

    // Terrain ground cover is generated inside its own effect rather than in a
    // memo: it has to know the live viewport rect (SVG size × inverse zoom
    // transform), which only the mounted DOM can answer.

    // ── Load SVG icons ──────────────────────────────
    useEffect(() => {
      let cancelled = false
      const defs = new Map<string, string>()

      Promise.all(
        ICON_NAMES.map(async (name) => {
          try {
            const base = import.meta.env.BASE_URL ?? "/"
            const resp = await fetch(`${base}map-icons/${name}.svg`)
            const text = await resp.text()
            // Extract inner SVG content
            const match = text.match(/<svg[^>]*>([\s\S]*)<\/svg>/i)
            if (match) {
              // The shipped icon files hard-code `fill="#fff"` (and a black
              // detail layer) on their shapes. An inline presentation
              // attribute outranks an inherited one, so the renderer's
              // `.attr("fill", color)` never applied and every icon drew pure
              // white — invisible against the light parchment land. Strip the
              // placeholders so the palette drives the colour.
              const inner = match[1]
                .replace(/\sfill="#fff"/gi, "")
                .replace(/\sfill="#000"/gi, "")
                // Outline icons (`fill="none" stroke="#fff" stroke-width="2.5"`,
                // town/water/desert/island/portal/sacred) draw the mark *with
                // its stroke*, in pure white — 2.5 units of it. This is the
                // half of the "63 white icons" the fill strip never touched,
                // and stripping it would be wrong: with no stroke of its own
                // the shape inherits the halo below, which is also light, so it
                // would go from invisible to invisible. Recolour instead — the
                // group sets `color`, and `currentColor` resolves against it.
                .replace(/\sstroke="#fff"/gi, ' stroke="currentColor"')
                .replace(/\sstroke="#000"/gi, ' stroke="currentColor"')
              defs.set(name, inner)
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
      if (spaceThemeProp) {
        // Space theme: render starfield dots on dark background
        const starfieldG = viewport.append("g").attr("id", "starfield").style("pointer-events", "none")
        const stars = generateStarfield(canvasW, canvasH)
        for (const star of stars) {
          starfieldG.append("circle")
            .attr("cx", star.x)
            .attr("cy", star.y)
            .attr("r", star.r)
            .attr("fill", `rgba(255, 255, 255, ${star.alpha})`)
        }
      } else if (!darkBg) {
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

      viewport.append("g").attr("id", "coastline-ocean")
      viewport.append("g").attr("id", "shelf")
      viewport.append("g").attr("id", "coastline")

      // Layer groups (Z-order)
      //
      // Two separate ground groups, and the split matters. `#terrain-biome` is
      // the baked Whittaker PNG: a whole-canvas noise wash with no land/sea
      // information in it, so it has to be land-clipped. `#terrain` is the
      // scattered ground cover, which deliberately *does* include sea waves and
      // therefore must not be clipped. They used to share one group, and
      // clipping that group deleted every wave in the ocean.
      viewport.append("g").attr("id", "terrain-biome")
      viewport.append("g").attr("id", "regions")
      // Ground cover belongs ABOVE the washes. It used to be appended first —
      // before the ocean fill, before #regions — so the region tint (17 % flat
      // fill + a displacement filter) painted straight over the grass and
      // ridges and the whole texture layer read as a faint mottle.
      viewport.append("g").attr("id", "terrain")
      viewport.append("g").attr("id", "region-labels")
      viewport.append("g").attr("id", "territories")
      viewport.append("g").attr("id", "territory-labels")
      // Rivers and roads are drawn ABOVE the region tints. Appended before
      // #regions they sat underneath a translucent fill plus the hand-drawn
      // displacement filter, and the ~2 px road strokes washed out — the map
      // read as bare land with no paths on it at all.
      viewport.append("g").attr("id", "rivers")
      viewport.append("g").attr("id", "roads")
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
          // React bails out when the key is unchanged, so a pure pan does not
          // re-render until it has actually moved the ground grid.
          setLodKey(terrainLodKey(event.transform))
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
    }, [canvasW, canvasH, layoutMode, layerType, bgColor, darkBg, spaceThemeProp])

    // ── Terrain image (Whittaker biome bottom layer) ──────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady || !terrainUrl || spaceThemeProp) return
      const svg = d3Selection.select(svgRef.current)
      const terrainG = svg.select("#terrain-biome")
      // Remove previous terrain image if any (keep hint symbols via class check)
      terrainG.selectAll("image.terrain-img").remove()

      // Opacity depends on whether the bake has structure to carry, not on the
      // bake alone. Measured on 西游记 over 0.40 -> 1.00:
      //
      //   v6 bake: land/sea dE*ab 14.49 -> 13.15, chroma 10.00 -> 8.84. A
      //     stronger wash costs separation and buys nothing.
      //   v7 bake: dE*ab 16.81 -> 23.19, chroma 11.10 -> 11.65, and the 4-8 px
      //     contrast rises 7.49 -> 8.53. Every step is a gain.
      //
      // The difference is not the number, it is what is being amplified. v6's
      // colour came from a biome table over a smooth blob field, so turning it
      // up turned up noise. v7 is one ridged height field with the palette
      // ramped over it, so turning it up turns up landform. 0.85 keeps some
      // parchment showing through; 1.00 is measurably better and visibly
      // heavier, and the choice between them is art direction, not accuracy.
      //
      // Unmeasured: the dark-theme branch. 0.70 extrapolates the old light/dark
      // ratio onto a bake whose own contrast already increased.
      const terrainOpacity = darkBg ? 0.70 : 0.85

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

    // ── Scatter terrain ground cover ─────────────────
    // Runs on the quantised LOD key, not on every zoom tick. The grid lives in
    // *canvas* space — a cell is `CELL_PX / k` wide, so it measures CELL_PX on
    // screen at any zoom — which means a pan does not re-lay the pattern, it
    // only slides the window of cells in view. The LOD key quantises that
    // window to ~96 px steps, and 96 px of pan is exactly six cells at the
    // default pitch, so consecutive keys share most of their cells. Between
    // steps the counter-scale effect below keeps sizes constant.
    //
    // There is deliberately no rAF coalescing here. It was added on the theory
    // that a brisk drag lands several changed keys inside one frame and each
    // one rebuilt the whole scatter. `count_rebuilds.py` then measured a
    // 12-step drag at 7 rebuilds with the coalescing and 7 without: React
    // commits once per frame whatever the input rate, so there was never a
    // burst to coalesce, and the scheduling bought nothing.
    useEffect(() => {
      if (!svgRef.current || !mapReady || spaceThemeProp) return
      const svgEl = svgRef.current
      const svg = d3Selection.select(svgEl)
      const terrainG = svg.select("#terrain")

      terrainG.selectAll("use").remove()

      const t = transformRef.current
      // `transformRef` always holds the live transform (identity before the
      // first gesture), so the rebuild never needs `currentScale` — which is
      // deliberate: depending on it would rebuild the whole scatter on every
      // wheel tick.
      const kz = t.k || 1
      const vbW = svgEl.clientWidth || svgEl.getBoundingClientRect().width || 0
      const vbH = svgEl.clientHeight || svgEl.getBoundingClientRect().height || 0
      // Canvas rect currently on screen = the viewport rect pushed back through
      // translate(tx,ty) scale(k).
      const viewRect =
        vbW > 0 && vbH > 0
          ? { x: -t.x / kz, y: -t.y / kz, w: vbW / kz, h: vbH / kz }
          : null

      const { symbolDefs, hints } = generateTerrainHints(
        allLocations ?? locations,
        allLayout ?? layout,
        { width: canvasW, height: canvasH },
        darkBg,
        kz,
        viewRect,
        landmasses,
      )
      hintsRef.current = hints
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

      // Render <use> elements into #terrain group.
      //
      // Built off-document in a single DocumentFragment and inserted once.
      // Appending ~1 100 nodes one at a time invalidates layout on the whole
      // SVG subtree on every insertion, which measured 47 ms at p95 during a
      // drag; the fragment version lands the whole batch in one go.
      const inv = 1 / kz
      const symById = new Map(symbolDefs.map((d) => [d.id, d]))
      const frag = document.createDocumentFragment()
      for (const hint of hints) {
        const def = symById.get(hint.symbolId)
        const sz = hint.size
        // Placed and counter-scaled in one go, so a freshly built batch is
        // already the right on-screen size even before the next zoom tick.
        const useEl = document.createElementNS(SVG_NS, "use")
        useEl.setAttribute("href", `#${hint.symbolId}`)
        useEl.setAttribute("x", "0")
        useEl.setAttribute("y", "0")
        useEl.setAttribute("width", String(sz))
        useEl.setAttribute("height", String(sz))
        useEl.setAttribute("opacity", String(hint.opacity))
        useEl.setAttribute(
          "transform",
          `translate(${hint.x},${hint.y}) scale(${inv})` +
            ` rotate(${hint.rotation}) translate(${-sz / 2},${-sz / 2})`,
        )
        useEl.style.pointerEvents = "none"

        if (def?.strokeOnly) {
          useEl.setAttribute("fill", "none")
          useEl.setAttribute("stroke", hint.color)
          // Interpreted on screen because the group is counter-scaled.
          useEl.setAttribute("stroke-width", "1.1")
        } else {
          useEl.setAttribute("fill", hint.color)
        }
        frag.appendChild(useEl)
      }
      const terrainNode = terrainG.node() as Element | null
      if (terrainNode) terrainNode.appendChild(frag)
    }, [
      mapReady,
      lodKey,
      allLocations,
      locations,
      allLayout,
      layout,
      canvasW,
      canvasH,
      darkBg,
      landmasses,
      spaceThemeProp,
    ])

    // ── Counter-scale ground symbols on zoom ─────────────
    // `hint.size` is authored in *screen* pixels, but without compensation it
    // gets multiplied by the zoom factor: at the fit zoom (k ≈ 0.19) every
    // symbol collapses to 1.5–4.9 px and the ground reads as empty paper.
    // Holding each symbol at a constant screen size is what turns a flat wash
    // into textured ground.
    // Separate from the scatter effect so a zoom gesture only rewrites one
    // attribute per node instead of rebuilding the batch.
    useEffect(() => {
      if (!svgRef.current || !mapReady || spaceThemeProp) return
      const hints = hintsRef.current
      if (hints.length === 0) return
      const inv = 1 / (currentScale || 1)
      d3Selection
        .select(svgRef.current)
        .select("#terrain")
        .selectAll<SVGUseElement, unknown>("use")
        .attr("transform", (_d, i) => {
          // DOM order matches `hints` order (appended in the same loop).
          const h = hints[i]
          if (!h) return null
          const sz = h.size
          return (
            `translate(${h.x},${h.y}) scale(${inv})` +
            ` rotate(${h.rotation}) translate(${-sz / 2},${-sz / 2})`
          )
        })
    }, [mapReady, currentScale, spaceThemeProp])

    // ── Render rivers (rough.js hand-drawn) ──────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady || spaceThemeProp) return
      const svg = d3Selection.select(svgRef.current)
      const riversG = svg.select("#rivers")
      riversG.selectAll("*").remove()

      if (!rivers || rivers.length === 0) return
      const rc = roughCanvasRef.current
      if (!rc) return

      const riverColor = darkBg
        ? "rgba(126,184,216,0.75)"
        : "rgba(74,118,156,0.78)"

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
          // Interpreted as screen pixels — see the non-scaling pass below.
          strokeWidth: Math.max(2, river.width * 1.5),
          fill: "none",
        })
        node.style.pointerEvents = "none"
        // rough.js emits a <g> wrapping several <path>s, so the attribute has
        // to go on the children. Without it the stroke is measured in canvas
        // units and collapses to ~0.5 px at the default fit zoom (k ≈ 0.19) —
        // the rivers, i.e. the drainage skeleton of the map, simply vanish.
        node
          .querySelectorAll("path")
          .forEach((p) => p.setAttribute("vector-effect", "non-scaling-stroke"))
        ;(riversG.node() as Element).appendChild(node)
      }
    }, [mapReady, rivers, darkBg])

    // ── Render road network (rough.js dashed lines) ──────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const roadsG = svg.select("#roads")
      roadsG.selectAll("*").remove()

      if (!roads || roads.length === 0) return

      // Road visibility. Previously a single 1px stroke at 30% opacity drawn over
      // the parchment land colour — effectively invisible. Measured on 西游记:
      // 119 road segments were rendered and the rendered stroke width was 1px, so
      // the map read as having no paths at all (the single most visible gap versus
      // a game map).
      // Now graded: every 3rd segment is presented as a major route (solid, thicker)
      // and the rest as minor (dashed).
      // ⚠️ The road data carries NO hierarchy field, so this alternation is a
      // presentation heuristic, not a real classification — if road等级 is ever
      // added upstream, replace this.
      const roadMajorColor = spaceThemeProp
        ? SPACE_THEME.routeColor
        : darkBg
          ? "rgba(170,148,104,0.72)"
          : "rgba(104,84,48,0.72)"
      const roadMinorColor = spaceThemeProp
        ? SPACE_THEME.routeColor
        : darkBg
          ? "rgba(170,148,104,0.52)"
          : "rgba(104,84,48,0.52)"
      const roadDash = spaceThemeProp ? "6,8" : "5,4"
      const roadMajorWidth = spaceThemeProp ? 1.5 : 2.2
      const roadMinorWidth = spaceThemeProp ? 1.5 : 1.5

      // Use simple SVG paths instead of roughjs for performance
      // (roughjs creates multiple DOM elements per road, causing zoom lag)
      // ⚠️ Rendered-road gate. The road data currently carries ONLY the two
      // endpoints of each edge — i.e. they are location co-occurrence chords
      // (two places that appear in the same chapter), NOT travel paths. Drawing
      // each edge as a segment produced 120+ (near-)straight lines criss-crossing
      // the map — a "strange straight line" web that has no business on a map and
      // that a curve merely disguises (measured: a 14 %-of-chord bow still leaves
      // 95/123 edges at straightness > 0.97). So we only draw roads that actually
      // carry real intermediate waypoints. With today's 2-point data this renders
      // nothing; the layer re-enables itself automatically if/when the upstream
      // ever emits genuine road geometry.
      const drawableRoads = roads.filter((r) => r.points.length > 2)
      if (drawableRoads.length === 0) return

      for (const [roadIndex, road] of drawableRoads.entries()) {
        const isMajor = roadIndex % 3 === 0
        const d = road.points
          .map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`))
          .join(" ")
        const path = roadsG
          .append("path")
          .attr("d", d)
          .attr("fill", "none")
          .attr("stroke", isMajor ? roadMajorColor : roadMinorColor)
          .attr("stroke-width", isMajor ? roadMajorWidth : roadMinorWidth)
          .attr("stroke-dasharray", isMajor ? "none" : roadDash)
          .attr("vector-effect", "non-scaling-stroke")
          .style("pointer-events", "none")
        // Space theme glow effect via SVG filter
        if (spaceThemeProp) {
          path.style("filter", "drop-shadow(0 0 4px rgba(100, 181, 246, 0.5))")
        }
      }
    }, [mapReady, roads, darkBg, spaceThemeProp])

    // ── Render coastline + ocean fill (rough.js) ──────────
    useEffect(() => {
      if (!svgRef.current || !mapReady || !roughCanvasRef.current || spaceThemeProp) return
      const svg = d3Selection.select(svgRef.current)
      const defs = svg.select("defs")
      const oceanG = svg.select("#coastline-ocean")
      const shelfG = svg.select("#shelf")
      const coastG = svg.select("#coastline")
      oceanG.selectAll("*").remove()
      shelfG.selectAll("*").remove()
      coastG.selectAll("*").remove()

      const rc = roughCanvasRef.current

      if (landmasses && landmasses.length > 0) {
        // ── New: multi-island landmass rendering ──

        // Helper: build SVG path from coordinate array
        const toPathD = (pts: [number, number][], reverse = false) => {
          const ordered = reverse ? [...pts].reverse() : pts
          return ordered.map((p, i) => `${i === 0 ? "M" : "L"} ${p[0]},${p[1]}`).join(" ") + " Z"
        }

        // ── Land clip ────────────────────────────────
        // Everything that is a *tint* rather than a shape needs the coastline
        // turned into a mask, or it washes over the sea: the region fills drew
        // a stained-glass partition across open water, and the terrain PNG is
        // a whole-canvas biome noise with no land/sea information in it at all,
        // so it greened the ocean too.
        //
        // One <path> per landmass rather than one path for all of them, because
        // `clip-rule` is a property of the path: within a single path, evenodd
        // would cancel any two landmasses that happen to overlap. Holes are
        // inner seas, and they are counted out by the same evenodd rule.
        const clipPath = defs.select<SVGClipPathElement>("#land-clip")
        clipPath.selectAll("*").remove()
        const cp = clipPath.empty()
          ? defs.append("clipPath").attr("id", "land-clip")
          : clipPath
        cp.attr("clipPathUnits", "userSpaceOnUse")
        for (const lm of landmasses) {
          let d = toPathD(lm.coastline)
          for (const hole of lm.holes) d += " " + toPathD(hole)
          cp.append("path").attr("d", d).attr("clip-rule", "evenodd")
        }
        // Tint/wash layers that must never paint over the sea. #terrain is the
        // scattered ground cover, which paints waves out at sea on purpose.
        // #territories is included (2026-09-27): its convex-hull outlines ran
        // straight across the ocean — measured, one hull spanned 4385×1648 and
        // 32 % of its boundary sat over open water.
        for (const id of ["#regions", "#terrain-biome", "#territories"]) {
          svg.select(id).attr("clip-path", "url(#land-clip)")
        }

        // Build ocean fill path (canvas rect + all coastlines as holes, evenodd)
        let oceanPathD = `M 0 0 L ${canvasW} 0 L ${canvasW} ${canvasH} L 0 ${canvasH} Z`
        for (const lm of landmasses) {
          oceanPathD += " " + toPathD(lm.coastline)
          // Add holes as "un-holes" in the ocean (they should be ocean)
          for (const hole of lm.holes) {
            oceanPathD += " " + toPathD(hole, true)
          }
        }

        // Ocean fill. This is the map's single most important value decision:
        // with the sea too close to the land in tone, the continents read as
        // stains rather than as places -- which is what a comment here had
        // already complained about in words.
        //
        // Measured with `scripts/probe_map_dom.cjs` + `probe_map_visual.py`, on a
        // 1600x1000 capture of 西游记 taken AFTER the render settles, using the
        // renderer's own land mask (`#coastline-ocean.isPointInFill`) rather than
        // a colour guess:
        //
        //   0.52  -> land/sea delta-L 13.7 at 1.13:1   (the "stains" complaint)
        //   0.68  -> still too close
        //   0.85  -> see the constant below; target is delta-L >= 28
        //
        // ⚠️ Measure only on a settled render. An earlier pass screenshotted with
        // `chrome --screenshot` before the fit/labels landed and reported 40.6 --
        // a number produced by the renderer not having drawn its shelf and region
        // bands yet. The probe that drives playwright waits for `.location-item`
        // and settles, so it cannot make that mistake.
        oceanG
          .append("path")
          .attr("d", oceanPathD)
          .attr("fill", darkBg ? "rgba(20,38,66,0.62)" : "rgba(74,108,150,0.85)")
          .attr("fill-rule", "evenodd")
          .style("pointer-events", "none")

        // ── Shallow-water shelf ───────────────────────
        // The shelf is the sea one band out from the coast (the backend traces
        // it at `dist_field < threshold * 1.3`, i.e. an expanded coastline
        // island), and lightening the water as it approaches land is the
        // single cheapest "this is a chart" signal there is — every game map
        // and every hand-drawn one does it. It was here already, but drawn as
        // a 0.12-alpha dashed hairline with `fill: none`, which is to say not
        // drawn at all.
        //
        // A *mask*, not a clip, and the distinction is the whole trick: the
        // shelf polygon covers the island as well as the water around it, so
        // the thing that has to be removed is the land. Interior holes are
        // inner seas and stay white in the mask, so they get shallow water too
        // — which is right, they are water.
        const seaMask = defs.select<SVGMaskElement>("#sea-mask")
        seaMask.selectAll("*").remove()
        const sm = seaMask.empty()
          ? defs.append("mask").attr("id", "sea-mask")
          : seaMask
        // The mask *region* is pinned to the canvas rect, not left to the
        // default -10%/120% box. Those defaults are percentages, and a
        // percentage has to resolve against some viewport — which here is the
        // 1680×1000 screen, not the 8000×4500 canvas the contents are written
        // in. The result is a mask that happens to cover the top-left corner
        // of the world and erases the shelf everywhere else.
        sm.attr("maskUnits", "userSpaceOnUse")
          .attr("x", 0)
          .attr("y", 0)
          .attr("width", canvasW)
          .attr("height", canvasH)
        sm.append("rect")
          .attr("x", 0)
          .attr("y", 0)
          .attr("width", canvasW)
          .attr("height", canvasH)
          .attr("fill", "#fff")
        for (const lm of landmasses) {
          let d = toPathD(lm.coastline)
          for (const hole of lm.holes) d += " " + toPathD(hole)
          sm.append("path")
            .attr("d", d)
            .attr("fill", "#000")
            .attr("fill-rule", "evenodd")
        }
        shelfG.attr("mask", "url(#sea-mask)")

        if (shelves) {
          // ── Which way is "shallow"? ─────────────────
          // Paler and slightly cyan, not bluer. The trap is that the shelf is
          // painted *over* the ocean, so the colour to compare against is the
          // ocean's composited result, not its fill: `rgba(118,156,188,0.46)`
          // over the parchment resolves to about (187,201,209) — a pale grey
          // blue — and a shelf fill at R=150 drops that by 15 while lifting B
          // by 3. The band came out *deeper* blue than the water it was
          // supposed to be shallowing, which is the one thing a depth cue must
          // not do. It was measurable and it was backwards: the A/B luminance
          // delta was -0.24, i.e. neither lighter nor darker, just bluer.
          //
          // So both fills are chosen to lift every channel above the water
          // they replace. Dark theme lifts harder because its ocean is
          // `rgba(34,58,92,0.42)` over a near-black plate — there the band is
          // most of the contrast the coastal water has.
          // A whisper, and the reason is measured rather than tastes. The band
          // is a flat fill with a hard edge, so it is only invisible while the
          // water around it is nearly the same value. Deepening the ocean (see
          // the fill above) put ~14 levels between the two and the band came
          // out as a distinct light ring around every landmass -- stickers with
          // an outline, not land in water. Two ways out: give up the depth, or
          // make the band faint enough that a hard edge has nothing to show.
          // Depth is worth more than the band, so the band keeps only enough
          // alpha to lift every channel above the water it replaces, which is
          // the requirement; the soft read is left to the distance.
          //
          // This was briefly 0.11, on the theory that three nested rings stacked
          // at one alpha would give the shallow-to-deep gradient for free. The
          // rings are built from a *global* distance field, so the outer one
          // wrapped the entire archipelago instead of each landmass, and the
          // whole thing went back to one ring (see `_SHELF_RING_MULTS`). One
          // ring, one alpha, so it is the measured 0.22 again.
          // ── Depth by band ─────────────────────────────
          // `shelf_depth` runs 0 at the shore to 1 at the furthest the recipe
          // looks; the backend emits one band per entry of `_SHELF_RING_MULTS`,
          // and with the shipped pair that is {0, 1}. Both ends are anchors on
          // the measured fills above rather than points on a ramp: the shallow
          // end is the same colour v9 and v10 settled on, and the deep end is
          // the one that makes the water read as a surface with a floor under
          // it instead of as one flat sheet.
          //
          // The shelves arrive sorted by area descending, i.e. outermost first,
          // and each band's polygon contains the bands inside it — so the last
          // path painted here is the innermost, and the shallow fill wins on top
          // of the deep one it is nested in. If that sort ever changes, the
          // whole banding reverses and the map turns inside out.
          const shelfShallow = darkBg
            ? { r: 96, g: 140, b: 180, a: 0.26 }
            : { r: 206, g: 230, b: 242, a: 0.22 }
          const shelfDeep = darkBg
            ? { r: 10, g: 26, b: 48, a: 0.34 }
            : { r: 78, g: 124, b: 168, a: 0.26 }
          const depths = shelfDepth ?? []
          for (let si = 0; si < shelves.length; si++) {
            const d = depths[si]
            const t = d === undefined || Number.isNaN(d) ? 0 : Math.min(1, Math.max(0, d))
            const mix = (a: number, b: number) => a + (b - a) * t
            shelfG
              .append("path")
              .attr("d", toPathD(shelves[si] as [number, number][]))
              .attr(
                "fill",
                `rgba(${Math.round(mix(shelfShallow.r, shelfDeep.r))},` +
                  `${Math.round(mix(shelfShallow.g, shelfDeep.g))},` +
                  `${Math.round(mix(shelfShallow.b, shelfDeep.b))},` +
                  `${mix(shelfShallow.a, shelfDeep.a).toFixed(3)})`,
              )
              .style("pointer-events", "none")
          }
        }

        // Render coastline borders per landmass
        for (const lm of landmasses) {
          const coastPathD = toPathD(lm.coastline)
          const coastNode = rc.path(coastPathD, {
            roughness: 1.5,
            bowing: 1.0,
            seed: 42,
            stroke: darkBg ? "rgba(110,142,172,0.55)" : "#5d4c33",
            strokeWidth: 1.9,
            fill: "none",
          })
          coastNode.style.pointerEvents = "none"
          // Non-scaling stroke: constant width regardless of zoom level
          coastNode.querySelectorAll("path").forEach((p) => {
            p.setAttribute("vector-effect", "non-scaling-stroke")
          })
          ;(coastG.node() as Element).appendChild(coastNode)

          // Render hole borders (inner seas/lakes)
          for (const hole of lm.holes) {
            const holePathD = toPathD(hole)
            const holeNode = rc.path(holePathD, {
              roughness: 1.5,
              bowing: 1.0,
              seed: 43,
              stroke: darkBg ? "rgba(90,120,150,0.45)" : "rgba(93,76,51,0.75)",
              strokeWidth: 1.4,
              fill: "none",
            })
            holeNode.style.pointerEvents = "none"
            holeNode.querySelectorAll("path").forEach((p) => {
              p.setAttribute("vector-effect", "non-scaling-stroke")
            })
            ;(coastG.node() as Element).appendChild(holeNode)
          }
        }
      } else {
        // ── Fallback: convex hull coastline (backward compat) ──
        // No landmass set means no mask. Leaving a stale clip-path on #regions,
        // #terrain or #territories would hide those layers completely, and a
        // stale #sea-mask would erase the shelf.
        defs.select("#land-clip").selectAll("*").remove()
        for (const id of ["#regions", "#terrain-biome", "#territories"]) {
          svg.select(id).attr("clip-path", null)
        }
        defs.select("#sea-mask").selectAll("*").remove()
        svg.select("#shelf").attr("mask", null)
        const stableLayout = allLayout ?? layout
        const allPoints: CoastPoint[] = stableLayout
          .filter((item) => !item.is_portal)
          .map((item) => [item.x, item.y] as CoastPoint)
        if (allPoints.length < 3) return

        const hull = convexHull(allPoints)
        const expanded = expandHull(hull, Math.min(canvasW, canvasH) * 0.08)
        const noisy = distortCoastline(expanded, 42)
        const pathD = coastlineToPath(noisy)

        const oceanPath = `M 0 0 L ${canvasW} 0 L ${canvasW} ${canvasH} L 0 ${canvasH} Z ${pathD}`
        oceanG
          .append("path")
          .attr("d", oceanPath)
          .attr("fill", darkBg ? "rgba(34,58,92,0.42)" : "rgba(104,142,178,0.52)")
          .attr("fill-rule", "evenodd")
          .style("pointer-events", "none")

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
      }
    }, [mapReady, landmasses, shelves, shelfDepth, allLayout, layout, canvasW, canvasH, darkBg])

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

        // ── Region terrain tint (visual L2, 2026-09-25) ──────────────────
        // regionBoundaries was only ever used to draw the curved region NAME —
        // its `polygon` and `color` were unused, and the #regions group was
        // cleared then left empty. Result: the land rendered as one flat colour
        // block, the single biggest "unfinished" tell versus a game map.
        // Tinting each region with its own colour gives biome/zone shading
        // WITHOUT touching the baked terrain.png — no re-bake, no backend
        // change, no data migration.
        // Kept translucent so terrain texture and roads beneath still read
        // through; the hand-drawn filter keeps it in the parchment idiom.
        if (rb.polygon && rb.polygon.length > 2) {
          regionsG
            .append("path")
            .attr("d", polygonToPath(rb.polygon))
            .attr("fill", rb.color)
            .attr("fill-opacity", darkBg ? 0.2 : 0.17)
            // No outline (2026-09-27). The 1.6 px brown stroke turned the 30
            // region polygons into a straight-edged web across the land — the
            // "strange brown triangles" a reader sees. Region identity rides on
            // the fills alone now; the tint layer stays, it just draws no ink.
            .attr("stroke", "none")
            .attr("filter", "url(#hand-drawn)")
            .style("pointer-events", "none")
        }

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
      if (!svgRef.current || !mapReady || spaceThemeProp) return
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

      // Per-level rendering parameters.
      // `STROKE_WIDTH` is authored in *screen* pixels — see the
      // non-scaling-stroke pass on each rough node below. Read as canvas units
      // it is a different width at every zoom: 3.0 draws at 0.57 px on the
      // 西游记 overview (k ≈ 0.19, i.e. a hairline that disappears) and at
      // 6 px once auto-fit lands on a sparse layer (天界/冥界 hold 17 places
      // apiece, so k sits well past 1 — the same 3.0 became fat marker
      // chrome). Non-scaling makes one number mean one width.
      const STROKE_WIDTH = [2.2, 1.8, 1.3, 1.0]
      const FILL_OP = darkBg
        ? [0.20, 0.15, 0.11, 0.08]
        : [0.14, 0.11, 0.08, 0.06]
      const LABEL_SIZE = [16, 13, 11, 10]
      const LABEL_OP = isDense
        ? [0.20, 0.12, 0.08, 0.06]
        : [0.35, 0.25, 0.20, 0.15]
      const LABEL_SPACING = ["3px", "1px", "0", "0"]

      const clamp = (level: number) => Math.min(level, 3)

      for (const terr of territories) {
        const li = clamp(terr.level)
        const pathData = polygonToPath(terr.polygon)

        // No outline (2026-09-27). A convex-hull outline is a polygon with
        // straight edges, so stroking it threw long brown chords across the
        // map — and, unclipped, straight out over the sea. The fill wash
        // carries faction identity on its own; #territories is clipped to land
        // now (see the land-clip block) and draws no boundary ink.
        const strokeColor = "none"
        const fillColor = darkBg ? terr.color : "#c4a97d"

        if (rc) {
          // Rough.js hand-drawn territory: flat wash + ink outline, the way a
          // printed atlas carries a province — the tint names the faction, the
          // line names the boundary.
          //
          // This used to be `fillStyle: "hachure"`, which paints the hull with
          // parallel rules spaced `hachureGap` **canvas** units apart. That is
          // zoom-dependent by construction: at the 西游记 fit zoom (k ≈ 0.19) a
          // gap of 6–12 canvas units is 1–2 px on screen, the rules fuse, and
          // the territory reads as an even wash — which is why the bug hid for
          // so long. Zoom in, or open a layer whose handful of places pushes
          // auto-fit past k = 1 (天界 17 places, 冥界 17), and the same gap
          // opens into a ruled grid: the territories arrive as wireframe quads
          // drawn over the art, lines running past the hull into open sea.
          // A solid fill deletes the whole artefact class at every zoom.
          const node = rc.path(pathData, {
            roughness: 1.2,
            bowing: 1.0,
            seed: hashString(terr.name) % 100,
            stroke: strokeColor,
            strokeWidth: STROKE_WIDTH[li],
            fill: fillColor,
            fillStyle: "solid",
          })
          node.querySelectorAll("path").forEach((p) => {
            // Same treatment rivers needed: rough.js hands back a <g> of
            // <path>s, so the attribute goes on the children.
            p.setAttribute("vector-effect", "non-scaling-stroke")
            const f = p.getAttribute("fill")
            if (f && f !== "none") {
              // rough.js 4.6.6 has no `fillOpacity` option; the solid-fill path
              // is the one child that carries a fill, so opacity goes here —
              // on the fill alone, leaving the boundary at full strength
              // instead of fading the whole node the way `style.opacity` did.
              p.setAttribute("fill-opacity", String(FILL_OP[li]))
            }
          })
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
            .attr("vector-effect", "non-scaling-stroke")
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
        const isWaypoint = !!(pt as TrajectoryPoint & { waypoint?: boolean }).waypoint
        const stay = stayDurations?.get(pt.location) ?? 0
        const baseR = isWaypoint ? 3 : Math.min(4 + stay * 1.5, 12)

        if (isWaypoint) {
          // Waypoint: small diamond (rotated square) to distinguish from chapter stops
          const s = baseR * 1.4
          trajG
            .append("rect")
            .attr("class", "traj-dot")
            .attr("x", coord[0] - s)
            .attr("y", coord[1] - s)
            .attr("width", s * 2)
            .attr("height", s * 2)
            .attr("data-base-r", baseR)
            .attr("transform", `rotate(45 ${coord[0]} ${coord[1]})`)
            .attr("fill", isVisible ? "#fb923c" : "#fdba74")
            .attr("fill-opacity", isVisible ? 0.8 : 0.2)
            .attr("stroke", isVisible ? "#fff" : "#fdba74")
            .attr("stroke-width", 1)
            .attr("stroke-opacity", isVisible ? 0.8 : 0.2)
            .append("title")
            .text(`${pt.location}（途经）`)
        } else {
          // Regular chapter stop: circle
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
        }

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

        const typeColor = locationColor(loc?.type ?? "", item.name, darkBg)
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
          color = locationColor(loc?.type ?? "", item.name, darkBg)
          opacity = 1
        } else if (isRevealed) {
          color = "#9ca3af"
          opacity = 0.35
        } else {
          color = locationColor(loc?.type ?? "", item.name, darkBg)
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

        // Confidence-based styling: unconstrained locations get dashed ring (constraint mode only)
        const isUnconstrained = layoutMode === "constraint" && isActive && loc?.placement_confidence === "unconstrained"
        if (isUnconstrained && !strokeDasharray) {
          strokeDasharray = "4 3"
          opacity *= 0.85
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
        const hitCircle = locG
          .append("circle")
          .attr("class", "loc-hitarea")
          .attr("cx", item.x)
          .attr("cy", item.y)
          .attr("r", Math.max(iconSize / 2 + 6, 14))
          .attr("fill", "transparent")

        // Tooltip for unconstrained (speculative) placements
        if (isUnconstrained) {
          hitCircle.append("title").text("推测放置（无空间约束）")
        }

        // Icon — render as inner SVG group (local coords centered at origin)
        if (spaceThemeProp) {
          // Space theme: glowing circle node
          const spaceColor = getSpaceNodeColor(tier)
          const glowR = getSpaceGlowRadius(tier)
          const nodeRadius = iconSize / 2
          if (glowR > 0) {
            locG
              .append("circle")
              .attr("class", "loc-glow")
              .attr("cx", item.x)
              .attr("cy", item.y)
              .attr("r", nodeRadius + glowR)
              .attr("fill", spaceColor)
              .attr("opacity", opacity * 0.15)
              .style("pointer-events", "none")
              .style("filter", `blur(${glowR * 0.6}px)`)
          }
          locG
            .append("circle")
            .attr("class", "loc-icon")
            .attr("cx", item.x)
            .attr("cy", item.y)
            .attr("r", nodeRadius)
            .attr("fill", spaceColor)
            .attr("opacity", opacity)
            .attr("stroke", "rgba(255,255,255,0.3)")
            .attr("stroke-width", 0.5)
        } else {
          // A pale plate under the mark, the way a printed map sets a city
          // stamp on a disc. It keeps the icon readable over dark ocean, pale
          // desert and busy forest alike without tinting the art itself.
          // Capped so continent-level marks don't get a dinner-plate.
          locG
            .append("circle")
            .attr("class", "loc-plate")
            .attr("cx", item.x)
            .attr("cy", item.y)
            .attr("r", Math.min(iconSize * 0.32, 18))
            .attr("fill", darkBg ? "rgba(17,24,39,0.5)" : "rgba(250,245,233,0.62)")
            .attr("stroke", darkBg ? "rgba(226,214,190,0.4)" : "rgba(120,96,66,0.45)")
            .attr("stroke-width", 1)
            .attr("vector-effect", "non-scaling-stroke")
            .attr("opacity", opacity)
            .style("pointer-events", "none")

          const iconContent = iconDefs.get(iconName)
          if (iconContent) {
            const iconG = locG
              .append("g")
              .attr("class", "loc-icon")
              .attr(
                "transform",
                // The 48 divisor is not a typo: these SVGs are 24-unit viewBoxes,
                // so scale(iconSize/48) renders the 24-unit box iconSize/2 wide,
                // and every mark's local centre sits at local (12,12) — which
                // that scale lands at `x - iconSize/4` unless the translate
                // pre-compensates by exactly that. It did not, so every mark
                // rode up and to the left of its anchor by a quarter of its own
                // size (measured on 西游记, 63/63 pairs: -14 px at continent,
                // -11 at kingdom, -9 at region, -7 at city, -5.5 at site,
                // -4 at building). Shifting by iconSize/4 re-centres the box
                // without touching the size calibration the plate radius is
                // built on — widening the divisor to 24 would double every mark.
                `translate(${item.x - iconSize / 4}, ${item.y - iconSize / 4}) scale(${iconSize / 48})`,
              )
              .attr("fill", color)
              // A light outline around the mark, in screen pixels. Measured on
              // 西游记: the pale plate under each mark covers the inner 41 % of
              // the icon's box — the art reaches iconSize/2, the plate is
              // min(iconSize*0.32, 18) — so most of every mark lands on bare
              // terrain. There the dark ink measures 2.4-3.1:1 against the
              // ground depending on where it falls (background luminance under
              // the icons runs 144-195), which is a coin toss. A halo fixes the
              // silhouette without enlarging the plate, whose 18-unit cap is
              // deliberate — a continent mark must not become a dinner plate.
              .attr("stroke", darkBg ? "rgba(12,18,32,0.85)" : "rgba(250,245,233,0.92)")
              .attr("stroke-width", 1)
              .attr("vector-effect", "non-scaling-stroke")
              .attr("paint-order", "stroke")
              .style("color", color)  // `currentColor` above resolves here
              .attr("opacity", opacity)
            iconG.html(iconContent)
          }
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

        // Dashed border ring (boundary-role or unconstrained confidence)
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
        const textColor = spaceThemeProp
          ? SPACE_THEME.labelColor
          : isRevealed
            ? "#9ca3af"
            : mention >= 3
              ? darkBg ? "#e5e7eb" : "#374151"
              : "#9ca3af"
        const fontSize = TIER_TEXT_SIZE[tier] ?? 12

        const labelEl = locG
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
        if (spaceThemeProp) {
          labelEl.style("filter", `drop-shadow(0 0 3px ${SPACE_THEME.labelGlow})`)
        }

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
      collapsedChildCount, spaceThemeProp,
    ])

    // ── Setup drag behavior (only in edit mode) ──────
    const setupDrag = useCallback(
      (svg: d3Selection.Selection<SVGSVGElement, unknown, null, undefined>) => {
        const locationItems = svg.selectAll<SVGGElement, unknown>(".location-item")

        // Remove existing drag handlers first
        locationItems.on(".drag", null)

        // Only enable drag in edit mode
        if (!editMode) {
          locationItems.style("cursor", "pointer")
          return
        }

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
                // Must match the render path's anchor, or a dragged mark jumps
                // back by a quarter of its size the moment the drag ends — the
                // drag transform writes this attribute and nothing re-reads it.
                `translate(${canvasX - iconSize / 4}, ${canvasY - iconSize / 4}) scale(${iconSize / 48})`,
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
      [locMap, iconDefs, editMode],
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
        // Direction/distance conflicts involve a pair — mark the other location too
        const other = c.details?.other as string | undefined
        if (other && (c.type === "direction" || c.type === "distance")) {
          const otherList = idx.get(other) ?? []
          otherList.push(c.description)
          idx.set(other, otherList)
        }
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
        const minScale = (TIER_MIN_SCALE[tier] ?? 1.2) / tierScaleDivisor
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
        const minScale = (TIER_MIN_SCALE[tier] ?? 1.2) / tierScaleDivisor
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
          // offsetX/Y are screen-space constants; counter-scale at (x,y) maps
          // label offset (labelX - x) → (labelX - x)/k in canvas, then zoom
          // restores it to (labelX - x) in screen-space — constant regardless of k.
          label
            .attr("x", x + placement.offsetX)
            .attr("y", y + placement.offsetY)
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
    }, [mapReady, currentScale, locMap, tierScaleDivisor])

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
      if (mapReady && layout.length > 0 && !focusLocation) {
        const t = setTimeout(fitToLocations, 200)
        return () => clearTimeout(t)
      }
    }, [mapReady, layout, fitToLocations, focusLocation])

    // ── Focus location: pan + zoom + persistent highlight ──
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const focusG = svg.select<SVGGElement>("#focus-overlay")
      focusG.selectAll("*").remove()

      if (!focusLocation || !zoomRef.current) return
      // Use allLayout (unfiltered) for flyTo lookup — filtered layout may not contain the target
      const flyLayout = allLayout && allLayout.length > 0 ? allLayout : layout
      const flyLocs = allLocations && allLocations.length > 0 ? allLocations : locations
      // Try exact match first, then fallback to parent location
      let item = flyLayout.find((l) => l.name === focusLocation)
      if (!item) {
        const loc = flyLocs.find((l) => l.name === focusLocation)
        if (loc?.parent) {
          item = flyLayout.find((l) => l.name === loc.parent)
        }
      }
      if (!item) return

      const svgEl = svgRef.current
      const svgWidth = svgEl.clientWidth || 800
      const svgHeight = svgEl.clientHeight || 600

      // Compute scale from full layout extent (not filtered subset)
      const xs = flyLayout.map((l) => l.x)
      const ys = flyLayout.map((l) => l.y)
      const dataW = Math.max(1, Math.max(...xs) - Math.min(...xs))
      const dataH = Math.max(1, Math.max(...ys) - Math.min(...ys))
      const targetViewW = dataW * 0.3
      const targetViewH = dataH * 0.3
      const scaleForView = Math.min(svgWidth / targetViewW, svgHeight / targetViewH)
      const focusScale = Math.min(Math.max(scaleForView, 0.3), 4.0)
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
    }, [focusLocation, locations, allLocations, layout, allLayout, mapReady, darkBg])

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
          {getVisibleTiers(currentScale, tierScaleDivisor)}
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
