# v0.67 Epics & Stories — 宏观地理骨架重构

**PRD:** prd-v067-hierarchy-skeleton.md
**Deadline:** 2026-04-23
**总 Story Points:** ~42 SP

---

## Epic 1: 宏观骨架深化 (P0, 10 SP)

**目标:** MacroSkeletonGenerator 从 2-3 级 → 4-5 级

### Story 1.1: 骨架 prompt 重写 (4 SP)

**任务:**
1. 修改 `macro_skeleton.txt`：
   - "2-3层核心骨架" → "4-5层完整地理骨架"
   - 新增 genre-aware 层级模板（幻想/现实/家宅三种）
   - 明确要求每层不超过 20 个节点
2. 修改 `macro_skeleton_generator.py`：
   - 输出 schema 支持 depth=5
   - 新增中间层 tier: province, prefecture, county, district
3. max_tokens 从 8192 调整（如果需要更多层级输出）

**验收:**
- 西游记骨架包含：天下→四大部洲→国→城/山 四层
- 红楼梦骨架包含：都中→宁荣街→荣国府/宁国府→大观园→各院 五层
- 水浒传骨架包含：天下→山东/河北/京畿→州→县/城 四层

**关键文件:**
- `backend/src/services/macro_skeleton_generator.py`
- `backend/src/extraction/prompts/macro_skeleton.txt`

---

### Story 1.2: 骨架深度验证 + 自修复 (3 SP)

**任务:**
1. 生成后检查最大深度，<4 则要求 LLM 补充
2. 检查每层节点数，>20 则要求 LLM 拆分
3. 最多重试 2 次

**关键文件:** `macro_skeleton_generator.py`

---

### Story 1.3: 骨架投票权重增强 (3 SP)

**任务:**
1. 骨架深层关系（depth 3-4）权重从 5-10 提升到 15-20
2. 骨架关系在 `_resolve_parents` 中标记为"authoritative"，不被基线投票覆盖
3. 新增 `skeleton_parents` 优先级：当骨架和投票冲突时，骨架 parent 只在投票证据 ≥3x 时才被覆盖

**关键文件:** `world_structure_agent.py` — `_resolve_parents()`

---

## Epic 2: 提取上下文增强 (P0, 8 SP)

**目标:** LLM 提取时能看到完整地理框架

### Story 2.1: 深层地理框架注入 (4 SP)

**任务:**
1. 重写 `_build_macro_hub_section()`：
   - 从 depth=1 扁平列表 → depth=3 缩进层级树
   - 格式：
     ```
     ### 地理框架（请参考此框架填写 parent）
     - 东胜神洲 [大洲]
       - 傲来国 [国家]
         - 花果山、水帘洞、傲来城...
       - 宝象国 [国家]
     ```
   - 每层最多 10 节点，超出折叠
2. 数据来源：使用 MacroSkeleton + 已累积的 location_parents

**验收:**
- 第 50 章的 context 中能看到完整的四大部洲层级
- 新提取的 parent 直接命中中间层（花果山→傲来国而非→东胜神洲）

**关键文件:** `context_summary_builder.py` — `_build_macro_hub_section()`

---

### Story 2.2: 提取 prompt parent 指导强化 (2 SP)

**任务:**
1. `extraction_system.txt` 新增规则：
   - "parent 填写**直接上级**，不要跳过中间层"
   - "参考上方'地理框架'中的层级结构确定 parent"
2. 新增 few-shot examples：
   - ✅ 水帘洞 → parent=花果山（直接上级）
   - ❌ 水帘洞 → parent=东胜神洲（跳过两层）
3. 新增 negative rule：
   - "如果你知道 A 在 B 中，B 在 C 中，那么 A 的 parent 是 B（不是 C）"

**关键文件:** `extraction_system.txt`, `extraction_examples.json`

---

### Story 2.3: Context budget 适配 (2 SP)

**任务:**
1. 评估深层树占用的额外 token（估计 200-500 token）
2. 在 `context_budget.py` 中为地理框架预留空间
3. 如果超限，自动降级为 depth=2 树

**关键文件:** `context_budget.py`

---

## Epic 3: 投票解析改进 (P0, 8 SP)

**目标:** 保护中间层，消除扁平化

### Story 3.1: 中间层投票保护 (4 SP)

**任务:**
1. 在 `_resolve_parents()` 中，当选出 winner 后做跳层检查：
   - 计算 child_rank 和 winner_rank 的差距
   - 如果差距 ≥ 2（跳了 2+ 个 tier），检查是否有中间层候选：
     - 候选条件：该中间层存在于骨架或投票中，且 tier 在 child 和 winner 之间
     - 如果找到中间层，改选它为 parent
2. 骨架中的 parent-child 关系作为"权威参考"：
   - 如果骨架说 A→B，即使投票 A→C 票数更多，只要 B→C 链路存在，选 B

**验收:**
- 花果山→傲来国（而非→东胜神洲）
- 水帘洞→花果山（而非→傲来国）

**关键文件:** `world_structure_agent.py` — `_resolve_parents()`

---

### Story 3.2: 超大父节点拆分 (2 SP)

**任务:**
1. `consolidate_hierarchy` 新增 `_split_oversized_parents()` 步骤
2. 逻辑：
   - 遍历所有 parent，如果子节点 > 30
   - 按子节点的 tier 分组
   - 如果有明显的 tier 分层（如 kingdom + city），创建中间层
   - 优先使用已存在的中间节点
