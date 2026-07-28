import { beforeEach, describe, expect, it } from "vitest"
import { useReadingSettingsStore } from "./readingSettingsStore"

describe("readingSettingsStore", () => {
  beforeEach(() => {
    useReadingSettingsStore.setState({ paragraphIndent: "two" })
  })

  it("uses a two-character first-line indent by default", () => {
    expect(useReadingSettingsStore.getState().paragraphIndent).toBe("two")
  })

  it("allows readers to disable first-line indentation", () => {
    useReadingSettingsStore.getState().setParagraphIndent("none")

    expect(useReadingSettingsStore.getState().paragraphIndent).toBe("none")
  })
})
