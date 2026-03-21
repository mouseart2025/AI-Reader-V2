import { describe, it, expect } from "vitest"
import { translateSpatialType, translateSpatialValue, SPATIAL_TYPE_LABELS } from "./spatialLabels"

describe("translateSpatialType", () => {
  it("translates known types to Chinese", () => {
    expect(translateSpatialType("contains")).toBe("包含")
    expect(translateSpatialType("adjacent")).toBe("相邻")
    expect(translateSpatialType("direction")).toBe("方位")
    expect(translateSpatialType("distance")).toBe("距离")
  })

  it("passes through unknown types", () => {
    expect(translateSpatialType("unknown_type")).toBe("unknown_type")
  })

  it("covers all expected relation types", () => {
    const expected = ["direction", "distance", "contains", "adjacent", "separated_by", "in_between", "travel_path", "cluster"]
    for (const key of expected) {
      expect(SPATIAL_TYPE_LABELS[key]).toBeDefined()
    }
  })
})

describe("translateSpatialValue", () => {
  it("translates directional values", () => {
    expect(translateSpatialValue("south_of")).toBe("南方")
    expect(translateSpatialValue("north_of")).toBe("北方")
    expect(translateSpatialValue("east_of")).toBe("东方")
    expect(translateSpatialValue("west_of")).toBe("西方")
  })

  it("translates compound directions", () => {
    expect(translateSpatialValue("northeast_of")).toBe("东北方")
    expect(translateSpatialValue("southwest_of")).toBe("西南方")
  })

  it("passes through Chinese values unchanged", () => {
    expect(translateSpatialValue("三天路程")).toBe("三天路程")
    expect(translateSpatialValue("河流")).toBe("河流")
  })
})
