# Story 7.12: 提取增强与通用性优化

Status: ready-for-dev

## Story

As a 系统,
I want 在 ChapterFact 中可选地提取世界观声明，并针对不同类型小说优化世界结构构建,
So that 世界结构质量更高、方案更通用。

## Acceptance Criteria

1. ChapterFact 新增可选 `world_declarations` 字段，提取区域划分/层声明/传送门声明
2. extraction prompt 增强，引导 LLM 提取世界观相关信息
3. 新增 `InBetween` 空间约束类型，表达"A 在 B 和 C 之间"
4. 地点语义位置提示：名含方位词的地点自动推断方位（如 "东洋大海" → 东方）
5. 不同类型小说优雅处理：奇幻多层、武侠平面、都市单层

## Tasks / Subtasks

- [ ] Task 1: ChapterFact 模型增强 (AC: #1)
  - [ ] 在 `backend/src/models/chapter_fact.py` 中新增:
    ```python
    class WorldDeclaration(BaseModel):
        declaration_type: str   # "region_division" / "layer_exists" / "portal" / "region_position"
        content: dict           # 类型相关的结构化内容
        narrative_evidence: str = ""
        confidence: str = "medium"  # high / medium / low

    class ChapterFact(BaseModel):
        # ... 现有字段 ...
        world_declarations: list[WorldDeclaration] = []  # 可选，向后兼容
    ```
  - [ ] 确保 `world_declarations` 为空列表时不影响现有逻辑（向后兼容）
  - [ ] 在 `fact_validator.py` 中添加 world_declarations 验证规则
- [ ] Task 2: Extraction Prompt 增强 (AC: #2)
  - [ ] 修改 `backend/src/extraction/prompts/extraction_system.txt`:
    - 在 locations 提取指令后新增 world_declarations 提取指令
    - 添加示例：如何识别"世界分为四大部洲"类的宏观声明
    - 明确说明此字段可选，没有世界观声明时输出空列表
  - [ ] 修改 `backend/src/extraction/prompts/extraction_examples.json`:
    - 新增包含 world_declarations 的示例
  - [ ] 确保不影响现有字段的提取质量（world_declarations 是低优先级附加提取）
- [ ] Task 3: InBetween 空间约束 (AC: #3)
  - [ ] 在 `SpatialRelationship.relation_type` 中新增 `"in_between"` 类型
  - [ ] InBetween 的 content: `{ middle: "A", endpoints: ["B", "C"] }`
  - [ ] 在 `map_layout_service.py` 的 ConstraintSolver 中处理 InBetween 约束:
    - A 的坐标应在 B 和 C 坐标的中间区域
    - 能量项: `|pos_A - midpoint(pos_B, pos_C)|^2`
  - [ ] 在 extraction prompt 中添加 in_between 关系的提取示例
- [ ] Task 4: 地点语义位置提示 (AC: #4)
  - [ ] 新增 `backend/src/services/location_hint_service.py`:
    - `extract_direction_hint(location_name: str) -> str | None`
    - 规则:
      - 名含 "东" (东海、东洋、东胜) → "east"
      - 名含 "西" (西海、西牛、西域) → "west"
      - 名含 "南" (南海、南赡、南方) → "south"
      - 名含 "北" (北海、北俱、北方) → "north"
      - 名含 "中" (中原、中土、中央) → "center"
    - 避免误判: "东坡"(人名) 不是方位提示 — 仅对 type 为地理类的地点适用
  - [ ] 在 `world_structure_agent._apply_heuristic_updates()` 中使用方位提示:
    - 新发现的区域如果名含方位词，自动设置 cardinal_direction
  - [ ] 在 ConstraintSolver 中使用方位提示作为弱约束（低权重能量项）
- [ ] Task 5: 通用性优化 (AC: #5)
  - [ ] 在 `world_structure_agent.py` 中实现小说类型自适应:
    - 统计分析特征来推断小说类型:
      - 奇幻/修仙: 高频出现 天/仙/魔/妖/修炼 关键词 → 启用多层检测
      - 武侠: 高频出现 江湖/门派/武功 → 主要检测区域划分，少量副本
      - 历史: 高频出现 朝代/年号/官职 → 主要检测地理区域
      - 都市: 高频出现 公司/城市/学校 → 单层模式，禁用副本检测
    - 自适应不改变 Agent 框架，仅调整信号检测的灵敏度阈值
  - [ ] 新增 `WorldStructure.novel_genre_hint: str | None` 字段（推断的小说类型）
  - [ ] 优雅处理简单结构:
    - 如果分析 30 章后仍只有 overworld 且无区域，标记为"单层小说"
    - 单层小说不显示 Tab 栏，不显示区域边界，退化为 V1 地图模式
    - 确保 V1 到 V2 的平滑过渡

## Dev Notes

### world_declarations 示例

```json
{
  "world_declarations": [
    {
      "declaration_type": "region_division",
      "content": {
        "parent": "世界",
        "children": ["东胜神洲", "西牛贺洲", "南赡部洲", "北俱芦洲"],
        "division_basis": "方位"
      },
      "narrative_evidence": "世界之间遂分为四大部洲",
      "confidence": "high"
    }
  ]
}
```

### InBetween 约束示例

```
"花果山在东胜神洲和大海之间" →
SpatialRelationship(
    source="花果山",
    target="东胜神洲",
    relation_type="in_between",
    details="花果山位于东胜神洲沿海",
    content={"middle": "花果山", "endpoints": ["东胜神洲", "大海"]}
)
```

### 通用性测试矩阵

| 小说 | 类型 | 预期结构 | 验证重点 |
|------|------|---------|---------|
| 西游记 | 奇幻 | 4区域 + 天界/冥界/副本 | 全功能验证 |
| 红楼梦 | 世情 | 单层 + 建筑层级 | 优雅退化到 V1 |
| 射雕英雄传 | 武侠 | 2-3区域 + 少量副本 | 区域划分 |
| 斗破苍穹 | 修仙网文 | 多层级空间 | 层切换 |
| 都市小说 | 都市 | 单层城市 | 不生成无用层 |

### References

- [Source: _bmad-output/world-map-v2-architecture.md#4.2-增强ChapterFact提取]
- [Source: _bmad-output/world-map-v2-architecture.md#12-开放问题-5-通用性]
- [Source: backend/src/models/chapter_fact.py — 现有 ChapterFact 模型]
- [Source: backend/src/extraction/prompts/extraction_system.txt — 现有提取 prompt]
- [Source: backend/src/services/map_layout_service.py — ConstraintSolver 约束处理]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
