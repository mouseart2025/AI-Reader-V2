import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { UploadPreviewResponse } from "@/api/types"

vi.mock("@/api/client", () => ({
  uploadNovelWithProgress: vi.fn(),
  confirmImport: vi.fn(),
  confirmDataImport: vi.fn(),
  previewImport: vi.fn(),
  fetchNovel: vi.fn(),
  deleteNovel: vi.fn(),
  fetchSplitModes: vi.fn(),
  reSplitChapters: vi.fn(),
  inferPattern: vi.fn(),
  cleanAndResplit: vi.fn(),
  fetchRawText: vi.fn(),
}))

// 文本预览/正则模板组件与冒烟目标无关，替换为占位组件
vi.mock("@/components/shared/TextPreviewPanel", () => ({
  TextPreviewPanel: () => null,
}))
vi.mock("@/components/shared/RegexTemplateSelector", () => ({
  RegexTemplateSelector: () => null,
}))

import { confirmImport, uploadNovelWithProgress } from "@/api/client"
import { UploadDialog } from "./UploadDialog"

const mockedUpload = vi.mocked(uploadNovelWithProgress)
const mockedConfirmImport = vi.mocked(confirmImport)

const PREVIEW: UploadPreviewResponse = {
  title: "测试小说",
  author: "作者甲",
  file_hash: "h1",
  total_chapters: 2,
  total_words: 5000,
  chapters: [
    { chapter_num: 1, title: "第一章", word_count: 2500 },
    { chapter_num: 2, title: "第二章", word_count: 2500 },
  ],
  warnings: [],
  duplicate_novel_id: null,
}

function renderDialog(onOpenChange = vi.fn(), onImported = vi.fn()) {
  render(
    <UploadDialog open onOpenChange={onOpenChange} onImported={onImported} />,
  )
  return { onOpenChange, onImported }
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')
  if (!input) throw new Error("file input not found")
  return input
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("UploadDialog smoke", () => {
  it("renders the file-select stage when open", () => {
    renderDialog()

    expect(screen.getByText("上传小说")).toBeInTheDocument()
    expect(screen.getByText("点击或拖拽文件到此处")).toBeInTheDocument()
    expect(screen.getByText(/支持 \.txt \/ \.md 小说文件/)).toBeInTheDocument()
  })

  it("rejects files with an unsupported extension", () => {
    renderDialog()

    fireEvent.change(fileInput(), {
      target: { files: [new File(["x"], "notes.pdf", { type: "application/pdf" })] },
    })

    expect(screen.getByText("仅支持 .txt / .md / .air / .json 格式")).toBeInTheDocument()
    expect(mockedUpload).not.toHaveBeenCalled()
  })

  it("uploads a .txt file, edits metadata in the preview, and confirms import", async () => {
    mockedUpload.mockImplementation(async (_file, onProgress) => {
      onProgress?.(100)
      return PREVIEW
    })
    mockedConfirmImport.mockResolvedValue({ novel_id: "n1" } as unknown as Awaited<ReturnType<typeof confirmImport>>)
    const { onImported } = renderDialog()

    fireEvent.change(fileInput(), {
      target: { files: [new File(["第一章 内容"], "测试小说.txt", { type: "text/plain" })] },
    })

    // 预览阶段：书名/作者可编辑，显示章节统计
    const titleInput = await screen.findByLabelText("书名")
    expect(titleInput).toHaveValue("测试小说")
    expect(screen.getByLabelText("作者")).toHaveValue("作者甲")
    expect(screen.getByText(/共 2 章/)).toBeInTheDocument()
    expect(screen.getByText("5000字")).toBeInTheDocument()

    fireEvent.change(titleInput, { target: { value: "新书名" } })

    fireEvent.click(screen.getByRole("button", { name: /确认导入/ }))

    await waitFor(() => {
      expect(mockedConfirmImport).toHaveBeenCalledWith({
        file_hash: "h1",
        title: "新书名",
        author: "作者甲",
        excluded_chapters: undefined,
      })
    })
    // 导入成功后通知父组件刷新书架
    await waitFor(() => expect(onImported).toHaveBeenCalled(), { timeout: 2000 })
  })

  it("shows the upload error and returns to the select stage on failure", async () => {
    mockedUpload.mockRejectedValue(new Error("服务器开小差了"))
    renderDialog()

    fireEvent.change(fileInput(), {
      target: { files: [new File(["x"], "bad.txt", { type: "text/plain" })] },
    })

    expect(await screen.findByText("服务器开小差了")).toBeInTheDocument()
    // 回到文件选择阶段
    expect(screen.getByText("点击或拖拽文件到此处")).toBeInTheDocument()
  })
})
