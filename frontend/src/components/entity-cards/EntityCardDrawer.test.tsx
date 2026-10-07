import { fireEvent, render, screen } from "@testing-library/react"
import { act } from "react"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { EntityProfile } from "@/api/types"

vi.mock("@/api/client", () => ({
  fetchEntityProfile: vi.fn(),
}))

// 四类实体卡片内容很重，冒烟测试只验证 drawer 的调度逻辑，替换为占位组件
vi.mock("@/components/entity-cards/PersonCard", () => ({
  PersonCard: ({ profile }: { profile: EntityProfile }) => (
    <div data-testid="person-card">{profile.name}</div>
  ),
}))
vi.mock("@/components/entity-cards/LocationCard", () => ({
  LocationCard: () => <div data-testid="location-card" />,
}))
vi.mock("@/components/entity-cards/ItemCard", () => ({
  ItemCard: () => <div data-testid="item-card" />,
}))
vi.mock("@/components/entity-cards/OrgCard", () => ({
  OrgCard: () => <div data-testid="org-card" />,
}))
vi.mock("@/components/entity-cards/AliasEditControls", () => ({
  AliasEditControls: () => null,
}))

import { fetchEntityProfile } from "@/api/client"
import { EntityCardDrawer } from "./EntityCardDrawer"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { useReadingStore } from "@/stores/readingStore"

const mockedFetchProfile = vi.mocked(fetchEntityProfile)

const initialCardState = useEntityCardStore.getState()
const initialReadingState = useReadingStore.getState()

beforeEach(() => {
  useEntityCardStore.setState({ ...initialCardState, profileCache: new Map() }, true)
  useReadingStore.setState(initialReadingState, true)
  vi.clearAllMocks()
})

function renderDrawer() {
  return render(
    <MemoryRouter>
      <EntityCardDrawer novelId="n1" />
    </MemoryRouter>,
  )
}

function stubProfile(name: string) {
  return { name, type: "person" } as unknown as Awaited<
    ReturnType<typeof fetchEntityProfile>
  >
}

function openCard(name: string, type: "person" | "location" = "person") {
  act(() => {
    useEntityCardStore.getState().openCard(name, type)
  })
}

describe("EntityCardDrawer smoke", () => {
  it("renders nothing when closed", () => {
    const { container } = renderDrawer()
    expect(container).toBeEmptyDOMElement()
  })

  it("opens in loading state and renders the profile after fetch", async () => {
    mockedFetchProfile.mockResolvedValue(stubProfile("孙悟空"))
    renderDrawer()

    openCard("孙悟空")

    expect(screen.getByText("Loading...")).toBeInTheDocument()
    // 面包屑立即可见
    expect(screen.getByText("孙悟空")).toBeInTheDocument()

    // 加载完成后渲染对应类型的卡片
    expect(await screen.findByTestId("person-card")).toHaveTextContent("孙悟空")
    expect(mockedFetchProfile).toHaveBeenCalledWith("n1", "孙悟空", "person")
    expect(screen.queryByText("Loading...")).not.toBeInTheDocument()
  })

  it("closes on Escape", async () => {
    mockedFetchProfile.mockResolvedValue(stubProfile("孙悟空"))
    renderDrawer()
    openCard("孙悟空")
    await screen.findByTestId("person-card")

    fireEvent.keyDown(window, { key: "Escape" })

    expect(useEntityCardStore.getState().open).toBe(false)
    expect(screen.queryByTestId("person-card")).not.toBeInTheDocument()
  })

  it("shows an error message when the profile fetch fails", async () => {
    mockedFetchProfile.mockRejectedValue(new Error("boom"))
    renderDrawer()
    openCard("孙悟空")

    expect(await screen.findByText("加载失败，请重试")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument()
  })

  it("refetches when the retry button is clicked after a failure", async () => {
    mockedFetchProfile
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue(stubProfile("孙悟空"))
    renderDrawer()
    openCard("孙悟空")
    expect(await screen.findByText("加载失败，请重试")).toBeInTheDocument()
    expect(mockedFetchProfile).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole("button", { name: "重试" }))

    expect(await screen.findByTestId("person-card")).toBeInTheDocument()
    expect(mockedFetchProfile).toHaveBeenCalledTimes(2)
  })

  it("serves a reopened entity from the profile cache instead of refetching", async () => {
    mockedFetchProfile.mockResolvedValue(stubProfile("孙悟空"))
    renderDrawer()

    openCard("孙悟空")
    await screen.findByTestId("person-card")
    expect(mockedFetchProfile).toHaveBeenCalledTimes(1)

    act(() => {
      useEntityCardStore.getState().close()
    })
    openCard("孙悟空")

    // 缓存命中：卡片直接出现，没有第二次请求
    expect(await screen.findByTestId("person-card")).toBeInTheDocument()
    expect(mockedFetchProfile).toHaveBeenCalledTimes(1)
  })

  it("resolves aliases through the reading store alias map", async () => {
    mockedFetchProfile.mockResolvedValue(stubProfile("孙悟空"))
    useReadingStore.getState().setAliasMap({ 猴哥: "孙悟空" })
    renderDrawer()

    openCard("猴哥")

    expect(await screen.findByTestId("person-card")).toHaveTextContent("孙悟空")
    expect(mockedFetchProfile).toHaveBeenCalledWith("n1", "孙悟空", "person")
  })
})
