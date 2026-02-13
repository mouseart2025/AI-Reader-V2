# Story 7.3: WorldStructureAgent LLM 增量更新

Status: ready-for-dev

## Story

As a 系统,
I want 当检测到高置信度世界观信号时调用 LLM 增量更新世界结构,
So that 系统能理解"世界分为四大部洲"等宏观世界观声明。

## Acceptance Criteria

1. `_should_trigger_llm()` 根据 5 个条件判断是否触发 LLM（前5章、region_division、首次 layer_transition、2+宏观地点、每20章）
2. LLM prompt 包含当前 WorldStructure + 信号原文 + 本章 fact，输出增量操作列表
3. 支持 6 种操作: ADD_REGION, ADD_LAYER, ADD_PORTAL, ASSIGN_LOCATION, UPDATE_REGION, NO_CHANGE
4. LLM 调用失败不中断分析流水线，WorldStructure 保持上一次成功状态
5. 100 章小说的 LLM 世界结构调用次数 ≤ 25 次

## Tasks / Subtasks

- [ ] Task 1: 实现触发条件判断 (AC: #1, #5)
  - [ ] `_should_trigger_llm(chapter_num, signals) -> bool`
  - [ ] 条件列表:
    1. `chapter_num <= 5` — 前 5 章强制触发
    2. 任何信号 signal_type == "region_division" — 世界划分声明
    3. 信号 signal_type == "layer_transition" 且目标层是新层（未在 WorldStructure.layers 中）— 首次进入新空间层
    4. 本章新出现的宏观地点（洲/域/界/国类型）≥ 2 个
    5. `chapter_num % 20 == 0` — 每 20 章例行检查
  - [ ] 满足任一条件即返回 True
- [ ] Task 2: 编写 LLM prompt 模板 (AC: #2)
  - [ ] 新建 `backend/src/extraction/prompts/world_structure_update.txt`
  - [ ] Prompt 结构:
    - 角色: 小说世界观构建专家
    - 输入: 当前 WorldStructure JSON + 本章信号原文摘录 + 本章 locations + 本章 spatial_relationships
    - 任务: 判断是否需要更新世界结构
    - 可执行操作定义（6 种）
    - 输出格式: `{ "operations": [...], "reasoning": "..." }`
  - [ ] 定义操作的 JSON schema（用于 structured output）
- [ ] Task 3: 实现 LLM 调用与操作解析 (AC: #2, #3)
  - [ ] `async def _call_llm_for_update(chapter_num, signals, fact) -> list[dict]`
  - [ ] 构建 prompt（填充模板变量）
  - [ ] 调用 `self.llm.generate()` with structured output schema
  - [ ] 解析返回的 operations 列表
  - [ ] 对 WorldStructure 的当前内容做摘要，控制 context 在 8K token 以内:
    - layers: 仅输出 layer_id, name, layer_type
    - regions: 仅输出 name, cardinal_direction, region_type
    - portals: 仅输出 name, source_layer, target_layer
    - location_region_map: 仅前 50 个条目
- [ ] Task 4: 实现操作应用 (AC: #3)
  - [ ] `_apply_operations(operations: list[dict]) -> None`
  - [ ] ADD_REGION: 在指定层（默认 overworld）中添加 WorldRegion
  - [ ] ADD_LAYER: 创建新 MapLayer（自动生成 layer_id）
  - [ ] ADD_PORTAL: 创建新 Portal（验证 source_layer/target_layer 存在）
  - [ ] ASSIGN_LOCATION: 更新 location_region_map 和/或 location_layer_map
  - [ ] UPDATE_REGION: 修改已有区域的属性
  - [ ] NO_CHANGE: 不做任何操作
  - [ ] 每个操作单独 try/except，部分失败不影响其他操作
- [ ] Task 5: 错误处理与容错 (AC: #4)
  - [ ] LLM 调用 timeout=120s
  - [ ] JSON 解析失败 → 记录 warning 日志，返回空操作列表
  - [ ] 操作应用失败 → 记录 warning 日志，跳过该操作
  - [ ] 整个 process_chapter 用 try/except 包裹，任何错误都不抛出到 analysis_service

## Dev Notes

### LLM 调用约束

- 使用 `src.infra.llm_client.get_llm_client()` 获取 LLM 客户端
- 参考 `chapter_fact_extractor.py:138-164` 的 `_call_and_parse()` 模式
- 使用 structured output (format=schema)，temperature=0.1
- timeout=120s, max_tokens=4096, num_ctx=8192（世界结构更新比章节提取简单）

### Context 预算

- qwen3:8b 的 context window = 16384 tokens
- 当前 WorldStructure 摘要目标 ≤ 2000 tokens
- 信号原文摘录 ≤ 1000 tokens（每个信号 200 字 × 5 个）
- 本章 locations+spatial_relationships ≤ 2000 tokens
- prompt 模板 ≤ 1500 tokens
- 总计 ≤ 6500 tokens，留 2000 给输出

### References

- [Source: _bmad-output/world-map-v2-architecture.md#5.3-阶段B-LLM世界结构更新]
- [Source: _bmad-output/world-map-v2-architecture.md#5.6-LLM调用频率控制]
- [Source: backend/src/extraction/chapter_fact_extractor.py — LLM 调用模式]
- [Source: backend/src/infra/llm_client.py — LLM 客户端接口]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
