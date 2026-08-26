"""Story Q0 — Phase 0 质量仪表盘：五本小说地图数据质量基线测量。

测量协议（预注册，口径冻结）见
  _bmad-output/implementation-artifacts/tech-spec-quality-dashboard.md
验收标准见 _bmad-output/q0-quality-dashboard.md（AC1-AC10）。

指标（每个指标一个独立模块函数，纯函数、可单测）：
  M1 层级健康   compute_m1  — roots / orphans / max_children / 深度分布
  M2 召回代理   compute_m2  — LLM 扫描(seed=42 抽 10 章) + build_alias_map 归并
  M3 方向错误率 screen_direction_candidates + compute_m3 — 初筛 + LLM 仲裁
  M4 泛称残留   compute_m4  — fact_validator._is_generic_location 匹配层级节点
  M5 忠实度     compute_m5  — judge 三维度综合(Epic 3,FR-3.2/3.3;相对指标,
                              校准缺失或 kappa 低于阈值时标记“未校准”)
  M6 关系维度   compute_m6  — 消费 FR-1.5 回测 JSON(Epic 3,FR-3.4;mock 口径)

口径说明（与 tech-spec 的两处具体化，均在此冻结记录）：
  * uber-root：parent 链的终端根（出现在 parent 值中但从不作为 child 的节点，
    五本实测均为 "天下"）。"主世界" 是虚拟层级根，视为普通节点（depth 1、
    计为唯一 root）。此口径下西游 roots=1、max_children=63(西牛贺洲)，与论文
    v071 冻结口径一致。
  * M4 泛称集合：tech-spec 写 "name_authority 的泛称集合"，但 name_authority
    只维护人物泛称；地点泛称的权威实现是 fact_validator._is_generic_location
    （命名管线实际使用的同一函数）。M4 采用后者，在此记录这一澄清。
  * M1 orphans 证据集：chapter_facts.spatial_relationships（全类型 source/
    target）∪ locations[] 中含非空 parent 的条目（name 与 parent 双端）。

Frozen-data safety（m5/stardust 模式）：
  真实 DB (~/.ai-reader-v2/data.db) 只读。main() 先把 DB 复制到 scratch 目录
  （默认 /tmp/q0-data，可用 Q0_DATA_DIR 覆盖），设 AI_READER_DATA_DIR 后再
  import src 模块，并做运行时断言（DB_PATH 必须落在 scratch 内）。
  模块 import 无副作用（不在 import 时复制 DB），单元测试可安全导入。

LLM：DeepSeek deepseek-chat，temperature=0，配置读 backend/.env
  （DEEPSEEK_API_KEY，回退 LLM_API_KEY；读法参照 stardust_cot_arm.py）。
  每次调用打印 token 数与估算成本。

Usage:
    cd backend && uv run python scripts/quality_dashboard.py            # 五本全量
    uv run python scripts/quality_dashboard.py --novel xiyouji          # 单本
    Q0_DATA_DIR=/tmp/q0-data uv run python scripts/quality_dashboard.py --refresh

Output:
    ../../ai-reader-internal/analysis/quality-baseline-2026-08/
      {slug}.json / {slug}.md / {slug}.calibration-sample.json / summary.md
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ── 冻结常量（预注册口径，不得边跑边改）──────────────────────────────

SEED = 42
SCAN_CHAPTERS = 10          # M2 阶段 A：每本抽样章数
CALIBRATION_SAMPLE = 200    # M2 阶段 B：人工校准抽样上限
ARBITRATE_SAMPLE = 200      # M3：候选 >200 时抽样仲裁上限
QA_SAMPLE = 10              # QA 门：每本 M3 候选 / M2 T\E 各抽 10 条
MAX_CONTENT_CHARS = 30000   # 单章送 LLM 的字符上限（经典小说章节约 3-10k，绰绰有余）

MODEL = "deepseek-chat"     # DeepSeek V3
BASE_URL = "https://api.deepseek.com/v1"
MAX_OUTPUT_TOKENS = 16384  # 与 stardust_cot_arm 一致；西游 ch32 在 8192 下输出截断
# DeepSeek V3 list prices (USD / 1M tokens), cache-miss input
PRICE_IN = 0.27
PRICE_OUT = 1.10

# 五本小说（与论文 gold 集一致）。西游/红楼 id 冻结（DB 中存在同名重复行），
# 其余三本按 title 从 novels 表解析。
NOVELS: list[tuple[str, str, str | None]] = [
    ("xiyouji", "西游记", "3b2ef56c-1a55-466a-a7d1-34272446a198"),
    ("honglou", "红楼梦", "c384901a-8b71-437a-af35-b5ec1c56c696"),
    ("shuihu", "水浒传", None),
    ("sanguo", "三国演义", None),
    ("fengshen", "封神演义", None),
]

OUT_DIR = (
    _BACKEND_DIR / ".." / ".." / "ai-reader-internal" / "analysis"
    / "quality-baseline-2026-08"
).resolve()

# ── M2 附录 A prompt 模板（入库常量，口径可追溯）────────────────────

M2_SCAN_SYSTEM = (
    "你是中国古典小说的地名提取专家。你只输出严格 JSON，不输出任何其他文字。"
)

M2_SCAN_USER_TEMPLATE = """以下是小说《{title}》第{chapter_num}回《{chapter_title}》的正文片段。请提取正文中提到的**全部地名/地点名**（包括大洲、国家、城市、山川、洞府、宫殿、建筑、院落等具体或泛指的地点名称）。

要求：
1. 只提取地点名，不要人名、物品名、抽象概念
2. 同一地点的多种写法只保留最完整的一种
3. 严格输出 JSON：{{"locations": ["地名1", "地名2", ...]}}
4. 不要输出任何解释、不要用 markdown 代码块

