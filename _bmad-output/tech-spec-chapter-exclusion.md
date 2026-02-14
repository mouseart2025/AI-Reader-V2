---
title: '章节排除 — 非正文内容自动识别与手动排除'
slug: 'chapter-exclusion'
created: '2026-02-14'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.9+/FastAPI', 'React 19/TypeScript 5.9', 'SQLite/aiosqlite', 'Zustand 5']
files_to_modify:
  - backend/src/utils/chapter_classifier.py (NEW)
  - backend/src/services/novel_service.py
  - backend/src/services/analysis_service.py
  - backend/src/db/chapter_store.py
  - backend/src/db/sqlite_db.py
  - backend/src/api/routes/chapters.py
  - backend/src/api/schemas/novels.py
  - frontend/src/api/types.ts
  - frontend/src/api/client.ts
  - frontend/src/components/shared/UploadDialog.tsx
code_patterns:
  - 'async everywhere: aiosqlite for DB'
  - 'Router pattern: APIRouter(prefix="/api/...", tags=[...])'
  - 'Zustand stores: { data, loading, error, fetchXxx, setXxx }'
  - 'Error messages in Chinese'
  - 'Pydantic BaseModel for schemas'
  - 'DDL migration: ALTER TABLE ADD COLUMN with try/except for idempotency'
test_patterns: []
---

# Tech-Spec: 章节排除 — 非正文内容自动识别与手动排除

**Created:** 2026-02-14

## Overview

### Problem Statement

很多小说 TXT 文件的开头和结尾包含非正文内容（文学导读、作者介绍、简介、附录、纪念文章、感悟、其他小说节选等）。当前系统将这些内容当作正常章节分析，导致：

1. **实体污染**：导读中引用的其他小说的角色/地点被提取为本小说实体（如平凡的世界分析出西游记的花果山、石猴）
2. **关系错乱**：非正文中的人物关系与正文混淆
3. **地图/可视化失真**：无关地点出现在世界地图上
4. **分析资源浪费**：LLM 处理无意义章节

真实案例：平凡的世界 TXT 文件前 3 章是文学评论和西游记内容，分析后产生了四大部洲、花果山等西游记实体。

### Solution

在上传预览和分析流程中增加「章节排除」能力：

1. **自动识别**：上传分章后，自动检测可疑的非正文章节（基于关键词/内容特征），标记高亮
2. **手动调整**：用户可在预览列表中勾选/取消排除标记，支持批量操作头尾章节
3. **分析跳过**：被标记为 `excluded` 的章节不参与分析流程
4. **事后排除**：分析完成后仍可标记排除，同时清理已产生的 chapter_facts

### Scope

**In Scope:**
- 非正文章节自动检测算法（纯规则：关键词 + 内容特征 + 位置）
- 上传预览阶段的排除交互（checkbox + 自动建议高亮）
- `chapters` 表新增 `is_excluded` 列
- 分析流程跳过 excluded 章节
- 确认导入时传递排除列表
- 事后排除/恢复 API + 关联 chapter_facts 清理
- 章节列表 API 返回 is_excluded 字段

**Out of Scope:**
- LLM 辅助判断（规则够用）
- 排除章节内容的元信息提取
- 跨小说去重
- 前端章节管理页的事后排除 UI（本期仅做 API，前端可后续迭代）

## Context for Development

### Codebase Patterns

