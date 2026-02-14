/**
 * Voronoi-based territory generation for parent-child containment.
 *
 * Given locations with parent/child relationships and their canvas positions,
 * generates territory polygons that visually show containment (parent wraps
 * around its children).
 *
 * Algorithm (inspired by Azgaar FMG states/provinces):
 * 1. Generate supplementary seed points (jittered grid) to fill canvas
 * 2. Compute Voronoi tessellation from all seed points via d3-delaunay
 * 3. Assign each cell to the nearest parent location (flood-fill)
 * 4. Merge cells belonging to the same parent → outer polygon
 * 5. Handle nesting: if A contains B contains C, generate 3 territory levels
 */

import { Delaunay } from "d3-delaunay"
import type { MapLocation, MapLayoutItem } from "@/api/types"
import type { Point } from "./edgeDistortion"

export interface Territory {
  name: string          // parent location name
  polygon: Point[]      // polygon vertices (canvas coords)
  color: string         // fill color
  level: number         // nesting depth (0 = outermost)
  children: string[]    // direct child location names
}

interface SeedPoint {
  x: number
  y: number
  owner: string | null  // parent location name this seed belongs to, or null (virtual)
}

/**
 * Generate territory polygons for locations that have children.
 */
export function generateTerritories(
  locations: MapLocation[],
  layout: MapLayoutItem[],
  canvasSize: { width: number; height: number },
): Territory[] {
  const layoutMap = new Map<string, MapLayoutItem>()
  for (const item of layout) layoutMap.set(item.name, item)

  const locMap = new Map<string, MapLocation>()
  for (const loc of locations) locMap.set(loc.name, loc)

  // Find locations that are parents (have children in the data)
  const parentNames = new Set<string>()
  const childToParent = new Map<string, string>()

  for (const loc of locations) {
    if (loc.parent && locMap.has(loc.parent)) {
      parentNames.add(loc.parent)
      childToParent.set(loc.name, loc.parent)
    }
  }

  // Only generate territories for parents that have positioned children
  const validParents: string[] = []
  for (const pName of parentNames) {
    const children = locations.filter(
      (l) => l.parent === pName && layoutMap.has(l.name),
    )
    // Need at least 1 positioned child (the parent itself may also have a position)
    if (children.length > 0) {
      validParents.push(pName)
    }
  }

  if (validParents.length === 0) return []

  const { width, height } = canvasSize
  const margin = 40

  // ── Step 1: Generate seed points ──
  const seeds: SeedPoint[] = []

  // Add real location positions as seeds
  for (const item of layout) {
    if (item.is_portal) continue
    const loc = locMap.get(item.name)
    const owner = loc?.parent && parentNames.has(loc.parent) ? loc.parent : null
    // If this location IS a parent, it owns itself
    const selfOwner = parentNames.has(item.name) ? item.name : owner
    seeds.push({ x: item.x, y: item.y, owner: selfOwner })
  }

  // Add virtual seed points on a jittered grid to fill the canvas
  const gridStep = Math.max(width, height) / 14
  for (let gx = margin; gx < width - margin; gx += gridStep) {
    for (let gy = margin; gy < height - margin; gy += gridStep) {
      // Jitter
      const jx = gx + (hashRand(gx, gy, 1) - 0.5) * gridStep * 0.6
      const jy = gy + (hashRand(gx, gy, 2) - 0.5) * gridStep * 0.6

      // Skip if too close to any real location
      let tooClose = false
      for (const item of layout) {
        const dx = jx - item.x
        const dy = jy - item.y
        if (dx * dx + dy * dy < gridStep * gridStep * 0.15) {
          tooClose = true
          break
        }
      }
      if (!tooClose) {
        seeds.push({ x: jx, y: jy, owner: null })
      }
    }
  }

  if (seeds.length < 3) return []

  // ── Step 2: Voronoi tessellation ──
  const points = seeds.map((s) => [s.x, s.y] as [number, number])
  const delaunay = Delaunay.from(points)
  const voronoi = delaunay.voronoi([0, 0, width, height])

  // ── Step 3: Assign cells to parents via proximity ──
  // For each parent, collect positions of self + all descendants
  const parentPositions = new Map<string, Point[]>()
  for (const pName of validParents) {
    const positions: Point[] = []
    // Parent's own position
    const pItem = layoutMap.get(pName)
    if (pItem) positions.push([pItem.x, pItem.y])
    // Children positions
    for (const loc of locations) {
      if (getAncestorChain(loc.name, childToParent).includes(pName)) {
        const item = layoutMap.get(loc.name)
        if (item) positions.push([item.x, item.y])
      }
    }
    if (positions.length > 0) {
      parentPositions.set(pName, positions)
    }
  }

  // Assign each cell to the closest parent (by minimum distance to any
  // of that parent's member positions)
  const cellOwners: (string | null)[] = new Array(seeds.length).fill(null)

  for (let i = 0; i < seeds.length; i++) {
    const seed = seeds[i]
    const sx = seed.x
    const sy = seed.y

    // If this seed is a real location belonging to a parent, assign it
    if (seed.owner) {
      cellOwners[i] = seed.owner
      continue
    }

    // Find closest parent territory
    let bestParent: string | null = null
    let bestDist = Infinity

    for (const [pName, positions] of parentPositions) {
      for (const [px, py] of positions) {
        const d = (sx - px) * (sx - px) + (sy - py) * (sy - py)
        if (d < bestDist) {
          bestDist = d
          bestParent = pName
        }
      }
    }

    // Only assign if close enough (within a reasonable radius)
    // Use average spacing between parent members as reference
    if (bestParent && bestDist < gridStep * gridStep * 6) {
      cellOwners[i] = bestParent
    }
  }

  // ── Step 4: Merge cells into polygons per parent ──
  const territories: Territory[] = []

  for (const pName of validParents) {
    // Collect all cell indices for this parent
    const cellIndices: number[] = []
    for (let i = 0; i < cellOwners.length; i++) {
      if (cellOwners[i] === pName) cellIndices.push(i)
    }

    if (cellIndices.length === 0) continue

    // Get outer boundary by merging Voronoi cells
    const polygon = mergeVoronoiCells(voronoi, cellIndices, width, height)
    if (polygon.length < 3) continue

    // Determine nesting level
    const level = getAncestorChain(pName, childToParent).length

    // Color based on parent's location type
    const loc = locMap.get(pName)
    const color = territoryColor(loc?.type ?? "")

    // Get direct children names
    const children = locations
      .filter((l) => l.parent === pName)
      .map((l) => l.name)

    territories.push({ name: pName, polygon, color, level, children })
  }

  // Sort by level ascending (outermost first)
  territories.sort((a, b) => a.level - b.level)

  return territories
}

