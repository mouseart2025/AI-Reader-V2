/**
 * 架空特殊空间（realm）前端共享工具。
 *
 * 与后端 SSOT `src/utils/location_names.is_special_space` 对齐：凡是被归入
 * `realm` 层级的地点（仙界/魔域/秘境/洞天…）在前端以「独立图标 + 弱提示样式」
 * 呈现，区别于常规地理层级（continent/kingdom/region/city/site/building）。
 *
 * 弱提示样式约定（对齐 PRD-A Story 4.2 subtype 弱提示）：
 * - 降饱和色（非权威地理本体）
 * - 独立标记图标 ✦
 * - 标签为「界域」而非「大陆」等地理类目
 */

export const SPECIAL_SPACE_TIER = "realm"
export const SPECIAL_SPACE_LABEL = "界域"
export const SPECIAL_SPACE_ICON = "✦"

/** 判断某 tier 是否为架空特殊空间层级。 */
export function isSpecialSpaceTier(tier?: string | null): boolean {
  return tier === SPECIAL_SPACE_TIER
}
