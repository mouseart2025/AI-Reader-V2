/**
 * Terrain semantic texture layer — generates decorative symbols
 * (mountain ridges, waves, tree clusters, dot patterns, stalactites)
 * around terrain-type locations. Inspired by hand-painted game maps:
 * large, dense, zone-filling terrain textures on parchment.
 */

import type { MapLayoutItem, MapLocation } from "@/api/types"

// ── Public types ──────────────────────────────────

export type TerrainCategory = "mountain" | "water" | "forest" | "desert" | "cave"

export interface TerrainSymbolDef {
  id: string
  pathData: string
  viewBox: string
  strokeOnly?: boolean
}

export interface TerrainHint {
  symbolId: string
  x: number
  y: number
  size: number          // actual render pixel size (tier-dependent)
  rotation: number      // degrees, ±15
  opacity: number
  color: string
}

export interface TerrainHintResult {
  symbolDefs: TerrainSymbolDef[]
  hints: TerrainHint[]
}

// ── Icon → terrain category ──────────────────────

const TERRAIN_MAP: Record<string, TerrainCategory> = {
  mountain: "mountain",
  water: "water",
  island: "water",
  forest: "forest",
  desert: "desert",
  cave: "cave",
}

// ── Per-tier budget & sizing ─────────────────────

const TIER_CONFIG: Record<string, { count: number; radius: number; baseSize: number }> = {
  continent: { count: 22, radius: 160, baseSize: 38 },
  kingdom:   { count: 16, radius: 120, baseSize: 32 },
  region:    { count: 12, radius: 85,  baseSize: 26 },
  city:      { count: 8,  radius: 55,  baseSize: 20 },
  site:      { count: 5,  radius: 38,  baseSize: 16 },
  building:  { count: 3,  radius: 24,  baseSize: 12 },
}

const MAX_HINTS = 900

// ── Symbol definitions (3 variants per category) ──