正文：
{content}"""

# ── M3 仲裁 prompt 模板（入库常量）──────────────────────────────────

M3_ARBITRATE_SYSTEM = (
    "你是中国古典小说的地理包含关系审核专家。你只输出严格 JSON，不输出任何其他文字。"
)

M3_ARBITRATE_USER_TEMPLATE = """以下是从小说《{title}》自动构建的地点层级中筛出的可疑父子对。对每个父子对，判断"父地点包含子地点"这个方向是否正确。

判定标准：
- "correct"：父确实包含子（如 花果山 包含 水帘洞）
- "reversed"：方向颠倒，实际是子包含父（如 水帘洞 包含 花果山 是错误的，应为 reversed）
- "unrelated"：两者不存在包含关系或无法判断

可疑父子对（附原文证据，可能为空）：
{pairs_block}

严格输出 JSON：
{{"verdicts": [{{"parent": "父名", "child": "子名", "judgment": "correct|reversed|unrelated"}}, ...]}}

要求：覆盖全部 {n_pairs} 个父子对，不得遗漏或新增；不要输出任何解释、不要用 markdown 代码块。"""

# M3 初筛规则 2 的后缀层级词序（与论文 single-shot prompt 同一套：
# 界>洲>国>城>山>谷>洞>殿，父级必须严格高于子级）。
TIER_SUFFIX_ORDER = ["界", "洲", "国", "城", "山", "谷", "洞", "殿"]


# ═══════════════════════════════════════════════════════════════════
# 纯函数模块（不依赖 DB / LLM，单元测试直接覆盖）
# ═══════════════════════════════════════════════════════════════════

def md5_file(path: Path) -> str:
    """流式计算文件 md5（冻结记录用）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pick_sample(sorted_items: list, n: int, seed: int = SEED) -> list:
    """固定 seed 抽样（协议 §3：所有随机抽样用固定 seed）。"""
    if len(sorted_items) <= n:
        return list(sorted_items)
    return random.Random(seed).sample(sorted_items, n)


def find_uber_root(location_parents: dict[str, str]) -> str:
    """uber-root = parent 值中出现但从不作为 child 的终端根。

    唯一时直接采用；多个时取子节点最多者并在报告中记录；都没有则回退 "天下"。
    """
    children = set(location_parents.keys())
    parents = {p for p in location_parents.values() if p}
    terminals = sorted(parents - children)
    if len(terminals) == 1:
        return terminals[0]
    if terminals:
        kids = Counter(location_parents.values())
        return max(terminals, key=lambda t: kids.get(t, 0))
    return "天下"


def compute_m1(
    location_parents: dict[str, str],
    uber_root: str,
    evidence_names: set[str],
) -> dict:
    """M1 层级健康（tech-spec §2.M1）。

    roots：parent 为空或指向 uber-root 的非 uber-root 节点数
    orphans：从未出现在任何 contains/空间关系证据中的节点数
    max_children 及其节点名；深度分布 depth 1/2/3/4+ 与 depth>=3 占比
    """
    lp = {c: p for c, p in location_parents.items() if c}
    universe = set(lp) | {p for p in lp.values() if p}
    nodes = universe - {uber_root}

    roots = sorted(
        n for n in nodes
        if not lp.get(n) or lp.get(n) == uber_root
    )
    orphans = sorted(n for n in nodes if n not in evidence_names)

    kids = Counter(p for c, p in lp.items() if p and c != uber_root)
    max_node, max_ch = ("", 0)
    if kids:
        max_node, max_ch = max(kids.items(), key=lambda kv: (kv[1], kv[0]))

    def _depth(node: str) -> int:
        """边数：uber-root 为 0，直挂 uber-root 为 1。环防护：成环节点按到达环的距离计。"""
        depth = 0
        seen = set()
        cur = node
        while cur in lp and lp.get(cur) and cur not in seen:
            seen.add(cur)
            cur = lp[cur]
            depth += 1
            if cur == uber_root:
                break
        return depth

    dist = {"1": 0, "2": 0, "3": 0, "4+": 0}
    for n in nodes:
        d = _depth(n)
        dist["4+" if d >= 4 else str(d)] += 1
    deep = dist["3"] + dist["4+"]

    return {
        "uber_root": uber_root,
        "total_nodes": len(nodes),
        "roots": len(roots),
        "root_names": roots,
        "orphans": len(orphans),
        "orphan_names": orphans,
        "orphan_rate": (len(orphans) / len(nodes)) if nodes else 0.0,
        "max_children": max_ch,
        "max_children_node": max_node,
        "depth_distribution": dist,
        "depth_ge3_ratio": (deep / len(nodes)) if nodes else 0.0,
    }


def canonicalize(names, alias_map: dict[str, str]) -> set[str]:
    """别名归并（entity_aggregator 同款用法：alias_map.get(name, name)）。"""
    return {alias_map.get(n, n) for n in names if n}


def compute_m2(
    text_names: set[str],
    extracted_names: set[str],
    alias_map: dict[str, str],
) -> dict:
    """M2 召回代理（tech-spec §2.M2）：recall_proxy = |T ∩ E| / |T|（别名归并后）。"""
    t_canon = canonicalize(text_names, alias_map)
    e_canon = canonicalize(extracted_names, alias_map)
    hit = t_canon & e_canon
    miss = t_canon - e_canon
    return {
        "T_size": len(t_canon),
        "E_size": len(e_canon),
        "T_intersect_E": len(hit),
        "recall_proxy": (len(hit) / len(t_canon)) if t_canon else None,
        "T_minus_E": sorted(miss),
    }


def tier_suffix_rank(name: str) -> int | None:
    """名字末尾命中的层级词在 TIER_SUFFIX_ORDER 中的名次（0=最高级）。"""
    for rank, suffix in enumerate(TIER_SUFFIX_ORDER):
        if name.endswith(suffix):
            return rank
    return None


