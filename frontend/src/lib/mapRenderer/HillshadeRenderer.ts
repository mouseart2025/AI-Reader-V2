/**
 * Hillshade 渲染器
 * 从地点类型生成伪高度图 → 高斯模糊 → Sobel 梯度 + 光照 → 灰度 Hillshade
 */

import type { MapStyle } from "./mapStyles"

export interface HillshadeLocation {
  x: number  // 0-1 归一化坐标
  y: number
  type: string
}

/** 生成伪高度图（Float32 精度，避免量化噪声） */
function generateHeightmap(
  locations: HillshadeLocation[],
  style: MapStyle,
  w: number,
  h: number
): Float32Array {
  const data = new Float32Array(w * h)
  data.fill(128)

  // sigma 按纹理大小缩放，确保地形特征在不同分辨率下视觉大小一致
  const scaleFactor = Math.max(w, h) / 1024
  for (const loc of locations) {
    const terrain = style.terrain[loc.type]
    if (!terrain) continue
    const cx = loc.x * w, cy = loc.y * h
    const { height: ht, sigma } = terrain
    const scaledSigma = sigma * scaleFactor
    const r = scaledSigma * 3
    const x0 = Math.max(0, Math.floor(cx - r))
    const x1 = Math.min(w - 1, Math.ceil(cx + r))
    const y0 = Math.max(0, Math.floor(cy - r))
    const y1 = Math.min(h - 1, Math.ceil(cy + r))
    const inv2s2 = 1 / (2 * scaledSigma * scaledSigma)

    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const dx = x - cx, dy = y - cy
        data[y * w + x] += ht * Math.exp(-(dx * dx + dy * dy) * inv2s2)
      }
    }
  }
  return data
}

/** 3 遍 box blur ≈ 高斯模糊（O(n) per pass） */
function boxBlur(data: Float32Array, w: number, h: number, radius: number): Float32Array {
  // 水平 pass
  const hPass = new Float32Array(data.length)
  for (let y = 0; y < h; y++) {
    let sum = 0, count = 0
    for (let x = 0; x < Math.min(radius + 1, w); x++) { sum += data[y * w + x]; count++ }
    for (let x = 0; x < w; x++) {
      hPass[y * w + x] = sum / count
      const addX = x + radius + 1, remX = x - radius
      if (addX < w) { sum += data[y * w + addX]; count++ }
      if (remX >= 0) { sum -= data[y * w + remX]; count-- }
    }
  }
  // 垂直 pass
  const vPass = new Float32Array(data.length)
  for (let x = 0; x < w; x++) {
    let sum = 0, count = 0
    for (let y = 0; y < Math.min(radius + 1, h); y++) { sum += hPass[y * w + x]; count++ }
    for (let y = 0; y < h; y++) {
      vPass[y * w + x] = sum / count
      const addY = y + radius + 1, remY = y - radius
      if (addY < h) { sum += hPass[addY * w + x]; count++ }
      if (remY >= 0) { sum -= hPass[remY * w + x]; count-- }
    }
  }
  return vPass
}

/** Hillshade 从 float 高度图计算（Sobel 梯度 + 光照点积） */
function computeHillshade(
  hm: Float32Array,
  w: number,
  h: number,
  azimuth: number,
  altitude: number,
  zFactor: number
): ImageData {
  const result = new ImageData(w, h)
  const az = (azimuth * Math.PI) / 180
  const alt = (altitude * Math.PI) / 180
  const sinAlt = Math.sin(alt), cosAlt = Math.cos(alt)

  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const tl = hm[(y - 1) * w + x - 1], t = hm[(y - 1) * w + x], tr = hm[(y - 1) * w + x + 1]
      const l = hm[y * w + x - 1], r = hm[y * w + x + 1]
      const bl = hm[(y + 1) * w + x - 1], b = hm[(y + 1) * w + x], br = hm[(y + 1) * w + x + 1]

      const dzdx = ((tr + 2 * r + br) - (tl + 2 * l + bl)) / 8
      const dzdy = ((bl + 2 * b + br) - (tl + 2 * t + tr)) / 8

      const slope = Math.atan(zFactor * Math.sqrt(dzdx * dzdx + dzdy * dzdy))
      let aspect = Math.atan2(dzdy, -dzdx)
      if (aspect < 0) aspect += 2 * Math.PI

      let shade = Math.cos(slope) * sinAlt + Math.sin(slope) * cosAlt * Math.cos(az - aspect)
      shade = Math.max(0, Math.min(1, shade))

      const v = Math.round(shade * 255)
      const oi = (y * w + x) * 4
      result.data[oi] = v
      result.data[oi + 1] = v
      result.data[oi + 2] = v
      result.data[oi + 3] = 255
    }
  }
  // 边缘像素填充（避免黑边）
  for (let x = 0; x < w; x++) {
    const topI = x * 4, botI = ((h - 1) * w + x) * 4
    const topSrc = (w + x) * 4, botSrc = ((h - 2) * w + x) * 4
    for (let c = 0; c < 4; c++) {
      result.data[topI + c] = result.data[topSrc + c]
      result.data[botI + c] = result.data[botSrc + c]
    }
  }
  for (let y = 0; y < h; y++) {
    const leftI = (y * w) * 4, rightI = (y * w + w - 1) * 4
    const leftSrc = (y * w + 1) * 4, rightSrc = (y * w + w - 2) * 4
    for (let c = 0; c < 4; c++) {
      result.data[leftI + c] = result.data[leftSrc + c]
      result.data[rightI + c] = result.data[rightSrc + c]
    }
  }
  return result
}

/**
 * 渲染 Hillshade
 * @returns ImageData（灰度，用于 multiply blend 叠加底图）
 */
export function renderHillshade(
  locations: HillshadeLocation[],
  style: MapStyle,
  width: number,
  height: number
): ImageData {
  // 1. 生成高度图
  const rawHM = generateHeightmap(locations, style, width, height)

  // 2. 高斯模糊 (3 遍 box blur)
  let blurred = rawHM
  const blurRadius = Math.max(4, Math.round(8 * Math.max(width, height) / 1024))
  for (let pass = 0; pass < 3; pass++) blurred = boxBlur(blurred, width, height, blurRadius)

  // 3. Hillshade
  return computeHillshade(
    blurred,
    width,
    height,
    style.hillshade.azimuth,
    style.hillshade.altitude,
    style.hillshade.zFactor
  )
}
