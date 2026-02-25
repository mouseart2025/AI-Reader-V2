/**
 * Simulated Annealing label layout optimizer for HD map export.
 *
 * Uses the same 8-anchor system as NovelMap's real-time greedy algorithm,
 * but applies global optimization via SA to find better label placements
 * when computation time is not constrained (seconds vs milliseconds).
 */

// ── Types ──────────────────────────────────────────────

export interface AnnealItem {
  name: string
  cx: number        // icon center x (world coords)
  cy: number        // icon center y (world coords)
  iconSize: number  // full icon diameter (world coords)
  fontSize: number  // label font size (world coords)
  labelW: number    // label width (world coords)
  labelH: number    // label height (world coords)
}

export interface AnnealPlacement {
  offsetX: number   // x offset from icon center (world coords)
  offsetY: number   // y offset from icon center (world coords)
  textAnchor: string // "middle" | "start" | "end"
}

// ── Anchor definitions (mirrors NovelMap ANCHOR_CANDIDATES) ──

interface AnchorDef {
  textAnchor: string
  getDx: (iconH: number, fh: number) => number
  getDy: (iconH: number, fh: number) => number
}

const ANCHORS: AnchorDef[] = [
  { textAnchor: "middle", getDx: () => 0,              getDy: (iconH, fh) => iconH / 2 + fh * 0.9 },   // bottom (default)
  { textAnchor: "start",  getDx: (iconH) => iconH / 2 + 4, getDy: () => 0 },                            // right
  { textAnchor: "start",  getDx: (iconH) => iconH / 2 + 2, getDy: (_, fh) => -(fh * 0.5 + 2) },        // top-right
  { textAnchor: "middle", getDx: () => 0,              getDy: (iconH, fh) => -(iconH / 2 + fh * 0.3 + 4) }, // top
  { textAnchor: "end",    getDx: (iconH) => -(iconH / 2 + 2), getDy: (_, fh) => -(fh * 0.5 + 2) },     // top-left
  { textAnchor: "end",    getDx: (iconH) => -(iconH / 2 + 4), getDy: () => 0 },                          // left
  { textAnchor: "end",    getDx: (iconH) => -(iconH / 2 + 2), getDy: (_, fh) => fh * 0.5 + 2 },         // bottom-left
  { textAnchor: "start",  getDx: (iconH) => iconH / 2 + 2,    getDy: (_, fh) => fh * 0.5 + 2 },         // bottom-right
]

// ── Rect helpers ───────────────────────────────────────

interface Rect { x: number; y: number; w: number; h: number }

function labelRect(item: AnnealItem, anchorIdx: number): Rect {
  const a = ANCHORS[anchorIdx]
  const dx = a.getDx(item.iconSize, item.fontSize)
  const dy = a.getDy(item.iconSize, item.fontSize)
  const px = item.cx + dx
  const py = item.cy + dy
  let x: number
  if (a.textAnchor === "middle") x = px - item.labelW / 2
  else if (a.textAnchor === "start") x = px
  else x = px - item.labelW
  return { x, y: py - item.labelH / 2, w: item.labelW, h: item.labelH }
}

function iconRect(item: AnnealItem): Rect {
  const r = item.iconSize / 2
  return { x: item.cx - r, y: item.cy - r, w: item.iconSize, h: item.iconSize }
}

function overlapArea(a: Rect, b: Rect): number {
  const dx = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
  const dy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  if (dx <= 0 || dy <= 0) return 0
  return dx * dy
}

// ── Energy weights ─────────────────────────────────────

const W_OVERLAP = 10    // label-label overlap (highest priority)
const W_OCCLUSION = 5   // label-icon overlap (medium)
const W_OFFSET = 0.5    // preference for default anchor (low)

// ── Main function ──────────────────────────────────────

export async function annealLabels(
  items: AnnealItem[],
  onProgress?: (pct: number) => void,
): Promise<Map<string, AnnealPlacement>> {
  const n = items.length
  if (n === 0) return new Map()

  const state = new Int8Array(n)  // anchor index per item (0 = bottom default)
  const rects: Rect[] = items.map((item) => labelRect(item, 0))
  const icons: Rect[] = items.map(iconRect)

  // Greedy initialization (warm start, like N30.1)
  for (let i = 0; i < n; i++) {
    for (let a = 0; a < ANCHORS.length; a++) {
      const rect = labelRect(items[i], a)
      let collision = false
      for (let j = 0; j < i; j++) {
        if (overlapArea(rect, rects[j]) > 0) { collision = true; break }
      }
      if (!collision) {
        state[i] = a
        rects[i] = rect
        break
      }
    }
  }

  // Simulated Annealing
  let T = 1.0
  const alpha = 0.995
  const maxIter = 5000
  const chunkSize = 500

  for (let iter = 0; iter < maxIter && T > 0.001; iter++) {
    const j = Math.floor(Math.random() * n)
    let newAnchor = Math.floor(Math.random() * 8)
    if (newAnchor === state[j]) newAnchor = (newAnchor + 1) % 8

    const oldRect = rects[j]
    const newRect = labelRect(items[j], newAnchor)

    // Incremental delta energy (only involving label j)
    let dE = 0

    for (let k = 0; k < n; k++) {
      if (k === j) continue
      // Label-label overlap
      dE += (overlapArea(newRect, rects[k]) - overlapArea(oldRect, rects[k])) * W_OVERLAP
      // Label-icon occlusion
      dE += (overlapArea(newRect, icons[k]) - overlapArea(oldRect, icons[k])) * W_OCCLUSION
    }

    // Offset from default anchor penalty
    const oldDist = state[j] === 0 ? 0 : 1
    const newDist = newAnchor === 0 ? 0 : 1
    dE += (newDist - oldDist) * W_OFFSET

    // Metropolis criterion
    if (dE < 0 || Math.random() < Math.exp(-dE / T)) {
      state[j] = newAnchor
      rects[j] = newRect
    }

    T *= alpha

    // Yield for UI progress updates
    if (iter % chunkSize === 0) {
      onProgress?.(iter / maxIter)
      await new Promise<void>(r => setTimeout(r, 0))
    }
  }

  onProgress?.(1)

  // Build result map
  const result = new Map<string, AnnealPlacement>()
  for (let i = 0; i < n; i++) {
    const a = ANCHORS[state[i]]
    const item = items[i]
    result.set(item.name, {
      offsetX: a.getDx(item.iconSize, item.fontSize),
      offsetY: a.getDy(item.iconSize, item.fontSize),
      textAnchor: a.textAnchor,
    })
  }
  return result
}
