/**
 * 底图合成器
 * 将 SDF 区域场 + Hillshade + 底色合成为一张纹理图，
 * 输出 dataURL 用于 MapLibre image source。
 */

import type { MapStyle } from "./mapStyles"
import type { SDFLocation } from "./SDFRenderer"
import type { HillshadeLocation } from "./HillshadeRenderer"
import { renderSDF } from "./SDFRenderer"
import { renderHillshade } from "./HillshadeRenderer"

export interface CompositeResult {
  dataUrl: string          // data:image/png 底图纹理
  width: number
  height: number
  renderTimeMs: number
}

/**
 * 合成底图纹理
 * @param sdfLocations SDF 场域地点（含 radius）
 * @param hillshadeLocations Hillshade 地点（含 type）
 * @param style 风格配置
 * @param width 纹理宽度（建议 2048-4096）
 * @param height 纹理高度
 */
export function compositeBaseMap(
  sdfLocations: SDFLocation[],
  hillshadeLocations: HillshadeLocation[],
  style: MapStyle,
  width: number = 2048,
  height: number = 2048
): CompositeResult {
  const t0 = performance.now()

  const canvas = document.createElement("canvas")
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext("2d")!

  // 1. SDF 区域场（已在 shader 内混合底色，输出不透明像素）
  const sdfCanvas = renderSDF(sdfLocations, style, width, height)
  if (sdfCanvas) {
    ctx.drawImage(sdfCanvas, 0, 0)
  } else {
    // WebGL 不可用 — 先填底色再画渐变
    ctx.fillStyle = style.background
    ctx.fillRect(0, 0, width, height)
    // WebGL 不可用时降级：用径向渐变
    for (const loc of sdfLocations) {
      const terrain = style.terrain[loc.type]
      if (!terrain) continue
      const cx = loc.x * width, cy = loc.y * height
      const r = loc.radius * width * 3
      const [cr, cg, cb, ca] = terrain.fill
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r)
      grad.addColorStop(0, `rgba(${cr},${cg},${cb},${ca * 0.7})`)
      grad.addColorStop(1, `rgba(${cr},${cg},${cb},0)`)
      ctx.fillStyle = grad
      ctx.fillRect(cx - r, cy - r, r * 2, r * 2)
    }
  }

  // 3. Hillshade（multiply blend 叠加）
  if (hillshadeLocations.length > 0) {
    const hillshade = renderHillshade(hillshadeLocations, style, width, height)
    const hsCanvas = document.createElement("canvas")
    hsCanvas.width = width
    hsCanvas.height = height
    hsCanvas.getContext("2d")!.putImageData(hillshade, 0, 0)

    ctx.globalCompositeOperation = style.hillshade.blendMode as GlobalCompositeOperation
    ctx.globalAlpha = style.hillshade.opacity
    ctx.drawImage(hsCanvas, 0, 0)
    ctx.globalCompositeOperation = "source-over"
    ctx.globalAlpha = 1
  }

  const dataUrl = canvas.toDataURL("image/png")
  return {
    dataUrl,
    width,
    height,
    renderTimeMs: Math.round(performance.now() - t0),
  }
}
