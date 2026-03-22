---
title: '跨章节空间补全 + 空间尺度自适应'
slug: 'spatial-completion-scale-adaptive'
created: '2026-03-22'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.9+/FastAPI async', 'LLM (Ollama/OpenAI/Anthropic)', 'SQLite/aiosqlite', 'React 19/TypeScript 5.9/Vite 7', 'D3 zoom', 'scipy', 'opensimplex']
files_to_modify:
  - 'backend/src/services/spatial_completion_agent.py (NEW)'
  - 'backend/src/services/world_structure_agent.py (_detect_spatial_scale, _detect_layer)'
  - 'backend/src/services/map_layout_service.py (SPATIAL_SCALE_CANVAS)'
  - 'backend/src/services/visualization_service.py (get_map_data canvas sizing)'
  - 'backend/src/models/world_structure.py (WorldStructure model)'
  - 'backend/src/api/routes/world_structure.py (new endpoint)'
  - 'frontend/src/components/visualization/NovelMap.tsx (LOD thresholds)'
  - 'backend/tests/test_spatial_completion.py (NEW)'
code_patterns:
  - 'SSE streaming (rebuild-hierarchy pattern)'
  - 'WorldStructureAgent vote injection (inject_external_votes)'
  - 'MacroSkeletonGenerator LLM prompt+schema pattern'
  - 'LOD tier visibility (TIER_MIN_SCALE + tierScaleDivisor)'
  - 'ConstraintSolver canvas_bounds adaptation'
test_patterns: ['pytest-asyncio with memory_db fixture', 'vitest']
---

# Tech-Spec: 跨章节空间补全 + 空间尺度自适应

**Created:** 2026-03-22

## Overview

### Problem Statement

当前系统逐章提取空间关系（方位、距离、包含、相邻），但单章提取存在三类系统性缺陷：

1. **空间关系碎片化**：A 的 parent 在第 3 章提到，但 A 在第 3-50 章出现。方位关系（A 在 B 北方）只在一章出现，从未跨章交叉验证。导致地图布局缺乏足够的约束条件，地点位置不够准确。

2. **层级归属错误**：天庭 parent=东胜神洲（应属天界层），水晶宫 parent=东洋大海（应属海底/龙宫层）。Layer 检测依赖关键词匹配，缺乏语义理解。

3. **空间尺度单一**：所有小说使用同一个固定画布（8000×4500），无论是发生在弹丸之地的校园小说还是横跨宇宙的星际小说。缺乏根据小说实际空间规模动态调整画布和缩放策略的能力。

### Solution

两阶段架构：**无 LLM 约束增强（即时）** + **LLM 空间补全（后台）** + **空间尺度自适应**：

1. **阶段 A — 约束增强（无 LLM，< 1 秒，get_map_data 内实时计算）**：
   - 轨迹隐含约束：角色 A→B 移动 → 自动注入 adjacent(A, B) 约束
   - 传递性推导：A 北 B, B 北 C → 推导 A 北 C
   - 兄弟互斥：同 parent 子节点分散布局
   - **用户立刻感知到布局改善，零等待**
2. **阶段 B — SpatialCompletionAgent（需 LLM，6-15 分钟，后台任务）**：
   - 混合三阶段 gap 检测 + LLM 方位/距离补全
   - 语义分层审校（天庭→天界，水晶宫→海底）
   - 分析完自动触发，解耦 task lifecycle
3. **空间尺度自适应**：9 级离散尺度 + per-layer 独立尺度 + 画布自动调整

### Scope

**In Scope (V1):**
- 阶段 A 约束增强：轨迹隐含 adjacent + 传递性方位推导（无 LLM，实时）
- 阶段 B SpatialCompletionAgent：混合三阶段 LLM 补全（方位/距离）
- 语义分层：LLM 审校层归属（关键词快速路径 + 语义兜底）
- LayerType 新增 underwater
- 空间尺度 9 级 + per-layer 独立尺度（`layer_spatial_scales`）
- 画布尺寸自适应：根据 spatial_scale 动态计算
- `_detect_spatial_scale()` 增强：最高 tier + 地点数 + genre 联合判断
- 独立 API 端点：`POST /spatial-completion`，SSE 流式进度
- 分析完自动触发（解耦后台任务）
- 补全结果持久化（WorldStructure.completed_spatial_relations, top 500）

