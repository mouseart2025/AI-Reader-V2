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
// Map [0, 1000] canvas coordinates to a 2° × 2° geographic extent
// centered at (10°E, 5°N) — near the equator for minimal Mercator distortion.
const CANVAS_SIZE = 1000
const EXTENT_DEG = 2.0
const SCALE = EXTENT_DEG / CANVAS_SIZE
const CENTER_LNG = 10
const CENTER_LAT = 5

function toLngLat(x: number, y: number): [number, number] {
  return [
    CENTER_LNG + (x - CANVAS_SIZE / 2) * SCALE,
    CENTER_LAT + (y - CANVAS_SIZE / 2) * SCALE,
  ]
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
const LYR_CIRCLES = "locations-circles"
const LYR_LABELS = "locations-labels"
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

    // ── Initialize map ──────────────────────────────
    useEffect(() => {
      if (!containerRef.current) return

      const center = toLngLat(CANVAS_SIZE / 2, CANVAS_SIZE / 2)
      const bgColor = getMapBgColor(layoutMode, layerType)
      const darkBg = isDarkBackground(layoutMode, layerType)

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
        zoom: 9,
        minZoom: 6,
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

      map.on("load", () => {
        // ── Z-order: regions → trajectory → locations → portals → labels ──

        // 1. Region boundaries (polygons)
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
            "fill-opacity": 0.08,
          },
        })
        map.addLayer({
          id: LYR_REGION_BORDERS,
          type: "line",
          source: SRC_REGIONS,
          paint: {
            "line-color": ["get", "color"],
            "line-opacity": 0.3,
            "line-width": 2,
            "line-dasharray": [4, 4],
          },
        })

        // Region labels (points at centroids)
        map.addSource(SRC_REGION_LABELS, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        })
        map.addLayer({
          id: LYR_REGION_LABELS,
          type: "symbol",
          source: SRC_REGION_LABELS,
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

        // 4. Location circles
        map.addSource(SRC_LOCATIONS, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        })
        map.addLayer({
          id: LYR_CIRCLES,
          type: "circle",
          source: SRC_LOCATIONS,
          paint: {
            "circle-radius": ["get", "radius"],
            "circle-color": ["get", "color"],
            "circle-opacity": ["get", "opacity"],
            "circle-stroke-color": [
              "case",
              ["get", "isCurrent"],
              "#d97706",
              ["get", "isRevealed"],
              "rgba(150,150,150,0.5)",
              "rgba(255,255,255,0.9)",
            ],
            "circle-stroke-width": [
              "case",
              ["get", "isCurrent"],
              3,
              1.5,
            ],
          },
        })

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

        // 6. Location labels
        map.addLayer({
          id: LYR_LABELS,
          type: "symbol",
          source: SRC_LOCATIONS,
          layout: {
            "text-field": ["get", "name"],
            "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
            "text-size": 12,
            "text-offset": [0, 1.2],
            "text-anchor": "top",
            "text-allow-overlap": false,
            "text-optional": true,
          },
          paint: {
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

        // ── Click handler for location circles ──
        map.on("click", LYR_CIRCLES, (e) => {
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
        })

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

        // Cursor style on hover
        map.on("mouseenter", LYR_CIRCLES, () => {
          map.getCanvas().style.cursor = "pointer"
        })
        map.on("mouseleave", LYR_CIRCLES, () => {
          map.getCanvas().style.cursor = ""
        })
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
    }, [layoutMode, layerType])

    // ── Load terrain image ──────────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapReady || !terrainUrl) return

      const topLeft = toLngLat(0, CANVAS_SIZE)
      const topRight = toLngLat(CANVAS_SIZE, CANVAS_SIZE)
      const bottomRight = toLngLat(CANVAS_SIZE, 0)
      const bottomLeft = toLngLat(0, 0)

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
    }, [mapReady, terrainUrl])

    // ── Update region GeoJSON ────────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapReady) return

      // Polygon features for fills + borders
      const polyFeatures: GeoJSON.Feature[] = []
      // Point features for labels at centroids
      const labelFeatures: GeoJSON.Feature[] = []

      if (regionBoundaries && regionBoundaries.length > 0) {
        for (const rb of regionBoundaries) {
          const { x1, y1, x2, y2 } = rb.bounds
          polyFeatures.push({
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: [[
                toLngLat(x1, y1),
                toLngLat(x2, y1),
                toLngLat(x2, y2),
                toLngLat(x1, y2),
                toLngLat(x1, y1),
              ]],
            },
            properties: { name: rb.region_name, color: rb.color },
          })
          // Label at centroid
          const cx = (x1 + x2) / 2
          const cy = (y1 + y2) / 2
          labelFeatures.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: toLngLat(cx, cy) },
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
            coordinates: toLngLat(item.x, item.y),
          },
          properties: {
            name: item.name,
            locType: loc?.type ?? "",
            parent: loc?.parent ?? "",
            mentionCount: mention,
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
          geometry: { type: "Point", coordinates: toLngLat(item.x, item.y) },
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
            geometry: { type: "Point", coordinates: toLngLat(srcItem.x, srcItem.y) },
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

      const coords: [number, number][] = []
      const pointFeatures: GeoJSON.Feature[] = []

      if (trajectoryPoints && trajectoryPoints.length > 0) {
        for (const pt of trajectoryPoints) {
          const item = layoutMapRef.current.get(pt.location)
          if (!item) continue
          const lnglat = toLngLat(item.x, item.y)
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

      const bounds = new maplibregl.LngLatBounds()
      for (const item of layout) {
        bounds.extend(toLngLat(item.x, item.y))
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

    return <div ref={containerRef} className="h-full w-full" />
  },
)
