#!/usr/bin/env node
/**
 * 地图 DOM 体检：读**运行时**的真实屏幕尺寸，而不是读规格常量。
 *
 * 为什么必须有它：`TIER_ICON_SIZE` / `TIER_TEXT_SIZE` 是 canvas 单位，真实屏幕尺寸
 * 还要过一遍"反缩放"（`scale(1/k)`）。只看常量会得出完全错误的结论 —— 我一开始
 * 就据此以为"标记 8–14px"，而 8× 截图给出的是 2–3px。两者差一个 k。
 *
 * 用法（需要 playwright-core；chromium 复用 ~/Library/Caches/ms-playwright）：
 *   PLAYWRIGHT_CORE=<dir>/node_modules/playwright-core \
 *   node scripts/probe_map_dom.cjs [URL] [SHOT.png]
 *
 * 报出：
 *   - 地图当前 zoom 变换
 *   - 每个 tier 的 `.loc-icon` 屏幕包围盒（视觉尺寸，非属性值）
 *   - 每个 tier 的 `.loc-label` 计算后的 font-size 与屏幕包围盒
 *   - `.location-item` 上有多少个带反缩放 transform（反缩放是否真的生效）
 *   - 控制台错误（渲染失败的常见原因：icon fetch 404）
 */

const fs = require("fs");
const path = require("path");

const DEFAULT_URL =
  "http://127.0.0.1:5173/map/3b2ef56c-1a55-466a-a7d1-34272446a198";

function loadPlaywright() {
  const tries = [
    process.env.PLAYWRIGHT_CORE,
    process.env.NODE_PATH ? path.join(process.env.NODE_PATH, "playwright-core") : null,
    "/Users/leonfeng/.workbuddy/binaries/node/workspace/node_modules/playwright-core",
    "playwright-core",
  ].filter(Boolean);
  for (const t of tries) {
    try {
      return require(t);
    } catch (_) {
      /* try next */
    }
  }
  throw new Error(
    "找不到 playwright-core。装法：cd <node workspace> && npm i playwright-core，并用 PLAYWRIGHT_CORE 指过去。"
  );
}

function findChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  const root = path.join(
    process.env.HOME,
    "Library/Caches/ms-playwright"
  );
  if (!fs.existsSync(root)) return undefined;
  for (const d of fs.readdirSync(root)) {
    if (!d.startsWith("chromium-")) continue;
    const exe = path.join(
      root,
      d,
      "chrome-mac-arm64",
      "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    );
    if (fs.existsSync(exe)) return exe;
  }
  return undefined; // 交给 playwright 自己找
}