**Out of Scope (V2+):**
- 重新分析章节（只做轻量二次 pass）
- WebGL 渲染（Sprint 3）
- 语义缩放 drill-down（flyTo 已覆盖核心需求）
- LayerType celestial/virtual（无当前用户需求）
- 特殊渲染模式（星图/平面图/海底反转）
- chapter-range 动态尺度
- 多 overworld 支持

## Context for Development

### 核心设计决策

**1. 补全结果存储：扩展 WorldStructure**

不创建虚拟章节，而是在 `WorldStructure` 模型中新增 `completed_spatial_relations` 字段：
```python
class WorldStructure(BaseModel):
    ...
    completed_spatial_relations: list[dict] = []  # 补全的跨章节空间关系
    # Each dict: {source, target, relation_type, value, confidence, evidence_chapters}
```

理由：补全关系属于"世界结构级"知识，不属于任何单一章节。和 `location_parents` 存在同一层。

**2. 层类型：扩展枚举 + 动态 layer_id**

`LayerType` 枚举扩展为 7 种渲染提示（影响背景色调/图标风格）：
```python
class LayerType(str, Enum):
    overworld = "overworld"      # 主大陆/主世界（默认，暖色调）
    sky = "sky"                  # 天空/天界/高层（浅色调）
    underground = "underground"  # 地下/冥界/深层（暗色调）
    underwater = "underwater"    # 海底/龙宫/水下（蓝色调）
    pocket = "pocket"            # 副本/秘境/独立空间
    celestial = "celestial"      # 星球/天体（星图模式）
    virtual = "virtual"          # 虚拟空间/梦境（赛博色调）
```

`layer_id` 和 `display_name` 完全动态——由 LLM 从内容中提取。检测策略：
- **快速路径**：关键词匹配（覆盖 90% 场景，零 LLM 开销）
- **语义兜底**：SpatialCompletion 阶段 LLM 批量审校未分配的地点

适应各类小说：

| 小说类型 | 层示例 | LayerType |
|----------|--------|-----------|
| 修仙/玄幻 | 天界、冥界、海底龙宫、秘境 | sky/underground/underwater/pocket |
| 科幻 | 各星球、空间站、虚拟世界 | celestial/pocket/virtual |
| 都市 | 地上、地铁/下水道、虚拟空间 | overworld/underground/virtual |
| 奇幻 | 主大陆、其他位面、精灵域 | overworld/pocket |

**3. 空间尺度：离散 9 级 + 每 layer 独立尺度**

保持离散级别（可测试、可调试），但从 5 级扩展到 9 级。每个 layer 可独立设置 spatial_scale。

```python
SPATIAL_SCALE_CANVAS = {
    "room": (800, 450),          # 密室、单个建筑
    "building": (1200, 675),     # 大观园、学校
    "district": (1600, 900),     # 城区、街道
    "city": (2400, 1350),        # 城市
    "national": (3200, 1800),    # 国家
    "continental": (4800, 2700), # 大陆
    "planetary": (6400, 3600),   # 星球
    "cosmic": (8000, 4500),      # 多界/宇宙
    "interstellar": (12000, 6750), # 星际
}
```

用户无感——尺度选择完全自动，用户只看到地图在合适的缩放下展示。

**4. 触发时机：分析完自动触发**

分析完最后一章时自动跑一轮 spatial completion（同 world structure 自动构建）。同时保留手动 API（`POST /spatial-completion`）。

**5. 语义缩放 drill-down（新发现）**

不做 tile rendering（我们的数据是向量不是栅格）。在现有 D3 zoom 基础上增加：
- 点击父区域 → zoom into 子区域的 bounding box
- 类似 Google Maps 点击国家进入城市视图
- 代码量 < 50 行（复用 flyTo 逻辑）

