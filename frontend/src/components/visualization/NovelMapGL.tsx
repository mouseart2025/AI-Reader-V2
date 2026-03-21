/**
 * NovelMapGL — WebGL 地图渲染组件 (Phase 1)
 *
 * 架构：MapLibre GL 交互层 + Canvas 底图纹理 + CSS 纸张/暗角叠加
 * 功能：语义缩放、SDF 区域场、Hillshade、点击联动、数据热更新
 */

import { useEffect, useRef, useMemo } from "react"
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import type { MapLocation, MapLayoutItem, TrajectoryPoint } from "@/api/types"
import {
  compositeBaseMap,
  generateParchmentTexture,
  getDefaultStyle,
  tierToNum,
  tierToMinZoom,
  resolveTerrainType,
} from "@/lib/mapRenderer"
import type { SDFLocation, HillshadeLocation } from "@/lib/mapRenderer"

export interface NovelMapGLProps {
  locations: MapLocation[]
  layout: MapLayoutItem[]
  canvasSize?: { width: number; height: number }
  trajectoryPoints?: TrajectoryPoint[]
  onLocationClick?: (name: string) => void
  onLocationDragEnd?: (name: string, x: number, y: number) => void
}

const LNG_RANGE = 60
const LAT_RANGE = 40

function canvasToLngLat(x: number, y: number, cw: number, ch: number): [number, number] {
  return [(x / cw - 0.5) * LNG_RANGE, (0.5 - y / ch) * LAT_RANGE]
}

/** 数据指纹，用于检测是否需要重建地图（含所有名称的哈希） */
function dataKey(locs: MapLocation[], lay: MapLayoutItem[]): string {
  // 包含所有名称确保层切换时一定触发重建
  const names = locs.map(l => l.name).join(",")
  let h = 0
  for (let i = 0; i < names.length; i++) h = ((h << 5) - h + names.charCodeAt(i)) | 0
  return `${locs.length}:${lay.length}:${h}`
}

