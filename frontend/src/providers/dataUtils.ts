/**
 * 共享数据提取工具 — 供 DemoDataProvider 和 DesktopDataProvider 复用
 * 从百科数据中提取实体列表和实体详情
 */

import type { EntitySummary } from "@/api/types"

/**
 * 从 encyclopedia-stats 的 by_type 结构中提取实体列表
 */
export function extractEntitiesFromStats(
  stats: Record<string, unknown>,
  type?: string,
): { entities: EntitySummary[]; alias_map: Record<string, string> } {
  const byType = stats.by_type as
    | Record<string, { name: string; type: string; chapter_count: number; first_chapter: number }[]>
    | undefined
  if (!byType) return { entities: [], alias_map: {} }

  let entities: EntitySummary[] = []
  for (const [t, items] of Object.entries(byType)) {
    if (type && t !== type) continue
    entities = entities.concat(
      items.map((e) => ({
        name: e.name,
        type: e.type ?? t,
        chapter_count: e.chapter_count,
        first_chapter: e.first_chapter,
      })),
    )
  }
  return { entities, alias_map: (stats.alias_map as Record<string, string>) ?? {} }
}

/**
 * 从 encyclopedia entries 中查找单个实体详情
 */
export function findEntityInEncyclopedia(
  encyclopedia: Record<string, unknown>,
  name: string,
): Record<string, unknown> {
  const entries = (encyclopedia as { entries?: Record<string, unknown>[] }).entries
  return entries?.find((e) => (e as { name?: string }).name === name) ?? {}
}
