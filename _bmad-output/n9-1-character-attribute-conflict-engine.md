# Story N9.1: 角色属性冲突检测引擎

Status: review

## Story

As a 网文作者,
I want 系统自动检测角色属性矛盾,
So that 我能避免"吃书"。

## Acceptance Criteria

1. **AC-1**: 检测角色能力矛盾（能力回退 A→B→A 模式）
2. **AC-2**: 检测角色关系矛盾（敌对↔亲属不合理转变、关系反复）
3. **AC-3**: 每个冲突标注严重程度：严重/一般/提示
4. **AC-4**: 每个冲突附带涉及章节号和描述

## Tasks / Subtasks

- [x] Task 1: 冲突检测服务
  - [x] 1.1 `backend/src/services/conflict_detector.py` — 4 类检测器
  - [x] 1.2 能力回退检测: 同维度 A→B→A 模式
  - [x] 1.3 关系矛盾检测: 敌对↔亲属转变 + 关系反复
  - [x] 1.4 地点层级检测: 同地点不同上级
  - [x] 1.5 死亡连续性: 阵亡角色后续出场
- [x] Task 2: API 端点
  - [x] 2.1 `backend/src/api/routes/conflicts.py` — GET /api/novels/{id}/conflicts
  - [x] 2.2 注册路由到 main.py
- [x] Task 3: 编译验证 — 57/57 测试通过

## Completion Notes

- `conflict_detector.py`: 4 类纯规则检测（无需 LLM）
  - ability: 同维度能力回退（A→B→A），严重度"一般"
  - relation: 敌对↔亲属转变 + 关系反复（flip-flop），严重度"一般"/"提示"
  - location: 同地点不同上级，严重度"一般"
  - death: 阵亡角色后续出场，严重度"严重"
- 使用 build_alias_map() 进行别名解析
- API 返回冲突列表 + severity_counts + type_counts

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/conflict_detector.py` | 新增 — 冲突检测引擎 |
| `backend/src/api/routes/conflicts.py` | 新增 — API 端点 |
| `backend/src/api/main.py` | 注册 conflicts router |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
