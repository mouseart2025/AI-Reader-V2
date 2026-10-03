// 场景模式段落虚拟化的纯逻辑：段落高度预估 + 按文本定位段落索引。
// 供 ReadingPage 的 useVirtualizer（estimateSize / 跳转定位回退）使用，
// 与 React/DOM 解耦以便单测。

import type { FontSize, LineHeight } from "@/stores/readingSettingsStore"

export interface ParagraphEstimateOptions {
  /** 字号（px），对应 FONT_SIZE_MAP 的 tailwind 档位 */
  fontSizePx: number
  /** 行高倍数，对应 LINE_HEIGHT_MAP 的 leading 档位 */
  lineHeightFactor: number
  /** 每行约可容纳的全角字符数（按内容区宽度 / 字号估算） */
  charsPerLine: number
  /** 段落下方的固定间距（px），对应渲染包裹层的 pb-2 */
  gapPx: number
}

export const FONT_SIZE_PX: Record<FontSize, number> = {
  small: 14,
  medium: 16,
  large: 18,
  xlarge: 20,
}

export const LINE_HEIGHT_FACTOR: Record<LineHeight, number> = {
  compact: 1.6,
  normal: 2.0,
  loose: 2.6,
}

/** 预估段落渲染高度：行数 × 行高 + 段间距，空段也占一行 */
export function estimateParagraphHeight(
  textLength: number,
  opts: ParagraphEstimateOptions,
): number {
  const perLine = Math.max(1, Math.floor(opts.charsPerLine))
  const lines = Math.max(1, Math.ceil(textLength / perLine))
  return lines * opts.fontSizePx * opts.lineHeightFactor + opts.gapPx
}

/**
 * 在段落文本数组中找到包含 needle 的首个段落索引，找不到返回 -1。
 * 匹配只看 needle 的前 20 个字符（与 scrollToText 的既有截断行为一致）。
 */
export function findParagraphIndex(
  texts: readonly string[],
  needle: string,
): number {
  const head = needle.slice(0, 20)
  if (!head) return -1
  return texts.findIndex((t) => t.includes(head))
}