const SYMBOL_DEFS: TerrainSymbolDef[] = [
  // ── Mountain ──────────────────────────────────
  // V0: single sharp peak
  {
    id: "terrain-mountain-0",
    viewBox: "0 0 14 14",
    pathData: '<path d="M 0,14 L 7,0 L 14,14 Z"/>',
  },
  // V1: wide shorter peak
  {
    id: "terrain-mountain-1",
    viewBox: "0 0 16 12",
    pathData: '<path d="M 0,12 L 8,0 L 16,12 Z"/>',
  },
  // V2: mountain ridge — 3 overlapping peaks (signature)
  {
    id: "terrain-mountain-2",
    viewBox: "0 0 24 14",
    pathData:
      '<path d="M 0,14 L 5,3 L 10,14 Z" opacity="0.7"/>' +
      '<path d="M 4,14 L 12,0 L 20,14 Z"/>' +
      '<path d="M 14,14 L 19,5 L 24,14 Z" opacity="0.6"/>',
  },

  // ── Water ─────────────────────────────────────
  // V0: single wave
  {
    id: "terrain-water-0",
    viewBox: "0 0 16 10",
    pathData: '<path d="M 0,5 Q 4,0 8,5 Q 12,10 16,5"/>',
    strokeOnly: true,
  },
  // V1: double wave (parallel)
  {
    id: "terrain-water-1",
    viewBox: "0 0 16 12",
    pathData:
      '<path d="M 0,4 Q 4,0 8,4 Q 12,8 16,4"/>' +
      '<path d="M 0,9 Q 4,5 8,9 Q 12,13 16,9"/>',
    strokeOnly: true,
  },
  // V2: triple wave (denser water feel)
  {
    id: "terrain-water-2",
    viewBox: "0 0 18 14",
    pathData:
      '<path d="M 0,3 Q 4.5,0 9,3 Q 13.5,6 18,3"/>' +
      '<path d="M 0,7 Q 4.5,4 9,7 Q 13.5,10 18,7"/>' +
      '<path d="M 0,11 Q 4.5,8 9,11 Q 13.5,14 18,11"/>',
    strokeOnly: true,
  },

  // ── Forest ────────────────────────────────────
  // V0: conifer tree
  {
    id: "terrain-forest-0",
    viewBox: "0 0 12 16",
    pathData: '<path d="M 6,0 L 11,7 L 8.5,7 L 8.5,14 L 3.5,14 L 3.5,7 L 1,7 Z"/>',
  },
  // V1: round-canopy tree
  {
    id: "terrain-forest-1",
    viewBox: "0 0 12 16",
    pathData:
      '<circle cx="6" cy="5" r="5"/>' +
      '<rect x="4.5" y="10" width="3" height="6"/>',
  },
  // V2: tree cluster — 3 trees (signature dense forest)
  {
    id: "terrain-forest-2",
    viewBox: "0 0 22 18",
    pathData:
      '<circle cx="5" cy="5" r="4.5"/><rect x="3.5" y="9.5" width="3" height="5"/>' +
      '<circle cx="14" cy="4" r="5"/><rect x="12.5" y="9" width="3" height="5.5"/>' +
      '<circle cx="9" cy="8" r="4"/><rect x="7.5" y="12" width="3" height="4.5"/>',
  },

  // ── Desert ────────────────────────────────────
  // V0: 5-dot cluster
  {
    id: "terrain-desert-0",
    viewBox: "0 0 14 14",
    pathData:
      '<circle cx="3" cy="3" r="1.5"/>' +
      '<circle cx="11" cy="2.5" r="1.3"/>' +
      '<circle cx="7" cy="7" r="1.6"/>' +
      '<circle cx="2.5" cy="11" r="1.2"/>' +
      '<circle cx="11" cy="11" r="1.4"/>',
  },
  // V1: 7-dot spread
  {
    id: "terrain-desert-1",
    viewBox: "0 0 16 14",
    pathData:
      '<circle cx="2" cy="5" r="1.3"/>' +
      '<circle cx="7" cy="2" r="1.4"/>' +
      '<circle cx="13" cy="3" r="1.1"/>' +
      '<circle cx="5" cy="8" r="1.2"/>' +
      '<circle cx="10" cy="7" r="1.5"/>' +
      '<circle cx="3" cy="12" r="1.1"/>' +
      '<circle cx="12" cy="12" r="1.3"/>',
  },

  // ── Cave ──────────────────────────────────────
  // V0: inverted triangle (stalactite)
  {
    id: "terrain-cave-0",
    viewBox: "0 0 14 12",
    pathData: '<path d="M 0,0 L 7,12 L 14,0 Z"/>',
  },
  // V1: wider stalactite
  {
    id: "terrain-cave-1",
    viewBox: "0 0 16 10",
    pathData: '<path d="M 0,0 L 8,10 L 16,0 Z"/>',
  },
]

// Category → symbol IDs
const CATEGORY_SYMBOLS: Record<TerrainCategory, string[]> = {
  mountain: ["terrain-mountain-0", "terrain-mountain-1", "terrain-mountain-2"],
  water:    ["terrain-water-0", "terrain-water-1", "terrain-water-2"],
  forest:   ["terrain-forest-0", "terrain-forest-1", "terrain-forest-2"],
  desert:   ["terrain-desert-0", "terrain-desert-1"],
  cave:     ["terrain-cave-0", "terrain-cave-1"],
}

// ── Color palettes ────────────────────────────────

const COLORS_LIGHT: Record<TerrainCategory, string> = {
  mountain: "#8b7355",
  water:    "#6b8fa3",
  forest:   "#6b8b5c",
  desert:   "#b09870",
  cave:     "#8b7355",
}

const COLORS_DARK: Record<TerrainCategory, string> = {
  mountain: "#c4a97d",
  water:    "#7bb5d0",
  forest:   "#8fad7e",
  desert:   "#c4b48a",
  cave:     "#a08e6e",
}

// ── Deterministic pseudo-random ───────────────────

function hashString(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0
  }
  return Math.abs(h)
}

function pseudoRandom(seed: number): number {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453
  return x - Math.floor(x)
}

// ── Main generator ────────────────────────────────