**6. 补全关系数量上限：top 500**

避免 `structure_json` 膨胀，补全关系按 confidence 排序保留 top 500。

### Codebase Patterns

- **SSE streaming**：同 rebuild-hierarchy 模式（`StreamingResponse` + `_sse()` helper）
- **LLM 调用**：同 `MacroSkeletonGenerator` / `LocationHierarchyReviewer` 模式（prompt template + JSON schema output）
- **Token budget**：使用 `context_budget.py` 的 `get_budget()` 控制上下文长度
- **Vote 注入**：同 `inject_external_votes()` 模式注入补全结果
- **LOD 已有**：`TIER_MIN_SCALE` 6 级渐变，`tierScaleDivisor` 按画布缩放阈值，fade-in 30% 范围
- **画布已有 5 级**：`SPATIAL_SCALE_CANVAS` cosmic(8000)→local(800)，ConstraintSolver 自适应 min_spacing=2%
- **前端 zoom**：D3 zoom, scaleExtent [0.2, 10], fitToLocations 自动适配，counter-scale 图标

### 关键发现（Step 2 调研）

**已有能力（不需要重建）：**
1. LOD 6 级 tier 渐变 + fade-in — 只需调参
2. 画布 5 级尺寸映射 — 只需扩展到更多级别
3. ConstraintSolver 适应画布尺寸 — min_spacing 2% 自动缩放
4. tierScaleDivisor 按画布比例调整 LOD 阈值 — 已有

**需要新建的：**
1. `SpatialCompletionAgent` — 核心新模块（gap 检测 + LLM 补全 + 语义分层）
2. `POST /spatial-completion` API 端点 — SSE streaming
3. 分析完成自动触发逻辑

**需要修改的：**
1. `WorldStructure` 模型 — 新增 `completed_spatial_relations` + `layer_spatial_scales` 字段
2. `LayerType` 枚举 — 新增 underwater/celestial/virtual
3. `SPATIAL_SCALE_CANVAS` — 从 5 级扩展到 9 级
4. `_detect_spatial_scale()` — 增强：最高 tier + 地点数 + genre 联合判断 + per-layer 支持
5. `_detect_layer()` — 关键词快速路径 + LLM 语义兜底
6. `generate_landmasses()` — underwater 层跳过大陆生成
7. 前端 NovelMap — 语义缩放 drill-down（点击区域 → zoom into 子区域）
8. 前端 layer 切换 — 切换 layer 时自动调整画布尺寸和 LOD 阈值

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `services/world_structure_agent.py` | 层级解析、层检测、vote 系统 |
| `services/macro_skeleton_generator.py` | LLM 骨架生成模式（prompt + schema） |
| `services/location_hierarchy_reviewer.py` | LLM 审校模式（reflect + validate） |
| `services/visualization_service.py` | 空间约束组装、get_map_data() |
| `services/map_layout_service.py` | 画布尺寸、ConstraintSolver |
| `models/world_structure.py` | WorldStructure 模型定义 |
| `models/chapter_fact.py` | SpatialRelationship 模型 |
| `api/routes/world_structure.py` | rebuild-hierarchy SSE 端点模式 |
| `infra/context_budget.py` | TokenBudget 上下文管理 |

## Implementation Plan

### 核心架构：两阶段分离（First Principles 推导）

**用户要的不是"更多空间关系"，而是"地图布局更准确"。**

已有数据中存在未被利用的布局信号（特别是轨迹数据）。先免费挖掘这些信号（阶段 A），再用 LLM 补方位（阶段 B）。

#### 阶段 A: 约束增强（无 LLM，实时，< 1 秒）

在 `visualization_service.get_map_data()` 的约束组装阶段注入：

