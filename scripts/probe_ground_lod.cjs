/**
 * probe_ground_lod.cjs — 地面符号层的「间距 ÷ 尺寸」实测器
 *
 * ## 为什么需要它
 *
 * `terrainHints.ts` 是**屏幕定距**（screen-pitched）层：格子间距恒为**当前 LOD 档的
 * 间距**（`GROUND_LOD`，L0 = 32 屏幕像素），与缩放无关。它自己的验收判据是
 * **中心距 ÷ 记号尺寸 ≥ 1.5**（低于 1.0 记号开始互相吃掉，视场读作「填充」而非「记号」）。
 *
 * 2026-09-29 先加了连续增益 `groundZoomGain = 1 + 0.63·log2(k)`：只放大尺寸、不动间距。
 * 本脚本量出它在 k≈9.75 把判据压到 **0.502**（99.1% 的记号与最近邻重叠）—— 图上就是
 * "从散点记号变成一张编织席子"。修法是把连续增益换成 LOD 阶梯（间距分档下移 + 尺寸按
 * 间距定比例），本脚本负责证明换对了。
 *
 * 每次同时切 `globalThis.__groundLod`（见 terrainHints.ts 的 seam 说明）：
 *   - `"legacy"` = 旧的连续增益（间距恒 32）
 *   - 自动       = LOD 阶梯
 *
 * ## 它量什么
 *
 * 对每个地面记号取其**屏幕**包围盒（`getBoundingClientRect`，后变换，直接是读者
 * 看到的尺寸），然后：
 *   - 最近邻中心距 ÷ 两记号平均尺寸 —— 即验收判据本身，按类别给分位数；
 *   - 重叠（ratio < 1.0）与跌破判据（ratio < 1.5）的占比；
 *   - 最近邻中心距本身 —— 即该缩放下实际生效的「间距」，用来验证间距是否按档下移；
 *   - 记号数与 `NODE_BUDGET`（1400）的关系 —— 判「预算是否咬合」。
 *
 * ⚠️ 量的是**包围盒**不是**墨迹**：`terrain-water-*` 是 `viewBox="0 0 16 10"` 的
 * 描边字形，盒子大半是空的。但描边的**横向跨度**确实吃满盒子宽度，而横向跨度正是
 * 间距必须让开的那一维 —— 所以这个尺子对「会不会撞上」是紧的，对「墨迹覆盖多少」
 * 才是松的。别拿它当覆盖率用。
 *
 * ⚠️ 翻标志**不会重建图层**（重建挂在 `terrainLodKey` 上），脚本用 96 px 平移往返
 * 逼一次重绘，并靠整数像素平移的可逆性保证两组量的是同一帧。
 *
 * ## 用法
 *
 *   node scripts/probe_ground_lod.cjs \
 *     --url http://127.0.0.1:5173/map/<id> \
 *     --k 1,2.5,5,9.75 \
 *     --anchor 800,500 \
 *     --out /tmp/ground_lod.json
 *
 * 位置参数按 `probe_map_dom.cjs` 的约定：第一个非 `--` 参数是 URL。
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

/** 页面内：取记号几何 + 最近邻统计。**必须与生产同一份坐标来源**（即 DOM）。 */
const COLLECT = () => {
  const nodes = Array.from(document.querySelectorAll("#terrain use"))
  const items = []
  for (const n of nodes) {
    const r = n.getBoundingClientRect()
    if (r.width <= 0 && r.height <= 0) continue
    items.push({
      cx: r.x + r.width / 2,
      cy: r.y + r.height / 2,
      // 记号尺寸取长边：字形有横竖之分，取长边是「它占据的空间」的上界，
      // 与判据「间距 ÷ 记号尺寸」里的「尺寸」同义。
      size: Math.max(r.width, r.height),
      // 类别由 `<use>` 的 href 反查（生产侧 symbolId 前缀即类别）
      sym: (n.getAttribute("href") || n.getAttribute("xlink:href") || "").replace("#", ""),
    })
  }
  // 最近邻中心距，O(n²)：n≈1200 ⇒ 1.4M 次，页内可接受。
  const out = items.map(() => ({ d: Infinity, j: -1 }))
  for (let i = 0; i < items.length; i++) {
    for (let j = i + 1; j < items.length; j++) {
      const dx = items[i].cx - items[j].cx
      const dy = items[i].cy - items[j].cy
      const d2 = dx * dx + dy * dy
      if (d2 < out[i].d * out[i].d) {
        out[i].d = Math.sqrt(d2)
        out[i].j = j
      }
      if (d2 < out[j].d * out[j].d) {
        out[j].d = Math.sqrt(d2)
        out[j].j = i
      }
    }
  }
  const marks = items.map((it, i) => {
    const nb = out[i]
    const meanSize = nb.j >= 0 ? (it.size + items[nb.j].size) / 2 : it.size
    return {
      sym: it.sym,
      size: it.size,
      nnDist: nb.d === Infinity ? null : nb.d,
      // 验收判据本体
      ratio: nb.d === Infinity || meanSize <= 0 ? null : nb.d / meanSize,
      // 记号的屏幕长度 —— 判据的分子是「间距」，即中心距
    }
  })
  return {
    scale: +(/scale\(([\d.]+)\)/.exec(
      (document.querySelector("#viewport") || {}).getAttribute?.("transform") || "",
    ) || [])[1] || 1,
    marks,
    markCount: document.querySelectorAll(".location-item").length,
    labelCount: Array.from(document.querySelectorAll(".loc-label")).filter(
      (e) => e.getBoundingClientRect().width > 0,
    ).length,
  }
}

