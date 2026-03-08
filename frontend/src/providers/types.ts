/**
 * DataProvider 抽象层类型定义
 * 统一 Web API / Demo CDN / Desktop (Tauri) 三种数据加载模式
 */

import type { MapData, WorldStructureData, EntitySummary, Scene } from "@/api/types"

// ── 平台检测 ──────────────────────────────────

export interface PlatformInfo {
  /** Tauri WebView 桌面版 */
  isTauri: boolean
  /** Demo 网站 (ai-reader.cc) */
  isDemo: boolean
  /** 本地开发 Web 版 (REST API) */
  isWeb: boolean
}

// ── Provider 数据类型 ──────────────────────────

export interface ChapterRange {
  chapterStart?: number
  chapterEnd?: number
}

export interface NovelListItem {
  slug: string
  title: string
  author?: string | null
  totalChapters: number
  stats?: { characters: number; relations: number; locations: number; events: number }
}

export interface ChapterContentResult {
  chapter_num: number
  title: string
  content: string
  word_count: number
  entities?: { name: string; type: string }[]
  scenes?: Scene[]
}

// ── Provider 接口 ──────────────────────────────

export interface NovelDataProvider {
  /** 获取所有小说列表 */
  getNovelList(): Promise<NovelListItem[]>

  /** 获取章节列表（元数据，不含正文） */
  getChapterList(slug: string): Promise<Record<string, unknown>[]>

  /** 获取单章内容 + 实体 */
  getChapterContent(slug: string, chapterNum: number): Promise<ChapterContentResult>

  /** 图谱数据 */
  getGraphData(slug: string, range?: ChapterRange): Promise<Record<string, unknown>>

  /** 地图数据 */
  getMapData(slug: string, range?: ChapterRange): Promise<MapData>

  /** 时间线数据 */
  getTimelineData(slug: string, range?: ChapterRange): Promise<Record<string, unknown>>

  /** 百科词条 */
  getEncyclopediaData(slug: string): Promise<Record<string, unknown>>

  /** 百科统计 */
  getEncyclopediaStats(slug: string): Promise<Record<string, unknown>>

  /** 势力数据 */
  getFactionsData(slug: string, range?: ChapterRange): Promise<Record<string, unknown>>

  /** 世界观结构 */
  getWorldStructure(slug: string): Promise<WorldStructureData>

  /** 实体详情 */
  getEntityProfile(slug: string, name: string, type?: string): Promise<Record<string, unknown>>

  /** 实体列表 */
  getEntities(slug: string, type?: string): Promise<{ entities: EntitySummary[]; alias_map: Record<string, string> }>

  /** 清除缓存 — 导入新小说后调用 (仅 DesktopDataProvider 实现) */
  clearCache?(): void
}