```python
def _enhance_constraints(spatial_constraints, trajectories, location_parents):
    # 1. 轨迹隐含 travel_sequence 约束
    #    过滤条件：tier 差距 ≤ 1 + 共同 3 级祖先 + 连续章节
    for char, path in trajectories.items():
        for i in range(len(path) - 1):
            locA, locB = path[i].location, path[i+1].location
            if abs(path[i].chapter - path[i+1].chapter) > 1: continue  # 非连续章节跳过
            if tier_gap(locA, locB) > 1: continue  # tier 差距过大跳过
            if not has_common_ancestor(locA, locB, depth=3): continue
            if not has_relation(locA, locB, spatial_constraints):
                spatial_constraints.append({
                    source: locA, target: locB,
                    relation_type: "travel_sequence",  # 弱于 adjacent
                    value: "travel_implied",
                    confidence: "medium", source_type: "trajectory"
                })

    # 2. 传递性方位推导（严格限制）
    #    仅 4 基本方向（north/south/east/west），链长 ≤ 2，置信度递减
    direction_graph = build_direction_graph(spatial_constraints, directions_only=["north_of","south_of","east_of","west_of"])
    for (a, c, direction) in transitive_closure(direction_graph, max_depth=2):
        if not has_relation(a, c, spatial_constraints):
            src_confidence = get_source_confidence(a, c, direction_graph)
            new_conf = "medium" if src_confidence == "high" else "low"
            spatial_constraints.append({...confidence: new_conf})

    return spatial_constraints
```

**效果**：ConstraintSolver 立刻获得更多约束 → 布局更准确 → 用户无需等待。

#### 约束合并优先级

三层合并，后来者**不覆盖**已有：

```
优先级：用户 override > 逐章提取 > LLM 补全(阶段B) > 逻辑推导(阶段A)

existing_keys = {(c.source, c.target, c.relation_type) for c in constraints}
# 注入阶段 A（不覆盖已有）
# 注入阶段 B（不覆盖已有 + 阶段A）
```

ConstraintSolver 按 confidence 加权：high=1.0, medium=0.6, low=0.3。

#### 阶段 B: LLM 空间补全（混合三阶段 gap 检测）

```
Gap 检测:
  阶段 B1: 轨迹图 gap（~500 对）→ 路径上相邻地点缺方位
  阶段 B2: 层级邻居 gap（~8K 对）→ 兄弟/parent-child 缺方位
  阶段 B3: 高频共现 gap（top 100 对）→ 兜底

LLM 补全:
  - 20 对/批, max 25 批
  - 输入：地点对 + 已有关系 + 原文 narrative_evidence
  - 输出：[{source, target, relation_type, value, confidence, reason}]
  - 强约束："只基于原文证据，无法确定回答'无法推断'"

性能: 西游记 ~6min, 凡人修仙传 ~15min（云端）
```

### Tasks

#### T1: 数据模型扩展
- [x] T1.1: WorldStructure 模型扩展
  - File: `backend/src/models/world_structure.py`
  - Action: 新增字段 `completed_spatial_relations: list[dict] = []` 和 `layer_spatial_scales: dict[str, str] = {}`
  - Notes: 无 DB schema 变更（JSON blob 内部扩展）

- [x] T1.2: LayerType 枚举扩展
  - File: `backend/src/models/world_structure.py`
  - Action: 新增 `underwater = "underwater"`（V1 只加这一个）
  - Notes: celestial/virtual 留 V2

- [x] T1.3: SPATIAL_SCALE_CANVAS 扩展到 9 级
  - File: `backend/src/services/map_layout_service.py`
  - Action: 从 5 级扩展到 9 级（room/building/district/city/national/continental/planetary/cosmic/interstellar）
  - Notes: 保持 16:9 比例

#### T2: 阶段 A — 约束增强（无 LLM，实时）
- [x] T2.1: `_enhance_constraints()` 函数
  - File: `backend/src/services/visualization_service.py`
  - Action: 新增函数，在 `get_map_data()` 的约束组装后调用
  - Notes: 包含轨迹 travel_sequence 注入 + 传递性方位推导

