"""Tests for region_cleanup.clean_cross_novel_regions."""

from src.utils.region_cleanup import clean_cross_novel_regions

_POLLUTED = {"东胜神洲", "西牛贺洲", "南膳部洲"}


def _ws() -> dict:
    return {
        "layers": [
            {"layer_id": "overworld", "name": "主世界", "regions": [
                {"name": "东胜神洲"}, {"name": "都中"},
                {"name": "南膳部洲"}, {"name": "大观园"},
            ]},
            {"layer_id": "celestial", "name": "天界", "regions": [
                {"name": "西牛贺洲"}, {"name": "离恨天"},
            ]},
        ],
        "location_parents": {
            "王子腾府": "都中", "都中": "天下",
            "铁网山": "主世界", "主世界": "天下",
            "甲": "乙", "乙": "丙", "丙": "天下",
        },
        "location_region_map": {
            "东胜神洲": "东胜神洲",       # 污染自映射 → 删
            "王子腾府": "东胜神洲",       # 父链到合法 region 都中 → 重挂
            "铁网山": "南膳部洲",         # 父链无合法 region → 删
            "怡红院": "大观园",           # 合法 → 不动
            "甲": "西牛贺洲",             # 父链 乙(无映射非region)→丙(同)→删
        },
    }


def test_removes_polluted_region_entries():
    ws = _ws()
    report = clean_cross_novel_regions(ws, _POLLUTED)
    overworld_names = [r["name"] for r in ws["layers"][0]["regions"]]
    celestial_names = [r["name"] for r in ws["layers"][1]["regions"]]
    assert overworld_names == ["都中", "大观园"]
    assert celestial_names == ["离恨天"]
    assert sorted(report["removed_regions"]) == [
        ("celestial", "西牛贺洲"),
        ("overworld", "东胜神洲"),
        ("overworld", "南膳部洲"),
    ]


def test_self_maps_dropped_and_children_remapped():
    ws = _ws()
    report = clean_cross_novel_regions(ws, _POLLUTED)
    rm = ws["location_region_map"]
    assert "东胜神洲" not in rm                      # 自映射删
    assert rm["王子腾府"] == "都中"                   # 重挂最近合法 region
    assert "铁网山" not in rm                        # 无合法 region → 删
    assert rm["怡红院"] == "大观园"                   # 合法不动
    assert "甲" not in rm                            # 父链无 region → 删
    assert report["remapped"] == {"王子腾府": ("东胜神洲", "都中")}
    assert set(report["dropped_maps"]) == {"铁网山", "甲"}
    assert report["dropped_self_maps"] == ["东胜神洲"]


def test_idempotent_and_empty():
    ws = _ws()
    clean_cross_novel_regions(ws, _POLLUTED)
    report2 = clean_cross_novel_regions(ws, _POLLUTED)
    assert report2 == {"removed_regions": [], "dropped_self_maps": [],
                       "remapped": {}, "dropped_maps": []}
    ws2 = _ws()
    assert clean_cross_novel_regions(ws2, set()) == report2
    # 空污染集:ws 不变
    assert ws2 == _ws()