/** 分位数（线性插值，与探针惯例一致）。 */
function q(sorted, p) {
  if (!sorted.length) return null
  const i = (sorted.length - 1) * p
  const lo = Math.floor(i)
  const hi = Math.ceil(i)
  if (lo === hi) return sorted[lo]
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo)
}

function summarise(raw) {
  const withNb = raw.marks.filter((m) => m.ratio !== null && m.size > 0)
  const byCat = new Map()
  for (const m of withNb) {
    // `terrain-<cat>-<variant>` 之类；取第二个 '-' 之前当类别
    const cat = (m.sym.split("-")[1] || m.sym) || "(无)"
    if (!byCat.has(cat)) byCat.set(cat, [])
    byCat.get(cat).push(m)
  }
  const cats = {}
  for (const [cat, arr] of byCat) {
    const r = arr.map((m) => m.ratio).sort((a, b) => a - b)
    const d = arr.map((m) => m.nnDist).sort((a, b) => a - b)
    const s = arr.map((m) => m.size).sort((a, b) => a - b)
    cats[cat] = {
      n: arr.length,
      ratio_p05: q(r, 0.05),
      ratio_p50: q(r, 0.5),
      ratio_p95: q(r, 0.95),
      overlap_lt_1: r.filter((x) => x < 1).length / r.length,
      below_floor_lt_1_5: r.filter((x) => x < 1.5).length / r.length,
      nndist_p50: q(d, 0.5),
      size_p50: q(s, 0.5),
      size_p95: q(s, 0.95),
    }
  }
  const allR = withNb.map((m) => m.ratio).sort((a, b) => a - b)
  const allD = withNb.map((m) => m.nnDist).sort((a, b) => a - b)
  const allS = withNb.map((m) => m.size).sort((a, b) => a - b)
  return {
    n: withNb.length,
    ratio_p05: q(allR, 0.05),
    ratio_p50: q(allR, 0.5),
    ratio_p95: q(allR, 0.95),
    overlap_lt_1: allR.length ? allR.filter((x) => x < 1).length / allR.length : null,
    below_floor_lt_1_5: allR.length ? allR.filter((x) => x < 1.5).length / allR.length : null,
    nndist_p50: q(allD, 0.5),
    size_p50: q(allS, 0.5),
    size_p95: q(allS, 0.95),
    byCategory: cats,
  }
}

const f3 = (x) => (x === null || x === undefined ? "  n/a" : x.toFixed(3).padStart(6))

