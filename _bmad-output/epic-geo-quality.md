---
date: '2026-02-19'
scope: 地理数据质量系统性提升（基于深度根因分析）
sourceAnalysis: 地点层级树质量深度根因分析报告（信号利用率矩阵 + 5 问题域诊断）
targetNovel: 《凡人修仙传》210/2452章, 350地点, 天下30直接子节点
---

# 地理数据质量提升 — Epic & Story 规划

## 背景

基于对 AI-Reader-V2 地点层级树的深度根因分析，发现 ChapterFact 中已提取信号的利用率仅约 40-50%。当前 parent 推断仅使用 `locations[].parent` 和 `spatial_relationships[contains]` 两个信号源，而 adjacent/direction/in_between 空间关系、角色-地点共现、场景 sibling 组等高价值信号完全未被利用。同时提取端存在修仙门派层级示例缺失、泛化地名过滤盲区、上下文注入不足等问题。

### 核心根因链

```
LLM 提取 parent 留空率高
  → 大量孤儿节点
    → 投票机制仅用 2/7 种信号源（contains + parent 字段）
      → consolidate catch-all 将所有孤儿直接挂天下
        → 天下 30 个直接子节点，缺乏中间层级

+ 泛化地名泄漏加剧噪声
+ 上下文不足导致后续章节持续产出低质量 parent
```

---

## Epic 总览

| # | Epic | 优先级 | Stories | 核心价值 |
|---|------|--------|---------|----------|
| A | 空间关系信号挖掘 | P0 | 3 | 零 LLM 成本，纯算法利用已有数据 |
| B | 提取与过滤质量提升 | P0 | 4 | 源头治理，改善后续所有新章节的提取质量 |
| C | 上下文注入与整合优化 | P1 | 4 | 提升 LLM 上下文利用率 + 整合逻辑精细化 |

共 **3 个 Epic、11 个 Story**。

---

## Epic A: 空间关系信号挖掘（P0, 纯算法）

**目标：** 挖掘 ChapterFact 中已提取但未用于 parent 推断的空间关系和共现信号，零 LLM 成本提升地点层级质量。

**成功标准：** 重建层级后，天下的直接子节点数从 30 降至 ≤10；depth≥2 的节点占比从当前值提升 20%+。

### Story A.1: Adjacent/Direction 空间关系 → parent 传播投票

As a 地图使用者,
I want 系统利用已提取的"相邻""方位"空间关系来推断地点的父子归属,
So that 即使 LLM 未直接声明 parent，也能通过 A adjacent B 且 B.parent=C 推断 A 也属于 C。

**当前状态：** `_rebuild_parent_votes()` 仅处理 `relation_type == "contains"`，完全跳过 adjacent/direction/distance/in_between 四种空间关系类型。

**实现要点：**
- 修改 `world_structure_agent.py` 的 `_rebuild_parent_votes()` 和 `_apply_heuristic_updates()`
- Adjacent 传播规则：如果 A adjacent B 且 B 已有 parent=C，给 A→C 加投票（权重 +1）
- Direction 传播规则：如果 A direction B 且 tier 相同（sibling），共享 parent 投票
- In_between 传播规则：如果 A in_between B 和 C，三者应共享 parent
- 传播需限制迭代轮次（最多 2 轮），防止误传播扩散

**验收标准：**
- [ ] `_rebuild_parent_votes()` 处理 adjacent/direction/in_between 三种空间关系
- [ ] 传播投票权重 ≤ 直接 parent 声明权重（防噪声放大）
- [ ] 凡人修仙传重建后，天下直接子节点减少 ≥5 个
- [ ] 不引入新的环路（cycle detection 兜底）

**涉及文件：** `backend/src/services/world_structure_agent.py`

---

### Story A.2: Sibling 组 parent 共享传播

As a 地图使用者,
I want 场景转换分析识别的 sibling 组在算法层面自动共享 parent,
So that 频繁双向转换的地点组（如七玄门内的各建筑）能自动归属到同一父节点。

**当前状态：** `SceneTransitionAnalyzer` 的 `sibling_groups` 仅被序列化到 LLM 审查 prompt 中，算法层面完全未利用。

**实现要点：**
- 修改 `scene_transition_analyzer.py` 的 `analyze()` 返回值，或在 `world_structure_agent.py` 中增加后处理
- Sibling 组传播规则：如果 sibling 组 `{A, B, C}` 中 B 已有 `parent = X`，则给 A→X 和 C→X 加投票（权重 +2）
- 仅传播 tier 一致或更低的成员（防止高 tier 节点被错误归属）
- 传播前检查 tier 兼容性：building 级 sibling 可以共享 site/city 级 parent

