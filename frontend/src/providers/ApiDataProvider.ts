/**
 * ApiDataProvider — 封装 api/client.ts 的 REST API 调用
 * 用于本地开发 Web 模式（后端 FastAPI 运行时）
 *
 * 注意：API 模式使用 novelId（数字 ID），消费者传入的 slug 即 novelId
 */

import * as api from "@/api/client"
import type { MapData, WorldStructureData, EntitySummary } from "@/api/types"
import type { NovelDataProvider, NovelListItem, ChapterContentResult, ChapterRange } from "./types"

export class ApiDataProvider implements NovelDataProvider {
  async getNovelList(): Promise<NovelListItem[]> {
    const { novels } = await api.fetchNovels()
    return novels.map((n) => ({
      slug: n.id,
      title: n.title,
      author: n.author,
      totalChapters: n.total_chapters,
    }))
  }

  async getChapterList(novelId: string): Promise<Record<string, unknown>[]> {
    const { chapters } = await api.fetchChapters(novelId)
    return chapters as unknown as Record<string, unknown>[]
  }

  async getChapterContent(novelId: string, chapterNum: number): Promise<ChapterContentResult> {
    const [chapter, { entities }] = await Promise.all([
      api.fetchChapterContent(novelId, chapterNum),
      api.fetchChapterEntities(novelId, chapterNum),
    ])
    return {
      chapter_num: chapter.chapter_num,
      title: chapter.title,
      content: chapter.content,
      word_count: chapter.word_count,
      entities,
    }
  }

  async getGraphData(novelId: string, range?: ChapterRange): Promise<Record<string, unknown>> {
    return api.fetchGraphData(novelId, range?.chapterStart, range?.chapterEnd)
  }

  async getMapData(novelId: string, range?: ChapterRange): Promise<MapData> {
    return api.fetchMapData(novelId, range?.chapterStart, range?.chapterEnd)
  }

  async getTimelineData(novelId: string, range?: ChapterRange): Promise<Record<string, unknown>> {
    return api.fetchTimelineData(novelId, range?.chapterStart, range?.chapterEnd)
  }

  async getEncyclopediaData(novelId: string): Promise<Record<string, unknown>> {
    return api.fetchEncyclopediaEntries(novelId) as unknown as Record<string, unknown>
  }

  async getEncyclopediaStats(novelId: string): Promise<Record<string, unknown>> {
    return api.fetchEncyclopediaStats(novelId)
  }

  async getFactionsData(novelId: string, range?: ChapterRange): Promise<Record<string, unknown>> {
    return api.fetchFactionsData(novelId, range?.chapterStart, range?.chapterEnd)
  }

  async getWorldStructure(novelId: string): Promise<WorldStructureData> {
    return api.fetchWorldStructure(novelId)
  }

  async getEntityProfile(novelId: string, name: string, type?: string): Promise<Record<string, unknown>> {
    return api.fetchEntityProfile(novelId, name, type)
  }

  async getEntities(novelId: string, type?: string): Promise<{ entities: EntitySummary[]; alias_map: Record<string, string> }> {
    return api.fetchEntities(novelId, type)
  }
}
