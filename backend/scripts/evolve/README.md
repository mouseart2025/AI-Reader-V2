# GeoEvolve — 自进化系统实验代码

规格：`docs/analysis/geo-self-evolve-methodology.md`。阶段 0/1/2 已完成，
本文档记录各阶段设计选型（阶段 3 在最上）。

## 阶段 3 设计（2026-09-18，GEPA 模式，prompt 级）

### 变异面

`extraction_system.txt` 的**"地点提取规则"段**（`## 地点提取规则（locations）`
至 `## 空间关系提取规则` 标记定界，原文 39 行）。每轮只改这一段（归因清晰）。
`prompt_state.json` 存 original_section/override_section 为单一事实源，
APPLY=段落替换原子写，回退/启动自愈按 state 重渲染（测试验证逐字节还原）。
genome.yaml level 3 已登记 mutable_section 与 state_file。

### 噪声底先行（任务书要求 1，实测后预注册进 eval_policy v3 再开进化）

E0 双跑（原 prompt、DeepSeek temp=0、冻结子集、A/B 两遍）实测：

| 指标 | 西游 | 红楼 | 水浒 | macro |
|---|---|---|---|---|
| prompt.recall A/B | 0.4891/0.4088 | 0.3918/0.4124 | 0.5185/0.5185 | 噪声 0.0199 |
| recall 噪声 | **0.0803** | 0.0206 | 0.0000 | — |
| generic_rate 噪声 | 0.0085 | 0.0194 | 0.0147 | — |

**DeepSeek temp=0 对长结构化输出并不确定**——单本噪声底最高 0.08。
预注册显著性阈值：单本 recall ±0.09、macro recall ±0.03、generic ±0.03、
count_inflation 相对 1.3×。小于噪声底的"改善/回归"不参与判定
（GATE 的 guard_overrides 与 ARCHIVE 的 min_improvement 同口径）。

### 评估分层（成本控制）

- **冻结基准**（一次性构建，$0.56，全部入 frozen_manifest）：
  `fixtures/stage3_chapters.json`（每本 5 章 = dashboard seed=42 抽样章的索引
  [0,2,4,6,8] 子集）、`stage3_t_set.json`（T 集 = M2 扫描 prompt 对 10 个抽样章
  的 DeepSeek 冻结结果）、`stage3_e0_baseline.json`（E0 双跑 + 噪声底 +
  judge 基线 supported_rate=0.25）。
- **快速层**（每代）：冻结 15 章重抽，并发 5，约 15 次调用 ≈ $0.10/代。
  指标：prompt.recall / count_inflation / generic_rate + macro。
- **确认层**（过门禁才花）：judge 抽检新增地名（E'∖E0，≤12 条，冻结 rubric
  `prompts/judge_spotcheck_s3.txt`），supported_rate < 0.25（=E0 双跑差异名的
  实测质量基线）即拒。judge 仅覆盖 locations——既有 judge_extraction_faithfulness
  只评关系/事件，故按同款模式（系统身份 + 严格 JSON + 逐条 verdict）新设地点版。
- 预算硬约束：llm_calls ≤ 100/代（LlmBudget.charge 逐次）、$2/代、1800s/代，
  超限记失败变异。

### GEPA 提议器

`prompts/propose_s3.txt`（冻结,sha256 入清单）+ DeepSeek temp=0。
输入轨迹：当前段落原文 + 失败样例（T∖E' 漏提名 × 原文 ±80 字语境窗口，
≤12 条）+ 护栏快照 + journal 中过往 prompt 变异成败（近 5 条）。
输出严格 JSON {hypothesis, new_section}，校验（锚点保留/长度 ≤2.5×/标题行/
不越界）→ anti-hack（新增文本含 golden 答案串即拒，原段落已有示例豁免）→
**带反馈重试**（最多 3 次，被拒原因喂回重写）。

### 人工复核

全部接受/拒绝的 prompt diff 逐代汇总到 `out/stage3_review.md`
（假设 + unified diff + 指标变化 + judge 结果），供人工复核（替代方法论的
"每轮抽样 10% 人工审"——无真人介入，改为全量可追溯材料）。

## 阶段 2 设计（2026-09-18 调研后定型）

### 外置参数（11 个，genome.yaml level 2 已落真值）