def screen_direction_candidates(
    location_parents: dict[str, str],
    uber_root: str,
) -> list[dict]:
    """M3 自动初筛（tech-spec §2.M3）。返回候选父子对及命中规则。

    R1 子名包含父名：child 以 parent 为子串（如 父=灵山 子=灵山胜境）→ 可疑
    R2 共用后缀层级词矛盾：父子都带层级后缀且父级名次不严格高于子级
       （如 父=水帘洞(洞) 子=花果山(山) → 洞<山，方向可疑）
    指向 uber-root 的边（虚拟根）不参与初筛。
    """
    candidates = []
    for child, parent in sorted(location_parents.items()):
        if not child or not parent or parent == uber_root or child == uber_root:
            continue
        rules = []
        if parent != child and parent in child:
            rules.append("R1_child_name_contains_parent")
        pr, cr = tier_suffix_rank(parent), tier_suffix_rank(child)
        if pr is not None and cr is not None and pr >= cr:
            rules.append("R2_suffix_tier_contradiction")
        if rules:
            candidates.append({"parent": parent, "child": child, "rules": rules})
    return candidates


def compute_m3(
    total_pairs: int,
    candidates: list[dict],
    verdicts: list[dict],
) -> dict:
    """M3 汇总：direction_error_rate = reversed / 已仲裁数；候选占比另行报告。"""
    judged = [v for v in verdicts if v.get("judgment") in ("correct", "reversed")]
    reversed_n = sum(1 for v in judged if v["judgment"] == "reversed")
    unclear_n = sum(1 for v in verdicts if v.get("judgment") == "unrelated")
    return {
        "total_parent_child_pairs": total_pairs,
        "candidates": len(candidates),
        "candidate_ratio": (len(candidates) / total_pairs) if total_pairs else 0.0,
        "arbitrated": len(verdicts),
        "reversed": reversed_n,
        "unclear": unclear_n,
        "direction_error_rate": (reversed_n / len(judged)) if judged else None,
    }


def compute_m4(node_names, is_generic_fn) -> dict:
    """M4 泛称残留（tech-spec §2.M4）：generic_residue = 命中数 / 总节点数。

    is_generic_fn: name -> reason str | None（真实运行传
    fact_validator._is_generic_location；测试传构造 stub）。
    """
    names = sorted(n for n in node_names if n)
    hits = []
    for n in names:
        reason = is_generic_fn(n)
        if reason:
            hits.append({"name": n, "reason": reason})
    return {
        "total_nodes": len(names),
        "generic_hits": len(hits),
        "generic_residue": (len(hits) / len(names)) if names else 0.0,
        "hit_details": hits,
    }


# ── M5/M6（Epic 3，FR-3.2–FR-3.4）──────────────────────────────────
# M5 faithfulness：消费 judge_extraction_faithfulness.py 的评分报告 + 校准报告；
# 校准缺失或 kappa 低于阈值时标记“未校准”（FR-3.3）。
# M6 关系维度准确率：消费 eval_relation_dimensions.py 的 JSON 副产物（FR-1.5 回测）。
AUDIT_REPORTS_DIR = _BACKEND_DIR / "audit_reports"
M5_KAPPA_THRESHOLD = 0.40  # 与 judge_extraction_faithfulness.KAPPA_THRESHOLD 一致


def compute_m5(judge_report: dict | None, calibration: dict | None) -> dict:
    """M5 faithfulness（judge 三维度综合）。judge 分数只作相对指标，不进论文数字。"""
    if not judge_report or not judge_report.get("aggregate"):
        return {"status": "missing", "calibration_label": "未校准"}
    agg = judge_report["aggregate"]
    kappa = (calibration or {}).get("kappa")
    calibrated = bool((calibration or {}).get("calibrated"))
    return {
        "status": "ok",
        "precision": agg.get("precision"),
        "faithfulness": agg.get("faithfulness"),
        "comprehensiveness": agg.get("comprehensiveness"),
        "m5": agg.get("m5"),
        "evidence_coverage": agg.get("evidence_coverage"),
        "span_located_rate": agg.get("span_located_rate"),
        "chapters_judged": agg.get("chapters_judged"),
        "kappa": kappa,
        "calibrated": calibrated,
        "calibration_label": "已校准" if calibrated else "未校准",
    }


def compute_m6(rel_eval: dict | None) -> dict:
    """M6 关系维度准确率（FR-1.5 回测产物，mock 口径，见该报告口径声明）。"""
    if not rel_eval:
        return {"status": "missing"}
    sh, xy = rel_eval["shuihu"], rel_eval["xiyouji"]
    target = rel_eval.get("shuihu_subtype_target", 0.55)
    sh_acc = sh["subtype"]["accuracy"]
    xy_mock = xy["mock_category"]["accuracy"]
    xy_base = xy["legacy_category_baseline"]["accuracy"]
    return {
        "status": "ok",
        "shuihu_subtype_accuracy": sh_acc,
        "shuihu_subtype_target": target,
        "shuihu_target_met": sh_acc is not None and sh_acc >= target,
        "xiyouji_mock_category": xy_mock,
        "xiyouji_legacy_baseline": xy_base,
        "xiyouji_not_below_baseline": (
            xy_mock is not None and xy_base is not None and xy_mock >= xy_base
        ),
    }


