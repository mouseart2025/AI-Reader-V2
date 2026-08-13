#!/usr/bin/env python3
"""Q0 质量数据人工核对工具 — 极简本地服务器。

用途：为 scripts/qa-review/index.html 提供：
  - GET  /                        静态页面（index.html / app.js / style.css）
  - GET  /api/novels              全部可用 novel slug + 状态
  - GET  /api/data?novel=<slug>   读取该小说的 QA/校准待核对样本（合并）
  - POST /api/annotate            写回该 novel 的人工标注（原位保存 + 自动备份 .bak）
  - POST /api/prefill             批量生成 AI 预填（{uid: {value,confidence}}，不覆盖人工标注）。
                                  model: deepseek（m3+m2，存 .prefill-<slug>.json）
                                       | qwen（仅 m2，存 .prefill-qwen-<slug>.json）| both（默认）。
                                  m2 预填带原文证据（冻结 DB ±120 字窗口）。
  - POST /api/prefill-clear       清除某 novel 的预填缓存

数据目录默认指向 ai-reader-internal/analysis/quality-baseline-2026-08，
可用环境变量 QA_DATA_DIR 覆盖。

用法：
    cd scripts/qa-review && python serve.py
    浏览器打开 http://localhost:8273
"""

import json
import os
import re
import shutil
import sqlite3
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SERVE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path(
    os.environ.get(
        "QA_DATA_DIR",
        os.path.expanduser(
            "~/Baiduyun/AISoul/ai-reader-internal/analysis/quality-baseline-2026-08"
        ),
    )
).resolve()
PORT = int(os.environ.get("QA_PORT", "8273"))

# Novel 展示名 → slug（与 quality_dashboard.py NOVELS 顺序一致）
NOVELS: list[tuple[str, str]] = [
    ("xiyouji", "西游记"),
    ("honglou", "红楼梦"),
    ("shuihu", "水浒传"),
    ("sanguo", "三国演义"),
    ("fengshen", "封神演义"),
]
TITLE = dict(NOVELS)


# ── 数据读写 ───────────────────────────────────────────────────


def data_path(slug: str) -> Path:
    return DEFAULT_DATA_DIR / f"{slug}.json"


def calib_path(slug: str) -> Path:
    return DEFAULT_DATA_DIR / f"{slug}.calibration-sample.json"


def _backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))


# 复合全称预筛：子名 = 父名 + 地点通名后缀（如「西天」+「大雷音寺」），
# 疑似把「父+通名」的单一复合专名当成了父子地理包含。缺中间层（如灵山）时
# 无法仅凭词形确证，故仅作「待确认」建议（预检勾选，人工可取消）。
_PLACE_SUFFIX = (
    "寺", "山", "洞", "宫", "亭", "海", "河", "关", "寨", "城", "州", "国",
    "府", "峰", "谷", "林", "村", "庄", "殿", "楼", "门", "桥", "观", "台",
)


def detect_composite(parent: str, child: str, rules) -> bool:
    """保守复合全称检测：命中 R1 且 子名=父名+<≥2字 以地点通名结尾>。"""
    if not parent or not child:
        return False
    r1 = "R1_child_name_contains_parent" in (rules or [])
    if not r1:
        return False
    if not child.startswith(parent) or len(child) <= len(parent):
        return False
    remainder = child[len(parent):]
    if len(remainder) < 2:
        return False
    return any(remainder.endswith(s) for s in _PLACE_SUFFIX)


# 泛称 / 非实体节点预筛：整名无专名成分（纯通名 / 纯方位 / 概念词），
# 如「高山」「东城」。这类节点无法作为独立地理实体建立父子关系。
_TONG_NAME = set("城乡山寺宫观殿台楼馆庄洞谷林水门口坊营帐库院堂厅轩榭阁廊亭园")
_GENERIC_EXACT = {
    "高山", "城门", "天宫", "庄院", "村舍", "松林", "小河", "东土", "后园",
    "前殿", "县衙", "丹墀", "密林", "花园", "山洞", "寺庙", "大殿", "后殿",
    "亭台", "院落", "馆驿", "营盘", "城楼", "城池", "东城", "西城", "南城",
    "北城", "城中", "城外", "山门", "洞口", "路口", "街头", "坡上", "途中",
    "田间", "东南", "西南", "东北", "西北", "中央", "附近", "池上", "池中",
    "园中", "园内", "园子", "大堂", "城南", "花阴", "西院", "都中", "平台",
    "大内", "仪门", "后廊", "帐房", "山石", "北府",
}


