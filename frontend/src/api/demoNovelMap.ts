/** Demo novel slug ↔ metadata mapping */

export interface DemoNovelInfo {
  slug: string
  title: string
  dataPath: string
  totalChapters: number
  stats: { characters: number; relations: number; locations: number; events: number }
}

const DEMO_NOVELS: DemoNovelInfo[] = [
  {
    slug: "honglou",
    title: "红楼梦",
    dataPath: "/demo-data/honglou",
    totalChapters: 122,
    stats: { characters: 669, relations: 776, locations: 756, events: 2591 },
  },
  {
    slug: "xiyouji",
    title: "西游记",
    dataPath: "/demo-data/xiyouji",
    totalChapters: 100,
    stats: { characters: 747, relations: 619, locations: 817, events: 2325 },
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
