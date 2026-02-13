# Story 7.2: WorldStructureAgent 信号扫描与启发式更新

Status: ready-for-dev

## Story

As a 系统,
I want 在每章分析后扫描世界观构建信号并进行轻量启发式更新,
So that 系统能自动识别天界/冥界/洞府等空间层和宏观区域划分。

## Acceptance Criteria

1. `_scan_signals()` 能从原文和 ChapterFact 中检测 4 种信号类型（region_division, layer_transition, instance_entry, macro_geography）
2. 每个信号包含原文摘录（≤200字）、章节号、置信度
3. `_apply_heuristic_updates()` 能基于关键词自动将地点分配到正确的层（celestial/underworld/instance）
4. 信号扫描纯本地执行，不调用 LLM，单章处理时间 < 50ms
5. agent 能正确初始化（加载已有或创建新的 WorldStructure）

## Tasks / Subtasks

- [ ] Task 1: 创建 WorldStructureAgent 类骨架 (AC: #5)
  - [ ] 新建 `backend/src/services/world_structure_agent.py`
  - [ ] `__init__(self, novel_id: str)` — 保存 novel_id，初始化空状态
  - [ ] `async def load_or_init(self) -> None` — 从 DB 加载已有结构，或创建默认结构（仅 overworld 层）
  - [ ] `async def process_chapter(self, chapter_num, chapter_text, fact) -> None` — 主入口（本 Story 实现信号扫描 + 启发式部分，LLM 部分在 7.3）
- [ ] Task 2: 实现信号扫描 (AC: #1, #2, #4)
  - [ ] 定义信号检测关键词和正则规则:
    - `region_division`: 关键词 ["分为", "划为"] + 模式 `(分|划)为[\d一二三四五六七八九十]+[大]?(部洲|大陆|界|域|国)`
    - `layer_transition`: 关键词 ["上了天", "到天宫", "进了地府", "入冥界", "潜入海底"] + location type 关键词 ["天宫", "天庭", "天界", "地府", "冥界", "海底", "龙宫"]
    - `instance_entry`: 关键词 ["走进洞", "入洞", "进了洞", "进入阵"] + location type 正则 `(洞|府|宫|阵|秘境|幻境|禁地)`
    - `macro_geography`: 新出现的宏观地点（type 含 洲/域/界/国 且 fact.locations 中存在）
  - [ ] `_scan_signals(chapter_num, chapter_text, fact) -> list[WorldBuildingSignal]`
  - [ ] 为每个检测到的信号提取周围 ≤200 字的原文上下文
- [ ] Task 3: 实现启发式更新 (AC: #3)
  - [ ] `_apply_heuristic_updates(chapter_num, fact) -> None`
  - [ ] 地点层分配规则:
    - 名含 `_CELESTIAL_KEYWORDS`（天宫/天庭/天门/天界/三十三天/大罗天/离恨天/兜率宫/凌霄殿/蟠桃园/瑶池/灵霄宝殿/南天门/九天应元府等）→ 分配到 celestial 层
    - 名含 `_UNDERWORLD_KEYWORDS`（地府/冥界/幽冥/阴司/阴曹/黄泉/奈何桥/阎罗殿/森罗殿/枉死城等）→ 分配到 underworld 层
    - type 含 洞/府 且有 parent 地点 → 标记为 instance 候选
  - [ ] 区域分配规则:
    - parent 是已知区域 → 分配到该区域
    - 名称含方位词（东/西/南/北）且为宏观类型 → 推断 cardinal_direction
  - [ ] 自动创建层（如果 celestial/underworld 层不存在但有地点被分配）
  - [ ] 更新 WorldStructure 的 location_layer_map 和 location_region_map
  - [ ] 调用 world_structure_store.save() 持久化

## Dev Notes

### 关键依赖

- 依赖 Story 7.1 的 WorldStructure 模型和 world_structure_store
- 复用 `map_layout_service.py` 中已有的 `_CELESTIAL_KEYWORDS` 和 `_UNDERWORLD_KEYWORDS` 定义（考虑抽取为共享常量）

### 现有代码参考

- `backend/src/services/map_layout_service.py:67-76` — 现有的 celestial/underworld 关键词定义
- `backend/src/models/chapter_fact.py` — LocationFact, SpatialRelationship 模型
- `backend/src/extraction/fact_validator.py` — 后验证模式参考

### 性能约束

- 信号扫描必须是纯 CPU 操作（正则匹配 + 关键词查找），不涉及 IO 或 LLM
- 目标: 单章 < 50ms

### References

- [Source: _bmad-output/world-map-v2-architecture.md#5.3-Agent的两阶段处理]
- [Source: backend/src/services/map_layout_service.py:67-76 — 现有关键词]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
