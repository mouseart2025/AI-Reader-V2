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
  let urlSeen = false;
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--hide") hides.push(args[++i]);
    else if (a === "--shot") shot = args[++i];
    // 位置参数按文档头部的顺序来：第一个是 URL，第二个是输出图。
    // 原实现把**任何**不以 `--` 开头的位置参数都写进 `url`，
    // 于是文档里写的 `[URL] [SHOT.png]` 实际会把输出路径当 URL 打开
    // （实测报 `Cannot navigate to invalid URL: /tmp/v3_before.png`）。
    // 文档与行为必须一致，改行为以合文档，而不是改文档以合 bug。
    else if (!a.startsWith("--")) {
      if (urlSeen) shot = a;
      else { url = a; urlSeen = true; }
    }
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
  // ── 语义就绪判据 ──────────────────────────────────────────────
  // 不是"网络空闲"，也不是固定 sleep：等页面自己说它算完了。
  /// 这个判据抄自仓里既有的 `ai-reader-internal/scripts/map-visual-audit/probe_visual.py`
  // 与 `shoot_zoom.py`（两个都带同一个 READY 串）。我今晚先写了 `.location-item` +
  // 固定 2200ms 的版本，比这个糙 —— 固定 sleep 在慢机器上会截到半成品，而那个正是
  // 我今晚栽过的坑（`chrome --screenshot` 拍出的"未渲染完成的地图"，据此得出了一整套
  // 错误结论）。**别再造第二套，直接沿用既有判据。**
  await page
    .waitForFunction(
      () =>
        !!document.querySelector("#viewport") &&
        !document.body.innerText.includes("计算地理坐标") &&
        !document.body.innerText.includes("求解空间布局") &&
        !document.body.innerText.includes("优化布局中") &&
        !document.body.innerText.includes("请稍候"),
      { timeout: 60000 }
    )
    .catch(() => {});
  await page.waitForSelector(".location-item", { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(1200); // 之后只留一个短的稳定窗，不再靠它兜底

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

  // ── 地点标记的屏幕几何 ────────────────────────────────────────
  // V3 那条待办说的是"底板比字形更主导，且地面符号与地点标记同尺寸同重量"。
  // 前一句在代码里有据可查（`r = min(iconSize*0.32, 18)`，字形伸到 `iconSize/2`），
  // 后一句**至今没有任何数**。要判"读者分不分得出两种语言"，可算的是
  // **两者的屏幕尺寸分布重叠多少** —— 同尺寸同重量必然落在同一个尺寸带里。
  // 所以这里把标记侧也取出来：tier、字形盒、底盘直径、标签字号，
  // 全部走 getBoundingClientRect（屏幕像素），与地面符号同一把尺子。
  const marks = await page.evaluate(() => {
    const rbox = (el) => el.getBoundingClientRect();
    const box = (el) => {
      const r = rbox(el);
      return [Math.round(r.width * 10) / 10, Math.round(r.height * 10) / 10];
    };
    const out = [];
    for (const g of document.querySelectorAll("#viewport g[class^='tier-'] > g")) {
      const host = g.closest("g[id^='locations-']");
      const tier = host ? host.id.replace("locations-", "") : "?";
      const icon = g.querySelector(".loc-icon");
      const plate = g.querySelector(".loc-plate");
      const label = g.querySelector(".loc-label");
      if (!icon) continue;
      const [iw, ih] = box(icon);
      const [pw, ph] = plate ? box(plate) : [0, 0];
      const cs = label ? getComputedStyle(label) : null;
      const lr = label ? rbox(label) : null;
      out.push({
        tier,
        icon: [iw, ih],
        plate: [pw, ph],
        plateRect: plate && pw > 0
          ? [rbox(plate).x, rbox(plate).y, pw, ph].map((v) => Math.round(v * 10) / 10)
          : null,
        plateR: plate ? +plate.getAttribute("r") : 0,
        labelPx: cs ? parseFloat(cs.fontSize) : 0,
        labelBox: label ? box(label) : [0, 0],
        labelRect: lr && lr.width > 0
          ? [lr.x, lr.y, lr.width, lr.height].map((v) => Math.round(v * 10) / 10)
          : null,
        labelText: label ? label.textContent.trim().slice(0, 12) : "",
        opacity: +(icon.getAttribute("opacity") ?? 1),
        iconSize: icon.getAttribute("transform") || "",
      });
    }
    const byTier = {};
    for (const m of out) byTier[m.tier] = (byTier[m.tier] || 0) + 1;

    // ── 标签被底板压住多少 ────────────────────────────────────────
    // "hidden" 口径只数 display:none / 宽度 0，而真正的毛病是**画了但被压住** ——
    // 并排图上 `祭赛国`/`天竺国`/`平顶山` 三个标签在带底板时完全读不出来，
    // 去掉底板才露出来，它们在此之前一个都没被记为 hidden。
    // 所以这里算几何遮挡：每个标签的屏幕盒与所有底板盒的重叠面积 ÷ 标签面积。
    const plates = out.filter((m) => m.plateRect).map((m) => m.plateRect);
    const labels = out.filter((m) => m.labelRect);
    let worst = 0;
    const perLabel = [];
    for (const m of labels) {
      const [lx, ly, lw, lh] = m.labelRect;
      const area = Math.max(lw * lh, 1e-6);
      let covered = 0;
      for (const [px, py, pw2, ph2] of plates) {
        const ox = Math.max(0, Math.min(lx + lw, px + pw2) - Math.max(lx, px));
        const oy = Math.max(0, Math.min(ly + lh, py + ph2) - Math.max(ly, py));
        covered += ox * oy;
      }
      const frac = Math.min(covered / area, 1);
      if (frac > worst) worst = frac;
      if (frac > 0.05) perLabel.push({ text: m.labelText, tier: m.tier, frac: +frac.toFixed(2) });
    }
    perLabel.sort((a, b) => b.frac - a.frac);
    return {
      total: out.length, byTier, items: out,
      labelsMeasured: labels.length,
      labelsOverlapped: perLabel.length,
      labelOverlapWorst: +worst.toFixed(2),
      labelOverlapHalf: perLabel.filter((p) => p.frac >= 0.5).length,
      labelOverlapTop: perLabel.slice(0, 12),
    };
  });
  fs.writeFileSync("/tmp/map_marks.json", JSON.stringify(marks));
  console.log(
    `location marks -> /tmp/map_marks.json  共 ${marks.total} 个  ` +
      JSON.stringify(marks.byTier)
  );
  console.log(
    `label occlusion: 量到 ${marks.labelsMeasured} 个标签，被底板压住 ` +
      `${marks.labelsOverlapped} 个（其中压掉一半以上的 ${marks.labelOverlapHalf} 个，` +
      `最严重 ${(marks.labelOverlapWorst * 100).toFixed(0)}%）`
  );
  // ⚠️ 这是**几何**口径（面积相交），它回答的是"框有没有被盖住"，
  // **不回答"盖住之后还读不读得出来"**。两者在底板被改淡之后会分岔：
  // 深色墨底下垫一层 alpha 0.30 的浅雾会**抬高**对比度，不是抹掉它。
  // 实测（2026-09-28，底板改完）：几何口径报 16/47 被压、2 个 100%，
  // 而光学口径（`probe_map_visual.py --label-ink`）报对比度损失中位 0.0%、p90 0.0%，
  // 连那两个"压满 100%"的损失都是 0.0%。我据此差点做了一次不必要的 z 序重构。
  // ⇒ 这个数只用来**定位嫌疑**，定案要用 `--label-ink`。
  console.log(
    "  ⚠️ 几何口径：只说明框被盖住，不等于读不出来。" +
      "定案用 probe_map_visual.py --label-ink（同一框两态比墨迹对比度）"
  );
  if (marks.labelOverlapTop.length) {
    console.log("  最严重的几个: " +
      marks.labelOverlapTop.map((p) => `${p.text}(${p.tier} ${(p.frac * 100).toFixed(0)}%)`).join(" "));
  }

  await page.screenshot({ path: shot });
  console.log("shot -> " + shot);
  if (errors.length) console.log("CONSOLE/REQ ERRORS:\n  " + errors.slice(0, 8).join("\n  "));
  await browser.close();
})();
