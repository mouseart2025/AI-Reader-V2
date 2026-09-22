"""Tests for visualization_service.canonicalize_map_names (map 展示层 canonical 化)."""

from src.services.visualization_service import canonicalize_map_names

_ALIAS = {"京师": "东京", "北京大名府": "北京"}


def _structures():
    loc_info = {
        "东京": {"name": "东京", "type": "城市", "parent": "京畿"},
        "京师": {"name": "京师", "type": "城市", "parent": "京畿"},
        "高太尉府": {"name": "高太尉府", "type": "府邸", "parent": "京师"},
        "北京大名府": {"name": "北京大名府", "type": "城市", "parent": None},
    }
    loc_chapters = {"东京": {1, 2}, "京师": {3}, "高太尉府": {2},
                    "北京大名府": {61}}
    loc_role = {"东京": "referenced", "京师": "setting",
                "高太尉府": "setting", "北京大名府": "referenced"}
    trajectories = {
        "林冲": [{"location": "京师", "chapter": 7},
                 {"location": "东京", "chapter": 7}],
        "卢俊义": [{"location": "北京大名府", "chapter": 61}],
    }
    constraint_map = {
        ("高太尉府", "京师", "contains"): {
            "source": "高太尉府", "target": "京师",
            "relation_type": "contains", "confidence": "high"},
        ("东京", "京师", "adjacent"): {
            "source": "东京", "target": "京师",
            "relation_type": "adjacent", "confidence": "medium"},
        ("涿郡", "北京大名府", "travel_path"): {
            "source": "涿郡", "target": "北京大名府",
            "relation_type": "travel_path", "confidence": "low",
            "waypoints": ["京师", "大名府"]},
    }
    return loc_info, loc_chapters, loc_role, trajectories, constraint_map


def test_canonical_merges_locations_and_references():
    li, lc, lr, tj, cm = _structures()
    report = canonicalize_map_names(li, lc, lr, tj, cm, dict(_ALIAS))
    # 别名条目消失,canonical 条目保留/合并
    assert "京师" not in li and "北京大名府" not in li
    assert li["东京"]["parent"] == "京畿"
    assert "北京" in li                      # canonical 缺失时由别名条目迁入
    assert report["merged_locations"] == ["京师"]
    # chapters 并集、role 取高优先级
    assert lc["东京"] == {1, 2, 3}
    assert lr["东京"] == "setting"
    assert lc["北京"] == {61}
    # 轨迹同步映射
    assert tj["林冲"] == [{"location": "东京", "chapter": 7},
                          {"location": "东京", "chapter": 7}]
    assert tj["卢俊义"][0]["location"] == "北京"
    # 约束端点/途经点映射;别名自指(东京↔京师)剔除
    assert ("高太尉府", "东京", "contains") in cm
    assert ("东京", "京师", "adjacent") not in cm
    assert ("东京", "东京", "adjacent") not in cm
    assert ("涿郡", "北京", "travel_path") in cm
    assert cm[("涿郡", "北京", "travel_path")]["waypoints"] == ["东京", "大名府"]
    assert report["self_loop_constraints"] == [("东京", "京师", "adjacent")]


def test_reference_integrity_no_alias_left():
    """归一后任何引用处不得残留别名(dangling 检查)。"""
    li, lc, lr, tj, cm = _structures()
    canonicalize_map_names(li, lc, lr, tj, cm, dict(_ALIAS))
    assert not set(li) & set(_ALIAS)
    assert not set(lc) & set(_ALIAS)
    for entries in tj.values():
        assert not {e["location"] for e in entries} & set(_ALIAS)
    for (s, t, _), c in cm.items():
        assert s not in _ALIAS and t not in _ALIAS
        assert not (set(c.get("waypoints") or []) & set(_ALIAS))


def test_empty_alias_map_zero_behavior():
    li, lc, lr, tj, cm = _structures()
    snapshot = (dict(li), {k: set(v) for k, v in lc.items()}, dict(lr),
                {k: [dict(e) for e in v] for k, v in tj.items()}, dict(cm))
    report = canonicalize_map_names(li, lc, lr, tj, cm, {})
    assert report == {"renamed": [], "merged_locations": [],
                      "self_loop_constraints": []}
    assert li == snapshot[0] and lc == snapshot[1] and lr == snapshot[2]
    assert tj == snapshot[3] and cm == snapshot[4]


def test_canonicalize_constraint_list_post_enhance():
    """enhance 注入 ws 存量原始名后:端点/途经点过别名、自指剔除、
    同键高置信去重、保序。"""
    from src.services.visualization_service import canonicalize_constraint_list

    constraints = [
        {"source": "敕建宝林寺", "target": "乌鸡国",
         "relation_type": "contains", "confidence": "medium"},
        {"source": "宝林寺", "target": "乌鸡国",
         "relation_type": "contains", "confidence": "high"},  # 同键高置信胜出
        {"source": "狮驼岭", "target": "八百里狮驼岭",
         "relation_type": "adjacent", "confidence": "low"},   # 映射后自指 → 剔
        {"source": "车迟国", "target": "宝林寺",
         "relation_type": "travel_path", "confidence": "medium",
         "waypoints": ["敕建宝林寺", "号山"]},
    ]
    out = canonicalize_constraint_list(
        constraints, {"敕建宝林寺": "宝林寺", "八百里狮驼岭": "狮驼岭"})
    assert len(out) == 2
    assert out[0]["source"] == "宝林寺" and out[0]["confidence"] == "high"
    assert out[1]["waypoints"] == ["宝林寺", "号山"]
    # 空表零行为
    assert canonicalize_constraint_list(constraints, {}) == constraints
