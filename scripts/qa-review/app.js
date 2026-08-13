/* Q0 质量数据核对 — 应用逻辑 */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  let novels = [];      // [{slug,title,exists}]
  let current = null;   // {slug,title,...}
  let filtered = [];    // 当前过滤后的 items
  let pos = 0;          // filtered 当前指针
  let history = [];     // 撤销栈 {slug,uid,prev}
  let judgedStack = []; // 最近已判 uid 顺序栈（用于「已判」横条快速返回，去重置顶）
  const HIST_MAX = 30;  // 已判横条保留条数
  let mode = "single";  // single | batch
  let batchList = [];   // 批量模式：当前批未判 items
  let batchFocus = 0;   // 批量模式：当前焦点卡下标
  let loaded = {};      // slug -> {title,items,counts,prefill}（切 novel 缓存）

  // Q3=A：AI 预填（双模型）。DS 来自 .prefill-<slug>.json（m3+m2），QW 来自
  // .prefill-qwen-<slug>.json（仅 m2）；M2 两侧一致判假=「双否」可批量确认。
  // 预填仅参考，需人工按键确认才算判定；#f-status=review 只看需复核项。
  const PREFILL_ENABLED = true;

  const TYPE_LABEL = {
    m3: "M3 · 父子方向",
    m2_qa: "M2 · 地点真伪",
    m2_cal: "M2 · 地点真伪（校准）",
  };

  // ── 网络 ──────────────────────────────────────────────
  async function api(path, opts) {
    const r = opts
      ? await fetch(path, opts)
      : await fetch(path);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ── 载入 ──────────────────────────────────────────────
  async function loadNovels() {
    const d = await api("/api/novels");
    novels = d.novels;
    renderTabs();
  }

  async function loadNovel(slug) {
    if (loaded[slug]) return;
    loaded[slug] = await api("/api/data?novel=" + slug);
  }

  function d0() {
    return current ? loaded[current.slug] : null;
  }

  function prefillFor(it) {
    const d = d0();
    if (!d || !PREFILL_ENABLED || !d.prefill) return null;
    return d.prefill[it.uid] || null;
  }

  // Qwen 第二模型预填（.prefill-qwen-<slug>.json，仅覆盖 M2）
  function prefillQFor(it) {
    const d = d0();
    if (!d || !PREFILL_ENABLED || !d.prefill_qwen) return null;
    return d.prefill_qwen[it.uid] || null;
  }

  // 双模型状态（仅 M2 且 DS/QW 两侧都有预填时）：both-false 双否 / both-true 双真 / disagree 分歧
  function dualState(it) {
    if (!it || it.type === "m3") return null;
    const a = prefillFor(it), b = prefillQFor(it);
    if (!a || !b) return null;
    if (a.value === null || a.value === undefined || b.value === null || b.value === undefined) return null;
    if (a.value === false && b.value === false) return "both-false";
    if (a.value === true && b.value === true) return "both-true";
    return "disagree";
  }

  // 双模型状态徽标（内联样式，免改 style.css）
  function dualBadgeHtml(it) {
    const st = dualState(it);
    if (!st) return "";
    const map = {
      "both-false": ["#1a7f37", "双否", "双模型一致判假（交叉实验准确率 100%），可批量确认为非地点"],
      "both-true": ["#6e7781", "双真", "双模型一致判真（交叉实验准确率约 70%），建议人工复核"],
      "disagree": ["#bf5400", "分歧", "双模型判定不一致，需人工判定"],
    };
    const [bg, txt, title] = map[st];
    return `<span title="${title}" style="background:${bg};color:#fff;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:600;white-space:nowrap">${txt}</span>`;
  }

  // 需复核：无预填（未标）或预填非高置信；未启用预填=都未标需看
  function needsReview(it) {
    const pf = prefillFor(it);
    if (!pf) return it.value === null || it.value === undefined;
    return it.value !== null && it.value !== undefined ? false : pf.confidence !== "high";
  }

  function isJudged(it) {
    return it.value !== null && it.value !== undefined;
  }
  // 批量模式："已处理" = 已判定（有值）或已跳过（M2 按3）
  function isProcessed(it) {
    return isJudged(it) || it._skipped === true;
  }

  // ── 过滤 ──────────────────────────────────────────────
  function applyFilters() {
    if (!current) { filtered = []; pos = 0; return; }
    const d = d0();
    const showM2 = $("#f-m2").checked;
    const showM3 = $("#f-m3").checked;
    const status = $("#f-status").value;
    const conf = $("#f-prefill").value;

    filtered = d.items.filter((it) => {
      const isM2 = it.type !== "m3";
      if ((isM2 && !showM2) || (!isM2 && !showM3)) return false;

      if (status === "unlabeled" && isJudged(it)) return false;
      if (status === "labeled" && !isJudged(it)) return false;
      if (status === "review" && !needsReview(it)) return false;
      if (status === "composite" && !hasIssue(it, "missing_intermediate")) return false;
      if (status === "entity" && !hasIssue(it, "entity_issue")) return false;
      if (status === "alias" && !hasIssue(it, "alias_variant")) return false;
      if (status === "generic" && !hasIssue(it, "generic_attachment")) return false;

      const pf = prefillFor(it);
      if (conf === "low" && (!pf || pf.confidence === "high")) return false;
      if (conf === "high" && (!pf || pf.confidence !== "high")) return false;
      return true;
    });

    if (pos >= filtered.length) pos = 0;
    advance();
  }

  // 前进到下一个未标项（若无则停留在末尾）
  function advance(force) {
    if (!filtered.length) { pos = 0; return; }
    for (let i = pos; i < filtered.length; i++) {
      if (!isJudged(filtered[i])) { pos = i; return; }
    }
    for (let i = 0; i < pos; i++) {
      if (!isJudged(filtered[i])) { pos = i; return; }
    }
    pos = filtered.length - 1;
    if (force) maybeShowDone();
  }

  // ── 渲染 ──────────────────────────────────────────────
  function renderTabs() {
    const tabs = $("#novel-tabs");
    tabs.innerHTML = "";
    novels.forEach((n) => {
      const el = document.createElement("div");
      el.className =
        "novel-tab" +
        (current && current.slug === n.slug ? " active" : "") +
        (n.exists ? "" : " disabled");
      el.textContent = n.title + (n.exists ? "" : " ·无数据");
      el.dataset.slug = n.slug;
      el.addEventListener("click", () => n.exists && selectNovel(n.slug));
      tabs.appendChild(el);
    });
  }

  async function selectNovel(slug) {
    if (!novels.find((n) => n.slug === slug && n.exists)) return;
    await loadNovel(slug);
    current = { slug, title: loaded[slug].title };
    pos = 0; history = []; judgedStack = []; batchList = []; batchFocus = 0;
    seedJudgedStack();
    closeDone();
    renderTabs();
    applyFilters();
    render();
  }

  function topProgress() {
    const d = d0();
    if (!d) return;
    let done = 0;
    d.items.forEach((it) => { if (isJudged(it)) done++; });
    const pct = d.items.length ? Math.round((done / d.items.length) * 100) : 0;
    $("#global-progress").style.width = pct + "%";
    $("#progress-num").textContent = done + "/" + d.items.length;
  }

  function displayValue(it) {
    const v = it.value;
    if (v === null || v === undefined) return "—";
    if (it.type === "m3") return v === "correct" ? "正确" : v === "wrong" ? "错误" : v === "uncertain" ? "不确定" : String(v);
    return v === true ? "真地点" : v === false ? "非地点" : String(v);
  }

  // 复合全称（缺中间层）+ 泛称/非实体 标记：M3 专用，存于 issue_type（'|' 分隔多 token）
  function issueTokens(it) {
    return new Set((it.issue_type || "").split("|").filter(Boolean));
  }
  function hasIssue(it, token) {
    return issueTokens(it).has(token);
  }
  function compFlagHtml(it) {
    if (it.type !== "m3") return "";
    const compChecked = hasIssue(it, "missing_intermediate");
    const entChecked = hasIssue(it, "entity_issue");
    const aliasChecked = hasIssue(it, "alias_variant");
    const compSuggested = it.issue_suggested === true;
    const entSuggested = it.entity_suggested === true;
    const aliasSuggested = it.alias_suggested === true;
    return `
      <div class="comp-flag">
        <label for="comp-toggle">
          <input type="checkbox" id="comp-toggle" ${compChecked ? "checked" : ""} />
          复合全称 / 缺中间层
        </label>
        ${compSuggested ? '<span class="comp-hint">（预筛建议）</span>' : ""}
        <span class="comp-hint" style="margin-left:auto">子为「父+通名」复合专名，缺中间层</span>
      </div>
      <div class="comp-flag ent">
        <label for="ent-toggle">
          <input type="checkbox" id="ent-toggle" ${entChecked ? "checked" : ""} />
          泛称 / 非实体节点
        </label>
        ${entSuggested ? '<span class="comp-hint">（预筛建议）</span>' : ""}
        <span class="comp-hint" style="margin-left:auto">父或子为纯泛称/非独立地名</span>
      </div>
      <div class="comp-flag alias">
        <label for="alias-toggle">
          <input type="checkbox" id="alias-toggle" ${aliasChecked ? "checked" : ""} />
          异名 / 拼写变体
        </label>
        ${aliasSuggested ? '<span class="comp-hint">（预筛建议）</span>' : ""}
        <span class="comp-hint" style="margin-left:auto">同一实体的异写/错别字/简称被拆成两节点</span>
      </div>
    `;
  }

  // M2 通名残留/待挂父级标记：单节点地名，纯泛称建筑/场所，真实存在但单拎非专名
  function genericFlagHtml(it) {
    if (it.type === "m3") return "";
    const checked = hasIssue(it, "generic_attachment");
    const suggested = it.generic_suggested === true;
    return `
      <div class="comp-flag generic">
        <label for="generic-toggle">
          <input type="checkbox" id="generic-toggle" ${checked ? "checked" : ""} />
          通名 / 待挂父级
        </label>
        ${suggested ? '<span class="comp-hint">（预筛建议）</span>' : ""}
        <span class="comp-hint" style="margin-left:auto">纯泛称建筑/场所名，真实存在但单拎非专名，应挂所属父级</span>
      </div>
    `;
  }

  function cardHtml(it) {
    const pf = prefillFor(it);
    const pfq = prefillQFor(it);

    let body;
    if (it.type === "m3") {
      body = `
        <div class="card-relation">
          <span>${esc(it.parent) || "（空）"}</span>
          <span class="arrow">→</span>
          <span>${esc(it.child) || "（空）"}</span>
        </div>
        ${it.rules && it.rules.length ? '<div class="rules">' + it.rules.map((r) => '<span class="rule-tag">' + esc(r) + "</span>").join("") + "</div>" : ""}
        <div class="evidence ${it.evidence ? "" : "empty"}">${it.evidence ? "原文证据：" + esc(it.evidence) : "（无原文证据）"}</div>
        ${compFlagHtml(it)}
      `;
    } else {
      body = `
        <div class="card-name">${esc(it.name) || "（空）"}</div>
        <div class="meta">出现于 ${it.chapters && it.chapters.length ? "第 " + it.chapters.map(esc).join("、") + " 回" : "（无章节记录）"}</div>
      `;
      // 原文上下文：名称加粗高亮，复用 m3 的 evidence 样式
      if (it.evidence_text) {
        let evHtml = esc(it.evidence_text);
        if (it.name && it.evidence_text !== "(原文未找到)")
          evHtml = evHtml.split(esc(it.name)).join("<b>" + esc(it.name) + "</b>");
        body += `<div class="evidence ${it.evidence_text === "(原文未找到)" ? "empty" : ""}">${evHtml}</div>`;
      }
      body += genericFlagHtml(it);   // M2 通名/待挂父级标记
    }

    // 紧凑双模型倾向：M2 显示 DS/QW 两侧 + 双否/双真/分歧徽标；M3 只显示 DS
    const shortVal = (v) => {
      if (it.type === "m3") return v === "correct" ? "对" : v === "wrong" ? "错" : v === "uncertain" ? "?" : "—";
      return v === true ? "真" : v === false ? "假" : "—";
    };
    const shortConf = (c) => (c === "high" ? "高" : c === "medium" ? "中" : "低");
    const modelTag = (label, p) =>
      p && p.value !== null && p.value !== undefined
        ? `<span class="tag ${p.confidence === "high" ? "t-high" : "t-low"}">${label}:${shortVal(p.value)}·${shortConf(p.confidence)}</span>`
        : "";
    const pfHtml =
      PREFILL_ENABLED && (pf || pfq)
        ? `<div class="prefill ${pf && pf.confidence === "high" ? "conf-high" : "conf-low"}">
            ${modelTag("DS", pf)}${it.type !== "m3" ? " " + modelTag("QW", pfq) : ""} ${dualBadgeHtml(it)}
            <span style="margin-left:auto;opacity:.65">仅参考，按 1/2/3 确认</span>
          </div>`
        : "";

    return `
      <div class="type-badge type-${it.type}">${TYPE_LABEL[it.type]}</div>
      ${body}
      ${isJudged(it) ? '<div class="meta" style="margin-top:12px"><b>已判：' + displayValue(it) + "</b></div>" : ""}
      ${pfHtml}
    `;
  }

  // ── 最近已判（快速返回修订）────────────────────────────
  // 载入某部小说时，用已持久化的「已判」项预填横条（按数据出现顺序的逆序近似最近）
  function seedJudgedStack() {
    const d = d0();
    if (!d) return;
    const judged = [];
    // 逆序遍历，后出现的（校准样本）视为较近，但要保持 m3/m2qa/m2cal 组内顺序 =>
    // 简单起见：全量已判项按数组原序取末尾 HIST_MAX（近似最近）
    for (const it of d.items) {
      if (isJudged(it)) judged.push(it.uid);
    }
    // 取末尾 HIST_MAX，保持它们在数据中的相对顺序（点击回看更一致）
    judgedStack = judged.slice(-HIST_MAX);
  }
  function pushJudged(it, slug) {
    judgedStack = judgedStack.filter((u) => u !== it.uid); // 去重（同 uid 移旧）
    judgedStack.push(it.uid);
    if (judgedStack.length > HIST_MAX) {
      judgedStack = judgedStack.slice(judgedStack.length - HIST_MAX);
    }
  }
  function removeJudged(uid) {
    judgedStack = judgedStack.filter((u) => u !== uid);
  }
  function findItem(uid) {
    const d = loaded[current.slug];
    if (!d) return null;
    return d.items.find((x) => x.uid === uid) || null;
  }
  function renderHistory() {
    const log = $("#history-log");
    if (!log) return;
    if (!current || judgedStack.length === 0) {
      log.innerHTML = '<span class="hist-empty" style="color:var(--muted);font-size:11px">暂无</span>';
      return;
    }
    const curUid = filtered[pos] ? filtered[pos].uid : null;
    log.innerHTML = judgedStack.map((uid) => {
      const it = findItem(uid);
      if (!it) return "";
      const label = it.type === "m3"
        ? `${it.parent} → ${it.child}`
        : it.name;
      const vcls = it.type === "m3"
        ? (it.value === "correct" ? "v-ok" : it.value === "wrong" ? "v-bad" : "v-unk")
        : (it.value === true ? "v-ok" : "v-bad");
      const marks = issueMarks(it);
      const cur = uid === curUid ? " current" : "";
      return `<span class="hist-item${cur}" data-uid="${uid}" title="点击回看修订">${label} <span class="dot ${vcls}"></span> ${marks ? `<span class="marks">${marks}</span>` : ""}</span>`;
    }).join("");
    // 绑定点击
    log.querySelectorAll(".hist-item").forEach((el) => {
      el.addEventListener("click", () => jumpTo(el.dataset.uid));
    });
  }
  // 标记符号：各类 issue 的短记号
  function issueMarks(it) {
    let m = "";
    if (hasIssue(it, "missing_intermediate")) m += "缺";
    if (hasIssue(it, "entity_issue")) m += "泛";
    if (hasIssue(it, "alias_variant")) m += "异";
    if (hasIssue(it, "generic_attachment")) m += "通";
    return m;
  }
  // 跳到某条已判项：若不在当前 filter，临时切「全部」
  function jumpTo(uid) {
    if (!current) return;
    let idx = filtered.findIndex((x) => x.uid === uid);
    if (idx < 0) {
      $("#f-status").value = "all";
      applyFilters();
      idx = filtered.findIndex((x) => x.uid === uid);
    }
    if (idx >= 0) {
      pos = idx;
      render();
    }
  }

  // ── 批量模式 ──────────────────────────────────────────
  // 从 filtered 中取前 5 个「未处理」（未判定且未跳过）项作为本批
  function refillBatch() {
    batchList = filtered.filter((x) => !isProcessed(x)).slice(0, 5);
    batchFocus = 0;
  }
  // 本批判完（或点击下一批）→ 取后续未处理项
  function advanceBatch() {
    const remaining = filtered.filter((x) => !isProcessed(x));
    if (!remaining.length) {
      batchList = [];
      batchFocus = 0;
      return;
    }
    batchList = remaining.slice(0, 5);
    batchFocus = 0;
  }
  function batchAllDone() {
    if (!batchList.length) return true;
    return batchList.every((x) => isProcessed(x));
  }
  function setMode(m) {
    mode = m;
    document.querySelectorAll("#mode-toggle .seg-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === m);
    });
    if (m === "batch") {
      refillBatch();
    }
    // flashBtn 在批量模式下无顶栏按钮，直接渲染
    render();
  }
  function renderBatch() {
    const grid = $("#batch-grid");
    if (!grid) return;
    if (!batchList.length || batchAllDone()) {
      // 全部已判完
      maybeShowDone();
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1"><div class="hint">本批已全部完成。<br><button class="btn" id="btn-batch-more">加载下一批 / 或其他筛选</button></div></div>';
      const more = $("#btn-batch-more");
      if (more) more.addEventListener("click", () => { advanceBatch(); renderBatch(); render(); });
      return;
    }
    grid.innerHTML = batchList.map((it, i) => {
      const focus = i === batchFocus ? " focus" : "";
      const processed = isJudged(it) ? " judged" : (it._skipped ? " skipped" : "");
      return miniCardHtml(it, i, focus + processed);
    }).join("");
    const doneInBatch = batchList.filter((x) => isProcessed(x)).length;
    $("#batch-info").textContent =
      `本批 ${doneInBatch}/${batchList.length} 已处理 · 焦点用 ← → 移动，1/2/3 判定` +
      ` · 共剩 ${filtered.filter((x) => !isProcessed(x)).length} 条待标`;
    // 绑定点击：选焦点（排除 checkbox/label/按钮，避免重绘打断控件交互）
    grid.querySelectorAll(".mini-card").forEach((el, i) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("input, label, button, .mini-gen")) return;
        batchFocus = i;
        renderBatch(); render();
      });
    });
    // 绑定迷你判定按钮
    grid.querySelectorAll("[data-mbtn]").forEach((btn) => {
      const key = btn.dataset.mbtn;
      const idx = +btn.dataset.idx;
      const it = batchList[idx];
      if (!it) return;
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        batchFocus = idx;
        judge(key, it, current.slug);
      });
    });
    // 绑定 M2 通名/待挂父级 迷你勾选
    grid.querySelectorAll(".mini-genbox").forEach((box) => {
      box.addEventListener("change", async (e) => {
        e.stopPropagation();
        const idx = +box.dataset.idx;
        const it = batchList[idx];
        if (!it) return;
        const prev = it.issue_type;
        const tokens = issueTokens(it);
        if (box.checked) tokens.add("generic_attachment");
        else tokens.delete("generic_attachment");
        it.issue_type = [...tokens].sort().join("|") || null;
        try {
          await post("/api/annotate", { slug: current.slug, uid: it.uid, field: "issue_type", value: it.issue_type || "" });
        } catch (err) {
          it.issue_type = prev;
          alert("写入失败：" + err.message);
        }
        renderBatch(); render();
      });
    });
  }
  function miniCardHtml(it, i, cls) {
    const pf = prefillFor(it);
    const pfShow = PREFILL_ENABLED && pf
      ? `<span class="mini-pf ${pf.confidence === "high" ? "conf-high" : "conf-low"}">AI ${pf.confidence === "high" ? "高" : pf.confidence}</span>`
      : "";
    const marks = issueMarks(it) ? `<span class="mini-issues">${issueMarks(it)}</span>` : "";
    let main;
    if (it.type === "m3") {
      main = `<div class="mini-relation">${esc(it.parent)} → ${esc(it.child)}</div>`;
    } else {
      main = `<div class="mini-name">${esc(it.name)}</div><div class="mini-meta">第 ${(it.chapters || []).join("、")} 回</div>`;
    }
    // 状态标记
    let stateMark = "";
    if (it.type === "m3" && isJudged(it)) stateMark = '<span class="mini-mark">✓</span>';
    else if (it.type !== "m3" && isJudged(it)) stateMark = '<span class="mini-mark">✓</span>';
    else if (it.type !== "m3" && it._skipped) stateMark = '<span class="mini-mark skip">跳</span>';
    // 三个迷你按钮，标出当前已判项
    function btnCls(k) {
      const v = it.value;
      if (it.type === "m3") {
        if (v === null || v === undefined) return "";
        return (k === "1" && v === "correct") || (k === "2" && v === "wrong") || (k === "3" && v === "uncertain") ? "active-" + k : "";
      }
      if (k === "3") return ""; // M2 的3=跳过，无激活态
      return (k === "1" && v === true) || (k === "2" && v === false) ? "active-" + k : "";
    }
    // M2 通名/待挂父级 迷你勾选
    const genericChk = it.type === "m3"
      ? ""
      : `<label class="mini-gen"><input type="checkbox" class="mini-genbox" data-idx="${i}" ${hasIssue(it, "generic_attachment") ? "checked" : ""} />通名/待挂父级</label>`;
    const b3label = it.type === "m3" ? "3不" : "3跳";
    return `
      <div class="mini-card${cls}" data-idx="${i}">
        ${stateMark}${marks}
        <div class="mini-head"><span class="idx">#${i + 1}</span><span class="type-badge type-${it.type}">${it.type.replace("_", "·")}</span>${pfShow}</div>
        ${main}
        ${genericChk}
        <div class="mini-btns">
          <button class="mini-btn m1 ${btnCls("1")}" data-mbtn="1" data-idx="${i}">1${it.type === "m3" ? "确" : "真"}</button>
          <button class="mini-btn m2 ${btnCls("2")}" data-mbtn="2" data-idx="${i}">2${it.type === "m3" ? "误" : "假"}</button>
          <button class="mini-btn m3 ${btnCls("3")}" data-mbtn="3" data-idx="${i}">${b3label}</button>
        </div>
      </div>
    `;
  }

  // ── 预填浏览模式 ────────────────────────────────────
  function aiValueDisplay(v, type) {
    if (v === null || v === undefined) return "—";
    if (type === "m3") return v === "correct" ? "正确" : v === "wrong" ? "错误" : v === "uncertain" ? "不确定" : String(v);
    return v === true ? "真" : v === false ? "假" : String(v);
  }
  function renderPrefill() {
    const list = $("#prefill-list");
    if (!list) return;
    const d = d0();
    const pf = d && d.prefill ? d.prefill : null;
    if (!pf || !Object.keys(pf).length) {
      list.innerHTML = '<div class="empty"><div class="hint">暂无预填。先在逐卡/批量模式点顶栏「AI 预填」生成。</div></div>';
      $("#pf-summary").textContent = "";
      return;
    }
    const fil = $("#pf-status").value;
    const rows = [];
    for (const it of d.items) {
      const p = pf[it.uid];
      if (!p || p.value === undefined || p.value === null) continue;
      // 筛选
      const conf = p.confidence || "medium";
      if (fil === "high" && conf !== "high") continue;
      if (fil === "low" && conf === "high") continue;
      const judged = isJudged(it);
      let match = null;
      if (judged) match = String(p.value) === String(it.value);
      if (fil === "match" && !(judged && match)) continue;
      if (fil === "diff" && !(judged && !match)) continue;
      const dual = dualState(it);
      if (fil === "both-false" && dual !== "both-false") continue;
      if (fil === "disagree" && dual !== "disagree") continue;

      const label = it.type === "m3" ? `${it.parent} → ${it.child}` : it.name;
      const aiTxt = aiValueDisplay(p.value, it.type);
      const confCls = conf === "high" ? "high" : (conf === "medium" ? "medium" : "low");
      const humanTxt = judged ? aiValueDisplay(it.value, it.type) : "—";
      let state;
      if (!judged) state = '<span class="nohuman">未判</span>';
      else if (match) state = '<span class="ok">✓ 一致</span>';
      else state = '<span class="diff">✗ 不一致</span>';
      // QW 第二模型判定（仅 M2 有）
      const pq = prefillQFor(it);
      let qwHtml;
      if (pq && pq.value !== null && pq.value !== undefined) {
        const qconf = pq.confidence || "medium";
        const qconfCls = qconf === "high" ? "high" : (qconf === "medium" ? "medium" : "low");
        qwHtml = `<span class="pf-ai">QW <span class="pf-conf ${qconfCls}">${qconf}</span><b>${aiValueDisplay(pq.value, it.type)}</b></span>`;
      } else {
        qwHtml = '<span class="pf-ai" style="opacity:.45">QW —</span>';
      }
      // 通名/待挂父级（及 M3 各类标记）状态：来自 issue_type 规则预筛（非 AI 判定）
      const issueMark = it.type === "m3" ? issueMarks(it) : (hasIssue(it, "generic_attachment") ? "通" : "");
      const issueHtml = issueMark
        ? `<span class="pf-issue" title="${it.type === "m3" ? "缺=复合全称 泛=泛称 异=异名" : "通=通名/待挂父级"}">${issueMark}</span>`
        : `<span class="pf-issue none">—</span>`;
      rows.push(`
        <div class="pf-row" data-uid="${it.uid}">
          <span class="pf-name" title="${esc(label)}">${esc(label)} ${issueHtml}</span>
          <span class="pf-ai"><span class="pf-conf ${confCls}">${conf}</span><b>${aiTxt}</b></span>
          ${qwHtml}
          ${dualBadgeHtml(it)}
          <span class="pf-human">人工：${humanTxt}</span>
          <span class="pf-state">${state}</span>
        </div>
      `);
    }
    list.innerHTML = rows.join("") || '<div class="empty"><div class="hint">当前筛选无结果</div></div>';
    const total = d.items.filter((x) => pf[x.uid]).length;
    const judged = d.items.filter((x) => pf[x.uid] && isJudged(x));
    const matchN = judged.filter((x) => String(pf[x.uid].value) === String(x.value)).length;
    let summary =
      `共 ${total} 条预填 · 已人工判定 ${judged.length} 条 · 一致 ${matchN} · 不一致 ${judged.length - matchN}`;
    // 双模型汇总（有 QW 预填时另起一行）
    if (d.prefill_qwen && Object.keys(d.prefill_qwen).length) {
      let nBF = 0, nBT = 0, nDG = 0;
      for (const it of d.items) {
        const st = dualState(it);
        if (st === "both-false") nBF++;
        else if (st === "both-true") nBT++;
        else if (st === "disagree") nDG++;
      }
      summary += `<br>双否 ${nBF} · 双真 ${nBT} · 分歧 ${nDG}`;
      $("#pf-summary").innerHTML = summary;
    } else {
      $("#pf-summary").textContent = summary;
    }
    // 点击行跳回逐卡定位
    list.querySelectorAll(".pf-row").forEach((el) => {
      el.addEventListener("click", () => {
        const uid = el.dataset.uid;
        jumpTo(uid);
        setMode("single");
      });
    });
  }

  function render() {
    if (!current) {
      $("#single-view").classList.remove("hidden");
      $("#batch-view").classList.add("hidden");
      $("#prefill-view").classList.add("hidden");
      $("#card").innerHTML = '<div class="empty"><div class="hint">请从顶栏选择一部小说开始核对。</div></div>';
      $("#judge-bar").style.display = "none";
      const bd0 = $("#btn-batch-dual");
      if (bd0) bd0.disabled = true;
      return;
    }
    topProgress();
    updateStats();
    renderHistory();

    // 批量确认双否按钮：仅当前小说有 QW 预填时可用
    const bd = $("#btn-batch-dual");
    if (bd) {
      const dd = d0();
      bd.disabled = !(dd && dd.prefill_qwen && Object.keys(dd.prefill_qwen).length);
    }

    if (mode === "prefill") {
      $("#single-view").classList.add("hidden");
      $("#batch-view").classList.add("hidden");
      $("#prefill-view").classList.remove("hidden");
      renderPrefill();
      return;
    }
    if (mode === "batch") {
      $("#single-view").classList.add("hidden");
      $("#batch-view").classList.remove("hidden");
      $("#prefill-view").classList.add("hidden");
      if (!batchList.length) refillBatch();
      renderBatch();
      return;
    }

    $("#single-view").classList.remove("hidden");
    $("#batch-view").classList.add("hidden");
    $("#prefill-view").classList.add("hidden");
    const it = filtered[pos];
    if (!it) {
      $("#card").innerHTML = '<div class="empty"><div class="hint">当前筛选下无待核对项。<br><button class="btn" id="btn-reset-filter">重置筛选</button></div></div>';
      $("#judge-bar").style.display = "none";
      const b = $("#btn-reset-filter");
      if (b) b.addEventListener("click", () => { $("#f-status").value = "all"; applyFilters(); render(); });
      return;
    }
    $("#judge-bar").style.display = "flex";

    // 判定按钮文案随类型变化
    const jButtons = $("#judge-bar").querySelectorAll(".judge-btn");
    if (it.type === "m3") {
      jButtons[0].textContent = "1 · 正确";
      jButtons[1].textContent = "2 · 错误";
      jButtons[2].textContent = "3 · 不确定";
    } else {
      jButtons[0].textContent = "1 · 真地点";
      jButtons[1].textContent = "2 · 非地点";
      jButtons[2].textContent = "3 · 跳过";
    }

    const card = $("#card");
    card.className = needsReview(it) && !isJudged(it) ? "need-review" : "";
    card.innerHTML = cardHtml(it);

    // 复合全称 / 泛称 / 异名（M3）+ 通名待挂父级（M2）：任一勾选/取消 → 重算 issue_type
    const boxes = [
      ["#comp-toggle", "missing_intermediate"],
      ["#ent-toggle", "entity_issue"],
      ["#alias-toggle", "alias_variant"],
      ["#generic-toggle", "generic_attachment"],
    ]
      .map(([sel, tok]) => [$(sel), tok])
      .filter(([el]) => el);
    boxes.forEach(([box, tok]) => {
      box.addEventListener("change", async () => {
        const prev = it.issue_type;
        const tokens = issueTokens(it);
        if (box.checked) tokens.add(tok);
        else tokens.delete(tok);
        it.issue_type = [...tokens].sort().join("|") || null;
        try {
          await post("/api/annotate", {
            slug: current.slug, uid: it.uid,
            field: "issue_type",
            value: it.issue_type || "",
          });
        } catch (e) {
          it.issue_type = prev;
          alert("写入失败：" + e.message);
        }
        render();
      });
    });

    $("#stat-current").textContent =
      `当前 ${pos + 1} / ${filtered.length} · ${filtered.filter((x) => isJudged(x)).length} 已标`;
  }

  function updateStats() {
    const d = d0();
    if (!d) return;
    let ok = 0, bad = 0, unk = 0, comp = 0, ent = 0, alias = 0, generic = 0, rem = 0;
    d.items.forEach((it) => {
      if (it.type === "m3") {
        if (hasIssue(it, "missing_intermediate")) comp++;
        if (hasIssue(it, "entity_issue")) ent++;
        if (hasIssue(it, "alias_variant")) alias++;
      } else {
        if (hasIssue(it, "generic_attachment")) generic++;
      }
      if (!isJudged(it)) { rem++; return; }
      if (it.type === "m3") {
        if (it.value === "correct") ok++;
        else if (it.value === "wrong") bad++;
        else if (it.value === "uncertain") unk++;
      } else {
        if (it.value === true) ok++;
        else if (it.value === false) bad++;
      }
    });
    $("#st-ok").textContent = ok;
    $("#st-bad").textContent = bad;
    $("#st-unk").textContent = unk;
    $("#st-comp").textContent = comp;
    $("#st-ent").textContent = ent;
    $("#st-alias").textContent = alias;
    $("#st-generic").textContent = generic;
    $("#st-rem").textContent = rem;
  }

  function maybeShowDone() {
    const d = d0();
    if (!d || !d.items.length) return;
    const all = d.items.every((x) => isJudged(x));
    if (!all) return;
    $("#done-overlay").classList.remove("hidden");
  }

  function closeDone() {
    $("#done-overlay").classList.add("hidden");
  }

  // ── 判定 ──────────────────────────────────────────────
  // 逐卡模式：判当前 filtered[pos]；批量模式：判 batchList[batchFocus]
  async function judge(key, it, slug) {
    if (!it) {
      if (mode === "batch") {
        it = batchList[batchFocus];
        slug = current.slug;
      } else {
        it = filtered[pos];
        slug = current.slug;
      }
    }
    if (!it) return;

    let value, applyWrite = true;
    if (it.type === "m3") {
      value = key === "1" ? "correct" : key === "2" ? "wrong" : "uncertain";
    } else {
      if (key === "1") value = true;
      else if (key === "2") value = false;
      else { applyWrite = false; value = null; /* 3=跳过 */ }
    }

    if (applyWrite) {
      const prev = it.value;
      it.value = value;
      history.push({ slug: current.slug, uid: it.uid, prev });
      pushJudged(it, current.slug);
      try {
        await post("/api/annotate", { slug: current.slug, uid: it.uid, value });
      } catch (e) {
        it.value = prev;
        history.pop();
        removeJudged(it.uid);
        alert("写入失败：" + e.message);
        return;
      }
    }

    if (mode === "batch") {
      // 批量：M2 按3=跳过 → 标记 _skipped；判定/跳过都算「已处理」
      if (!applyWrite && it.type !== "m3") {
        it._skipped = true;
      }
      renderBatch();
      if (batchList.every((x) => isProcessed(x))) {
        // 全判定/跳过 → 换下一批
        advanceBatch();
      }
      render();
      return;
    }

    render();
    advance(true);
    // advance 已可能触发 maybeShowDone；最后再补一次 UI
    render();
  }

  function undo() {
    const rec = history.pop();
    if (!rec) return;
    const d = loaded[rec.slug];
    if (!d) return;
    for (const it of d.items) {
      if (it.uid === rec.uid) { it.value = rec.prev; break; }
    }
    removeJudged(rec.uid);
    if (current && current.slug === rec.slug) {
      for (let i = 0; i < filtered.length; i++) {
        if (filtered[i].uid === rec.uid) { pos = i; break; }
      }
    }
    closeDone();
    render();
  }

  // ── 导出 ──────────────────────────────────────────────
  function doPrefill() {
    const d = d0();
    if (!current) { alert("请先选择一部小说"); return; }
    const btn = $("#btn-prefill");
    if (!confirm(`调 DeepSeek + Qwen 双模型为《${current.title}》生成 AI 预填（M2 带原文证据）？\n（约 ${d.items.length} 条，分批调用。已人工判定的不会被覆盖）`))
      return;
    btn.disabled = true;
    btn.textContent = "生成中…";
    post("/api/prefill", { slug: current.slug, model: "both" })
      .then((r) => {
        const pf = r.prefilled || {};
        const parts = [];
        if (pf.deepseek !== undefined) parts.push(`DeepSeek ${pf.deepseek} 条`);
        if (pf.qwen !== undefined) parts.push(`Qwen ${pf.qwen} 条`);
        let msg = `预填完成：${parts.join("，") || "无"}。`;
        if (r.skipped && r.skipped.length) msg += `\n跳过：${r.skipped.join("；")}`;
        msg += `\nM2 卡片显示 DS/QW 双模型倾向与「双否/双真/分歧」徽标。`;
        alert(msg);
        // 清缓存重载该 novel（带上新 prefill），保留已人工判定
        delete loaded[current.slug];
        return loadNovel(current.slug).then(() => { applyFilters(); render(); });
      })
      .catch((e) => { alert("预填失败：" + e.message); })
      .finally(() => { btn.disabled = false; btn.textContent = "AI 预填"; });
  }

  // 批量确认双否：对「双模型一致判假且尚未人工判定」的 M2 项逐条写回 false
  async function doBatchDualFalse() {
    const d = d0();
    if (!current || !d) { alert("请先选择一部小说"); return; }
    const targets = d.items.filter((it) =>
      it.type !== "m3" && dualState(it) === "both-false" && !isJudged(it));
    if (!targets.length) {
      alert("没有可确认的「双否」项（无 QW 预填，或均已人工判定）。");
      return;
    }
    if (!confirm(`将批量确认 ${targets.length} 条「双否」M2 项为「非地点」？\n（双模型一致判假，交叉实验准确率 100%。已有人工判定的项不会被覆盖）`))
      return;
    const btn = $("#btn-batch-dual");
    btn.disabled = true;
    btn.textContent = "确认中…";
    let okN = 0, failN = 0;
    for (const it of targets) {
      const prev = it.value;
      it.value = false;
      try {
        await post("/api/annotate", { slug: current.slug, uid: it.uid, value: false });
        pushJudged(it, current.slug);
        okN++;
      } catch (e) {
        it.value = prev;
        failN++;
      }
    }
    btn.textContent = "批量确认双否";
    alert(`批量确认双否完成：成功 ${okN} 条，失败 ${failN} 条。`);
    // 清缓存重载该 novel（带上最新人工判定）
    delete loaded[current.slug];
    await loadNovel(current.slug);
    applyFilters();
    render();
  }

  function doExport() {
    const d = d0();
    if (!d) return;
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `qa-review-${current.slug}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
  }

  // ── 事件 ──────────────────────────────────────────────
  function flashBtn(k) {
    const b = $("#judge-bar .judge-btn[data-k='" + k + "']");
    if (!b) return;
    const cls = { 1: "pressed-ok", 2: "pressed-bad", 3: "pressed-unk" }[k];
    b.classList.add(cls);
    setTimeout(() => b.classList.remove(cls), 150);
  }

  function init() {
    [1, 2, 3].forEach((k) => {
      $("#judge-bar .judge-btn[data-k='" + k + "']").addEventListener("click", () => {
        flashBtn(k); judge(String(k));
      });
    });
    $("#btn-undo").addEventListener("click", undo);
    $("#btn-export").addEventListener("click", doExport);
    $("#btn-prefill").addEventListener("click", doPrefill);
    $("#btn-batch-dual").addEventListener("click", doBatchDualFalse);
    $("#btn-refresh").addEventListener("click", async () => {
      loaded = {};
      if (current) {
        await loadNovel(current.slug);
        selectNovel(current.slug);
      }
    });
    $("#btn-done-close").addEventListener("click", closeDone);
    $("#ref-toggle").addEventListener("click", () => $("#ref-panel").classList.toggle("collapsed"));
    ["f-m2", "f-m3", "f-status", "f-prefill"].forEach((id) => {
      $("#" + id).addEventListener("change", () => { pos = 0; applyFilters(); render(); });
    });
    $("#pf-status").addEventListener("change", () => { if (mode === "prefill") renderPrefill(); });
    $("#done-overlay").addEventListener("click", (e) => { if (e.target === $("#done-overlay")) closeDone(); });
    $("#btn-history-clear").addEventListener("click", () => { judgedStack = []; render(); });
    // 模式切换
    document.querySelectorAll("#mode-toggle .seg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        setMode(b.dataset.mode);
      });
    });
    // 下一批
    $("#btn-batch-next").addEventListener("click", () => { advanceBatch(); render(); });

    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;

      // 批量模式：←/→/Tab 移动焦点
      if (mode === "batch") {
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          if (batchList.length) { batchFocus = (batchFocus - 1 + batchList.length) % batchList.length; renderBatch(); render(); e.preventDefault(); }
          return;
        }
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          if (batchList.length) { batchFocus = (batchFocus + 1) % batchList.length; renderBatch(); render(); e.preventDefault(); }
          return;
        }
        if (e.key === "Tab") {
          const n = e.shiftKey ? -1 : 1;
          if (batchList.length) { batchFocus = (batchFocus + n + batchList.length) % batchList.length; renderBatch(); render(); e.preventDefault(); }
          return;
        }
      }

      if (e.key === "1" || e.key === "2" || e.key === "3") {
        e.preventDefault();
        flashBtn(e.key); judge(e.key);
      } else if (e.key === "ArrowLeft" || e.key === "Backspace") {
        e.preventDefault(); undo();
      } else if (e.key === "j" || e.key === "J") {
        if (pos < filtered.length - 1) { pos++; render(); }
      } else if (e.key === "k" || e.key === "K") {
        if (pos > 0) { pos--; render(); }
      } else if (e.key === "Escape") {
        closeDone();
      }
    });

    loadNovels();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
