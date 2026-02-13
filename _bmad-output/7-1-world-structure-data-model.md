# Story 7.1: WorldStructure 数据模型与存储层

Status: ready-for-dev

## Story

As a 系统,
I want 定义世界结构的数据模型并提供数据库存储,
So that 世界结构代理有持久化的数据基础。

## Acceptance Criteria

1. 新增 `WorldStructure` 及其关联 Pydantic 模型，包含完整的世界结构数据类型
2. 新增 `world_structure_store.py`，实现 save/load/delete 三个异步方法
3. SQLite schema 新增 `world_structures` 表和 `layer_layouts` 表
4. 所有模型可正确序列化/反序列化（JSON round-trip）
5. 默认 WorldStructure 只含一个 overworld 层

## Tasks / Subtasks

- [ ] Task 1: 创建 Pydantic 模型 (AC: #1)
  - [ ] 新建 `backend/src/models/world_structure.py`
  - [ ] 定义 `LayerType(str, Enum)`: overworld, celestial, underworld, underwater, instance, pocket
  - [ ] 定义 `WorldRegion(BaseModel)`: name, cardinal_direction(str|None), region_type(str), parent_region(str|None), description(str)
  - [ ] 定义 `MapLayer(BaseModel)`: layer_id(str), name(str), layer_type(LayerType), description(str), regions(list[WorldRegion])
  - [ ] 定义 `Portal(BaseModel)`: name, source_layer, source_location, target_layer, target_location(str|None), is_bidirectional(bool), first_chapter(int)
  - [ ] 定义 `WorldBuildingSignal(BaseModel)`: signal_type(str), chapter(int), raw_text_excerpt(str), extracted_facts(list), confidence(str)
  - [ ] 定义 `WorldStructure(BaseModel)`: novel_id(str), layers(list[MapLayer]), portals(list[Portal]), location_region_map(dict[str,str]), location_layer_map(dict[str,str])
- [ ] Task 2: 新增数据库表 (AC: #3)
  - [ ] 在 `backend/src/db/sqlite_db.py` `_SCHEMA_SQL` 中新增 `world_structures` 表:
    ```sql
    CREATE TABLE IF NOT EXISTS world_structures (
        novel_id TEXT PRIMARY KEY REFERENCES novels(id) ON DELETE CASCADE,
        structure_json TEXT NOT NULL,
        source_chapters TEXT NOT NULL DEFAULT '[]',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    ```
  - [ ] 新增 `layer_layouts` 表:
    ```sql
    CREATE TABLE IF NOT EXISTS layer_layouts (
        novel_id TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
        layer_id TEXT NOT NULL,
        chapter_hash TEXT NOT NULL,
        layout_json TEXT NOT NULL,
        layout_mode TEXT NOT NULL DEFAULT 'hierarchy',
        terrain_path TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (novel_id, layer_id, chapter_hash)
    );
    ```
- [ ] Task 3: 实现存储层 (AC: #2, #4)
  - [ ] 新建 `backend/src/db/world_structure_store.py`
  - [ ] `async def save(novel_id: str, structure: WorldStructure) -> None` — UPSERT 序列化后的 JSON
  - [ ] `async def load(novel_id: str) -> WorldStructure | None` — 读取并反序列化
  - [ ] `async def delete(novel_id: str) -> None` — 删除
  - [ ] 在 `backend/src/db/__init__.py` 中注册（如果存在 __init__）
- [ ] Task 4: 验证 (AC: #4, #5)
  - [ ] 创建默认 WorldStructure（仅 overworld 层），验证 JSON round-trip
  - [ ] 验证包含多层/多区域/多传送门的复杂结构的序列化/反序列化

## Dev Notes

### 项目结构

- 现有模型文件: `backend/src/models/chapter_fact.py`, `backend/src/models/entity_profiles.py`
- 现有 store 文件: `backend/src/db/novel_store.py`, `chapter_store.py`, `chapter_fact_store.py`, `conversation_store.py`, `analysis_task_store.py`
- 数据库初始化: `backend/src/db/sqlite_db.py` 的 `_SCHEMA_SQL` 字符串中直接定义建表语句
- 异步数据库: 使用 `aiosqlite`，通过 `get_connection()` 获取连接

### 编码约定

- 所有 store 函数为 async def
- 使用 `from src.db.sqlite_db import get_connection` 获取连接
- 连接使用 `async with` 或 try/finally 确保关闭
- Pydantic 模型继承 BaseModel，使用 `model_validate()` 和 `model_dump()` 做序列化

### 坐标系约定

- +x = 东（右），+y = 北（上）
- 遵循上北下南左西右东惯例
- cardinal_direction 使用英文: "east" / "west" / "south" / "north" / None

### References

- [Source: _bmad-output/world-map-v2-architecture.md#4-数据模型设计]
- [Source: backend/src/db/sqlite_db.py — 现有 schema 模式]
- [Source: backend/src/models/chapter_fact.py — 现有 Pydantic 模型模式]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
