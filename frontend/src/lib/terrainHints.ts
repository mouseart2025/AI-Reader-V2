/**
 * Ground-cover texture layer — decorative terrain symbols (mountain ridges,
 * waves, tree clusters, grass tufts, stalactites) scattered across the land.
 *
 * This is the Wonderdraft / Inkarnate idiom: a map reads as *terrain* because
 * its ground is built from thousands of small stamps, not because a polygon is
 * coloured in. Two things make that work, and both were wrong before:
 *
 *  1. **Symbols belong to the ground, not to the labels.** Placement used to be
 *     a disc of radius `TIER_CONFIG[tier].radius` around each location whose
 *     icon happened to be mountain/water/forest/… So the texture clung to those
 *     pins and every stretch of open land stayed flat colour. Zooming in — the
 *     exact gesture that is supposed to reveal detail — landed on empty paper.
 *     Placement is now a biome field: a jittered grid over the canvas, each
 *     cell painted by the nearest terrain seed with a falloff, so a massif or a
 *     forest grows as an *area* and the gaps between get grass.
 *
 *  2. **Density is a screen-space quantity.** The grid pitch is fixed in screen
 *     pixels and the caller passes the visible canvas rect, so the number of
 *     symbols on screen (and therefore the DOM budget) is constant while the
 *     *canvas* pitch tightens as you zoom. Zooming in genuinely reveals new
 *     ground instead of magnifying the same stamps.
 *
 *  3. **Ground cover is masked to land.** The grid has to cover the viewport,
 *     but a mountain ridge inked on top of open sea reads as a rendering bug.
 *     Cells whose centre falls outside every coastline are skipped, so the
 *     cover follows the shoreline instead of the rectangle.
 */

import type { Landmass, MapLayoutItem, MapLocation } from "@/api/types"

// ── Public types ──────────────────────────────────

export type TerrainCategory =
  | "mountain"
  | "water"
  | "forest"
  | "desert"
  | "cave"
  /** No terrain seed nearby — open ground. */
  | "plains"

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
  size: number          // on-screen render size, in px
  rotation: number      // degrees, ±14
  opacity: number
  color: string
}

