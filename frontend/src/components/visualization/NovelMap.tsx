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
import type { MapLayoutItem, MapLocation, TrajectoryPoint } from "@/api/types"

// ── Canvas coordinate system ────────────────────────
// Map [0, 1000] canvas coordinates to a small lng/lat range.
// Within ~0.009° the Mercator projection is nearly equidistant.
const CANVAS_SIZE = 1000
const SCALE = 0.009 / CANVAS_SIZE

function toLngLat(x: number, y: number): [number, number] {
  return [x * SCALE, y * SCALE]
}

function fromLngLat(lng: number, lat: number): [number, number] {
  return [lng / SCALE, lat / SCALE]
}

// ── Type colors ─────────────────────────────────────
function locationColor(type: string): string {
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

// ── Props ───────────────────────────────────────────

export interface NovelMapProps {
  locations: MapLocation[]
  layout: MapLayoutItem[]
  layoutMode: "constraint" | "hierarchy"
  terrainUrl: string | null
  /** Set of location names visible in the current chapter range */
  visibleLocationNames: Set<string>
  /** Currently highlighted trajectory points (for animation) */
  trajectoryPoints?: TrajectoryPoint[]
  /** Full trajectory for the selected person */
  fullTrajectory?: TrajectoryPoint[]
  /** Currently active location during playback */
  currentLocation?: string | null
  onLocationClick?: (name: string) => void
  onLocationDragEnd?: (name: string, x: number, y: number) => void
}

export interface NovelMapHandle {
  fitToLocations: () => void
}

export const NovelMap = forwardRef<NovelMapHandle, NovelMapProps>(
  function NovelMap(
    {
      locations,
      layout,
      layoutMode,
      terrainUrl,
      visibleLocationNames,
      trajectoryPoints,
      fullTrajectory,
      currentLocation,
      onLocationClick,
      onLocationDragEnd,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)
    const markersRef = useRef<Map<string, maplibregl.Marker>>(new Map())
    const popupRef = useRef<maplibregl.Popup | null>(null)
    const [mapLoaded, setMapLoaded] = useState(false)

    // Build layout lookup
    const layoutMap = useRef<Map<string, MapLayoutItem>>(new Map())
    useEffect(() => {
      const m = new Map<string, MapLayoutItem>()
      for (const item of layout) {
        m.set(item.name, item)
      }
      layoutMap.current = m
    }, [layout])

    // ── Initialize map ──────────────────────────────
    useEffect(() => {
      if (!containerRef.current) return

      const center = toLngLat(CANVAS_SIZE / 2, CANVAS_SIZE / 2)

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
                "background-color":
                  layoutMode === "hierarchy" ? "#1a1a2e" : "#f0ead6",
              },
            },
          ],
        },
        center: center,
        zoom: 14,
        minZoom: 12,
        maxZoom: 20,
        attributionControl: false,
      })

      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right")

      map.on("load", () => {
        setMapLoaded(true)
      })

      mapRef.current = map

      return () => {
        // Clean up markers
        markersRef.current.forEach((m) => m.remove())
        markersRef.current.clear()
        map.remove()
        mapRef.current = null
        setMapLoaded(false)
      }
    }, [layoutMode])

    // ── Load terrain image ──────────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapLoaded || !terrainUrl) return

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

      map.addLayer(
        {
          id: "terrain-layer",
          type: "raster",
          source: "terrain-img",
          paint: { "raster-opacity": 0.8 },
        },
        // Insert below markers
        undefined,
      )
    }, [mapLoaded, terrainUrl])

    // ── Trajectory line layer ───────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapLoaded) return

      const sourceId = "trajectory-line"
      const layerId = "trajectory-line-layer"
      const pointsLayerId = "trajectory-points-layer"

      // Build GeoJSON from trajectory points
      const coords: [number, number][] = []
      const pointFeatures: GeoJSON.Feature[] = []

      if (trajectoryPoints && trajectoryPoints.length > 0) {
        for (const pt of trajectoryPoints) {
          const item = layoutMap.current.get(pt.location)
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

      const lineGeoJSON: GeoJSON.FeatureCollection = {
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
      }

      const pointsGeoJSON: GeoJSON.FeatureCollection = {
        type: "FeatureCollection",
        features: pointFeatures,
      }

      if (map.getSource(sourceId)) {
        ;(map.getSource(sourceId) as maplibregl.GeoJSONSource).setData(lineGeoJSON)
        ;(map.getSource("trajectory-points") as maplibregl.GeoJSONSource).setData(
          pointsGeoJSON,
        )
      } else {
        map.addSource(sourceId, { type: "geojson", data: lineGeoJSON })
        map.addLayer({
          id: layerId,
          type: "line",
          source: sourceId,
          paint: {
            "line-color": "#f59e0b",
            "line-width": 3,
            "line-opacity": 0.85,
          },
          layout: {
            "line-cap": "round",
            "line-join": "round",
          },
        })

        map.addSource("trajectory-points", { type: "geojson", data: pointsGeoJSON })
        map.addLayer({
          id: pointsLayerId,
          type: "circle",
          source: "trajectory-points",
          paint: {
            "circle-radius": 5,
            "circle-color": "#d97706",
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 1.5,
          },
        })
      }
    }, [mapLoaded, trajectoryPoints])

    // ── Render markers ──────────────────────────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapLoaded) return

      const existingMarkers = markersRef.current
      const newNames = new Set(layout.map((l) => l.name))

      // Remove markers no longer in layout
      existingMarkers.forEach((marker, name) => {
        if (!newNames.has(name)) {
          marker.remove()
          existingMarkers.delete(name)
        }
      })

      // Location info lookup
      const locMap = new Map(locations.map((l) => [l.name, l]))

      for (const item of layout) {
        const loc = locMap.get(item.name)
        if (!loc) continue

        const lnglat = toLngLat(item.x, item.y)
        const isVisible = visibleLocationNames.has(item.name)
        const isCurrent = currentLocation === item.name
        const color = locationColor(loc.type)

        let marker = existingMarkers.get(item.name)

        if (!marker) {
          // Create marker element
          const el = document.createElement("div")
          el.className = "novel-map-marker"
          _styleMarkerEl(el, color, item.radius, isVisible, isCurrent)

          // Label
          const label = document.createElement("div")
          label.className = "novel-map-label"
          label.textContent = item.name
          label.style.cssText = `
            position: absolute;
            left: 50%;
            transform: translateX(-50%);
            top: 100%;
            margin-top: 2px;
            font-size: 11px;
            white-space: nowrap;
            color: ${isVisible ? "#374151" : "#9ca3af"};
            pointer-events: none;
            text-shadow: 0 0 3px #fff, 0 0 3px #fff;
          `
          el.appendChild(label)

          marker = new maplibregl.Marker({ element: el, anchor: "center" })
            .setLngLat(lnglat)
            .addTo(map)

          // Click handler
          el.addEventListener("click", (e) => {
            e.stopPropagation()
            _showPopup(map, item, loc, lnglat, onLocationClick)
          })

          // Long-press drag support
          _setupLongPressDrag(el, marker, item, onLocationDragEnd)

          existingMarkers.set(item.name, marker)
        } else {
          // Update existing marker position and style
          marker.setLngLat(lnglat)
          const el = marker.getElement()
          _styleMarkerEl(el, color, item.radius, isVisible, isCurrent)
          const label = el.querySelector(".novel-map-label") as HTMLElement
          if (label) {
            label.style.color = isVisible ? "#374151" : "#9ca3af"
          }
        }
      }
    }, [
      mapLoaded,
      layout,
      locations,
      visibleLocationNames,
      currentLocation,
      onLocationClick,
      onLocationDragEnd,
    ])

    // ── Progressive label visibility by zoom ────────
    useEffect(() => {
      const map = mapRef.current
      if (!map || !mapLoaded) return

      const locMap = new Map(locations.map((l) => [l.name, l]))

      function updateLabelVisibility() {
        const zoom = map!.getZoom()
        // At zoom 14 (default), show labels for mention_count > 10
        // At zoom 16+, show all labels
        const threshold = zoom >= 16 ? 0 : zoom >= 15 ? 3 : 10

        markersRef.current.forEach((marker, name) => {
          const label = marker.getElement().querySelector(".novel-map-label") as HTMLElement
          if (!label) return
          const loc = locMap.get(name)
          const mentions = loc?.mention_count ?? 0
          label.style.display = mentions >= threshold ? "" : "none"
        })
      }

      map.on("zoom", updateLabelVisibility)
      updateLabelVisibility()

      return () => {
        map.off("zoom", updateLabelVisibility)
      }
    }, [mapLoaded, locations])

    // ── Fit to locations ────────────────────────────
    const fitToLocations = useCallback(() => {
      const map = mapRef.current
      if (!map || layout.length === 0) return

      const bounds = new maplibregl.LngLatBounds()
      for (const item of layout) {
        bounds.extend(toLngLat(item.x, item.y))
      }
      map.fitBounds(bounds, { padding: 60, maxZoom: 17 })
    }, [layout])

    useImperativeHandle(ref, () => ({ fitToLocations }), [fitToLocations])

    // Auto-fit when layout changes
    useEffect(() => {
      if (mapLoaded && layout.length > 0) {
        // Small delay to ensure markers are rendered
        const t = setTimeout(fitToLocations, 200)
        return () => clearTimeout(t)
      }
    }, [mapLoaded, layout, fitToLocations])

    return (
      <div ref={containerRef} className="h-full w-full" />
    )
  },
)

