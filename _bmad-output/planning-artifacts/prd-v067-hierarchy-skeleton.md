---
stepsCompleted: [step-01-init, step-02-discovery, step-02b-vision, step-02c-executive-summary, step-03-success, step-04-journeys, step-05-domain, step-06-innovation, step-07-project-type, step-08-scoping, step-09-functional, step-10-nonfunctional, step-11-polish, step-12-complete]
classification:
  projectType: full-stack-web-app
  domain: ai-nlp-chinese-novel-analysis
  complexity: high
  projectContext: brownfield
workflowType: 'prd'
---

# PRD: v0.67 宏观地理骨架重构 — 从扁平到深层

**Author:** leonfeng
**Date:** 2026-03-31
**Status:** Ready for Development
**Deadline:** 2026-04-23（论文 Phase 3 消融实验前）

---

## Executive Summary

v0.66 通过 golden standard 闭环将地点层级 precision 提至 100%，但全量诊断暴露了系统性骨架缺陷：三本小说的层级平均深度仅 2-3 层，"天下"直挂 90+ 子节点，缺少中间层。

**根因**：三个独立机制协同压扁了层级：
1. **MacroSkeletonGenerator** 被硬编码为生成 2-3 级骨架
2. **提取 prompt** 没有宏观地理上下文，LLM 不知道应该有中间层
3. **投票解析** 用 winner-takes-all，中间层投票被吞没

v0.67 从**提取层到 rebuild 层**全链路改进，目标是让系统自主建出 4-6 层深度的正确地理骨架，无需人工 override。

---

## Problem Domain — 三层根因

### Layer 1: 提取层 — LLM 不知道宏观地理

`context_summary_builder._build_macro_hub_section()` 只展示 depth=1 的宏观区域：
```
### 本小说主要宏观区域
- **东胜神洲** [continent]（含 25 处下属地点）→ 傲来国、花果山...
```

LLM 看到的是**扁平列表**，不是层级树。它不知道"花果山在傲来国，傲来国在东胜神洲"，所以提取时把花果山的 parent 直接标为东胜神洲（跳过傲来国）。

### Layer 2: 骨架层 — 硬编码 2-3 级

`macro_skeleton_generator.py` 的 prompt 明确要求"2-3层的核心地理骨架"，只支持 {world, continent, kingdom, region, city} 五个 tier，缺少 province/prefecture/county 等中间层。

### Layer 3: 投票层 — 中间层被吞没

`_resolve_parents()` 用 `most_common(1)` 做 winner-takes-all。当花果山有 5 票投傲来国但 10 票投东胜神洲时，东胜神洲直接胜出，傲来国作为中间层消失。

---

## Success Criteria

### 量化指标

| 指标 | v0.66 基线 | v0.67 目标 |
|------|-----------|-----------|
| 平均层级深度（三本均值） | 2.8 | **≥ 4.0** |
| "天下"直接子节点数 | 90+ | **≤ 20** |
| 最大子节点数 | 99 | **≤ 30** |
| Tier 逆转数 | 39 | **0** |
| 噪声根节点 | 5 | **0** |
| Golden standard precision | 100% | **≥ 95%**（不退化） |

### 定性指标

- 西游记：四大部洲作为 root，每个洲下有国→城→建筑层级
- 红楼梦：都中→宁荣街→荣国府→大观园→各院 完整链路
- 水浒传：天下→山东/河北/京畿→州→县→城/寨 行政层级

---

## Scope

### In Scope

| # | Epic | 描述 | 层 | 优先级 |
|---|------|------|-----|--------|
| E1 | 宏观骨架深化 | MacroSkeletonGenerator 支持 4-5 级，prompt 重写 | 骨架 | P0 |
| E2 | 提取上下文增强 | context_summary_builder 注入深层层级树 | 提取 | P0 |
| E3 | 投票解析改进 | 中间层保护 + 超大父节点拆分 | 投票 | P0 |
| E4 | Tier 重分类 | suffix rank 扩充 + 上下文 tier 修正 | 分类 | P1 |
| E5 | 微地点治理 | 裁剪/折叠低频微地点 | 后处理 | P1 |
| E6 | 全量健康度监控 | 自动化指标 + 三本小说验证 | 评估 | P1 |

