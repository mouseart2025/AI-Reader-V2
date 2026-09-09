import { describe, expect, it } from "vitest"
import {
  SPECIAL_SPACE_TIER,
  SPECIAL_SPACE_LABEL,
  SPECIAL_SPACE_ICON,
  isSpecialSpaceTier,
} from "./specialSpace"

describe("specialSpace", () => {
  it("exports stable constants", () => {
    expect(SPECIAL_SPACE_TIER).toBe("realm")
    expect(SPECIAL_SPACE_LABEL).toBe("界域")
    expect(SPECIAL_SPACE_ICON).toBe("✦")
  })

  it("detects realm tier", () => {
    expect(isSpecialSpaceTier("realm")).toBe(true)
    expect(isSpecialSpaceTier("continent")).toBe(false)
    expect(isSpecialSpaceTier("")).toBe(false)
    expect(isSpecialSpaceTier(undefined)).toBe(false)
    expect(isSpecialSpaceTier(null)).toBe(false)
  })
})
