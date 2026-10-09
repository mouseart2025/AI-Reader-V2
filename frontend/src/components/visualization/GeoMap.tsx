import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  MapContainer,
  GeoJSON as LeafletGeoJSON,
  CircleMarker,
  Marker,
  Polyline,
  Tooltip,
  Popup,
  useMap,
  useMapEvents,
} from "react-leaflet"
import L from "leaflet"
import type { LatLngBoundsExpression } from "leaflet"
import type { FeatureCollection } from "geojson"
import "leaflet/dist/leaflet.css"
import type { MapLocation, TrajectoryPoint } from "@/api/types"
import chinaProvinces from "@/assets/china_provinces.json"
import worldCountries from "@/assets/world_countries.json"

// ── 底图:自绘素净画布 ──
// 现代栅格底图(CARTO 要 key / Esri 英文标注+画法合规风险 / 高德有铁路与
// 现代道路,时空错乱)都不适合小说地图。改为本地渲染中国省级轮廓
// (阿里 DataV 边界数据,依据自然资源部标准地图,台湾省/港澳齐全),
// 无现代基础设施、离线可用。坐标系已用 6 座 WGS-84 城市做点面校验,
// 与 GeoNames 标记同一基准,无需 GCJ-02 转换。
// 世界范围的小说(卡拉马佐夫兄弟、海底两万里)地点遍布全球,只画中国
// 轮廓会让外国地点飘在"海"里 → 叠加 Natural Earth 110m 国家轮廓作
// 世界陆地底图(公有领域,离线可用),中国部分剔除以免与省级轮廓重边。
const CHINA_GEOJSON = chinaProvinces as unknown as FeatureCollection
const WORLD_GEOJSON = {
  ...worldCountries,
  features: (worldCountries as unknown as FeatureCollection).features.filter(
    (f) => f.properties?.name !== "China",
  ),
} as unknown as FeatureCollection
const MAP_ATTRIBUTION = "边界数据 © 阿里 DataV（依据自然资源部标准地图）"
const WORLD_ATTRIBUTION = "世界陆地 © Natural Earth"

// 画布配色:浅海色面板 + 暖纸色陆地 + 低饱和描边,不抢小说标记
const LAND_STYLE: L.PathOptions = {
  color: "#a8b4bd",
  weight: 1,
  fillColor: "#f7f5f0",
  fillOpacity: 1,
}

// ── 世界陆地 + 中国省级轮廓(素净画布底图) ──
function ProvinceLayer() {
  const zoom = useZoomLevel()
  const map = useMap()
  useEffect(() => {
    map.attributionControl?.addAttribution(MAP_ATTRIBUTION)
    map.attributionControl?.addAttribution(WORLD_ATTRIBUTION)
  }, [map])
  return (
    <>
      <LeafletGeoJSON
        data={WORLD_GEOJSON}
        style={() => LAND_STYLE}
        interactive={false}
      />
      <LeafletGeoJSON
        data={CHINA_GEOJSON}
        style={() => LAND_STYLE}
        interactive={false}
      />
      {/* 省名标注:仅低缩放级别显示,高缩放让位给小说地点 */}
      {zoom <= 5 &&
        CHINA_GEOJSON.features.map((f) => {
          const name = f.properties?.name as string | undefined
          const centroid = f.properties?.centroid as [number, number] | undefined
          if (!name || !centroid) return null
          return (
            <Marker
              key={`prov-${name}`}
              position={[centroid[1], centroid[0]]}
              icon={L.divIcon({
                className: "",
                html: `<span style="font-size:11px;color:#7d8a95;white-space:nowrap">${name}</span>`,
                iconSize: [0, 0],
              })}
              interactive={false}
              keyboard={false}
            />
          )
        })}
    </>
  )
}

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

// ── Zoom level tracker ────────────────────────────
function useZoomLevel(): number {
  const map = useMap()
  const [zoom, setZoom] = useState(map.getZoom())
  useMapEvents({
    zoomend: () => setZoom(map.getZoom()),
  })
  return zoom
}

// Minimum mention_count for a label to show permanently at each zoom level.
// Geographic mode: locations are spread across the globe, so thresholds are
// lower than fantasy-map mode — even at moderate zoom, visible markers are few.
function labelMentionThreshold(zoom: number): number {
  if (zoom >= 5) return 1   // show all labels at region level
  if (zoom >= 4) return 2   // 2+ at continent level
  if (zoom >= 3) return 3   // 3+ at multi-continent
  return 5                   // only major locations at full world zoom
}

// ── Zoom-aware location markers ───────────────────
function ZoomAwareMarkers({
  geoLocations,
  geoCoords,
  currentLocation,
  focusLocation,
  editingLocation,
  onLocationClick,
  onEditLocation,
  handleDragEnd,
}: {
  geoLocations: MapLocation[]
  geoCoords: Record<string, { lat: number; lng: number }>
  currentLocation: string | null
  focusLocation: string | null
  editingLocation: string | null
  onLocationClick?: (name: string) => void
  onEditLocation?: (name: string) => void
  handleDragEnd: (name: string, e: L.DragEndEvent) => void
}) {
  const zoom = useZoomLevel()
  const minMention = labelMentionThreshold(zoom)

  return (
    <>
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

        const showLabel = mention >= minMention || isFocused

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
            <Tooltip key={showLabel ? "p" : "h"} direction="top" offset={[0, -8]} permanent={showLabel}>
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
    </>
  )
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
        style={{ height: "100%", width: "100%", background: "#dbe4ea" }}
        zoomControl={true}
        scrollWheelZoom={true}
      >
        <ProvinceLayer />
        <FitBounds coords={geoCoords} />
        <FlyToLocation locationName={flyTarget} geoCoords={geoCoords} />

        {/* Location markers — rendered inside a zoom-aware wrapper */}
        <ZoomAwareMarkers
          geoLocations={geoLocations}
          geoCoords={geoCoords}
          currentLocation={currentLocation ?? null}
          focusLocation={focusLocation ?? null}
          editingLocation={editingLocation ?? null}
          onLocationClick={onLocationClick}
          onEditLocation={onEditLocation}
          handleDragEnd={handleDragEnd}
        />

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