def load_latest_judge_report(slug: str) -> dict | None:
    """加载 audit_reports 中最新的 judge 评分报告；无则 None（不影响 M1–M4）。"""
    candidates = sorted(AUDIT_REPORTS_DIR.glob(f"judge_faithfulness_{slug}_*.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def load_judge_calibration() -> dict | None:
    """加载 judge 校准报告（FR-3.3）；无则 None。"""
    path = AUDIT_REPORTS_DIR / "judge_calibration.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_m6_eval() -> dict | None:
    """加载 FR-1.5 回测 JSON；缺失时离线重算（纯规则，不调 LLM）。"""
    path = AUDIT_REPORTS_DIR / "relation_dimensions_eval.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        scripts_dir = str(_BACKEND_DIR / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import eval_relation_dimensions as ev

        return {
            "schema": ev.SCHEMA_VERSION,
            "shuihu_subtype_target": ev.SHUIHU_SUBTYPE_TARGET,
            "shuihu": ev.evaluate_shuihu(ev.load_shuihu_silver()),
            "xiyouji": ev.evaluate_xiyouji(ev.load_xiyouji_gold()),
        }
    except Exception:
        return None


def parse_llm_json(text: str):
    """解析 LLM 输出：容忍 markdown 代码块包裹，截取最外层 JSON 对象。"""
    t = text.strip()
    if "```json" in t:
        t = t.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in t:
        t = t.split("```", 1)[1].split("```", 1)[0]
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        t = t[start:end + 1]
    return json.loads(t)


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return prompt_tokens / 1_000_000 * PRICE_IN + completion_tokens / 1_000_000 * PRICE_OUT


# ═══════════════════════════════════════════════════════════════════
# 报告生成器（纯函数）
# ═══════════════════════════════════════════════════════════════════

def render_novel_md(report: dict) -> str:
    """单本 markdown 报告。"""
    r = report
    lines = [
        f"# 质量基线：{r['title']}（{r['slug']}）",
        "",
        f"- novel_id: `{r['novel_id']}`",
        f"- DB md5: `{r['freeze']['db_md5']}`",
        f"- chapter_facts 行数: {r['freeze']['chapter_facts_rows']}",
        f"- seed: {SEED} · 模型: {MODEL} (temperature=0)",
        "",
    ]
    if r.get("error"):
        lines += [f"> ⚠️ 本小说测量失败：{r['error']}", ""]
        return "\n".join(lines)

    m1 = r["m1"]
    lines += [
        "## M1 层级健康",
        "",
        f"- 节点总数（不含 uber-root `{m1['uber_root']}`）: {m1['total_nodes']}",
        f"- roots: {m1['roots']}（{', '.join(m1['root_names'][:10])}"
        + ("…" if len(m1["root_names"]) > 10 else "") + "）",
        f"- orphans: {m1['orphans']}（orphan_rate={m1['orphan_rate']:.1%}）",
        f"- max_children: {m1['max_children']}（{m1['max_children_node']}）",
        f"- 深度分布: depth1={m1['depth_distribution']['1']} "
        f"depth2={m1['depth_distribution']['2']} "
        f"depth3={m1['depth_distribution']['3']} "
        f"depth4+={m1['depth_distribution']['4+']} "
        f"（depth≥3 占比 {m1['depth_ge3_ratio']:.1%}）",
        "",
    ]

    m2 = r["m2"]
    if m2.get("status") == "ok":
        lines += [
            "## M2 召回代理",
            "",
            f"- 抽样章节（seed={SEED}）: {m2['sampled_chapters']}",
            f"- |T|（文本可证地点集）: {m2['T_size']} · |E|（已抽取地点集）: {m2['E_size']}",
            f"- |T ∩ E|: {m2['T_intersect_E']} · **recall_proxy（raw）: "
            f"{m2['recall_proxy']:.1%}**",
            f"- T\\E 差集（漏抽候选）: {len(m2['T_minus_E'])} 个",
            f"- 人工校准: `{m2['calibration_file']}` "
            f"（{m2['calibration_size']} 条，**待人工校准**）",
            "",
        ]
    else:
        lines += ["## M2 召回代理", "", f"> ⚠️ 未完成：{m2.get('error', '未知原因')}", ""]

    m3 = r["m3"]
    if m3.get("status") == "ok":
        lines += [
            "## M3 方向错误率",
            "",
            f"- 父子对总数: {m3['total_parent_child_pairs']}",
            f"- 初筛候选: {m3['candidates']}（占比 {m3['candidate_ratio']:.1%}）",
            f"- LLM 仲裁: {m3['arbitrated']} 条 → 方向颠倒 {m3['reversed']} 条 "
            f"（unrelated {m3['unclear']} 条）",
            f"- **direction_error_rate: "
            + (f"{m3['direction_error_rate']:.1%}" if m3["direction_error_rate"] is not None else "N/A")
            + "**",
            "",
        ]
    else:
        lines += ["## M3 方向错误率", "", f"> ⚠️ 未完成：{m3.get('error', '未知原因')}", ""]

    m4 = r["m4"]
    lines += [
        "## M4 泛称残留",
        "",
        f"- 命中: {m4['generic_hits']} / {m4['total_nodes']} "
        f"（**generic_residue={m4['generic_residue']:.1%}**）",
    ]
    if m4["hit_details"]:
        lines.append("- 命中明细: " + "、".join(
            f"{h['name']}({h['reason']})" for h in m4["hit_details"][:20])
            + ("…" if len(m4["hit_details"]) > 20 else ""))
    lines.append("")

    m5 = r.get("m5")
    if m5 and m5.get("status") == "ok":
        lines += ["## M5 抽取忠实度（judge 三维度）", ""]
        if m5.get("precision") is not None:
            lines.append(
                f"- precision={m5['precision']:.1%} · faithfulness={m5['faithfulness']:.1%} "
                f"· comprehensiveness={m5['comprehensiveness']:.1%}"
            )
        if m5.get("m5") is not None:
            kappa_note = f", κ={m5['kappa']:.3f}" if m5.get("kappa") is not None else ""
            lines.append(
                f"- **M5 综合: {m5['m5']:.1%}**（{m5['calibration_label']}{kappa_note}）"
            )
        if m5.get("evidence_coverage") is not None:
            lines.append(
                f"- evidence 覆盖率: {m5['evidence_coverage']:.1%} · span 可定位率: "
                f"{m5['span_located_rate']:.1%}"
            )
        lines.append("")
    elif m5:
        lines += ["## M5 抽取忠实度（judge 三维度）", "",
                  "> ⚠️ 未运行 judge（FR-3.2），M5 缺失", ""]

    qa = r.get("qa_samples", {})
    lines += [
        "## QA 抽检（待人工核对）",
        "",
        f"- M3 候选抽检 {len(qa.get('m3', []))} 条 / M2 T\\E 抽检 {len(qa.get('m2', []))} 条"
        "（详见 JSON 报告 qa_samples 字段）",
        "",
    ]
    return "\n".join(lines)


def render_summary_md(reports: list[dict], freeze: dict, llm_cost: dict,
                      m6: dict | None = None) -> str:
    """summary.md：五本汇总 + RQ1/RQ2 + Phase 1 方向建议（PRD §4 决策规则）。

    m6：FR-1.5 关系维度回测结果（compute_m6 输出），为 None 时 M6 列记 N/A。
    """
    lines = [
        "# Phase 0 质量仪表盘：五本小说基线汇总",
        "",
        f"- 生成时间: {freeze['generated_at']}",
        f"- DB md5: `{freeze['db_md5']}`（复跑前必须先核对此哈希）",
        f"- seed: {SEED} · 模型: {MODEL} (temperature=0)",
        f"- LLM 花费: 输入 {llm_cost['prompt_tokens']:,} tok / 输出 "
        f"{llm_cost['completion_tokens']:,} tok ≈ **${llm_cost['cost_usd']:.4f}**",
        "",
        "> ⚠️ M2 recall_proxy 为 **raw 值**；人工校准样本已产出"
        "（每本一个 〈slug〉.calibration-sample.json），**待人工校准**后给出修正召回。",
        "> QA 抽检（每本 M3 候选 + M2 T\\E 各 10 条）已生成，**待人工核对**。",
        "> M5 为 judge 相对指标（不进论文冻结数字）；M6 为 FR-1.5 回测 mock 口径。",
        "",
        "## 指标总表",
        "",
        "| 小说 | roots | orphans(率) | max_children | depth≥3 | recall_proxy(raw) | 方向候选占比 | direction_error_rate | generic_residue | M5 faithfulness | M6 关系维度 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        if r.get("error"):
            lines.append(f"| {r['title']} | 失败: {r['error']} | — | — | — | — | — | — | — | — | — |")
            continue
        m1, m2, m3, m4 = r["m1"], r["m2"], r["m3"], r["m4"]
        recall = f"{m2['recall_proxy']:.1%}" if m2.get("recall_proxy") is not None else "N/A"
        der = f"{m3['direction_error_rate']:.1%}" if m3.get("direction_error_rate") is not None else "N/A"
        cand = f"{m3['candidate_ratio']:.1%}" if m3.get("candidate_ratio") is not None else "N/A"
        m5 = r.get("m5") or {}
        if m5.get("status") == "ok" and m5.get("m5") is not None:
            m5_cell = f"{m5['m5']:.1%}({m5['calibration_label']})"
        else:
            m5_cell = "N/A"
        m6_cell = "—"
        if m6 and m6.get("status") == "ok":
            if r["slug"] == "shuihu":
                acc = m6["shuihu_subtype_accuracy"]
                m6_cell = (
                    f"类型级 {acc:.1%}({'达标' if m6['shuihu_target_met'] else '未达标'})"
                    if acc is not None else "N/A"
                )
            elif r["slug"] == "xiyouji":
                mock = m6["xiyouji_mock_category"]
                m6_cell = (
                    f"category {mock:.1%}({'不低于旧基线' if m6['xiyouji_not_below_baseline'] else '低于旧基线'})"
                    if mock is not None else "N/A"
                )
        lines.append(
            f"| {r['title']} | {m1['roots']} | {m1['orphans']}({m1['orphan_rate']:.1%}) "
            f"| {m1['max_children']}({m1['max_children_node']}) "
            f"| {m1['depth_ge3_ratio']:.1%} | {recall} | {cand} | {der} "
            f"| {m4['generic_residue']:.1%} | {m5_cell} | {m6_cell} |"
        )
    lines.append("")

    ok = [r for r in reports if not r.get("error")]
    recalls = [r["m2"]["recall_proxy"] for r in ok if r["m2"].get("recall_proxy") is not None]
    ders = [r["m3"]["direction_error_rate"] for r in ok if r["m3"].get("direction_error_rate") is not None]
    orphan_rates = [r["m1"]["orphan_rate"] for r in ok]
    avg = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731

    lines += [
        "## RQ1：召回基线",
        "",
    ]
    if recalls:
        lines.append(
            f"- 五本 raw recall_proxy 均值 **{avg(recalls):.1%}**"
            f"（区间 {min(recalls):.1%}–{max(recalls):.1%}）。"
            "该值为 LLM 扫描（10 章抽样）对照已抽取地点集的代理召回，"
            "待 200 条人工校准后修正。"
        )
    else:
        lines.append("- M2 全部失败，无召回基线。")
    lines += [
        "",
        "## RQ2：错误构成比",
        "",
    ]
    if ders:
        lines.append(
            f"- 方向错误率（候选集内）均值 **{avg(ders):.1%}**；"
            f"孤儿率均值 **{avg(orphan_rates):.1%}**；"
            "泛称残留见总表。缺中间层由 depth≥3 占比侧面反映。"
        )
    else:
        lines.append("- M3 全部失败，无方向错误率。")

    lines += ["", "## Phase 1 方向建议（PRD §4 决策规则）", ""]
    fired = []
    if orphan_rates and avg(orphan_rates) > 0.15:
        fired.append(f"- **D1 信号扩展**：孤儿率均值 {avg(orphan_rates):.1%} > 15% → 优先")
    if recalls and avg(recalls) < 0.70:
        fired.append(f"- **D2 召回补抽**：raw 召回均值 {avg(recalls):.1%} < 70% → 优先")
    if ders and avg(ders) > 0.10:
        fired.append(f"- **D3 方向校正**：方向错误率均值 {avg(ders):.1%} > 10% → 优先")
    if fired:
        lines += fired
        lines.append("- D4 人工修正闭环：作为产品侧长尾并行推进（决策规则未直接触发）。")
    else:
        lines.append("- 三条决策规则均未触发阈值；建议先完成 M2 人工校准再定 Phase 1。")
    lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 以下为运行时（DB / LLM）代码 — 仅在 main() 路径执行
# ═══════════════════════════════════════════════════════════════════

def setup_scratch_db() -> Path:
    """m5/stardust 模式：复制冻结 DB 到 scratch，设 AI_READER_DATA_DIR。

    必须在任何 src.* import 之前调用。
    """
    real_dir = Path(os.environ.get("AI_READER_DATA_DIR", Path.home() / ".ai-reader-v2"))
    scratch = Path(os.environ.get("Q0_DATA_DIR", "/tmp/q0-data"))
    if scratch.resolve() == real_dir.resolve():
        sys.exit("FATAL: Q0_DATA_DIR must differ from the real data dir (frozen novels).")
    real_db = real_dir / "data.db"
    scratch_db = scratch / "data.db"
    if "--refresh" in sys.argv or not scratch_db.exists():
        import shutil

        scratch.mkdir(parents=True, exist_ok=True)
        print(f"[q0] copying frozen DB → {scratch_db} (this may take a moment)...")
        shutil.copy2(real_db, scratch_db)
    os.environ["AI_READER_DATA_DIR"] = str(scratch)
    return scratch


def resolve_novel_ids(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """解析五本 novel_id：西游/红楼用冻结 id，其余按 title 查 novels 表。"""
    resolved = []
    for slug, title, fixed_id in NOVELS:
        if fixed_id:
            resolved.append((slug, title, fixed_id))
            continue
        rows = conn.execute(
            "SELECT id FROM novels WHERE title=?", (title,)
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(f"title={title!r} 匹配到 {len(rows)} 行，无法唯一解析")
        resolved.append((slug, title, rows[0][0]))
    return resolved


def load_novel_data(conn: sqlite3.Connection, novel_id: str) -> dict:
    """只读加载：ws 层级 + chapter_facts 证据 + 章节清单。"""
    row = conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("world_structures 无此行（未构建层级？）")
    ws = json.loads(row[0])
    location_parents = ws.get("location_parents") or {}
    genre = ws.get("novel_genre_hint")

    chapters = [
        (r[0], r[1], r[2])
        for r in conn.execute(
            "SELECT chapter_num, title, content FROM chapters "
            "WHERE novel_id=? ORDER BY chapter_num",
            (novel_id,),
        )
    ]

    evidence_names: set[str] = set()
    extracted_names: set[str] = set()
    pair_evidence: dict[tuple[str, str], str] = {}
    cf_rows = 0
    for (fact_json_text,) in conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
    ):
        cf_rows += 1
        try:
            fact = json.loads(fact_json_text)
        except Exception:
            continue
        for loc in fact.get("locations") or []:
            name = (loc.get("name") or "").strip()
            parent = (loc.get("parent") or "").strip()
            if name:
                extracted_names.add(name)
            if name and parent and parent.lower() not in ("none", "null"):
                evidence_names.add(name)
                evidence_names.add(parent)
                ev = (loc.get("parent_evidence") or "").strip()
                if ev and (parent, name) not in pair_evidence:
                    pair_evidence[(parent, name)] = ev
        for sr in fact.get("spatial_relationships") or []:
            src = (sr.get("source") or "").strip()
            tgt = (sr.get("target") or "").strip()
            if src:
                evidence_names.add(src)
            if tgt:
                evidence_names.add(tgt)
            ev = (sr.get("narrative_evidence") or sr.get("value") or "").strip()
            if src and tgt and ev:
                pair_evidence.setdefault((src, tgt), ev)

    return {
        "location_parents": location_parents,
        "genre": genre,
        "chapters": chapters,
        "evidence_names": evidence_names,
        "extracted_names": extracted_names,
        "pair_evidence": pair_evidence,
        "chapter_facts_rows": cf_rows,
    }


async def deepseek_chat(system: str, user: str, tag: str, cost_acc: dict) -> str:
    """DeepSeek 调用（stardust_cot_arm 读法）：打印并累计 token/成本。"""
    import httpx

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or os.environ.get(
        "LLM_API_KEY", ""
    ).strip()
    if not api_key:
        sys.exit("DEEPSEEK_API_KEY / LLM_API_KEY not set (check backend/.env).")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=300.0,
    ) as client:
        resp = await client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
    usage = data["usage"]
    c = cost_usd(usage["prompt_tokens"], usage["completion_tokens"])
    cost_acc["prompt_tokens"] += usage["prompt_tokens"]
    cost_acc["completion_tokens"] += usage["completion_tokens"]
    cost_acc["cost_usd"] += c
    print(
        f"[q0][llm] {tag}: in={usage['prompt_tokens']:,} "
        f"out={usage['completion_tokens']:,} cost≈${c:.4f}"
    )
    return data["choices"][0]["message"]["content"]


async def run_m2_scan(
    title: str,
    chapters: list[tuple[int, str, str]],
    cost_acc: dict,
) -> tuple[set[str], dict[str, list[int]], list[int]]:
    """M2 阶段 A：seed=42 抽 10 章，DeepSeek 提取地点 → 文本可证地点集 T。"""
    chapter_nums = [c[0] for c in chapters]
    sampled_nums = sorted(pick_sample(chapter_nums, SCAN_CHAPTERS))
    by_num = {c[0]: c for c in chapters}

    text_names: set[str] = set()
    name_chapters: dict[str, list[int]] = {}
    for num in sampled_nums:
        _, ch_title, content = by_num[num]
        content = (content or "")[:MAX_CONTENT_CHARS]
        user = M2_SCAN_USER_TEMPLATE.format(
            title=title, chapter_num=num,
            chapter_title=ch_title or f"第{num}回", content=content,
        )
        raw = await deepseek_chat(M2_SCAN_SYSTEM, user, f"m2-scan {title} ch{num}", cost_acc)
        try:
            parsed = parse_llm_json(raw)
            names = parsed.get("locations") or []
        except Exception as e:
            # 输出截断/格式错误时重试一次（温度 0，口径不变）
            print(f"[q0][m2] WARN ch{num} JSON 解析失败，重试一次: {e}")
            raw = await deepseek_chat(
                M2_SCAN_SYSTEM, user, f"m2-scan {title} ch{num} retry", cost_acc
            )
            try:
                parsed = parse_llm_json(raw)
                names = parsed.get("locations") or []
            except Exception as e2:
                print(f"[q0][m2] WARN ch{num} 重试仍失败，跳过本章: {e2}")
                continue
        for n in names:
            n = (n or "").strip()
            if n:
                text_names.add(n)
                name_chapters.setdefault(n, []).append(num)
    return text_names, name_chapters, sampled_nums


async def run_m3_arbitrate(
    title: str,
    candidates: list[dict],
    pair_evidence: dict[tuple[str, str], str],
    cost_acc: dict,
) -> list[dict]:
    """M3 LLM 仲裁：候选 ≤200 全量，>200 seed=42 抽样；每批 50 对一次调用。"""
    to_judge = candidates
    if len(candidates) > ARBITRATE_SAMPLE:
        to_judge = pick_sample(candidates, ARBITRATE_SAMPLE)
    verdicts: list[dict] = []
    batch = 50
    for i in range(0, len(to_judge), batch):
        chunk = to_judge[i:i + batch]
        lines = []
        for j, cand in enumerate(chunk, 1):
            ev = pair_evidence.get((cand["parent"], cand["child"])) or \
                 pair_evidence.get((cand["child"], cand["parent"])) or ""
            lines.append(f"{j}. 父={cand['parent']} 子={cand['child']} 证据: {ev[:120]}")
        user = M3_ARBITRATE_USER_TEMPLATE.format(
            title=title, pairs_block="\n".join(lines), n_pairs=len(chunk),
        )
        raw = await deepseek_chat(
            M3_ARBITRATE_SYSTEM, user, f"m3-arbitrate {title} batch{i // batch + 1}",
            cost_acc,
        )
        try:
            parsed = parse_llm_json(raw)
            for v in parsed.get("verdicts") or []:
                j = (v.get("judgment") or "").strip().lower()
                if j not in ("correct", "reversed", "unrelated"):
                    j = "unrelated"
                verdicts.append({
                    "parent": v.get("parent"), "child": v.get("child"),
                    "judgment": j,
                })
        except Exception as e:
            print(f"[q0][m3] WARN batch{i // batch + 1} JSON 解析失败，本批记为缺失: {e}")
    return verdicts


async def run_novel(
    conn: sqlite3.Connection,
    slug: str,
    title: str,
    novel_id: str,
    freeze: dict,
    cost_acc: dict,
    out_dir: Path,
) -> dict:
    """单本全流程（AC10：异常由调用方捕获，不阻塞其他小说）。"""
    print(f"\n=== {title} ({slug}) id={novel_id} ===")
    data = load_novel_data(conn, novel_id)
    lp = data["location_parents"]
    uber = find_uber_root(lp)
    universe = (set(lp) | {p for p in lp.values() if p}) - {uber}

    # 别名归并（build_alias_map，entity_aggregator 同款用法）
    from src.services.alias_resolver import build_alias_map

    alias_map = await build_alias_map(novel_id)
    print(f"[q0] alias_map: {len(alias_map)} 条")

    # M1
    m1 = compute_m1(lp, uber, data["evidence_names"])
    print(f"[q0][m1] roots={m1['roots']} orphans={m1['orphans']} "
          f"max_ch={m1['max_children']}({m1['max_children_node']})")

    # M2
    try:
        text_names, name_chapters, sampled = await run_m2_scan(
            title, data["chapters"], cost_acc
        )
        m2 = compute_m2(text_names, data["extracted_names"], alias_map)
        m2["status"] = "ok"
        m2["sampled_chapters"] = sampled
        # 阶段 B：人工校准样本（is_valid 留空待人工标注）
        cal = pick_sample(sorted(text_names), CALIBRATION_SAMPLE)
        cal_entries = [
            {"name": n, "chapters": sorted(name_chapters.get(n, [])), "is_valid": None}
            for n in cal
        ]
        cal_file = f"{slug}.calibration-sample.json"
        (out_dir / cal_file).write_text(
            json.dumps({
                "novel": title, "slug": slug, "seed": SEED,
                "note": "M2 阶段 B 人工校准样本：is_valid 待人工填写 "
                        "(true=真地点 / false=非地点)，用于修正 raw recall_proxy",
                "samples": cal_entries,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        m2["calibration_file"] = cal_file
        m2["calibration_size"] = len(cal_entries)
        m2["calibration_status"] = "待人工校准"
        print(f"[q0][m2] |T|={m2['T_size']} |E|={m2['E_size']} "
              f"recall_proxy={m2['recall_proxy']:.1%}")
    except Exception as e:
        m2 = {"status": "failed", "error": str(e)}
        print(f"[q0][m2] FAILED: {e}")

    # M3
    try:
        total_pairs = sum(
            1 for c, p in lp.items() if c and p and p != uber and c != uber
        )
        candidates = screen_direction_candidates(lp, uber)
        for cand in candidates:
            cand["evidence"] = (
                data["pair_evidence"].get((cand["parent"], cand["child"]))
                or data["pair_evidence"].get((cand["child"], cand["parent"]))
                or ""
            )
        verdicts = await run_m3_arbitrate(
            title, candidates, data["pair_evidence"], cost_acc
        )
        m3 = compute_m3(total_pairs, candidates, verdicts)
        m3["status"] = "ok"
        m3["candidate_list"] = candidates
        m3["verdicts"] = verdicts
        m3["arbitration_note"] = (
            "全量仲裁" if len(candidates) <= ARBITRATE_SAMPLE
            else f"seed={SEED} 抽样 {ARBITRATE_SAMPLE} 仲裁"
        )
        der = m3["direction_error_rate"]
        print(f"[q0][m3] pairs={total_pairs} candidates={len(candidates)} "
              f"error_rate={der:.1%}" if der is not None else
              f"[q0][m3] pairs={total_pairs} candidates={len(candidates)} error_rate=N/A")
    except Exception as e:
        m3 = {"status": "failed", "error": str(e)}
        print(f"[q0][m3] FAILED: {e}")

    # M4（地点泛称权威实现：fact_validator._is_generic_location；见模块docstring口径说明）
    from src.extraction.fact_validator import _is_generic_location

    genre = data["genre"]
    m4 = compute_m4(universe, lambda n: _is_generic_location(n, genre))
    print(f"[q0][m4] generic_residue={m4['generic_residue']:.1%} "
          f"({m4['generic_hits']}/{m4['total_nodes']})")

    # QA 抽检样本（tech-spec §3：M3 候选与 M2 T\E 各抽 10 条，待人工核对）
    qa = {"status": "待人工核对", "m3": [], "m2": []}
    if m3.get("status") == "ok":
        qa["m3"] = [
            {"parent": c["parent"], "child": c["child"], "rules": c["rules"],
             "evidence": c.get("evidence", ""), "human_verdict": None}
            for c in pick_sample(m3["candidate_list"], QA_SAMPLE)
        ]
    if m2.get("status") == "ok":
        qa["m2"] = [
            {"name": n, "chapters": sorted(name_chapters.get(n, [])),
             "human_verdict": None}
            for n in pick_sample(m2["T_minus_E"], QA_SAMPLE)
        ]

    # M5（FR-3.2/3.3）：消费 judge 评分报告 + 校准报告；未运行 judge 时记 missing，
    # 不影响 M1–M4
    m5 = compute_m5(load_latest_judge_report(slug), load_judge_calibration())
    print(f"[q0][m5] {m5.get('calibration_label', '未校准')} "
          f"m5={m5['m5']:.1%}" if m5.get("m5") is not None else "[q0][m5] 缺失")

    return {
        "slug": slug,
        "title": title,
        "novel_id": novel_id,
        "freeze": {
            "db_md5": freeze["db_md5"],
            "chapter_facts_rows": data["chapter_facts_rows"],
        },
        "m1": m1,
        "m2": m2,
        "m3": m3,
        "m4": m4,
        "m5": m5,
        "qa_samples": qa,
    }


async def main() -> None:
    from datetime import datetime, timezone

    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env", override=True)

    scratch = setup_scratch_db()

    # 运行时断言：src 必须指向 scratch 副本（m5 模式）
    from src.infra.config import DB_PATH

    if scratch.resolve() not in Path(DB_PATH).resolve().parents:
        sys.exit(f"FATAL: DB_PATH {DB_PATH} 不在 scratch {scratch} 内，拒绝运行。")

    real_db = Path.home() / ".ai-reader-v2" / "data.db"
    freeze = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_md5": md5_file(real_db),
        "db_path": str(real_db),
    }
    print(f"[q0] freeze: db_md5={freeze['db_md5']}")

    conn = sqlite3.connect(str(scratch / "data.db"))

    only = None
    if "--novel" in sys.argv:
        only = sys.argv[sys.argv.index("--novel") + 1]

    out_dir = Path(os.environ.get("Q0_OUT_DIR", str(OUT_DIR)))
    out_dir.mkdir(parents=True, exist_ok=True)

    cost_acc = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    reports: list[dict] = []

    for slug, title, novel_id in resolve_novel_ids(conn):
        if only and slug != only:
            continue
        try:
            report = await run_novel(
                conn, slug, title, novel_id, freeze, cost_acc, out_dir
            )
        except Exception as e:  # AC10：单本失败不阻塞其他
            print(f"[q0] {title} FAILED: {e}")
            report = {
                "slug": slug, "title": title, "novel_id": novel_id,
                "freeze": {"db_md5": freeze["db_md5"], "chapter_facts_rows": None},
                "error": str(e),
            }
        reports.append(report)
        (out_dir / f"{slug}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / f"{slug}.md").write_text(render_novel_md(report), encoding="utf-8")

    conn.close()

    # M6（FR-3.4）：FR-1.5 回测 JSON（缺失时离线重算，纯规则不调 LLM）
    m6 = compute_m6(load_m6_eval())
    if m6.get("status") == "ok":
        print(f"[q0][m6] 水浒类型级={m6['shuihu_subtype_accuracy']:.1%} "
              f"西游 mock category={m6['xiyouji_mock_category']:.1%}")
    else:
        print("[q0][m6] 缺失")

    (out_dir / "summary.md").write_text(
        render_summary_md(reports, freeze, cost_acc, m6), encoding="utf-8"
    )
    print(f"\n[q0] done. 总成本 ≈ ${cost_acc['cost_usd']:.4f} → {out_dir}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
