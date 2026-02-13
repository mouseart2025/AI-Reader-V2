# Story 7.4: 分析流水线集成与上下文反馈

Status: ready-for-dev

## Story

As a 系统,
I want 将 WorldStructureAgent 嵌入分析流水线，并将世界结构反馈到提取上下文,
So that 后续章节的提取能利用已构建的世界知识。

## Acceptance Criteria

1. `analysis_service._run_loop_inner()` 在 `validator.validate(fact)` 之后调用 `world_agent.process_chapter()`
2. Agent 错误不阻塞分析流水线（try/except 包裹）
3. `context_summary_builder.build()` 返回的上下文包含"已知世界结构"摘要段落
4. 用户 force 重新分析时 WorldStructure 增量更新（不清空重建）
5. 用西游记前 10 章测试，Agent 能识别四大部洲 + 天界 + 冥界

## Tasks / Subtasks

- [ ] Task 1: 修改 analysis_service.py (AC: #1, #2, #4)
  - [ ] 在 `AnalysisService.__init__()` 中创建 `WorldStructureAgent` 实例字典（per novel_id）
  - [ ] 在 `_run_loop_inner()` 循环开始前，初始化 agent: `agent = WorldStructureAgent(novel_id); await agent.load_or_init()`
  - [ ] 在 `fact = self.validator.validate(fact)` 之后、`chapter_fact_store.insert_chapter_fact()` 之前，添加:
    ```python
    try:
        await agent.process_chapter(chapter_num, chapter["content"], fact)
    except Exception as e:
        logger.warning("World structure agent error for chapter %d: %s", chapter_num, e)
    ```
  - [ ] force=True 时不清空 WorldStructure（agent.load_or_init() 自然加载已有数据）
- [ ] Task 2: 修改 context_summary_builder.py (AC: #3)
  - [ ] 在 `build()` 方法中，聚合完 characters/relationships/locations/items 后，加载 WorldStructure
  - [ ] 新增 `_format_world_structure(ws: WorldStructure) -> str` 方法
  - [ ] 格式化为:
    ```
    ### 已知世界结构
    - 主世界区域: 东胜神洲(东), 西牛贺洲(西), 南赡部洲(南), 北俱芦洲(北)
    - 天界 (celestial): 天宫, 凌霄殿, ...
    - 冥界 (underworld): 地府, 阎罗殿, ...
    - 传送门: 南天门 (主世界 ↔ 天界)
    ```
  - [ ] 世界结构摘要控制在 500 字以内
  - [ ] 如果 WorldStructure 为空或仅有默认 overworld，跳过此段落
- [ ] Task 3: WebSocket 进度广播增强 (AC: #1)
  - [ ] 在 chapter_done 广播中可选地包含 `world_structure_updated: bool` 字段
  - [ ] 不影响现有前端（新增字段，前端可忽略）

## Dev Notes

### 注入点

```python
# analysis_service.py _run_loop_inner() 中的关键位置:
# 行 230: context = await self.context_builder.build(novel_id, chapter_num)
# 行 233: fact = await self.extractor.extract(...)
# 行 241: fact = self.validator.validate(fact)
# ★ 此处注入: await agent.process_chapter(chapter_num, chapter_text, fact)
# 行 246: await chapter_fact_store.insert_chapter_fact(...)
```

### 现有 context_builder 结构

- `context_summary_builder.py:20` — `build()` 方法
- `_ACTIVE_WINDOW = 20`（最近 20 章）
- `_MAX_CHARS = 6000`（上下文最大字符数）
- 输出分段: `### 已知人物` / `### 已知关系` / `### 已知地点` / `### 已知物品`
- 新增 `### 已知世界结构` 段落

### 验证标准

用西游记测试，前 10 章应识别:
- 区域: 东胜神洲(east), 西牛贺洲(west), 南赡部洲(south), 北俱芦洲(north)
- 层: celestial（天宫相关地点）, underworld（地府相关地点）
- 传送门: 至少 1 个（如南天门）

### References

- [Source: backend/src/services/analysis_service.py:155-334 — 分析循环]
- [Source: backend/src/extraction/context_summary_builder.py — 上下文构建]
- [Source: _bmad-output/world-map-v2-architecture.md#5.5-与上下文构建器的集成]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