**验收标准：**
- [ ] Sibling 组中已有 parent 的成员能将 parent 传播给无 parent 的成员
- [ ] 传播仅在 tier 兼容时发生（高 tier 不会被拉入低 tier 的 parent 下）
- [ ] 凡人修仙传重建后，七玄门下的建筑类地点正确归属率提升

**涉及文件：** `backend/src/services/scene_transition_analyzer.py`, `backend/src/services/world_structure_agent.py`

---

### Story A.3: 角色活动范围 → parent 推断

As a 地图使用者,
I want 系统基于角色活动范围推断地点的父子关系,
So that 角色在某区域持续活动时出现的建筑/房间能自动归属到该区域。

**当前状态：** `characters[].locations_in_chapter` 仅用于轨迹和访客统计，未参与 parent 推断。

**实现要点：**
- 新增 `_infer_parents_from_character_colocation()` 方法
- 遍历所有 chapter_facts 的 `characters[].locations_in_chapter`
- 规则：如果角色在连续 N 章（N≥3）活动于 `[大地点A, 小地点B, 小地点C]`，且 A.tier ∈ {region, kingdom, city} 而 B/C.tier ∈ {site, building, room}，且 B/C 无 parent，给 B→A 和 C→A 加投票
- 需要 tier 差异阈值：parent 的 tier rank 至少比 child 低 2 级
- 集成到 `_rebuild_parent_votes()` 流程中

**验收标准：**
- [ ] 基于角色活动共现产生新的 parent 投票
- [ ] 仅当 tier 差异 ≥2 级且共现章节数 ≥3 时才产生投票
- [ ] 投票权重 = min(共现章节数, 5)，防止长期同地活动过度加权
- [ ] 凡人修仙传中"议事大殿""百药园"等建筑能通过角色活动推断归属

**涉及文件：** `backend/src/services/world_structure_agent.py`, `backend/src/db/chapter_fact_store.py`（可能需要新查询）

---

## Epic B: 提取与过滤质量提升（P0, 源头治理）

**目标：** 从 LLM 提取端和后处理过滤端双向提升地点数据质量，减少 parent 留空率和噪声地名泄漏。

**成功标准：** 新章节分析后 parent 非空率从当前 ~60% 提升到 ≥80%；泛化地名泄漏率降为 0。

### Story B.1: LLM 提示词修仙/仙侠层级增强

As a 分析引擎,
I want 提取提示词包含修仙小说的地理层级示例和更积极的 parent 填写指导,
So that LLM 对修仙/仙侠小说的 parent 提取准确率显著提升。

**当前状态：** `extraction_system.txt` 第 44-51 行仅提供行政/机构/奇幻三种层级示例，缺少修仙门派层级。"不确定时留空"导致 LLM 过于保守。

**实现要点：**
- 修改 `extraction_system.txt` 的 parent 规则（第 44-51 行）
  - 增加修仙/仙侠层级示例：`修仙界/天南 > 越国 > 彩霞山 > 七玄门 > 百药园 > 药房`
  - 增加门派/宗门内部层级：`宗门 > 大殿/后山/洞府/禁地`
  - 将"不确定时留空"改为"如果当前场景发生在某个已知地点内部，优先将该地点填为 parent；仅完全无法推断时才留空"
- 修改第 55 行无名地形词规则，增加量词前缀模式

**验收标准：**
- [ ] 提示词包含修仙/仙侠专用地理层级示例
- [ ] parent 填写指导从"留空优先"改为"推断优先"
- [ ] 使用新提示词分析一章凡人修仙传文本，parent 非空率 > 提示词修改前

**涉及文件：** `backend/src/extraction/prompts/extraction_system.txt`

---

### Story B.2: Few-shot 示例 parent 字段修正

As a 分析引擎,
I want few-shot 示例中的 parent 字段填写正确且充分,
So that LLM 从示例中学到"应该积极填写 parent"的行为模式。

**当前状态：** `extraction_examples.json` 中多个示例的 parent=null（七玄门、灵霄宝殿、南天门），错误地教会 LLM 不填 parent。