- [x] T2.2: 轨迹隐含 travel_sequence 约束
  - File: `backend/src/services/visualization_service.py` (在 `_enhance_constraints` 内)
  - Action: 从 trajectories 提取连续章节的角色移动，注入 travel_sequence 约束
  - Notes: 过滤条件：tier 差距 ≤ 1 + 连续章节 + 共同 3 级祖先

- [x] T2.3: 传递性方位推导
  - File: `backend/src/services/visualization_service.py` (在 `_enhance_constraints` 内)
  - Action: 从已有 direction 关系构建有向图，推导传递闭包
  - Notes: 仅 4 基本方向，链长 ≤ 2，置信度递减（high→medium, medium→low）

- [x] T2.4: 约束合并优先级
  - File: `backend/src/services/visualization_service.py`
  - Action: 在 `get_map_data()` 中实现三层合并（逐章 > 补全 > 推导），existing_keys 防覆盖
  - Notes: 同时注入 WorldStructure.completed_spatial_relations

#### T3: 空间尺度检测增强
- [x] T3.1: `_detect_spatial_scale()` 重写
  - File: `backend/src/services/world_structure_agent.py`
  - Action: 增强检测逻辑——最高 tier + 地点数 + genre + 距离交叉验证
  - Notes: 向后兼容——已有 spatial_scale 不变，新小说才用新逻辑

- [x] T3.2: 非 overworld 层尺度检测
  - File: `backend/src/services/world_structure_agent.py`
  - Action: 新增 `_detect_layer_scale(location_count)` 按地点数映射
  - Notes: ≤5→building, ≤15→district, ≤50→city, ≤150→national, 150+→continental

- [x] T3.3: per-layer canvas 尺寸应用
  - File: `backend/src/services/visualization_service.py`
  - Action: `get_map_data()` 根据当前 layer 的 spatial_scale 选择画布尺寸
  - Notes: 读取 `WorldStructure.layer_spatial_scales[layer_id]`

#### T4: 层检测增强
- [x] T4.1: underwater 关键词检测
  - File: `backend/src/services/world_structure_agent.py`
  - Action: `_detect_layer()` 新增 underwater 关键词（龙宫/水晶宫/海底/水府）
  - Notes: 关键词快速路径，零 LLM 开销

- [x] T4.2: 父级层传播
  - File: `backend/src/services/world_structure_agent.py`
  - Action: parent 已在非 overworld 层 → child 自动继承
  - Notes: 在 `_assign_layers()` 循环后加传播 pass

- [x] T4.3: `generate_landmasses()` underwater 跳过
  - File: `backend/src/services/visualization_service.py`
  - Action: 当 layer_type == underwater 时跳过 `generate_landmasses()` 调用
  - Notes: 同时跳过 shelves 生成

#### T5: 阶段 B — SpatialCompletionAgent（LLM）
- [x] T5.1: 新建 `spatial_completion_agent.py`
  - File: `backend/src/services/spatial_completion_agent.py` (NEW)
  - Action: 创建 SpatialCompletionAgent 类，包含 gap 检测 + LLM 补全 + 语义分层
  - Notes: 遵循 MacroSkeletonGenerator 的 prompt+schema 模式

- [x] T5.2: 混合三阶段 gap 检测
  - File: `backend/src/services/spatial_completion_agent.py`
  - Action: 实现 B1(轨迹gap) + B2(层级邻居gap) + B3(高频共现gap) + 去重排序
  - Notes: Tier 剪枝 + co-occurrence ≥ 2 + batch 上限 10

- [x] T5.3: LLM 补全 prompt 设计
  - File: `backend/src/extraction/prompts/spatial_completion.txt` (NEW)
  - Action: 设计 prompt template（20 对/批 + 原文证据 + JSON schema output）
  - Notes: 强约束"只基于原文证据" + confidence 必填

- [x] T5.4: 矛盾检测 + confidence 过滤
  - File: `backend/src/services/spatial_completion_agent.py`
  - Action: 新关系与已有关系冲突检测 + confidence < medium 丢弃 + top 500 cap
  - Notes: 方向对立检测（north vs south）

