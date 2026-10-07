/**
 * GCJ-02（火星坐标）⇄ WGS-84 转换。
 *
 * 高德/腾讯等国内地图服务使用 GCJ-02 加密坐标，GeoNames 等数据源
 * 使用 WGS-84。二者直接叠加会产生 100-600m 偏移，标记必须转换后
 * 再渲染；从地图读回的坐标（如拖拽编辑）需反向转换后再持久化。
 * 算法为公开的标准实现；中国境外坐标无偏移，原样返回。
 */

const PI = 3.1415926535897932384626
const A = 6378245.0 // 长半轴
const EE = 0.00669342162296594323 // 偏心率平方

export interface LatLng {
  lat: number
  lng: number
}

/** 是否在中国境外（境外不做偏移） */
export function outOfChina(lat: number, lng: number): boolean {
  return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271
}

function transformLat(x: number, y: number): number {
  let ret =
    -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x))
  ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0
  ret += ((20.0 * Math.sin(y * PI) + 40.0 * Math.sin((y / 3.0) * PI)) * 2.0) / 3.0
  ret += ((160.0 * Math.sin((y / 12.0) * PI) + 320 * Math.sin((y * PI) / 30.0)) * 2.0) / 3.0
  return ret
}

function transformLng(x: number, y: number): number {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x))
  ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0
  ret += ((20.0 * Math.sin(x * PI) + 40.0 * Math.sin((x / 3.0) * PI)) * 2.0) / 3.0
  ret += ((150.0 * Math.sin((x / 12.0) * PI) + 300.0 * Math.sin((x / 30.0) * PI)) * 2.0) / 3.0
  return ret
}

/** WGS-84 → GCJ-02 */
export function wgs84ToGcj02(lat: number, lng: number): LatLng {
  if (outOfChina(lat, lng)) return { lat, lng }
  let dLat = transformLat(lng - 105.0, lat - 35.0)
  let dLng = transformLng(lng - 105.0, lat - 35.0)
  const radLat = (lat / 180.0) * PI
  let magic = Math.sin(radLat)
  magic = 1 - EE * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  dLat = (dLat * 180.0) / (((A * (1 - EE)) / (magic * sqrtMagic)) * PI)
  dLng = (dLng * 180.0) / ((A / sqrtMagic) * Math.cos(radLat) * PI)
  return { lat: lat + dLat, lng: lng + dLng }
}

/** GCJ-02 → WGS-84（迭代反解,默认 3 轮收敛到亚毫米级） */
export function gcj02ToWgs84(lat: number, lng: number, iterations = 3): LatLng {
  if (outOfChina(lat, lng)) return { lat, lng }
  let wgs: LatLng = { lat, lng }
  for (let i = 0; i < iterations; i++) {
    const gcj = wgs84ToGcj02(wgs.lat, wgs.lng)
    wgs = {
      lat: wgs.lat + (lat - gcj.lat),
      lng: wgs.lng + (lng - gcj.lng),
    }
  }
  return wgs
}
