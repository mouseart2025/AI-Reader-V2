/**
 * Hand-drawn edge distortion for polygon boundaries.
 *
 * Ported from backend `map_layout_service.py:_distort_polygon_edges()`.
 * Uses hash-based noise instead of OpenSimplex to avoid extra dependencies.
 */

/** Deterministic hash-based noise returning value in [-1, 1]. */
function hashNoise(x: number, y: number, seed: number = 0): number {
  const h = Math.sin(x * 12.9898 + y * 78.233 + seed * 1.618) * 43758.5453
  return (h - Math.floor(h)) * 2 - 1
}

/**
 * Smooth 2D noise via bilinear interpolation of hash-noise on a grid.
 * Gives more natural results than raw hash noise.
 */
function smoothNoise(x: number, y: number, seed: number = 0): number {
  const ix = Math.floor(x)
  const iy = Math.floor(y)
  const fx = x - ix
  const fy = y - iy

  // Smoothstep
  const sx = fx * fx * (3 - 2 * fx)
  const sy = fy * fy * (3 - 2 * fy)

  const n00 = hashNoise(ix, iy, seed)
  const n10 = hashNoise(ix + 1, iy, seed)
  const n01 = hashNoise(ix, iy + 1, seed)
  const n11 = hashNoise(ix + 1, iy + 1, seed)

  const nx0 = n00 + sx * (n10 - n00)
  const nx1 = n01 + sx * (n11 - n01)

  return nx0 + sy * (nx1 - nx0)
}

export type Point = [number, number]

/**
 * Apply noise distortion to polygon edges for a hand-drawn look.
 *
 * Each edge is subdivided into `numSegments` segments. Intermediate points
 * are displaced perpendicular to the edge by an amount controlled by noise.
 * The displacement tapers to zero at vertices via sin(t*pi) so that
 * adjacent polygons sharing an edge produce identical distortions (no gaps).
 *
 * The noise anchor is derived from the canonical (lexicographically sorted)
 * edge midpoint, ensuring two polygons that share an edge get the same curve.
 */
export function distortPolygonEdges(
  polygon: Point[],
  canvasWidth: number,
  canvasHeight: number,
  numSegments: number = 16,
  seed: number = 0,
): Point[] {
  if (polygon.length < 3) return polygon

  const amplitude = Math.min(canvasWidth, canvasHeight) * 0.018

  const result: Point[] = []
  const n = polygon.length

  for (let i = 0; i < n; i++) {
    const p0 = polygon[i]
    const p1 = polygon[(i + 1) % n]

    // Canonical edge key: sort endpoints lexicographically so both
    // adjacent polygons use the same noise anchor for this edge.
    const canonical = p0[0] < p1[0] || (p0[0] === p1[0] && p0[1] <= p1[1])
    const anchorX = (p0[0] + p1[0]) / 2
    const anchorY = (p0[1] + p1[1]) / 2
    // Direction sign: compensates for the perpendicular vector flipping
    // when the edge is traversed in non-canonical order.
    const direction = canonical ? 1.0 : -1.0

    // Edge direction and perpendicular
    const ex = p1[0] - p0[0]
    const ey = p1[1] - p0[1]
    const edgeLen = Math.sqrt(ex * ex + ey * ey)
    if (edgeLen < 1e-6) {
      result.push(p0)
      continue
    }
    // Unit perpendicular (rotated 90 degrees CCW)
    const nx = -ey / edgeLen
    const ny = ex / edgeLen

    // Add the start vertex (no displacement)
    result.push(p0)

    // Subdivide and displace intermediate points
    for (let seg = 1; seg < numSegments; seg++) {
      const t = seg / numSegments
      // Linear interpolation along edge
      const ix = p0[0] + t * ex
      const iy = p0[1] + t * ey

      // sin(t*pi) envelope: zero at endpoints, max at midpoint
      const envelope = Math.sin(t * Math.PI)

      // Use canonical t for noise sampling so both polygons sharing
      // this edge sample the same noise values at each physical point.
      const canonicalT = canonical ? t : 1.0 - t

      // Noise input: use anchor + canonical parameter for deterministic curve
      const noiseVal = smoothNoise(
        anchorX * 0.05 + canonicalT * 3.0,
        anchorY * 0.05,
        seed,
      )

      const displacement = noiseVal * amplitude * envelope * direction

      result.push([ix + nx * displacement, iy + ny * displacement])
    }
  }

  return result
}