// ── Helpers ─────────────────────────────────────────

function _styleMarkerEl(
  el: HTMLElement,
  color: string,
  radius: number,
  isVisible: boolean,
  isCurrent: boolean,
) {
  const size = Math.max(10, Math.min(28, radius * 0.5))
  el.style.cssText = `
    width: ${size}px;
    height: ${size}px;
    border-radius: 50%;
    background-color: ${isCurrent ? "#f59e0b" : color};
    border: 2px solid ${isCurrent ? "#d97706" : "rgba(255,255,255,0.9)"};
    box-shadow: 0 1px 4px rgba(0,0,0,0.3);
    cursor: pointer;
    opacity: ${isVisible ? 1 : 0.25};
    transition: opacity 0.5s ease, background-color 0.3s ease;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  `
  if (isCurrent) {
    el.style.boxShadow = "0 0 0 4px rgba(245,158,11,0.3), 0 1px 4px rgba(0,0,0,0.3)"
  }
}

function _showPopup(
  map: maplibregl.Map,
  item: MapLayoutItem,
  loc: MapLocation,
  lnglat: [number, number],
  onLocationClick?: (name: string) => void,
) {
  const html = `
    <div style="font-size:13px; max-width:200px;">
      <div style="font-weight:600; margin-bottom:4px;">${loc.name}</div>
      <div style="color:#666; font-size:11px; margin-bottom:4px;">
        ${loc.type}${loc.parent ? ` · ${loc.parent}` : ""}
      </div>
      <div style="font-size:11px; color:#888; margin-bottom:6px;">
        出现 ${loc.mention_count} 章
      </div>
      <button id="popup-card-btn" style="
        font-size:11px; color:#3b82f6; background:none; border:none;
        cursor:pointer; padding:0; text-decoration:underline;
      ">查看卡片</button>
    </div>
  `

  const popup = new maplibregl.Popup({ offset: 15, closeButton: true })
    .setLngLat(lnglat)
    .setHTML(html)
    .addTo(map)

  // Attach click handler after DOM insertion
  setTimeout(() => {
    const btn = document.getElementById("popup-card-btn")
    if (btn && onLocationClick) {
      btn.addEventListener("click", () => {
        onLocationClick(loc.name)
        popup.remove()
      })
    }
  }, 0)
}