| 参数 | 出处 | 默认 | 范围 | 选择理由 |
|---|---|---|---|---|
| vote_builder.chapter_slope | vote_builder.py:123 | 0.5 | [0,2] | 后章证据加权斜率，直接影响票分布 |
| vote_builder.peer_discount | vote_builder.py:138 | 0.33 | [0,1] | peers 互指折扣，影响互挂边 |
| vote_builder.spatial_high_weight | vote_builder.py:159 | 2 | [1,4] | 高置信 contains 票权 |
| vote_builder.primary_setting_weight | vote_builder.py:216 | 2 | [0,5] | 主场景推断票权（orphan 主要来源通道） |
| vote_builder.baseline_weight | vote_builder.py:254 | 1 | [0,3] | 既有 parent 保留强度 |
| vote_builder.uber_root_cap | vote_builder.py:266 | 2 | [1,5] int | 天下通吃抑制 |
| knowledge_prior.prior_weight | knowledge_prior.py:27 | 20 | [5,40] | 先验 vs 章节票的力量对比（注释明写 5-15 典型票） |
| edmonds.tier_soft_penalty | edmonds_resolver.py:99 | 0.1 | [0.01,0.5] | tier 倒置软惩罚 |
| edmonds.name_contain_weight | edmonds_resolver.py:122 | 25.0 | [10,50] | 名包含注入权（注释明写须 >典型票 1-15） |
| edmonds.prior_threshold | edmonds_resolver.py:255 | 15.0 | [5,30] | 先验覆盖 LLM parent 的阈值 |
| edmonds.max_children | edmonds_resolver.py:359 | 30 | [15,60] int | 度均衡上限 |

**弃选记录**：tier_classifier 阈值（mc≥30/ch≥15 等）嵌在复合布尔表达式里，
外置注入点大、行为漂移风险高，弃；ConstraintSolver 约束权重——satisfaction
只有西游有布局且布局重算成本高，留到地图质量专项；`fallback_weight=0.001`
（edmonds :161）量级太小不敏感，弃。

### 注入机制（生产行为逐字节不变）

新文件 `src/services/geo_skills/evolve_params.py`：`evolve_param(key, default)`
未设 `EVOLVE_PARAMS_JSON` 环境变量时原样返回 default（=原硬编码字面量）；
设置后按 key 注入（mtime 缓存，EVAL 每代换文件即生效）。11 个注入点均为
"原字面量包一层"的最小编辑。**不变证据**：钩子合入后、未设环境变量跑
compute_weight_metrics 全量重建，五本 topo/结构指标与合入前完全一致
（xiyouji 0.3438/0.3235/0.2553 等逐值相同）；设参后 sanguo max_children
64→87，证明注入真实生效。

### 敏感指标（eval_policy v2 预注册）

内层三本：`<slug>.topo.{parent_precision,parent_recall,chain_accuracy}`——
scratch DB 上 fresh 重建（规则管线 ~4.5s/五本，无 LLM；封神跳过 prior skill
避 LLM 路径，父子同口径）后 vs golden fixture 的 topology_metrics。
默认参数基线：西游 0.344/0.324/0.255，红楼 0.583/0.539/0.337，水浒 0.600/0.548/0.449。
全五本护栏：`<slug>.rebuild.{max_children,root_count}`（rebuild 后 orphan 恒 0，
orphan_rate 无区分度故弃）。

### 算子与种群

`weight_jitter`：单代只扰动 1 个参数（归因），±10-20% 乘性小步 + clamp +
int 取整 + 零值加性退化；目标选择=**轮询**（gen % 11）+动量（改善同向、
被拒反向），替代阶段 1 的单点贪心。Pareto 前沿**按阶段分档**
（`out/frontier_stage2.json`）——阶段 1 前沿与阶段 2 目标空间不同，
混档会导致跨阶段支配误判（实测 gen23 因此被误拒，已修）。

### §6.3 复测降级

收尾用 `--permute-chapters`（scratch 里按种子置换 chapter_facts 的 fact_json，
行序不动）以 2 个种子重评当前最优；任一内层 topo 指标抖动 >0.01 即降级
（移出前沿 + journal 记 downgraded_unstable）。

## 阶段 1 设计（2026-09-18 调研后定型）

### (a) 可变异基因位

`backend/src/services/geo_resolver.py` 的 **`_SUPPLEMENT_GEO` 手工地名字典**
（name → (lat, lng)，486 条现状）。选型理由：

- 它是生产解析链路的**第一优先级**（`resolve_names` Level 1，curated 数据优先于
  GeoNames 噪声匹配），变异真实生效；
- 该字典本来就是人工逐条策展的（历史/文学地名 section 即先例），ACE 增量 delta
  模式与其天然吻合；
- 变异面窄、单向（只加坐标，不删不过滤），是方法论里"最安全"的基因级 1。

变异**不直接改** `_SUPPLEMENT_GEO` 字面量，而是在文件末尾维护一个定界块
（`# ── GeoEvolve delta begin/end ──` 包裹的 `_SUPPLEMENT_GEO.update({...})`），
由 `vocab_delta.json`（单一事实源，git 跟踪）确定性重渲染，随时可无损还原。

### (b) 敏感指标：`<slug>.geo.unresolved_rate`（越低越好）

定义（阶段 1 预注册，随 eval_policy v1 冻结）：对每本小说，取
`world_structures.location_parents` 全集地名（冻结 DB，只读），用生产函数
`GeoResolver(dataset_key="cn").resolve_names(names, parent_map)` 解析，
`unresolved_rate = 1 − |resolved| / |names|`。

选型理由：

- **纯规则、本地数据**（CN.txt / cities5000.txt / zh_geonames.tsv 已缓存在
  `~/.ai-reader-v2/geonames/`），每轮重算全五本 ≈ 6s（索引加载一次性 4.6s +
  每本 <0.2s），可进每轮 EVAL；LLM 指标（M2/M3）每轮重算太贵，保持基线缓存值。