### Out of Scope

- 地图渲染改动（本轮只改数据质量）
- 新增 golden standard for 水浒传
- 地点别名归一化（v0.66 已做）

---

## Functional Requirements

### Epic 1: 宏观骨架深化 (P0)

**目标：** 骨架从 2-3 级 → 4-5 级

**FR-1.1: MacroSkeletonGenerator prompt 重写**
- 当前 prompt 要求"2-3层核心骨架"→ 改为"4-5层完整地理骨架"
- 新增 tier 层级模板：
  - 幻想类：天下 → 大洲 → 国/域 → 城/山 → 建筑/洞府
  - 现实类：天下 → 省/道 → 州/府 → 县/镇 → 街/村
  - 家宅类：城 → 区/街 → 府/宅 → 园 → 院/阁/斋
- Genre-aware：根据 novel genre 注入对应模板

**FR-1.2: 骨架深度验证**
- 生成后检查：如果最大深度 < 4，要求 LLM 补充中间层
- 如果某个节点子节点 > 20，要求 LLM 拆分为子区域

**FR-1.3: 骨架投票权重增强**
- 当前骨架投票权重 5-10，容易被 chapter_facts 的大量基线投票(1)覆盖
- 骨架层级关系权重提升：深层关系（depth 3-4）权重 15-20

### Epic 2: 提取上下文增强 (P0)

**目标：** 让 LLM 提取时知道完整的宏观地理框架

**FR-2.1: _build_macro_hub_section 输出深层树**
- 当前：只显示 depth=1（天下的直接子节点）
- 改为：显示 depth=3 的层级树，格式：
  ```
  ### 本小说地理框架
  - 东胜神洲 [大洲]
    - 傲来国 [国家]
      - 花果山、傲来城、...
    - 宝象国 [国家]
      - ...
  - 西牛贺洲 [大洲]
    - ...
  ```
- 限制：每个层级最多显示 10 个子节点，超出用 "+N" 折叠

**FR-2.2: 提取 prompt 增强 parent 指导**
- extraction_system.txt 新增规则：
  - "parent 应填写**直接**上级，不要跳层。如果花果山在傲来国，parent=傲来国（不是东胜神洲）"
  - "参考上方地理框架中的层级结构"
- 新增 negative examples：
  - ❌ 花果山 → parent=东胜神洲（跳过了傲来国）
  - ✅ 花果山 → parent=傲来国

**FR-2.3: Context budget 调整**
- 深层树比扁平列表占更多 token
- 需要在 context_budget.py 中为地理框架预留足够 token

### Epic 3: 投票解析改进 (P0)

**目标：** 保护中间层投票，消除 winner-takes-all 吞没

**FR-3.1: 中间层投票保护**
- 当前：`most_common(1)` 直接选最高票
- 改为：如果最高票是跨层（如花果山→东胜神洲 跳了 2 个 tier），检查是否有中间层候选：
  - 如果花果山有 5 票投傲来国 + 10 票投东胜神洲
  - 且傲来国→东胜神洲 在骨架中存在
  - 则选傲来国（更精确的直接 parent），而非东胜神洲

**FR-3.2: 超大父节点拆分**
- consolidate_hierarchy 新增步骤：
  - 如果某节点子节点 > 30，检查子节点的 tier 分布
  - 如果子节点包含不同 tier（如 kingdom + city + site 混合），按 tier 分层
  - 创建虚拟中间节点（如"山东诸城"）或使用已有的中间节点

**FR-3.3: 噪声根节点清理**
- 检测非 world/continent tier 的根节点（如"池上"、"考场"、"雪岸"）
- 自动归入最可能的父节点（基于共现证据）或标记为噪声删除