3. 不创建全新的虚拟节点——只重新分配到已有的更合适的 parent

**关键文件:** `hierarchy_consolidator.py`

---

### Story 3.3: 噪声根节点清理 (2 SP)

**任务:**
1. 识别噪声根节点：tier 不是 world/continent 的 root
2. 处理策略：
   - 如果与主世界有共现证据 → 归入最近的 parent
   - 如果无证据（诗词意象如"池上"、"雪岸"）→ 从层级树中移除
3. 保留合法的非主世界根节点（天界、冥界等 layer 根节点）

**关键文件:** `hierarchy_consolidator.py`

---

## Epic 4: Tier 重分类 (P1, 4 SP)

### Story 4.1: Suffix rank 中间层扩充 (2 SP)

**任务:**
1. 新增中间行政层级 rank：
   - 省/道: rank=1.5
   - 州/府: rank=2.5
   - 县/镇: rank=3.5
2. 修复特定 tier 错误：
   - "东京" suffix → city (不是 world)
   - "大观园" → site/region (不是 building)
3. 前缀检测："X京/X都" → capital city tier

**关键文件:** `world_structure_agent.py` — `_get_suffix_rank()`, `_classify_tier()`

---

### Story 4.2: Tier 一致性自动修复 (2 SP)

**任务:**
1. 在 consolidate 阶段：如果 child.tier < parent.tier（逆转），修正 child.tier
2. 修正策略：基于 suffix_rank 重新计算 tier
3. 验收：三本小说 tier 逆转 = 0

---

## Epic 5: 微地点治理 (P1, 6 SP)

### Story 5.1: 低频微地点裁剪 (3 SP)

**任务:**
1. 在 consolidate 阶段新增 `_prune_micro_locations()`:
   - 条件：freq ≤ 1 章 AND 无子节点 AND tier in (building, site)
   - 名字包含通用描述词（路口/门外/树下/角落/旁边/附近）
   - 从 location_parents 中移除（不删 chapter_facts 原始数据）
2. 阈值可配置：`_MICRO_PRUNE_THRESHOLD = 1`

**关键文件:** `hierarchy_consolidator.py`

---

### Story 5.2: 战场微地点检测与折叠 (3 SP)

**任务:**
1. 检测模式：某父节点 >40 个子节点，且 >70% freq=1
2. 这些通常是战场/旅途章节的一次性微地点（"北门外"、"松树林"等）
3. 折叠策略：保留 freq ≥ 2 的子节点，其余从层级中移除
4. 记录日志：被折叠的数量和父节点名称

---

## Epic 6: 全量健康度监控 (P1, 6 SP)

### Story 6.1: 健康度指标函数 (3 SP)

**任务:**
1. 新增 `compute_hierarchy_health(location_parents, location_tiers)`:
   ```python
   {
     "avg_depth": float,          # 平均层级深度
     "max_depth": int,            # 最大深度
     "max_children": int,         # 最大子节点数
     "max_children_location": str, # 最大子节点的地点名
     "tier_inversions": int,      # tier 逆转数
     "noise_roots": int,          # 噪声根节点数
     "orphan_rate": float,        # 孤儿率
     "total_locations": int,
     "total_parents": int,
   }
   ```
2. 在 rebuild-hierarchy SSE 中输出健康度报告
3. 在 `hierarchy_iteration.py` 中集成

**关键文件:** `topology_metrics.py`, `world_structure.py` route

---

### Story 6.2: 三本小说回归验证 (3 SP)

**任务:**
1. 扩展 `hierarchy_iteration.py`：
   - 重分析后自动 rebuild
   - 跑 golden standard precision + 健康度指标
   - 对比 v0.66 基线
2. 输出对比报告（JSON + 可读 markdown）
3. 如果任何 golden standard precision 退化 > 5pp，报警

---

## Sprint Plan

### Sprint 1 (D1-D5): P0 骨架+提取+投票

| Story | SP | 依赖 |
|-------|-----|------|
| 1.1 骨架 prompt 重写 | 4 | 无 |
| 1.2 骨架深度验证 | 3 | 1.1 |
| 2.1 深层地理框架注入 | 4 | 1.1 (需要骨架数据) |
| 2.2 提取 prompt 强化 | 2 | 2.1 |
| 3.1 中间层投票保护 | 4 | 无（可并行） |

### Sprint 2 (D6-D8): P1 tier+微地点+监控

| Story | SP | 依赖 |
|-------|-----|------|
| 1.3 骨架投票权重增强 | 3 | Sprint 1 |
| 2.3 Context budget 适配 | 2 | 2.1 |
| 3.2 超大父节点拆分 | 2 | 3.1 |
| 3.3 噪声根节点清理 | 2 | 无 |
| 4.1 Suffix rank 扩充 | 2 | 无 |
| 4.2 Tier 一致性修复 | 2 | 4.1 |
| 5.1 低频微地点裁剪 | 3 | 无 |
| 5.2 战场微地点折叠 | 3 | 5.1 |
| 6.1 健康度指标 | 3 | 无 |

### Sprint 3 (D9-D12): 重分析+验证

| Story | SP | 依赖 |
|-------|-----|------|
| 三本小说重分析 | — | Sprint 1-2 全部完成 |
| Rebuild + 健康度检查 | — | 重分析完成 |
| 6.2 三本回归验证 | 3 | 全部 |
| 版本发布 v0.67.0 | — | 验证通过 |
