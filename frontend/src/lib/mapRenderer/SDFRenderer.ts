/**
 * SDF 区域场渲染器
 * 用独立 WebGL context 渲染 Metaball 场域到 offscreen canvas。
 * 每个地点作为引力源，产生有机的水彩晕染风格区域边界。
 */

import type { MapStyle, TerrainStyle } from "./mapStyles"

export interface SDFLocation {
  x: number      // 0-1 归一化坐标
  y: number
  radius: number // 场域半径（由 mentions/tier 决定）
  type: string   // → style.terrain[type].fill
}

/** 生成 GLSL ES if-chain 颜色查找函数 */
function buildShader(locationCount: number, terrain: Record<string, TerrainStyle>): {
  vert: string
  frag: string
  colorKeys: string[]
} {
  const colorKeys = Object.keys(terrain)

  const colorLines = colorKeys.map((key, i) => {
    const c = terrain[key].fill
    return `  ${i === 0 ? "if" : "else if"} (idx == ${i}) return vec3(${(c[0] / 255).toFixed(4)}, ${(c[1] / 255).toFixed(4)}, ${(c[2] / 255).toFixed(4)});`
  })
  const colorFunc = `vec3 getColor(int idx) {\n${colorLines.join("\n")}\n  else return vec3(0.9, 0.87, 0.8);\n}`

  const frag = `
precision mediump float;
uniform vec2 u_resolution;
uniform vec2 u_centers[${locationCount}];
uniform float u_radii[${locationCount}];
uniform float u_colorIdx[${locationCount}];

${colorFunc}

float metaball(vec2 p, vec2 center, float radius) {
  float d = length(p - center);
  return radius * radius / (d * d + 0.001);
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_resolution;
  float totalField = 0.0;
  vec3 weightedColor = vec3(0.0);
  float totalWeight = 0.0;

  for (int i = 0; i < ${locationCount}; i++) {
    float field = metaball(uv, u_centers[i], u_radii[i]);
    totalField += field;
    vec3 c = getColor(int(u_colorIdx[i]));
    weightedColor += field * c;
    totalWeight += field;
  }

  // Voronoi 着色：最强贡献者决定颜色
  float maxField = 0.0;
  vec3 dominantColor = vec3(0.941, 0.894, 0.816);
  for (int i = 0; i < ${locationCount}; i++) {
    float field = metaball(uv, u_centers[i], u_radii[i]);
    if (field > maxField) {
      maxField = field;
      dominantColor = getColor(int(u_colorIdx[i]));
    }
  }

  // 总场强控制着色范围（f2_aggressive 参数）
  float edge = smoothstep(0.6, 2.5, totalField);

  // 边缘微噪声
  float noise = fract(sin(dot(uv * 50.0, vec2(12.9898, 78.233))) * 43758.5453);
  edge *= 1.0 - noise * 0.06;

  vec3 bg = vec3(0.941, 0.894, 0.816); // #f0e4d0
  vec3 final = mix(bg, dominantColor, edge * 0.90);
  gl_FragColor = vec4(final, 1.0);
}
`

  const vert = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`

  return { vert, frag, colorKeys }
}

/**
 * 渲染 SDF 到 offscreen canvas
 * @returns HTMLCanvasElement（含 alpha 通道） 或 null（WebGL 不可用时）
 */
export function renderSDF(
  locations: SDFLocation[],
  style: MapStyle,
  width: number,
  height: number
): HTMLCanvasElement | null {
  if (locations.length === 0) return null

  // WebGL uniform 数组上限约 256-1024 个 float
  // 每个地点需要 2(center) + 1(radius) + 1(colorIdx) = 4 个 float
  // 上限约 256 个地点，超出需要 texture 方案
  const MAX_UNIFORM_LOCATIONS = 200
  const locs = locations.length > MAX_UNIFORM_LOCATIONS
    ? locations.slice(0, MAX_UNIFORM_LOCATIONS)
    : locations

  const canvas = document.createElement("canvas")
  canvas.width = width
  canvas.height = height

  const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: false, preserveDrawingBuffer: true })
  if (!gl) return null

  const { vert, frag, colorKeys } = buildShader(locs.length, style.terrain)

  // 编译着色器
  const vs = gl.createShader(gl.VERTEX_SHADER)!
  gl.shaderSource(vs, vert)
  gl.compileShader(vs)
  if (!gl.getShaderParameter(vs, gl.COMPILE_STATUS)) {
    console.warn("SDF vertex shader error:", gl.getShaderInfoLog(vs))
    return null
  }

  const fs = gl.createShader(gl.FRAGMENT_SHADER)!
  gl.shaderSource(fs, frag)
  gl.compileShader(fs)
  if (!gl.getShaderParameter(fs, gl.COMPILE_STATUS)) {
    console.warn("SDF fragment shader error:", gl.getShaderInfoLog(fs))
    return null
  }

  const prog = gl.createProgram()!
  gl.attachShader(prog, vs)
  gl.attachShader(prog, fs)
  gl.linkProgram(prog)
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.warn("SDF link error:", gl.getProgramInfoLog(prog))
    return null
  }

  gl.useProgram(prog)

  // 全屏四边形
  const buf = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buf)
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
  const posLoc = gl.getAttribLocation(prog, "a_pos")
  gl.enableVertexAttribArray(posLoc)
  gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0)

  // Uniforms
  gl.uniform2f(gl.getUniformLocation(prog, "u_resolution"), width, height)

  const centers: number[] = []
  const radii: number[] = []
  const colorIdx: number[] = []
  for (const loc of locs) {
    centers.push(loc.x, 1 - loc.y)
    radii.push(loc.radius)
    colorIdx.push(Math.max(0, colorKeys.indexOf(loc.type)))
  }
  gl.uniform2fv(gl.getUniformLocation(prog, "u_centers"), centers)
  gl.uniform1fv(gl.getUniformLocation(prog, "u_radii"), radii)
  gl.uniform1fv(gl.getUniformLocation(prog, "u_colorIdx"), colorIdx)

  // 渲染
  gl.viewport(0, 0, width, height)
  gl.clearColor(0, 0, 0, 0)
  gl.clear(gl.COLOR_BUFFER_BIT)
  gl.enable(gl.BLEND)
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)

  // 将 WebGL 结果复制到 2D canvas（避免 GL context 生命周期问题）
  const out = document.createElement("canvas")
  out.width = width
  out.height = height
  out.getContext("2d")!.drawImage(canvas, 0, 0)

  // 清理 WebGL 资源
  gl.deleteBuffer(buf)
  gl.deleteShader(vs)
  gl.deleteShader(fs)
  gl.deleteProgram(prog)
  const ext = gl.getExtension("WEBGL_lose_context")
  ext?.loseContext()

  return out
}
