import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { AppendChaptersResponse, Novel } from "@/api/types"

vi.mock("@/api/client", () => ({
  appendChapters: vi.fn(),
}))

import { appendChapters } from "@/api/client"
import { AppendChaptersDialog } from "./AppendChaptersDialog"

const mockedAppend = vi.mocked(appendChapters)

const NOVEL: Novel = {
  id: "n1",
  title: "测试小说",
  author: null,
  total_chapters: 10,
  total_words: 50000,
  created_at: "2026-01-01",
  updated_at: "2026-01-01",
  is_sample: false,
  analysis_progress: 1,
  failed_count: 0,
  reading_progress: 0,
  last_opened: null,
}

const RESULT: AppendChaptersResponse = {
  added: 2,
  skipped: 1,
  total_chapters: 12,
  chapters: [
    { chapter_num: 11, title: "打虎", word_count: 2500 },
    { chapter_num: 12, title: "下山", word_count: 2600 },
  ],
  message: "新增 2 章，跳过 1 章重复内容",
}

function renderDialog(onAppended = vi.fn(), onOpenChange = vi.fn()) {
  render(
    <AppendChaptersDialog
      open
      onOpenChange={onOpenChange}
      novel={NOVEL}
      onAppended={onAppended}
    />,
  )
  return { onAppended, onOpenChange }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("AppendChaptersDialog", () => {
  it("shows current chapter count and next chapter number", () => {
    renderDialog()

    expect(screen.getByText("追加章节 - 测试小说")).toBeInTheDocument()
    expect(screen.getByText(/当前共 10 章/)).toBeInTheDocument()
    expect(screen.getByText(/从第\s*11\s*章开始编号/)).toBeInTheDocument()
  })

  it("submits pasted text and shows the append result", async () => {
    mockedAppend.mockResolvedValue(RESULT)
    const { onAppended } = renderDialog()

    fireEvent.click(screen.getByRole("button", { name: /粘贴文本/ }))
    fireEvent.change(screen.getByLabelText(/追加文本/), {
      target: { value: "第十一章 打虎\n正文……" },
    })
    fireEvent.click(screen.getByRole("button", { name: "确认追加" }))

    await waitFor(() => {
      expect(mockedAppend).toHaveBeenCalledWith("n1", {
        file: undefined,
        text: "第十一章 打虎\n正文……",
      })
    })
    // 成功提示：新增 N 章（跳过 M 章重复）+ 新章列表
    expect(await screen.findByText(RESULT.message)).toBeInTheDocument()
    expect(screen.getByText("打虎")).toBeInTheDocument()
    expect(screen.getByText(/已完成章节会自动跳过/)).toBeInTheDocument()
    expect(onAppended).toHaveBeenCalled()
  })

  it("does not notify parent when nothing was added (all duplicates)", async () => {
    mockedAppend.mockResolvedValue({
      added: 0,
      skipped: 2,
      total_chapters: 10,
      chapters: [],
      message: "未新增章节：2 章与已有内容重复",
    })
    const { onAppended } = renderDialog()

    fireEvent.click(screen.getByRole("button", { name: /粘贴文本/ }))
    fireEvent.change(screen.getByLabelText(/追加文本/), {
      target: { value: "第一章 已有\n正文……" },
    })
    fireEvent.click(screen.getByRole("button", { name: "确认追加" }))

    expect(
      await screen.findByText("未新增章节：2 章与已有内容重复"),
    ).toBeInTheDocument()
    expect(onAppended).not.toHaveBeenCalled()
  })

  it("shows the server error message on failure", async () => {
    mockedAppend.mockRejectedValue(new Error("分析进行中，请先暂停"))
    renderDialog()

    fireEvent.click(screen.getByRole("button", { name: /粘贴文本/ }))
    fireEvent.change(screen.getByLabelText(/追加文本/), {
      target: { value: "第十一章 打虎\n正文……" },
    })
    fireEvent.click(screen.getByRole("button", { name: "确认追加" }))

    expect(
      await screen.findByText("分析进行中，请先暂停"),
    ).toBeInTheDocument()
  })

  it("rejects empty pasted text without calling the API", () => {
    renderDialog()

    fireEvent.click(screen.getByRole("button", { name: /粘贴文本/ }))
    fireEvent.click(screen.getByRole("button", { name: "确认追加" }))

    expect(screen.getByText("请先粘贴要追加的文本")).toBeInTheDocument()
    expect(mockedAppend).not.toHaveBeenCalled()
  })
})
