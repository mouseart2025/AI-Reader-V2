# Story 7.11: 用户编辑世界结构

Status: ready-for-dev

## Story

As a 用户,
I want 手动调整世界结构（区域归属、传送门增删、区域方位）,
So that 我可以修正 LLM 生成的错误。

## Acceptance Criteria

1. 用户可以将地点拖拽到另一个区域，更新 location_region_map
2. 用户可以在传送门编辑面板中添加新传送门（指定名称、源层+地点、目标层）
3. 用户可以删除已有传送门
4. 用户编辑存为 override，优先于 LLM 生成的结构
5. LLM 后续更新不覆盖用户 override

## Tasks / Subtasks

- [ ] Task 1: 后端 Override 存储 (AC: #4, #5)
  - [ ] 新增 `world_structure_overrides` 数据库表:
    ```sql
    CREATE TABLE IF NOT EXISTS world_structure_overrides (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        novel_id     TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
        override_type TEXT NOT NULL,   -- "location_region" / "add_portal" / "delete_portal" / "region_direction"
        override_key  TEXT NOT NULL,   -- 被覆盖的实体标识
        override_json TEXT NOT NULL,   -- 覆盖内容 JSON
        created_at   TEXT DEFAULT (datetime('now')),
        UNIQUE(novel_id, override_type, override_key)
    );
    ```
  - [ ] 在 `sqlite_db.py` 的 `_SCHEMA_SQL` 中添加表定义
  - [ ] 新增 `backend/src/db/world_structure_override_store.py`: save/load/delete CRUD
- [ ] Task 2: Override API 端点 (AC: #1, #2, #3)
  - [ ] 新增 `PUT /api/novels/{novel_id}/world-structure/overrides`:
    - 接收 override 操作列表
    - 写入 overrides 表
    - 返回更新后的完整 WorldStructure（合并 LLM 生成 + overrides）
  - [ ] 新增 `DELETE /api/novels/{novel_id}/world-structure/overrides/{override_id}`:
    - 删除指定 override
    - 返回更新后的 WorldStructure
  - [ ] 新增 `GET /api/novels/{novel_id}/world-structure/overrides`:
    - 返回当前所有 override 列表（便于前端标记哪些是用户修改的）
- [ ] Task 3: WorldStructure 合并逻辑 (AC: #4, #5)
  - [ ] 在 `world_structure_store.py` 或新建 `world_structure_merger.py` 中实现:
    - `load_with_overrides(novel_id)` → 加载 LLM 生成的基础结构 + 用户 overrides
    - 合并规则: override 优先级 > LLM 生成
    - location_region override: 替换 location_region_map 中的条目
    - add_portal override: 在 portals 列表中追加
    - delete_portal override: 从 portals 列表中移除（按 name 匹配）
  - [ ] 修改 `world_structure_agent.py`: `_apply_operations()` 时检查 overrides，不覆盖用户修改
- [ ] Task 4: 前端编辑面板 (AC: #1, #2, #3)
  - [ ] 新建 `frontend/src/components/visualization/WorldStructureEditor.tsx`:
    - 区域归属编辑: 地点列表 + 区域下拉选择器（或拖拽分组）
    - 传送门编辑: 传送门列表 + 添加/删除按钮
    - 添加传送门表单: 名称、源层、源地点、目标层 下拉选择
    - 保存按钮 → 调用 override API
  - [ ] 在 MapPage 中添加"编辑世界结构"入口按钮
  - [ ] 编辑面板以抽屉形式从右侧滑出
- [ ] Task 5: 用户修改可视标记 (AC: #4)
  - [ ] 在 NovelMap 中，被用户 override 的地点/传送门用特殊图标或边框标记
  - [ ] tooltip 显示"用户手动修改"提示
  - [ ] 提供"重置为 AI 生成"按钮（删除对应 override）

## Dev Notes

### Override 设计原则

Override 是"补丁"机制，不直接修改 world_structures 表中的 LLM 生成数据。这样：
1. 用户可以随时撤销修改（删除 override 即回退到 LLM 版本）
2. LLM 重新分析时可以更新基础结构，但 override 始终保持
3. 可以清楚区分哪些是 AI 生成的、哪些是用户修改的

### 现有类似模式

参考已有的 `map_layouts` 表中 `user_overrides` 字段的用户坐标覆盖机制（`map_layout_service.py` 中的 `UserOverride`），但世界结构 override 更复杂，需要独立的表。

### References

- [Source: _bmad-output/world-map-v2-architecture.md#10-决策4-WorldStructure是否可以手动编辑]
- [Source: backend/src/services/map_layout_service.py — UserOverride 模式参考]
- [Source: backend/src/db/world_structure_store.py — 基础存储层（Story 7.1 创建）]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
