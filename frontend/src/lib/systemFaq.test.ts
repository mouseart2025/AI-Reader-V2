import { describe, it, expect } from "vitest"
import { matchSystemFaq, QUICK_QUESTIONS } from "./systemFaq"

describe("matchSystemFaq", () => {
  // issue #67 回归：五个预设问题必须全部以 ≥0.8 置信命中 FAQ，
  // 否则会漏进小说 RAG 得到"暂未找到相关信息"
  it("all quick questions hit FAQ with confidence >= 0.8", () => {
    for (const q of QUICK_QUESTIONS) {
      const result = matchSystemFaq(q)
      expect(result, `预设问题未命中 FAQ: ${q}`).not.toBeNull()
      expect(result!.confidence, `预设问题置信不足: ${q}`).toBeGreaterThanOrEqual(0.8)
    }
  })

  it("分析耗时问题命中「多长时间」词条而非泛泛的「分析」词条", () => {
    const result = matchSystemFaq("分析需要多长时间？")
    expect(result).not.toBeNull()
    expect(result!.answer).toContain("章节")
  })

  it("小说内容问题（含实体名对）降置信，让位 RAG", () => {
    const result = matchSystemFaq("分析贾宝玉和林黛玉的关系")
    // 命中「分析」关键词但被实体对模式降权，应低于 FAQ 阈值
    expect(result === null || result.confidence < 0.8).toBe(true)
  })

  it("无关键词命中返回 null", () => {
    expect(matchSystemFaq("今天天气怎么样")).toBeNull()
  })

  it("空输入返回 null", () => {
    expect(matchSystemFaq("")).toBeNull()
    expect(matchSystemFaq("   ")).toBeNull()
  })
})
