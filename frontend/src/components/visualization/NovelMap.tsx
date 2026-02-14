import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react"
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import type {
  LayerType,
  MapLayoutItem,
  MapLocation,
  PortalInfo,
  RegionBoundary,
  TrajectoryPoint,
} from "@/api/types"

// ── Canvas coordinate system ────────────────────────
// Map canvas coordinates to a geographic extent centered at (10°E, 5°N)
// near the equator for minimal Mercator distortion.
const DEFAULT_CANVAS_SIZE = 1000
const CENTER_LNG = 10
const CENTER_LAT = 5

function getExtentDeg(canvasSize: number): number {
  if (canvasSize >= 3000) return 6.0
  if (canvasSize >= 2000) return 4.0
  return 2.0
}

// Default initial zoom per spatial scale
const SCALE_DEFAULT_ZOOM: Record<string, number> = {
  cosmic: 5,
  continental: 6,
  national: 7,
  urban: 9,
  local: 11,
}

function getDefaultZoom(spatialScale?: string): number {
  return SCALE_DEFAULT_ZOOM[spatialScale ?? ""] ?? 9
}

function makeLngLatMapper(canvasSize: number) {
  const extentDeg = getExtentDeg(canvasSize)
  const scale = extentDeg / canvasSize
  return function toLngLat(x: number, y: number): [number, number] {
    return [
      CENTER_LNG + (x - canvasSize / 2) * scale,
      CENTER_LAT + (y - canvasSize / 2) * scale,
    ]
  }
}

// ── Tier zoom mapping ─────────────────────────────────
const TIER_MIN_ZOOM: Record<string, number> = {
  world: 6,
  continent: 6,
  kingdom: 7,
  region: 8,
  city: 9,
  site: 10,
  building: 11,
}

const TIER_TEXT_SIZE: Record<string, [number, number, number, number]> = {
  // [minZoom, minSize, maxZoom, maxSize] for interpolation
  continent: [6, 16, 12, 22],
  kingdom:   [7, 14, 12, 18],
  region:    [8, 12, 13, 16],
  city:      [9, 10, 14, 14],
  site:      [10, 9, 14, 12],
  building:  [11, 8, 14, 10],
}

const TIERS = ["continent", "kingdom", "region", "city", "site", "building"] as const

// Chinese labels for tier names (used in zoom indicator)
const TIER_LABELS: Record<string, string> = {
  continent: "大洲",
  kingdom: "国",
  region: "区域",
  city: "城镇",
  site: "地点",
  building: "建筑",
}

function getVisibleTiers(zoom: number): string {
  const visible = TIERS.filter((t) => zoom >= (TIER_MIN_ZOOM[t] ?? 99))
  if (visible.length === 0) return ""
  return visible.map((t) => TIER_LABELS[t] ?? t).join("/")
}

// ── Type colors ─────────────────────────────────────

// Detect celestial/underworld from location name
const CELESTIAL_KW = ["天宫", "天庭", "天门", "天界", "三十三天", "大罗天", "离恨天",
  "兜率宫", "凌霄殿", "蟠桃园", "瑶池", "灵霄宝殿", "九天应元府"]
const UNDERWORLD_KW = ["地府", "冥界", "幽冥", "阴司", "阴曹", "黄泉",
  "奈何桥", "阎罗殿", "森罗殿", "枉死城"]

function locationColor(type: string, name?: string): string {
  // Check name-based celestial/underworld first
  if (name) {
    if (CELESTIAL_KW.some((kw) => name.includes(kw))) return "#f59e0b"
    if (UNDERWORLD_KW.some((kw) => name.includes(kw))) return "#7c3aed"
  }

  const t = type.toLowerCase()
  if (t.includes("国") || t.includes("域") || t.includes("界")) return "#3b82f6"
  if (t.includes("城") || t.includes("镇") || t.includes("都") || t.includes("村"))
    return "#10b981"
  if (
    t.includes("山") ||
    t.includes("洞") ||
    t.includes("谷") ||
    t.includes("林")
  )
    return "#84cc16"
  if (t.includes("宗") || t.includes("派") || t.includes("门")) return "#8b5cf6"
  if (t.includes("海") || t.includes("河") || t.includes("湖")) return "#06b6d4"
  return "#6b7280"
}

