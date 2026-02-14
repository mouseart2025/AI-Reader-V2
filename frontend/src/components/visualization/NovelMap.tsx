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
  MapLayoutItem,
  MapLocation,
  PortalInfo,
  RegionBoundary,
  TrajectoryPoint,
} from "@/api/types"
import { generateTerritories } from "@/lib/territoryGenerator"
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

const TIER_TEXT_SIZE: Record<string, number> = {
  continent: 22,
  kingdom: 18,
  region: 14,
  city: 12,
  site: 10,
  building: 9,
}

const TIER_ICON_SIZE: Record<string, number> = {
  continent: 32,
  kingdom: 28,
  region: 24,
  city: 20,
  site: 16,
  building: 14,
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
  layoutMode: "constraint" | "hierarchy" | "layered"
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
      terrainUrl,
      visibleLocationNames,
      revealedLocationNames,
      regionBoundaries,
      portals,
      trajectoryPoints,
      currentLocation,
      canvasSize: canvasSizeProp,
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
      () => generateTerritories(locations, layout, { width: canvasW, height: canvasH }),
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
        .attr("scale", "2")
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
          .attr("opacity", 0.04)
          .attr("fill", "#8b7355")
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

    // ── Terrain image ────────────────────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const terrainG = d3Selection.select(svgRef.current).select("#terrain")
      terrainG.selectAll("*").remove()

      if (terrainUrl) {
        terrainG
          .append("image")
          .attr("href", terrainUrl)
          .attr("x", 0)
          .attr("y", 0)
          .attr("width", canvasW)
          .attr("height", canvasH)
          .attr("opacity", 0.8)
          .attr("preserveAspectRatio", "none")
      }
    }, [mapReady, terrainUrl, canvasW, canvasH])

    // ── Render regions ───────────────────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const regionsG = svg.select("#regions")
      const labelsG = svg.select("#region-labels")
      regionsG.selectAll("*").remove()
      labelsG.selectAll("*").remove()

      if (!regionBoundaries || regionBoundaries.length === 0) return

      for (const rb of regionBoundaries) {
        // Distort for hand-drawn look
        const distorted = distortPolygonEdges(
          rb.polygon,
          canvasW,
          canvasH,
          12,
          42,
        )
        const pathData = polygonToPath(distorted)

        // Fill
        regionsG
          .append("path")
          .attr("d", pathData)
          .attr("fill", rb.color)
          .attr("fill-opacity", 0.08)
          .attr("stroke", rb.color)
          .attr("stroke-opacity", 0.25)
          .attr("stroke-width", 1.5)
          .attr("stroke-dasharray", "6,4")

        // Label at center
        labelsG
          .append("text")
          .attr("x", rb.center[0])
          .attr("y", rb.center[1])
          .attr("text-anchor", "middle")
          .attr("dominant-baseline", "central")
          .attr("fill", rb.color)
          .attr("opacity", 0.4)
          .attr("font-size", "18px")
          .attr("font-weight", "300")
          .style("pointer-events", "none")
          .text(rb.region_name)
      }
    }, [mapReady, regionBoundaries, canvasW, canvasH])

    // ── Render territories ───────────────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)
      const terrG = svg.select("#territories")
      const terrLabelsG = svg.select("#territory-labels")
      terrG.selectAll("*").remove()
      terrLabelsG.selectAll("*").remove()

      if (territories.length === 0) return

      for (const terr of territories) {
        const distorted = distortPolygonEdges(
          terr.polygon,
          canvasW,
          canvasH,
          16,
          7,
        )
        const pathData = polygonToPath(distorted)

        terrG
          .append("path")
          .attr("d", pathData)
          .attr("fill", terr.color)
          .attr("fill-opacity", 0.06 + terr.level * 0.02)
          .attr("stroke", terr.color)
          .attr("stroke-opacity", 0.3)
          .attr("stroke-width", Math.max(1, 2 - terr.level * 0.5))
          .attr("stroke-dasharray", "8,4")

        // Label at centroid
        const centroid = polygonCentroid(terr.polygon)
        terrLabelsG
          .append("text")
          .attr("x", centroid[0])
          .attr("y", centroid[1])
          .attr("text-anchor", "middle")
          .attr("dominant-baseline", "central")
          .attr("fill", terr.color)
          .attr("opacity", 0.3)
          .attr("font-size", `${Math.max(12, 16 - terr.level * 2)}px`)
          .attr("font-weight", "300")
          .style("pointer-events", "none")
          .text(terr.name)
      }
    }, [mapReady, territories, canvasW, canvasH])

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

        const dot = dotsG
          .append("circle")
          .attr("cx", item.x)
          .attr("cy", item.y)
          .attr("r", 3)
          .attr("fill", color)
          .attr("opacity", opacity)

        if (isCurrent) {
          dot
            .attr("stroke", "#92400e")
            .attr("stroke-width", 1.5)
        }
      }
    }, [mapReady, layout, locMap, visibleLocationNames, revealedLocationNames, currentLocation])

    // ── Render location icons + labels ───────────────
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

        const iconName = loc?.icon ?? "generic"
        const iconSize = TIER_ICON_SIZE[tier] ?? 20

        // Location group
        const locG = tierG
          .append("g")
          .attr("class", "location-item")
          .attr("data-name", item.name)
          .attr("data-tier", tier)
          .style("cursor", "pointer")

        // Icon — render as inner SVG group
        const iconContent = iconDefs.get(iconName)
        if (iconContent) {
          const iconG = locG
            .append("g")
            .attr(
              "transform",
              `translate(${item.x - iconSize / 2}, ${item.y - iconSize / 2}) scale(${iconSize / 48})`,
            )
            .attr("fill", color)
            .attr("opacity", opacity)
          iconG.html(iconContent)
        }

        // Label
        const textColor = isRevealed
          ? "#9ca3af"
          : mention >= 3
            ? darkBg ? "#e5e7eb" : "#374151"
            : "#9ca3af"
        const fontSize = TIER_TEXT_SIZE[tier] ?? 12

        locG
          .append("text")
          .attr("x", item.x)
          .attr("y", item.y + iconSize / 2 + fontSize * 0.9)
          .attr("text-anchor", "middle")
          .attr("font-size", `${fontSize}px`)
          .attr("fill", textColor)
          .attr("opacity", opacity)
          .attr("stroke", darkBg ? "rgba(0,0,0,0.6)" : "#ffffff")
          .attr("stroke-width", 1.5)
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
            const name = d3Selection.select(this).attr("data-name")
            if (!name) return
            const tier = d3Selection.select(this).attr("data-tier") as string
            const iconSize = TIER_ICON_SIZE[tier] ?? 20
            const fontSize = TIER_TEXT_SIZE[tier] ?? 12
            const iconName = locMap.get(name)?.icon ?? "generic"

            // Convert screen dx/dy to canvas coords by dividing by current scale
            const t = transformRef.current
            const canvasX = (event.sourceEvent.offsetX - t.x) / t.k
            const canvasY = (event.sourceEvent.offsetY - t.y) / t.k

            // Update icon position
            const g = d3Selection.select(this)
            const iconG = g.select("g")
            if (!iconG.empty() && iconDefs.has(iconName)) {
              iconG.attr(
                "transform",
                `translate(${canvasX - iconSize / 2}, ${canvasY - iconSize / 2}) scale(${iconSize / 48})`,
              )
            }

            // Update text position
            g.select("text")
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

    // ── Zoom-based tier visibility ───────────────────
    useEffect(() => {
      if (!svgRef.current || !mapReady) return
      const svg = d3Selection.select(svgRef.current)

      for (const tier of TIERS) {
        const minScale = TIER_MIN_SCALE[tier] ?? 1.2
        svg
          .select(`#locations-${tier}`)
          .style("display", currentScale >= minScale ? "" : "none")
      }

      // Territory labels fade at high zoom
      svg
        .select("#territory-labels")
        .style("opacity", currentScale < 2 ? 1 : 0.3)
      svg
        .select("#region-labels")
        .style("opacity", currentScale < 1.5 ? 1 : 0.3)

      // Overview dots fade at high zoom
      svg
        .select("#overview-dots")
        .style("opacity", currentScale > 1.5 ? 0.3 : 1)
    }, [mapReady, currentScale])

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
                "radial-gradient(ellipse at center, transparent 50%, rgba(120,90,50,0.18) 100%)",
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
