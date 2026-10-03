import { describe, it, expect } from "vitest"
import { estimateParagraphHeight, findParagraphIndex } from "./paragraphWindow"

const OPTS = { fontSizePx: 16, lineHeightFactor: 2.0, charsPerLine: 40, gapPx: 8 }
const LINE = 16 * 2.0 // 32px per line

describe("estimateParagraphHeight", () => {
  it("empty paragraph still occupies one line plus gap", () => {
    expect(estimateParagraphHeight(0, OPTS)).toBe(LINE + 8)
  })

  it("paragraph fitting in one line is one line plus gap", () => {
    expect(estimateParagraphHeight(1, OPTS)).toBe(LINE + 8)
    expect(estimateParagraphHeight(40, OPTS)).toBe(LINE + 8)
  })

  it("longer paragraphs scale by line count", () => {
    expect(estimateParagraphHeight(41, OPTS)).toBe(2 * LINE + 8)
    expect(estimateParagraphHeight(80, OPTS)).toBe(2 * LINE + 8)
    expect(estimateParagraphHeight(81, OPTS)).toBe(3 * LINE + 8)
  })

  it("larger font size increases the estimate", () => {
    const bigger = estimateParagraphHeight(100, { ...OPTS, fontSizePx: 20 })
    expect(bigger).toBeGreaterThan(estimateParagraphHeight(100, OPTS))
  })

  it("degenerate charsPerLine never divides by zero", () => {
    expect(estimateParagraphHeight(10, { ...OPTS, charsPerLine: 0 })).toBeGreaterThan(0)
    expect(estimateParagraphHeight(10, { ...OPTS, charsPerLine: -5 })).toBeGreaterThan(0)
  })
})

describe("findParagraphIndex", () => {
  const texts = ["第一段文字。", "第二段文字很长，包含目标内容。", "第三段。"]

  it("finds the paragraph containing the needle", () => {
    expect(findParagraphIndex(texts, "目标内容")).toBe(1)
    expect(findParagraphIndex(texts, "第三段")).toBe(2)
  })

  it("returns -1 when nothing matches", () => {
    expect(findParagraphIndex(texts, "不存在的内容")).toBe(-1)
  })

  it("returns -1 for an empty needle", () => {
    expect(findParagraphIndex(texts, "")).toBe(-1)
  })

  it("matches on only the first 20 characters of the needle", () => {
    const para = "长".repeat(30)
    const needle = "长".repeat(20) + "短".repeat(10) // 前 20 字符与段落一致，之后不同
    expect(findParagraphIndex(["x", para], needle)).toBe(1)
  })
})
