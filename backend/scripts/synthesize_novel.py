"""Generate a contamination-free Chinese xianxia novel using DeepSeek V3.

Two-phase pipeline:
  Phase 1: one LLM call → world bible + 32-40 locations (hierarchy tree)
           + 12-15 characters (with aliases) + 15+ relationships
           + 30-chapter outline, all as a single JSON.
  Phase 2: 30 LLM calls → one chapter (2500-3500 char) per call, using
           Phase 1 JSON + running prior-chapter summaries as context.

Output layout (under --out-dir, default
  paper/evaluation/contamination-free/novel):

    phase1_bible.json            structured world
    phase1_raw.txt               raw LLM response
    phase1_validation.md         human-readable audit report
    chapters/
        ch01.txt                 one chapter per file
        ...
    metadata.json                tokens + cost log (append-only)
    full_novel.txt               (after --concat) stitched TXT ready to
                                 import into AI Reader

Usage:
    # Step 1 — generate world bible + outline
    uv run python scripts/synthesize_novel.py --phase 1

    # Step 2 — audit Phase 1 structure (no API call)
    uv run python scripts/synthesize_novel.py --validate

    # Step 3 — produce 30 chapters (resumable, skips already-written ones)
    uv run python scripts/synthesize_novel.py --phase 2

    # Step 3b — produce just a subset
    uv run python scripts/synthesize_novel.py --phase 2 --chapters 1-3

    # Step 4 — stitch ch*.txt into full_novel.txt
    uv run python scripts/synthesize_novel.py --concat

Prerequisites:
    DEEPSEEK_API_KEY in backend/.env (not shell — we load .env first).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

DEFAULT_OUT = Path(
    "/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/contamination-free/novel"
)

MODEL = "deepseek-chat"  # DeepSeek V3 (points to latest V3 snapshot)
BASE_URL = "https://api.deepseek.com/v1"

# DeepSeek V3 pricing (2025): $0.27/MTok input (cache miss), $1.10/MTok output
PRICE_INPUT = 0.27 / 1_000_000
PRICE_OUTPUT = 1.10 / 1_000_000

TARGET_LOCATION_COUNT = (32, 45)
TARGET_CHARACTER_COUNT = (12, 15)
TARGET_RELATIONS_MIN = 15
TARGET_CHAPTER_COUNT = 30
TARGET_MAIN_CHAR_ALIAS_MIN = 4
TARGET_CHAPTER_CHARS = (2500, 3500)

REQUIRED_CATEGORIES = {"family", "intimate", "hierarchical", "social", "hostile", "other"}
CATEGORY_MIN = {
    "family": 3,
    "intimate": 2,
    "hierarchical": 3,
    "social": 2,
    "hostile": 2,
    "other": 2,
}

FORBIDDEN_NAMES = {
    # 已有仙侠/武侠
    "少林", "武当", "青城", "华山", "全真", "蜀山", "诛仙", "昆仑", "天山",
    "青云", "万剑一", "张三丰", "令狐冲", "韩立", "陈长生", "萧炎", "唐三",
    "东方不败", "岳不群", "李寻欢", "郭靖", "黄蓉", "小龙女", "乔峰",
    # 神话/古典
    "孙悟空", "猪八戒", "沙僧", "唐僧", "观音", "玉帝", "哪吒", "太上老君",
    "贾宝玉", "林黛玉", "薛宝钗", "王熙凤", "贾母",
    # 真实朝代
    "唐朝", "宋朝", "明朝", "清朝", "秦朝", "汉朝", "周朝", "商朝", "大唐", "大宋",
    # 真实地名
    "长安", "洛阳", "苏州", "杭州", "泰山", "峨眉", "终南山", "五台山", "九华山",
    "普陀", "崆峒", "青城山", "武当山", "华山",
}


# =============================================================================
# Phase 1 prompt
# =============================================================================

PHASE1_SYSTEM = "你是一位资深的中文仙侠小说策划师,擅长构建完整自洽的世界设定。你会严格按照用户给出的格式约束输出。"

PHASE1_USER = """你正在为 AI 研究者生成一部【仙侠长篇小说】的完整设定与章节大纲。该小说将用作中文自然语言处理评测数据(contamination-free 测试),LLM 训练数据里不应存在此书。因此所有专名(人名、地名、门派、法宝、功法)必须由你完全虚构,禁止使用任何现实或已有小说中的名字。