- **分章**: `chapter_splitter.py` `split_chapters()` 返回 `list[ChapterInfo]`，无检测逻辑
- **预览缓存**: `novel_service.py` `_upload_cache` dict，key=file_hash，TTL=30min，存 `_CachedUpload(preview, chapters, raw_text)`
- **确认导入**: `novel_service.py` `confirm_import()` → `novel_store.insert_chapters(novel_id, chapters)` 批量写入所有章节
- **分析循环**: `analysis_service.py` `_run_loop_inner()` line 252: `if not force and chapter["analysis_status"] == "completed": continue`
- **前端预览表**: `UploadDialog.tsx` lines 466-492: 简单 `<table>` 渲染章节列表，无 checkbox/交互
- **Schema**: `ChapterPreviewItem` 仅有 `chapter_num, title, word_count` 三个字段
- **DDL 迁移**: `sqlite_db.py` `init_db()` 用 `ALTER TABLE ADD COLUMN` + try/except 做幂等迁移

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `backend/src/utils/chapter_splitter.py` | 分章逻辑，ChapterInfo 数据类 |
| `backend/src/services/novel_service.py` | `parse_upload()` 返回预览, `confirm_import()` 写入 DB |
| `backend/src/services/analysis_service.py` | `_run_loop_inner()` 分析循环，line 252 跳过逻辑 |
| `backend/src/db/chapter_store.py` | 章节查询，无 update/exclude 函数 |
| `backend/src/db/novel_store.py` | `insert_chapters()` 批量插入 |
| `backend/src/db/sqlite_db.py` | DDL schema + `init_db()` 迁移 |
| `backend/src/api/routes/chapters.py` | 6 个章节端点，无状态修改 |
| `backend/src/api/schemas/novels.py` | `ChapterPreviewItem`, `UploadPreviewResponse`, `ConfirmImportRequest` |
| `frontend/src/api/types.ts` | `ChapterPreview`, `Chapter`, `UploadPreviewResponse` TypeScript 类型 |
| `frontend/src/api/client.ts` | API 调用函数 |
| `frontend/src/components/shared/UploadDialog.tsx` | 上传对话框，预览阶段章节表 |

### Technical Decisions

1. **排除状态**: 新增 `is_excluded INTEGER DEFAULT 0`，独立于 `analysis_status`。语义不同：excluded 是用户决策，status 是系统状态
2. **检测时机**: 分章后、预览返回前。检测结果附加到 `ChapterPreviewItem.is_suspect` 字段
3. **检测算法**: 纯规则引擎，不引入 LLM。基于关键词匹配 + 内容特征（对话密度、人名密度）+ 位置（首尾章）
4. **确认导入**: `ConfirmImportRequest` 新增 `excluded_chapters: list[int]`，导入时设置 `is_excluded=1`
5. **事后排除**: `PATCH /api/novels/{id}/chapters/exclude` 批量端点，同时删除关联 `chapter_facts`
6. **事后恢复**: 同一端点取消排除，`analysis_status` 重置为 `pending` 以便重新分析

## Implementation Plan

### Tasks

- [ ] **Task 1: 创建非正文检测模块 `chapter_classifier.py`**
  - File: `backend/src/utils/chapter_classifier.py` (NEW)
  - Action: 创建 `classify_chapters(chapters: list[ChapterInfo]) -> list[bool]` 函数
  - 检测规则:
    1. **标题关键词**: 匹配「序」「前言」「导读」「简介」「作者简介」「附录」「后记」「尾声」「完本感言」「创作谈」「纪念」「书评」「读后感」「出版说明」等
    2. **内容关键词**: 包含「作者」「出版」「ISBN」「版权」「转载」「编辑」「校对」等出版元数据词汇
    3. **位置启发**: 第 1 章（序章/prologue）且字数 < 3000 时为可疑；最后 2 章标题含关键词时为可疑
    4. **内容特征**: 对话行占比 < 5% 且无对话引号（`"` `"` `「」`），很可能是评论/说明文
    5. **章节标题为「序章」**: `_split_by_matches` 自动生成的标题，内容通常是简介
  - 返回值: 与 chapters 等长的 bool 列表，True = 可疑非正文
  - Notes: 纯函数，不依赖 DB 或网络

- [ ] **Task 2: 修改 `ChapterPreviewItem` schema 新增 `is_suspect` 字段**
  - File: `backend/src/api/schemas/novels.py`
  - Action: `ChapterPreviewItem` 新增 `is_suspect: bool = False`
  - Action: `ConfirmImportRequest` 新增 `excluded_chapters: list[int] = []`
  - Notes: is_suspect 是检测建议，excluded_chapters 是用户最终决策

