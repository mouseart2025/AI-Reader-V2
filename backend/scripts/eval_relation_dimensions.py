"""FR-1.5 水浒/西游关系维度回测(增量评测,离线、CI 可复跑)。

消费两类标注产物:
  1. 水浒 silver 小样  backend/data/review/shuihu_relations_silver.json
     (silver_draft 草稿,未经人工复核,不得作为论文绝对数字)
  2. 西游 gold        backend/data/review/xiyouji_relations.json(冻结,只读)

口径声明(重要):
  * 冻结的 demo/抽取产物中不含三维字段(polarity/rel_subtype/closeness),
    重新抽取需调用 LLM。本脚本**不打任何真实 LLM**:水浒/西游的"新口径系统
    输出"用确定性规则映射 LEGACY_TYPE_TO_SUBTYPE 从旧单标签 mock 得到,
    属于离线近似,报告中明确标注为 mock 口径。
  * 水浒 28% 基线为论文数字,本 repo 内无法复跑(风险表已记录);本脚本产出
    的是 silver 小样上的新口径测量,两者**不可直接比较**。
  * 水浒"旧单标签口径"对照基线:旧系统 relation_type → 旧六类 category
    (normalize_relation_type + classify_relation_category),与标注
    rel_subtype 派生的 category 对比。

指标(均为纯函数,可单测):
  类型级准确率  — 系统 rel_subtype(mock)vs 标注 rel_subtype
  极性准确率    — 系统 polarity(规则 mock)vs 标注 polarity(有标注时)
  派生一致率    — derive_category_from_dimensions(标注 subtype)与旧路径
                  category 的一致程度(水浒)/ 维度派生 category 与旧路径
                  category 的一致程度(西游,同一 system_type 两条路径)
  旧口径对照    — 水浒:旧六类 category 准确率;西游:旧单标签口径基线 vs
                  新口径 mock,验收"西游类型级不低于旧单标签口径基线"

Usage:
    cd backend && .venv/bin/python scripts/eval_relation_dimensions.py
    .venv/bin/python scripts/eval_relation_dimensions.py --out audit_reports/relation_dimensions_eval.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from src.services.relation_utils import (  # noqa: E402
    classify_relation_category,
    derive_category_from_dimensions,
    normalize_relation_type,
)

SCHEMA_VERSION = "relation-dimension-schema-v1(2026-08-26 冻结)"

SHUIHU_SILVER_PATH = _BACKEND_DIR / "data" / "review" / "shuihu_relations_silver.json"
XIYOUJI_GOLD_PATH = _BACKEND_DIR / "data" / "review" / "xiyouji_relations.json"
DEFAULT_REPORT_PATH = _BACKEND_DIR / "audit_reports" / "relation_dimensions_eval.md"

# 水浒类型级准确率验收阈值(FR-1.5;28% 基线引自论文,不在本 repo 复跑)
SHUIHU_SUBTYPE_TARGET = 0.55

# ── 旧单标签 → rel_subtype 规则映射(离线 mock,替代 LLM 重抽取)────────
# 键为 normalize_relation_type 归一化后的旧类型;未命中一律 "其他"(schema
# 兜底槽位)。夫妻按 schema v1 §3 归 辈分-亲属(平辈姻亲)。
LEGACY_TYPE_TO_SUBTYPE: dict[str, str] = {
    # 辈分-亲属(血缘/姻亲,含夫妻)
    "父子": "辈分-亲属", "父女": "辈分-亲属", "母子": "辈分-亲属", "母女": "辈分-亲属",
    "兄弟": "辈分-亲属", "兄妹": "辈分-亲属", "姐弟": "辈分-亲属", "姐妹": "辈分-亲属",
    "叔侄": "辈分-亲属", "祖孙": "辈分-亲属", "婆媳": "辈分-亲属",
    "表亲": "辈分-亲属", "堂亲": "辈分-亲属", "甥舅": "辈分-亲属",
    "姑侄": "辈分-亲属", "翁媳": "辈分-亲属", "妯娌": "辈分-亲属",
    "姑嫂": "辈分-亲属", "连襟": "辈分-亲属", "嫂叔": "辈分-亲属",
    "嫡庶": "辈分-亲属", "亲家": "辈分-亲属", "亲戚": "辈分-亲属",
    "族人": "辈分-亲属", "夫妻": "辈分-亲属",
    "翁婿": "辈分-亲属", "郎舅": "辈分-亲属",
    # 结拜 / 婚恋 / 爱慕
    "结拜兄弟": "结拜",
    "恋人": "婚恋",
    "求亲": "爱慕", "爱慕": "爱慕",
    # 师门
    "师徒": "师门-师徒",
    "师兄弟": "师门-同门", "同门": "师门-同门",
    # 垂直统属
    "主仆": "主从", "雇佣": "主从",
    "君臣": "君臣-上下级", "上下级": "君臣-上下级",
    "听令": "君臣-上下级", "命令": "君臣-上下级", "封赏": "君臣-上下级",
    "敬拜": "君臣-上下级",
    # 同盟 / 社交
    "盟友": "同盟", "友军": "同盟", "同伙": "同盟",
    "朋友": "朋友-社交", "同学": "朋友-社交", "同事": "朋友-社交",
    "邻居": "朋友-社交", "搭档": "朋友-社交", "同僚": "朋友-社交",
    "世交": "朋友-社交", "同乡": "朋友-社交",
    # 恩怨
    "恩人": "恩怨-报恩", "救助": "恩怨-报恩", "恩义": "恩怨-报恩",
    "救命": "恩怨-报恩", "施恩": "恩怨-报恩", "资助": "恩怨-报恩",
    # 敌对
    "敌对": "敌对", "情敌": "敌对", "逼婚": "敌对",
    "威胁": "敌对", "被捉": "敌对", "追捕": "敌对", "冲突": "敌对",
}

# rel_subtype → polarity 规则 mock(依据 schema v1 §2 判定指引)
SUBTYPE_TO_POLARITY: dict[str, str] = {
    "辈分-亲属": "positive", "结拜": "positive", "婚恋": "positive",
    "爱慕": "positive", "同盟": "positive", "朋友-社交": "positive",
    "恩怨-报恩": "positive",
    "师门-师徒": "neutral", "师门-同门": "neutral", "主从": "neutral",
    "君臣-上下级": "neutral",
    "敌对": "negative",
    "其他": "neutral",
}

VALID_SUBTYPES = set(LEGACY_TYPE_TO_SUBTYPE.values()) | {"其他"}


def map_legacy_type_to_subtype(relation_type: str | None) -> str | None:
    """旧单标签 → rel_subtype 的确定性规则映射(离线 mock,非 LLM 输出)。

    normalize_relation_type 归一化后查表;未命中返回 "其他"(schema 兜底)。
    输入为空返回 None(评测时按缺失处理,不计入准确率分子分母)。
    """
    if not relation_type:
        return None
    normalized = normalize_relation_type(relation_type)
    return LEGACY_TYPE_TO_SUBTYPE.get(normalized, "其他")


def mock_polarity_for_subtype(rel_subtype: str | None) -> str | None:
    """按 schema v1 §2 指引从 rel_subtype 规则推 polarity(离线 mock)。"""
    if rel_subtype is None:
        return None
    return SUBTYPE_TO_POLARITY.get(rel_subtype, "neutral")


# ── 通用计算(纯函数)────────────────────────────────────────────────

def accuracy(pairs: list[tuple[str | None, str | None]]) -> dict:
    """(预测, 标注) 对列表 → {n, correct, accuracy};任一端缺失的条目不参与。"""
    usable = [(p, g) for p, g in pairs if p is not None and g is not None]
    n = len(usable)
    correct = sum(1 for p, g in usable if p == g)
    return {
        "n": n,
        "correct": correct,
        "accuracy": (correct / n) if n else None,
    }


def top_confusions(
    pairs: list[tuple[str | None, str | None]], limit: int = 3
) -> list[tuple[str, str, int]]:
    """返回混淆最多的 (预测, 标注, 次数) 列表(仅预测≠标注的条目)。"""
    counter: Counter[tuple[str, str]] = Counter(
        (p, g) for p, g in pairs if p is not None and g is not None and p != g
    )
    return [(p, g, c) for (p, g), c in counter.most_common(limit)]


def subtype_distribution(labels: list[str]) -> dict[str, int]:
    return dict(Counter(labels).most_common())


# ── 水浒 silver 小样评测 ────────────────────────────────────────────

def load_shuihu_silver(path: Path = SHUIHU_SILVER_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["relations"]


def evaluate_shuihu(records: list[dict]) -> dict:
    """水浒 silver 小样:类型级(mock)/极性(mock)/派生一致率/旧口径对照。"""
    subtype_pairs = [
        (map_legacy_type_to_subtype(r["system_type"]), r.get("rel_subtype"))
        for r in records
    ]
    polarity_pairs = [
        (mock_polarity_for_subtype(map_legacy_type_to_subtype(r["system_type"])),
         r.get("polarity"))
        for r in records
    ]
    # 旧单标签口径:旧路径 category vs 标注 subtype 派生 category
    legacy_category_pairs = [
        (classify_relation_category(normalize_relation_type(r["system_type"])),
         derive_category_from_dimensions(r.get("rel_subtype")))
        for r in records
    ]
    # 维度→旧六类派生一致率:同一 system_type,mock subtype 派生 category
    # vs 旧路径 category(系统内部两条路径的自洽程度,不涉标注)
    derivation_pairs = [
        (derive_category_from_dimensions(
            map_legacy_type_to_subtype(r["system_type"])),
         classify_relation_category(normalize_relation_type(r["system_type"])))
        for r in records
    ]
    return {
        "n": len(records),
        "label_subtype_dist": subtype_distribution(
            [r["rel_subtype"] for r in records if r.get("rel_subtype")]
        ),
        "subtype": accuracy(subtype_pairs),
        "subtype_confusions": top_confusions(subtype_pairs),
        "polarity": accuracy(polarity_pairs),
        "legacy_category_baseline": accuracy(legacy_category_pairs),
        "derivation_agreement": accuracy(derivation_pairs),
    }


# ── 西游 gold 评测 ──────────────────────────────────────────────────

def load_xiyouji_gold(path: Path = XIYOUJI_GOLD_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["relations"]


def evaluate_xiyouji(records: list[dict]) -> dict:
    """西游 gold:旧单标签口径基线 vs 新口径 mock(类型级与 category 级)。

    gold 内 system_type == correct_type(该 gold 校验的是 category 归类),
    类型级对比因此以 "system_type→mock subtype vs correct_type→期望 subtype"
    计算,衡量规则映射在 gold 类型词表上的稳定性。
    """
    legacy_pairs = [
        (classify_relation_category(normalize_relation_type(r["system_type"])),
         r["correct_category"])
        for r in records
    ]
    mock_pairs = [
        (derive_category_from_dimensions(map_legacy_type_to_subtype(r["system_type"])),
         r["correct_category"])
        for r in records
    ]
    subtype_pairs = [
        (map_legacy_type_to_subtype(r["system_type"]),
         map_legacy_type_to_subtype(r["correct_type"]))
        for r in records
    ]
    # 派生一致率:同一 system_type,维度派生 category vs 旧路径 category
    derivation_pairs = [
        (derive_category_from_dimensions(map_legacy_type_to_subtype(r["system_type"])),
         classify_relation_category(normalize_relation_type(r["system_type"])))
        for r in records
    ]
    return {
        "n": len(records),
        "legacy_category_baseline": accuracy(legacy_pairs),
        "mock_category": accuracy(mock_pairs),
        "mock_subtype": accuracy(subtype_pairs),
        "subtype_confusions": top_confusions(subtype_pairs),
        "derivation_agreement": accuracy(derivation_pairs),
    }


# ── 报告渲染 ────────────────────────────────────────────────────────

def _pct(acc: float | None) -> str:
    return "—" if acc is None else f"{acc * 100:.1f}%"


def _confusion_lines(confusions: list[tuple[str, str, int]]) -> str:
    if not confusions:
        return "无混淆。"
    return "、".join(f"{p}→{g} ×{c}" for p, g, c in confusions)


def render_report(
    shuihu: dict,
    xiyouji: dict,
    *,
    report_date: date | None = None,
) -> str:
    d = (report_date or date.today()).isoformat()
    sh, xy = shuihu, xiyouji
    sh_subtype_ok = (
        sh["subtype"]["accuracy"] is not None
        and sh["subtype"]["accuracy"] >= SHUIHU_SUBTYPE_TARGET
    )
    xy_ok = (
        xy["mock_subtype"]["accuracy"] is not None
        and xy["legacy_category_baseline"]["accuracy"] is not None
        and xy["mock_category"]["accuracy"] is not None
        and xy["mock_category"]["accuracy"] >= xy["legacy_category_baseline"]["accuracy"]
    )
    lines = [
        "# 关系维度回测报告(FR-1.5)",
        "",
        f"- 日期:{d}",
        f"- Schema:{SCHEMA_VERSION}",
        "- 脚本:`backend/scripts/eval_relation_dimensions.py`(纯离线,未调用任何 LLM/付费 API)",
        "",
        "## 口径声明(必读)",
        "",
        "- 水浒 silver 小样为 **silver_draft 草稿标注(规则生成 + 少量人工知识修正),未经人工复核**,不得作为论文绝对数字。",
        "- 水浒 28% 旧基线**引自论文**,本 repo 内无可复跑的水浒关系 gold;本报告新口径数字是 silver 小样上的测量,**两者不可直接比较**。",
        "- 冻结抽取产物不含三维字段;为避免触发 LLM 调用,水浒/西游的“新口径系统输出”由确定性规则映射(旧单标签→rel_subtype→category)mock 得到,属离线近似口径。",
        "- 本报告为增量产物,不触碰冻结 gold 与论文数字。",
        "",
        "## 水浒(silver 小样)",
        "",
        f"- 样本量:{sh['n']}",
        f"- 标注 subtype 分布:{json.dumps(sh['label_subtype_dist'], ensure_ascii=False)}",
        f"- **类型级准确率(mock 系统输出 vs silver 标注):{_pct(sh['subtype']['accuracy'])}**"
        f"({sh['subtype']['correct']}/{sh['subtype']['n']};验收线 ≥{SHUIHU_SUBTYPE_TARGET * 100:.0f}% → {'达标' if sh_subtype_ok else '未达标'})",
        f"- 混淆最多的 subtype 对:{_confusion_lines(sh['subtype_confusions'])}",
        f"- 极性准确率(规则 mock vs 标注,有标注条目):{_pct(sh['polarity']['accuracy'])}({sh['polarity']['correct']}/{sh['polarity']['n']})",
        f"- 旧单标签口径对照(旧路径 category vs 标注派生 category):{_pct(sh['legacy_category_baseline']['accuracy'])}({sh['legacy_category_baseline']['correct']}/{sh['legacy_category_baseline']['n']})",
        f"- 维度→旧六类派生一致率(mock subtype 派生 vs 旧路径,系统自洽):{_pct(sh['derivation_agreement']['accuracy'])}({sh['derivation_agreement']['correct']}/{sh['derivation_agreement']['n']})",
        "",
        "## 西游(gold,冻结只读)",
        "",
        f"- 样本量:{xy['n']}",
        f"- 旧单标签口径基线(旧路径 category vs correct_category):{_pct(xy['legacy_category_baseline']['accuracy'])}({xy['legacy_category_baseline']['correct']}/{xy['legacy_category_baseline']['n']})",
        f"- 新口径 mock 类型级(system_type→subtype vs correct_type→subtype):{_pct(xy['mock_subtype']['accuracy'])}({xy['mock_subtype']['correct']}/{xy['mock_subtype']['n']})",
        f"- 新口径 mock category 级:{_pct(xy['mock_category']['accuracy'])}({xy['mock_category']['correct']}/{xy['mock_category']['n']})"
        f"(验收:不低于旧单标签口径基线 → {'达标' if xy_ok else '未达标'})",
        f"- 混淆最多的 subtype 对:{_confusion_lines(xy['subtype_confusions'])}",
        f"- 维度→旧六类派生一致率(同一 system_type 两条路径):{_pct(xy['derivation_agreement']['accuracy'])}({xy['derivation_agreement']['correct']}/{xy['derivation_agreement']['n']})",
        "",
        "## 验收对照(FR-1.5)",
        "",
        f"- 水浒类型级 ≥55%(silver 小样口径):**{'达标' if sh_subtype_ok else '未达标'}**({_pct(sh['subtype']['accuracy'])})",
        f"- 西游类型级不低于旧单标签口径基线:**{'达标' if xy_ok else '未达标'}**"
        f"(mock category {_pct(xy['mock_category']['accuracy'])} vs 旧基线 {_pct(xy['legacy_category_baseline']['accuracy'])})",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="FR-1.5 关系维度回测")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--shuihu", type=Path, default=SHUIHU_SILVER_PATH)
    parser.add_argument("--xiyouji", type=Path, default=XIYOUJI_GOLD_PATH)
    args = parser.parse_args()

    shuihu_result = evaluate_shuihu(load_shuihu_silver(args.shuihu))
    xiyouji_result = evaluate_xiyouji(load_xiyouji_gold(args.xiyouji))
    report = render_report(shuihu_result, xiyouji_result)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"报告已写入 {args.out}")
    print(f"水浒类型级(mock 口径):{_pct(shuihu_result['subtype']['accuracy'])}"
          f"  西游 mock category:{_pct(xiyouji_result['mock_category']['accuracy'])}"
          f" vs 旧基线 {_pct(xiyouji_result['legacy_category_baseline']['accuracy'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
