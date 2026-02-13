# Story 8.1: 数据模型与存储层

Status: ready-for-dev

## Story

As a 系统,
I want 定义实体预扫描词典的数据模型并提供数据库存储,
So that 预扫描引擎有持久化的数据基础，后续 Stories 可以读写词典数据。

## Acceptance Criteria

1. SQLite schema 新增 `entity_dictionary` 表，包含 novel_id, name, entity_type, frequency, confidence, aliases(JSON), source, sample_context 字段
2. `novels` 表新增 `prescan_status` 列（默认值 `pending`）
3. 新增 `EntityDictEntry` Pydantic 模型，可正确序列化/反序列化
4. 新增 `entity_dictionary_store.py`，实现 insert_batch / get_all / get_by_type / delete_all / get_prescan_status / update_prescan_status 六个异步方法
5. UNIQUE(novel_id, name) 约束确保同一小说不重复
6. ON DELETE CASCADE 确保小说删除时词典级联清除
7. 新增 jieba 依赖到 pyproject.toml

## Tasks / Subtasks

- [ ] Task 1: 新增 Pydantic 模型 (AC: #3)
  - [ ] 新建 `backend/src/models/entity_dict.py`
  - [ ] 定义 `EntityDictEntry(BaseModel)`: name(str), entity_type(str="unknown"), frequency(int=0), confidence(str="medium"), aliases(list[str]=[]), source(str), sample_context(str|None)
  - [ ] 验证 JSON round-trip

- [ ] Task 2: 新增数据库表 (AC: #1, #2, #5, #6)
  - [ ] 在 `backend/src/db/sqlite_db.py` 的 `_SCHEMA_SQL` 中新增：
    ```sql
    CREATE TABLE IF NOT EXISTS entity_dictionary (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
        name            TEXT NOT NULL,
        entity_type     TEXT,
        frequency       INTEGER DEFAULT 0,
        confidence      TEXT DEFAULT 'medium',
        aliases         TEXT DEFAULT '[]',
        source          TEXT NOT NULL,
        sample_context  TEXT,
        created_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(novel_id, name)
    );
    CREATE INDEX IF NOT EXISTS idx_entity_dict_novel ON entity_dictionary(novel_id, entity_type);
    ```
  - [ ] 新增迁移逻辑：`ALTER TABLE novels ADD COLUMN prescan_status TEXT DEFAULT 'pending'`（兼容已有数据库）

- [ ] Task 3: 新增 Store (AC: #4)
  - [ ] 新建 `backend/src/db/entity_dictionary_store.py`
  - [ ] `async def insert_batch(novel_id: str, entries: list[EntityDictEntry]) -> int` — 批量插入，返回插入数量，使用 INSERT OR REPLACE 处理重复
  - [ ] `async def get_all(novel_id: str) -> list[EntityDictEntry]` — 获取全部词典条目，按 frequency DESC 排序
  - [ ] `async def get_by_type(novel_id: str, entity_type: str, limit: int = 50) -> list[EntityDictEntry]` — 按类型筛选
  - [ ] `async def delete_all(novel_id: str) -> int` — 删除该小说的全部词典数据
  - [ ] `async def get_prescan_status(novel_id: str) -> str` — 查询 novels.prescan_status
  - [ ] `async def update_prescan_status(novel_id: str, status: str) -> None` — 更新 prescan_status

- [ ] Task 4: 新增 jieba 依赖 (AC: #7)
  - [ ] 在 `backend/pyproject.toml` 的 dependencies 中添加 `jieba`
  - [ ] 运行 `uv sync` 确认安装成功

## Dev Notes

- 遵循现有 store 模式：参考 `chapter_fact_store.py` 的异步 aiosqlite 用法
- aliases 字段存储为 JSON 字符串（`json.dumps/loads`），与 chapter_facts.fact_json 模式一致
- prescan_status 迁移需兼容已有数据库——使用 `ALTER TABLE ... ADD COLUMN` 并在 `_ensure_schema()` 中处理 "duplicate column" 异常
- entity_type 的合法值：person / location / item / org / concept / unknown

### Project Structure Notes

- `backend/src/models/entity_dict.py` — 新文件，与 `chapter_fact.py`、`world_structure.py` 同级
- `backend/src/db/entity_dictionary_store.py` — 新文件，与 `chapter_fact_store.py` 同级

### References

- [Source: _bmad-output/entity-prescan-architecture.md#3. 数据模型]
- [Source: _bmad-output/architecture.md#5. 数据模型设计] — 现有 DB schema 规范
- [Source: backend/src/db/chapter_fact_store.py] — Store 实现参考

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
