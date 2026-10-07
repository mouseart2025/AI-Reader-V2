import { render, screen, fireEvent } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import type { Novel } from "@/api/types"
import { DesktopNovelCard } from "./NovelCard"

function makeNovel(overrides: Partial<Novel> = {}): Novel {
  return {
    id: "n1",
    title: "诡秘之主",
    author: "爱潜水的乌贼",
    total_chapters: 1395,
    total_words: 4_500_000,
    is_sample: false,
    analysis_progress: 0,
    failed_count: 0,
    reading_progress: 0,
    ...overrides,
  } as Novel
}

function renderCard(
  novel: Novel,
  analysisStatus: "running" | "paused" | null = null,
) {
  const onClick = vi.fn()
  const onDelete = vi.fn()
  render(
    <DesktopNovelCard
      novel={novel}
      analysisStatus={analysisStatus}
      onClick={onClick}
      onDelete={onDelete}
    />,
  )
  return { onClick, onDelete }
}

describe("DesktopNovelCard", () => {
  it("未分析：muted 状态文案，进度条 0 宽", () => {
    renderCard(makeNovel())
    expect(screen.getByText("未分析")).toBeInTheDocument()
    expect(screen.getByText("1395 章 · 450 万字")).toBeInTheDocument()
  })

  it("分析完成：emerald ✓ 状态", () => {
    renderCard(makeNovel({ analysis_progress: 1 }))
    expect(screen.getByText("分析完成")).toBeInTheDocument()
  })

  it("分析完成但有失败章：amber 提示", () => {
    renderCard(makeNovel({ analysis_progress: 1, failed_count: 3 }))
    expect(screen.getByText("分析完成（3 章失败）")).toBeInTheDocument()
  })

  it("分析中：显示百分比与脉动 badge", () => {
    renderCard(makeNovel({ analysis_progress: 0.42 }), "running")
    expect(screen.getByText("分析中")).toBeInTheDocument()
    expect(screen.getByText("分析 42%")).toBeInTheDocument()
  })

  it("已暂停：amber badge", () => {
    renderCard(makeNovel({ analysis_progress: 0.5 }), "paused")
    expect(screen.getByText("已暂停")).toBeInTheDocument()
    expect(screen.getByText("分析 50%")).toBeInTheDocument()
  })

  it("已开始阅读：渲染阅读进度轨", () => {
    const { container } = render(
      <DesktopNovelCard
        novel={makeNovel({ analysis_progress: 1, reading_progress: 0.3 })}
        analysisStatus={null}
        onClick={() => {}}
        onDelete={() => {}}
      />,
    )
    const bars = container.querySelectorAll(".bg-indigo-400\\/70")
    expect(bars.length).toBe(1)
  })

  it("点击卡片触发 onClick，删除按钮触发 onDelete 且不冒泡", () => {
    const { onClick, onDelete } = renderCard(makeNovel())
    fireEvent.click(screen.getByText("诡秘之主"))
    expect(onClick).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTitle("删除"))
    expect(onDelete).toHaveBeenCalledTimes(1)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it("同一书名色相稳定，不同书名色相不同（确定性书脊色）", () => {
    const { container: c1 } = render(
      <DesktopNovelCard novel={makeNovel()} analysisStatus={null} onClick={() => {}} onDelete={() => {}} />,
    )
    const { container: c2 } = render(
      <DesktopNovelCard novel={makeNovel({ id: "n2", title: "红楼梦" })} analysisStatus={null} onClick={() => {}} onDelete={() => {}} />,
    )
    const spine1 = c1.querySelector(".absolute.inset-y-0.left-0")!.getAttribute("style")
    const spine2 = c2.querySelector(".absolute.inset-y-0.left-0")!.getAttribute("style")
    expect(spine1).toBeTruthy()
    expect(spine1).not.toBe(spine2)
  })
})