export function generateTerrainHints(
  locations: MapLocation[],
  layout: MapLayoutItem[],
  canvasSize: { width: number; height: number },
  darkBg: boolean,
): TerrainHintResult {
  const layoutMap = new Map<string, MapLayoutItem>()
  for (const item of layout) layoutMap.set(item.name, item)

  const baseOpacity = darkBg ? 0.28 : 0.35
  const colorPalette = darkBg ? COLORS_DARK : COLORS_LIGHT
  const pad = 20

  // Step 1: filter to terrain locations with layout positions
  interface TerrainLoc {
    name: string
    x: number
    y: number
    category: TerrainCategory
    tier: string
  }

  const terrainLocs: TerrainLoc[] = []
  for (const loc of locations) {
    const cat = TERRAIN_MAP[loc.icon ?? ""]
    if (!cat) continue
    const item = layoutMap.get(loc.name)
    if (!item || item.is_portal) continue
    terrainLocs.push({
      name: loc.name,
      x: item.x,
      y: item.y,
      category: cat,
      tier: loc.tier ?? "city",
    })
  }

  if (terrainLocs.length === 0) {
    return { symbolDefs: [], hints: [] }
  }

  // Step 2: compute budgets
  let totalBudget = 0
  const budgets: number[] = []
  for (const tl of terrainLocs) {
    const cfg = TIER_CONFIG[tl.tier] ?? TIER_CONFIG.city
    budgets.push(cfg.count)
    totalBudget += cfg.count
  }

  if (totalBudget > MAX_HINTS) {
    const ratio = MAX_HINTS / totalBudget
    for (let i = 0; i < budgets.length; i++) {
      budgets[i] = Math.max(1, Math.round(budgets[i] * ratio))
    }
  }

  // Location centers for collision avoidance
  const centers: { x: number; y: number }[] = []
  for (const item of layout) {
    if (!item.is_portal) centers.push({ x: item.x, y: item.y })
  }

  // Step 3–6: generate hints
  const hints: TerrainHint[] = []
  const usedCategories = new Set<TerrainCategory>()

  for (let li = 0; li < terrainLocs.length; li++) {
    const tl = terrainLocs[li]
    const count = budgets[li]
    const cfg = TIER_CONFIG[tl.tier] ?? TIER_CONFIG.city
    const spreadRadius = cfg.radius
    const symbols = CATEGORY_SYMBOLS[tl.category]
    const color = colorPalette[tl.category]
    const baseSeed = hashString(tl.name)

    usedCategories.add(tl.category)

    for (let i = 0; i < count; i++) {
      const seed = baseSeed + i * 7919

      const angle = pseudoRandom(seed) * Math.PI * 2
      const r = spreadRadius * (0.3 + 0.7 * Math.sqrt(pseudoRandom(seed + 1)))
      const x = tl.x + Math.cos(angle) * r
      const y = tl.y + Math.sin(angle) * r

      // Canvas boundary clipping
      if (x < pad || x > canvasSize.width - pad || y < pad || y > canvasSize.height - pad) {
        continue
      }

      // Collision: skip if too close to any location center
      let tooClose = false
      for (const c of centers) {
        const dx = x - c.x
        const dy = y - c.y
        if (dx * dx + dy * dy < 18 * 18) {
          tooClose = true
          break
        }
      }
      if (tooClose) continue

      // Size: tier-dependent base with random variation (0.5 – 1.0)
      const sizeScale = 0.5 + 0.5 * pseudoRandom(seed + 2)
      const size = cfg.baseSize * sizeScale

      const rotation = (pseudoRandom(seed + 3) - 0.5) * 30
      const opacity = baseOpacity * (0.6 + 0.4 * pseudoRandom(seed + 4))

      // Variant: mix single + cluster symbols. Cluster variants (index 2)
      // appear ~30% of the time for mountain/forest/water for density feel.
      let symbolIdx: number
      if (symbols.length >= 3 && pseudoRandom(seed + 5) < 0.35) {
        symbolIdx = 2  // cluster variant
      } else {
        symbolIdx = (baseSeed + i) % Math.min(symbols.length, 2)
      }

      hints.push({
        symbolId: symbols[symbolIdx],
        x,
        y,
        size,
        rotation,
        opacity,
        color,
      })
    }
  }

  // Only include defs for used categories
  const symbolDefs = SYMBOL_DEFS.filter((sd) => {
    for (const cat of usedCategories) {
      if (CATEGORY_SYMBOLS[cat].includes(sd.id)) return true
    }
    return false
  })

  return { symbolDefs, hints }
}
