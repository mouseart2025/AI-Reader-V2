/** Demo novel slug ↔ metadata mapping */

export interface DemoNovelInfo {
  slug: string
  title: string
  dataPath: string
  totalChapters: number
  stats: { characters: number; relations: number; locations: number; events: number }
}

// Only novels analyzed with v0.71.1+ pipeline are exposed to the public demo —
// older analyses (v0.59~v0.69 era) contain canonical bugs / location issues
// that have since been fixed. Re-analyze before re-adding.
const DEMO_NOVELS: DemoNovelInfo[] = [
  // ── 五大经典(v0.71.1+ 重分析) ──
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
  {
    slug: "shuihu",
    title: "水浒传",
    dataPath: "/demo-data/shuihu",
    totalChapters: 121,
    stats: { characters: 1040, relations: 1745, locations: 1276, events: 4667 },
  },
  {
    slug: "sanguo",
    title: "三国演义",
    dataPath: "/demo-data/sanguo",
    totalChapters: 120,
    stats: { characters: 1198, relations: 1857, locations: 980, events: 4542 },
  },
  {
    slug: "fengshen",
    title: "封神演义",
    dataPath: "/demo-data/fengshen",
    totalChapters: 90,
    stats: { characters: 735, relations: 1148, locations: 469, events: 3148 },
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
