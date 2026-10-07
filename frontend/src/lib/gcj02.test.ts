import { describe, expect, it } from "vitest"
import { gcj02ToWgs84, outOfChina, wgs84ToGcj02 } from "./gcj02"

describe("gcj02 坐标转换", () => {
  it("中国境外坐标不做偏移", () => {
    // 伦敦
    expect(wgs84ToGcj02(51.5074, -0.1278)).toEqual({ lat: 51.5074, lng: -0.1278 })
    // 东京(经度超出中国范围)
    expect(wgs84ToGcj02(35.6762, 139.6503)).toEqual({ lat: 35.6762, lng: 139.6503 })
    expect(outOfChina(39.9042, 116.4074)).toBe(false)
  })

  it("中国境内坐标产生合理量级与方向的偏移", () => {
    // 北京(WGS-84)
    const gcj = wgs84ToGcj02(39.9042, 116.4074)
    const dLat = gcj.lat - 39.9042
    const dLng = gcj.lng - 116.4074
    // 国内偏移典型量级:0.001°~0.01°(约 100-600m)
    expect(Math.abs(dLat)).toBeGreaterThan(0.0005)
    expect(Math.abs(dLat)).toBeLessThan(0.01)
    expect(Math.abs(dLng)).toBeGreaterThan(0.0005)
    expect(Math.abs(dLng)).toBeLessThan(0.01)
    // 已知:国内 GCJ 偏移后纬度偏北、经度偏东
    expect(dLat).toBeGreaterThan(0)
    expect(dLng).toBeGreaterThan(0)
  })

  it("往返转换误差可忽略(编辑拖拽场景)", () => {
    const points: [number, number][] = [
      [39.9042, 116.4074], // 北京
      [31.2304, 121.4737], // 上海
      [22.3193, 114.1694], // 香港
      [30.5728, 104.0668], // 成都
    ]
    for (const [lat, lng] of points) {
      const roundTrip = gcj02ToWgs84(...Object.values(wgs84ToGcj02(lat, lng)) as [number, number])
      expect(Math.abs(roundTrip.lat - lat)).toBeLessThan(1e-5)
      expect(Math.abs(roundTrip.lng - lng)).toBeLessThan(1e-5)
    }
  })

  it("同一点转换结果确定(可缓存)", () => {
    expect(wgs84ToGcj02(32.0603, 118.7969)).toEqual(wgs84ToGcj02(32.0603, 118.7969))
  })
})