function _setupLongPressDrag(
  el: HTMLElement,
  marker: maplibregl.Marker,
  item: MapLayoutItem,
  onDragEnd?: (name: string, x: number, y: number) => void,
) {
  let pressTimer: ReturnType<typeof setTimeout> | null = null
  let isDragging = false

  el.addEventListener("pointerdown", (e) => {
    pressTimer = setTimeout(() => {
      isDragging = true
      marker.setDraggable(true)
      el.style.cursor = "grabbing"
      el.style.boxShadow = "0 0 0 4px rgba(59,130,246,0.4), 0 2px 8px rgba(0,0,0,0.3)"
    }, 500)
  })

  el.addEventListener("pointerup", () => {
    if (pressTimer) {
      clearTimeout(pressTimer)
      pressTimer = null
    }
    if (isDragging) {
      isDragging = false
      marker.setDraggable(false)
      el.style.cursor = "pointer"
      el.style.boxShadow = "0 1px 4px rgba(0,0,0,0.3)"

      // Get new position and convert back to canvas coords
      const lnglat = marker.getLngLat()
      const [x, y] = fromLngLat(lnglat.lng, lnglat.lat)
      if (onDragEnd) {
        onDragEnd(item.name, x, y)
      }
    }
  })

  el.addEventListener("pointercancel", () => {
    if (pressTimer) {
      clearTimeout(pressTimer)
      pressTimer = null
    }
    isDragging = false
    marker.setDraggable(false)
  })
}