(async () => {
  const { chromium } = loadPlaywright();
  const args = process.argv.slice(2);
  const hides = [];
  let url = DEFAULT_URL;
  let shot = "/tmp/map_pw.png";
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--hide") hides.push(args[++i]);
    else if (a === "--shot") shot = args[++i];
    else if (!a.startsWith("--")) url = a;
  }

  const browser = await chromium.launch({
    executablePath: findChromium(),
    headless: true,
  });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  });
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text().slice(0, 200));
  });
  page.on("requestfailed", (r) => errors.push("REQ FAIL " + r.url().slice(0, 120)));

  await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
  await page
    .waitForSelector(".location-item", { timeout: 30000 })
    .catch(() => {});
  await page.waitForTimeout(2500); // 让反缩放 effect 与标签碰撞求解跑完

  const report = await page.evaluate(() => {
    const items = Array.from(document.querySelectorAll(".location-item"));
    const byTier = {};
    const icon = {};
    const label = {};
    let withCounterScale = 0;
    let labelHidden = 0;

    const push = (obj, tier, v) => (obj[tier] = obj[tier] || []).push(v);

    for (const it of items) {
      const tier = it.getAttribute("data-tier") || "?";
      byTier[tier] = (byTier[tier] || 0) + 1;
      const t = it.getAttribute("transform") || "";
      if (t.includes("scale(")) withCounterScale++;

      const ic = it.querySelector(".loc-icon");
      if (ic) {
        const r = ic.getBoundingClientRect();
        push(icon, tier, [+r.width.toFixed(1), +r.height.toFixed(1)]);
      }
      const lb = it.querySelector(".loc-label");
      if (lb) {
        const cs = getComputedStyle(lb);
        const r = lb.getBoundingClientRect();
        if (cs.display === "none" || cs.visibility === "hidden" || r.width === 0) {
          labelHidden++;
        }
        push(label, tier, [
          cs.fontSize,
          +r.width.toFixed(1),
          +r.height.toFixed(1),
          lb.getAttribute("opacity"),
        ]);
      }
    }

    // 地图内容的 zoom 变换：取带 scale 的祖先 g
    let zoom = null;
    const svg = document.querySelector("svg");
    if (svg) {
      for (const g of svg.querySelectorAll("g")) {
        const t = g.getAttribute("transform");
        if (t && t.includes("scale(")) {
          zoom = t;
          break;
        }
      }
    }

    const stat = (arr) => {
      if (!arr || !arr.length) return null;
      const w = arr.map((a) => a[0]).sort((x, y) => x - y);
      return {
        n: arr.length,
        min: w[0],
        median: w[Math.floor(w.length / 2)],
        max: w[w.length - 1],
        sample: arr[0],
      };
    };
    const out = {};
    for (const k of Object.keys(byTier)) {
      out[k] = {
        count: byTier[k],
        iconScreenW: stat(icon[k]),
        labelSize: label[k] ? label[k][0] : null,
        labelScreenW: stat(label[k]),
      };
    }

    // 精确屏幕坐标：别再靠"猜裁切框"找元素 —— 这一步踩过两次坑
    // （把区域弧线标签当成地点标签，得出"标签不可读"的错误结论）。
    const boxes = [];
    const rp = document.querySelector(".map-viewport") || document.body;
    const pr = rp.getBoundingClientRect();
    for (const it of items.slice(0, 40)) {
      const ic = it.querySelector(".loc-icon");
      const lb = it.querySelector(".loc-label");
      if (!ic) continue;
      const ir = ic.getBoundingClientRect();
      const lr = lb ? lb.getBoundingClientRect() : null;
      const cs = lb ? getComputedStyle(lb) : null;
      boxes.push({
        name: it.getAttribute("data-name"),
        tier: it.getAttribute("data-tier"),
        icon: [+ir.x.toFixed(0), +ir.y.toFixed(0), +ir.width.toFixed(1), +ir.height.toFixed(1)],
        label: lr ? [+lr.x.toFixed(0), +lr.y.toFixed(0), +lr.width.toFixed(1), +lr.height.toFixed(1)] : null,
        labelVisible: lr ? !(cs.display === "none" || cs.visibility === "hidden" || lr.width === 0) : false,
        labelFont: cs ? cs.fontSize : null,
      });
    }
    return {
      locationItems: items.length,
      withCounterScale,
      labelHidden,
      zoomTransform: zoom,
      viewportRect: [+pr.x.toFixed(0), +pr.y.toFixed(0), +pr.width.toFixed(0), +pr.height.toFixed(0)],
      byTier: out,
      boxes,
    };
  });

  console.log(JSON.stringify(report, null, 1));

  // 视口变换：屏幕↔画布 的映射。任何"把屏幕上的符号位置与画布坐标的量对起来"
  // 的分析都需要它，而 `#viewport` 是它的权威来源（`__probe` 里试过找第一个 svg，
  // 那是 UI 图标）。没有它就只能靠猜缩放倍数 —— 我猜错过一次（0.11 vs 实际 0.18）。
  const vp = await page.evaluate(() => {
    const g = document.querySelector("#viewport");
    return g ? g.getAttribute("transform") : null;
  });
  console.log("viewportTransform: " + vp);

  // ── 真实陆地掩膜 ──────────────────────────────────────────────
  // 按颜色冷暖分陆海在这张图上**不成立**：陆地自己的羊皮纸污渍带有冷色斑块，
  // 会被判成海（实测因此把 ΔL 算成 17.3「陆海偏糊」）。改用真正的地形来源 ——
  // `#coastline-ocean` 的路径是「整幅矩形 + 海岸线作洞 + evenodd」，
  // 它的 `isPointInFill` 就是权威的"这是海吗"。
  const mask = await page.evaluate(() => {
    const ocean = document.querySelector("#coastline-ocean path");
    if (!ocean) return null;
    const ctm = ocean.getScreenCTM();
    if (!ctm) return null;
    const inv = ctm.inverse();
    // ⚠️ 用**海洋路径自己的**包围盒，不要用第一个 `svg`：
    // 页面里还有 UI 图标的小 svg，选中它会让采样网格缩成 2x2（实测踩到）。
    const r = ocean.getBoundingClientRect();
    const W = Math.min(window.innerWidth, r.x + r.width);
    const H = Math.min(window.innerHeight, r.y + r.height);
    const x0 = Math.max(0, r.x);
    const y0 = Math.max(0, r.y);
    const step = 8;
    const cols = Math.floor((W - x0) / step);
    const rows = Math.floor((H - y0) / step);
    if (cols < 10 || rows < 10) return null; // 明显选错了元素
    const out = [];
    for (let j = 0; j < rows; j++) {
      let s = "";
      for (let i = 0; i < cols; i++) {
        const x = x0 + i * step + step / 2;
        const y = y0 + j * step + step / 2;
        s += ocean.isPointInFill(new DOMPoint(x, y).matrixTransform(inv)) ? "1" : "0";
      }
      out.push(s);
    }
    return { x: x0, y: y0, step, cols, rows, sea: out };
  });
  if (mask) {
    fs.writeFileSync("/tmp/map_mask.json", JSON.stringify(mask));
    console.log(`landmask -> /tmp/map_mask.json  ${mask.cols}x${mask.rows} @${mask.step}px`);
  } else {
    console.log("landmask: UNAVAILABLE (#coastline-ocean path 未找到)");
  }

  // ── 图层隔离 ─────────────────────────────────────────────
  // "这个视觉问题是谁画的" 不能靠猜：逐个把可疑图层 `display:none` 再拍。
  // 这一条是被反复坑出来的 —— 我两次把区域弧线标签当成地点标签下结论。
  for (const sel of hides) {
    const n = await page.evaluate((s) => {
      const els = document.querySelectorAll(s);
      els.forEach((e) => (e.style.display = "none"));
      return els.length;
    }, sel);
    console.log(`hide ${sel} -> ${n} 个元素`);
  }

  // ── 地面符号分布 ──────────────────────────────────────────────
  // "读得出地貌"不是靠底下的洗层亮度，而是靠**符号分布**：看到一片山脊符号
  // 才知道这里是山。所以要把 `#terrain` 里每个 <use> 的类别与屏幕位置拿出来，
  // 交给 Python 侧算"地域纯度 / 连片度 / 密度"。
  // 类别直接从符号 id 反解（约定 `terrain-<category>-<n>`，见 CATEGORY_SYMBOLS）。
  const symbols = await page.evaluate(() => {
    const out = [];
    const byCat = {};
    for (const u of document.querySelectorAll("#terrain use")) {
      const href = u.getAttribute("href") || u.getAttribute("xlink:href") || "";
      const m = /terrain-([a-z]+)-\d+/.exec(href);
      const cat = m ? m[1] : "unknown";
      const r = u.getBoundingClientRect();
      byCat[cat] = (byCat[cat] || 0) + 1;
      out.push([
        cat,
        Math.round(r.x + r.width / 2),
        Math.round(r.y + r.height / 2),
        +Math.max(r.width, r.height).toFixed(1),   // 渲染后的符号尺寸
      ]);
    }
    return { total: out.length, byCat, items: out };
  });
  fs.writeFileSync("/tmp/map_symbols.json", JSON.stringify(symbols));
  console.log(
    `ground symbols -> /tmp/map_symbols.json  共 ${symbols.total} 个  ` +
      JSON.stringify(symbols.byCat)
  );

  await page.screenshot({ path: shot });
  console.log("shot -> " + shot);
  if (errors.length) console.log("CONSOLE/REQ ERRORS:\n  " + errors.slice(0, 8).join("\n  "));
  await browser.close();
})();