;(async () => {
  const { chromium } = loadPlaywright()
  const args = process.argv.slice(2)
  let url = null
  let targets = [1, 2.5, 5, 9.75]
  let anchor = { x: 800, y: 500 }
  let out = "/tmp/ground_lod.json"
  let shotDir = null
  let overrides = []
  let strokes = []
  for (let i = 0; i < args.length; i++) {
    const a = args[i]
    if (a === "--url") url = args[++i]
    else if (a === "--k") targets = args[++i].split(",").map(Number)
    else if (a === "--anchor") {
      const [x, y] = args[++i].split(",").map(Number)
      anchor = { x, y }
    } else if (a === "--out") out = args[++i]
    else if (a === "--shots") shotDir = args[++i]
    else if (a === "--strokes") {
      // `1.1,1.4,1.8` —— 线宽（stroke-width）。与几何判据正交，见 NovelMap.tsx 的 seam 注释。
      strokes = args[++i].split(",").map(Number)
      if (strokes.some((v) => !Number.isFinite(v) || v <= 0)) {
        console.error(`--strokes 项无法解析：${args[i]}`)
        process.exit(2)
      }
    } else if (a === "--overrides") {
      // `p20f1.30,p24f1.60` —— 直接扫 (间距, 填充) 两个旋钮。取代"自动/legacy"两组。
      overrides = args[++i].split(",").map((s) => {
        const m = /^p(\d+(?:\.\d+)?)f(\d+(?:\.\d+)?)$/.exec(s.trim())
        if (!m) {
          console.error(`--overrides 项无法解析：${s}（应为 p<间距>f<填充>，如 p20f1.30）`)
          process.exit(2)
        }
        return { pitchPx: +m[1], fill: +m[2] }
      })
    }
    else if (!a.startsWith("--") && !url) url = a
  }
  if (!url) {
    console.error("用法：node scripts/probe_ground_lod.cjs --url <URL> [--k 1,2.5,5,9.75]")
    process.exit(2)
  }

  const browser = await chromium.launch({
    executablePath: findChromium(),
    args: ["--no-proxy-server", "--proxy-bypass-list=*"],
  })
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  })
  await page.goto(url, { waitUntil: "networkidle", timeout: 90000 })
  // 语义就绪判据，与本仓既有探针同一条，不另造第二套。
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
  await page.waitForSelector(".location-item", { timeout: 30000 }).catch(() => {})
  await page.waitForTimeout(4000)

  const results = []
  const sortedTargets = [...targets].sort((a, b) => a - b)
  for (const target of sortedTargets) {
    // 只往上走，不缩回来：D3 的缩放是连续的，回缩会引入另一段路径。
    // 因此目标 k 必须单调递增传入（脚本自己排序）。
    let k = await page.evaluate(() =>
      +(/scale\(([\d.]+)\)/.exec(
        document.querySelector("#viewport").getAttribute("transform") || "",
      ) || [])[1] || 1,
    )
    let guard = 0
    while (k < target * 0.97 && guard++ < 40) {
      await page.mouse.move(anchor.x, anchor.y)
      await page.mouse.wheel(0, -240)
      await page.waitForTimeout(260)
      k = await page.evaluate(() =>
        +(/scale\(([\d.]+)\)/.exec(
          document.querySelector("#viewport").getAttribute("transform") || "",
        ) || [])[1] || 1,
      )
    }
    await page.waitForTimeout(2200) // 让这一帧画完再量

    const conditions = []
    const strokeList = strokes.length ? strokes : [null]
    if (overrides.length) {
      // 叉乘：每个 (间距, 填充) × 每个线宽
      for (const o of overrides)
        for (const s of strokeList)
          conditions.push({
            mode: `p${o.pitchPx}f${o.fill}`,
            override: o,
            stroke: s,
            label: `p${o.pitchPx}f${o.fill}${s ? `w${s}` : ""}`,
          })
    } else {
      for (const s of strokeList) {
        conditions.push({ mode: "legacy", stroke: s, label: s ? `legacyw${s}` : "legacy" })
        conditions.push({ mode: "auto", stroke: s, label: s ? `autow${s}` : "auto" })
      }
    }
    for (const cond of conditions) {
      // ⚠️ 只翻标志**不会重建图层** —— 重建挂在 `terrainLodKey` 上
      // （`NovelMap.tsx:271`：`round(log2 k * 2) : round(tx/96) : round(ty/96)`）。
      // 所以翻完必须**逼一次重建**：平移 96 px 再平移回来，键变两次 ⇒ 重绘两次，
      // 而整数像素的平移是可逆的，回到同一个 transform，两种条件看到的是同一帧。
      // 第一版没有这一步，于是"开/关"两组数字逐位相同（尺寸、p50 全部 +0.000），
      // 差点被当成"这个开关没用"。
      await page.evaluate((c) => {
        if (c.mode === "legacy") globalThis.__groundLod = "legacy"
        else if (c.mode === "auto") delete globalThis.__groundLod
        else globalThis.__groundLod = { pitchPx: c.override.pitchPx, fill: c.override.fill }
        // 线宽：与几何判据正交（不动记号、自相关也看不见它），所以必须与
        // (间距, 填充) 一起扫，才能知道"墨量"能不能在不引入印花的前提下买到。
        if (c.stroke === null || c.stroke === undefined) delete globalThis.__groundStroke
        else globalThis.__groundStroke = c.stroke
      }, cond)
      await page.waitForTimeout(200)
      await page.mouse.move(anchor.x, anchor.y)
      await page.mouse.down()
      await page.mouse.move(anchor.x + 96, anchor.y, { steps: 6 })
      await page.mouse.up()
      await page.waitForTimeout(500)
      await page.mouse.move(anchor.x, anchor.y)
      await page.mouse.down()
      await page.mouse.move(anchor.x - 96, anchor.y, { steps: 6 })
      await page.mouse.up()
      await page.waitForTimeout(2200)
      const raw = await page.evaluate(COLLECT)
      const s = summarise(raw)
      const lod = await page.evaluate((c) => {
        const t = (document.querySelector("#viewport") || {}).getAttribute?.("transform") || ""
        const k = +(/scale\(([\d.]+)\)/.exec(t) || [])[1] || 1
        if (c.mode === "legacy") return "legacy"
        if (c.mode !== "auto") return `${c.override.pitchPx}/${c.override.fill}`
        return k >= 5 ? 2 : k >= 2.4 ? 1 : 0
      }, cond)
      if (raw.marks.length === 0) {
        // 空图层会伪装成"判据完美"（没有记号就没有重叠）。今天已经栽过一次。
        console.log(`⚠️ k=${raw.scale.toFixed(2)} ${cond.mode}: 地面层 0 个记号 —— 这一帧不能用来下结论`)
        continue
      }
      results.push({
        targetK: target,
        k: raw.scale,
        mode: cond.mode,
        lod,
        markCount: raw.markCount,
        labelCount: raw.labelCount,
        ...s,
      })
      console.log(
        `k=${raw.scale.toFixed(2).padStart(5)}  ${cond.label.padEnd(12)} L${lod === "legacy" ? "-" : lod}` +
          `  记号 ${String(s.n).padStart(4)}` +
          `  间距/尺寸 p05 ${f3(s.ratio_p05)} p50 ${f3(s.ratio_p50)} p95 ${f3(s.ratio_p95)}` +
          `  <1.0 ${((s.overlap_lt_1 || 0) * 100).toFixed(1)}%` +
          `  <1.5 ${((s.below_floor_lt_1_5 || 0) * 100).toFixed(1)}%` +
          `  中心距p50 ${f3(s.nndist_p50)}  尺寸p50 ${f3(s.size_p50)} p95 ${f3(s.size_p95)}`,
      )
      if (shotDir) {
        fs.mkdirSync(shotDir, { recursive: true })
        await page.screenshot({
          path: path.join(shotDir, `k${raw.scale.toFixed(2)}_${cond.label}.png`),
        })
      }
    }
    await page.evaluate(() => {
      delete globalThis.__groundLod
      delete globalThis.__groundStroke
    })
  }

  // ── 配对差（同一会话、同一机位、交替切档）──
  console.log("\n配对差 LOD 阶梯(auto) − 旧连续增益(legacy)，同 k：")
  for (const target of sortedTargets) {
    const on = results.find((r) => r.targetK === target && r.mode === "auto")
    const off = results.find((r) => r.targetK === target && r.mode === "legacy")
    if (!on || !off) continue
    const d = (a, b) => {
      if (a === null || b === null || a === undefined || b === undefined) return "n/a"
      const v = b - a
      return `${v >= 0 ? "+" : ""}${v.toFixed(3)}`
    }
    console.log(
      `  k≈${target}: 记号数 ${off.n} -> ${on.n}` +
        `  尺寸p50 ${d(off.size_p50, on.size_p50)}` +
        `  间距p50 ${d(off.nndist_p50, on.nndist_p50)}` +
        `  判据p50 ${d(off.ratio_p50, on.ratio_p50)}` +
        `  <1.0 ${((off.overlap_lt_1 || 0) * 100).toFixed(1)}% -> ${((on.overlap_lt_1 || 0) * 100).toFixed(1)}%`,
    )
  }

  // ── 结论行：判据是否成立 ──
  console.log("\n判据（间距/尺寸 ≥ 1.5，<1.0 即视场读作填充）：")
  for (const target of sortedTargets) {
    for (const mode of ["legacy", "auto"]) {
      const r = results.find((x) => x.targetK === target && x.mode === mode)
      if (!r) continue
      const ok = r.ratio_p50 >= 1.5
      const note =
        r.overlap_lt_1 > 0.5
          ? "半数以上记号与邻居重叠 —— 视场读作填充"
          : r.below_floor_lt_1_5 > 0.5
            ? "半数以上跌破判据 —— 无重叠余量"
            : "余量充足"
      console.log(
        `  k≈${target.toFixed(2)}  ${mode.padEnd(6)}  p50=${f3(r.ratio_p50)}  ${ok ? "✅" : "❌"}  ${note}`,
      )
    }
  }

  fs.writeFileSync(out, JSON.stringify(results, null, 2))
  console.log(`\nJSON -> ${out}`)
  await browser.close()
})()
