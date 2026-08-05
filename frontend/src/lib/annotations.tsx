import type { ReactNode } from "react"
import type { Annotation, ChapterEntity } from "@/api/types"
import { highlightText } from "@/lib/entityHighlight"

// ── Annotation colors ──────────────────────────

export const ANNOTATION_COLORS: Record<string, string> = {
  yellow: "#eab308",
  green: "#22c55e",
  blue: "#3b82f6",
  pink: "#ec4899",
}

export const ANNOTATION_COLOR_KEYS = ["yellow", "green", "blue", "pink"]

// ── Offset math (code-point based) ─────────────
//
// 后端用 Python len()/切片计算偏移（按 Unicode 码点），而 JS 的
// String.length 按 UTF-16 码元计数，遇到代理对（生僻字如 𠮷）会差一。
// 因此前端所有偏移计算与切片都统一走码点口径，避免锚点漂移。

/** 码点长度（与 Python len(str) 一致） */
export function cpLen(s: string): number {
  return [...s].length // 展开运算符按码点迭代
}

/** 按码点切片（与 Python s[start:end] 一致） */
function cpSlice(s: string, start: number, end: number): string {
  return [...s].slice(start, end).join("")
}

// ── Selection capture ──────────────────────────

export interface SelectionOffsets {
  start: number
  end: number
  text: string
}

/**
 * 计算当前选区相对章节正文的码点偏移。
 * 依赖渲染时每个文本分段上的 data-off 属性（该分段在正文中的起始偏移）。
 * 选区为空、不在容器内、或端点不在任何分段内时返回 null。
 */
export function getSelectionOffsets(container: HTMLElement): SelectionOffsets | null {
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null
  const range = sel.getRangeAt(0)
  if (!container.contains(range.commonAncestorContainer)) return null

  const start = offsetOfPoint(container, range.startContainer, range.startOffset)
  const end = offsetOfPoint(container, range.endContainer, range.endOffset)
  if (start == null || end == null || end <= start) return null

  const text = sel.toString()
  if (!text.trim()) return null
  return { start, end, text }
}

function offsetOfPoint(
  container: HTMLElement,
  node: Node,
  offsetInNode: number,
): number | null {
  // 向上找最近的带 data-off 的祖先（分段 span / mark）
  let el: HTMLElement | null =
    node.nodeType === Node.TEXT_NODE
      ? node.parentElement
      : (node as HTMLElement)
  while (el && el !== container && !el.dataset?.off) {
    el = el.parentElement
  }
  if (!el || el === container || !el.dataset.off) return null

  // 累加分段内、该点之前的文本长度
  const r = document.createRange()
  r.selectNodeContents(el)
  try {
    r.setEnd(node, offsetInNode)
  } catch {
    return null
  }
  return parseInt(el.dataset.off, 10) + cpLen(r.toString())
}

// ── Rendering ──────────────────────────────────
//
// 图层共存取舍：实体高亮（entityHighlight.tsx）用「背景色 + 文字色」，
// 批注若也用背景色会互相遮盖、难以区分。因此批注采用「3px 下划线 +
// 同色系 12% 透明度底色」——下划线是正交于背景色的视觉通道，实体高亮
// 开启时两套样式可叠加辨认，关闭时批注依然清晰可见。

export interface RenderWithAnnotationsOptions {
  text: string
  baseOffset: number // text 在章节正文中的起始码点偏移
  annotations: Annotation[]
  highlightEnabled: boolean
  entities: ChapterEntity[]
  onEntityClick: (name: string, type: string) => void
  onAnnotationClick: (annotation: Annotation, el: HTMLElement) => void
}

/**
 * 渲染一段正文，按批注 offset 切分为若干 <mark>/<span> 分段，
 * 分段内部再叠加实体高亮。每个分段带 data-off 供选区偏移计算。
 * 重叠的批注按 start_offset 排序后先先生效，后者裁掉重叠部分。
 */
export function renderWithAnnotations({
  text,
  baseOffset,
  annotations,
  highlightEnabled,
  entities,
  onEntityClick,
  onAnnotationClick,
}: RenderWithAnnotationsOptions): ReactNode {
  const textLen = cpLen(text)
  const segEnd = baseOffset + textLen

  // 裁剪到本段范围并过滤无效区间
  const ranges = annotations
    .map((a) => ({
      ann: a,
      start: Math.max(a.start_offset, baseOffset),
      end: Math.min(a.end_offset, segEnd),
    }))
    .filter((r) => r.end > r.start)
    .sort((a, b) => a.start - b.start)

  // 去掉重叠（先开始者优先）
  const clipped: typeof ranges = []
  let cursor = baseOffset
  for (const r of ranges) {
    const start = Math.max(r.start, cursor)
    if (r.end > start) {
      clipped.push({ ann: r.ann, start, end: r.end })
      cursor = r.end
    }
  }

  const renderInner = (segText: string): ReactNode =>
    highlightEnabled ? highlightText(segText, entities, onEntityClick) : segText

  const parts: ReactNode[] = []
  let pos = baseOffset
  let key = 0

  for (const r of clipped) {
    if (r.start > pos) {
      const segText = cpSlice(text, pos - baseOffset, r.start - baseOffset)
      parts.push(
        <span key={key++} data-off={pos}>
          {renderInner(segText)}
        </span>,
      )
    }
    const color = ANNOTATION_COLORS[r.ann.color] ?? ANNOTATION_COLORS.yellow
    const segText = cpSlice(text, r.start - baseOffset, r.end - baseOffset)
    parts.push(
      <mark
        key={key++}
        data-off={r.start}
        className="cursor-pointer rounded-sm"
        style={{
          backgroundColor: `${color}1f`, // 12% 透明度底色
          borderBottom: `3px solid ${color}`,
        }}
        title={r.ann.note || undefined}
        onClick={(e) => {
          // 点在实体高亮 span 上时让实体卡片优先；
          // 只有点在批注自身的普通文本上才打开批注浮层
          if (e.target === e.currentTarget) {
            onAnnotationClick(r.ann, e.currentTarget)
          }
        }}
      >
        {renderInner(segText)}
      </mark>,
    )
    pos = r.end
  }

  if (pos < segEnd) {
    const segText = cpSlice(text, pos - baseOffset, textLen)
    parts.push(
      <span key={key++} data-off={pos}>
        {renderInner(segText)}
      </span>,
    )
  }

  // 无批注时也要包一层 data-off，否则选区无法定位
  if (parts.length === 0) {
    return <span data-off={baseOffset}>{renderInner(text)}</span>
  }
  return parts
}