/** The canvas-space rectangle the viewport currently shows. */
export interface TerrainViewRect {
  x: number
  y: number
  w: number
  h: number
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

// ── Placement budget ─────────────────────────────

/**
 * Target on-screen pitch between ground symbols, in CSS px.
 *
 * This number *is* the ground-cover density, and it is the single most visible
 * dial in this file.
 *
 * It is not a whole-canvas density, which is why 32 looked dense enough and was
 * not: on 西游记 the land is only 27 % of the canvas, so a landmask drops a 32 px
 * lattice to ~120 stamps on a 1680×1000 screen — one per 140×110 px, the same
 * sparse wash that motivated the rewrite. The pitch quoted here is therefore
 * the pitch *on land*. 16 px puts ~540 stamps on screen and takes areal cover
 * of the visible land from ~11 % to ~30 %, which is the band where the eye
 * stops seeing individual marks and starts seeing ground.
 */
const CELL_PX = 16

/**
 * Hard cap on grid cells, hence on the cost of a rebuild. Note this caps
 * *candidate* cells, not symbols: most fall outside the coastline and are
 * discarded, so the node count actually rendered stays a few hundred.
 */
const MAX_CELLS = 7000

/**
 * Cap on *rendered symbols*, not on cells.
 *
 * The two are not proportional, which is why the cell cap above is not enough.
 * A cell is a fixed number of screen pixels, so the candidate grid is ~105×62
 * at every zoom level; what changes as the reader zooms is how much of that
 * window is *land*. On the fit view 西游记's land is 27 % of the canvas, so of
 * ~6 500 cells only ~1 760 are eligible and the layer renders ~1 000 nodes.
 * Zoom into an interior and every cell is eligible — the same window costs
 * ~3 800 nodes, and dragging there went from a p95 of 59 ms to 178 ms. The
 * layer was never expensive to *compute*; it was expensive to *rasterise*.
 *
 * Thinning is a per-cell Bernoulli test rather than every n-th survivor, for
 * two reasons: a fixed stride would re-create in the surviving set exactly the
 * lattice the row stagger exists to remove, and it would do so only at some
 * zoom levels, which is worse — a regular grid that appears and disappears.
 */
const NODE_BUDGET = 1400

/**
 * How far a terrain seed paints, in **canvas units** — a property of the world,
 * not of the screen.
 *
 * These were cell counts, and a cell is `CELL_PX / k`, so a massif's reach was
 * 3 × 16 = 48 screen pixels no matter how far the reader zoomed in. The ground
 * layer was therefore scale-invariant: zooming onto land did not reveal a
 * mountain range, it just showed the same 48 px patch of mountains against an
 * ever-widening field of identical grass. Measured over the main landmass of
 * 西游记, everything above k ≈ 2.7 came back as 100 % `plains` — 1 800 grass
 * tufts and not one ridge. Making the reach a world distance is what lets the
 * reader zoom *into* a biome instead of only past it.
 *
 * Scaled by `worldScale` below so a smaller canvas keeps the same proportions.
 * A lone 山 label yields a small massif; a run of them yields a range.
 * `plains` is never a seed — it is the fallback.
 */
const BIOME_REACH: Record<TerrainCategory, number> = {
  mountain: 430,
  forest: 470,
  water: 330,
  desert: 580,
  cave: 230,
  plains: 0,
}

/** Canvas size the reaches above are authored against. */
const BIOME_REF_MIN_SIDE = 4500

/** On-screen base size per biome. See CATEGORY_SIZE_SPREAD for the variation. */
const CATEGORY_SIZE: Record<TerrainCategory, number> = {
  mountain: 20,
  forest: 15,
  water: 22,
  desert: 14,
  cave: 13,
  plains: 14,
}

/**
 * Relative size variation per biome — the half-width of the size range, as a
 * fraction of `CATEGORY_SIZE`.
 *
 * These used to be much wider (mountain 0.75, forest 0.5), because a range is
 * *made* of peaks of different heights and ±12 % triangles are a printed
 * pattern. That reasoning was right about the symptom and wrong about the
 * cure: the variation was uncorrelated, and uncorrelated variation is noise —
 * it made the field look like a defect rather than a range, which is what the
 * reader was seeing at deep zoom.
 *
 * `RELIEF_SIZE` and friends now supply the size variation, *correlated* in
 * space, so what is left here is only the per-mark irregularity that keeps a
 * crest from being a single machined shape. Measured on nearest-neighbour
 * pairs, the correlated share has to be the larger one or the field is
 * invisible: at a jitter of ±37 % the relief scored r=0.20 and at ±22 % the
 * same field scores r=0.43.
 *
 * Water keeps a wide range on purpose — it is exempt from the relief field,
 * because a wave is a surface and not a height, so this is the only thing
 * keeping the open ocean from being a stamped repeating pattern.
 */
const CATEGORY_SIZE_SPREAD: Record<TerrainCategory, number> = {
  mountain: 0.26,
  forest: 0.18,
  water: 0.45,
  desert: 0.16,
  cave: 0.14,
  plains: 0.12,
}

/**
 * Chance an open-ground cell carries grass at all.
 *
 * Open ground is most of most maps, so this is the dial that decides whether
 * land reads as *ground* or as flat paper — and at 0.42, with hairline symbols
 * at ~0.35 effective opacity, it read as flat paper: the fit view showed whole
 * landmasses as an empty wash with a decorative crack through them.
 */
const PLAINS_DENSITY = 0.55

/**
 * Wave density out in the open ocean, away from any water seed.
 *
 * Without it, the sea between two rivers is not water but *nothing*: the
 * landmask suppresses the land symbols and the falloff suppresses the waves,
 * so zooming in on open water lands the reader on a blank rectangle. A map
 * where the ocean is the only textured thing is the wrong way round — this is
 * what makes the shoreline read as a shoreline too.
 *
 * Kept low on purpose. This is half of the ground layer's node budget at fit
 * zoom, and node count is what decides the frame time — see `CATEGORY_SIZE`.
 * 0.16 with the larger wave marks below covers the sea at ~16 %, which is
 * enough for the eye to read moving water without the ocean competing with the
 * land for attention.
 */
const OCEAN_DENSITY = 0.16

/** Constant on-screen clearance kept around every location pin, in CSS px. */
const PIN_CLEARANCE_PX = 16

/**
 * Size of one patch of the ground-cover density field, in cells.
 *
 * A uniform grid with a constant per-cell chance reads as wallpaper: every
 * cell is statistically identical, so the eye locks onto the lattice. Fading a
 * low-frequency noise field into the per-cell probability gives cover genuine
 * thickets and genuine bald ground. 5 cells ≈ 160 px at the default pitch,
 * which is roughly the size of a readable "patch" at fit zoom.
 */
const PATCH_PERIOD = 5

/**
 * Density multiplier at the driest and the lushest part of a patch.
 *
 * 0.45 → 1.20 rather than 0.62 → 1.17: a patch has to be able to go nearly
 * bare for the thickets to register as thickets. The point of the field is
 * contrast between patches, and a 1.9:1 ratio spread over a 160 px blob is
 * barely visible once the eye averages it.
 */
const PATCH_FLOOR = 0.45
const PATCH_RANGE = 0.75

// ── Symbol definitions (2–3 variants per biome) ──

const SYMBOL_DEFS: TerrainSymbolDef[] = [
  // ── Mountain ──────────────────────────────────
  // Three layers per peak: body, shadowed flank, outline.
  //
  // The shadow and the outline are black at low opacity, *not* a second entry
  // from the palette, and that is deliberate. The palette flips between the
  // light and dark themes, so a hard-coded dark tone would be correct in one
  // and inverted in the other; black-at-0.16 over whatever colour the `<use>`
  // inherits is a relative shade, and relative is what a shaded face is.
  //
  // Why it matters: at deep zoom the mountain category is roughly 70 % of all
  // ground marks, so the peak glyph *is* the texture the reader sees when they
  // zoom in. A solid equilateral triangle at ±12 % scale is a printed pattern;
  // a peaked silhouette with a lit face and an edge is a drawn mountain.
  {
    id: "terrain-mountain-0",
    viewBox: "0 0 14 14",
    pathData:
      '<path d="M 0,14 L 7,0 L 14,14 Z"/>' +
      '<path d="M 0,14 L 7,0 L 7,14 Z" fill="#000" fill-opacity="0.16"/>' +
      '<path d="M 0,14 L 7,0 L 14,14 Z" fill="none" stroke="#000"' +
      ' stroke-opacity="0.24" stroke-width="0.7" stroke-linejoin="round"/>',
  },
  {
    id: "terrain-mountain-1",
    viewBox: "0 0 16 12",
    pathData:
      '<path d="M 0,12 L 8,0 L 16,12 Z"/>' +
      '<path d="M 0,12 L 8,0 L 8,12 Z" fill="#000" fill-opacity="0.16"/>' +
      '<path d="M 0,12 L 8,0 L 16,12 Z" fill="none" stroke="#000"' +
      ' stroke-opacity="0.24" stroke-width="0.7" stroke-linejoin="round"/>',
  },
  // Ridge — 3 overlapping peaks (signature)
  {
    id: "terrain-mountain-2",
    viewBox: "0 0 24 14",
    pathData:
      '<path d="M 0,14 L 5,3 L 10,14 Z" opacity="0.72"/>' +
      '<path d="M 4,14 L 12,0 L 20,14 Z"/>' +
      '<path d="M 4,14 L 12,0 L 12,14 Z" fill="#000" fill-opacity="0.16"/>' +
      '<path d="M 14,14 L 19,5 L 24,14 Z" opacity="0.85"/>' +
      '<path d="M 4,14 L 12,0 L 20,14 Z" fill="none" stroke="#000"' +
      ' stroke-opacity="0.24" stroke-width="0.7" stroke-linejoin="round"/>',
  },

  // ── Water ─────────────────────────────────────
  {
    id: "terrain-water-0",
    viewBox: "0 0 16 10",
    pathData: '<path d="M 0,5 Q 4,0 8,5 Q 12,10 16,5"/>',
    strokeOnly: true,
  },
  {
    id: "terrain-water-1",
    viewBox: "0 0 16 12",
    pathData:
      '<path d="M 0,4 Q 4,0 8,4 Q 12,8 16,4"/>' +
      '<path d="M 0,9 Q 4,5 8,9 Q 12,13 16,9"/>',
    strokeOnly: true,
  },
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
  {
    id: "terrain-forest-0",
    viewBox: "0 0 12 16",
    pathData: '<path d="M 6,0 L 11,7 L 8.5,7 L 8.5,14 L 3.5,14 L 3.5,7 L 1,7 Z"/>',
  },
  {
    id: "terrain-forest-1",
    viewBox: "0 0 12 16",
    pathData:
      '<circle cx="6" cy="5" r="5"/>' +
      '<rect x="4.5" y="10" width="3" height="6"/>',
  },
  // Stand of 3 trees (signature dense forest)
  {
    id: "terrain-forest-2",
    viewBox: "0 0 22 18",
    pathData:
      '<circle cx="5" cy="5" r="4.5"/><rect x="3.5" y="9.5" width="3" height="5"/>' +
      '<circle cx="14" cy="4" r="5"/><rect x="12.5" y="9" width="3" height="5.5"/>' +
      '<circle cx="9" cy="8" r="4"/><rect x="7.5" y="12" width="3" height="4.5"/>',
  },

  // ── Desert ────────────────────────────────────
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
  // Dune — two long wind arcs
  {
    id: "terrain-desert-2",
    viewBox: "0 0 20 12",
    pathData:
      '<path d="M 0,8 Q 5,2 10,8 Q 15,14 20,8"/>' +
      '<path d="M 2,4 Q 6,0 10,4" opacity="0.7"/>',
    strokeOnly: true,
  },

  // ── Cave ──────────────────────────────────────
  {
    id: "terrain-cave-0",
    viewBox: "0 0 14 12",
    pathData: '<path d="M 0,0 L 7,12 L 14,0 Z"/>',
  },
  {
    id: "terrain-cave-1",
    viewBox: "0 0 16 10",
    pathData: '<path d="M 0,0 L 8,10 L 16,0 Z"/>',
  },

  // ── Plains (open ground) ──────────────────────
  // Grass tuft — 3 blades
  {
    id: "terrain-plains-0",
    viewBox: "0 0 14 13",
    pathData:
      '<path d="M 3,12 Q 2,7 4.5,2"/>' +
      '<path d="M 7,12 Q 7,6 7,1"/>' +
      '<path d="M 11,12 Q 12,7 9.5,2"/>',
    strokeOnly: true,
  },
  // Scrub — two low arcs
  {
    id: "terrain-plains-1",
    viewBox: "0 0 14 12",
    pathData:
      '<path d="M 0,10 Q 3.5,5 7,10 Q 10.5,5 14,10"/>' +
      '<path d="M 3,7 Q 5.5,4 8,7" opacity="0.75"/>',
    strokeOnly: true,
  },
  // Mottle — scattered pebbles, and the only *filled* plains mark.
  //
  // Open ground built only from hairline strokes has no body: a 1.1 px arc at
  // 0.35 effective opacity is not texture, it is nothing, which is how whole
  // landmasses came out as empty paper. A filled mark also survives being
  // scaled down, where a stroke thins away.
  {
    id: "terrain-plains-2",
    viewBox: "0 0 14 14",
    pathData:
      '<circle cx="3.5" cy="4" r="1.1"/>' +
      '<circle cx="9.5" cy="2.8" r="0.85"/>' +
      '<circle cx="6.5" cy="8" r="1.25"/>' +
      '<circle cx="11.6" cy="10.2" r="0.8"/>' +
      '<circle cx="2.4" cy="10.6" r="0.9"/>',
  },
]

// Category → symbol IDs
const CATEGORY_SYMBOLS: Record<TerrainCategory, string[]> = {
  mountain: ["terrain-mountain-0", "terrain-mountain-1", "terrain-mountain-2"],
  water:    ["terrain-water-0", "terrain-water-1", "terrain-water-2"],
  forest:   ["terrain-forest-0", "terrain-forest-1", "terrain-forest-2"],
  desert:   ["terrain-desert-0", "terrain-desert-1", "terrain-desert-2"],
  cave:     ["terrain-cave-0", "terrain-cave-1"],
  plains:   ["terrain-plains-0", "terrain-plains-1", "terrain-plains-2"],
}

// ── Color palettes ────────────────────────────────

const COLORS_LIGHT: Record<TerrainCategory, string> = {
  mountain: "#8b7355",
  water:    "#6b8fa3",
  forest:   "#6b8b5c",
  desert:   "#b09870",
  cave:     "#8b7355",
  plains:   "#8f8560",
}

const COLORS_DARK: Record<TerrainCategory, string> = {
  mountain: "#c4a97d",
  water:    "#7bb5d0",
  forest:   "#8fad7e",
  desert:   "#c4b48a",
  cave:     "#a08e6e",
  plains:   "#a99b78",
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

// ── Low-frequency density field ───────────────────

function smoothstep(t: number): number {
  return t * t * (3 - 2 * t)
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v
}

function patchNode(gx: number, gy: number): number {
  return pseudoRandom(hashString(`patch:${gx}:${gy}`))
}

/**
 * Value noise sampled on a lattice of PATCH_PERIOD cells.
 *
 * Chosen over a sum of sinusoids on purpose: sinusoids impose a direction and a
 * wavelength, so two of them in x/y produce visible diagonal banding — a
 * different wallpaper, not the absence of one. Hashed lattice nodes with a
 * smoothstep blend have no preferred orientation.
 */
function patchDensity(ix: number, iy: number): number {
  const gx = ix / PATCH_PERIOD
  const gy = iy / PATCH_PERIOD
  const x0 = Math.floor(gx)
  const y0 = Math.floor(gy)
  const fx = smoothstep(gx - x0)
  const fy = smoothstep(gy - y0)
  const v00 = patchNode(x0, y0)
  const v10 = patchNode(x0 + 1, y0)
  const v01 = patchNode(x0, y0 + 1)
  const v11 = patchNode(x0 + 1, y0 + 1)
  const top = v00 + (v10 - v00) * fx
  const bottom = v01 + (v11 - v01) * fx
  return top + (bottom - top) * fy
}

// ── World-space relief field ──────────────────────

/**
 * Octave wavelengths in **canvas units**, coarse to fine, with their weights.
 *
 * Why this exists at all. Everything above is a *screen*-space mechanism: the
 * candidate grid is pitched at `CELL_PX` on screen, so a cell is `CELL_PX / k`
 * canvas units and the candidate count is ~105×62 at every zoom level. What
 * changes when the reader zooms in is only the biome that happens to be under
 * the window — and within one biome seed's reach the category is constant and
 * the falloff only scales density. So the deep-zoom view was a field of
 * identically-sized glyphs at roughly constant spacing: 953 mountains, 360
 * caves, 85 forests, all the same size, all the same distance apart. That is
 * the "wallpaper" reading, and no amount of per-glyph random size fixes it,
 * because uncorrelated random size *is* noise.
 *
 * Hand-drawn and game maps convey relief with a correlated field: glyphs grow
 * toward a crest, thin out in a hollow, and the crest continues across the
 * page. So the field has to live in canvas units — a *world* property, like
 * `BIOME_REACH` — and that is the whole point of this constant: wavelengths
 * are fixed in the world, so zooming in reveals finer octaves instead of
 * showing the same blob bigger.
 *
 * The fineness stops at 24 units deliberately. At k=10 (the deepest the
 * reader can go) that is a 240 px feature with a cell of 1.6 units — enough
 * cells across one ridge to draw it. A second pair of finer octaves would be
 * cheaper to add than to justify: below ~1.5 cells the field stops being
 * correlated between neighbouring glyphs and turns back into the noise it is
 * meant to replace.
 */
const RELIEF_OCTAVES: ReadonlyArray<readonly [number, number]> = [
  [1400, 1.0],
  [520, 0.52],
  [190, 0.27],
  [66, 0.14],
  [24, 0.07],
]

/**
 * How much a biome seed lifts or drops the relief around it, before the
 * noise detail is added.
 *
 * This is the *authored* half of the field, and it is what keeps the relief
 * agreeing with the story: a 山 raises the ground near it, a 河 lowers it, and
 * between locations the noise takes over. Without it, the coarse octaves would
 * put a mountain range wherever the hash felt like it, next to a location
 * called 花果山 that is rendered flat.
 */
const SEED_RELIEF: Record<TerrainCategory, number> = {
  mountain: 1.0,
  cave: 0.7,
  forest: 0.42,
  plains: 0.05,
  desert: -0.3,
  water: -1.0,
}

/**
 * Integer hash for lattice nodes.
 *
 * `hashString('relief:1:2')` would do, but this runs 4 times per octave per
 * candidate cell — 20 per cell, ~130k per rebuild at fit zoom — and building a
 * template literal 130k times per frame is a garbage-collection bill the
 * scatter cannot afford. `Math.imul` keeps the multiply 32-bit and exact.
 */
function hash2i(x: number, y: number, salt: number): number {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^
    Math.imul(salt, 2246822519)
  h = Math.imul(h ^ (h >>> 13), 1274126177)
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

function reliefNode(gx: number, gy: number, salt: number): number {
  return hash2i(gx, gy, salt)
}

/**
 * Multi-octave value noise in 0..1, at the given world point.
 *
 * `minWl` is the level-of-detail cut, and the octaves are *faded* across it
 * rather than dropped at it. Dropping is the obvious implementation and it
 * pops: while the reader wheels through the threshold, one octave's worth of
 * amplitude (~7 % of the field) appears in a single frame, and on a smooth
 * field that is visible as the whole ground shivering and re-settling. Fading
 * costs one extra `smoothstep` and removes the artefact entirely.
 *
 * The cut exists for two reasons, and the second is the important one. The
 * cheap one is cost. The real one is that an octave finer than the glyph pitch
 * does not read as terrain — neighbouring glyphs land on opposite sides of it,
 * so it *is* the uncorrelated size jitter this field is replacing, minus the
 * honesty. Fading it out means the visible detail always has a wavelength the
 * scatter can actually resolve.
 */
function reliefNoise(x: number, y: number, minWl: number, salt: number): number {
  let v = 0
  let amp = 0
  for (const [wl, a] of RELIEF_OCTAVES) {
    const f = smoothstep(clamp01((wl / minWl - 0.8) / 1.2))
    if (f <= 0) continue
    v += valueNoise2(x / wl, y / wl, salt) * a * f
    amp += a * f
  }
  return amp > 0 ? v / amp : 0.5
}

function valueNoise2(gx: number, gy: number, salt: number): number {
  const x0 = Math.floor(gx)
  const y0 = Math.floor(gy)
  const fx = smoothstep(gx - x0)
  const fy = smoothstep(gy - y0)
  const v00 = reliefNode(x0, y0, salt)
  const v10 = reliefNode(x0 + 1, y0, salt)
  const v01 = reliefNode(x0, y0 + 1, salt)
  const v11 = reliefNode(x0 + 1, y0 + 1, salt)
  const top = v00 + (v10 - v00) * fx
  const bottom = v01 + (v11 - v01) * fx
  return top + (bottom - top) * fy
}

/**
 * Per-novel salt for the relief field.
 *
 * Without it every map in the library would have *the same* hills in the same
 * places, because the noise is a function of canvas coordinates and nothing
 * else — the two novels whose layouts differ only in where the locations sit
 * would share an identical undulation. Deriving the salt from the location set
 * keeps the field stable for a given novel across sessions and different for
 * every novel, without threading a novel id through the call chain.
 *
 * Memoised on the array identity: the caller passes a stable array, and
 * re-sorting ~800 names on every pan rebuild would cost more than the field.
 */
const reliefSaltCache = new WeakMap<object, number>()

function reliefSalt(locations: MapLocation[]): number {
  const hit = reliefSaltCache.get(locations)
  if (hit !== undefined) return hit
  const names = locations.map((l) => l.name).sort()
  const salt = Math.floor(hashString(names.join("|")) % 2147483647)
  reliefSaltCache.set(locations, salt)
  return salt
}

/**
 * How far the relief field is allowed to move a glyph's size, density and
 * opacity, as multipliers at relief 0 and relief 1.
 *
 * Density gets the widest range on purpose. Size and opacity are read one
 * glyph at a time; density is read as *clumping*, and clumping is what the eye
 * actually uses to decide that a band of glyphs is a ridge rather than a
 * scatter — it is the same quantity the nearest-neighbour CV scores.
 */
const RELIEF_SIZE = [0.74, 1.42] as const
const RELIEF_DENSITY = [0.58, 1.55] as const
const RELIEF_OPACITY = [0.84, 1.16] as const

/**
 * Relief threshold below which a mountain or forest becomes open ground, and
 * the relief above which open ground becomes a mountain.
 *
 * Both are applied as a *probability*, not a cut. A hard threshold on a smooth
 * field draws its own contour line across the map, and a visible iso-line is a
 * worse artefact than the uniformity being fixed — the reader sees the
 * algorithm. Ramping the probability over a band of relief keeps the boundary
 * ragged, which is what a real treeline looks like.
 */
const HOLLOW_BELOW = 0.44
const RIDGE_ABOVE = 0.76

// ── Main generator ────────────────────────────────

export function generateTerrainHints(
  locations: MapLocation[],
  layout: MapLayoutItem[],
  canvasSize: { width: number; height: number },
  darkBg: boolean,
  zoom = 1,
  view: TerrainViewRect | null = null,
  land: Landmass[] | null = null,
): TerrainHintResult {
  const layoutMap = new Map<string, MapLayoutItem>()
  for (const item of layout) layoutMap.set(item.name, item)

  const colorPalette = darkBg ? COLORS_DARK : COLORS_LIGHT
  const baseOpacity = darkBg ? 0.52 : 0.62
  const k = zoom > 0 ? zoom : 1

  // Seed for the world-space relief field. See `reliefSalt`.
  const rsalt = reliefSalt(locations)

  // ── Biome seeds ─────────────────────────────────
  // Every terrain-typed place paints its surroundings. Deliberately *not*
  // filtered by the mention-count slider: ground cover is scenery, and a
  // decoration that appears and disappears as the reader drags a filter reads
  // as a glitch. `allLocations`/`allLayout` are passed for this reason.
  interface Seed { x: number; y: number; cat: TerrainCategory }
  const seeds: Seed[] = []
  for (const loc of locations) {
    const cat = TERRAIN_MAP[loc.icon ?? ""]
    if (!cat) continue
    const item = layoutMap.get(loc.name)
    if (!item || item.is_portal) continue
    seeds.push({ x: item.x, y: item.y, cat })
  }
  if (seeds.length === 0) {
    return { symbolDefs: [], hints: [] }
  }

  // ── Grid extent ─────────────────────────────────
  const fullW = canvasSize.width
  const fullH = canvasSize.height

  // ── World scale ─────────────────────────────────
  // `BIOME_REACH` is authored against a 4500-unit short side. Dividing by that
  // reference turns it from an absolute pixel count into a fraction of the map,
  // so a 2000×1200 canvas gets reaches 4× smaller rather than painting a
  // continent's worth of mountains onto a thumbnail.
  const worldScale = Math.min(fullW, fullH) / BIOME_REF_MIN_SIDE

  const rect = view ?? { x: 0, y: 0, w: fullW, h: fullH }
  const x0 = Math.max(0, Math.min(fullW, rect.x))
  const y0 = Math.max(0, Math.min(fullH, rect.y))
  const x1 = Math.max(0, Math.min(fullW, rect.x + rect.w))
  const y1 = Math.max(0, Math.min(fullH, rect.y + rect.h))
  const spanW = x1 - x0
  const spanH = y1 - y0
  if (spanW <= 0 || spanH <= 0) {
    return { symbolDefs: [], hints: [] }
  }

  // Grid pitch: CELL_PX on screen, so `CELL_PX / k` in canvas units. Widened
  // only if the visible area would blow the node budget (a very wide viewport
  // at a very low zoom).
  let cell = CELL_PX / k
  const cols = Math.ceil(spanW / cell)
  const rows = Math.ceil(spanH / cell)
  if (cols * rows > MAX_CELLS) {
    cell *= Math.sqrt((cols * rows) / MAX_CELLS)
  }

  // Level-of-detail cut for the relief field: octaves finer than this are
  // skipped, because a wavelength the glyph pitch cannot resolve is not
  // terrain, it is jitter. See `reliefNoise`.
  const minWl = cell * 1.5

  // ── Constant on-screen clearance around pins ────
  const minSep = PIN_CLEARANCE_PX / k
  const minSep2 = minSep * minSep
  const pins: { x: number; y: number }[] = []
  for (const item of layout) {
    if (!item.is_portal) pins.push({ x: item.x, y: item.y })
  }

  // ── Landmask ────────────────────────────────────
  // Coastlines live in the same canvas coordinate space as the layout (the
  // ocean fill in NovelMap strokes them straight onto canvasW×canvasH), so a
  // point-in-polygon test against them is the whole land test: inside any
  // coastline and outside that landmass's holes, which are inner seas.
  interface Ring {
    pts: [number, number][]
    minX: number
    minY: number
    maxX: number
    maxY: number
  }
  /** A ring, plus the y-band index `ringContains` walks it by. */
  interface IndexedRing extends Ring {
    isHole: boolean
    /** Row index -> edge indices into `pts`. Sparse; only non-empty rows. */
    buckets: Map<number, number[]>
  }

  /**
   * Tallest edge of a ring, measured as a rise in y.
   *
   * Screens the row height against the data. Only y matters here: the index
   * has no columns, so an edge that is long and flat costs nothing while one
   * that climbs across the canvas would land in every row it passes.
   */
  const maxEdgeRise = (pts: [number, number][]): number => {
    let m = 0
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const d = Math.abs(pts[i][1] - pts[j][1])
      if (d > m) m = d
    }
    return m
  }

  const toRing = (pts: [number, number][]): Ring => {
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (const p of pts) {
      if (p[0] < minX) minX = p[0]
      if (p[0] > maxX) maxX = p[0]
      if (p[1] < minY) minY = p[1]
      if (p[1] > maxY) maxY = p[1]
    }
    return { pts, minX, minY, maxX, maxY }
  }
  const ringList: IndexedRing[] = []
  let tallest = 0
  for (const lm of land ?? []) {
    const outline = toRing(lm.coastline)
    if (outline.pts.length >= 3) {
      tallest = Math.max(tallest, maxEdgeRise(outline.pts))
      ringList.push({ ...outline, isHole: false, buckets: new Map() })
    }
    for (const h of lm.holes ?? []) {
      const r = toRing(h)
      if (r.pts.length < 3) continue
      tallest = Math.max(tallest, maxEdgeRise(r.pts))
      ringList.push({ ...r, isHole: true, buckets: new Map() })
    }
  }

  // ── Y-band index for the landmask ───────────────
  // `ringContains` is a ray cast over every vertex of a ring, and a rebuild
  // asks it once per candidate cell — 427 ms of self time over one drag, two
  // thirds of the layer's JavaScript. The ring bboxes skipped in `isOnLand`
  // cut it down but not enough: a cell in the middle of a continent is nowhere
  // near a coastline and still walked every vertex.
  //
  // The index is over **rows only, never columns**, and the reason is worth
  // keeping: the first attempt bucketed edges into 2-D boxes, on the argument
  // that an edge which can flip the parity for (px,py) must have (px,py) in
  // its bounding box. That argument is false, and measurably so — it reported
  // 14 569 land points where the scan reported 615 279 over the same grid.
  //
  // The crossing the test counts is at (x_cross, py) with x_cross < px, so the
  // edge's bbox contains a point far to the *left* of the query, not the query
  // itself. A column bucket therefore misses every edge that ends its run in
  // the query's column and crosses the ray from outside it. With this
  // primitive there is no valid x-prune at all: the parity needs every edge of
  // the row, which is why the index is one-dimensional.
  //
  // Edges with no vertical extent are dropped outright — `yi > py !== yj > py`
  // is false whenever `yi === yj`, so a horizontal edge can never contribute.
  // Row height is a quarter of the tallest edge's rise, so an edge lands in at
  // most four rows and the build stays O(vertices). Empty rows cost one Map
  // miss and allocate nothing.
  const rowH = Math.max(tallest / 4, 1)
  const rowCount = Math.max(1, Math.ceil(fullH / rowH))

  const indexRing = (r: IndexedRing) => {
    const pts = r.pts
    const n = pts.length
    for (let i = 0, j = n - 1; i < n; j = i++) {
      const ay = pts[j][1]
      const by = pts[i][1]
      if (ay === by) continue
      let y0 = Math.floor(Math.min(ay, by) / rowH)
      let y1 = Math.floor(Math.max(ay, by) / rowH)
      if (y0 < 0) y0 = 0
      if (y1 > rowCount - 1) y1 = rowCount - 1
      for (let yy = y0; yy <= y1; yy++) {
        const list = r.buckets.get(yy)
        if (list) list.push(i)
        else r.buckets.set(yy, [i])
      }
    }
  }
  for (const r of ringList) indexRing(r)

  const ringContains = (r: IndexedRing, px: number, py: number): boolean => {
    const by = Math.floor(py / rowH)
    if (by < 0 || by >= rowCount) return false
    const list = r.buckets.get(by)
    if (!list) return false
    const pts = r.pts
    const n = pts.length
    let inside = false
    for (let q = 0; q < list.length; q++) {
      const i = list[q]
      const j = i === 0 ? n - 1 : i - 1
      const xi = pts[i][0]
      const yi = pts[i][1]
      const xj = pts[j][0]
      const yj = pts[j][1]
      if (
        yi > py !== yj > py &&
        px < ((xj - xi) * (py - yi)) / (yj - yi) + xi
      ) {
        inside = !inside
      }
    }
    return inside
  }

  // A hole suppresses only its own outline, which is why this is a scan over
  // every ring rather than a single parity count: two landmasses may overlap
  // on the canvas, and one shared even-odd total would cancel them out.
  const isOnLand = (px: number, py: number): boolean => {
    for (const r of ringList) {
      if (
        px < r.minX ||
        px > r.maxX ||
        py < r.minY ||
        py > r.maxY
      ) {
        continue
      }
      if (r.isHole) {
        if (ringContains(r, px, py)) continue
      } else if (ringContains(r, px, py)) {
        return true
      }
    }
    return false
  }

  // Guard against a landmask that is present but meaningless — a coastline set
  // in a different coordinate space would delete the entire layer.
  //
  // Deliberately sampled over the *whole canvas*, not over the current view.
  // Sampling the view makes the guard indistinguishable from "the reader has
  // zoomed into open water": the sample comes back 100 % sea, the guard
  // declares the mask broken, and it switches off — which is exactly when the
  // mask is needed most. Land cover is a property of the map, so the test has
  // to be too.
  let useLandmask = ringList.length > 0
  if (useLandmask) {
    const stride = Math.max(48, fullW / 24)
    let seen = 0
    let hit = 0
    for (let wy = stride * 0.5; wy < fullH; wy += stride) {
      for (let wx = stride * 0.5; wx < fullW; wx += stride) {
        seen++
        if (isOnLand(wx, wy)) hit++
      }
    }
    if (seen > 0 && hit / seen < 0.1) useLandmask = false
  }

  // ── Scatter ─────────────────────────────────────
  // Flat arrays keep the nearest-seed search cheap: a rebuild walks
  // cells × seeds (≈ 600 × 200 here), which is a couple of milliseconds.
  const sx = new Float64Array(seeds.length)
  const sy = new Float64Array(seeds.length)
  const scat = new Array<TerrainCategory>(seeds.length)
  for (let i = 0; i < seeds.length; i++) {
    sx[i] = seeds[i].x
    sy[i] = seeds[i].y
    scat[i] = seeds[i].cat
  }

  const hints: TerrainHint[] = []
  // Parallel to `hints`, needed because the node budget below can remove a
  // category's last members, and the emitted `<defs>` must match what is
  // actually on screen — a `<use>` pointing at a missing symbol renders nothing.
  const cats: TerrainCategory[] = []

  // Water seeds in their own list so an ocean cell can ask "is any sea within
  // reach?" without rescanning every seed. Only ~50 of 800 here.
  const waterIdx: number[] = []
  for (let i = 0; i < scat.length; i++) {
    if (scat[i] === "water") waterIdx.push(i)
  }
  const waterReach = BIOME_REACH.water * worldScale
  const nearestWaterFalloff = (px: number, py: number): number => {
    if (waterIdx.length === 0) return -1
    let bd2 = Infinity
    for (const i of waterIdx) {
      const dx = px - sx[i]
      const dy = py - sy[i]
      const d2 = dx * dx + dy * dy
      if (d2 < bd2) bd2 = d2
    }
    if (bd2 >= waterReach * waterReach) return -1
    return 1 - Math.sqrt(bd2) / waterReach
  }

  const ix0 = Math.floor(x0 / cell)
  const ix1 = Math.ceil(x1 / cell)
  const iy0 = Math.floor(y0 / cell)
  const iy1 = Math.ceil(y1 / cell)

  for (let iy = iy0; iy < iy1; iy++) {
    for (let ix = ix0; ix < ix1; ix++) {
      // Seeded by the *absolute* grid index, so the ground is identical after
      // any pan or zoom round-trip instead of re-rolling.
      const seed = hashString(`${ix}:${iy}`)

      // Row stagger first. Jittering each cell on its own is not enough to
      // break a lattice: every point stays offset by the same fraction of its
      // own cell, so the columns still line up. Shifting whole rows by a
      // different fraction destroys the alignment outright.
      const rowShift = (pseudoRandom(hashString(`row:${iy}`)) - 0.5) * cell * 0.9
      const jx = (pseudoRandom(seed) - 0.5) * cell * 0.85
      const jy = (pseudoRandom(seed + 1) - 0.5) * cell * 0.85
      const px = (ix + 0.5) * cell + rowShift + jx
      const py = (iy + 0.5) * cell + jy

      if (px < 0 || px > fullW || py < 0 || py > fullH) continue

      let blocked = false
      for (const p of pins) {
        const dx = px - p.x
        const dy = py - p.y
        if (dx * dx + dy * dy < minSep2) {
          blocked = true
          break
        }
      }
      if (blocked) continue

      // Nearest biome seed + linear falloff. The falloff is what keeps a
      // single 山 from carpeting the quadrant and what carves the transition
      // from massif to grassland.
      let best = -1
      let bestD2 = Infinity
      for (let i = 0; i < sx.length; i++) {
        const dx = px - sx[i]
        const dy = py - sy[i]
        const d2 = dx * dx + dy * dy
        if (d2 < bestD2) {
          bestD2 = d2
          best = i
        }
      }

      let cat: TerrainCategory = "plains"
      let density = PLAINS_DENSITY
      let falloff = 0
      if (best >= 0) {
        const reach = BIOME_REACH[scat[best]] * worldScale
        if (reach > 0) {
          const dist = Math.sqrt(bestD2)
          const t = 1 - dist / reach
          if (t > 0) {
            cat = scat[best]
            falloff = Math.min(1, t)
            density = 0.45 + 0.55 * falloff
          }
        }
      }

      // ── Surface test ─────────────────────────────
      // Two kinds of ground, not one. On land the biome field above decides
      // what grows there; on sea the answer is always waves. Everything in the
      // ocean is water, whether or not a river seed is nearby — the seed only
      // decides whether the waves are thick (near shore) or thin (mid sea).
      if (useLandmask && cat !== "water" && !isOnLand(px, py)) {
        cat = "water"
        const wt = nearestWaterFalloff(px, py)
        density = wt > 0 ? 0.45 + 0.55 * Math.min(1, wt) : OCEAN_DENSITY
      }

      // ── Relief ───────────────────────────────────
      // The authored half is the nearest seed's sign, weighted by how close
      // that seed is; the procedural half is the world-space noise. Splitting
      // it this way is what stops the field from contradicting the story: a
      // 山 has to be high ground even if the hash disagrees, and away from
      // any location the noise is free to make its own ranges.
      //
      // Water is exempt. Sea has no relief to describe — the waves are a
      // surface, not a height — and letting the field darken the middle of an
      // ocean would read as a shoal that is not in the data.
      let relief = 0.5
      if (cat !== "water") {
        const authored = best >= 0 ? SEED_RELIEF[scat[best]] * falloff : 0
        const detail = (reliefNoise(px, py, minWl, rsalt) - 0.5) * 2
        relief = clamp01(0.5 + 0.30 * authored + 0.20 * detail)

        // ── Category refinement ─────────────────────
        // The biome field says what a location *is*; the relief field says
        // where its ground actually stands. A massif is not a plateau — it
        // has hollows — and open ground next to a range has spurs that climb
        // into it. Ramping the probability keeps both boundaries ragged, which
        // is what stops the relief from drawing contour lines across the map.
        if (relief < HOLLOW_BELOW && (cat === "mountain" || cat === "cave")) {
          const p = ((HOLLOW_BELOW - relief) / HOLLOW_BELOW) * 0.9
          if (pseudoRandom(seed + 7) < p) cat = "plains"
        } else if (relief > RIDGE_ABOVE && cat === "plains") {
          const p = ((relief - RIDGE_ABOVE) / (1 - RIDGE_ABOVE)) * 0.85
          if (pseudoRandom(seed + 8) < p) cat = "mountain"
        }
      }

      // Patch field. Indexed in *grid* space, not canvas space, so a patch
      // stays anchored to its cells and does not slide across the map while
      // the reader pans.
      density *= PATCH_FLOOR + PATCH_RANGE * patchDensity(ix, iy)
      if (cat !== "water") {
        density *= RELIEF_DENSITY[0] + (RELIEF_DENSITY[1] - RELIEF_DENSITY[0]) * relief
      }

      if (pseudoRandom(seed + 2) > density) continue

      // Size and opacity follow the same field, so a ridge crest carries
      // larger, darker marks than the hollows beside it — the gradient the eye
      // reads as relief. Only the spread of *neighbouring* sizes matters here,
      // which is why it is driven by the field and not by `pseudoRandom`: an
      // uncorrelated ±25 % is indistinguishable from a printing defect.
      const relSize = cat === "water"
        ? 1
        : RELIEF_SIZE[0] + (RELIEF_SIZE[1] - RELIEF_SIZE[0]) * relief
      const relOp = cat === "water"
        ? 1
        : RELIEF_OPACITY[0] + (RELIEF_OPACITY[1] - RELIEF_OPACITY[0]) * relief
      const size =
        CATEGORY_SIZE[cat] * relSize *
        (1 + (pseudoRandom(seed + 3) - 0.5) * CATEGORY_SIZE_SPREAD[cat])
      const rotation = (pseudoRandom(seed + 4) - 0.5) * 28
      // Open ground stays quieter than a massif or a forest — but only a
      // little. At 0.72 the two effects compounded (low density × low opacity)
      // into ground that was not there.
      const opFactor = cat === "plains" ? 0.95 : 1
      const opacity =
        baseOpacity * opFactor * relOp * (0.6 + 0.4 * pseudoRandom(seed + 5))

      const symbols = CATEGORY_SYMBOLS[cat]
      let symbolIdx: number
      if (symbols.length >= 3 && pseudoRandom(seed + 6) < 0.32) {
        symbolIdx = 2 // cluster / ridge / dune variant
      } else {
        symbolIdx = (seed + ix + iy) % Math.min(symbols.length, 2)
      }

      hints.push({
        symbolId: symbols[symbolIdx],
        x: px,
        y: py,
        size,
        rotation,
        opacity,
        color: colorPalette[cat],
      })
      cats.push(cat)
    }
  }

  // ── Node budget ────────────────────────────────
  // See the NODE_BUDGET comment above the constant: it only bites when the
  // whole viewport is land, which is the deep-zoom case, and it is the
  // difference between a 59 ms and a 178 ms drag.
  const usedCategories = new Set<TerrainCategory>()
  if (hints.length > NODE_BUDGET) {
    const keep = NODE_BUDGET / hints.length
    let w = 0
    for (let r = 0; r < hints.length; r++) {
      if (pseudoRandom(hashString(`thin:${hints[r].x}:${hints[r].y}`)) >= keep) continue
      hints[w] = hints[r]
      cats[w] = cats[r]
      w++
    }
    hints.length = w
    cats.length = w
  }
  for (const c of cats) usedCategories.add(c)

  const symbolDefs = SYMBOL_DEFS.filter((sd) => {
    for (const cat of usedCategories) {
      if (CATEGORY_SYMBOLS[cat].includes(sd.id)) return true
    }
    return false
  })

  return { symbolDefs, hints }
}
