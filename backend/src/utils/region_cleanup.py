"""Cross-novel region pollution cleanup (红楼 layers 混西游大洲名,2026-09-22).

溯源结论:污染为 2026-04 前初始分析时 LLM 宏观骨架幻觉的存量数据
(四大部洲+错字变体 南膳部洲 混入红楼 overworld regions);geo_skills
rebuild/apply 链路不写 layers.regions(只动 parents/tiers/layer_map/
virtual),清理后不会被 rebuild 再污染。

clean_cross_novel_regions:纯函数,输入 ws dict 与污染名集合,返回报告。
  1. 从所有 layer.regions 摘除污染 region 条目;
  2. location_region_map:污染自映射删除;指向污染 region 的地点沿
     location_parents 链向上找最近的合法 region 重挂,找不到则删映射。
"""

from __future__ import annotations


def clean_cross_novel_regions(
    ws: dict,
    polluted: set[str] | frozenset[str],
) -> dict:
    """清理 ws dict 中的跨书污染 region。返回清理报告(保序、幂等)。"""
    report: dict = {
        "removed_regions": [],
        "dropped_self_maps": [],
        "remapped": {},
        "dropped_maps": [],
    }
    if not polluted:
        return report

    # 1. 摘除污染 region 条目
    for layer in ws.get("layers") or []:
        regions = layer.get("regions") or []
        kept = []
        for r in regions:
            if r.get("name") in polluted:
                report["removed_regions"].append(
                    (layer.get("layer_id"), r.get("name")))
            else:
                kept.append(r)
        layer["regions"] = kept

    valid_regions = {
        r.get("name")
        for layer in ws.get("layers") or []
        for r in (layer.get("regions") or [])
    }

    # 2. 修 location_region_map
    parents = ws.get("location_parents") or {}
    region_map = ws.get("location_region_map") or {}
    for loc in sorted(list(region_map.keys())):
        region = region_map.get(loc)
        if region not in polluted:
            continue
        if loc == region:
            report["dropped_self_maps"].append(loc)
            del region_map[loc]
            continue
        # 沿父链找最近合法 region(先查父节点自身的 region 映射,
        # 再查父节点本身是否合法 region)
        node, seen = parents.get(loc), {loc}
        new_region = None
        while node and node not in seen:
            seen.add(node)
            mapped = region_map.get(node)
            if mapped and mapped not in polluted and mapped in valid_regions:
                new_region = mapped
                break
            if node in valid_regions:
                new_region = node
                break
            node = parents.get(node)
        if new_region:
            report["remapped"][loc] = (region, new_region)
            region_map[loc] = new_region
        else:
            report["dropped_maps"].append(loc)
            del region_map[loc]
    return report