### Epic 4: Tier 重分类 (P1)

**FR-4.1: suffix rank 扩充**
- 当前 suffix rank 缺少中间行政层级
- 新增：省/道(rank=1.5), 州/府(rank=2.5), 县/镇(rank=3.5)
- 修复：东京 不应该是 world tier（它是 city）

**FR-4.2: 上下文 tier 修正**
- 如果一个地点的 tier 与其 parent 的 tier 冲突（如 city 挂在 site 下），自动修正
- 修正策略：基于 suffix rank + parent tier 推断正确 tier

### Epic 5: 微地点治理 (P1)

**FR-5.1: 低频微地点裁剪**
- 出现 ≤ 1 章、无子节点、tier=building/site 的地点
- 如"路口"、"树下"、"门外" → 从层级中移除（保留在 chapter_facts 中不删除）
- 阈值可配置

**FR-5.2: 战场微地点折叠**
- 检测模式：某父节点 50+ 子节点，且 >80% 的子节点 freq=1
- 这些是战场/旅途章节的一次性微地点
- 折叠为"X附近" 虚拟节点（不影响 chapter_facts 原始数据）

### Epic 6: 全量健康度监控 (P1)

**FR-6.1: 健康度指标自动计算**
- 新增 `compute_hierarchy_health(location_parents, location_tiers)` 函数
- 指标：平均深度、最大子节点数、tier 逆转数、噪声根节点数、孤儿率
- 在 rebuild-hierarchy 完成后自动输出

**FR-6.2: 三本小说回归验证**
- 扩展 hierarchy_iteration.py：除了 golden standard precision，还检查健康度指标
- CI 可选：rebuild 后自动跑健康度检查

---

## Implementation Order

| 天 | Epic | 任务 | 说明 |
|----|------|------|------|
| D1-2 | E1 | 骨架 prompt 重写 + 深度验证 | 改 macro_skeleton_generator + macro_skeleton.txt |
| D3 | E2 | 提取上下文深层树注入 | 改 context_summary_builder + extraction_system.txt |
| D4 | E3 | 中间层投票保护 | 改 _resolve_parents |
| D5 | E3+E5 | 超大父节点拆分 + 微地点裁剪 | 改 consolidate_hierarchy |
| D6 | E4 | Tier 重分类 | 改 world_structure_agent tier 逻辑 |
| D7 | E5 | 噪声根节点清理 + 战场折叠 | 改 consolidate_hierarchy |
| D8 | E6 | 健康度指标 + 回归验证 | 新增函数 + 扩展 hierarchy_iteration.py |
| D9-10 | — | **三本小说重分析** | MiniMax 云端 ~2-3h/本 |
| D11 | — | rebuild + 验证 | 跑 rebuild-hierarchy + 健康度检查 |
| D12 | — | 对比 v0.66 + 版本发布 | 确认不退化 + v0.67.0 |

---

## Technical Risks

| 风险 | 影响 | 缓解 |
|------|------|------|
| 深层骨架 LLM 生成不稳定 | 骨架质量波动 | 骨架验证 + 重试机制 |
| 提取上下文增加导致 token 超限 | 部分章节截断 | context_budget 预留 + 折叠策略 |
| 中间层保护过于激进 | 正确的跨层 parent 被拒绝 | suffix rank 差距阈值（跳1层OK，跳2层才触发） |
| 重分析后关系/别名质量变化 | v0.66 的 80%/91% 可能波动 | 重分析后重跑五维度评估 |
| 微地点裁剪影响地图渲染 | 地图上少了些点 | 裁剪只影响层级树，不删 chapter_facts |

---

## Non-Functional Requirements

- 测试：现有 352 tests 全通过 + 新增健康度测试
- 性能：rebuild-hierarchy 时间不增加 >50%
- 兼容：未重分析的小说在 rebuild 时也能改善（E3-E5 在 rebuild 层生效）
- 可观测：rebuild 完成后输出健康度报告
