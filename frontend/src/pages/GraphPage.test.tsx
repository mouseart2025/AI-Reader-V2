import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { Novel } from "@/api/types"

// canvas 力导图在 jsdom 中无法渲染，替换为占位组件（页面逻辑层才是测试目标）
vi.mock("react-force-graph-2d", () => ({
  default: () => <div data-testid="force-graph" />,
}))

vi.mock("@/api/client", () => ({
  fetchGraphData: vi.fn(),
  fetchNovel: vi.fn(),
}))

vi.mock("@/components/entity-cards/EntityCardDrawer", () => ({
  EntityCardDrawer: () => null,
}))

vi.mock("@/lib/tracker", () => ({ trackEvent: vi.fn() }))
vi.mock("@/lib/tabTracking", () => ({ recordTabVisit: vi.fn() }))

import { fetchGraphData, fetchNovel } from "@/api/client"
import GraphPage from "./GraphPage"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useEntityCardStore } from "@/stores/entityCardStore"

const mockedFetchGraphData = vi.mocked(fetchGraphData)
const mockedFetchNovel = vi.mocked(fetchNovel)

const GRAPH_DATA = {
  nodes: [
    { id: "1", name: "孙悟空", type: "person", chapter_count: 10, org: "" },
    { id: "2", name: "唐僧", type: "person", chapter_count: 8, org: "" },
    { id: "3", name: "白骨精", type: "person", chapter_count: 5, org: "" },
  ],
  edges: [
    { source: "1", target: "2", relation_type: "师徒", weight: 5, chapters: [1, 2], category: "hierarchical" },
    { source: "1", target: "3", relation_type: "三打", weight: 2, chapters: [3], category: "hostile" },
  ],
  category_counts: { hierarchical: 1, hostile: 1 },
  analyzed_range: [1, 10],
  suggested_min_edge_weight: 1,
  max_edge_weight: 5,
}

const NOVEL: Novel = {
  id: "n1",
  title: "西游记",
  author: "吴承恩",
  total_chapters: 10,
  total_words: 100000,
  created_at: "2026-01-01",
  updated_at: "2026-01-01",
  is_sample: false,
  analysis_progress: 1,
  failed_count: 0,
  reading_progress: 0,
  last_opened: null,
}

const initialRangeState = useChapterRangeStore.getState()
const initialCardState = useEntityCardStore.getState()

beforeEach(() => {
  useChapterRangeStore.setState(initialRangeState, true)
  useEntityCardStore.setState({ ...initialCardState, profileCache: new Map() }, true)
  // 预置章节范围：VisualizationLayout 挂载时会 resetForNovel，
  // novelId 相同则跳过重置，避免 RangeSlider 出现 min===max
  useChapterRangeStore.setState({
    novelId: "n1",
    chapterStart: 1,
    chapterEnd: 10,
    analyzedFirst: 1,
    analyzedLast: 10,
    totalChapters: 10,
  })
  vi.clearAllMocks()
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  mockedFetchGraphData.mockResolvedValue(GRAPH_DATA as any)
  mockedFetchNovel.mockResolvedValue(NOVEL)
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/novel/n1/graph"]}>
      <Routes>
        <Route path="/novel/:novelId/graph" element={<GraphPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe("GraphPage smoke", () => {
  it("shows a loading state, then renders controls and category chips", async () => {
    renderPage()

    expect(screen.getByText("Loading graph...")).toBeInTheDocument()

    // 数据加载完成后出现控制按钮和分类 chips
    expect(await screen.findByRole("button", { name: "筛选" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "路径查找" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "主从 1" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "敌对 1" })).toBeInTheDocument()
    expect(screen.getByTestId("force-graph")).toBeInTheDocument()
    expect(screen.queryByText("Loading graph...")).not.toBeInTheDocument()

    expect(mockedFetchGraphData).toHaveBeenCalledWith("n1", 1, 10)
  })

  it("finds the shortest path between two characters via the search panel", async () => {
    renderPage()
    fireEvent.click(await screen.findByRole("button", { name: "路径查找" }))

    fireEvent.change(screen.getByPlaceholderText("人物 A"), { target: { value: "孙悟空" } })
    fireEvent.change(screen.getByPlaceholderText("人物 B"), { target: { value: "唐僧" } })
    fireEvent.click(screen.getByRole("button", { name: "查找" }))

    expect(await screen.findByText(/最短路径 \(1 步\)/)).toBeInTheDocument()

    // 清除后路径结果消失
    fireEvent.click(screen.getAllByRole("button", { name: "清除" })[0])
    expect(screen.queryByText(/最短路径/)).not.toBeInTheDocument()
  })

  it("toggling a category chip hides its edges and isolated nodes", async () => {
    renderPage()
    fireEvent.click(await screen.findByRole("button", { name: "敌对 1" }))

    // 打开筛选面板查看统计：白骨精失去唯一边后被过滤
    fireEvent.click(screen.getByRole("button", { name: "筛选" }))

    expect(
      await screen.findByText((_, el) => {
        const text = el?.textContent ?? ""
        return (
          el?.tagName === "P" &&
          text.includes("2/3 人物") &&
          text.includes("1/2 关系") &&
          text.includes("1 类隐藏")
        )
      }),
    ).toBeInTheDocument()
  })
})