**实现要点：**
- 修改 `extraction_examples.json`
  - 示例2：七玄门的 `parent` 从 null 改为 "彩霞山"
  - 示例4：灵霄宝殿 `parent` 改为 "天庭"，南天门 `parent` 改为 "天庭"
  - 检查所有示例中 parent=null 的地点，评估是否应填写
  - 增加至少 1 个建筑归属门派/城市的完整示例

**验收标准：**
- [ ] 所有 few-shot 示例中"可推断但填了 null"的 parent 已修正
- [ ] 至少有 1 个示例展示"建筑 → 门派/城市"的 parent 关系

**涉及文件：** `backend/src/extraction/prompts/extraction_examples.json`

---

### Story B.3: 泛化地名过滤加强

As a 数据质量系统,
I want 过滤器能识别并阻止"某条偏僻小路""没有标识的通道"等描述性短语作为地点名,
So that 层级树中不含噪声地名。

**当前状态：** `_GENERIC_MODIFIERS` 缺少"某条""某个""某座"等量词组合；无 `量词+描述性修饰+通名` 模式检测。

**实现要点：**
- 修改 `fact_validator.py`
  - `_GENERIC_MODIFIERS` 补充："某条""某个""某座""某处""某片"
  - 新增 Rule 15：`量词+描述性修饰+通名` 模式检测
    - 正则：`^(某|一)[条个座片处][^\s]{0,4}(路|道|洞|房|殿|厅|屋|廊|洞穴|通道|山洞|小路|大路)$`
  - 可选：降低 Rule 6 长度阈值从 >7 到 >6
- 修改 `extraction_system.txt` 第 55 行，增加量词前缀描述性短语的禁止示例

**验收标准：**
- [ ] "某条偏僻小路" 被 `_is_generic_location()` 拦截
- [ ] "一个破旧的山洞" 被拦截
- [ ] 正常地名（"落日峰""七玄门""越国"）不受影响
- [ ] 现有 350 个地点中的泛化地名可通过重新验证过滤

**涉及文件：** `backend/src/extraction/fact_validator.py`, `backend/src/extraction/prompts/extraction_system.txt`

---

### Story B.4: Tier 分类后缀补充

As a 层级系统,
I want 地名后缀到 tier 的映射更完整,
So that "李长老家"被正确分为 building 而非 kingdom。

**当前状态：** `_NAME_SUFFIX_TIER` 缺少"家""宅""路""处"等常见后缀。Layer 4 fallback 对 parent=None 的孤儿节点过于激进（默认 region/city）。

**实现要点：**
- 修改 `world_structure_agent.py` 的 `_NAME_SUFFIX_TIER` 列表
  - 增加："家" → building, "宅" → building, "路" → site, "处" → site, "舍" → building
- 修改 `_classify_tier()` Layer 4 fallback
  - parent=None 且无法识别 suffix 时，默认 site 而非 city/region
  - 减少对 parent=None + level=0 的 region 分配

**验收标准：**
- [ ] "李长老家" 分类为 building
- [ ] "某条偏僻小路"（如未被 B.3 过滤）分类为 site 而非 kingdom
- [ ] 不影响正常地名的 tier 分类

**涉及文件：** `backend/src/services/world_structure_agent.py`

---

## Epic C: 上下文注入与整合优化（P1）

**目标：** 增强 LLM 提取时的上下文信息，优化 consolidate_hierarchy 的整合逻辑，减少孤儿节点直接挂天下的情况。

**成功标准：** 连续章节分析后 parent 一致性提升；catch-all 后天下直接子节点中建筑类占比 < 10%。

### Story C.1: 当前场景焦点注入机制

As a 分析引擎,
I want 上下文摘要中突出"当前场景焦点"——最近几章角色最频繁出现的地点及其完整层级链,
So that LLM 知道新出现的建筑/房间应归属哪个门派/城市。

**当前状态：** `ContextSummaryBuilder` 注入全局已知地点列表和 top-8 层级链，无"当前场景焦点"概念。

**实现要点：**
- 修改 `context_summary_builder.py` 的 `build()` 方法
- 新增"当前场景焦点"区块：
  - 基于最近 2-3 章 `characters[].locations_in_chapter` 中出现频率最高的 1-3 个地点
  - 展示完整层级链（如 `越国 > 彩霞山 > 七玄门`）
  - 增加 LLM 提示："本章如出现新的建筑/房间名称，优先归属到上述焦点地点"
- 焦点区块放在已知地点列表之前，增加注意力权重

**验收标准：**
- [ ] 上下文摘要包含"当前场景焦点"区块
- [ ] 焦点地点基于最近章节的角色活动频率选取
- [ ] 焦点地点展示完整层级链而非仅一级 parent

