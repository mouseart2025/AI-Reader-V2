# Story 8.3: Phase 2 LLM 分类

Status: ready-for-dev

## Story

As a 系统,
I want 用 LLM 对统计扫描产生的候选词进行分类和别名关联,
So that 词典中的实体类型和别名信息更准确，显著提升提取质量。

## Acceptance Criteria

1. 实现 LLM 分类 prompt，输入候选词列表 + 频次 + 上下文，输出分类结果 + 别名组 + 拒绝词
2. 候选数量控制：取 Top-300（按频次），每条附 ≤ 50 字上下文
3. LLM 输出为 JSON：`{ entities: [...], alias_groups: [[...]], rejected: [...] }`
4. LLM 分类结果合并回候选列表：更新 entity_type、confidence、aliases
5. rejected 列表中的词从候选中移除
6. alias_groups 中的词互相设置 aliases 字段
7. Phase 2 LLM 调用失败时降级为仅使用 Phase 1 统计结果（不阻塞流程）
8. 总输入 token ≤ 5000，单次 LLM 调用

## Tasks / Subtasks

- [ ] Task 1: 创建 prompt 模板 (AC: #1, #2)
  - [ ] 新建 `backend/src/extraction/prescan_prompts.py`
  - [ ] 定义 `build_classification_prompt(candidates: list[EntityDictEntry]) -> tuple[str, str]`
  - [ ] 返回 (system_prompt, user_prompt)
  - [ ] system_prompt：角色定义 + JSON 输出格式说明
  - [ ] user_prompt：候选列表表格（词 | 频次 | 来源 | 上下文） + 输出要求

- [ ] Task 2: 实现 LLM 分类调用 (AC: #3, #8)
  - [ ] 在 `entity_pre_scanner.py` 中添加 `async def _classify_with_llm(candidates: list[EntityDictEntry]) -> dict`
  - [ ] 筛选 Top-300 候选
  - [ ] 调用 `get_llm_client().generate()` with `format={"type": "object"}` (JSON mode)
  - [ ] 解析 LLM 返回的 JSON

- [ ] Task 3: 实现结果合并 (AC: #4, #5, #6)
  - [ ] 方法 `_merge_llm_results(candidates: list[EntityDictEntry], llm_result: dict) -> list[EntityDictEntry]`
  - [ ] 遍历 `llm_result["entities"]`，更新对应候选的 entity_type、confidence
  - [ ] 遍历 `llm_result["alias_groups"]`，为组内每个词设置 aliases = 组内其他词
  - [ ] 移除 `llm_result["rejected"]` 中的词

- [ ] Task 4: 实现降级策略 (AC: #7)
  - [ ] 在 scan() 主流程中 try/except 包裹 Phase 2
  - [ ] 失败时 log warning，直接使用 Phase 1 候选列表写入词典
  - [ ] prescan_status 仍设为 completed（Phase 1 数据仍有价值）

## Dev Notes

- 复用现有 `get_llm_client()` 工厂（自动选择 Ollama / OpenAI 兼容客户端）
- LLM 分类 prompt 的 JSON schema 无需严格强制——用 `format={"type": "object"}` 即可，手动解析字段
- 如果 LLM 返回的 JSON 缺少某个字段（如没有 alias_groups），应安全降级而非报错
- 本地 Ollama (qwen3:8b) 也能做分类，但准确率不如云端模型。不论哪个 provider 都走同一流程
- max_tokens 设为 4096（分类输出不会太长）

### Project Structure Notes

- `backend/src/extraction/prescan_prompts.py` — 新文件，与 `extraction_system.txt` 同级但使用 Python 构造 prompt（非 .txt 模板）
- LLM 分类方法集成在 `entity_pre_scanner.py` 中

### References

- [Source: _bmad-output/entity-prescan-architecture.md#4.3 Phase 2 详细设计]
- [Source: backend/src/infra/llm_client.py] — LLM 客户端接口
- [Source: backend/src/infra/openai_client.py] — 云端客户端实现

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
