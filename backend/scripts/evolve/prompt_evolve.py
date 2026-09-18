"""GeoEvolve 阶段 3 —— prompt 级自进化（GEPA 模式）。

设计见 README.md 阶段 3 节。要点：
  - 变异面：extraction_system.txt 的"地点提取规则"段（SECTION_BEGIN~SECTION_END
    标记之间），每轮只改这一段；prompt_state.json 存 original_section 与
    override_section，APPLY=段落替换，回退/自愈=按 state 重渲染，逐字节还原
  - 冻结基准：fixtures/stage3_chapters.json（章节子集）+ stage3_t_set.json
    （T 集,DeepSeek 扫描一次性冻结）+ stage3_e0_baseline.json（原 prompt 双跑
    E0 + 噪声底 + judge 基线）——三者均入 frozen_manifest
  - 评估分层：快速层=冻结章节子集重抽 → prompt.recall / count_inflation /
    generic_rate；确认层=过门禁后 judge 抽检新增地名（冻结 rubric）
  - 提议器：DeepSeek(temp=0) + 冻结 propose_s3.txt；输出严格 JSON 校验；
    anti-hack 检测新增文本不得含 golden fixture 地名（原段落已有示例除外）
  - LLM 调用逐次 LlmBudget.charge()，成本按 token 累计
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import quality_dashboard as qd  # noqa: E402 冻结评估器:复用 deepseek_chat/parse_llm_json/定价常量

PROMPT_FILE = _BACKEND_DIR / "src" / "extraction" / "prompts" / "extraction_system.txt"
STATE_PATH = _EVOLVE_DIR / "prompt_state.json"
FIXTURES_DIR = _EVOLVE_DIR / "fixtures"
CHAPTERS_FIXTURE = FIXTURES_DIR / "stage3_chapters.json"
T_SET_FIXTURE = FIXTURES_DIR / "stage3_t_set.json"
E0_FIXTURE = FIXTURES_DIR / "stage3_e0_baseline.json"
PROPOSE_PROMPT = _EVOLVE_DIR / "prompts" / "propose_s3.txt"
JUDGE_PROMPT = _EVOLVE_DIR / "prompts" / "judge_spotcheck_s3.txt"
EXTRACT_USER_TEMPLATE = _EVOLVE_DIR / "prompts" / "extract_user_s3.txt"

# 变异段落定界（extracton_system.txt 内）
SECTION_BEGIN = "## 地点提取规则（locations）"
SECTION_END = "## 空间关系提取规则"

INNER = ["xiyouji", "honglou", "shuihu"]
CONCURRENCY = 5

# 快速层护栏常量（口径随 eval_policy v3 预注册）
MAX_SECTION_LEN_RATIO = 2.5   # 新段落长度上限（相对原段落）
JUDGE_MIN_SUPPORTED = 0.7     # judge 抽检新增地名 supported 率下限
JUDGE_NEW_NAMES_SAMPLE = 12   # judge 抽检的新增地名上限


# ── 段落手术（APPLY 隔离：逐字节可还原）─────────────────────────────

class SectionError(ValueError):
    """段落定界失败（标记缺失/重复）。"""


def split_section(text: str) -> tuple[str, str, str]:
    """把文件切成 (head, section, tail)。section 从 BEGIN 行起到 END 标记前。"""
    begin = text.find(SECTION_BEGIN)
    end = text.find(SECTION_END)
    if begin < 0 or end < 0 or end <= begin:
        raise SectionError("段落标记缺失或顺序异常")
    if text.find(SECTION_BEGIN, begin + 1) >= 0 or text.find(SECTION_END, end + 1) >= 0:
        raise SectionError("段落标记不唯一")
    return text[:begin], text[begin:end], text[end:]


def replace_section(file_text: str, new_section: str) -> str:
    """用 new_section 替换变异段（幂等;原段或已变异段都能换）。"""
    head, _section, tail = split_section(file_text)
    if not new_section.endswith("\n"):
        new_section += "\n"
    return head + new_section + tail


def load_state(path: Path | None = None) -> dict:
    """prompt 变异状态（单一事实源）。"""
    path = path or STATE_PATH
    if not path.exists():
        original = split_section(PROMPT_FILE.read_text(encoding="utf-8"))[1]
        try:
            file_rel = str(PROMPT_FILE.relative_to(_REPO_ROOT))
        except ValueError:
            file_rel = str(PROMPT_FILE)
        return {"version": 1, "updated_at": None,
                "file": file_rel,
                "original_section": original,
                "override_section": None,
                "history": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def current_section(state: dict) -> str:
    return state.get("override_section") or state["original_section"]


def render_file(state: dict) -> str:
    return replace_section(PROMPT_FILE.read_text(encoding="utf-8"),
                           current_section(state))


def apply_section(state: dict, new_section: str,
                  path: Path | None = None) -> None:
    """APPLY：把 new_section 写入 prompt 文件（原子写）。"""
    path = path or PROMPT_FILE
    content = replace_section(path.read_text(encoding="utf-8"), new_section)
    tmp = path.with_suffix(".txt.evolve-tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def heal_prompt_file(state: dict, path: Path | None = None) -> bool:
    """启动自愈：文件段落偏离 state 时重渲染。返回是否修复。"""
    path = path or PROMPT_FILE
    text = path.read_text(encoding="utf-8")
    _head, file_section, _tail = split_section(text)
    expected = current_section(state)
    if not expected.endswith("\n"):
        expected += "\n"
    if file_section != expected:
        apply_section(state, expected, path)
        return True
    return False


# ── LLM 调用（复用冻结评估器的通道，预算逐次 charge）─────────────────

async def llm_call(system: str, user: str, tag: str, cost_acc: dict,
                   llm_budget) -> str:
    """DeepSeek 调用：qd.deepseek_chat 通道 + LlmBudget 硬约束。"""
    if llm_budget is not None:
        llm_budget.charge()  # 超限抛 LlmBudgetExceeded → 该代记失败
    return await qd.deepseek_chat(system, user, tag, cost_acc)


# ── 快速层：冻结子集重抽 ────────────────────────────────────────────

def load_genre_hints(db_path: Path | None = None) -> dict[str, str]:
    """各内层小说的 genre（world_structures.novel_genre_hint，冻结 DB 只读）。"""
    import sqlite3

    db_path = db_path or (Path.home() / ".ai-reader-v2" / "data.db")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = {}
        ids = {slug: nid for slug, _t, nid in qd.resolve_novel_ids(conn)}
        for slug in INNER:
            row = conn.execute(
                "SELECT structure_json FROM world_structures WHERE novel_id=?",
                (ids[slug],)).fetchone()
            ws = json.loads(row[0]) if row else {}
            out[slug] = ws.get("novel_genre_hint") or ""
        return out
    finally:
        conn.close()


def build_system_prompt(section_text: str, genre: str) -> str:
    """用候选段落渲染完整 system prompt（生产模板的 {genre_context}/{context} 填法）。"""
    from src.extraction.chapter_fact_extractor import _GENRE_CONTEXT

    template = PROMPT_FILE.read_text(encoding="utf-8")
    rendered = replace_section(template, section_text)
    return (rendered
            .replace("{genre_context}", _GENRE_CONTEXT.get(genre, ""))
            .replace("{context}", "（本评估为单章独立抽取，无前序上下文）"))


def parse_extracted_names(raw: str) -> list[str]:
    """从 LLM 输出稳健解析 locations 名清单。"""
    try:
        data = qd.parse_llm_json(raw)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for loc in data.get("locations") or []:
        if isinstance(loc, dict):
            name = (loc.get("name") or "").strip()
            if name:
                out.append(name)
        elif isinstance(loc, str) and loc.strip():
            out.append(loc.strip())
    return out


async def extract_subset(system_by_slug: dict[str, str], chapters_fixture: dict,
                         cost_acc: dict, llm_budget,
                         db_path: Path | None = None) -> dict[str, dict[int, list[str]]]:
    """快速层：对冻结章节子集重跑抽取。返回 {slug: {chapter_num: [names]}}。"""
    import sqlite3

    db_path = db_path or (Path.home() / ".ai-reader-v2" / "data.db")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    try:
        ids = {slug: nid for slug, _t, nid in qd.resolve_novel_ids(conn)}
        tasks = []

        async def one(slug: str, chapter_num: int) -> tuple[str, int, list[str]]:
            row = conn.execute(
                "SELECT title, content FROM chapters WHERE novel_id=? AND chapter_num=?",
                (ids[slug], chapter_num)).fetchone()
            if not row:
                return slug, chapter_num, []
            title, content = row
            user = EXTRACT_USER_TEMPLATE.read_text(encoding="utf-8").format(
                title=qd_titles()[slug], chapter_num=chapter_num,
                chapter_title=title, content=content[: qd.MAX_CONTENT_CHARS])
            async with sem:
                raw = await llm_call(system_by_slug[slug], user,
                                     f"s3-extract {slug} ch{chapter_num}",
                                     cost_acc, llm_budget)
            return slug, chapter_num, parse_extracted_names(raw)

        for slug, chapter_nums in chapters_fixture.items():
            for ch in chapter_nums:
                tasks.append(one(slug, ch))
        results = await asyncio.gather(*tasks)
    finally:
        conn.close()
    out: dict[str, dict[int, list[str]]] = {slug: {} for slug in chapters_fixture}
    for slug, ch, names in results:
        out[slug][ch] = names
    return out


def qd_titles() -> dict[str, str]:
    return {slug: title for slug, title, _ in qd.NOVELS}


def fast_layer_metrics(extracted: dict[str, dict[int, list[str]]],
                       t_set: dict, e0: dict,
                       chapters_fixture: dict | None = None) -> dict[str, dict]:
    """prompt.recall / count_inflation / generic_rate（纯函数）。

    recall 的 T 只取快速层章节子集内的名字（T fixture 含全部 10 个抽样章，
    超出子集的名字不可达，计入分母会系统性压低 recall）。
    """
    from src.extraction.fact_validator import _is_generic_location

    out = {}
    for slug in INNER:
        e_names = {n for names in extracted.get(slug, {}).values() for n in names}
        if chapters_fixture is not None:
            t_names = {n for ch in chapters_fixture[slug]
                       for n in t_set[slug]["chapters"].get(str(ch), [])}
        else:
            t_names = {n for names in t_set[slug]["chapters"].values() for n in names}
        e0_names = set(e0[slug]["names"])
        recall = len(t_names & e_names) / len(t_names) if t_names else None
        inflation = len(e_names) / len(e0_names) if e0_names else None
        generic_hits = sum(1 for n in e_names if _is_generic_location(n) is not None)
        generic_rate = generic_hits / len(e_names) if e_names else None
        out[slug] = {"prompt.recall": recall,
                     "prompt.count_inflation": inflation,
                     "prompt.generic_rate": generic_rate,
                     "e_size": len(e_names)}
    return out


# ── 确认层：judge 抽检（冻结 rubric，新增地名为主）────────────────────

async def judge_spotcheck(extracted: dict[str, dict[int, list[str]]],
                          e0: dict, cost_acc: dict, llm_budget,
                          db_path: Path | None = None) -> dict:
    """对 E'∖E0 的新增地名抽样裁定。返回 {supported_rate, verdicts}。"""
    import random
    import sqlite3

    db_path = db_path or (Path.home() / ".ai-reader-v2" / "data.db")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        ids = {slug: nid for slug, _t, nid in qd.resolve_novel_ids(conn)}
        rng = random.Random(42)
        samples: list[tuple[str, int, str]] = []  # (slug, chapter_num, name)
        for slug in INNER:
            e0_names = set(e0[slug]["names"])
            new_by_ch: dict[int, list[str]] = {}
            for ch, names in extracted.get(slug, {}).items():
                fresh = [n for n in names if n not in e0_names]
                if fresh:
                    new_by_ch[ch] = fresh
            for ch, fresh in new_by_ch.items():
                for n in rng.sample(fresh, min(2, len(fresh))):
                    samples.append((slug, ch, n))
        samples = samples[:JUDGE_NEW_NAMES_SAMPLE]
        verdicts = []
        for slug, ch, name in samples:
            row = conn.execute(
                "SELECT title, content FROM chapters WHERE novel_id=? AND chapter_num=?",
                (ids[slug], ch)).fetchone()
            if not row:
                continue
            user = (f"## 原文（《{qd_titles()[slug]}》第{ch}回《{row[0]}》）\n"
                    f"{row[1][: qd.MAX_CONTENT_CHARS]}\n\n## 抽取地名清单\n{name}")
            raw = await llm_call(JUDGE_PROMPT.read_text(encoding="utf-8"),
                                 user, f"s3-judge {slug} ch{ch} {name}",
                                 cost_acc, llm_budget)
            try:
                data = qd.parse_llm_json(raw)
                for v in data.get("verdicts", []):
                    verdicts.append({"slug": slug, "chapter": ch,
                                     "name": v.get("name"), "verdict": v.get("verdict")})
            except Exception:
                continue
    finally:
        conn.close()
    ok = sum(1 for v in verdicts if v["verdict"] == "ok")
    return {"supported_rate": (ok / len(verdicts)) if verdicts else None,
            "n": len(verdicts), "verdicts": verdicts}


