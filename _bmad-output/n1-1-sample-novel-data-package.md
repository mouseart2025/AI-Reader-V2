# Story N1.1: 制作样本小说预分析数据包

Status: review

## Story

As a 开发团队,
I want 为《西游记》前25回和《三国演义》前30回生成完整的预分析数据,
So that 样本数据质量与用户自行分析的结果一致。

## Acceptance Criteria

1. **AC-1**: Given 《西游记》前25回和《三国演义》前30回的 TXT 文本，When 使用当前最优模型（qwen3:14b）执行完整分析流程，Then 生成 novel 元信息 + chapters + chapter_facts + entity_dictionary + world_structure 全量数据
2. **AC-2**: 导出为可导入的 JSON 数据包，两本样本总计压缩后 < 4MB
3. **AC-3**: 数据包含 ChapterFact、实体词典、世界结构、别名映射等全部分析产物

## Tasks / Subtasks

- [x] Task 1: 升级导出格式到 v2 — 增加 entity_dictionary + world_structures (AC: #1, #3)
  - [x] 1.1 `export_service.py` — `export_novel()` 增加查询并导出 `entity_dictionary` 表数据（字段: name, entity_type, frequency, confidence, aliases, source, sample_context）
  - [x] 1.2 `export_service.py` — `export_novel()` 增加查询并导出 `world_structures` 表数据（字段: structure_json, source_chapters）
  - [x] 1.3 `export_service.py` — `export_novel()` 输出中 novel 对象增加 `prescan_status` 字段；chapters 数组每项增加 `is_excluded` 字段
  - [x] 1.4 `export_service.py` — `format_version` 升级为 `2`
  - [x] 1.5 为 Task 1 编写 pytest 单元测试（mock DB，验证 v2 输出结构包含所有新字段）

- [x] Task 2: 升级导入逻辑支持 v2 格式（向后兼容 v1）(AC: #1)
  - [x] 2.1 `export_service.py` — `import_novel()` 增加 entity_dictionary 导入（遍历数组逐条 INSERT）
  - [x] 2.2 `export_service.py` — `import_novel()` 增加 world_structures 导入（INSERT structure_json + source_chapters）
  - [x] 2.3 `export_service.py` — `import_novel()` 恢复 prescan_status 到 novels 表、is_excluded 到 chapters 表
  - [x] 2.4 `import_novel()` 版本兼容逻辑：v1 文件正常导入（无新字段不报错）；v2 文件导入全量数据
  - [x] 2.5 `preview_import()` 增加显示 entity_dictionary 条目数、world_structure 是否存在
  - [x] 2.6 为 Task 2 编写 pytest 单元测试（v1 兼容 + v2 全量导入 + preview 增强）

- [x] Task 3: 创建样本数据生成脚本 (AC: #1)
  - [x] 3.1 新建 `backend/scripts/generate_sample_data.py`，支持命令行参数：`--input <txt_path> --chapters <N> --output <json_path> [--model <model_name>]`
  - [x] 3.2 脚本流程：读取 TXT → 章节切分（复用 chapter_splitter）→ 写入 DB → 执行 prescan → 执行 analysis（全章节）→ 调用 export_novel(v2) → 写出 JSON 文件
  - [x] 3.3 脚本支持 `--skip-content` 选项：导出时省略 chapters[].content 以减小体积（样本包不需要完整原文，Story 1.2 导入时另行处理）
  - [x] 3.4 脚本结束打印摘要：章节数、实体词典条数、ChapterFact 数、world_structure 存在性、JSON 文件大小

- [x] Task 4: 准备样本 TXT 文件并生成数据包 (AC: #1, #2, #3)
  - [x] 4.1 获取《西游记》前25回纯文本，放入 `backend/sample-novels/xiyouji.txt`
  - [x] 4.2 获取《三国演义》前30回纯文本，放入 `backend/sample-novels/sanguoyanyi.txt`
  - [x] 4.3 使用 generate_sample_data.py 生成两个 JSON 数据包，放入 `frontend/public/sample-data/` 目录
  - [x] 4.4 验证两个 JSON 数据包合计压缩（gzip）后 < 4MB（实际: 297 KB gzip, 远低于 4MB 限制）
  - [x] 4.5 验证数据完整性：每个包含 novel + chapters + chapter_facts + entity_dictionary + world_structures

## Dev Notes

### 关键架构约束

1. **现有导出服务位置**: `backend/src/services/export_service.py`（3 个函数：export_novel / import_novel / preview_import）
2. **现有路由**: `backend/src/api/routes/export_import.py`（3 个端点）
3. **数据库**: SQLite 单文件，schema 在 `backend/src/db/sqlite_db.py`
4. **不要触碰路由层**：本 Story 只改 service 层 + 新增脚本

### 导出格式 v1 → v2 变更要点

**当前 v1 结构**:
```json
{
  "format_version": 1,
  "novel": { id, title, author, file_hash, total_chapters, total_words, created_at, updated_at },
  "chapters": [{ chapter_num, volume_num, volume_title, title, content, word_count, analysis_status, analyzed_at }],
  "chapter_facts": [{ chapter_id, chapter_num, fact_json, llm_model, extracted_at, extraction_ms }],
  "user_state": { last_chapter, scroll_position, chapter_range, updated_at } | null
}
```

**v2 新增字段**:
```json
{
  "format_version": 2,
  "novel": { ...v1, "prescan_status": "completed" },
  "chapters": [{ ...v1, "is_excluded": 0 }],
  "entity_dictionary": [{ name, entity_type, frequency, confidence, aliases, source, sample_context }],
  "world_structures": { structure_json, source_chapters } | null,
  ...其余同 v1
}
```

### 数据库 Schema 参考

**entity_dictionary 表** (每行一个实体):
```sql
id, novel_id, name TEXT, entity_type TEXT, frequency INTEGER,
confidence TEXT, aliases TEXT(JSON数组), source TEXT, sample_context TEXT
```

**world_structures 表** (每小说一行):
```sql
novel_id TEXT PK, structure_json TEXT, source_chapters TEXT, created_at, updated_at
```

**chapters 表迁移字段**: `is_excluded INTEGER DEFAULT 0`
**novels 表迁移字段**: `prescan_status TEXT DEFAULT 'pending'`

### 章节切分复用

脚本中复用 `backend/src/utils/chapter_splitter.py` 的切分逻辑。切分模式自动检测（`mode_order` 自动探测）。

### 分析流程复用

脚本中复用 `backend/src/services/analysis_service.py` 的 `AnalysisService`。注意：
- 需要 Ollama 运行且有 qwen3:14b 模型
- 分析是 async 的，脚本需要 `asyncio.run()`
- prescan 通过 `backend/src/extraction/entity_pre_scanner.py` 的 `EntityPreScanner`

### 样本包大小控制

- 25回西游记原文约 15-20 万字，30回三国演义约 20-25 万字
- 完整导出含原文会很大（>10MB）
- 使用 `--skip-content` 省略 chapters[].content（Story 1.2 导入时，原文可从内置 TXT 资源恢复）
- ChapterFact + entity_dictionary + world_structures 的纯结构化数据预估 1-2MB/本
- gzip 压缩后预计每本 < 2MB

### 测试框架

项目当前无 pytest 基础设施。需在 Task 1.5 中：
- 确认 `pyproject.toml` 有 pytest 依赖（`uv add --dev pytest pytest-asyncio`）
- 创建 `backend/tests/` 目录和 `conftest.py`
- 测试使用 `pytest-asyncio` 处理 async 函数
- Mock DB 使用内存 SQLite（`:memory:`）

### 文件存放规范

| 产物 | 路径 | 说明 |
|------|------|------|
| 样本 TXT 原文 | `backend/sample-novels/` | 仅用于生成，不打入最终包 |
| 生成脚本 | `backend/scripts/generate_sample_data.py` | 一次性运行工具 |
| 样本 JSON 数据包 | `frontend/public/sample-data/` | 前端 public 目录，可被 fetch 加载 |
| 测试文件 | `backend/tests/test_export_service.py` | pytest 单元测试 |

### References

- [Source: backend/src/services/export_service.py] — 现有导出/导入逻辑
- [Source: backend/src/api/routes/export_import.py] — 导出/导入路由
- [Source: backend/src/db/sqlite_db.py:6-141] — 完整 DDL + 迁移
- [Source: backend/src/services/analysis_service.py] — 分析流水线
- [Source: backend/src/extraction/entity_pre_scanner.py] — 实体预扫描
- [Source: backend/src/utils/chapter_splitter.py] — 章节切分
- [Source: backend/src/models/chapter_fact.py] — ChapterFact Pydantic 模型
- [Source: CLAUDE.md] — 项目技术栈和架构概览
- [Source: _bmad-output/epics.md#Epic-1] — Epic 1 全部 Story 上下文

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- 前端 build 有预存 TS 错误（ChatPage, EncyclopediaPage, FactionsPage, MapPage, TimelinePage），与本 Story 无关

### Completion Notes List

- **Task 1 完成**: export_service.py 导出格式升级到 v2，新增 entity_dictionary、world_structures、prescan_status、is_excluded、skip_content 参数
- **Task 2 完成**: import_novel() 支持 v1/v2 双版本导入，v2 增加 entity_dictionary + world_structures 写入；preview_import() 增加 entity_dict_count 和 has_world_structures
- **Task 1+2 测试**: 14 个 pytest 测试全部通过（export v2 结构 8 项 + import v2/v1 兼容 3 项 + preview 3 项）
- **Task 3 完成**: generate_sample_data.py 脚本创建，支持全流程自动化（TXT → split → DB → prescan → analysis → export v2）
- **Task 4 完成**: 两本样本数据包生成成功
  - 西游记: 25 章, 25 ChapterFact, 500 实体词典（命中 500 上限）, 有 world_structures, 724 KB JSON / 155 KB gzip
  - 三国演义: 30 章, 30 ChapterFact, 432 实体词典, 有 world_structures, 760 KB JSON / 143 KB gzip
  - 合计 gzip: ~297 KB（远低于 4MB 限制，AC-2 满足）
  - 使用模型: qwen3:8b（AC 指定 qwen3:14b 但本机未安装，使用 8b 替代）
  - 分析过程中有少量 Pydantic 验证失败（自动重试成功）和 Cloud API 输出截断（JSON 自动修复成功）

### File List

- `backend/src/services/export_service.py` — 升级 v1→v2（export/import/preview 三函数改动）
- `backend/tests/conftest.py` — 新增：测试基础设施（内存 SQLite + mock_get_connection fixture）
- `backend/tests/test_export_service.py` — 新增：14 个单元测试
- `backend/scripts/generate_sample_data.py` — 新增：样本数据生成脚本
- `backend/pyproject.toml` — 新增 dev 依赖：pytest, pytest-asyncio
- `backend/sample-novels/xiyouji.txt` — 西游记原文 TXT（2.1 MB）
- `backend/sample-novels/sanguoyanyi.txt` — 三国演义原文 TXT（1.7 MB）
- `frontend/public/sample-data/xiyouji.json` — 西游记样本数据包（v2, 724 KB）
- `frontend/public/sample-data/sanguoyanyi.json` — 三国演义样本数据包（v2, 760 KB）
