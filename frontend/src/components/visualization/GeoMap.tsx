import { useCallback, useEffect, useMemo, useRef } from "react"
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Marker,
  Polyline,
  Tooltip,
  Popup,
  useMap,
} from "react-leaflet"
import L from "leaflet"
import type { LatLngBoundsExpression } from "leaflet"
import "leaflet/dist/leaflet.css"
import type { MapLocation, TrajectoryPoint } from "@/api/types"

// ── Tile layer (no labels — clean canvas for novel markers) ──
const TILE_URL =
  "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png"
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>'

// ── Crosshair icon for editing mode ──────────────
const CROSSHAIR_ICON = L.divIcon({
  className: "",
  html: `<svg width="36" height="36" viewBox="0 0 36 36" xmlns="http://www.w3.org/2000/svg">
    <circle cx="18" cy="18" r="14" fill="none" stroke="#ef4444" stroke-width="2" stroke-dasharray="5 3" opacity="0.7"/>
    <line x1="18" y1="1" x2="18" y2="35" stroke="#ef4444" stroke-width="1.5" opacity="0.6"/>
    <line x1="1" y1="18" x2="35" y2="18" stroke="#ef4444" stroke-width="1.5" opacity="0.6"/>
    <circle cx="18" cy="18" r="4" fill="#ef4444" opacity="0.8"/>
  </svg>`,
  iconSize: [36, 36],
  iconAnchor: [18, 18],
})

// ── Location color by type ────────────────────────
function locationColor(type: string): string {
  const t = type.toLowerCase()
  if (t.includes("国") || t.includes("域") || t.includes("界") || t.includes("洲"))
    return "#3b82f6"
  if (t.includes("城") || t.includes("镇") || t.includes("都") || t.includes("村"))
    return "#10b981"
  if (t.includes("山") || t.includes("洞") || t.includes("谷") || t.includes("林"))
    return "#84cc16"
  if (t.includes("海") || t.includes("河") || t.includes("湖")) return "#06b6d4"
  return "#6b7280"
}

// ── Props ─────────────────────────────────────────
export interface GeoMapProps {
  locations: MapLocation[]
  geoCoords: Record<string, { lat: number; lng: number }>
  trajectoryPoints?: TrajectoryPoint[]
  currentLocation?: string | null
  focusLocation?: string | null
  editingLocation?: string | null
  onLocationClick?: (name: string) => void
  onEditLocation?: (name: string) => void
  onEditDragEnd?: (name: string, lat: number, lng: number) => void
  onEditCancel?: () => void
}

// ── Auto fit bounds on mount / data change ────────
function FitBounds({
  coords,
}: {
  coords: Record<string, { lat: number; lng: number }>
}) {
  const map = useMap()
  const fitted = useRef(false)

  useEffect(() => {
    const entries = Object.values(coords)
    if (entries.length === 0 || fitted.current) return

    const bounds: LatLngBoundsExpression = entries.map(
      (c) => [c.lat, c.lng] as [number, number],
    )
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 10 })
    fitted.current = true
  }, [coords, map])

  return null
}

// ── Fly to a specific location when it changes ───
function FlyToLocation({
  locationName,
  geoCoords,
}: {
  locationName: string | null
  geoCoords: Record<string, { lat: number; lng: number }>
}) {
  const map = useMap()
  const prevName = useRef<string | null>(null)

  useEffect(() => {
    if (!locationName) {
      prevName.current = null
      return
    }
    if (locationName === prevName.current) return
    prevName.current = locationName
    const coord = geoCoords[locationName]
    if (!coord) return
    map.flyTo([coord.lat, coord.lng], Math.max(map.getZoom(), 6), {
      duration: 1,
    })
  }, [locationName, geoCoords, map])

  return null
}