- 对 supplement 字典变异**直接敏感且单调**：每接受一条 delta，目标小说未解析数
  严格减一，无噪声淹没。
- 直接对应阶段 1 退出标准"未解析地名率显著下降"。

基线（2026-09-18，冻结 DB）：xiyouji 0.809 / honglou 0.870 / shuihu 0.765 /
sanguo 0.658 / fengshen 0.785。

已知口径偏差（记录在案）：西游生产 `geo_type=fantasy`，正常管线不做坐标解析；
本指标绕过 geo_type 门直接测量"解析器+字典对该小说地名集的覆盖能力"，是字典
覆盖率的测量，不是生产行为的重放。 supplement 条目增多反而可能把 geo_type
检测推向 mixed（`detect_geo_type` 统计 supplement 命中），对西游是正向作用。

其余指标处理：golden pytest（16 题，~2s，纯本地）每轮实跑作硬门禁；M1–M6 /
satisfaction 不受 geo 字典影响（层级在冻结 DB 里、M4 用 fact_validator 词表），
沿用 baseline.json 缓存值参与 GATE/ARCHIVE，口径在 baseline.json 注明。

### (c) 变异原料

仅来自**运行时产物**（§6.3）：冻结 DB 的 `location_parents`（未解析名清单）+
生产函数 `_find_resolved_ancestor` 推出的最近已解祖先坐标 + chapter_facts 里的
地名频次（排序用）。**禁止**来自 golden fixture；`geo_vocab.anti_hack_filter`
对每条 delta 做黄金集原文包含检测，命中即剔除并记账。

提议器是规则算子（非 LLM）：每轮选剩余候选池最大的小说，按频次降序取前 K
（默认 10）条通过 Curator 的名字，以其已解祖先坐标为坐标。

### Curator 去重/冲突规则（ACE）

逐条检查，任一命中即剔除该条（其余保留）：

1. 重复：已在 `_SUPPLEMENT_GEO` / `_SUPPLEMENT_CN` / 已提交 delta 集合中；
2. 冲突：已在已提交 delta 中但坐标不同（拒绝覆盖，需人工）；
3. 语义冲突：`fact_validator._is_generic_location(name, genre)` 判定应过滤
   （泛称/非地点不该有坐标）；
4. 形态：长度 <2、无已解祖先（坐标无来源）；
5. anti-hack：出现在 golden fixture 原文中。

### APPLY 隔离与崩溃恢复

- 单一事实源 = `vocab_delta.json`（已提交 delta）；源文件只是它的渲染产物。
- 每代：渲染 源文件 = pristine + block(已提交+候选) → EVAL →
  接受：提交 delta 落盘（文件已是该状态）；拒绝：`finally` 中重渲染为已提交状态。
- 硬杀（SIGKILL）遗留脏文件 → 下次循环启动时按 `vocab_delta.json` 重渲染自愈。
- pristine 内容不需要备份文件：`strip_evolve_block` 定界剥离即可确定性重建。

### LLM 预算真实计数

`run_loop.LlmBudget`：EVAL 路径每次 LLM 调用前 `charge()` 计数，超
`budget.llm_calls_per_generation` 抛 `LlmBudgetExceeded`，该代记失败变异。
阶段 1 的 EVAL 全为规则路径（golden pytest 子进程 + geo 度量子进程均不调 LLM），
实测每代 llm_calls=0；计数器对后续阶段的 LLM 提议器/judge 即插即用。
wall-clock 超限同记失败（阶段 0 已有）。

## 运行

```bash
cd backend
# 阶段 0 空转
.venv/bin/python scripts/evolve/run_loop.py --generations 3 --dry-run
# 阶段 1 live 进化（真实评估：golden 门禁 + geo 未解析率逐轮重算）
.venv/bin/python scripts/evolve/run_loop.py --stage 1 --generations 20 --eval-backend live
# 阶段 2 live 进化（权重扰动 + rebuild 拓扑指标 + 收尾复测与全量门禁）
.venv/bin/python scripts/evolve/run_loop.py --stage 2 --generations 22
# 阶段 3 live 进化（GEPA prompt 变异 + 冻结子集快速层 + judge 抽检）
.venv/bin/python scripts/evolve/run_loop.py --stage 3 --generations 15
# 报告
.venv/bin/python scripts/evolve/run_loop.py --report
# 重建基线（含 geo.unresolved_rate）
.venv/bin/python scripts/evolve/build_baseline.py
# 阶段 3 冻结基准一次性重建（删除 fixtures/stage3_* 后）
.venv/bin/python scripts/evolve/build_stage3_fixtures.py
```

产物：`baseline.json` / `vocab_delta.json` / `weights_state.json` /
`prompt_state.json` / `evolution_journal.jsonl` / `fixtures/stage3_*.json` /
`prompts/*_s3.txt`（git 跟踪）；`out/`（dashboard 产物、frontier*.json、
candidate_params.json、stage3_review.md，gitignored）。
