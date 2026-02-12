# Story 4.8: 空间关系提取增强

Status: ready-for-dev

## Story

As a 系统,
I want 在章节分析时额外提取地点间的空间关系（方位、距离、地形、路径）,
So that 后续可以基于这些约束自动计算地点坐标并生成世界地图。

## Acceptance Criteria

1. **Given** 章节分析正在进行
   **When** LLM 提取 ChapterFact
   **Then** 除现有 `locations` 数组外，新增 `spatial_relationships` 数组，每条包含：
   - `source`: 源地点名
   - `target`: 目标地点名
   - `relation_type`: 关系类型（枚举：`direction` | `distance` | `contains` | `adjacent` | `separated_by` | `terrain`）
   - `value`: 关系值（如 "north"、"3天路程"、"河流"）
   - `confidence`: 置信度（high/medium/low）
   - `narrative_evidence`: 原文依据（≤50字）

2. **Given** 提取的空间关系
   **When** 聚合为全局空间图
   **Then** 去重合并同一对地点的关系，冲突时以高置信度优先
   **And** 返回空间约束列表供约束求解器使用

3. **Given** 已有分析数据的小说（向后兼容）
   **When** 请求空间数据
   **Then** 对无 `spatial_relationships` 字段的旧 ChapterFact 正常返回空数组

4. **Given** 地图 API 请求
   **When** 调用 `GET /api/novels/{id}/map`
   **Then** 响应新增 `spatial_constraints` 字段，包含聚合后的空间约束列表

## Tasks / Subtasks

- [ ] Task 1: 扩展 ChapterFact 数据模型 (AC: #1)
  - [ ] 1.1 在 `backend/src/models/chapter_fact.py` 新增 `SpatialRelationship` Pydantic 模型
  - [ ] 1.2 在 `ChapterFact` 中新增 `spatial_relationships: list[SpatialRelationship] = []`
  - [ ] 1.3 更新 `FactValidator` 验证空间关系的合法性（relation_type 枚举、source/target 非空）

- [ ] Task 2: 更新提取 Prompt (AC: #1)
  - [ ] 2.1 在 `extraction_system.txt` 新增空间关系提取规则段落
  - [ ] 2.2 在 `extraction_examples.json` 新增含空间关系的 few-shot 示例（至少 2 个）
  - [ ] 2.3 Prompt 中明确 12 种约束类型的中文映射和提取规则：
    - direction（方位）: north_of / south_of / east_of / west_of / northeast_of / ...
    - distance（距离）: near / far / travel_time（需包含交通方式）
    - contains（包含）: 已有 parent 字段，此处用于区域级包含
    - adjacent（相邻）: 接壤/毗邻
    - separated_by（分隔）: 被山脉/河流/海洋分隔
    - terrain（地形）: on_coast / in_forest / on_mountain / in_desert / by_river

- [ ] Task 3: 实现空间约束聚合 (AC: #2, #3)
  - [ ] 3.1 在 `visualization_service.py` 的 `get_map_data` 中新增空间约束聚合逻辑
  - [ ] 3.2 去重策略：同一 (source, target, relation_type) 保留最高置信度
  - [ ] 3.3 兼容旧数据：`fact_json` 中无 `spatial_relationships` 字段时默认空列表

- [ ] Task 4: 更新 API 响应 (AC: #4)
  - [ ] 4.1 `get_map_data` 返回值新增 `spatial_constraints` 字段
  - [ ] 4.2 前端 `fetchMapData` 类型保持 `Record<string, unknown>`，无需改动

## Dev Notes

### 现有代码结构

- **ChapterFact 模型**: `backend/src/models/chapter_fact.py`
  - 已有 `LocationFact(name, type, parent, description)`
  - 角色已有 `locations_in_chapter: list[str]` 用于轨迹
  - 新增字段与现有结构平行，不影响已有字段

- **提取器**: `backend/src/extraction/chapter_fact_extractor.py`
  - 使用 `LLMClient.generate_json()` + Pydantic schema 约束输出
  - 新增字段只需更新 schema，LLM 会自动在 JSON 中填充

- **系统 Prompt**: `backend/src/extraction/prompts/extraction_system.txt`
  - 当前 location 提取规则在 24-28 行
  - 在其后追加空间关系提取规则

- **聚合服务**: `backend/src/services/visualization_service.py`
  - `get_map_data()` 函数在 132-199 行
  - 已从 ChapterFact 聚合 locations 和 trajectories
  - 新增 spatial_constraints 聚合逻辑类似

### 关键设计决策

1. **relation_type 枚举**：参考 PlotMap 的 12 种约束类型，但用中文提示让 LLM 更准确提取
2. **confidence 字段**：LLM 对模糊描述（"据说在北边"）标 low，对明确描述（"向北走了三天到达"）标 high
3. **narrative_evidence**：保留原文依据，方便后续人工校验和前端展示
4. **向后兼容**：旧数据无 spatial_relationships 字段时默认空列表，不影响已有功能

### 空间关系提取 Prompt 设计要点

```
## 空间关系提取规则（spatial_relationships）
1. 提取地点之间的所有空间信息：方位、距离、包含、相邻、分隔、地形
2. relation_type 必须是以下之一：direction, distance, contains, adjacent, separated_by, terrain
3. direction 的 value 使用英文方位：north_of, south_of, east_of, west_of, northeast_of, northwest_of, southeast_of, southwest_of
4. distance 的 value 描述距离信息，如 "三天路程（步行）"、"百里"、"very_near"
5. separated_by 的 value 描述分隔物：如 "大河"、"山脉"、"荒漠"
6. terrain 的 value 描述地形特征：on_coast, in_forest, on_mountain, in_desert, by_river, in_plains
7. confidence: high=原文明确描述, medium=可推断, low=模糊暗示
8. narrative_evidence: 摘录原文依据，不超过 50 字
9. 只提取文中有依据的关系，不要推测
```

### References

- [Source: PlotMap 约束类型] 12 种空间约束：distance-based, directional, terrain-based, separation
- [Source: backend/src/models/chapter_fact.py] 现有 ChapterFact 数据模型
- [Source: backend/src/extraction/prompts/extraction_system.txt#L24-28] 现有 location 提取规则
- [Source: backend/src/services/visualization_service.py#L132-199] 现有 get_map_data 函数
