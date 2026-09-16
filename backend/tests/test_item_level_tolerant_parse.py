"""q1-1 条目级宽容解析回归测试 (Story 1.1, Epic-Q Epic 1)。

核心保证:LLM 返回的 section 内混有坏条目(缺必填字段 / 类型错误 /
非 dict)时,坏条目被单丢、好条目保留、整章不因一个坏条目失败;
合法全量输入零丢弃(不回退 gold 基线)。
"""


from src.extraction.chapter_fact_extractor import _tolerant_validate_sections
from src.models.chapter_fact import ChapterFact


def _valid_full() -> dict:
    return {
        "characters": [{"name": "孙悟空"}, {"name": "猪八戒"}],
        "relationships": [
            {"person_a": "孙悟空", "person_b": "猪八戒", "relation_type": "师徒"},
        ],
        "locations": [{"name": "花果山", "type": "山"}],
        "spatial_relationships": [
            {"source": "a", "target": "b", "relation_type": "adjacent"},
        ],
        "item_events": [
            {"item_name": "金箍棒", "item_type": "兵器", "action": "获得"},
        ],
        "org_events": [{}],  # 全部字段有默认值,空 dict 合法
        "events": [{"summary": "大闹天宫", "type": "战斗"}],
        "new_concepts": [{"name": "七十二变"}],
        "world_declarations": [
            {"declaration_type": "region_division", "content": {"x": 1}},
        ],
    }


def test_ac1_single_bad_item_dropped_not_chapter_failed():
    """AC-1: 1 坏 + 4 好 → 4 好入库、坏计 1、整章 model_validate 成功。"""
    bad_char = {"new_aliases": ["无名字"]}  # 缺必填 name
    valid_char = {"name": "孙悟空"}
    result = _valid_full()
    result["characters"] = [valid_char, bad_char, valid_char, valid_char, valid_char]

    dropped: dict[str, int] = {}
    out = _tolerant_validate_sections(result, dropped)

    assert len(out["characters"]) == 4
    assert dropped.get("characters") == 1

    # 终态:清洗后整章经 ChapterFact.model_validate 必须成功(坏条目单丢不废章)
    out["novel_id"] = "n1"
    out["chapter_id"] = 1
    cf = ChapterFact.model_validate(out)
    assert len(cf.characters) == 4


def test_ac1_multi_section_each_one_bad():
    """AC-1 变体:多个 section 各坏一条,分别计数。"""
    r = _valid_full()
    r["relationships"] = [
        {"person_a": "a", "person_b": "b", "relation_type": "友"},
        {"person_a": "x"},  # 缺 person_b / relation_type
    ]
    r["locations"] = [
        {"name": "长安", "type": "城"},
        {"type": "城"},  # 缺 name
    ]
    dropped: dict[str, int] = {}
    out = _tolerant_validate_sections(r, dropped)
    assert dropped == {"relationships": 1, "locations": 1}, dropped
    assert len(out["relationships"]) == 1
    assert len(out["locations"]) == 1


def test_no_regression_valid_full_zero_drop():
    """AC-4 前置:合法全量输入零丢弃,且保留原始 dict(与改动前行为一致)。"""
    good = _valid_full()
    dropped: dict[str, int] = {}
    out = _tolerant_validate_sections(good, dropped)
    assert dropped == {}, dropped
    # 保留原始 dict(不改写,避免与改动前产生任何差异)
    assert out["characters"] == good["characters"]
    assert out["relationships"] == good["relationships"]


def test_non_dict_entry_dropped():
    """非 dict 条目(模型在截断压力下偶发)直接丢弃并计数。"""
    r = _valid_full()
    r["events"] = ["大闹天宫", {"summary": "出海", "type": "旅行"}]
    dropped: dict[str, int] = {}
    out = _tolerant_validate_sections(r, dropped)
    assert dropped.get("events") == 1
    assert len(out["events"]) == 1


def test_absent_section_skipped():
    """缺省的 section 不报错、不计入 dropped。"""
    r = {"characters": [{"name": "孙悟空"}]}
    dropped: dict[str, int] = {}
    out = _tolerant_validate_sections(r, dropped)
    assert dropped == {}
    assert len(out["characters"]) == 1