- [ ] **Task 3: 修改 `novel_service.py` 集成检测 + 排除导入**
  - File: `backend/src/services/novel_service.py`
  - Action (parse_upload): 分章后调用 `classify_chapters()`，将结果写入 `ChapterPreviewItem.is_suspect`
  - Action (confirm_import): 读取 `excluded_chapters` 参数，导入时为被排除章节设置 `is_excluded=1`
  - Action (re_split): 同 parse_upload，重新分章后也要跑 classify
  - Anchor: `parse_upload()` line ~102 构建 chapter_previews 处
  - Anchor: `confirm_import()` line 166 调用 insert_chapters 处

- [ ] **Task 4: 修改 DB schema + 迁移**
  - File: `backend/src/db/sqlite_db.py`
  - Action: `init_db()` 新增迁移: `ALTER TABLE chapters ADD COLUMN is_excluded INTEGER DEFAULT 0`
  - Pattern: 与 `prescan_status` 迁移相同的 try/except 幂等模式

- [ ] **Task 5: 修改 `novel_store.insert_chapters()` 支持排除标记**
  - File: `backend/src/db/novel_store.py`
  - Action: `insert_chapters()` 新增可选参数 `excluded_nums: set[int] | None = None`
  - Action: INSERT 语句新增 `is_excluded` 列，根据 `ch.chapter_num in excluded_nums` 设值
  - Notes: 保持向后兼容，不传则全部为 0

- [ ] **Task 6: 修改 `chapter_store.py` 新增排除/恢复 + 查询函数**
  - File: `backend/src/db/chapter_store.py`
  - Action: 新增 `set_chapters_excluded(novel_id, chapter_nums: list[int], excluded: bool)` — 批量设置 is_excluded
  - Action: 新增 `delete_chapter_facts(novel_id, chapter_nums: list[int])` — 删除指定章节的 chapter_facts
  - Action: `list_chapters()` SELECT 新增 `is_excluded` 字段
  - Notes: set_chapters_excluded 同时处理 analysis_status: 排除时不改 status；恢复时如果 status=completed 则重置为 pending

- [ ] **Task 7: 修改分析循环跳过 excluded 章节**
  - File: `backend/src/services/analysis_service.py`
  - Action: `_run_loop_inner()` line 252 附近，在 completed 检查之前新增:
    ```python
    if chapter.get("is_excluded"):
        logger.debug("Skipping excluded chapter %d", chapter_num)
        # broadcast progress but mark as skipped
        ...
        continue
    ```
  - Notes: excluded 优先级高于 force 参数（force 重分析不应包含被排除章节）

- [ ] **Task 8: 新增排除 API 端点**
  - File: `backend/src/api/routes/chapters.py`
  - Action: 新增 `PATCH /api/novels/{novel_id}/chapters/exclude`
  - Request body:
    ```python
    class ChapterExcludeRequest(BaseModel):
        chapter_nums: list[int]
        excluded: bool  # True=排除, False=恢复
    ```
  - Logic:
    1. 调用 `set_chapters_excluded()`
    2. 如果 excluded=True，调用 `delete_chapter_facts()` 清理已分析数据
    3. 如果 excluded=False，重置 analysis_status 为 pending
    4. 返回更新后的章节列表

- [ ] **Task 9: 修改前端 TypeScript 类型**
  - File: `frontend/src/api/types.ts`
  - Action: `ChapterPreview` 新增 `is_suspect?: boolean`
  - Action: `Chapter` 新增 `is_excluded?: boolean`
  - Action: `ConfirmImportRequest` (如果有) 新增 `excluded_chapters?: number[]`
  - File: `frontend/src/api/client.ts`
  - Action: `confirmImport()` 传递 excluded_chapters 参数
  - Action: 新增 `excludeChapters(novelId, chapterNums, excluded)` API 调用

