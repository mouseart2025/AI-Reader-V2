/** Demo novel slug ↔ metadata mapping */

export interface DemoNovelInfo {
  slug: string
  title: string
  dataPath: string
  totalChapters: number
  stats: { characters: number; relations: number; locations: number; events: number }
}

// Public demo only exposes 红楼梦 + 西游记 (per-chapter files complete under
// chapters/ch-NNN.json.gz). 水浒/三国/封神 lack per-chapter files in
// landing/demo/demo-data/, so opening any chapter would 404 → SPA fallback
// returns index.html → JSON parse error in the reader. Re-add only after
// per-chapter files are generated.
const DEMO_NOVELS: DemoNovelInfo[] = [
  {
    slug: "honglou",
    title: "红楼梦",
    dataPath: "/demo-data/honglou",
    totalChapters: 122,
    stats: { characters: 593, relations: 931, locations: 618, events: 2974 },
  },
  {
    slug: "xiyouji",
    title: "西游记",
    dataPath: "/demo-data/xiyouji",
    totalChapters: 100,
    stats: { characters: 812, relations: 809, locations: 693, events: 2632 },
  },
]

export function getDemoNovel(slug: string): DemoNovelInfo | undefined {
  return DEMO_NOVELS.find((n) => n.slug === slug)
}

export function getAllDemoNovels(): DemoNovelInfo[] {
  return DEMO_NOVELS
}

/** File names for each demo data endpoint */
export const DEMO_FILES = {
  novel: "novel.json.gz",
  chapters: "chapters.json.gz",
  graph: "graph.json.gz",
  map: "map.json.gz",
  timeline: "timeline.json.gz",
  encyclopedia: "encyclopedia.json.gz",
  "encyclopedia-stats": "encyclopedia-stats.json.gz",
  factions: "factions.json.gz",
  "world-structure": "world-structure.json.gz",
} as const

export type DemoEndpoint = keyof typeof DEMO_FILES