// ── Component ─────────────────────────────────────
export function GeoMap({
  locations,
  geoCoords,
  trajectoryPoints,
  currentLocation,
  focusLocation,
  editingLocation,
  onLocationClick,
  onEditLocation,
  onEditDragEnd,
  onEditCancel,
}: GeoMapProps) {
  // Only render locations that have geo coordinates
  const geoLocations = useMemo(
    () => locations.filter((loc) => geoCoords[loc.name]),
    [locations, geoCoords],
  )

  // Trajectory polyline coordinates
  const trajectoryLatLngs = useMemo(() => {
    if (!trajectoryPoints || trajectoryPoints.length === 0) return []
    const coords: [number, number][] = []
    for (const tp of trajectoryPoints) {
      const gc = geoCoords[tp.location]
      if (gc) coords.push([gc.lat, gc.lng])
    }
    return coords
  }, [trajectoryPoints, geoCoords])

  // Current location highlight
  const currentCoord = currentLocation ? geoCoords[currentLocation] : null

  // Focus/fly target: editing location takes priority over focusLocation
  const flyTarget = editingLocation ?? focusLocation ?? null

  // Focus location for pulse
  const focusCoord = focusLocation ? geoCoords[focusLocation] ?? null : null

  // Default center (world center)
  const defaultCenter: [number, number] = [20, 0]

  // Handle drag end on editable marker
  const handleDragEnd = useCallback(
    (name: string, e: L.DragEndEvent) => {
      const marker = e.target as L.Marker
      const latlng = marker.getLatLng()
      onEditDragEnd?.(name, latlng.lat, latlng.lng)
    },
    [onEditDragEnd],
  )

  return (
    <div className="h-full w-full" style={{ position: "relative", zIndex: 0 }}>
      {/* Edit mode hint bar */}
      {editingLocation && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-[1000] rounded-full border border-red-300 bg-red-50 px-4 py-1.5 shadow-lg flex items-center gap-2">
          <span className="text-xs text-red-700">
            拖拽十字丝移动「{editingLocation}」的位置
          </span>
          <button
            className="text-xs text-red-500 hover:text-red-700 underline"
            onClick={onEditCancel}
          >
            取消
          </button>
        </div>
      )}

      <MapContainer
        center={defaultCenter}
        zoom={2}
        style={{ height: "100%", width: "100%" }}
        zoomControl={true}
        scrollWheelZoom={true}
      >
        <TileLayer url={TILE_URL} attribution={TILE_ATTR} />
        <FitBounds coords={geoCoords} />
        <FlyToLocation locationName={flyTarget} geoCoords={geoCoords} />

        {/* Location markers */}
        {geoLocations.map((loc) => {
          const gc = geoCoords[loc.name]!
          const mention = loc.mention_count ?? 1
          const radius = Math.max(5, Math.min(20, 4 + Math.sqrt(mention) * 2))
          const color = locationColor(loc.type ?? "")
          const isCurrent = loc.name === currentLocation
          const isFocused = loc.name === focusLocation
          const isEditing = loc.name === editingLocation

          // Editing marker: draggable Marker with crosshair icon
          if (isEditing) {
            return (
              <Marker
                key={`${loc.name}:edit`}
                position={[gc.lat, gc.lng]}
                icon={CROSSHAIR_ICON}
                draggable={true}
                eventHandlers={{
                  dragend: (e) => handleDragEnd(loc.name, e),
                }}
              >
                <Tooltip direction="top" offset={[0, -20]} permanent>
                  <span className="text-xs font-bold text-red-600">{loc.name}</span>
                </Tooltip>
              </Marker>
            )
          }

          return (
            <CircleMarker
              key={`${loc.name}${isFocused ? ":f" : ""}`}
              center={[gc.lat, gc.lng]}
              radius={isCurrent || isFocused ? radius + 4 : radius}
              pathOptions={{
                color: isCurrent ? "#f59e0b" : isFocused ? "#ef4444" : color,
                fillColor: color,
                fillOpacity: editingLocation ? 0.3 : 0.7,
                weight: isCurrent || isFocused ? 3 : 1.5,
              }}
              eventHandlers={{
                click: () => onLocationClick?.(loc.name),
              }}
            >
              <Tooltip direction="top" offset={[0, -8]} permanent={mention >= 5 || isFocused}>
                <span className="text-xs font-medium">{loc.name}</span>
              </Tooltip>
              {!editingLocation && (
                <Popup>
                  <div className="min-w-[120px]">
                    <div className="font-semibold">{loc.name}</div>
                    {loc.type && (
                      <div className="text-xs text-gray-500">{loc.type}</div>
                    )}
                    {loc.parent && (
                      <div className="text-xs text-gray-400">{loc.parent}</div>
                    )}
                    <div className="text-xs text-gray-400">
                      提及 {loc.mention_count ?? 0} 次
                    </div>
                    <div className="mt-1.5 flex gap-2">
                      <button
                        className="text-xs text-blue-600 hover:underline"
                        onClick={(e) => {
                          e.stopPropagation()
                          onLocationClick?.(loc.name)
                        }}
                      >
                        查看详情
                      </button>
                      <button
                        className="text-xs text-red-500 hover:underline"
                        onClick={(e) => {
                          e.stopPropagation()
                          onEditLocation?.(loc.name)
                        }}
                      >
                        编辑位置
                      </button>
                    </div>
                  </div>
                </Popup>
              )}
            </CircleMarker>
          )
        })}

        {/* Trajectory line */}
        {trajectoryLatLngs.length >= 2 && (
          <Polyline
            positions={trajectoryLatLngs}
            pathOptions={{
              color: "#f59e0b",
              weight: 3,
              opacity: 0.8,
              dashArray: "8 4",
            }}
          />
        )}

        {/* Current location pulse (trajectory playback) */}
        {currentCoord && (
          <CircleMarker
            center={[currentCoord.lat, currentCoord.lng]}
            radius={25}
            pathOptions={{
              color: "#f59e0b",
              fillColor: "#f59e0b",
              fillOpacity: 0.15,
              weight: 2,
              dashArray: "4 4",
            }}
          />
        )}

        {/* Focus location pulse (click-to-navigate) */}
        {focusCoord && !currentCoord && !editingLocation && (
          <CircleMarker
            center={[focusCoord.lat, focusCoord.lng]}
            radius={25}
            pathOptions={{
              color: "#ef4444",
              fillColor: "#ef4444",
              fillOpacity: 0.12,
              weight: 2,
              dashArray: "4 4",
            }}
          />
        )}
      </MapContainer>
    </div>
  )
}
