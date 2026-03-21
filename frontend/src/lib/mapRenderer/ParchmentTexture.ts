/**
 * 羊皮纸纹理生成器
 * 用 SVG feTurbulence 离屏渲染纸张纹理，输出 dataURL 用于 CSS background-image。
 */

import type { MapStyle } from "./mapStyles"

let cachedDataUrl: string | null = null
let cachedStyleId: string | null = null

/**
 * 生成羊皮纸纹理 dataURL（带缓存）
 * 仅在风格变化时重新生成
 */
export async function generateParchmentTexture(
  style: MapStyle,
  size: number = 512
): Promise<string> {
  if (cachedDataUrl && cachedStyleId === style.id) return cachedDataUrl

  const svgNS = "http://www.w3.org/2000/svg"
  const svg = document.createElementNS(svgNS, "svg")
  svg.setAttribute("width", String(size))
  svg.setAttribute("height", String(size))

  const defs = document.createElementNS(svgNS, "defs")
  const filter = document.createElementNS(svgNS, "filter")
  filter.setAttribute("id", "parch")
  filter.setAttribute("x", "0%")
  filter.setAttribute("y", "0%")
  filter.setAttribute("width", "100%")
  filter.setAttribute("height", "100%")

  // 纤维噪声
  const turb = document.createElementNS(svgNS, "feTurbulence")
  turb.setAttribute("type", "fractalNoise")
  turb.setAttribute("baseFrequency", String(style.paper.noiseFrequency))
  turb.setAttribute("numOctaves", String(style.paper.noiseOctaves))
  turb.setAttribute("seed", "42")
  turb.setAttribute("stitchTiles", "stitch")
  turb.setAttribute("result", "noise")
  filter.appendChild(turb)

  // 去色
  const cm = document.createElementNS(svgNS, "feColorMatrix")
  cm.setAttribute("type", "saturate")
  cm.setAttribute("values", "0")
  cm.setAttribute("in", "noise")
  cm.setAttribute("result", "gray")
  filter.appendChild(cm)

  // 底色
  const flood = document.createElementNS(svgNS, "feFlood")
  flood.setAttribute("flood-color", style.background)
  flood.setAttribute("flood-opacity", "1")
  flood.setAttribute("result", "base")
  filter.appendChild(flood)

  // multiply 混合
  const blend = document.createElementNS(svgNS, "feBlend")
  blend.setAttribute("mode", "multiply")
  blend.setAttribute("in", "gray")
  blend.setAttribute("in2", "base")
  blend.setAttribute("result", "paper")
  filter.appendChild(blend)

  // 霉斑
  if (style.paper.stainOpacity > 0) {
    const turb2 = document.createElementNS(svgNS, "feTurbulence")
    turb2.setAttribute("type", "fractalNoise")
    turb2.setAttribute("baseFrequency", "0.02")
    turb2.setAttribute("numOctaves", "2")
    turb2.setAttribute("seed", "7")
    turb2.setAttribute("result", "stainNoise")
    filter.appendChild(turb2)

    const cm2 = document.createElementNS(svgNS, "feColorMatrix")
    cm2.setAttribute("type", "matrix")
    cm2.setAttribute(
      "values",
      `0 0 0 0 0.4  0 0 0 0 0.35  0 0 0 0 0.2  0 0 0 ${style.paper.stainOpacity} 0`
    )
    cm2.setAttribute("in", "stainNoise")
    cm2.setAttribute("result", "stain")
    filter.appendChild(cm2)

    const comp = document.createElementNS(svgNS, "feComposite")
    comp.setAttribute("operator", "over")
    comp.setAttribute("in", "stain")
    comp.setAttribute("in2", "paper")
    filter.appendChild(comp)
  }

  defs.appendChild(filter)
  svg.appendChild(defs)

  const rect = document.createElementNS(svgNS, "rect")
  rect.setAttribute("width", "100%")
  rect.setAttribute("height", "100%")
  rect.setAttribute("filter", "url(#parch)")
  svg.appendChild(rect)

  // SVG → Canvas → dataURL
  const dataUrl = await new Promise<string>((resolve) => {
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], {
      type: "image/svg+xml",
    })
    const url = URL.createObjectURL(blob)
    const img = new Image()
    img.onload = () => {
      const c = document.createElement("canvas")
      c.width = size
      c.height = size
      c.getContext("2d")!.drawImage(img, 0, 0, size, size)
      URL.revokeObjectURL(url)
      resolve(c.toDataURL("image/png"))
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      resolve("")  // fallback: no texture
    }
    img.src = url
  })

  cachedDataUrl = dataUrl
  cachedStyleId = style.id
  return dataUrl
}
