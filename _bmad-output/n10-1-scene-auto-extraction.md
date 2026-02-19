# Story N10.1: 场景自动提取

Status: review

## Story

As a 编剧,
I want 系统自动将章节内容拆分为场景,
So that 我可以以场景为单位进行改编。

## Acceptance Criteria

1. **AC-1**: 每章自动拆分为 1-N 个场景
2. **AC-2**: 场景边界基于地点变化、时间跳跃、人物出场变化识别
3. **AC-3**: 每个场景包含：标题、地点、角色列表、描述、对话
4. **AC-4**: 场景数据缓存（避免重复提取）

## Tasks / Subtasks

- [x] Task 1: 场景提取服务
  - [x] 1.1 `backend/src/services/scene_extractor.py`
- [x] Task 2: API 端点
  - [x] 2.1 `backend/src/api/routes/scenes.py`
  - [x] 2.2 注册路由到 main.py
- [x] Task 3: 编译验证

## Completion Notes

- 纯规则提取，无 LLM 调用
- 事件驱动分割：地点变化 + 参与者变动 >50% 触发场景边界
- 文本结构兜底：识别 ※★☆◇◆■ 等分隔符模式
- 内存缓存 `_scene_cache[novel_id][chapter_num]`
- 每个场景包含: index, chapter, title, location, characters, description, dialogue_count, paragraph_range, events

### Files Changed

- `backend/src/services/scene_extractor.py` — 场景提取服务（新增）
- `backend/src/api/routes/scenes.py` — 场景 API 端点（新增）
- `backend/src/api/main.py` — 注册 scenes 路由

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
