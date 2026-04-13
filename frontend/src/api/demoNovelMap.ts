/** Demo novel slug ↔ metadata mapping */

export interface DemoNovelInfo {
  slug: string
  title: string
  dataPath: string
  totalChapters: number
  stats: { characters: number; relations: number; locations: number; events: number }
}

const DEMO_NOVELS: DemoNovelInfo[] = [
  // ── 中国古典四大名著 ──
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
  // ── 神话/修仙 ──
  {
    slug: "fengshen",
    title: "封神演义",
    dataPath: "/demo-data/fengshen",
    totalChapters: 90,
    stats: { characters: 735, relations: 1148, locations: 469, events: 3148 },
  },
  {
    slug: "fanren",
    title: "凡人修仙传",
    dataPath: "/demo-data/fanren",
    totalChapters: 2452,
    stats: { characters: 3756, relations: 4608, locations: 4697, events: 23492 },
  },
  // ── 武侠 ──
  {
    slug: "tianlong",
    title: "天龙八部",
    dataPath: "/demo-data/tianlong",
    totalChapters: 53,
    stats: { characters: 516, relations: 690, locations: 808, events: 2389 },
  },
  {
    slug: "shediao",
    title: "射雕英雄传",
    dataPath: "/demo-data/shediao",
    totalChapters: 48,
    stats: { characters: 426, relations: 603, locations: 764, events: 2413 },
  },
  // ── 现代文学/科幻/西方 ──
  {
    slug: "santi",
    title: "三体",
    dataPath: "/demo-data/santi",
    totalChapters: 38,
    stats: { characters: 145, relations: 149, locations: 210, events: 575 },
  },
  {
    slug: "mojie",
    title: "魔戒",
    dataPath: "/demo-data/mojie",
    totalChapters: 82,
    stats: { characters: 425, relations: 509, locations: 886, events: 2186 },
  },
  {
    slug: "pingfan",
    title: "平凡的世界",
    dataPath: "/demo-data/pingfan",
    totalChapters: 171,
    stats: { characters: 355, relations: 563, locations: 843, events: 2255 },
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
