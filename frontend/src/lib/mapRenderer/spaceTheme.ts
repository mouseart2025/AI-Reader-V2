/**
 * Space theme constants and rendering helpers for sci-fi novels.
 */

export const SPACE_THEME = {
  bg: '#0a0e1a',
  nodeFill: '#4fc3f7',
  nodeGlow: 'rgba(79, 195, 247, 0.3)',
  nodeStroke: 'rgba(79, 195, 247, 0.6)',
  routeColor: 'rgba(100, 181, 246, 0.4)',
  routeDash: [6, 8] as number[],
  labelColor: 'rgba(224, 224, 224, 0.9)',
  labelGlow: 'rgba(79, 195, 247, 0.5)',
  starDotColor: 'rgba(255, 255, 255, 0.3)',
  territoryFill: 'rgba(79, 195, 247, 0.06)',
  territoryStroke: 'rgba(79, 195, 247, 0.15)',
  typeColors: {
    world: '#ff9800',
    continent: '#ab47bc',
    kingdom: '#66bb6a',
    region: '#42a5f5',
    city: '#26c6da',
    site: '#78909c',
    building: '#8d6e63',
  } as Record<string, string>,
} as const

const _SPACE_KEYWORDS = new Set([
  '星系', '星球', '行星', '恒星', '太阳系', '银河', '星区', '星团',
  '母星', '恒星系', '星际', '宇宙', '太空', '舰队', '光年',
])

/** Check if a location name is "space-like" for per-node styling */
export function isSpaceLocation(name: string): boolean {
  return Array.from(_SPACE_KEYWORDS).some(kw => name.includes(kw))
}

/** Create an off-screen canvas with star dots (call once, reuse) */
export function createStarfieldCanvas(width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')!
  // Random star dots
  const count = Math.min(400, Math.floor(width * height / 3000))
  for (let i = 0; i < count; i++) {
    const x = Math.random() * width
    const y = Math.random() * height
    const r = Math.random() * 1.5 + 0.3
    const alpha = Math.random() * 0.4 + 0.1
    ctx.beginPath()
    ctx.arc(x, y, r, 0, Math.PI * 2)
    ctx.fillStyle = `rgba(255, 255, 255, ${alpha})`
    ctx.fill()
  }
  return canvas
}

/** Draw a glowing node (space style) */
export function drawSpaceNode(
  ctx: CanvasRenderingContext2D,
  x: number, y: number,
  radius: number,
  tier: string,
  _name: string,
) {
  const color = SPACE_THEME.typeColors[tier] || SPACE_THEME.nodeFill

  // Glow intensity by tier
  const glowTiers: Record<string, number> = {
    world: 20, continent: 15, kingdom: 10, region: 8, city: 6, site: 4, building: 0,
  }
  const glowRadius = glowTiers[tier] ?? 4

  if (glowRadius > 0) {
    // Outer glow
    const gradient = ctx.createRadialGradient(x, y, radius * 0.3, x, y, radius + glowRadius)
    gradient.addColorStop(0, color)
    gradient.addColorStop(0.6, color.replace(')', ', 0.3)').replace('rgb(', 'rgba('))
    gradient.addColorStop(1, 'transparent')
    ctx.beginPath()
    ctx.arc(x, y, radius + glowRadius, 0, Math.PI * 2)
    ctx.fillStyle = gradient
    ctx.fill()
  }

  // Core circle
  ctx.beginPath()
  ctx.arc(x, y, radius, 0, Math.PI * 2)
  ctx.fillStyle = color
  ctx.fill()
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)'
  ctx.lineWidth = 0.5
  ctx.stroke()
}

/** Draw a space-style route/edge (dashed glow line) */
export function drawSpaceRoute(
  ctx: CanvasRenderingContext2D,
  points: Array<{x: number, y: number}>,
) {
  if (points.length < 2) return
  ctx.save()
  ctx.setLineDash(SPACE_THEME.routeDash)
  ctx.strokeStyle = SPACE_THEME.routeColor
  ctx.lineWidth = 1.5
  ctx.shadowColor = SPACE_THEME.routeColor
  ctx.shadowBlur = 6
  ctx.beginPath()
  ctx.moveTo(points[0].x, points[0].y)
  for (let i = 1; i < points.length; i++) {
    ctx.lineTo(points[i].x, points[i].y)
  }
  ctx.stroke()
  ctx.restore()
}

/** Draw a space-style label */
export function drawSpaceLabel(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number, y: number,
  fontSize: number,
) {
  ctx.save()
  ctx.font = `${fontSize}px system-ui, sans-serif`
  ctx.fillStyle = SPACE_THEME.labelColor
  ctx.shadowColor = SPACE_THEME.labelGlow
  ctx.shadowBlur = 4
  ctx.textAlign = 'center'
  ctx.fillText(text, x, y)
  ctx.restore()
}

// ── SVG-compatible helpers for NovelMap (D3/SVG-based) ──────────

/** Glow radius by tier for SVG rendering */
const SVG_GLOW_RADIUS: Record<string, number> = {
  world: 20, continent: 15, kingdom: 10, region: 8, city: 6, site: 4, building: 0,
}

/** Get the space theme color for a tier */
export function getSpaceNodeColor(tier: string): string {
  return SPACE_THEME.typeColors[tier] || SPACE_THEME.nodeFill
}

/** Get the glow radius for a tier */
export function getSpaceGlowRadius(tier: string): number {
  return SVG_GLOW_RADIUS[tier] ?? 4
}

/**
 * Generate starfield SVG elements data (positions + radii + alphas).
 * Returns an array of star descriptors for SVG circle rendering.
 */
export function generateStarfield(
  width: number, height: number, seed = 42,
): Array<{ x: number; y: number; r: number; alpha: number }> {
  // Simple seeded PRNG (mulberry32)
  let s = seed | 0
  const rand = () => {
    s = (s + 0x6D2B79F5) | 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  const count = Math.min(400, Math.floor(width * height / 3000))
  const stars: Array<{ x: number; y: number; r: number; alpha: number }> = []
  for (let i = 0; i < count; i++) {
    stars.push({
      x: rand() * width,
      y: rand() * height,
      r: rand() * 1.5 + 0.3,
      alpha: rand() * 0.4 + 0.1,
    })
  }
  return stars
}