/** Get ancestor chain for a location (not including self). */
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

/**
 * Merge multiple Voronoi cells into a single outer polygon.
 * Uses edge-tracking: an edge is on the boundary if it separates an
 * owned cell from an unowned cell (or the canvas border).
 */
function mergeVoronoiCells(
  voronoi: ReturnType<typeof Delaunay.prototype.voronoi>,
  cellIndices: number[],
  _width: number,
  _height: number,
): Point[] {
  // Collect boundary edges
  // An edge is boundary if it borders a cell NOT in the owned set
  const edgeMap = new Map<string, { p: Point; q: Point }>()

  for (const ci of cellIndices) {
    const cellPoly = voronoi.cellPolygon(ci)
    if (!cellPoly) continue

    for (let j = 0; j < cellPoly.length - 1; j++) {
      const p: Point = [cellPoly[j][0], cellPoly[j][1]]
      const q: Point = [cellPoly[j + 1][0], cellPoly[j + 1][1]]

      // Edge key: canonical direction
      const fwdKey = `${roundKey(p[0])},${roundKey(p[1])}-${roundKey(q[0])},${roundKey(q[1])}`
      const revKey = `${roundKey(q[0])},${roundKey(q[1])}-${roundKey(p[0])},${roundKey(p[1])}`

      if (edgeMap.has(revKey)) {
        // This edge is shared between two owned cells → interior, remove it
        edgeMap.delete(revKey)
      } else {
        edgeMap.set(fwdKey, { p, q })
      }
    }
  }

  if (edgeMap.size === 0) return []

  // Chain edges into polygon(s) — take the longest loop
  const edges = Array.from(edgeMap.values())
  const loops = chainEdges(edges)

  // Return the longest loop
  if (loops.length === 0) return []
  loops.sort((a, b) => b.length - a.length)
  return loops[0]
}

/** Chain loose edges into closed loops. */
function chainEdges(edges: { p: Point; q: Point }[]): Point[][] {
  // Build adjacency: point → list of edges starting/ending there
  const adj = new Map<string, { target: Point; used: boolean }[]>()

  for (const e of edges) {
    const pk = ptKey(e.p)
    const qk = ptKey(e.q)
    if (!adj.has(pk)) adj.set(pk, [])
    if (!adj.has(qk)) adj.set(qk, [])
    const entry = { target: e.q, used: false }
    const revEntry = { target: e.p, used: false }
    adj.get(pk)!.push(entry)
    adj.get(qk)!.push(revEntry)
  }

  const loops: Point[][] = []

  for (const [startKey] of adj) {
    const startEdges = adj.get(startKey)!
    const firstUnused = startEdges.find((e) => !e.used)
    if (!firstUnused) continue

    const loop: Point[] = []
    let currentKey = startKey
    let currentEntry = firstUnused
    currentEntry.used = true
    // Mark reverse
    markReverse(adj, currentEntry.target, parseKey(currentKey))

    loop.push(parseKey(currentKey))

    const maxIter = edges.length * 2 + 10
    let iter = 0
    while (iter++ < maxIter) {
      const nextKey = ptKey(currentEntry.target)
      loop.push(currentEntry.target)

      if (nextKey === startKey) break

      const nextEdges = adj.get(nextKey)
      if (!nextEdges) break
      const next = nextEdges.find((e) => !e.used)
      if (!next) break

      next.used = true
      markReverse(adj, next.target, currentEntry.target)

      currentEntry = next
    }

    if (loop.length >= 3) {
      loops.push(loop)
    }
  }

  return loops
}

function markReverse(
  adj: Map<string, { target: Point; used: boolean }[]>,
  from: Point,
  to: Point,
) {
  const fk = ptKey(from)
  const entries = adj.get(fk)
  if (!entries) return
  const tk = ptKey(to)
  for (const e of entries) {
    if (ptKey(e.target) === tk && !e.used) {
      e.used = true
      break
    }
  }
}

function roundKey(v: number): string {
  return (Math.round(v * 100) / 100).toFixed(2)
}

function ptKey(p: Point): string {
  return `${roundKey(p[0])},${roundKey(p[1])}`
}

function parseKey(k: string): Point {
  const parts = k.split(",")
  return [parseFloat(parts[0]), parseFloat(parts[1])]
}

function hashRand(x: number, y: number, seed: number): number {
  const h = Math.sin(x * 12.9898 + y * 78.233 + seed * 1.618) * 43758.5453
  return h - Math.floor(h)
}

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
