# Story N1.2: 首次启动自动导入样本数据

Status: review

## Story

As a 新用户,
I want 首次启动应用时样本小说自动出现在书架上,
So that 我无需任何操作即可开始浏览。

## Acceptance Criteria

1. **AC-1**: Given 用户首次启动应用（数据库为空），When 应用完成初始化，Then 书架自动展示《西游记》和《三国演义》两本样本小说
2. **AC-2**: 样本小说标记为"内置样本"，在书架卡片上有明显视觉标识
3. **AC-3**: 样本小说可直接点击进入阅读页，所有功能（实体高亮、关系图、地图、百科、问答）均可使用
4. **AC-4**: 导入过程 < 3 秒，不阻塞 UI（后端启动时同步完成，前端首次加载时已就绪）
5. **AC-5**: 用户可从书架删除样本小说释放空间

## Tasks / Subtasks

- [x] Task 1: 数据库 schema 增加 `is_sample` 字段 (AC: #2)
  - [x] 1.1 `sqlite_db.py` — 在 novels 表增加迁移：`ALTER TABLE novels ADD COLUMN is_sample INTEGER DEFAULT 0`
  - [x] 1.2 `sqlite_db.py` — DDL 更新，确保新建库时 `is_sample` 字段存在
  - [x] 1.3 为 Task 1 编写 pytest 测试（3 个测试：字段存在/可设置/迁移幂等）

- [x] Task 2: 创建样本数据自动导入服务 (AC: #1, #3, #4)
  - [x] 2.1 新建 `backend/src/services/sample_data_service.py`，函数 `auto_import_samples()`: 检测 novels 表是否为空，为空则执行导入
  - [x] 2.2 导入逻辑：读取 `frontend/public/sample-data/*.json`，调用 `export_service.import_novel()` 导入
  - [x] 2.3 内容恢复：读取 `backend/sample-novels/*.txt`，使用 `chapter_splitter.split_chapters_ex()` 切分后，通过 SQL UPDATE 回填 `chapters.content`
  - [x] 2.4 导入后标记：`UPDATE novels SET is_sample = 1 WHERE id = ?`
  - [x] 2.5 为 Task 2 编写 pytest 测试（4 个测试：首次导入/非首次跳过/缺失文件跳过/内容恢复）

- [x] Task 3: 后端启动集成 + API 暴露 is_sample (AC: #1, #2, #4)
  - [x] 3.1 `main.py` — lifespan 中 `init_db()` 之后调用 `auto_import_samples()`
  - [x] 3.2 `novel_store.py` — `list_novels()` SQL 增加 `n.is_sample` 字段；`NovelListItem` schema 增加 `is_sample: bool`
  - [x] 3.3 Task 3 测试由 Task 2 测试覆盖（auto_import_samples 已验证 is_sample 标记）

- [x] Task 4: 前端书架显示样本标识 (AC: #2, #5)
  - [x] 4.1 `frontend/src/api/types.ts` — Novel 类型增加 `is_sample: boolean` 字段
  - [x] 4.2 `BookshelfPage.tsx` — 样本小说卡片左上角显示"内置样本"徽章（半透明白底 pill）
  - [x] 4.3 确认删除样本小说的流程与普通小说一致（已有删除功能，无需额外修改）

## Dev Notes

### 关键架构约束

1. **现有导入服务**: `backend/src/services/export_service.py` — `import_novel(data, overwrite)` 支持 v2 格式
2. **后端启动**: `backend/src/api/main.py` — `lifespan()` 中已有 `init_db()` + `recover_stale_tasks()`
3. **样本数据文件**（由 Story N1.1 生成）:
   - `frontend/public/sample-data/xiyouji.json` (724 KB, v2, 25 章, 无章节原文)
   - `frontend/public/sample-data/sanguoyanyi.json` (760 KB, v2, 30 章, 无章节原文)
4. **样本 TXT 原文**:
   - `backend/sample-novels/xiyouji.txt` (2.1 MB)
   - `backend/sample-novels/sanguoyanyi.txt` (1.7 MB)
5. **不要触碰路由层的导入端点**：只改 service 层 + novels 列表端点

### 内容恢复方案

样本 JSON 使用 `--skip-content` 生成，不含章节原文。但 AC-3 要求"实体高亮"等功能可用，需要章节原文。

**恢复流程：**
1. `import_novel()` 先导入 JSON 数据（无 content）
2. 读取对应 TXT 文件 → `split_chapters_ex()` 切分 → 得到 chapters 列表
3. 按 `chapter_num` 匹配，`UPDATE chapters SET content = ? WHERE novel_id = ? AND chapter_num = ?`

**TXT 文件与 JSON 的对应关系：**
- `xiyouji.json` → `xiyouji.txt`（章节数需匹配: 25 章，TXT 可能包含更多章节，取前 25）
- `sanguoyanyi.json` → `sanguoyanyi.txt`（取前 30 章）

### 首次启动判断

简单策略：`SELECT COUNT(*) FROM novels` == 0 时导入。用户删除所有小说后重启会重新导入样本，这是可接受的行为（相当于重置）。

### 文件路径发现

`sample_data_service.py` 需要找到项目根目录下的样本文件。策略：
- 使用 `__file__` 相对路径：`Path(__file__).parent.parent.parent` 得到 `backend/`
- JSON 路径: `backend/../frontend/public/sample-data/`
- TXT 路径: `backend/sample-novels/`
- 如果文件不存在（如生产环境未打包），静默跳过不报错

### novels 表 schema 参考

```sql
CREATE TABLE IF NOT EXISTS novels (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    author          TEXT,
    file_hash       TEXT,
    total_chapters  INTEGER DEFAULT 0,
    total_words     INTEGER DEFAULT 0,
    prescan_status  TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
    -- 新增: is_sample INTEGER DEFAULT 0
);
```

### GET /api/novels 当前响应结构

```python
# routes/novels.py — list_novels()
novels = [
    {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "total_chapters": row["total_chapters"],
        "total_words": row["total_words"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "analysis_progress": ...,
        "reading_progress": ...,
        "last_opened": ...,
    }
]
# 需要增加: "is_sample": bool(row.get("is_sample", 0))
```

### 测试框架

项目已有 pytest 基础设施（Story N1.1 建立）：
- `backend/tests/conftest.py` — 内存 SQLite fixture
- `backend/tests/test_export_service.py` — 14 个已有测试
- 运行: `cd backend && uv run pytest tests/ -v`

### References

- [Source: backend/src/services/export_service.py] — import_novel() 函数
- [Source: backend/src/api/main.py] — lifespan 启动逻辑
- [Source: backend/src/db/sqlite_db.py] — schema + 迁移
- [Source: backend/src/api/routes/novels.py] — novels 列表 API
- [Source: frontend/src/pages/BookshelfPage.tsx] — 书架页面
- [Source: frontend/src/api/types.ts] — Novel 类型定义
- [Source: backend/src/utils/chapter_splitter.py] — 章节切分
- [Source: _bmad-output/n1-1-sample-novel-data-package.md] — N1.1 完成记录

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- conftest.py `mock_get_connection` fixture extended to also patch `sample_data_service.get_connection` (in addition to `export_service.get_connection`)
- pyproject.toml 增加 `[tool.pytest.ini_options]` 的 `pythonpath = ["."]` 配置，修复 `import src` 路径问题
- 前端 build 有预存 TS 错误（ChatPage, EncyclopediaPage, FactionsPage, MapPage, TimelinePage, analysisStore），与本 Story 无关

### Completion Notes List

- **Task 1 完成**: `sqlite_db.py` DDL 和迁移均增加 `is_sample INTEGER DEFAULT 0`；`conftest.py` 测试 schema 同步更新；3 个 pytest 测试通过
- **Task 2 完成**: `sample_data_service.py` 实现 `auto_import_samples()` + `_restore_chapter_content()`；4 个 pytest 测试通过（首次导入 + 非首次跳过 + 缺失文件 + 内容恢复）
- **Task 3 完成**: `main.py` lifespan 中 `init_db()` 后调用 `auto_import_samples()`；`novel_store.py` SQL 增加 `n.is_sample`；`NovelListItem` Pydantic schema 增加 `is_sample: bool = False`
- **Task 4 完成**: `types.ts` Novel 接口增加 `is_sample: boolean`；`BookshelfPage.tsx` 卡片封面左上角显示"内置样本"半透明 pill 徽章
- **全部 21 个 pytest 测试通过**（14 原有 + 3 schema 迁移 + 4 样本导入服务）

### File List

- `backend/src/db/sqlite_db.py` — DDL 增加 `is_sample` 列 + ALTER TABLE 迁移
- `backend/src/services/sample_data_service.py` — 新增：首次启动自动导入服务
- `backend/src/api/main.py` — lifespan 增加 `auto_import_samples()` 调用
- `backend/src/db/novel_store.py` — `list_novels()` SQL 增加 `n.is_sample`
- `backend/src/api/schemas/novels.py` — `NovelListItem` 增加 `is_sample: bool`
- `backend/pyproject.toml` — 增加 `[tool.pytest.ini_options]` pythonpath 配置
- `backend/tests/conftest.py` — `_TEST_SCHEMA` 增加 `is_sample`；`mock_get_connection` 增加 `sample_data_service` patch
- `backend/tests/test_schema_migration.py` — 新增：3 个 schema 迁移测试
- `backend/tests/test_sample_data_service.py` — 新增：4 个样本导入服务测试
- `frontend/src/api/types.ts` — Novel 接口增加 `is_sample: boolean`
- `frontend/src/pages/BookshelfPage.tsx` — 书架卡片增加"内置样本"徽章
