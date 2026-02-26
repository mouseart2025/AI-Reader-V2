/**
 * Coastline generation: convex hull → expand → noise distortion → SVG path.
 * Ported from frontend/demo/fantasy-map-demo.html computeCoastline().
 */

export type Point = [number, number]

/** Cross product of vectors OA and OB (used for convex hull orientation). */
function cross(O: Point, A: Point, B: Point): number {
  return (A[0] - O[0]) * (B[1] - O[1]) - (A[1] - O[1]) * (B[0] - O[0])
}

/** Graham scan convex hull — returns points in counter-clockwise order. */
export function convexHull(points: Point[]): Point[] {
  if (points.length < 3) return points.slice()
  const sorted = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1])

  const lower: Point[] = []
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0)
      lower.pop()
    lower.push(p)
  }

  const upper: Point[] = []
  for (const p of sorted.reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0)
      upper.pop()
    upper.push(p)
  }

  return lower.slice(0, -1).concat(upper.slice(0, -1))
}

/** Expand hull outward from centroid by `padding` pixels. */
export function expandHull(hull: Point[], padding: number): Point[] {
  const cx = hull.reduce((s, p) => s + p[0], 0) / hull.length
  const cy = hull.reduce((s, p) => s + p[1], 0) / hull.length
  return hull.map((p) => {
    const dx = p[0] - cx
    const dy = p[1] - cy
    const d = Math.sqrt(dx * dx + dy * dy) || 1
    return [p[0] + (dx / d) * padding, p[1] + (dy / d) * padding] as Point
  })
}

/**
 * Apply multi-frequency sinusoidal noise + 4× subdivision for organic coastline.
 * 3 octaves: ±25px, ±15px, ±8px; subdivision adds ±12px sub-noise.
 */
export function distortCoastline(hull: Point[], seed: number): Point[] {
  const cx = hull.reduce((s, p) => s + p[0], 0) / hull.length
  const cy = hull.reduce((s, p) => s + p[1], 0) / hull.length

  // Multi-frequency radial noise
  const noisy = hull.map((p) => {
    const angle = Math.atan2(p[1] - cy, p[0] - cx)
    const noise =
      Math.sin(angle * 7 + seed) * 25 +
      Math.sin(angle * 13 + seed * 2) * 15 +
      Math.sin(angle * 23 + seed * 3) * 8
    const dx = p[0] - cx
    const dy = p[1] - cy
    const d = Math.sqrt(dx * dx + dy * dy) || 1
    return [cx + (dx / d) * (d + noise), cy + (dy / d) * (d + noise)] as Point
  })

  // Subdivide for smoothness (4× interpolation between pairs)
  const smooth: Point[] = []
  for (let i = 0; i < noisy.length; i++) {
    const a = noisy[i]
    const b = noisy[(i + 1) % noisy.length]
    smooth.push(a)
    for (let t = 0.25; t < 1; t += 0.25) {
      const mx = a[0] + (b[0] - a[0]) * t
      const my = a[1] + (b[1] - a[1]) * t
      const subNoise = Math.sin(mx * 0.05 + my * 0.03 + seed) * 12
      const ddx = mx - cx
      const ddy = my - cy
      const dd = Math.sqrt(ddx * ddx + ddy * ddy) || 1
      smooth.push([cx + (ddx / dd) * (dd + subNoise), cy + (ddy / dd) * (dd + subNoise)])
    }
  }

  return smooth
}

/** Convert point array to closed SVG path data. */
export function coastlineToPath(points: Point[]): string {
  if (points.length < 3) return ""
  let d = `M ${points[0][0]} ${points[0][1]}`
  for (let i = 1; i < points.length; i++) {
    d += ` L ${points[i][0]} ${points[i][1]}`
  }
  d += " Z"
  return d
}
