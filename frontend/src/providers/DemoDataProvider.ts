/**
 * DemoDataProvider — 封装 demoDataAdapter.ts 的 CDN JSON.gz 加载
 * 用于 ai-reader.cc 在线 Demo 模式
 */

import { loadDemoData, loadDemoChapterContent } from "@/api/demoDataAdapter"
import { getAllDemoNovels } from "@/api/demoNovelMap"
import type { MapData, WorldStructureData } from "@/api/types"
import type { NovelDataProvider, NovelListItem, ChapterContentResult, ChapterRange } from "./types"
import { extractEntitiesFromStats, findEntityInEncyclopedia } from "./dataUtils"

export class DemoDataProvider implements NovelDataProvider {
  async getNovelList(): Promise<NovelListItem[]> {
    return getAllDemoNovels().map((n) => ({
      slug: n.slug,
      title: n.title,
      totalChapters: n.totalChapters,
      stats: n.stats,
    }))
  }

  async getChapterList(slug: string): Promise<Record<string, unknown>[]> {
    return loadDemoData<Record<string, unknown>[]>(slug, "chapters")
  }

  async getChapterContent(slug: string, chapterNum: number): Promise<ChapterContentResult> {
    const ch = await loadDemoChapterContent(slug, chapterNum)
    return {
      chapter_num: ch.chapter_num,
      title: ch.title,
      content: ch.content,
      word_count: ch.word_count,
      entities: ch.entities,
      scenes: ch.scenes,
    }
  }

  async getGraphData(slug: string, _range?: ChapterRange): Promise<Record<string, unknown>> {
    return loadDemoData<Record<string, unknown>>(slug, "graph")
  }

  async getMapData(slug: string, _range?: ChapterRange): Promise<MapData> {
    return loadDemoData<MapData>(slug, "map")
  }

  async getTimelineData(slug: string, _range?: ChapterRange): Promise<Record<string, unknown>> {
    return loadDemoData<Record<string, unknown>>(slug, "timeline")
  }

  async getEncyclopediaData(slug: string): Promise<Record<string, unknown>> {
    return loadDemoData<Record<string, unknown>>(slug, "encyclopedia")
  }

  async getEncyclopediaStats(slug: string): Promise<Record<string, unknown>> {
    return loadDemoData<Record<string, unknown>>(slug, "encyclopedia-stats")
  }

  async getFactionsData(slug: string, _range?: ChapterRange): Promise<Record<string, unknown>> {
    return loadDemoData<Record<string, unknown>>(slug, "factions")
  }

  async getWorldStructure(slug: string): Promise<WorldStructureData> {
    return loadDemoData<WorldStructureData>(slug, "world-structure")
  }

  async getEntityProfile(slug: string, name: string, _type?: string): Promise<Record<string, unknown>> {
    const encyclopedia = await loadDemoData<Record<string, unknown>>(slug, "encyclopedia")
    return findEntityInEncyclopedia(encyclopedia, name)
  }

  async getEntities(slug: string, type?: string): Promise<{ entities: import("@/api/types").EntitySummary[]; alias_map: Record<string, string> }> {
    const stats = await loadDemoData<Record<string, unknown>>(slug, "encyclopedia-stats")
    return extractEntitiesFromStats(stats, type)
  }
}
