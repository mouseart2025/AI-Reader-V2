/**
 * Convex-hull based territory generation for nested parent-child containment.
 *
 * Replaces Voronoi flat-partition with convex hulls that naturally nest:
 * a parent hull wraps around all its descendants, showing containment visually.
 *
 * Algorithm:
 * 1. Build parent→children index, find parents with positioned descendants
 * 2. For each parent: collect all descendant positions → convex hull
 * 3. Expand hull outward by tier-dependent padding (bisector offset)
 * 4. Handle degenerate cases: 1 point → regular polygon, 2 points → capsule
 * 5. Sort by level ascending (outermost first) for correct layer ordering
 */

import type { MapLocation, MapLayoutItem } from "@/api/types"
import type { Point } from "./edgeDistortion"

export interface Territory {
  name: string        // parent location name
  polygon: Point[]    // hull vertices (expanded)
  color: string       // fill color (by type)
  level: number       // nesting depth (0 = outermost)
  children: string[]  // direct child location names
  tier: string        // parent location's tier
}

// ── Tier padding (px at 1600px canvas width) ─────────────
const TIER_PADDING: Record<string, number> = {
  continent: 80,
  kingdom: 60,
  region: 45,
  city: 35,
  site: 25,
  building: 18,
}

const DEFAULT_PADDING = 30

/**
 * Generate nested convex-hull territories for locations with children.
 */
export function generateHullTerritories(
  locations: MapLocation[],
  layout: MapLayoutItem[],
  canvasSize: { width: number; height: number },
): Territory[] {
  // ── Step 1: Build indexes ──
  const layoutMap = new Map<string, MapLayoutItem>()
  for (const item of layout) layoutMap.set(item.name, item)

  const locMap = new Map<string, MapLocation>()
  for (const loc of locations) locMap.set(loc.name, loc)

  const childrenMap = new Map<string, string[]>()
  const childToParent = new Map<string, string>()

  for (const loc of locations) {
    if (loc.parent && locMap.has(loc.parent)) {
      childToParent.set(loc.name, loc.parent)
      const siblings = childrenMap.get(loc.parent) ?? []
      siblings.push(loc.name)
      childrenMap.set(loc.parent, siblings)
    }
  }

  // ── Step 2: Find parents with at least 1 positioned descendant ──
  const territories: Territory[] = []
  const scale = canvasSize.width / 1600

  // Count total positioned (non-portal) locations for coverage filter
  const totalPositioned = layout.filter((item) => !item.is_portal).length

  for (const [parentName] of childrenMap) {
    const points = collectDescendantPositions(parentName, childrenMap, layoutMap)
    if (points.length === 0) continue

    // Skip tiny territories — need at least 3 descendants for a meaningful hull
    if (points.length < 3) continue

    // Skip hulls that cover too much of the map — they add noise, not clarity.
    // When a hull wraps >50% of all positioned locations, its edges just cut
    // diagonally through the map without conveying useful containment.
    if (totalPositioned > 0 && points.length / totalPositioned > 0.5) continue

    // Also include the parent's own position if it has one
    const parentItem = layoutMap.get(parentName)
    if (parentItem) {
      points.push([parentItem.x, parentItem.y])
    }

    // ── Step 3: Convex hull + expand ──
    const loc = locMap.get(parentName)
    const tier = loc?.tier ?? "city"
    const basePadding = TIER_PADDING[tier] ?? DEFAULT_PADDING
    const padding = basePadding * scale

    let hull = convexHull(points)

    // Degenerate cases
    if (hull.length === 0) continue
    if (hull.length === 1) {
      hull = regularPolygon(hull[0], padding, 12)
    } else if (hull.length === 2 || isCollinear(hull)) {
      hull = capsule(hull, padding)
    } else {
      hull = expandConvexHull(hull, padding)
    }

    // Nesting level
    const level = getAncestorChain(parentName, childToParent).length

    // Color
    const color = territoryColor(loc?.type ?? "")

    // Direct children
    const children = (childrenMap.get(parentName) ?? []).slice()

    territories.push({ name: parentName, polygon: hull, color, level, children, tier })
  }

  // ── Step 4: Sort by level ascending + apply render depth limit ──
  territories.sort((a, b) => a.level - b.level)

  const maxLevel = getMaxRenderLevel(territories.length)
  return territories.filter((t) => t.level <= maxLevel)
}

// ── Collect descendant positions (recursive, with cycle detection) ──

function collectDescendantPositions(
  name: string,
  childrenMap: Map<string, string[]>,
  layoutMap: Map<string, MapLayoutItem>,
  visited?: Set<string>,
): Point[] {
  const seen = visited ?? new Set<string>()
  if (seen.has(name)) return []
  seen.add(name)

  const points: Point[] = []
  const children = childrenMap.get(name) ?? []

  for (const child of children) {
    const item = layoutMap.get(child)
    if (item) points.push([item.x, item.y])
    // Recurse into child's children
    points.push(...collectDescendantPositions(child, childrenMap, layoutMap, seen))
  }

  return points
}

// ── Convex Hull — Andrew's Monotone Chain ──

function convexHull(points: Point[]): Point[] {
  // Deduplicate (epsilon = 0.1)
  const unique: Point[] = []
  const seen = new Set<string>()
  for (const [x, y] of points) {
    const key = `${Math.round(x * 10)},${Math.round(y * 10)}`
    if (!seen.has(key)) {
      seen.add(key)
      unique.push([x, y])
    }
  }

  if (unique.length <= 1) return unique
  if (unique.length === 2) return unique

  // Sort by (x, y) lexicographically
  unique.sort((a, b) => a[0] - b[0] || a[1] - b[1])

  const n = unique.length

  // Lower hull
  const lower: Point[] = []
  for (let i = 0; i < n; i++) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], unique[i]) <= 0) {
      lower.pop()
    }
    lower.push(unique[i])
  }

  // Upper hull
  const upper: Point[] = []
  for (let i = n - 1; i >= 0; i--) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], unique[i]) <= 0) {
      upper.pop()
    }
    upper.push(unique[i])
  }

  // Remove last point of each half (it's the first of the other)
  lower.pop()
  upper.pop()

  return [...lower, ...upper]
}