# 一、独创性硬约束(若违反,整个任务作废)
1. 禁用以下类型的名字:
   - 已有仙侠/武侠小说专名:少林、武当、青城、华山、全真、蜀山、诛仙、昆仑、天山、青云、万剑一、张三丰、令狐冲、韩立、陈长生、萧炎、唐三、东方不败、岳不群等等
   - 神话传说人物:孙悟空、猪八戒、观音、玉帝、哪吒、太上老君等
   - 真实朝代名:唐/宋/明/清/秦/汉/周/商(架空朝代可用)
   - 真实地名:长安、洛阳、苏州、泰山、峨眉、终南山、五台山、九华山等
2. 所有专名自造。自造时可借用汉字组合,但不能拼成上述任何已知名字。

# 二、世界与地理结构要求
构建 **32-40 个具名地点**,形成 5-6 层的明确包含树(instance-of hierarchy):

| 层级 | tier 值 | 数量 | 命名后缀示例 |
|---|---|---|---|
| 世界 | world | 1 | 界/天 |
| 大陆/洲 | continent | 3-4 | 洲/域/荒 |
| 国家/王国 | kingdom | 4-5 | 国/王国 |
| 城镇 | city | 5-7 | 城/镇/关 |
| 门派/山/谷(site) | site | 8-10 | 派/宗/山/谷/洞天 |
| 殿阁房间(building) | building | 8-10 | 殿/阁/轩/院/堂 |

规则:
- 每个地点必须指定唯一 parent(顶级世界除外,parent=null)
- 至少 2 个地点出现在**多个章节**,不同章节对其 parent 的表述可略有差异(测投票聚合)
- 至少 3 处地名后缀组合体现层级对比(如"X 洞天"是 site,"X 殿"是其内部 building)

# 三、人物要求(12-15 个具名角色)
- **主角 1 人**:必须有 ≥ 4 个不同称呼(本名 + 字 + 道号 + 绰号/外号),四个名字字面彼此之间不应完全包含(如"沈啸天"和"啸天"属于包含关系,这种只算一个变体;需要 4 个互不包含的变体)
- **关键配角 6-8 人**:每人至少 1 个别名或尊称
- **次要人物 4-6 人**:本名即可

关系网至少 15 对,必须覆盖 6 大类:
| 大类(英文代码)| 中文关系示例 | 最少对数 |
|---|---|---|
| family | 父子/母女/兄妹/夫妻/叔侄 | 3 |
| intimate | 挚友/恋人/结拜兄弟 | 2 |
| hierarchical | 师徒/君臣/主仆/同门 | 3 |
| social | 朋友/同僚/邻人 | 2 |
| hostile | 宿敌/仇家/对手 | 2 |
| other | 过客/一面之交 | 2 |

# 四、章节大纲(30 章)
每章标题 + 150 字情节摘要 + 主要登场人物 + 发生地点(必须出自地点列表)。
情节弧:1-5 章入门立世 → 6-12 章历练 → 13-18 章奇遇 → 19-24 章冲突 → 25-30 章决战收束。

# 五、输出格式
严格返回**单个 JSON 对象**,schema 如下:

