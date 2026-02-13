# Story 8.4: 流水线集成

Status: ready-for-dev

## Story

As a 用户,
I want 预扫描词典自动集成到分析流水线中，无需手动干预,
So that 每次分析都能自动利用全书实体参考信息，提升提取质量。

## Acceptance Criteria

1. 小说导入确认后自动触发预扫描（后台任务，不阻塞导入响应）
2. 分析启动前检查 prescan_status：pending → 同步触发；running → 等待（timeout=120s）；failed → 降级为无词典；completed → 加载词典
3. ContextSummaryBuilder.build() 新增词典注入：在 context 末尾追加"本书高频实体参考"段落
4. 词典注入格式：`- {name}（{type}，出现{freq}次） 别名：{aliases}`，Top-100 实体
5. 词典注入增加的 token 预算 ≤ 2000 token（在 context 的 18K char 预算内）
6. 预扫描超时或失败不阻塞分析流程（降级为无词典模式继续）
7. 重新分析（force=true）不触发重新预扫描
8. 清除分析数据时保留词典

## Tasks / Subtasks

- [ ] Task 1: 导入后触发预扫描 (AC: #1)
  - [ ] 修改 `backend/src/services/novel_service.py` 的 `confirm_import()` 方法
  - [ ] 在返回前添加 `asyncio.create_task(entity_pre_scanner.scan(novel_id))`
  - [ ] 确保 import EntityPreScanner 和实例化

- [ ] Task 2: 分析启动前检查 (AC: #2, #6, #7)
  - [ ] 修改 `backend/src/services/analysis_service.py` 的 `start()` 方法
  - [ ] 在创建 task 之前：
    ```python
    status = await entity_dictionary_store.get_prescan_status(novel_id)
    if status == "pending":
        try:
            await entity_pre_scanner.scan(novel_id)
        except Exception:
            logger.warning("Pre-scan failed, continuing without dictionary")
    elif status == "running":
        # 等待预扫描完成，最多 120s
        for _ in range(24):
            await asyncio.sleep(5)
            status = await entity_dictionary_store.get_prescan_status(novel_id)
            if status != "running":
                break
    ```
  - [ ] force=true 时不检查、不重新触发预扫描

- [ ] Task 3: Context 注入词典 (AC: #3, #4, #5)
  - [ ] 修改 `backend/src/extraction/context_summary_builder.py` 的 `build()` 方法
  - [ ] 在现有 context 构建完成后，追加词典段落：
    ```python
    dictionary = await entity_dictionary_store.get_all(novel_id)
    if dictionary:
        dict_lines = ["\n\n## 本书高频实体参考",
                      "以下实体在全书中高频出现，提取时请特别注意不要遗漏（仅供参考，仍以原文为准）："]
        for entry in dictionary[:100]:  # Top-100
            line = f"- {entry.name}（{entry.entity_type}，出现{entry.frequency}次）"
            if entry.aliases:
                line += f" 别名：{'、'.join(entry.aliases)}"
            dict_lines.append(line)
        context += "\n".join(dict_lines)
    ```

- [ ] Task 4: 清除分析数据时保留词典 (AC: #8)
  - [ ] 检查现有的"清除分析数据"逻辑（`analysis_service` 或 routes 中）
  - [ ] 确保清除操作只删除 chapter_facts、analysis_tasks、world_structures 等，不删除 entity_dictionary
  - [ ] 确保 prescan_status 不被重置

## Dev Notes

- `asyncio.create_task()` 在导入后触发预扫描时，需确保扫描失败不会导致未处理的异常。scan() 方法内部应捕获所有异常并设置 prescan_status='failed'
- context_summary_builder.py 的 `build()` 目前是同步返回 context 字符串。词典查询需要 async DB 调用，需确认 build() 是否为 async（从代码看是 async def）
- 词典注入应在 world structure summary 之后追加，作为 context 的最后一个段落
- 等待预扫描的 polling 机制（每 5s 查一次，最多 24 次 = 120s）简单可靠，不需要事件机制

### Project Structure Notes

- 修改 3 个现有文件：novel_service.py、analysis_service.py、context_summary_builder.py
- 无新增文件

### References

- [Source: _bmad-output/entity-prescan-architecture.md#5. 集成设计]
- [Source: backend/src/services/novel_service.py] — confirm_import() 方法
- [Source: backend/src/services/analysis_service.py] — start() 和 _run_loop_inner() 方法
- [Source: backend/src/extraction/context_summary_builder.py] — build() 方法

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
