"""水浒关系标注 silver 小样构建器(FR-1.5,W1 里程碑)。

从冻结的 demo 抽取产物 demo/shuihu/graph.json.gz 抽样 80–120 条关系,
用确定性规则(all_types 优先级 + 旧类型→subtype 映射)+ 少量基于原著
知识的 curated override 生成**草稿标签**,写入
backend/data/review/shuihu_relations_silver.json。

诚实声明:产物为 silver_draft 级——规则生成 + 局部人工知识修正,
未经系统人工复核,不得作为论文绝对数字,仅供 FR-1.5 增量回测使用。
本脚本只读水浒原始数据,不做任何修改;不调用 LLM(本地 ollama 无可用模型)。

Usage:
    cd backend && .venv/bin/python scripts/build_shuihu_silver_sample.py
    .venv/bin/python scripts/build_shuihu_silver_sample.py --dry-run   # 只打印抽样,不写文件
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent

_GRAPH_PATH = _REPO_ROOT / "demo" / "shuihu" / "graph.json.gz"
_OUT_PATH = _BACKEND_DIR / "data" / "review" / "shuihu_relations_silver.json"

# 复用回测脚本的 旧类型→rel_subtype 映射,保证草稿标签与 mock 口径同源
_spec = importlib.util.spec_from_file_location(
    "eval_relation_dimensions", _BACKEND_DIR / "scripts" / "eval_relation_dimensions.py"
)
_eval = importlib.util.module_from_spec(_spec)
sys.modules["eval_relation_dimensions"] = _eval
_spec.loader.exec_module(_eval)
map_legacy_type_to_subtype = _eval.map_legacy_type_to_subtype

# 水浒主要角色(抽样向这些角色倾斜,兼顾配角)
MAJORS = {
    "宋江", "卢俊义", "吴用", "林冲", "鲁智深", "武松", "李逵", "晁盖",
    "燕青", "戴宗", "花荣", "柴进", "朱仝", "公孙胜", "关胜", "秦明",
    "高俅", "潘金莲", "西门庆", "扈三娘", "孙二娘", "史进", "杨志",
}

# 亲属类旧类型(主 relation_type 命中即入 family 桶)
FAMILY_PRIMARY = {
    "父子", "父女", "母子", "母女", "兄弟", "兄妹", "姐弟", "姐妹",
    "叔侄", "伯侄", "翁婿", "郎舅", "夫妻", "祖孙", "养父子", "舅甥",
}

# 分层抽样配额:(桶判据, 配额, 是否要求主要角色参与)
BUCKETS: list[tuple[str, int, bool]] = [
    ("结拜兄弟", 12, False),
    ("师徒", 8, False),
    ("主仆", 6, False),
    ("雇佣", 3, False),
    ("family", 18, False),
    ("敌对", 20, True),
    ("上下级", 16, True),
    ("君臣", 6, False),
    ("救助", 5, False),
    ("施恩", 3, False),
    ("朋友", 6, False),
    ("同门", 4, False),
    ("师兄弟", 2, False),
    ("恋人", 4, False),
    ("爱慕", 4, False),
    ("同伙", 3, False),
]

# 草稿标签优先级:all_types 中多个候选 subtype 时按此顺序取先
SUBTYPE_PRIORITY = [
    "辈分-亲属", "结拜", "师门-师徒", "师门-同门", "主从", "敌对",
    "恩怨-报恩", "婚恋", "爱慕", "君臣-上下级", "同盟", "朋友-社交", "其他",
]

SUBTYPE_TO_POLARITY = _eval.SUBTYPE_TO_POLARITY
CLOSE_SUBTYPES = {"辈分-亲属", "结拜", "婚恋", "主从", "师门-师徒"}

# ── curated overrides:基于原著知识对规则草稿的修正 ─────────────────
# 键为 frozenset({person_a, person_b});值为 (rel_subtype, polarity, 理由)。
# 仅收录把握较高的条目;不确定的一律留给规则,理由中注明待复核。
CURATED: dict[frozenset, tuple[str, str, str]] = {
    frozenset({"宋江", "晁盖"}): (
        "朋友-社交", "positive",
        "郓城故交,生辰纲事发宋江冒死报信;书中无结拜记载,后为前后任寨主"),
    frozenset({"宋江", "武松"}): (
        "结拜", "positive", "柴进庄上结拜为义兄弟(schema v1 例子)"),
    frozenset({"宋江", "李逵"}): (
        "君臣-上下级", "neutral",
        "李逵为宋江嫡系步军头领,忠诚统属关系,书中无结拜记载"),
    frozenset({"卢俊义", "宋江"}): (
        "君臣-上下级", "neutral", "梁山正副寨主统属关系;all_types 中“爱慕”为抽取噪声"),
    frozenset({"燕青", "宋江"}): (
        "君臣-上下级", "neutral", "燕青随卢俊义上梁山为头领,与宋江为统属关系,非结拜/主仆"),
    frozenset({"燕青", "卢俊义"}): (
        "主从", "positive", "燕青为卢俊义自幼收养的心腹家人,主仆之实"),
    frozenset({"公孙胜", "宋江"}): (
        "君臣-上下级", "neutral", "公孙胜为梁山头领受宋江统属;其师为罗真人,与宋江非师徒"),
    frozenset({"鲁智深", "林冲"}): (
        "朋友-社交", "positive", "鲁智深与林冲结为好友(schema v1 例子),野猪林相救"),
    frozenset({"鲁智深", "武松"}): (
        "朋友-社交", "positive", "二龙山共事至交,书中无同门/结拜记载"),
    frozenset({"武松", "施恩"}): (
        "恩怨-报恩", "positive", "施恩厚待配军武松,武松醉打蒋门神夺回快活林以报"),
    frozenset({"柴进", "宋江"}): (
        "恩怨-报恩", "positive", "柴进多次庇护资助宋江(沧州避难、资助盘缠)"),
    frozenset({"朱仝", "宋江"}): (
        "恩怨-报恩", "positive", "朱仝先后义释晁盖、宋江,有救命/庇护之恩"),
    frozenset({"林冲", "柴进"}): (
        "恩怨-报恩", "positive", "柴进收留庇护发配途中的林冲并修书荐往梁山"),
    frozenset({"吴用", "晁盖"}): (
        "朋友-社交", "positive", "吴用与晁盖自幼结交,为其心腹智囊"),
    frozenset({"朱仝", "雷横"}): (
        "朋友-社交", "positive", "郓城县马步军都头同僚兼好友"),
    frozenset({"石秀", "杨雄"}): (
        "结拜", "positive", "石秀与杨雄结拜为兄弟"),
    frozenset({"秦明", "花荣"}): (
        "辈分-亲属", "positive", "秦明娶花荣之妹,郎舅姻亲"),
    frozenset({"扈三娘", "王矮虎"}): (
        "辈分-亲属", "positive", "宋江主婚,扈三娘嫁王英为夫妻(平辈姻亲)"),
    frozenset({"扈三娘", "王英"}): (
        "辈分-亲属", "positive", "宋江主婚,扈三娘嫁王英为夫妻(平辈姻亲)"),
    frozenset({"潘金莲", "西门庆"}): (
        "婚恋", "positive", "双向私通的情人关系(非单向爱慕)"),
    frozenset({"林冲", "高俅"}): (
        "敌对", "negative", "高俅设局白虎堂、火烧草料场,步步迫害林冲"),
    frozenset({"林冲", "高衙内"}): (
        "敌对", "negative", "高衙内调戏林娘子,结怨"),
    frozenset({"林冲", "陆谦"}): (
        "敌对", "negative", "陆谦卖友求荣,谋害林冲于草料场"),
    frozenset({"林冲", "陆虞候"}): (
        "敌对", "negative", "陆虞候(陆谦)卖友求荣,谋害林冲于草料场"),
    frozenset({"林冲", "王伦"}): (
        "敌对", "negative", "王伦嫉贤刁难,林冲火并杀之"),
    frozenset({"武松", "潘金莲"}): (
        "敌对", "negative", "潘金莲毒杀武大郎,武松杀嫂复仇,亲属名分被仇恨主导"),
    frozenset({"武松", "西门庆"}): (
        "敌对", "negative", "西门庆通嫂杀兄,武松斗杀西门庆"),
    frozenset({"武松", "蒋门神"}): (
        "敌对", "negative", "蒋门神夺施恩快活林,武松醉打蒋门神"),
    frozenset({"鲁智深", "郑屠"}): (
        "敌对", "negative", "鲁智深三拳打死镇关西"),
    frozenset({"金翠莲", "郑屠"}): (
        "敌对", "negative", "郑屠虚钱实契强骗金翠莲为妾并逼讨典身钱"),
    frozenset({"晁盖", "史文恭"}): (
        "敌对", "negative", "史文恭毒箭射杀晁盖"),
    frozenset({"卢俊义", "史文恭"}): (
        "敌对", "negative", "卢俊义活捉史文恭为晁盖报仇"),
    frozenset({"宋江", "高俅"}): (
        "敌对", "negative", "高俅等设计毒杀宋江"),
    frozenset({"鲁智深", "金翠莲"}): (
        "恩怨-报恩", "positive", "金翠莲父女感念鲁智深救命之恩(schema v1 例子)"),
    frozenset({"公孙胜", "罗真人"}): (
        "师门-师徒", "positive", "公孙胜拜罗真人为师学道"),
    # 七星聚义(晁盖吴用公孙胜刘唐三阮):聚义共谋生辰纲,为同盟而非结拜仪式
    frozenset({"刘唐", "晁盖"}): (
        "同盟", "positive", "七星聚义共谋生辰纲,对等联盟,非结拜"),
    frozenset({"公孙胜", "晁盖"}): (
        "同盟", "positive", "七星聚义共谋生辰纲,对等联盟,非结拜"),
    frozenset({"公孙胜", "吴用"}): (
        "同盟", "positive", "七星聚义共谋生辰纲,对等联盟,非结拜"),
    frozenset({"刘唐", "吴用"}): (
        "同盟", "positive", "七星聚义共谋生辰纲,对等联盟,非结拜"),
    frozenset({"晁盖", "阮小七"}): (
        "同盟", "positive", "七星聚义共谋生辰纲,对等联盟,非结拜"),
    frozenset({"晁盖", "阮小五"}): (
        "同盟", "positive", "七星聚义共谋生辰纲,对等联盟,非结拜"),
    frozenset({"吴用", "阮小七"}): (
        "同盟", "positive", "七星聚义共谋生辰纲,对等联盟,非结拜"),
    frozenset({"吴用", "阮小五"}): (
        "同盟", "positive", "七星聚义共谋生辰纲,对等联盟,非结拜"),
    frozenset({"吴用", "白胜"}): (
        "同盟", "positive", "生辰纲同伙,对等联盟,非同门"),
    frozenset({"宋江", "戴宗"}): (
        "君臣-上下级", "neutral", "戴宗为宋江心腹,梁山统属关系,非主仆"),
    frozenset({"关胜", "宋江"}): (
        "君臣-上下级", "neutral", "关胜被擒后感宋江义气归顺,为梁山马军头领"),
    frozenset({"宋江", "鲁智深"}): (
        "君臣-上下级", "neutral", "鲁智深率二龙山并入梁山后受宋江统属,无结拜记载"),
    frozenset({"宋江", "王矮虎"}): (
        "君臣-上下级", "neutral", "王英(王矮虎)拜服宋江后为梁山头领,统属关系"),
    frozenset({"吕方", "郭盛"}): (
        "朋友-社交", "positive", "对影山比武对手,后同上梁山为同僚,非同门"),
    frozenset({"张清", "董平"}): (
        "朋友-社交", "positive", "梁山马军同僚,书中无同门记载"),
    frozenset({"李逵", "鲁智深"}): (
        "朋友-社交", "positive", "梁山同僚,书中无同门记载"),
    frozenset({"时迁", "石秀"}): (
        "朋友-社交", "positive", "结伴同上梁山的同伴,非同门"),
    frozenset({"李忠", "鲁智深"}): (
        "朋友-社交", "positive", "渭州相识的旧交;李忠是史进之师,与鲁智深非师徒"),
    frozenset({"史进", "朱武"}): (
        "朋友-社交", "positive", "史进与少华山朱武结交,书信往来"),
    frozenset({"孔亮", "武松"}): (
        "朋友-社交", "positive", "白虎山不打不相识;孔明孔亮之师为宋江,非武松"),
    frozenset({"张青", "鲁智深"}): (
        "朋友-社交", "positive", "十字坡相识后同上二龙山,书中无结拜记载"),
    frozenset({"孙二娘", "鲁智深"}): (
        "朋友-社交", "positive", "十字坡不打不相识,后同为二龙山头领,非同门"),
    frozenset({"孙二娘", "武松"}): (
        "结拜", "positive", "十字坡张青孙二娘与武松结拜,武松认二人为兄嫂"),
    frozenset({"小衙内", "朱仝"}): (
        "主从", "positive", "朱仝受沧州知府托付看顾小衙内,看顾之责;“爱慕”为疼爱误抽"),
    frozenset({"宋江", "李师师"}): (
        "朋友-社交", "neutral", "宋江借李师师通关节求招安,事务性交往,非恋慕"),
    frozenset({"婆惜", "宋江"}): (
        "婚恋", "negative", "阎婆惜为宋江外室,情人关系;后交恶,宋江怒杀阎婆惜"),
    frozenset({"李应", "祝氏三杰"}): (
        "敌对", "negative", "李应与祝家庄结盟后反目,祝彪箭伤李应"),
    frozenset({"史进", "王进"}): (
        "师门-师徒", "positive", "王进点拨史进武艺,有师徒之实"),
    frozenset({"史进", "鲁智深"}): (
        "朋友-社交", "positive", "渭州结识的好友"),
    frozenset({"李固", "卢俊义"}): (
        "主从", "neutral", "李固为卢俊义府上都管,主仆关系"),
    frozenset({"李固", "贾氏"}): (
        "婚恋", "positive", "李固与卢俊义妻贾氏私通"),
    frozenset({"高俅", "高衙内"}): (
        "辈分-亲属", "positive", "高衙内为高俅螟蛉义子"),
    frozenset({"扈三娘", "宋江"}): (
        "结拜", "positive", "宋江认扈三娘为义妹,拟制亲属"),
    frozenset({"晁盖", "林冲"}): (
        "恩怨-报恩", "positive", "林冲火并王伦,奉晁盖为寨主,有拥立之恩"),
}


def _bucket_match(bucket: str, edge: dict) -> bool:
    if bucket == "family":
        return edge["relation_type"] in FAMILY_PRIMARY
    return edge["relation_type"] == bucket


def sample_edges(edges: list[dict], node_weight: dict[str, int]) -> list[dict]:
    """按 BUCKETS 配额分层抽样(每桶内按 weight 降序),pair 去重。"""
    selected: list[dict] = []
    seen: set[frozenset] = set()
    for bucket, quota, need_major in BUCKETS:
        pool = [e for e in edges if _bucket_match(bucket, e)]
        if need_major:
            major_pool = [
                e for e in pool
                if e["source"] in MAJORS or e["target"] in MAJORS
                or max(node_weight.get(e["source"], 0), node_weight.get(e["target"], 0)) >= 20
            ]
            # 主要角色池不足时回退全池,保证配额可填满
            pool = major_pool if len(major_pool) >= quota else pool
        pool.sort(key=lambda e: -e.get("weight", 0))
        taken = 0
        for e in pool:
            if taken >= quota:
                break
            pair = frozenset({e["source"], e["target"]})
            if pair in seen:
                continue
            seen.add(pair)
            selected.append(e)
            taken += 1
    return selected


def draft_label(edge: dict) -> tuple[str, str, str]:
    """规则草稿标签 → (rel_subtype, polarity, 理由);curated 优先。"""
    pair = frozenset({edge["source"], edge["target"]})
    if pair in CURATED:
        subtype, polarity, reason = CURATED[pair]
        return subtype, polarity, f"[curated] {reason}"
    candidates = {
        map_legacy_type_to_subtype(t) for t in edge.get("all_types") or [edge["relation_type"]]
    } - {None}
    subtype = next((s for s in SUBTYPE_PRIORITY if s in candidates), "其他")
    polarity = SUBTYPE_TO_POLARITY.get(subtype, "neutral")
    reason = (
        f"[rule] all_types={edge.get('all_types')} 按优先级规则取“{subtype}”,"
        "未经人工复核"
    )
    return subtype, polarity, reason


def build_records(edges: list[dict]) -> list[dict]:
    records = []
    for e in edges:
        subtype, polarity, reason = draft_label(e)
        records.append({
            "person_a": e["source"],
            "person_b": e["target"],
            "system_type": e["relation_type"],
            "system_all_types": e.get("all_types", []),
            "system_category": e.get("category"),
            "mention_count": e.get("weight", 0),
            "first_seen": f"ch{min(e['chapters'])}" if e.get("chapters") else None,
            "rel_subtype": subtype,
            "polarity": polarity,
            "closeness": "close" if subtype in CLOSE_SUBTYPES else "unknown",
            "reason": reason,
            "label_source": "silver_draft",
        })
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="水浒 silver 小样构建器")
    parser.add_argument("--dry-run", action="store_true", help="只打印抽样,不写文件")
    parser.add_argument("--out", type=Path, default=_OUT_PATH)
    args = parser.parse_args()

    graph = json.load(gzip.open(_GRAPH_PATH))
    edges = graph["edges"]
    node_weight = {n["id"]: n.get("chapter_count", 0) for n in graph["nodes"]}

    selected = sample_edges(edges, node_weight)
    records = build_records(selected)

    dist = Counter(r["rel_subtype"] for r in records)
    print(f"抽样 {len(records)} 条;subtype 分布:")
    for subtype, count in dist.most_common():
        print(f"  {subtype}: {count}")
    n_curated = sum(1 for r in records if r["reason"].startswith("[curated]"))
    print(f"curated override {n_curated} 条,规则草稿 {len(records) - n_curated} 条")

    if args.dry_run:
        for r in records:
            print(f"{r['person_a']} × {r['person_b']} | {r['system_type']} "
                  f"{r['system_all_types']} → {r['rel_subtype']} | {r['reason']}")
        return 0

    payload = {
        "_novel": "水浒传",
        "_label_level": "silver_draft",
        "_total": len(records),
        "_instructions": (
            "silver 草稿标注:规则生成(all_types 优先级 + 旧类型→rel_subtype 映射)"
            " + 少量基于原著知识的 curated override(reason 以 [curated] 开头),"
            "未经人工复核,不得作为论文绝对数字。rel_subtype 必填;"
            "polarity/closeness 为规则默认值。取样自 demo/shuihu/graph.json.gz"
            " 冻结抽取产物(只读,未修改)。schema:docs/analysis/"
            "relation-dimension-schema-v1.md(13 个 rel_subtype 取值)。"
        ),
        "_schema_version": "relation-dimension-schema-v1",
        "_source": "demo/shuihu/graph.json.gz (frozen extraction, read-only)",
        "relations": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