{
  "novel_title": "书名",
  "genre": "仙侠",
  "world_bible": {
    "setting_summary": "≤200 字世界观",
    "power_system": "≤120 字能量/修炼体系(自造,不用已有的练气/筑基/金丹 名称)"
  },
  "locations": [
    {"name": "九曜天", "tier": "world", "parent": null, "note": "..."},
    {"name": "东明洲", "tier": "continent", "parent": "九曜天", "note": "..."}
  ],
  "characters": [
    {
      "canonical_name": "沈啸天",
      "aliases": ["沈少侠", "啸天子", "青鸾剑客", "独孤啸"],
      "role": "主角",
      "description": "..."
    }
  ],
  "relationships": [
    {"person_a": "沈啸天", "person_b": "柳若烟", "type": "夫妻", "category": "family"}
  ],
  "chapter_outline": [
    {
      "chapter": 1,
      "title": "青鸾初鸣",
      "setting": "青鸾山·青鸾派·诵经殿",
      "plot_summary": "...(150 字)",
      "key_characters": ["沈啸天", "苍玄子"],
      "new_locations": ["青鸾山", "青鸾派", "诵经殿"]
    }
  ]
}

# 六、自检(在脑中过一遍再输出)
1. 地点树 5-6 层、无环、每个非 root 都有父?
2. 主角 4 个别名都明显不同字面(非子串包含)?
3. 15 对关系覆盖 6 大类且各自达到最少对数?
4. 30 章情节弧线连续、每章 setting 都在 locations 列表里?
5. 所有专名都未与现实/已有小说重名?

通过后,直接输出 JSON(不加 markdown 代码块,不加解释文字)。
"""

# =============================================================================
# Phase 2 prompt (template)
# =============================================================================

PHASE2_SYSTEM = "你是一位仙侠小说作者,严格遵循用户给出的世界设定和本章大纲,专注写出高质量章节正文。"

PHASE2_USER_TEMPLATE = """你正在续写一部原创仙侠长篇小说的第 {N} 章。严格遵守以下设定。

# 一、小说全局设定(JSON)

```json
{WORLD_BIBLE}
```

# 二、前 {PREV_N} 章情节累计摘要

{PREV_SUMMARY}

# 三、前一章末尾(保持衔接)

{PREV_TAIL}

# 四、本章大纲
- 章节号:第 {N} 章
- 标题:{TITLE}
- 发生地点:{SETTING}
- 主要人物:{KEY_CHARS}
- 本章新增地点:{NEW_LOCS}
- 情节摘要:{PLOT_SUMMARY}

# 五、本章写作硬约束(每一条必须达标)

1. **字数**:{MIN_CHARS}-{MAX_CHARS} 中文字符(不含标点)
2. **人物别名变换**:主角出场不少于 3 次,每次使用**不同称呼**(从 aliases 中轮换);重要配角至少 2 种称呼
3. **地理明示**:每次场景切换必须明写地点全名,并至少一处写清父级归属(如:"青鸾山中的诵经殿内")
4. **空间关系显式**:正文必须包含至少一处下列表述之一:
   * "X 位于 Y 之中"、"X 在 Y 内"、"X 坐落于 Y"
   * "X 与 Y 毗邻"、"X 与 Y 一水之隔"
   * "X 下辖 Y"、"Y 属 X"
5. **关系事件推进**:至少一起关系变化(结识/拜师/立誓/结怨/和解/决裂),用动作+对白呈现
6. **禁止抄袭**:不逐字复制前章;不引用现实或已知小说的诗句、招式、功法名(如需诗句请自造)
7. **语言风格**:传统仙侠语境,避免"淦""破防""真的绷不住"等现代网络用语

# 六、输出

第一行:章节标题,格式为 "第 {N} 章  {TITLE}"
空一行
正文(2500-3500 字)