export function NovelMapGL({
  locations,
  layout,
  canvasSize,
  trajectoryPoints,
  onLocationClick,
  onLocationDragEnd,
}: NovelMapGLProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const builtKeyRef = useRef("")

  // 稳定回调引用
  const callbacksRef = useRef({ onLocationClick, onLocationDragEnd })
  callbacksRef.current = { onLocationClick, onLocationDragEnd }

  // 自适应画布尺寸：使用布局坐标的实际范围（解决非主世界层的坐标空间不匹配）
  const { cw, ch } = useMemo(() => {
    if (layout.length === 0) return { cw: canvasSize?.width ?? 1000, ch: canvasSize?.height ?? 1000 }
    let maxX = 0, maxY = 0
    for (const item of layout) {
      if (item.x > maxX) maxX = item.x
      if (item.y > maxY) maxY = item.y
    }
    // 加 10% padding，确保边缘点不贴边
    const pad = 1.1
    const effectiveCw = Math.max(maxX * pad, 100)
    const effectiveCh = Math.max(maxY * pad, 100)
    // 如果布局坐标接近 canvasSize 则使用 canvasSize（主世界场景）
    const propCw = canvasSize?.width ?? 1000
    const propCh = canvasSize?.height ?? 1000
    if (effectiveCw > propCw * 0.5) return { cw: propCw, ch: propCh }
    return { cw: effectiveCw, ch: effectiveCh }
  }, [layout, canvasSize])
  const mapStyle = getDefaultStyle()

  // 坐标映射
  const layoutMap = useMemo(() => {
    const m = new Map<string, MapLayoutItem>()
    for (const item of layout) m.set(item.name, item)
    return m
  }, [layout])

  const maxTier = useMemo(() => {
    let mt = 1
    for (const loc of locations) {
      const t = tierToNum(loc.tier)
      if (t > mt) mt = t
    }
    return mt
  }, [locations])

  // GeoJSON features
  const features = useMemo((): GeoJSON.Feature[] =>
    locations
      .filter((loc) => layoutMap.has(loc.name))
      .map((loc) => {
        const li = layoutMap.get(loc.name)!
        const [lng, lat] = canvasToLngLat(li.x, li.y, cw, ch)
        return {
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [lng, lat] },
          properties: {
            name: loc.name,
            type: loc.type,
            terrainType: resolveTerrainType(loc.icon, loc.tier, loc.type),
            tier: tierToNum(loc.tier),
            mentions: loc.mention_count,
          },
        }
      }),
    [locations, layoutMap, cw, ch],
  )

  // ── 主效果：构建/重建 MapLibre ──
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const key = dataKey(locations, layout)

    // 如果数据没变且地图已存在，只更新 GeoJSON
    if (mapRef.current && builtKeyRef.current === key) {
      const src = mapRef.current.getSource("locations") as maplibregl.GeoJSONSource | undefined
      if (src) src.setData({ type: "FeatureCollection", features })
      return
    }

    // 需要重建（首次或数据结构变化）
    if (mapRef.current) {
      mapRef.current.remove()
      mapRef.current = null
    }

    // 等容器有非零尺寸
    let cancelled = false
    let rafId = requestAnimationFrame(function tryInit() {
      if (cancelled) return
      const rect = el.getBoundingClientRect()
      if (rect.width < 10 || rect.height < 10) {
        rafId = requestAnimationFrame(tryInit)
        return
      }
      buildMap(el, key)
    })

    return () => {
      cancelled = true
      cancelAnimationFrame(rafId)
      if (mapRef.current) {
        mapRef.current.remove()
        mapRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locations, layout, canvasSize])

  // ── 轨迹数据更新 ──
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return

    const src = map.getSource("trajectory") as maplibregl.GeoJSONSource | undefined
    if (!trajectoryPoints || trajectoryPoints.length < 2) {
      if (src) src.setData({ type: "FeatureCollection", features: [] })
      return
    }

    const coords: [number, number][] = []
    for (const tp of trajectoryPoints) {
      const li = layoutMap.get(tp.location)
      if (li) coords.push(canvasToLngLat(li.x, li.y, cw, ch))
    }
    if (coords.length < 2) return

    const lineData: GeoJSON.Feature = {
      type: "Feature",
      geometry: { type: "LineString", coordinates: coords },
      properties: {},
    }
    if (src) {
      src.setData(lineData)
    } else {
      map.addSource("trajectory", { type: "geojson", data: lineData })
      map.addLayer({
        id: "traj-line", type: "line", source: "trajectory",
        paint: { "line-color": "#d4763a", "line-width": 2.5, "line-opacity": 0.7, "line-dasharray": [2, 1] },
      }, map.getLayer("t1-c") ? "t1-c" : undefined)
    }
  }, [trajectoryPoints, layoutMap, cw, ch])

  function buildMap(container: HTMLElement, key: string) {
    // ── 预渲染底图纹理 ──
    const sdfLocs: SDFLocation[] = []
    const hsLocs: HillshadeLocation[] = []
    const maxMention = Math.max(1, ...locations.map(l => l.mention_count))
    for (const loc of locations) {
      const li = layoutMap.get(loc.name)
      if (!li) continue
      const nx = li.x / cw, ny = li.y / ch
      const tierNum = tierToNum(loc.tier)
      const mentionRatio = loc.mention_count / maxMention
      const terrainType = resolveTerrainType(loc.icon, loc.tier, loc.type)
      sdfLocs.push({
        x: nx, y: ny,
        radius: 0.025 + mentionRatio * 0.06 + (1 / tierNum) * 0.04,
        type: terrainType,
      })
      hsLocs.push({ x: nx, y: ny, type: terrainType })
    }

    // 动态纹理分辨率：按画布尺寸缩放，保持纵横比
    const maxDim = Math.max(cw, ch)
    const texBase = Math.min(2048, Math.max(1024, Math.round(maxDim / 4)))
    const aspect = cw / ch
    const texW = texBase
    const texH = Math.round(texBase / aspect)
    const composite = compositeBaseMap(sdfLocs, hsLocs, mapStyle, texW, texH)
    console.log(`[NovelMapGL] 底图: ${texW}×${texH}, ${composite.renderTimeMs}ms, ${(composite.dataUrl.length / 1024).toFixed(0)}KB, ${sdfLocs.length} locs`)

    const halfLng = LNG_RANGE / 2, halfLat = LAT_RANGE / 2

    // ── MapLibre ──
    const map = new maplibregl.Map({
      container,
      style: {
        version: 8,
        sources: {},
        layers: [
          { id: "bg", type: "background", paint: { "background-color": mapStyle.background } },
        ],
      },
      center: [0, 0],
      zoom: 2,
      minZoom: 0.5,
      maxZoom: 10,
      maxBounds: [[-(halfLng + 10), -(halfLat + 10)], [halfLng + 10, halfLat + 10]],
      attributionControl: false,
    })
    mapRef.current = map
    builtKeyRef.current = key

    map.on("load", () => {
      // 底图
      map.addSource("basemap", {
        type: "image",
        url: composite.dataUrl,
        coordinates: [
          [-halfLng, halfLat], [halfLng, halfLat],
          [halfLng, -halfLat], [-halfLng, -halfLat],
        ],
      })
      map.addLayer({ id: "basemap-raster", type: "raster", source: "basemap", paint: { "raster-opacity": 1 } })

      // GeoJSON
      map.addSource("locations", { type: "geojson", data: { type: "FeatureCollection", features } })

      // 语义缩放图层
      // 小层（< 200 features）禁用 minzoom，让所有地点在初始视图即可见
      const isSmallLayer = features.length < 200
      for (let tier = 1; tier <= maxTier; tier++) {
        const minzoom = isSmallLayer ? 0 : tierToMinZoom(tier, maxTier)
        const fontSize = mapStyle.label.tierSizes[tier - 1] ?? 10
        const circleR = Math.max(4, 14 - tier * 2)

        map.addLayer({
          id: `t${tier}-c`, type: "circle", source: "locations",
          filter: ["==", ["get", "tier"], tier],
          minzoom,
          paint: {
            "circle-radius": circleR,
            "circle-color": [
              "match", ["get", "terrainType"],
              "realm", "#b4963a", "kingdom", "#82aa5f", "city", "#af8c55",
              "town", "#b9a56e", "mountain", "#786040", "hill", "#9b8c5f",
              "forest", "#469037", "water", "#4182be", "ocean", "#2d64b4",
              "desert", "#c8a046", "valley", "#6ea555", "plain", "#b9aa78",
              "org", "#9170be", "cave", "#826e55", "temple", "#a078b4",
              "palace", "#aa8240", "island", "#64a08c", "sacred", "#aa8cc8",
              "ruins", "#968264",
              "#b4963a",
            ] as maplibregl.ExpressionSpecification,
            "circle-opacity": 0.9,
            "circle-stroke-width": tier <= 2 ? 2 : 1,
            "circle-stroke-color": "#8b7355",
          },
        })
        map.addLayer({
          id: `t${tier}-l`, type: "symbol", source: "locations",
          filter: ["==", ["get", "tier"], tier],
          minzoom,
          layout: {
            "text-field": ["get", "name"],
            "text-size": fontSize,
            "text-font": ["Open Sans Regular"],
            "text-offset": [0, tier <= 2 ? 1.4 : 1.1],
            "text-allow-overlap": false,
            ...(tier <= 1 ? { "text-letter-spacing": 0.15 } : {}),
          },
          paint: {
            "text-color": mapStyle.label.color,
            "text-halo-color": mapStyle.label.haloColor,
            "text-halo-width": tier <= 2 ? 2 : 1.5,
          },
        })
      }

      // 交互：点击地点
      for (let tier = 1; tier <= maxTier; tier++) {
        const lid = `t${tier}-c`
        map.on("click", lid, (e) => {
          const name = e.features?.[0]?.properties?.name
          if (name) callbacksRef.current.onLocationClick?.(name)
        })
        map.on("mouseenter", lid, () => { map.getCanvas().style.cursor = "pointer" })
        map.on("mouseleave", lid, () => { map.getCanvas().style.cursor = "" })
      }

      // Popup on hover
      const popup = new maplibregl.Popup({
        closeButton: false, closeOnClick: false,
        offset: 15, className: "novel-map-popup",
      })
      for (let tier = 1; tier <= maxTier; tier++) {
        map.on("mouseenter", `t${tier}-c`, (e) => {
          const props = e.features?.[0]?.properties
          if (!props) return
          const coords = (e.features![0].geometry as GeoJSON.Point).coordinates.slice() as [number, number]
          popup.setLngLat(coords)
            .setHTML(`<b>${props.name}</b><br/><span style="opacity:0.7">${props.type} · 提及${props.mentions}次</span>`)
            .addTo(map)
        })
        map.on("mouseleave", `t${tier}-c`, () => popup.remove())
      }

      // Auto-fit bounds to features
      if (features.length > 0) {
        const bounds = new maplibregl.LngLatBounds()
        for (const f of features) {
          const coords = (f.geometry as GeoJSON.Point).coordinates as [number, number]
          bounds.extend(coords)
        }
        map.fitBounds(bounds, { padding: 60, maxZoom: 8 })
      }

      console.log(`[NovelMapGL] ready: ${features.length} features, maxTier=${maxTier}, smallLayer=${isSmallLayer}`)
    })

    map.on("error", (e) => {
      console.error("[NovelMapGL] error:", e.error?.message || e)
    })

    // 羊皮纸纹理
    generateParchmentTexture(mapStyle).then((dataUrl) => {
      if (!dataUrl) return
      const pEl = container.parentElement?.querySelector("[data-parchment]") as HTMLDivElement | null
      if (pEl) pEl.style.backgroundImage = `url(${dataUrl})`
    })
  }

  return (
    <div className="w-full h-full relative">
      <div ref={containerRef} className="w-full h-full" />

      {/* 羊皮纸纹理 */}
      <div
        data-parchment=""
        className="absolute inset-0 pointer-events-none"
        style={{
          zIndex: 1,
          backgroundSize: "512px 512px",
          backgroundRepeat: "repeat",
          mixBlendMode: "multiply",
          opacity: mapStyle.paper.noiseStrength,
        }}
      />

      {/* Vignette */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          zIndex: 2,
          background: `radial-gradient(ellipse at center, transparent ${mapStyle.vignette.start}%, ${mapStyle.vignette.color} 100%)`,
        }}
      />
    </div>
  )
}