- [x] T5.5: 语义分层审校
  - File: `backend/src/services/spatial_completion_agent.py`
  - Action: 对未分配层的地点做 LLM 批量审校（overworld 默认原则）
  - Notes: 仅确认/否决，不做分类；父级传播后的残余

#### T6: API 端点 + 自动触发
- [x] T6.1: `POST /spatial-completion` SSE 端点
  - File: `backend/src/api/routes/world_structure.py`
  - Action: 新增 SSE streaming 端点，调用 SpatialCompletionAgent
  - Notes: 复用 rebuild-hierarchy 的 `_sse()` helper 模式

- [x] T6.2: 分析完成自动触发
  - File: `backend/src/services/analysis_service.py`
  - Action: 最后一章完成后立即标记 task completed，然后 `asyncio.create_task()` 启动补全
  - Notes: timeout 300s + 失败非致命 + 独立 task lifecycle

#### T7: 前端适配
- [x] T7.1: layer 切换画布自适应
  - File: `frontend/src/pages/MapPage.tsx`
  - Action: 切换 layer 时读取 `layer_spatial_scales` 对应画布尺寸，传给 NovelMap
  - Notes: 如果无对应尺度，fallback 到主层尺度

- [x] T7.2: ConstraintSolver confidence 加权
  - File: `backend/src/services/map_layout_service.py`
  - Action: ConstraintSolver 能量函数按 confidence 加权（high=1.0, medium=0.6, low=0.3）
  - Notes: 现有 confidence_score 字段已有，确保 travel_sequence 和补全关系使用

#### T8: 测试
- [x] T8.1: `test_spatial_completion.py`
  - File: `backend/tests/test_spatial_completion.py` (NEW)
  - Action: gap 检测逻辑（3 阶段）、约束合并优先级、传递性推导、尺度检测增强、矛盾检测
  - Notes: 使用 memory_db fixture，mock LLM 调用

- [x] T8.2: `test_spatial_scale.py`
  - File: `backend/tests/test_spatial_scale.py` (NEW)
  - Action: 9 级尺度检测、per-layer 尺度、地点数映射、向后兼容
  - Notes: 纯函数测试，不需要 DB

### Acceptance Criteria

- [x] AC1: Given 一本已分析完的小说有轨迹数据, when 调用 get_map_data(), then 返回的 spatial_constraints 中包含 travel_sequence 类型约束（阶段A）
- [x] AC2: Given 已有 A north_of B 和 B north_of C (confidence=high), when 阶段A运行, then 自动推导 A north_of C (confidence=medium)
- [x] AC3: Given A north_of B 已存在, when 阶段B补全出 B north_of A, then 矛盾关系被丢弃
- [x] AC4: Given 西游记 824 个地点, when 调用 POST /spatial-completion, then SSE 返回进度事件 + 完成后 completed_spatial_relations ≤ 500 条
- [x] AC5: Given 地点"水晶宫"的 parent 链含水域关键词, when 层检测运行, then 分配到 underwater 层
- [x] AC6: Given 天庭的 parent 在 celestial 层, when 父级传播运行, then 南天门/灵霄宝殿自动继承 celestial 层
- [x] AC7: Given 一本只有 15 个 building-tier 地点的小说, when 尺度检测运行, then spatial_scale = "building" (非默认 continental)
- [x] AC8: Given 凡人修仙传有 overworld + celestial + underground 层, when 查看不同层地图, then 每层使用独立画布尺寸
- [x] AC9: Given 已有 spatial_scale 且用户有 map_user_overrides, when 新尺度检测逻辑运行, then 保持原 spatial_scale 不变（向后兼容）
- [x] AC10: Given 分析完最后一章, when analysis task 完成, then task 立即标记 completed + 补全任务异步启动 + 补全失败不影响 task 状态

## Additional Context

### Dependencies

- 现有 LLM 客户端（Ollama/OpenAI/Anthropic）
- scipy（已有）、opensimplex（已有）
- 无新外部依赖

### Testing Strategy

- `test_spatial_completion.py`：gap 检测逻辑、关系合并、尺度检测
- 西游记 + 凡人修仙传实际数据验证
- 前端：vitest 画布尺寸计算测试