不要 markdown、不要注释、不要额外元数据。通过自检后直接输出。
"""


# =============================================================================
# Core orchestration
# =============================================================================

class DeepSeekClient:
    """Minimal synchronous DeepSeek client using httpx (no new deps)."""

    def __init__(self, api_key: str):
        import httpx

        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=300.0,
        )

    def chat(self, *, messages: list[dict], temperature: float, max_tokens: int,
             response_format: dict | None = None, max_retries: int = 4) -> dict:
        """Return dict with keys: content, prompt_tokens, completion_tokens.

        Retries on transient network errors (incomplete read, timeout, 5xx) with
        exponential backoff: 5s, 15s, 45s, 135s.
        """
        import httpx

        payload: dict = {
            "model": MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = self.client.post("/chat/completions", json=payload)
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout,
                    httpx.ConnectError, httpx.PoolTimeout) as e:
                last_err = e
                if attempt < max_retries:
                    wait = 5 * (3 ** attempt)
                    print(f"  [retry {attempt+1}/{max_retries}] {type(e).__name__}: {e}; sleep {wait}s")
                    time.sleep(wait)
                    continue
                raise

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "content": data["choices"][0]["message"]["content"] or "",
                    "prompt_tokens": data["usage"]["prompt_tokens"],
                    "completion_tokens": data["usage"]["completion_tokens"],
                }
            # Retry on 429 (rate limit) and 5xx
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                wait = 5 * (3 ** attempt)
                print(f"  [retry {attempt+1}/{max_retries}] HTTP {resp.status_code}; sleep {wait}s")
                time.sleep(wait)
                continue
            raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")

        # Should not reach here
        raise RuntimeError(f"DeepSeek exhausted {max_retries} retries: {last_err}")


def build_client() -> DeepSeekClient:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        sys.exit(
            "ERROR: DEEPSEEK_API_KEY missing.\n"
            "Add to backend/.env:\n"
            "    DEEPSEEK_API_KEY=sk-..."
        )
    return DeepSeekClient(api_key)


def append_metadata(out_dir: Path, event: dict):
    meta_path = out_dir / "metadata.json"
    log: list[dict] = []
    if meta_path.exists():
        try:
            log = json.loads(meta_path.read_text())
        except Exception:
            log = []
    event["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log.append(event)
    meta_path.write_text(json.dumps(log, ensure_ascii=False, indent=2))


def parse_json_lenient(text: str) -> dict | None:
    """Best-effort JSON parse — strips markdown fences, trims prose."""
    t = text.strip()
    # Strip ```json ... ``` or ``` ... ```
    if "```json" in t:
        t = t.split("```json", 1)[1].split("```", 1)[0]
    elif t.startswith("```"):
        t = t.split("```", 1)[1].split("```", 1)[0]
    # Trim to outermost { ... }
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        t = t[start : end + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


# =============================================================================
# Phase 1
# =============================================================================

def run_phase1(out_dir: Path, temperature: float = 0.8) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    bible_path = out_dir / "phase1_bible.json"
    raw_path = out_dir / "phase1_raw.txt"

    if bible_path.exists():
        print(f"[phase1] {bible_path} exists; overwrite? (y/N) ", end="")
        ans = input().strip().lower()
        if ans != "y":
            print("[phase1] skipped.")
            return

    client = build_client()

    print(f"[phase1] calling {MODEL} (temperature={temperature})...")
    t0 = time.time()
    resp = client.chat(
        messages=[
            {"role": "system", "content": PHASE1_SYSTEM},
            {"role": "user", "content": PHASE1_USER},
        ],
        temperature=temperature,
        max_tokens=8192,
        response_format={"type": "json_object"},
    )
    elapsed = time.time() - t0

    content = resp["content"]
    in_tok = resp["prompt_tokens"]
    out_tok = resp["completion_tokens"]
    cost = in_tok * PRICE_INPUT + out_tok * PRICE_OUTPUT
    print(f"[phase1] done in {elapsed:.1f}s | input={in_tok} output={out_tok} ≈${cost:.4f}")

    raw_path.write_text(content)
    parsed = parse_json_lenient(content)
    if parsed is None:
        print("[phase1] ⚠ failed to parse JSON; see phase1_raw.txt")
        append_metadata(
            out_dir,
            {"event": "phase1_parse_error", "tokens": {"in": in_tok, "out": out_tok}, "cost": cost},
        )
        return

    bible_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2))
    append_metadata(
        out_dir,
        {
            "event": "phase1_generated",
            "model": MODEL,
            "temperature": temperature,
            "elapsed_s": round(elapsed, 1),
            "tokens": {"in": in_tok, "out": out_tok},
            "cost_usd": round(cost, 4),
        },
    )
    print(f"[phase1] saved: {bible_path}")
    print("[phase1] next: uv run python scripts/synthesize_novel.py --validate")


# =============================================================================
# Validation
# =============================================================================

def validate(out_dir: Path) -> bool:
    """Audit the Phase 1 JSON. Returns True iff all hard checks pass."""
    bible_path = out_dir / "phase1_bible.json"
    if not bible_path.exists():
        print(f"[validate] ERROR: {bible_path} not found")
        return False

    data = json.loads(bible_path.read_text())
    report_lines: list[str] = []
    failures = 0
    warnings = 0

    def rec(ok: bool, msg: str, warn: bool = False) -> None:
        nonlocal failures, warnings
        if ok:
            report_lines.append(f"- [x] {msg}")
        elif warn:
            report_lines.append(f"- [!] WARN: {msg}")
            warnings += 1
        else:
            report_lines.append(f"- [ ] FAIL: {msg}")
            failures += 1

    title = data.get("novel_title") or "?"
    report_lines.append(f"# Phase 1 Validation — 《{title}》\n")

    # --- Locations ---
    locs = data.get("locations") or []
    loc_names = {L.get("name") for L in locs if L.get("name")}
    report_lines.append("## Locations")
    rec(
        TARGET_LOCATION_COUNT[0] <= len(locs) <= TARGET_LOCATION_COUNT[1],
        f"count {len(locs)} in {TARGET_LOCATION_COUNT}",
    )

    tier_counts = Counter(L.get("tier") for L in locs)
    for tier, (mn, mx) in {
        "world": (1, 1),
        "continent": (3, 4),
        "kingdom": (4, 5),
        "city": (5, 8),
        "site": (8, 12),
        "building": (8, 15),  # relaxed: extra buildings help eval, not hurt
    }.items():
        c = tier_counts.get(tier, 0)
        rec(mn <= c <= mx, f"tier={tier}: {c} (want {mn}-{mx})", warn=(c and abs(c - (mn + mx) // 2) <= 2))

    # Parent integrity + cycles
    roots = [L for L in locs if L.get("parent") is None]
    rec(len(roots) == 1, f"single root (got {len(roots)})")
    orphan = 0
    for L in locs:
        p = L.get("parent")
        if p and p not in loc_names:
            rec(False, f"dangling parent: {L['name']} → {p}")
            orphan += 1
    if orphan == 0:
        rec(True, "no dangling parents")

    # Cycle detection
    def has_cycle() -> bool:
        parents = {L["name"]: L.get("parent") for L in locs if L.get("name")}
        for n in parents:
            seen, cur = set(), n
            while cur:
                if cur in seen:
                    return True
                seen.add(cur)
                cur = parents.get(cur)
        return False

    rec(not has_cycle(), "no cycles")

    # Tree depth
    parents_map = {L["name"]: L.get("parent") for L in locs if L.get("name")}

    def depth(n: str) -> int:
        d, cur = 0, n
        while parents_map.get(cur):
            d += 1
            cur = parents_map[cur]
            if d > 20:
                return d
        return d

    max_d = max((depth(n) for n in loc_names), default=0)
    rec(5 <= max_d <= 7, f"max tree depth = {max_d} (want 5-7)")

    # --- Characters ---
    chars = data.get("characters") or []
    report_lines.append("\n## Characters")
    rec(
        TARGET_CHARACTER_COUNT[0] <= len(chars) <= TARGET_CHARACTER_COUNT[1],
        f"count {len(chars)} in {TARGET_CHARACTER_COUNT}",
    )

    main = next((c for c in chars if c.get("role", "").startswith("主角") or c.get("role") == "protagonist"), None)
    if main:
        aliases = main.get("aliases") or []
        canon = main.get("canonical_name", "")
        # count unique non-substring variants
        all_variants = [canon] + list(aliases)
        unique_variants: list[str] = []
        for v in all_variants:
            if not v:
                continue
            if any((v in u) or (u in v) for u in unique_variants):
                continue
            unique_variants.append(v)
        rec(
            len(unique_variants) >= TARGET_MAIN_CHAR_ALIAS_MIN,
            f"main char ({canon}) has {len(unique_variants)} non-substring variants (want ≥{TARGET_MAIN_CHAR_ALIAS_MIN})",
        )
    else:
        rec(False, "no main character (role starts with 主角) found")

    # --- Relationships ---
    rels = data.get("relationships") or []
    report_lines.append("\n## Relationships")
    rec(len(rels) >= TARGET_RELATIONS_MIN, f"total ≥ {TARGET_RELATIONS_MIN} (got {len(rels)})")
    cat_counts = Counter(r.get("category") for r in rels)
    rec(set(cat_counts.keys()) >= REQUIRED_CATEGORIES, f"covers 6 categories (got {sorted(cat_counts.keys())})")
    for cat, mn in CATEGORY_MIN.items():
        rec(cat_counts.get(cat, 0) >= mn, f"category {cat}: {cat_counts.get(cat, 0)} (want ≥{mn})")

    # --- Chapters ---
    outline = data.get("chapter_outline") or []
    report_lines.append("\n## Chapters")
    rec(len(outline) == TARGET_CHAPTER_COUNT, f"exactly {TARGET_CHAPTER_COUNT} chapters (got {len(outline)})")
    missing_setting = 0
    for ch in outline:
        setting = (ch.get("setting") or "").split("·")[0]
        if setting and setting not in loc_names:
            # Allow if any dot-segment is in loc_names
            segs = (ch.get("setting") or "").split("·")
            if not any(s in loc_names for s in segs):
                missing_setting += 1
    rec(missing_setting == 0, f"all chapter settings reference known locations ({missing_setting} mismatches)")

    # --- Forbidden names ---
    report_lines.append("\n## Originality")
    tex = json.dumps(data, ensure_ascii=False)
    hits = [name for name in FORBIDDEN_NAMES if name in tex]
    rec(not hits, f"no forbidden names ({len(hits)} hits: {hits[:5]})")

    # --- Summary ---
    report_lines.append("")
    report_lines.append(f"## Summary: {failures} FAIL, {warnings} WARN")

    verdict = "✅ PASS" if failures == 0 else "❌ FAIL"
    report_lines.append(f"\n**{verdict}**")
    if failures == 0:
        report_lines.append("\nProceed to Phase 2:")
        report_lines.append("```")
        report_lines.append("uv run python scripts/synthesize_novel.py --phase 2")
        report_lines.append("```")
    else:
        report_lines.append("\n修复建议:让 DeepSeek 针对失败项局部重生成,或重新跑 Phase 1。")

    report_path = out_dir / "phase1_validation.md"
    report_path.write_text("\n".join(report_lines))
    print("\n".join(report_lines))
    print(f"\n[validate] report saved: {report_path}")
    return failures == 0


# =============================================================================
# Phase 2
# =============================================================================

def phase2_prev_summary(bible: dict, chapters_done: list[int]) -> str:
    """Build a concise prior-chapter summary using outline.plot_summary."""
    outline = bible.get("chapter_outline") or []
    if not chapters_done:
        return "(本章为全书第一章,无前情。)"
    parts = []
    for n in chapters_done:
        ch = next((c for c in outline if c.get("chapter") == n), None)
        if ch:
            parts.append(f"第{n}章 《{ch.get('title', '')}》:{ch.get('plot_summary', '')[:120]}")
    return "\n".join(parts)


def phase2_prev_tail(out_dir: Path, prev_n: int, max_chars: int = 400) -> str:
    """Return last `max_chars` Chinese chars of previous chapter file, if any."""
    if prev_n < 1:
        return "(无)"
    f = out_dir / "chapters" / f"ch{prev_n:02d}.txt"
    if not f.exists():
        return "(前一章尚未生成,本章独立推进。)"
    text = f.read_text()
    return text[-max_chars:]


def run_phase2_chapter(
    client, bible: dict, n: int, out_dir: Path, temperature: float = 0.85
) -> dict | None:
    outline = bible.get("chapter_outline") or []
    ch = next((c for c in outline if c.get("chapter") == n), None)
    if not ch:
        print(f"[phase2] ch{n}: outline missing; skip")
        return None

    chapters_dir = out_dir / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    ch_path = chapters_dir / f"ch{n:02d}.txt"
    if ch_path.exists():
        print(f"[phase2] ch{n:02d}: already exists, skip")
        return None

    # Trim the bible for prompt: keep world_bible + locations + characters (names+aliases only) + relationships
    # The full outline is too big; just pass the target chapter's outline item.
    trimmed_bible = {
        "novel_title": bible.get("novel_title"),
        "genre": bible.get("genre"),
        "world_bible": bible.get("world_bible"),
        "locations": bible.get("locations"),
        "characters": [
            {
                "canonical_name": c.get("canonical_name"),
                "aliases": c.get("aliases"),
                "role": c.get("role"),
            }
            for c in bible.get("characters") or []
        ],
        "relationships": bible.get("relationships"),
    }

    prev_summary = phase2_prev_summary(bible, list(range(1, n)))
    prev_tail = phase2_prev_tail(out_dir, n - 1)

    user = PHASE2_USER_TEMPLATE.format(
        N=n,
        PREV_N=n - 1,
        WORLD_BIBLE=json.dumps(trimmed_bible, ensure_ascii=False, indent=2),
        PREV_SUMMARY=prev_summary,
        PREV_TAIL=prev_tail,
        TITLE=ch.get("title", ""),
        SETTING=ch.get("setting", ""),
        KEY_CHARS=", ".join(ch.get("key_characters") or []),
        NEW_LOCS=", ".join(ch.get("new_locations") or []),
        PLOT_SUMMARY=ch.get("plot_summary", ""),
        MIN_CHARS=TARGET_CHAPTER_CHARS[0],
        MAX_CHARS=TARGET_CHAPTER_CHARS[1],
    )

    t0 = time.time()
    resp = client.chat(
        messages=[
            {"role": "system", "content": PHASE2_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=8192,
    )
    elapsed = time.time() - t0

    text = resp["content"]
    in_tok = resp["prompt_tokens"]
    out_tok = resp["completion_tokens"]
    cost = in_tok * PRICE_INPUT + out_tok * PRICE_OUTPUT

    # Quick structural check: first line should look like "第 N 章"
    first_line = text.strip().split("\n", 1)[0]
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))

    ch_path.write_text(text)
    result = {
        "event": "phase2_chapter",
        "chapter": n,
        "title": ch.get("title"),
        "elapsed_s": round(elapsed, 1),
        "tokens": {"in": in_tok, "out": out_tok},
        "cost_usd": round(cost, 4),
        "cjk_chars": cjk_count,
        "first_line": first_line[:40],
    }
    append_metadata(out_dir, result)
    flag = ""
    if not (TARGET_CHAPTER_CHARS[0] <= cjk_count <= TARGET_CHAPTER_CHARS[1]):
        flag = f" ⚠ CJK={cjk_count} (want {TARGET_CHAPTER_CHARS[0]}-{TARGET_CHAPTER_CHARS[1]})"
    print(
        f"[phase2] ch{n:02d}: {cjk_count:4d} chars, {elapsed:4.1f}s, ${cost:.4f} | "
        f"{first_line[:30]}{flag}"
    )
    return result


def run_phase2(out_dir: Path, chapters: list[int], temperature: float = 0.85) -> None:
    bible_path = out_dir / "phase1_bible.json"
    if not bible_path.exists():
        sys.exit(f"[phase2] ERROR: run Phase 1 first; {bible_path} not found")
    bible = json.loads(bible_path.read_text())

    client = build_client()
    total_cost = 0.0
    failed: list[int] = []
    for n in chapters:
        try:
            result = run_phase2_chapter(client, bible, n, out_dir, temperature=temperature)
            if result:
                total_cost += result["cost_usd"]
        except Exception as e:
            print(f"[phase2] ch{n:02d} FAILED after retries: {type(e).__name__}: {e}")
            failed.append(n)
            append_metadata(out_dir, {"event": "phase2_chapter_failed", "chapter": n, "error": str(e)})
    print(f"[phase2] total cost this run: ${total_cost:.4f}")
    if failed:
        print(f"[phase2] ⚠ failed chapters: {failed} — rerun to retry")


# =============================================================================
# Concat
# =============================================================================

def run_concat(out_dir: Path) -> None:
    chapters_dir = out_dir / "chapters"
    files = sorted(chapters_dir.glob("ch*.txt"))
    if not files:
        sys.exit(f"[concat] no chapters in {chapters_dir}")
    out_file = out_dir / "full_novel.txt"
    bible = json.loads((out_dir / "phase1_bible.json").read_text())
    title = bible.get("novel_title", "Synthetic Novel")
    buf = [f"《{title}》\n\n"]
    for f in files:
        buf.append(f.read_text().rstrip())
        buf.append("\n\n")
    out_file.write_text("".join(buf))
    n = len(files)
    total_chars = sum(len(re.findall(r"[\u4e00-\u9fff]", f.read_text())) for f in files)
    print(f"[concat] {n} chapters → {out_file} ({total_chars:,} CJK chars)")


# =============================================================================
# CLI
# =============================================================================

def parse_chapter_range(s: str) -> list[int]:
    """Accept '3', '1-5', '1,3,7', '1-5,10'."""
    out: list[int] = []
    for tok in s.split(","):
        tok = tok.strip()
        if "-" in tok:
            a, b = tok.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif tok:
            out.append(int(tok))
    return [n for n in out if 1 <= n <= TARGET_CHAPTER_COUNT]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--phase", type=int, choices=[1, 2], help="Which phase to run")
    ap.add_argument("--validate", action="store_true", help="Audit Phase 1 output (no API call)")
    ap.add_argument("--concat", action="store_true", help="Stitch ch*.txt into full_novel.txt")
    ap.add_argument(
        "--chapters", default="1-30", help="Chapter range for Phase 2 (e.g. '1-30', '5', '1,3-5')"
    )
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT), type=Path, help="Output directory")
    ap.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Override sampling temperature (default 0.8 phase1, 0.85 phase2)",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)

    actions_selected = sum([args.phase is not None, args.validate, args.concat])
    if actions_selected != 1:
        ap.error("choose exactly one of: --phase 1 | --phase 2 | --validate | --concat")

    if args.phase == 1:
        run_phase1(out_dir, temperature=args.temperature or 0.8)
    elif args.validate:
        ok = validate(out_dir)
        sys.exit(0 if ok else 1)
    elif args.phase == 2:
        chapters = parse_chapter_range(args.chapters)
        if not chapters:
            sys.exit("[phase2] no valid chapters in range")
        run_phase2(out_dir, chapters, temperature=args.temperature or 0.85)
    elif args.concat:
        run_concat(out_dir)


if __name__ == "__main__":
    main()
