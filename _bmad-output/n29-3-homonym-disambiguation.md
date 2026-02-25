# Story N29.3: 同名地点消歧

Status: draft

## Story

As a 用户,
I want 同名但实际不同的地点（如大观园的"夹道"和甄家的"夹道"）被识别为不同实体,
So that 地图上地点的层级关系和位置数据不会因为同名混淆而出错。

## Background

红楼梦中大量通用建筑名（夹道、后门、角门、上房、里间、外间房等）在不同建筑中都存在。当前系统将所有"夹道"视为同一个地点实体，导致：

1. **层级混乱**：同一个"夹道"在不同章节有不同 parent（大观园 vs 甄家 vs 荣国府），触发虚假冲突告警
2. **数据污染**：本属于不同建筑的描述、事件合并到同一个实体 profile 中
3. **地图错位**：布局引擎无法为同一个实体计算合理位置（它同时属于多个 parent）

N28 的 S2 已在冲突检测层过滤了这些误报，但数据层的混淆仍然存在。

### 消歧策略

对通用建筑名在 **后处理阶段** 加父级前缀消歧：
- `夹道` + parent=`大观园` → `大观园·夹道`
- `夹道` + parent=`甄家` → `甄家·夹道`
- 无 parent 时保持原名

在 `FactValidator` 后处理阶段（而非 LLM prompt）执行，因为：
1. LLM 提取时 parent 信息已存在于 location JSON 中
2. 后处理更可控，不依赖 LLM 行为变化
3. 可以只对已知的通用名集合操作，避免误改专有地名

## Acceptance Criteria

1. **AC-1**: 定义通用建筑名集合 `_DISAMBIGUATE_NAMES`（复用 conflict_detector 的 `_HOMONYM_PRONE_NAMES`），仅对集合内的名称执行消歧
2. **AC-2**: `FactValidator` 在 `_validate_locations()` 最后阶段，对通用建筑名执行消歧：当 location.parent 非空时，将 name 改为 `"{parent}·{name}"`
3. **AC-3**: 消歧同步更新同一 ChapterFact 内所有引用该地点的字段：characters[].locations_in_chapter、events[].location、spatial_relationships[].source/target
4. **AC-4**: 已分析的旧数据不受影响（消歧只在新分析时生效），向后兼容
5. **AC-5**: 消歧后的地点名在前端百科、地图、实体卡片中正确显示为 "大观园·夹道" 格式
6. **AC-6**: AliasResolver 将消歧前的原名（"夹道"）注册为消歧后名字的 alias，确保搜索和 Q&A 仍能匹配

## Tasks / Subtasks

- [ ] Task 1: 定义消歧名单
  - [ ] 1.1 将 `conflict_detector.py` 中的 `_HOMONYM_PRONE_NAMES` 提取为共享模块（如 `utils/location_names.py`），供 conflict_detector 和 fact_validator 共同引用
  - [ ] 1.2 确保名单覆盖红楼梦、西游记、凡人修仙传等已知小说的高频通用建筑名

- [ ] Task 2: FactValidator 消歧逻辑
  - [ ] 2.1 在 `fact_validator.py` 的 `_validate_locations()` 尾部新增消歧步骤
  - [ ] 2.2 遍历 locations，对 name 在 `_DISAMBIGUATE_NAMES` 中且 parent 非空的项执行重命名：
    ```python
    new_name = f"{loc['parent']}·{loc['name']}"
    rename_map[loc["name"]] = new_name  # 记录本次重命名映射
    loc["name"] = new_name
    ```
  - [ ] 2.3 注意：同一个通用名在同一章中可能出现多次且 parent 不同（如第1章有"甄家·夹道"和"大观园·夹道"），需要逐个处理，不能假设一章只有一个实例

- [ ] Task 3: 同一 ChapterFact 内的引用同步
  - [ ] 3.1 用 rename_map 更新 characters[].locations_in_chapter 中的旧名
  - [ ] 3.2 用 rename_map 更新 events[].location 中的旧名
  - [ ] 3.3 用 rename_map 更新 spatial_relationships[].source 和 .target 中的旧名
  - [ ] 3.4 用 rename_map 更新 locations[].parent（如果 parent 本身也被消歧了的情况，虽然罕见但需处理）

- [ ] Task 4: AliasResolver 集成
  - [ ] 4.1 消歧后的 ChapterFact 中，在 location 的描述或 alias 字段记录原名（"夹道"），使 AliasResolver 能建立 "夹道" → "大观园·夹道" 的映射
  - [ ] 4.2 考虑当多个消歧名都来自同一原名时（"大观园·夹道"和"甄家·夹道"），AliasResolver 不应将它们合并（它们是不同实体）

- [ ] Task 5: 前端显示兼容
  - [ ] 5.1 确认前端组件（EntityCardDrawer、EncyclopediaPage、NovelMap）能正确渲染带 "·" 分隔符的地点名
  - [ ] 5.2 搜索框匹配逻辑能匹配 "夹道" 查到 "大观园·夹道"（substring match 已天然支持）

## Dev Notes

### 消歧时机

在 `FactValidator.validate()` 的 `_validate_locations()` 结束后、返回最终 fact_json 前执行。这确保：
1. 所有其他验证（generic location 过滤、parent 修正）已完成
2. parent 字段已经是最终值
3. 消歧后的名字直接写入 DB，不需要额外迁移

### rename_map 冲突处理

同一章中，"夹道" 可能出现 2 次（parent 不同）。rename_map 不能是简单的 `old → new` 映射，需要按 index 或 parent 区分：

```python
# 方案：先消歧 locations 列表，再用 (old_name, parent) 做精确匹配更新引用
for loc in locations:
    if loc["name"] in _DISAMBIGUATE_NAMES and loc.get("parent"):
        old_name = loc["name"]
        new_name = f"{loc['parent']}·{old_name}"
        loc["name"] = new_name
        # 更新引用时需要注意上下文
```

对于 characters[].locations_in_chapter，该列表是简单的名称数组，无法区分同名不同 parent。**安全策略**：只在 locations 列表中该通用名恰好只出现一次时执行引用替换；多次出现时跳过引用替换（保留原名，不会造成额外问题，只是失去消歧的引用关联）。

### 向后兼容

- 已分析的数据保持原样（"夹道" 不会被追溯修改）
- 只有重新分析时才会产生消歧后的名字
- 前端和 API 不依赖地点名格式，"·" 分隔符只是视觉约定
