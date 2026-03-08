/**
 * DesktopDataProvider — Tauri 桌面版本地资源加载
 *
 * 使用 Rust invoke 命令 `load_resource` 直接读取并解压 JSON/JSON.gz 文件
 * 绕过 asset protocol, 通过 IPC 直接返回 JSON 字符串
 * 所有 @tauri-apps/api 导入使用动态 import() — 禁止顶层 import (ADR-06)
 *
 * 支持两种数据来源:
 * - 预装小说: resources/novels/{slug}/ (bundle 内, 只读)
 * - 导入小说: app_data_dir/novels/{slug}/ (用户数据, 可写)
 */

import type { MapData, WorldStructureData } from "@/api/types"
import type { NovelDataProvider, NovelListItem, ChapterContentResult, ChapterRange } from "./types"
import { extractEntitiesFromStats, findEntityInEncyclopedia } from "./dataUtils"

interface NovelManifest {
  version: number
  generated?: string
  novels: NovelListItem[]
}

/** Extended novel info with source tracking */
interface NovelWithSource extends NovelListItem {
  /** "preinstalled" = bundled, "imported" = user data dir */
  source: "preinstalled" | "imported"
  /** For imported novels: absolute path to novel data dir */
  dataDir?: string
}

export class DesktopDataProvider implements NovelDataProvider {
  private cache = new Map<string, unknown>()
  private invoke: (<T>(cmd: string, args?: Record<string, unknown>) => Promise<T>) | null = null
  /** Tracks which slugs are imported (with absolute data dir path) */
  private importedNovels = new Map<string, string>()
  /** Cached user data dir path */
  private userNovelsDir: string | null = null

  /** 首次调用时初始化 Tauri invoke 引用 */
  private async ensureInvoke(): Promise<void> {
    if (this.invoke) return
    const { invoke } = await import("@tauri-apps/api/core")
    this.invoke = invoke
  }

  /**
   * 加载 Tauri 资源文件 (JSON 或 JSON.gz)
   * 通过 Rust IPC 命令读取 + 解压, 返回解析后的对象
   */
  private async loadResource<T>(relativePath: string): Promise<T> {
    if (this.cache.has(relativePath)) return this.cache.get(relativePath) as T

    await this.ensureInvoke()

    const jsonString = await this.invoke!<string>("load_resource", { path: relativePath })
    const data = JSON.parse(jsonString) as T

    this.cache.set(relativePath, data)
    return data
  }

  /**
   * 加载用户数据目录中的文件 (绝对路径)
   */
  private async loadAbsoluteFile<T>(absolutePath: string): Promise<T> {
    if (this.cache.has(absolutePath)) return this.cache.get(absolutePath) as T

    await this.ensureInvoke()

    const jsonString = await this.invoke!<string>("load_file_absolute", { path: absolutePath })
    const data = JSON.parse(jsonString) as T

    this.cache.set(absolutePath, data)
    return data
  }

  /**
   * 根据 slug 智能加载数据 — 自动判断是预装还是导入
   */
  private async loadNovelData<T>(slug: string, filename: string): Promise<T> {
    const importedDir = this.importedNovels.get(slug)
    if (importedDir) {
      return this.loadAbsoluteFile<T>(`${importedDir}/${filename}`)
    }
    return this.loadResource<T>(`resources/novels/${slug}/${filename}`)
  }

  /** Tauri bundle preserves resources/ prefix from tauri.conf.json glob */
  private res(path: string): string {
    return `resources/novels/${path}`
  }

  /** 获取用户数据目录路径 */
  private async getUserNovelsDir(): Promise<string> {
    if (this.userNovelsDir) return this.userNovelsDir
    await this.ensureInvoke()
    this.userNovelsDir = await this.invoke!<string>("get_user_novels_dir")
    return this.userNovelsDir
  }

  async getNovelList(): Promise<NovelListItem[]> {
    // 1. Load bundled novels
    const bundledManifest = await this.loadResource<NovelManifest>(this.res("manifest.json"))
    const bundled: NovelWithSource[] = bundledManifest.novels.map(n => ({
      ...n,
      source: "preinstalled" as const,
    }))

    // 2. Load user-imported novels (may not exist)
    let imported: NovelWithSource[] = []
    try {
      const userDir = await this.getUserNovelsDir()
      const manifestPath = `${userDir}/manifest.json`
      const jsonString = await this.invoke!<string>("load_file_absolute", { path: manifestPath })
      const userManifest = JSON.parse(jsonString) as NovelManifest

      imported = userManifest.novels.map(n => ({
        ...n,
        source: "imported" as const,
        dataDir: `${userDir}/${n.slug}`,
      }))

      // Register imported novels for data loading
      for (const novel of imported) {
        this.importedNovels.set(novel.slug, novel.dataDir!)
      }
    } catch {
      // No user manifest yet — normal for fresh install
    }

    return [...bundled, ...imported]
  }

  async getChapterList(slug: string): Promise<Record<string, unknown>[]> {
    return this.loadNovelData<Record<string, unknown>[]>(slug, "chapters.json.gz")
  }

  async getChapterContent(slug: string, chapterNum: number): Promise<ChapterContentResult> {
    const paddedNum = String(chapterNum).padStart(3, "0")
    return this.loadNovelData<ChapterContentResult>(slug, `chapters/ch-${paddedNum}.json.gz`)
  }

  async getGraphData(slug: string, _range?: ChapterRange): Promise<Record<string, unknown>> {
    return this.loadNovelData<Record<string, unknown>>(slug, "graph.json.gz")
  }

  async getMapData(slug: string, _range?: ChapterRange): Promise<MapData> {
    return this.loadNovelData<MapData>(slug, "map.json.gz")
  }

  async getTimelineData(slug: string, _range?: ChapterRange): Promise<Record<string, unknown>> {
    return this.loadNovelData<Record<string, unknown>>(slug, "timeline.json.gz")
  }

  async getEncyclopediaData(slug: string): Promise<Record<string, unknown>> {
    return this.loadNovelData<Record<string, unknown>>(slug, "encyclopedia.json.gz")
  }

  async getEncyclopediaStats(slug: string): Promise<Record<string, unknown>> {
    return this.loadNovelData<Record<string, unknown>>(slug, "encyclopedia-stats.json.gz")
  }

  async getFactionsData(slug: string, _range?: ChapterRange): Promise<Record<string, unknown>> {
    return this.loadNovelData<Record<string, unknown>>(slug, "factions.json.gz")
  }

  async getWorldStructure(slug: string): Promise<WorldStructureData> {
    return this.loadNovelData<WorldStructureData>(slug, "world-structure.json.gz")
  }

  async getEntityProfile(slug: string, name: string, _type?: string): Promise<Record<string, unknown>> {
    const encyclopedia = await this.loadNovelData<Record<string, unknown>>(slug, "encyclopedia.json.gz")
    return findEntityInEncyclopedia(encyclopedia, name)
  }

  async getEntities(slug: string, type?: string): Promise<{ entities: import("@/api/types").EntitySummary[]; alias_map: Record<string, string> }> {
    const stats = await this.loadNovelData<Record<string, unknown>>(slug, "encyclopedia-stats.json.gz")
    return extractEntitiesFromStats(stats, type)
  }

  /** 清除所有缓存 — 导入新小说后调用 */
  clearCache(): void {
    this.cache.clear()
    this.importedNovels.clear()
    this.userNovelsDir = null
  }
}