def is_generic_entity(name: str) -> bool:
    """判定单个地点名是否为纯泛称/非实体。"""
    n = (name or "").strip()
    if not n:
        return False
    if n in _GENERIC_EXACT:
        return True
    if len(n) == 1 and n in _TONG_NAME:
        return True
    if n[-1] in "上中下外内边旁里头和" and len(n) <= 3:
        prefix = n[:-1]
        if all(ch in "池园花林城山门洞巷廊街院东西南北中后前大" for ch in prefix):
            return True
    return False


def detect_entity_issue(parent: str, child: str) -> bool:
    """泛称/非实体节点预筛：父或子一方为纯泛称即标记待确认。"""
    return is_generic_entity(parent) or is_generic_entity(child)


def _levenshtein(a: str, b: str) -> int:
    """小字符串编辑距离（用于异名检测）。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return abs(la - lb)
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, lb + 1):
            tmp = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = tmp
    return dp[lb]


# M2 通名残留/待挂父级预筛：纯泛称的建筑/场所名（无专名成分），
# 在原文中指真实建筑但单拎不是专名，应挂所属父级。如「大雄宝殿」「厨房」。
_BARE_GENERIC_PLACE = {
    "大雄宝殿", "大殿", "前殿", "后殿", "正殿", "中殿", "宝殿", "佛殿", "禅堂",
    "法堂", "斋堂", "经堂", "讲堂", "草堂", "厅堂", "正厅", "大厅", "厅", "堂",
    "厨房", "库房", "厢房", "客房", "正房", "偏房", "耳房", "书房", "后房",
    "帐房", "库", "仓", "花园", "后花园", "御花园", "御园", "园子", "庭院",
    "院落", "院子", "园", "山门", "后门", "正门", "旁门", "侧门", "角门",
    "宫门", "城门", "城楼", "庄门", "大门", "小门", "中门", "牌楼", "月台",
    "宫殿", "宫室", "宫", "殿", "楼阁", "亭台", "台", "亭", "桥", "井", "池",
    "河", "寺", "庵", "庙", "观", "寺院", "寺庙", "土地庙", "祠堂", "祭坛",
    "坛", "三宫六院", "三市六街", "四牌楼",
}


def is_bare_generic(name: str) -> bool:
    """M2：纯泛称建筑/场所名（无专名成分）。"""
    n = (name or "").strip()
    if not n:
        return False
    if n in _BARE_GENERIC_PLACE:
        return True
    if len(n) == 1 and n in "城乡山寺宫观殿台楼馆庄洞谷林水门口坊":
        return True
    return False


def detect_alias_variant(parent: str, child: str) -> bool:
    """异名/拼写变体预筛：同一实体的异写、错别字、简称/全称变体被拆成两节点。

    判据：长度差 <=1 且编辑距离 <=1，且不是纯前缀从属关系（父≠子的前缀截断），
    剔除「父+后缀」的正常包含从属。例：南赡部洲/南瞻部洲、乌斯藏国/乌斯藏界。
    """
    p, ch = (parent or "").strip(), (child or "").strip()
    if not p or not ch or p == ch:
        return False
    # 前缀从属（如 东海/东海龙宫）是包含关系，不是异名，除非长度相同
    if ch.startswith(p) or p.startswith(ch):
        if len(p) != len(ch):
            return False
    if abs(len(p) - len(ch)) > 1:
        return False
    return _levenshtein(p, ch) <= 1


def merge_samples(slug: str) -> dict:
    """读取并对齐一部小说的全部待核对项。

    每项带稳定 uid，命名规则：
      m3:      a<idx>      —— qa_samples.m3[idx]
      m2_qa:   q<idx>      —— qa_samples.m2[idx]
      m2_cal:  c<idx>      —— calibration.samples[idx]
    M3 项 issue_type 为聚合标记（'|' 分隔，可多token）：
      missing_intermediate 复合全称/缺中间层
      entity_issue         泛称/非实体节点
    另带 *_suggested 布尔指示预筛建议。
    """
    robot = json.loads(data_path(slug).read_text())
    qa = robot.get("qa_samples") or {}

    m3 = []
    for i, c in enumerate(qa.get("m3") or []):
        persisted = c.get("issue_type") or ""
        comp_sugg = detect_composite(c.get("parent", ""), c.get("child", ""), c.get("rules"))
        ent_sugg = detect_entity_issue(c.get("parent", ""), c.get("child", ""))
        alias_sugg = detect_alias_variant(c.get("parent", ""), c.get("child", ""))
        # issue_type：持久化优先；无持久化时用预筛建议
        tokens = set(t for t in persisted.split("|") if t)
        if not tokens:
            if comp_sugg:
                tokens.add("missing_intermediate")
            if ent_sugg:
                tokens.add("entity_issue")
            if alias_sugg:
                tokens.add("alias_variant")
        m3.append({
            "uid": f"a{i}", "type": "m3",
            "parent": c.get("parent", ""), "child": c.get("child", ""),
            "rules": c.get("rules", []), "evidence": c.get("evidence", ""),
            "value": c.get("human_verdict"),
            "issue_type": "|".join(sorted(tokens)),
            "issue_suggested": bool(comp_sugg),
            "entity_suggested": bool(ent_sugg),
            "alias_suggested": bool(alias_sugg),
        })

    m2qa = []
    for i, c in enumerate(qa.get("m2") or []):
        persisted = c.get("issue_type") or ""
        tokens = set(t for t in persisted.split("|") if t)
        if not tokens and is_bare_generic(c.get("name", "")):
            tokens.add("generic_attachment")
        m2qa.append({
            "uid": f"q{i}", "type": "m2_qa", "name": c.get("name", ""),
            "chapters": c.get("chapters", []), "value": c.get("human_verdict"),
            "issue_type": "|".join(sorted(tokens)),
            "generic_suggested": bool(is_bare_generic(c.get("name", ""))),
        })

    m2cal = []
    cp = calib_path(slug)
    if cp.exists():
        cal = json.loads(cp.read_text())
        for i, c in enumerate(cal.get("samples") or []):
            persisted = c.get("issue_type") or ""
            tokens = set(t for t in persisted.split("|") if t)
            if not tokens and is_bare_generic(c.get("name", "")):
                tokens.add("generic_attachment")
            m2cal.append({
                "uid": f"c{i}", "type": "m2_cal", "name": c.get("name", ""),
                "chapters": c.get("chapters", []), "value": c.get("is_valid"),
                "issue_type": "|".join(sorted(tokens)),
                "generic_suggested": bool(is_bare_generic(c.get("name", ""))),
            })

    return {
        "items": m3 + m2qa + m2cal,
        "counts": {"m3": len(m3), "m2_qa": len(m2qa), "m2_cal": len(m2cal)},
    }


def write_annotation(slug: str, uid: str, value, field: str = "human_verdict") -> bool:
    """按 uid 将 value 写回对应字段（原位 + 备份）。

    field: human_verdict（m3/m2_qa）| is_valid（m2_cal）| issue_type（m3 复合全称标记）
    """
    robot_path = data_path(slug)
    _backup(robot_path)

    k = uid[:1]
    idx = int(uid[1:])

    if k == "a":  # m3
        robot = json.loads(robot_path.read_text())
        target = robot["qa_samples"]["m3"][idx]
        if field == "issue_type":
            if value:  # 非空才写入；空表示清除该标记
                target["issue_type"] = value
            else:
                target.pop("issue_type", None)
        else:
            target["human_verdict"] = value
        robot_path.write_text(json.dumps(robot, ensure_ascii=False, indent=2), "utf-8")
        return True

    if k == "q":  # m2_qa
        robot = json.loads(robot_path.read_text())
        target = robot["qa_samples"]["m2"][idx]
        if field == "issue_type":
            if value:
                target["issue_type"] = value
            else:
                target.pop("issue_type", None)
        else:
            target["human_verdict"] = value
        robot_path.write_text(json.dumps(robot, ensure_ascii=False, indent=2), "utf-8")
        return True

    if k == "c":  # m2_cal
        cp = calib_path(slug)
        if not cp.exists():
            return False
        _backup(cp)
        cal = json.loads(cp.read_text())
        target = cal["samples"][idx]
        if field == "issue_type":
            if value:
                target["issue_type"] = value
            else:
                target.pop("issue_type", None)
        else:
            target["is_valid"] = value
        cp.write_text(json.dumps(cal, ensure_ascii=False, indent=2), "utf-8")
        return True

    return False


def _prefill_path(slug: str, model: str = "deepseek") -> Path:
    name = f".prefill-{slug}.json" if model == "deepseek" else f".prefill-{model}-{slug}.json"
    return DEFAULT_DATA_DIR / name


def load_prefill(slug: str, model: str = "deepseek") -> dict:
    """读取已生成的预填结果（若存在）。"""
    p = _prefill_path(slug, model)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_prefill(slug: str, prefill: dict, model: str = "deepseek") -> Path:
    p = _prefill_path(slug, model)
    p.write_text(json.dumps(prefill, ensure_ascii=False, indent=2), "utf-8")
    return p


# ── 原文证据提取（M2，只读冻结 DB 副本）────────────────────────

_EVIDENCE_WINDOW = 120


def _load_chapter_map(novel_id: str) -> dict:
    """从冻结 DB 的临时副本读取 {chapter_num: content}（只读；DB 缺/无章节→空 dict）。"""
    live_db = os.path.expanduser("~/.ai-reader-v2/data.db")
    if not novel_id or not os.path.exists(live_db):
        return {}
    tmp = os.path.join(tempfile.gettempdir(), "qa-serve-data.db")
    if not os.path.exists(tmp):
        shutil.copyfile(live_db, tmp)
    try:
        conn = sqlite3.connect(tmp)
        try:
            rows = conn.execute(
                "SELECT chapter_num, content FROM chapters WHERE novel_id=?", (novel_id,)
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return dict(rows)


def evidence_for(name: str, chap_nums, chapters: dict) -> str:
    """±120 字窗口证据：先按项的 chapters 顺序找首次出现，找不到再全章节升序找。"""
    for cn in list(chap_nums or []) + sorted(chapters):
        content = chapters.get(cn) or ""
        i = content.find(name)
        if i >= 0:
            seg = content[max(0, i - _EVIDENCE_WINDOW): i + len(name) + _EVIDENCE_WINDOW]
            return f"第{cn}回: …" + re.sub(r"\s+", " ", seg).strip() + "…"
    return "(原文未找到)"


def attach_m2_evidence(slug: str, items: list) -> None:
    """为 m2 项（m2_qa/m2_cal）就地挂 evidence_text；DB 无该小说章节时为 (原文未找到)。"""
    targets = [it for it in items if it["type"] in ("m2_qa", "m2_cal")]
    if not targets:
        return
    novel_id = json.loads(data_path(slug).read_text()).get("novel_id")
    chapters = _load_chapter_map(novel_id)
    for it in targets:
        it["evidence_text"] = evidence_for(it["name"], it.get("chapters"), chapters)


# ── LLM 预填（Q3=A）────────────────────────────────────────────


def _backend_env() -> dict:
    """读取 backend/.env 中的 LLM 配置。"""
    env_path = SERVE_DIR.parent.parent / "backend" / ".env"
    cfg = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    api_key = cfg.get("DEEPSEEK_API_KEY") or cfg.get("LLM_API_KEY") or ""
    return {
        "api_key": api_key,
        "base_url": cfg.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        "model": cfg.get("LLM_MODEL", "deepseek-chat"),
        "dashscope_key": cfg.get("DASHSCOPE_API_KEY", ""),
    }


_PROMPT_MAP = {
    "m3": (
        "你是中国古典小说的地理包含关系审核专家。对每个父子对，判断「父地点包含子地点」"
        "这一方向是否正确，并给出置信度。"
        "判定: correct=父确实包含子; wrong=方向颠倒或无包含关系(如两者独立); "
        "uncertain=无法判断。"
        "置信度: 有原文证据且明确=high, 一般把握=medium, 边界/无证据=low。"
        '严格只输出 JSON: {"verdicts":[{"id":0,"value":"correct|wrong|uncertain",'
        '"confidence":"high|medium|low"}]}，id 必须保持输入顺序从 0 递增、不得遗漏、'
        '不得多出、不要解释。'
    ),
    "m2": (
        "你是中国古典小说地点识别专家。根据给出的原文片段，判断该名称在小说中是不是「故事世界里真实存在的专有地点」（任意粒度，从大陆到亭台均可）。\n"
        "判 false 的情形：\n"
        "1. 泛称/通名（如「洞」「山」「人家」、无专名化的「京城」「禅堂」）；\n"
        "2. 韵语、诗词、对仗铺陈中罗列的景物名或前朝典故名（如「大明宫」「华清宫」出现在写景韵文中，叙事中并无此地），判 false；\n"
        "   但名称虽出现在韵文中、却指故事世界真实地点的（如「广寒宫」即月宫），仍判 true。\n"
        "3. 通名/泛称一律 false：名称本身是无专名修饰的通用词（如「花园」「山顶」「东廊」「后宫」「京城」「禅堂」「牌楼」「山门」），即使在叙事中真实出现、人物确实身处其中，也判 false——这类名称应挂到所属专名父级下，不独立成节点；\n"
        "4. 人名、神佛名、器物名被误识别为地点，或原文中并不存在（幻觉），判 false。\n"
        "判 true 的情形：名称为专有名称，且在叙事中作为故事世界真实地点出现（如「五庄观山门」「广寒宫」）。\n"
        "置信度: 证据明确=high, 一般把握=medium, 边界=low。\n"
        '严格只输出 JSON: {"verdicts":[{"id":0,"value":true|false,"confidence":"high|medium|low"}]}，id 必须保持输入顺序从 0 递增、不得遗漏、不得多出、不要解释。'
    ),
}


def prefill_novel(slug: str, model: str = "deepseek") -> dict:
    """对一部小说的待核对项生成预填，返回 {uid: {value, confidence}}。

    model: deepseek（m3+m2，批 25）| qwen（仅 m2，批 15 防截断）。
    m2 项带原文证据（±120 字窗口）送入证据版 prompt；m3 流程不变。
    """
    cfg = _backend_env()
    if model == "qwen":
        if not cfg["dashscope_key"]:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置（检查 backend/.env）")
        chat = lambda system, user: _qwen_chat(cfg["dashscope_key"], system, user)  # noqa: E731
        keys, BATCH = ("m2",), 15
    else:
        if not cfg["api_key"]:
            raise RuntimeError("DEEPSEEK_API_KEY / LLM_API_KEY 未配置（检查 backend/.env）")
        chat = lambda system, user: _deepseek_chat(cfg, system, user)  # noqa: E731
        keys, BATCH = ("m3", "m2"), 25

    data = merge_samples(slug)
    items = data["items"]
    attach_m2_evidence(slug, items)
    result: dict[str, dict] = {}

    # 按类型分批（大 JSON 易截断/漏 id，缩小批提高回填完整率）
    for key in keys:
        chunk_items = [it for it in items if (it["type"] == key) or (it["type"] in ("m2_qa", "m2_cal") and key == "m2")]
        if not chunk_items:
            continue
        for i in range(0, len(chunk_items), BATCH):
            chunk = chunk_items[i:i + BATCH]
            lines = []
            for k, it in enumerate(chunk):
                if it["type"] == "m3":
                    ev = (it.get("evidence") or "")[:160]
                    lines.append(f'{k}. 父={it["parent"]} 子={it["child"]} 证据: {ev}')
                else:
                    ev = it.get("evidence_text") or "(原文未找到)"
                    lines.append(f'{k}. 名称={it["name"]} 原文: {ev}')
            user = f"去重核对以下 {len(chunk)} 条（id 从 0 到 {len(chunk)-1}）：\n" + "\n".join(lines)
            # 解析本批，失败重试一次（temp 0 口径不变）
            verdicts = []
            for attempt in range(2):
                try:
                    raw = chat(_PROMPT_MAP[key], user)
                    parsed = _parse_llm_json(raw)
                    verdicts = parsed.get("verdicts") or []
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 0:
                        print(f"[qa][prefill] WARN {slug} {model}:{key} 批 {i // BATCH + 1} 解析失败，重试: {e}")
                    else:
                        print(f"[qa][prefill] WARN {slug} {model}:{key} 批 {i // BATCH + 1} 重试仍失败: {e}")
            # 按 id 对齐到 chunk：LLM 返回 id=输入序号(0..n-1)，逐条归位；id 缺失/越界则按顺序兜底
            byid: dict[int, dict] = {}
            for v in verdicts:
                try:
                    byid[int(v.get("id"))] = v
                except (TypeError, ValueError):
                    continue
            for k, it in enumerate(chunk):
                v = byid.get(k)
                if v is None:
                    continue
                result[it["uid"]] = {
                    "value": v.get("value"),
                    "confidence": v.get("confidence", "medium"),
                }
    return result


def _deepseek_chat(cfg: dict, system: str, user: str) -> str:
    import httpx
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": 8192,   # 大批 JSON 输出需足够上限，避免截断丢批
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(
        base_url=cfg["base_url"],
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
        timeout=300.0,
    ) as client:
        resp = client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def _qwen_chat(dashscope_key: str, system: str, user: str) -> str:
    """第二模型独立判定：阿里云百炼 Qwen（OpenAI 兼容接口）。"""
    import httpx
    resp = httpx.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        headers={"Authorization": f"Bearer {dashscope_key}", "Content-Type": "application/json"},
        json={
            "model": "qwen3-235b-a22b-instruct-2507",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 16384,
        },
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _parse_llm_json(raw: str) -> dict:
    import re
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no json brace")
    import json as _json
    return _json.loads(text[start:end + 1])


# ── 极简 HTTP 服务器 ────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name: str):
        ext = Path(name).suffix
        mime = {"": "text/html", ".html": "text/html", ".js": "application/javascript",
                ".css": "text/css"}.get(ext, "application/octet-stream")
        target = (SERVE_DIR / name).resolve()
        if not str(target).startswith(str(SERVE_DIR)) or not target.exists():
            return self._send_json({"error": "not found"}, 404)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path)
        if p.path in ("/", "/index.html", "/app.js", "/style.css"):
            return self._send_file(p.path.lstrip("/") or "index.html")
        if p.path == "/api/novels":
            out = []
            for slug, title in NOVELS:
                rp = data_path(slug)
                out.append({"slug": slug, "title": title, "exists": rp.exists()})
            return self._send_json({"novels": out})
        if p.path == "/api/data":
            q = parse_qs(p.query)
            slug = q.get("novel", [""])[0]
            if slug not in TITLE or not data_path(slug).exists():
                return self._send_json({"error": "no data"}, 404)
            data = merge_samples(slug)
            data["title"] = TITLE[slug]
            data["prefill"] = load_prefill(slug)
            data["prefill_qwen"] = load_prefill(slug, "qwen")
            attach_m2_evidence(slug, data["items"])  # 卡片展示用原文上下文
            return self._send_json(data)
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if p.path == "/api/annotate":
            try:
                ok = write_annotation(
                    body.get("slug", ""), body.get("uid", ""),
                    body.get("value"), body.get("field", "human_verdict"),
                )
            except Exception as e:  # noqa: BLE001
                return self._send_json({"error": str(e)}, 500)
            return self._send_json({"ok": ok})
        if p.path == "/api/prefill":
            slug = body.get("slug", "")
            model = body.get("model", "both")
            if slug not in TITLE or not data_path(slug).exists():
                return self._send_json({"error": "no data"}, 404)
            models = ["deepseek", "qwen"] if model == "both" else [model]
            cfg = _backend_env()
            prefilled: dict[str, int] = {}
            skipped: list[str] = []
            for m in models:
                if m == "deepseek" and not cfg["api_key"]:
                    skipped.append("deepseek (DEEPSEEK_API_KEY/LLM_API_KEY 未配置)")
                    continue
                if m == "qwen" and not cfg["dashscope_key"]:
                    skipped.append("qwen (DASHSCOPE_API_KEY 未配置)")
                    continue
                try:
                    prefill = prefill_novel(slug, m)
                    save_prefill(slug, prefill, m)
                    prefilled[m] = len(prefill)
                except Exception as e:  # noqa: BLE001
                    skipped.append(f"{m} ({e})")
            if not prefilled and skipped:
                return self._send_json({"error": "; ".join(skipped)}, 500)
            return self._send_json({"ok": True, "prefilled": prefilled, "skipped": skipped})
        if p.path == "/api/prefill-clear":
            slug = body.get("slug", "")
            p = DEFAULT_DATA_DIR / f".prefill-{slug}.json"
            if p.exists():
                p.unlink()
            return self._send_json({"ok": True})
        return self._send_json({"error": "not found"}, 404)


def main():
    print(f"Q0 数据核对工具  http://localhost:{PORT}")
    print(f"数据目录: {DEFAULT_DATA_DIR}")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