function cross(o: Point, a: Point, b: Point): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
}

// ── Polygon Expansion — Bisector Offset ──

function expandConvexHull(hull: Point[], padding: number): Point[] {
  const n = hull.length
  if (n < 3) return hull

  const result: Point[] = []

  for (let i = 0; i < n; i++) {
    const prev = hull[(i - 1 + n) % n]
    const curr = hull[i]
    const next = hull[(i + 1) % n]

    // Edge vectors
    const e1x = curr[0] - prev[0]
    const e1y = curr[1] - prev[1]
    const e2x = next[0] - curr[0]
    const e2y = next[1] - curr[1]

    // Outward normals (rotate 90 CW for convex hull wound CCW)
    const len1 = Math.sqrt(e1x * e1x + e1y * e1y) || 1
    const len2 = Math.sqrt(e2x * e2x + e2y * e2y) || 1

    const n1x = e1y / len1
    const n1y = -e1x / len1
    const n2x = e2y / len2
    const n2y = -e2x / len2

    // Bisector
    let bx = n1x + n2x
    let by = n1y + n2y
    const bLen = Math.sqrt(bx * bx + by * by)
    if (bLen < 1e-8) {
      // Parallel edges — just offset along n1
      result.push([curr[0] + n1x * padding, curr[1] + n1y * padding])
      continue
    }
    bx /= bLen
    by /= bLen

    // cosHalf = dot(bisector, n1)
    const cosHalf = bx * n1x + by * n1y
    const offset = cosHalf < 0.1 ? padding * 2 : padding / cosHalf

    result.push([curr[0] + bx * offset, curr[1] + by * offset])
  }

  return result
}

// ── Degenerate: Regular polygon (for single-point case) ──

function regularPolygon(center: Point, radius: number, sides: number): Point[] {
  const result: Point[] = []
  for (let i = 0; i < sides; i++) {
    const angle = (2 * Math.PI * i) / sides - Math.PI / 2
    result.push([
      center[0] + radius * Math.cos(angle),
      center[1] + radius * Math.sin(angle),
    ])
  }
  return result
}

// ── Degenerate: Capsule (for 2-point / collinear case) ──

function capsule(points: Point[], padding: number): Point[] {
  // Use first and last point of the hull
  const p0 = points[0]
  const p1 = points[points.length - 1]

  const dx = p1[0] - p0[0]
  const dy = p1[1] - p0[1]

  // Build capsule: semicircle around p0, straight to p1, semicircle around p1
  const result: Point[] = []
  const semiSegments = 8

  // Semicircle around p0 (from perpendicular-left to perpendicular-right, going away from p1)
  const baseAngle0 = Math.atan2(-dy, -dx) // pointing away from p1
  for (let i = 0; i <= semiSegments; i++) {
    const t = i / semiSegments
    const angle = baseAngle0 - Math.PI / 2 + t * Math.PI
    result.push([
      p0[0] + padding * Math.cos(angle),
      p0[1] + padding * Math.sin(angle),
    ])
  }

  // Semicircle around p1 (from perpendicular-right to perpendicular-left, going away from p0)
  const baseAngle1 = Math.atan2(dy, dx) // pointing away from p0
  for (let i = 0; i <= semiSegments; i++) {
    const t = i / semiSegments
    const angle = baseAngle1 - Math.PI / 2 + t * Math.PI
    result.push([
      p1[0] + padding * Math.cos(angle),
      p1[1] + padding * Math.sin(angle),
    ])
  }

  return result
}

// ── Collinearity check ──

function isCollinear(points: Point[]): boolean {
  if (points.length <= 2) return true
  for (let i = 2; i < points.length; i++) {
    const c = cross(points[0], points[1], points[i])
    if (Math.abs(c) > 0.5) return false
  }
  return true
}

// ── Ancestor chain (cycle-safe) ──

function getAncestorChain(
  name: string,
  childToParent: Map<string, string>,
): string[] {
  const chain: string[] = []
  let current = childToParent.get(name)
  const visited = new Set<string>()
  while (current && !visited.has(current)) {
    chain.push(current)
    visited.add(current)
    current = childToParent.get(current)
  }
  return chain
}

// ── Render depth limit ──

function getMaxRenderLevel(totalTerritories: number): number {
  if (totalTerritories > 50) return 2   // very dense: top 3 levels
  if (totalTerritories > 30) return 3   // dense: top 4 levels
  return 5                               // normal: top 6 levels (show deep nesting)
}

// ── Territory color by location type ──

function territoryColor(locType: string): string {
  const t = locType.toLowerCase()
  if (t.includes("国") || t.includes("域") || t.includes("界")) return "#3b82f6"
  if (t.includes("城") || t.includes("镇") || t.includes("都") || t.includes("村"))
    return "#10b981"
  if (t.includes("山") || t.includes("洞") || t.includes("谷") || t.includes("林"))
    return "#84cc16"
  if (t.includes("宗") || t.includes("派") || t.includes("门")) return "#8b5cf6"
  if (t.includes("海") || t.includes("河") || t.includes("湖")) return "#06b6d4"
  if (t.includes("州") || t.includes("省") || t.includes("区")) return "#6366f1"
  return "#6b7280"
}