# ── anti-hack：黄金集答案串检测（prompt 适配版）──────────────────────

def load_golden_names() -> set[str]:
    """golden fixture 全部地名与 parent 值（答案串集合）。"""
    names: set[str] = set()
    for p in sorted((_REPO_ROOT / "backend/tests/fixtures").glob("golden_standard_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for loc in d.get("locations", []):
            for key in ("name", "correct_parent"):
                v = loc.get(key)
                if v:
                    names.add(v)
    return names


def anti_hack_prompt_check(new_section: str, original_section: str,
                           golden_names: set[str]) -> list[str]:
    """新增文本中出现的 golden 答案串清单（原段落已有的示例豁免）。空=通过。"""
    return sorted(n for n in golden_names
                  if n in new_section and n not in original_section)


# ── GEPA 反思提议器 ─────────────────────────────────────────────────

def build_failure_trajectory(t_set: dict, extracted: dict[str, dict[int, list[str]]],
                             db_path: Path | None = None,
                             max_samples: int = 12) -> list[dict]:
    """失败样例：T 有而 E' 无的地名 + 原文语境窗口（±80 字）。"""
    import sqlite3

    db_path = db_path or (Path.home() / ".ai-reader-v2" / "data.db")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    samples = []
    try:
        ids = {slug: nid for slug, _t, nid in qd.resolve_novel_ids(conn)}
        for slug in INNER:
            e_names = {n for ns in extracted.get(slug, {}).values() for n in ns}
            for ch, t_names in sorted(t_set[slug]["chapters"].items(),
                                      key=lambda kv: int(kv[0])):
                missed = [n for n in t_names if n not in e_names]
                if not missed:
                    continue
                row = conn.execute(
                    "SELECT content FROM chapters WHERE novel_id=? AND chapter_num=?",
                    (ids[slug], int(ch))).fetchone()
                content = row[0] if row else ""
                for name in missed[:3]:
                    idx = content.find(name)
                    window = (content[max(0, idx - 80): idx + len(name) + 80]
                              if idx >= 0 else "")
                    samples.append({"novel": slug, "chapter": int(ch),
                                    "missed": name, "context": window})
    finally:
        conn.close()
    return samples[:max_samples]


def validate_proposal(parsed: dict, original_section: str) -> str:
    """校验提议器输出，返回 new_section。畸形即抛 ProposalError。"""
    if not isinstance(parsed, dict):
        raise ProposalError("输出不是 JSON 对象")
    hypothesis = parsed.get("hypothesis")
    new_section = parsed.get("new_section")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ProposalError("缺 hypothesis")
    if not isinstance(new_section, str) or not new_section.strip():
        raise ProposalError("缺 new_section")
    if not new_section.startswith(SECTION_BEGIN):
        raise ProposalError("new_section 必须以原段落标题行开头")
    if SECTION_END in new_section:
        raise ProposalError("new_section 越界包含下一段标记")
    if len(new_section) > MAX_SECTION_LEN_RATIO * len(original_section):
        raise ProposalError(
            f"new_section 超长({len(new_section)} > {MAX_SECTION_LEN_RATIO}×原段)")
    if new_section.strip() == original_section.strip():
        raise ProposalError("new_section 与原段落相同(恒等)")
    # 四类禁止规则的语义锚点必须保留
    for anchor in ("不要提取泛化地理词", "不要提取相对位置词", "概念不算地名"):
        if anchor not in new_section:
            raise ProposalError(f"删除/改写了禁止规则锚点: {anchor}")
    return new_section


class ProposalError(ValueError):
    """提议器输出畸形/越界。"""


class GEPAReflectOperator:
    """GEPA 模式提议器：DeepSeek(temp=0) 读失败轨迹反思改写地点规则段。"""

    name = "prompt_gepa_reflect"

    def __init__(self, llm_budget=None, cost_acc: dict | None = None):
        self.llm_budget = llm_budget
        self.cost_acc = cost_acc if cost_acc is not None else {
            "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

    async def propose_async(self, genome: dict, context: dict) -> dict:
        section = context["current_section"]
        failures = context.get("failure_trajectory", [])
        history = context.get("prompt_history", [])
        guards = context.get("guard_snapshot", {})
        base_payload = json.dumps({
            "current_section": section,
            "failure_trajectory": failures,
            "guard_snapshot": guards,
            "history": history[-5:],
            "stagnation_note": context.get("stagnation_note"),
            "downgrade_lessons": context.get("downgrade_lessons"),
        }, ensure_ascii=False, indent=2)
        # 元层(§4.4):提议器输入快照哈希,供回放器校验轨迹一致性
        payload_hash = hashlib.sha256(base_payload.encode()).hexdigest()

        # 带反馈重试：anti-hack 命中或输出畸形时,把被拒原因喂回提议器重写
        # (被拒的变异本身已按 §6.3 记录;重写产生的是新提议)
        feedback = ""
        anti_hack_hits: list[str] = []
        errors: list[str] = []
        hypothesis = ""
        for attempt in range(3):
            raw = await llm_call(PROPOSE_PROMPT.read_text(encoding="utf-8"),
                                 base_payload + feedback,
                                 f"s3-propose try{attempt + 1}",
                                 self.cost_acc, self.llm_budget)
            try:
                parsed = qd.parse_llm_json(raw)
                new_section = validate_proposal(parsed, context["original_section"])
                hypothesis = parsed["hypothesis"]
            except Exception as err:  # 解析/校验失败都带反馈重试
                errors.append(f"try{attempt + 1}: {type(err).__name__}: {err}")
                feedback = (f"\n\n# 上次输出被拒（{err}）。请严格按输出契约重试。\n")
                continue
            anti_hack_hits = anti_hack_prompt_check(
                new_section, context["original_section"],
                context.get("golden_names", set()))
            if not anti_hack_hits:
                return {
                    "operator": self.name,
                    "hypothesis": hypothesis,
                    "genome_diff": {"prompt.section": "extraction_system.txt#locations"},
                    "new_section": new_section,
                    "attempts": attempt + 1,
                    "retry_errors": errors,
                    "context_hash": payload_hash,
                }
            feedback = (f"\n\n# 上次输出被拒：新增文本含有禁用具体地名 "
                        f"{anti_hack_hits}（它们属于评测基准答案,写入 prompt 即作弊）。"
                        f"重写时不得包含这些及任何具体作品的具体地名,规则必须通用。\n")
        return {
            "operator": self.name,
            "hypothesis": hypothesis,
            "genome_diff": {},
            "rejected_anti_hack": anti_hack_hits,
            "attempts": 3,
            "retry_errors": errors,
            "context_hash": payload_hash,
        }
