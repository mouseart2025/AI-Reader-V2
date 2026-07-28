import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { EntityCardDrawer } from "./EntityCardDrawer"

describe("EntityCardDrawer concept popup", () => {
  afterEach(() => {
    cleanup()
    useEntityCardStore.setState({ open: false, conceptPopup: null })
  })

  it("shows concept details without requiring an entity profile drawer", () => {
    render(
      <MemoryRouter>
        <EntityCardDrawer novelId="novel-1" />
      </MemoryRouter>,
    )

    act(() => {
      useEntityCardStore.getState().openConceptPopup({
        name: "罗汉金身",
        definition: "少林修行所追求的高深境界。",
        category: "修炼境界",
        related: ["法身"],
      })
    })

    expect(screen.getByRole("dialog").textContent).toContain("罗汉金身")
    expect(screen.getByRole("dialog").textContent).toContain("修炼境界")
    expect(screen.getByRole("dialog").textContent).toContain("法身")
  })
})