- [ ] **Task 10: 修改 UploadDialog 预览阶段 UI**
  - File: `frontend/src/components/shared/UploadDialog.tsx`
  - Action: 章节表格新增 checkbox 列（排除/包含切换）
  - Action: is_suspect=true 的行默认勾选排除 + 背景高亮（淡黄色）
  - Action: 表头增加「全选/取消」按钮或批量操作
  - Action: 预览统计区显示「{n} 章被标记为非正文（建议排除）」提示文案
  - Action: `confirmImport` 调用时传递被勾选排除的 chapter_nums
  - Anchor: lines 466-492 章节表格
  - Notes: 使用 shadcn/ui Checkbox 组件; 排除章节的 word_count 不计入 total_words 显示

### Acceptance Criteria

- [ ] **AC 1**: Given 一个包含文学导读（序章）的小说 TXT, When 上传并预览, Then 导读章节的 `is_suspect` 为 true 且在预览表中高亮显示
- [ ] **AC 2**: Given 预览阶段有 is_suspect 章节, When 用户不做修改直接确认导入, Then 被标记的章节导入时 `is_excluded=1`
- [ ] **AC 3**: Given 用户在预览阶段取消某个 is_suspect 章节的排除勾选, When 确认导入, Then 该章节 `is_excluded=0`
- [ ] **AC 4**: Given 用户在预览阶段手动勾选排除一个正常章节, When 确认导入, Then 该章节 `is_excluded=1`
- [ ] **AC 5**: Given 小说已导入且有 excluded 章节, When 启动分析, Then 分析循环跳过 excluded 章节，WebSocket 进度消息正常推送
- [ ] **AC 6**: Given 已分析完成的小说, When 调用排除 API 排除第 1-3 章, Then 第 1-3 章 `is_excluded=1`，对应 chapter_facts 被删除
- [ ] **AC 7**: Given 已排除的章节, When 调用恢复 API, Then `is_excluded=0`, `analysis_status` 重置为 `pending`
- [ ] **AC 8**: Given 标题含「作者简介」「附录」的尾部章节, When 上传预览, Then 这些章节被自动标记为 is_suspect
- [ ] **AC 9**: Given 正常的小说内容章节, When 上传预览, Then 不被错误标记为 is_suspect（低误报率）
- [ ] **AC 10**: Given 排除操作后, When 查看章节列表 API, Then `is_excluded` 字段正确反映当前状态

## Additional Context

### Dependencies

- 无新外部依赖
- shadcn/ui Checkbox 组件（前端已有 shadcn/ui 依赖）

### Testing Strategy

- **手动验证 1**: 上传包含文学导读的「平凡的世界」TXT，验证前 1-3 章被标记 is_suspect
- **手动验证 2**: 确认导入后，验证 excluded 章节在 chapters 表 is_excluded=1
- **手动验证 3**: 启动分析，验证 excluded 章节被跳过，进度条正常
- **手动验证 4**: 分析完成后调用排除 API，验证 chapter_facts 被清理
- **手动验证 5**: 调用恢复 API，验证 analysis_status 重置为 pending
- **手动验证 6**: 上传正常小说（无导读），验证无误报

### Notes

- **误报容忍度**: 自动检测允许有少量误报（用户可手动取消），但要尽量避免漏报（非正文没被检出）
- **「后记」「尾声」「完本感言」**: 这些在分章正则中被识别为章节标题。它们可能是正文的一部分（如"后记"作为故事结尾章），也可能是非正文（作者感想）。检测算法应基于内容特征（对话密度）而非仅标题来判断
- **序章**: `_split_by_matches` 为首章标记前的长文本自动生成标题「序章」。大多数情况下是简介/导读，但也可能是真正的序章。检测算法对序章应偏保守，标记为 is_suspect 但不强制排除
- **性能**: `classify_chapters()` 是纯 CPU 操作（关键词匹配），对 1000+ 章的小说也能在毫秒内完成
- **事后排除的前端 UI**: 本期仅实现后端 API，前端章节管理页的排除按钮可在后续迭代中添加。用户目前可通过 API 或 curl 操作