// ── Layer background colors ─────────────────────────

const LAYER_BG_COLORS: Record<LayerType, string> = {
  overworld: "#f0ead6",
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

// ── Portal colors by target layer type ──────────────

const PORTAL_COLORS: Record<string, string> = {
  sky: "#f59e0b",
  underground: "#7c3aed",
  sea: "#06b6d4",
  pocket: "#a0845c",
  spirit: "#7c3aed",
  overworld: "#3b82f6",
}

// ── Map icon names ──────────────────────────────────

const ICON_NAMES = [
  "capital", "city", "town", "village", "camp",
  "mountain", "forest", "water", "desert", "island",
  "temple", "palace", "cave", "tower", "gate",
  "portal", "ruins", "sacred", "generic",
] as const

// Icon size interpolation per tier
const TIER_ICON_SIZE: Record<string, [number, number, number, number]> = {
  // [minZoom, minSize, maxZoom, maxSize]
  continent: [6, 0.6, 12, 1.2],
  kingdom:   [7, 0.5, 12, 1.0],
  region:    [8, 0.4, 13, 0.9],
  city:      [9, 0.35, 14, 0.8],
  site:      [10, 0.3, 14, 0.7],
  building:  [11, 0.25, 14, 0.6],
}

async function loadMapIcons(map: maplibregl.Map) {
  for (const name of ICON_NAMES) {
    const resp = await fetch(`/map-icons/${name}.svg`)
    const svgText = await resp.text()
    const img = new Image(48, 48)
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgText)}`
    await new Promise<void>((resolve) => {
      img.onload = () => resolve()
      img.onerror = () => resolve() // graceful fallback
    })
    const canvas = document.createElement("canvas")
    canvas.width = canvas.height = 48
    const ctx = canvas.getContext("2d")!
    ctx.drawImage(img, 0, 0, 48, 48)
    const data = ctx.getImageData(0, 0, 48, 48)
    map.addImage(`icon-${name}`, { width: 48, height: 48, data: data.data }, { sdf: true })
  }
}

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
  canvasSize?: number
  spatialScale?: string
  onLocationClick?: (name: string) => void
  onLocationDragEnd?: (name: string, x: number, y: number) => void
  onPortalClick?: (targetLayerId: string) => void
}

export interface NovelMapHandle {
  fitToLocations: () => void
}

// ── Layer / source IDs ──────────────────────────────
const SRC_REGIONS = "regions-src"
const SRC_REGION_LABELS = "region-labels-src"
const LYR_REGION_FILLS = "region-fills"
const LYR_REGION_BORDERS = "region-borders"
const LYR_REGION_LABELS = "region-labels"
const SRC_LOCATIONS = "locations-src"
const SRC_TRAJECTORY = "trajectory-line"
const LYR_TRAJECTORY_LINE = "trajectory-line-layer"
const SRC_TRAJ_POINTS = "trajectory-points"
const LYR_TRAJ_POINTS = "trajectory-points-layer"
const SRC_PORTALS = "portals-src"
const LYR_PORTALS = "portals-layer"

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
      spatialScale,
      onLocationClick,
      onLocationDragEnd,
      onPortalClick,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)
    const popupRef = useRef<maplibregl.Popup | null>(null)
    const [mapReady, setMapReady] = useState(false)

    // Stable refs for callbacks used inside map event handlers
    const onClickRef = useRef(onLocationClick)
    onClickRef.current = onLocationClick
    const onDragEndRef = useRef(onLocationDragEnd)
    onDragEndRef.current = onLocationDragEnd
    const onPortalClickRef = useRef(onPortalClick)
    onPortalClickRef.current = onPortalClick
    const fitAllCallbackRef = useRef<() => void>(() => {})

    // Build layout lookup
    const layoutMapRef = useRef<Map<string, MapLayoutItem>>(new Map())
    useEffect(() => {
      const m = new Map<string, MapLayoutItem>()
      for (const item of layout) m.set(item.name, item)
      layoutMapRef.current = m
    }, [layout])

    // Compute dynamic toLngLat based on canvasSize prop
    const canvasSize = canvasSizeProp ?? DEFAULT_CANVAS_SIZE
    const toLngLat = makeLngLatMapper(canvasSize)
    const toLngLatRef = useRef(toLngLat)
    toLngLatRef.current = toLngLat

    // ── Initialize map ──────────────────────────────
    useEffect(() => {
      if (!containerRef.current) return

      const cs = canvasSizeProp ?? DEFAULT_CANVAS_SIZE
      const localToLngLat = makeLngLatMapper(cs)
      const center = localToLngLat(cs / 2, cs / 2)
      const bgColor = getMapBgColor(layoutMode, layerType)
      const darkBg = isDarkBackground(layoutMode, layerType)

      const defaultZoom = getDefaultZoom(spatialScale)
      const map = new maplibregl.Map({
        container: containerRef.current,
        style: {
          version: 8,
          sources: {},
          layers: [
            {
              id: "background",
              type: "background",
              paint: {
                "background-color": bgColor,
              },
            },
          ],
        },
        center,
        zoom: defaultZoom,
        minZoom: Math.max(4, defaultZoom - 3),
        maxZoom: 16,
        renderWorldCopies: false,
        attributionControl: false,
      })

      map.addControl(
        new maplibregl.NavigationControl({ showCompass: true }),
        "top-right",
      )

      // Custom "fit all" control — uses ref to call latest fitToLocations
      const fitAllBtn = document.createElement("button")
      fitAllBtn.type = "button"
      fitAllBtn.title = "查看全貌"
      fitAllBtn.className = "maplibregl-ctrl-icon"
      fitAllBtn.style.cssText = "font-size:16px; line-height:29px;"
      fitAllBtn.textContent = "⌂"
      fitAllBtn.addEventListener("click", () => fitAllCallbackRef.current())

      class FitAllControl implements maplibregl.IControl {
        _container?: HTMLDivElement

        onAdd() {
          this._container = document.createElement("div")
          this._container.className = "maplibregl-ctrl maplibregl-ctrl-group"
          this._container.appendChild(fitAllBtn)
          return this._container
        }

        onRemove() {
          this._container?.remove()
        }
      }

      map.addControl(new FitAllControl(), "top-right")

      // Zoom level indicator (bottom-left)
      const zoomIndicator = document.createElement("div")
      zoomIndicator.style.cssText =
        "font-size:11px; color:rgba(120,120,120,0.8); padding:4px 8px; white-space:nowrap; pointer-events:none;"

      function updateZoomIndicator() {
        const z = Math.round(map.getZoom() * 10) / 10
        const tiers = getVisibleTiers(z)
        zoomIndicator.textContent = tiers ? `${tiers}` : ""
      }
      updateZoomIndicator()
      map.on("zoom", updateZoomIndicator)

      class ZoomIndicatorControl implements maplibregl.IControl {
        _container?: HTMLDivElement
        onAdd() {
          this._container = document.createElement("div")
          this._container.className = "maplibregl-ctrl"
          this._container.appendChild(zoomIndicator)
          return this._container
        }
        onRemove() {
          this._container?.remove()
        }
      }
      map.addControl(new ZoomIndicatorControl(), "bottom-left")

      map.on("load", async () => {
        // ── Load SVG icons as SDF images ──
        await loadMapIcons(map)

        // ── Z-order: regions → trajectory → locations (per-tier) → portals ──

        // 1. Region boundaries (Voronoi polygons)
        map.addSource(SRC_REGIONS, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        })
        map.addLayer({
          id: LYR_REGION_FILLS,
          type: "fill",
          source: SRC_REGIONS,
          paint: {
            "fill-color": ["get", "color"],
            "fill-opacity": [
              "interpolate", ["linear"], ["zoom"],
              6, 0.12,
              10, 0.04,
            ],
          },
        })
        map.addLayer({
          id: LYR_REGION_BORDERS,
          type: "line",
          source: SRC_REGIONS,
          paint: {
            "line-color": ["get", "color"],
            "line-opacity": [
              "interpolate", ["linear"], ["zoom"],
              6, 0.25,
              11, 0.08,
            ],
            "line-width": 1,
          },
        })

        // Region labels (points at centers)
        map.addSource(SRC_REGION_LABELS, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        })
        map.addLayer({
          id: LYR_REGION_LABELS,
          type: "symbol",
          source: SRC_REGION_LABELS,
          maxzoom: 10,
          layout: {
            "text-field": ["get", "name"],
            "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
            "text-size": 18,
            "text-allow-overlap": true,
          },
          paint: {
            "text-color": ["get", "color"],
            "text-opacity": 0.4,
            "text-halo-color": darkBg ? "rgba(0,0,0,0.3)" : "rgba(255,255,255,0.3)",
            "text-halo-width": 1,
          },
        })

        // 2. Trajectory line
        map.addSource(SRC_TRAJECTORY, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        })
        map.addLayer({
          id: LYR_TRAJECTORY_LINE,
          type: "line",
          source: SRC_TRAJECTORY,
          paint: {
            "line-color": "#f59e0b",
            "line-width": 3,
            "line-opacity": 0.85,
          },
          layout: { "line-cap": "round", "line-join": "round" },
        })

        // 3. Trajectory points
        map.addSource(SRC_TRAJ_POINTS, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        })
        map.addLayer({
          id: LYR_TRAJ_POINTS,
          type: "circle",
          source: SRC_TRAJ_POINTS,
          paint: {
            "circle-radius": 5,
            "circle-color": "#d97706",
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 1.5,
          },
        })

        // 4. Location source (shared across per-tier symbol layers)
        map.addSource(SRC_LOCATIONS, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        })

        // 4a. Per-tier symbol layers (icon + label combined)
        for (const tier of TIERS) {
          const minZoom = TIER_MIN_ZOOM[tier] ?? 9
          const textSizes = TIER_TEXT_SIZE[tier] ?? [9, 10, 14, 14]
          const iconSizes = TIER_ICON_SIZE[tier] ?? [9, 0.35, 14, 0.8]

          map.addLayer({
            id: `loc-${tier}`,
            type: "symbol",
            source: SRC_LOCATIONS,
            filter: ["==", ["get", "tier"], tier],
            minzoom: minZoom,
            layout: {
              "icon-image": ["concat", "icon-", ["get", "icon"]],
              "icon-size": [
                "interpolate", ["linear"], ["zoom"],
                iconSizes[0], iconSizes[1],
                iconSizes[2], iconSizes[3],
              ],
              "icon-allow-overlap": tier === "continent" || tier === "kingdom",
              "text-field": ["get", "name"],
              "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
              "text-size": [
                "interpolate", ["linear"], ["zoom"],
                textSizes[0], textSizes[1],
                textSizes[2], textSizes[3],
              ],
              "text-offset": [0, 1.5],
              "text-anchor": "top",
              "text-optional": tier !== "continent",
              "text-allow-overlap": false,
            },
            paint: {
              "icon-color": ["get", "color"],
              "icon-opacity": ["get", "opacity"],
              "text-color": [
                "case",
                ["get", "isRevealed"],
                "#9ca3af",
                [">=", ["get", "mentionCount"], 3],
                darkBg ? "#e5e7eb" : "#374151",
                "#9ca3af",
              ],
              "text-halo-color": darkBg ? "rgba(0,0,0,0.6)" : "#ffffff",
              "text-halo-width": 1.5,
              "text-opacity": ["get", "opacity"],
            },
          })
        }

        // 5. Portal markers
        map.addSource(SRC_PORTALS, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        })
        map.addLayer({
          id: LYR_PORTALS,
          type: "symbol",
          source: SRC_PORTALS,
          layout: {
            "text-field": "\u2299",
            "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
            "text-size": 20,
            "text-allow-overlap": true,
          },
          paint: {
            "text-color": ["get", "color"],
            "text-halo-color": darkBg ? "rgba(0,0,0,0.6)" : "rgba(255,255,255,0.8)",
            "text-halo-width": 2,
          },
        })

        // ── Click handler for location symbol layers (all per-tier) ──
        const symbolLayerIds = TIERS.map((t) => `loc-${t}`)

        function handleLocationClick(e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) {
          if (!e.features?.[0]) return
          const props = e.features[0].properties
          if (!props) return
          const name = props.name as string
          const lnglat = (e.features[0].geometry as GeoJSON.Point)
            .coordinates as [number, number]

          if (popupRef.current) popupRef.current.remove()

          const popup = new maplibregl.Popup({ offset: 12, closeButton: true })
            .setLngLat(lnglat)
            .setHTML(
              `<div style="font-size:13px; max-width:200px;">
                <div style="font-weight:600; margin-bottom:4px;">${props.name}</div>
                <div style="color:#666; font-size:11px; margin-bottom:4px;">
                  ${props.locType}${props.parent ? ` · ${props.parent}` : ""}
                </div>
                <div style="font-size:11px; color:#888; margin-bottom:6px;">
                  出现 ${props.mentionCount} 章
                </div>
                <button class="popup-card-btn" style="
                  font-size:11px; color:#3b82f6; background:none; border:none;
                  cursor:pointer; padding:0; text-decoration:underline;
                ">查看卡片</button>
              </div>`,
            )
            .addTo(map)

          popupRef.current = popup

          setTimeout(() => {
            const btn = popup
              .getElement()
              ?.querySelector(".popup-card-btn")
            if (btn) {
              btn.addEventListener("click", () => {
                onClickRef.current?.(name)
                popup.remove()
              })
            }
          }, 0)
        }

        for (const layerId of symbolLayerIds) {
          map.on("click", layerId, handleLocationClick)
          map.on("mouseenter", layerId, () => {
            map.getCanvas().style.cursor = "pointer"
          })
          map.on("mouseleave", layerId, () => {
            map.getCanvas().style.cursor = ""
          })
        }

        // ── Click handler for portal markers ──
        map.on("click", LYR_PORTALS, (e) => {
          if (!e.features?.[0]) return
          const props = e.features[0].properties
          if (!props) return
          const lnglat = (e.features[0].geometry as GeoJSON.Point)
            .coordinates as [number, number]

          if (popupRef.current) popupRef.current.remove()

          const popup = new maplibregl.Popup({ offset: 12, closeButton: true })
            .setLngLat(lnglat)
            .setHTML(
              `<div style="font-size:13px; max-width:220px;">
                <div style="font-weight:600; margin-bottom:4px;">${props.name}</div>
                <div style="color:#666; font-size:11px; margin-bottom:6px;">
                  通往: ${props.targetLayerName}
                </div>
                <button class="portal-enter-btn" style="
                  font-size:11px; color:#3b82f6; background:none; border:none;
                  cursor:pointer; padding:0; text-decoration:underline;
                  margin-right:8px;
                ">进入地图</button>
              </div>`,
            )
            .addTo(map)

          popupRef.current = popup

          setTimeout(() => {
            const enterBtn = popup.getElement()?.querySelector(".portal-enter-btn")
            if (enterBtn) {
              enterBtn.addEventListener("click", () => {
                onPortalClickRef.current?.(props.targetLayer as string)
                popup.remove()
              })
            }
          }, 0)
        })

        // Cursor style on hover for portals
        map.on("mouseenter", LYR_PORTALS, () => {
          map.getCanvas().style.cursor = "pointer"
        })
        map.on("mouseleave", LYR_PORTALS, () => {
          map.getCanvas().style.cursor = ""
        })

        setMapReady(true)
      })

      mapRef.current = map

      return () => {
        if (popupRef.current) popupRef.current.remove()
        map.remove()
        mapRef.current = null
        setMapReady(false)
      }
    }, [layoutMode, layerType, canvasSizeProp, spatialScale])

    // ── Load terrain image ──────────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapReady || !terrainUrl) return

      const cs = canvasSizeProp ?? DEFAULT_CANVAS_SIZE
      const localToLngLat = makeLngLatMapper(cs)
      const topLeft = localToLngLat(0, cs)
      const topRight = localToLngLat(cs, cs)
      const bottomRight = localToLngLat(cs, 0)
      const bottomLeft = localToLngLat(0, 0)

      if (map.getSource("terrain-img")) {
        map.removeLayer("terrain-layer")
        map.removeSource("terrain-img")
      }

      map.addSource("terrain-img", {
        type: "image",
        url: terrainUrl,
        coordinates: [topLeft, topRight, bottomRight, bottomLeft],
      })

      // Insert terrain below region layers
      map.addLayer(
        {
          id: "terrain-layer",
          type: "raster",
          source: "terrain-img",
          paint: { "raster-opacity": 0.8 },
        },
        LYR_REGION_FILLS,
      )
    }, [mapReady, terrainUrl, canvasSizeProp])

    // ── Update region GeoJSON ────────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapReady) return

      const ll = toLngLatRef.current

      // Polygon features for fills + borders
      const polyFeatures: GeoJSON.Feature[] = []
      // Point features for labels at centroids
      const labelFeatures: GeoJSON.Feature[] = []

      if (regionBoundaries && regionBoundaries.length > 0) {
        for (const rb of regionBoundaries) {
          // Voronoi polygon vertices
          const coords = rb.polygon.map(([x, y]) => ll(x, y))
          coords.push(coords[0]) // close the ring
          polyFeatures.push({
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: [coords],
            },
            properties: { name: rb.region_name, color: rb.color },
          })
          // Label at center
          labelFeatures.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: ll(rb.center[0], rb.center[1]) },
            properties: { name: rb.region_name, color: rb.color },
          })
        }
      }

      const regSrc = map.getSource(SRC_REGIONS) as maplibregl.GeoJSONSource
      if (regSrc) {
        regSrc.setData({ type: "FeatureCollection", features: polyFeatures })
      }
      const lblSrc = map.getSource(SRC_REGION_LABELS) as maplibregl.GeoJSONSource
      if (lblSrc) {
        lblSrc.setData({ type: "FeatureCollection", features: labelFeatures })
      }
    }, [mapReady, regionBoundaries])

    // ── Update location GeoJSON ─────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapReady) return

      const ll = toLngLatRef.current
      const locMap = new Map(locations.map((l) => [l.name, l]))
      const revealed = revealedLocationNames ?? new Set<string>()

      // Filter out portal markers from location circles
      const locationItems = layout.filter((item) => !item.is_portal)

      const features: GeoJSON.Feature[] = locationItems.map((item) => {
        const loc = locMap.get(item.name)
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

        return {
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: ll(item.x, item.y),
          },
          properties: {
            name: item.name,
            locType: loc?.type ?? "",
            parent: loc?.parent ?? "",
            mentionCount: mention,
            tier: loc?.tier ?? "city",
            icon: loc?.icon ?? "generic",
            radius: isCurrent
              ? 8
              : isRevealed
                ? 3
                : Math.max(4, Math.min(10, 3 + mention * 0.5)),
            color,
            opacity,
            isCurrent,
            isRevealed,
          },
        }
      })

      const src = map.getSource(SRC_LOCATIONS) as maplibregl.GeoJSONSource
      if (src) {
        src.setData({ type: "FeatureCollection", features })
      }
    }, [mapReady, layout, locations, visibleLocationNames, revealedLocationNames, currentLocation])

    // ── Update portal GeoJSON ────────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapReady) return

      const ll = toLngLatRef.current
      const portalFeatures: GeoJSON.Feature[] = []

      // Build portal features from layout items with is_portal flag
      const portalItems = layout.filter((item) => item.is_portal)
      for (const item of portalItems) {
        // Look up portal metadata from the portals prop
        const info = portals?.find((p) => p.name === item.name)
        const targetLayer = info?.target_layer ?? item.target_layer ?? ""
        // Determine layer type for color
        const color = PORTAL_COLORS[targetLayer] ?? PORTAL_COLORS.overworld

        portalFeatures.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: ll(item.x, item.y) },
          properties: {
            name: item.name,
            targetLayer,
            targetLayerName: info?.target_layer_name ?? targetLayer,
            color,
          },
        })
      }

      // Also add portals from the portals prop that aren't in layout
      // (use source_location position as fallback)
      if (portals) {
        const layoutPortalNames = new Set(portalItems.map((p) => p.name))
        for (const p of portals) {
          if (layoutPortalNames.has(p.name)) continue
          const srcItem = layoutMapRef.current.get(p.source_location)
          if (!srcItem) continue
          const color = PORTAL_COLORS[p.target_layer] ?? PORTAL_COLORS.overworld
          portalFeatures.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: ll(srcItem.x, srcItem.y) },
            properties: {
              name: p.name,
              targetLayer: p.target_layer,
              targetLayerName: p.target_layer_name,
              color,
            },
          })
        }
      }

      const src = map.getSource(SRC_PORTALS) as maplibregl.GeoJSONSource
      if (src) {
        src.setData({ type: "FeatureCollection", features: portalFeatures })
      }
    }, [mapReady, layout, portals])

    // ── Update trajectory GeoJSON ───────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapReady) return

      const ll = toLngLatRef.current
      const coords: [number, number][] = []
      const pointFeatures: GeoJSON.Feature[] = []

      if (trajectoryPoints && trajectoryPoints.length > 0) {
        for (const pt of trajectoryPoints) {
          const item = layoutMapRef.current.get(pt.location)
          if (!item) continue
          const lnglat = ll(item.x, item.y)
          coords.push(lnglat)
          pointFeatures.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: lnglat },
            properties: { chapter: pt.chapter, location: pt.location },
          })
        }
      }

      const lineSrc = map.getSource(SRC_TRAJECTORY) as maplibregl.GeoJSONSource
      if (lineSrc) {
        lineSrc.setData({
          type: "FeatureCollection",
          features:
            coords.length >= 2
              ? [
                  {
                    type: "Feature",
                    geometry: { type: "LineString", coordinates: coords },
                    properties: {},
                  },
                ]
              : [],
        })
      }

      const ptsSrc = map.getSource(SRC_TRAJ_POINTS) as maplibregl.GeoJSONSource
      if (ptsSrc) {
        ptsSrc.setData({ type: "FeatureCollection", features: pointFeatures })
      }
    }, [mapReady, trajectoryPoints])

    // ── Fit to locations ────────────────────────────
    const fitToLocations = useCallback(() => {
      const map = mapRef.current
      if (!map || layout.length === 0) return

      const ll = toLngLatRef.current
      const bounds = new maplibregl.LngLatBounds()
      for (const item of layout) {
        bounds.extend(ll(item.x, item.y))
      }
      map.fitBounds(bounds, { padding: 60, maxZoom: 13 })
    }, [layout])

    fitAllCallbackRef.current = fitToLocations
    useImperativeHandle(ref, () => ({ fitToLocations }), [fitToLocations])

    // Auto-fit when layout changes
    useEffect(() => {
      if (mapReady && layout.length > 0) {
        const t = setTimeout(fitToLocations, 200)
        return () => clearTimeout(t)
      }
    }, [mapReady, layout, fitToLocations])

    // ── Keyboard shortcuts ──────────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapReady) return

      function handleKeyDown(e: KeyboardEvent) {
        // Ignore when typing in inputs
        const tag = (e.target as HTMLElement)?.tagName
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return

        if (e.key === "Home") {
          e.preventDefault()
          fitAllCallbackRef.current()
        } else if (e.key === "=" || e.key === "+") {
          e.preventDefault()
          map!.zoomIn()
        } else if (e.key === "-") {
          e.preventDefault()
          map!.zoomOut()
        }
      }

      window.addEventListener("keydown", handleKeyDown)
      return () => window.removeEventListener("keydown", handleKeyDown)
    }, [mapReady])

    return <div ref={containerRef} className="h-full w-full" />
  },
)