### 风险预防措施（Pre-mortem Analysis）

**P0 — 必须在 v1 实现：**

1. **Gap 检测防爆炸**：
   - Tier 剪枝：只检测同 tier 或相邻 tier 的地点对（building↔continent 不检测）
   - Co-occurrence 阈值：至少共现 2 章以上才检测
   - Batch 上限：最多 10 批 LLM 调用，按 `mention_count × co-occurrence` 优先级排序
   - Token budget cap：`get_budget().completion_max_tokens` 控制总预算

2. **LLM 幻觉防护**：
   - 必须注入原文证据（narrative_evidence + descriptions）到 prompt
   - Prompt 强约束："只基于以下原文证据推断，无法确定请回答'无法推断'"
   - Confidence 过滤：补全结果 confidence ≥ medium 才入库
   - 矛盾检测：新关系与已有关系冲突时丢弃（A 在 B 北方 vs B 在 A 北方）

**P1 — 应在 v1 实现：**

3. **尺度检测校正**：
   - 距离交叉验证：所有距离关系都是 near/步行 → 强制降级
   - 地点数校正：< 20 个地点 → 强制 ≤ city
   - Genre 优先：校园/都市/推理 → 强制 ≤ city
   - 最高 tier 检测：最高 tier 为 building 且无 region+ → 强制 building 尺度
   - 人工 override：用户可手动选择空间尺度
   - **非 overworld 层**：按地点数直接映射（≤5→building, ≤15→district, ≤50→city, ≤150→national, 150+→continental）
   - **向后兼容**：已有 spatial_scale + 用户 override → 保持不变，新小说才用新逻辑

4. **语义分层 safeguard**：
   - Overworld 默认原则：只有明确叙事证据才分配到非 overworld 层
   - 父级传播：parent 已在某层 → child 自动继承
   - 分层结果预览：返回 diff 让用户确认

5. **自动触发解耦**：
   - Analysis task 在最后一章完成时立即标记 completed
   - Completion 是独立后台任务，timeout 300s，失败非致命
   - 手动重试入口：`POST /spatial-completion`

### 极端场景分析（What-If Scenarios）

**V1 必做（源自场景分析）：**
1. `_detect_spatial_scale()` 增强 — 最高 tier + 地点数(<20→≤city) + genre 联合判断（场景 A/C）
2. `layer_spatial_scales: dict[str, str]` — 每 layer 独立尺度（场景 C: 凡人修仙传凡人界=national, 灵界=continental, 仙界=cosmic）
3. underwater 层正确检测 — 水域关键词占比 >50% 时主层为 underwater（场景 D）
4. `generate_landmasses()` 在 underwater 层跳过（场景 D）
5. 前端 layer 切换时自动调整画布和 LOD（场景 C）

**V2 延后（明确不在本轮）：**
- 星图渲染模式（interstellar，场景 B）→ V1 fallback 到 cosmic 画布
- 平面图渲染模式（room/building，场景 A）→ V1 用正常布局
- 海底地形反转渲染（场景 D）→ V1 只跳过大陆生成
- chapter-range 动态尺度（场景 C）→ V1 用全量检测
- 多 overworld 支持（场景 E）→ V1 虚拟世界当 pocket 层
- 航线/洋流网络替代陆地道路（场景 D）→ V1 用 MST 道路

### Notes

- 用户反馈：小说空间规模差异巨大（弹丸之地 vs 宇宙），地图应像 Google Maps 一样多级缩放
- 星际题材：宇宙星图 + 星球副本模式
- SPATIAL_SCALE_CANVAS 映射表在 map_layout_service.py 中
- Party Mode 共识：不做 tile rendering，做语义缩放 drill-down
- Party Mode 共识：保持离散尺度（9 级），每 layer 独立尺度
- Party Mode 共识：补全关系 top 500 cap 防 JSON 膨胀
- Party Mode 共识：分析完自动触发补全（解耦 task lifecycle）