**涉及文件：** `backend/src/extraction/context_summary_builder.py`

---

### Story C.2: 层级链与世界结构摘要扩容

As a 分析引擎,
I want 注入 LLM 的层级链条数和世界结构摘要容量增加,
So that LLM 有更完整的世界观信息来推断 parent。

**当前状态：** 层级链 top-8 条，世界结构摘要 500 字符限制。

**实现要点：**
- 修改 `context_summary_builder.py`
  - `max_chains` 从 8 提升到 15（cloud 模式）/ 10（local 模式）
  - 世界结构摘要限制从 500 字符提升到 1500 字符（cloud）/ 800 字符（local）
  - 在层级链部分增加提示："本章如出现新建筑/房间，请查看上述层级中是否有合适的 parent"

**验收标准：**
- [ ] Cloud 模式注入 15 条层级链 + 1500 字符世界结构摘要
- [ ] Local 模式注入 10 条层级链 + 800 字符
- [ ] 不超出 LLM 上下文窗口限制

**涉及文件：** `backend/src/extraction/context_summary_builder.py`

---

### Story C.3: catch-all 分层 — 按 tier 找中间节点

As a 层级整合系统,
I want catch-all 不直接将所有孤儿节点挂到天下，而是先尝试按 tier 寻找合适的中间节点,
So that 天下的直接子节点是 kingdom/region 级别，不出现 building 直挂天下的情况。

**当前状态：** `consolidate_hierarchy` Step 12 将所有无 parent 的节点直接挂到天下。

**实现要点：**
- 修改 `hierarchy_consolidator.py` 的 Step 12
- 分层 catch-all 逻辑：
  1. 先收集所有需要 catch-all 的孤儿节点
  2. 按 tier rank 从低到高排序（building → site → city → region）
  3. 对于 building/room 级孤儿：查找天下子节点中 tier 为 site/city 的节点，选择名称最相似或同章出现过的作为 parent
  4. 对于 site/city 级孤儿：查找天下子节点中 tier 为 region/kingdom 的节点
  5. 无法匹配时仍 fallback 到天下
- 名称相似度可用简单的子串匹配（如"落日峰主殿"匹配"落日峰"）

**验收标准：**
- [ ] building/room 级孤儿优先挂到 site/city 级中间节点而非天下
- [ ] 天下的直接子节点中 building 类占比 < 10%
- [ ] 无法找到合适中间节点时仍正确 fallback 到天下
- [ ] 不引入环路

**涉及文件：** `backend/src/services/hierarchy_consolidator.py`

---

### Story C.4: LLM 多轮审查（分批处理）

As a 层级审查系统,
I want LocationHierarchyReviewer 支持对大量 orphan 分批审查,
So that 超过 100 个 orphan 时不因截断而遗漏。

**当前状态：** 单次 LLM 调用，截断到 200 条层级树 + 100 个 orphan。

**实现要点：**
- 修改 `location_hierarchy_reviewer.py`
- 当 orphan > 80 时，分批处理：每批 60-80 个 orphan
- 每批带入完整层级树（或截断到 200 条最重要的）+ 前一批的审查结果
- 合并所有批次的 suggestions 后返回
- 控制总 LLM 调用次数上限（最多 3 批）

**验收标准：**
- [ ] orphan > 80 时自动分批处理
- [ ] 每批审查结果能叠加到后续批次的上下文中
- [ ] 总 LLM 调用次数 ≤ 3（成本控制）
- [ ] 审查覆盖率从 ~57% 提升到 ≥90%

**涉及文件：** `backend/src/services/location_hierarchy_reviewer.py`

---

## 实施顺序建议

```
Sprint 1（P0 核心）:
  A.1 → A.2 → B.3 → B.4    # 算法挖掘 + 过滤修复（纯后端，可并行）

Sprint 2（P0 提取端）:
  B.1 → B.2 → A.3           # 提示词优化 + 角色活动推断

Sprint 3（P1 上下文与整合）:
  C.1 → C.2 → C.3 → C.4    # 上下文增强 + 整合精细化
```

**关键依赖：**
- A.1/A.2 无依赖，可立即开始
- B.1/B.2 修改提示词后需要重新分析章节才能看到效果
- C.1 依赖 B.1（焦点注入需要正确的 parent 数据才有意义）
- C.3 依赖 A.1/A.2（需要更多 parent 数据才能做分层 catch-all）
