/**
 * dump_coastlines.cjs — 把**画出来的**海岸线/浅滩几何原样导出成 JSON。
 *
 * ## 为什么从 DOM 取，而不是从后端 ring
 *
 * 读者看到的是这一份。`#coastline` 里的路径可能经过了 rough.js 的手绘化，
 * 也可能没有 —— 这本身就是待测的问题之一（手绘抖动到底是在**加**特征还是在**毁**特征）。
 * 用后端的 ring 量会把这一段完全跳过，于是"量到的"和"看到的"不是同一个东西。
 *
 * `d` 里的坐标已经是 **canvas 单位**（`#viewport` 的 zoom 变换在 group 之上，
 * 不在 path 属性里），所以不需要再乘缩放。
 *
 * ## 用法
 *
 *   node scripts/dump_coastlines.cjs <URL> --out /tmp/coastlines.json
 */

const fs = require("fs")
const path = require("path")

function loadPlaywright() {
  const cands = [
    process.env.PLAYWRIGHT_CORE,
    process.env.NODE_PATH ? path.join(process.env.NODE_PATH, "playwright-core") : null,
    "/Users/leonfeng/.workbuddy/binaries/node/workspace/node_modules/playwright-core",
  ]
  for (const c of cands) {
    if (!c) continue
    try {
      return require(c)
    } catch {
      /* 下一个候选 */
    }
  }
  throw new Error("找不到 playwright-core：设 PLAYWRIGHT_CORE 或 NODE_PATH")
}

function findChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH
  const root = path.join(process.env.HOME, "Library/Caches/ms-playwright")
  if (!fs.existsSync(root)) return undefined
  for (const d of fs.readdirSync(root)) {
    if (!d.startsWith("chromium-")) continue
    const exe = path.join(
      root,
      d,
      "chrome-mac-arm64",
      "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    )
    if (fs.existsSync(exe)) return exe
  }
  return undefined
}

const COLLECT = () => {
  const groups = ["coastline", "shelf", "coastline-ocean"]
  const out = { canvas: null, groups: {} }
  const svg = document.querySelector("svg")
  if (svg) {
    // canvas 尺寸从 `#viewport` 的父级 viewBox 取，供后续把坐标归一化
    out.canvas = svg.getAttribute("viewBox")
  }
  for (const gid of groups) {
    const g = document.getElementById(gid)
    if (!g) {
      out.groups[gid] = null
      continue
    }
    const shapes = []
    for (const el of Array.from(g.querySelectorAll("path"))) {
      const d = el.getAttribute("d") || ""
      const cs = getComputedStyle(el)
      shapes.push({
        tag: "path",
        d,
        stroke: el.getAttribute("stroke"),
        fill: el.getAttribute("fill"),
        strokeWidth: el.getAttribute("stroke-width"),
        opacity: el.getAttribute("opacity") || cs.opacity,
        nSubpaths: (d.match(/M/g) || []).length,
        nSegs: (d.match(/[LCQTA]/g) || []).length,
      })
    }
    out.groups[gid] = shapes
  }
  return out
}

;(async () => {
  const { chromium } = loadPlaywright()
  const args = process.argv.slice(2)
  let url = null
  let out = "/tmp/coastlines.json"
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--out") out = args[++i]
    else if (!args[i].startsWith("--") && !url) url = args[i]
  }
  if (!url) {
    console.error("用法：node scripts/dump_coastlines.cjs <URL> --out /tmp/coastlines.json")
    process.exit(2)
  }

  const browser = await chromium.launch({
    executablePath: findChromium(),
    args: ["--no-proxy-server", "--proxy-bypass-list=*"],
  })
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 })
  await page.goto(url, { waitUntil: "networkidle", timeout: 90000 })
  await page
    .waitForFunction(
      () =>
        !!document.querySelector("#viewport") &&
        !document.body.innerText.includes("计算地理坐标") &&
        !document.body.innerText.includes("求解空间布局") &&
        !document.body.innerText.includes("优化布局中") &&
        !document.body.innerText.includes("请稍候"),
      { timeout: 120000 },
    )
    .catch(() => {})
  await page.waitForTimeout(4000)

  const data = await page.evaluate(COLLECT)
  fs.writeFileSync(out, JSON.stringify(data))
  for (const gid of Object.keys(data.groups)) {
    const g = data.groups[gid]
    if (!g) {
      console.log(`${gid}: 不存在`)
      continue
    }
    const segs = g.reduce((s, x) => s + x.nSegs, 0)
    console.log(`${gid}: ${g.length} 条 path，共 ${segs} 段`)
  }
  console.log(`viewBox = ${data.canvas}`)
  console.log(`-> ${out}`)
  await browser.close()
})()
