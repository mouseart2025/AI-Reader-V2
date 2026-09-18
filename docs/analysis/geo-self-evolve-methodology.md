# 地点/层级/地图质量自进化系统方法论（GeoEvolve）

- 状态：草案 v1（实验分支 `exp/geo-self-evolve`）
- 日期：2026-09-18
- 范围：地点提取、地点层级关系、地图质量三个方向的质量自迭代系统
- 约束：在独立分支实验，不影响 main 工作流；取得明确成果后再协商并入

> 参考文章说明：用户提供的 4 篇知乎参考文中，仅第 1 篇（周星星《什么是 Self-evolving / self-improving / RSI？一篇文章搞懂自进化》）成功取得全文（经 qingkeai.online 镜像），其余 3 篇被知乎反爬拦截（HTTP 403），未能读取。从检索结果推断它们同属"自进化"主题系列（三层分类体系、Harness 工程等）。本方法论以已确认的第 1 篇 + 公开的自进化文献（AlphaEvolve、Sakana RHI、MiniMax M2.7 scaffold 迭代、Lilian Weng《Harness Engineering for Self-Improvement》）为基础。若其余 3 篇有超出此范围的具体主张，请提供正文，方法论将做相应修订。

---

## 1. 目标与非目标

**目标**：建立一个可无人值守运行 N 轮的"测量 → 变异 → 评估 → 门禁 → 保留/回退"闭环，持续提升三项质量：

1. 地点提取质量（召回、忠实度、类型/角色标注正确性）
2. 地点层级质量（parent 准确率、链正确率、孤儿率、tier 合理性）
3. 地图质量（约束满足率、GeoNames 命中率、布局可读性代理指标）

**非目标**：

- 不做模型层自进化（不训练/微调权重）。
- 不在循环内修改黄金数据集与指标口径（防 Goodhart，见 §6）。
- 不改动前端交互与主数据流 API。

## 2. 理论框架：三层自进化在本项目的映射

按"自进化三层分类"（Artifacts / Harness / Model），本项目做前两层：

| 层 | 优化对象 | 本项目对应物 | 迭代方式 |
|---|---|---|---|
| Artifacts | 单次任务的产出物 | 每部小说的层级树、地图布局、satisfaction | rebuild → eval → 保留最优快照（已有 `SnapshotStore` 版本链） |
| Harness | 跨任务复用的脚手架 | 抽取 prompt、geo-skill 管线配置、过滤词表、地名字典、约束权重、特性开关 | LLM/算子变异 → 冻结指标评估 → 门禁保留/回退 |
| Model | 模型参数 | —— | 不做（仅支持跨模型 A/B 对照） |

关键借鉴：

- **Karpathy autoresearch**：变异算子只准动一个受控面（genome 文件），固定评估、客观打分、改好就留、改差就扔。
- **AlphaEvolve**：候选配置构成小种群，自动评估器打分，进化选择保留最优；评估器必须自动且 cheap。
- **Sakana RHI**：LLM 提议器读"自我比较历史"生成下一代 harness 变异。
- **MiniMax M2.7**：循环 = 分析失败 → 规划改动 → 修改 → 跑评测 → 对比 → 保留/回退，百轮无人值守。
- **Lilian Weng**：harness 迭代是性价比最高的自进化层；提议改动不必用最贵模型，中档模型即可。

## 3. 现状盘点：已有的底座（不要重造）

项目已具备自迭代所需的四大件，本系统是"泛化 + 自动化"而非从零建设：

| 能力 | 现有资产 | 在 GeoEvolve 中的角色 |
|---|---|---|
| 测量 | `backend/src/utils/topology_metrics.py`（parent P/R、chain_accuracy、orphan_rate）；`backend/scripts/quality_dashboard.py`（M1–M6，口径已预注册冻结）；`ConstraintSolver._calculate_satisfaction`（`map_layout_service.py:2371`，地图质量分） | 冻结评估器 |
| 黄金数据 | `backend/tests/fixtures/golden_standard_*.json`（西游/红楼/水浒人工标注） | 进化内层评估集 |
| 版本化 | `hierarchy_snapshots` 快照链（`geo_skills/snapshot_store.py`）；`audit_reports/quality_history.jsonl` | 保留/回退 + 时序日志 |
| 门禁 | `backend/scripts/quality_loop.py`（硬门禁，回归退出码非零）；`benchmark_hierarchy.py`（回归 >0.01 退出码 2） | keep/revert 判定 |
| 自迭代原型 | `backend/scripts/hierarchy_iteration.py`（rebuild→eval→错误分类→overrides→repeat）；`backend/scripts/auto_improve.py`（audit→自动改词表→revalidate） | 泛化为统一进化循环的两个前身 |
| 变异面 | prompt 全外置（`backend/src/extraction/prompts/*.txt`）；模型热切换（`infra/llm_client.py`）；特性开关 5 个 | genome 的基因位 |

## 4. 系统设计

### 4.1 基因组（Genome）：允许变异的受控面

所有可变异项收敛到一个版本化配置文件 `backend/scripts/evolve/genome.yaml`，循环只允许动这个文件（Karpathy "只改 train.py"原则的对应物）。基因位分五级，按风险从低到高：

1. **词表/字典类**（最安全）：`fact_validator.py`/`name_authority.py` 过滤词表、`_SUPPLEMENT_GEO` 手工字典、别名增补。变更可自动从"未解析地名/误剔实体"审计报告生成。
2. **权重/参数类**：`SpatialRelationship` 置信阈值、parent 票聚合权重、edmonds 先验权重、`ConstraintSolver` 各约束权重、tier 分类阈值。
3. **prompt 类**：`extraction/prompts/*.txt` 中的地点规则段（89–110 行）、`world_structure_update.txt`、`hierarchy_*.txt`。由 LLM 提议器改写，每次只改一个文件的一段。
4. **管线结构类**：geo-skill 顺序/开关（`orchestrator.py` 的 tier→votes→prior→edmonds→suffix→purify）、reviewer pass 次数。
5. **模型选择类**：`LLM_PROVIDER`/模型档位切换（仅做 A/B 对照，不进主进化循环）。

前两级可全自动；第 3 级自动但需抽样人工审查；第 4 级每轮需人工确认；第 5 级独立实验。

### 4.2 评估器（Evaluator）：双集防过拟合

- **内层集（进化用）**：3 部黄金小说（西游/红楼/水浒）的黄金指标 + M1–M6 + satisfaction。口径冻结，循环内任何人/LLM 不得修改 `quality_dashboard.py`、`topology_metrics.py`、golden fixture（由分支保护 + 循环脚本自检文件 hash 保证）。
- **留出集（泛化检查用）**：demo 中的非黄金小说（三国、封神）只算 M1–M6/satisfaction（无人工标注）。每隔 K 轮或内层集显著提升时跑一次；**内层涨、留出跌 = 过拟合信号，触发回退该变异谱系**。
- 综合分：先用单项硬门禁（任何冻结指标回归 > 阈值即拒绝），再在通过者中按预设权重加和排序。权重写在 genome 外的 `eval_policy.yaml`，改它等于改实验协议，需人工 commit。

### 4.3 进化循环（一轮）

```
1. ANALYZE   读取上轮评估产物:错误分类(层级错挂/孤儿/未解析地名/约束违例类型分布)
             + quality_history.jsonl 趋势
2. PROPOSE   变异提议器(规则算子 for 基因级1-2;LLM 提议器 for 级3,读"自我比较历史")
             产出 1~3 个候选 genome 变异,每个带假设说明
3. APPLY     在隔离工作区应用变异(不动 main 代码;配置级变异直接生效,
             词表类经 auto_improve 既有注入路径)
4. EVAL      跑评估套件:golden pytest 子集 + M1-M6 + satisfaction(3 部内层集)
             单次评估预算有上限(时间/LLM 调用),超预算记为失败变异
5. GATE      硬门禁:冻结指标无回归(阈值:单项 >0.01 绝对回归即拒,沿用
             benchmark_hierarchy 惯例);软排序:综合分比较
6. COMMIT    通过 → 记录为新一代(best genome + 指标 + 假设 → evolution_journal.jsonl),
             打 hierarchy snapshot;拒绝 → 回退,记录失败原因(失败史供 PROPOSE 避免重复)
7. REPORT    每轮追加一行到 journal;每 10 轮生成可读报告(趋势图数据 + 保留/拒绝率
             + 当前最优 genome diff)
```

### 4.4 与既有脚本的关系

- `quality_loop.py` 仍是"系统级总门禁"，GeoEvolve 的每代 best genome 必须能过它。
- `hierarchy_iteration.py` 与 `auto_improve.py` 的变异逻辑迁移为基因级 1–2 的"规则算子"实现，不再各自为政。
- `hierarchy_snapshots` 做数据产物的版本链；`genome.yaml` 本身用 git 做版本链（每代一个 commit）。

## 5. 分支实验计划

分支：`exp/geo-self-evolve`（已从 main@6d7d2391 切出）。实验代码全部放 `backend/scripts/evolve/`，不改 `backend/src/` 既有行为（若必须加钩子，用特性开关包裹且默认关）。

- **阶段 0 — 基线与骨架**（退出标准：一键跑通空转 3 轮）
  - `evolve/genome.yaml` 落出现行默认配置（从代码里抽取现状值）
  - `evolve/run_loop.py`：ANALYZE/EVAL/GATE/COMMIT 先用规则算子 + 恒等变异跑通
  - 基线测量：3 部内层集 + 2 部留出集的当前分数，写入 `evolve/baseline.json`
- **阶段 1 — 词表/字典级自进化**（最安全，验证闭环真能提分）
  - 退出标准：≥20 轮无人值守；内层集未解析地名率或误剔率显著下降且留出集无回归
- **阶段 2 — 权重/参数级进化**（AlphaEvolve 式小种群，种群 4–6，锦标赛选择）
  - 退出标准：parent_precision 或 satisfaction 至少一项较基线提升且过 `quality_loop.py`
- **阶段 3 — prompt 级进化**（LLM 提议器，RHI 式自我比较历史；每轮抽样 10% 变更人工审）
  - 退出标准：M5 忠实度或 M2 召回代理提升，且 LLM-judge 抽检无退化
- **阶段 4 — 总结与并入谈判**：产出对比报告（基线 vs 最优代，内层/留出双集，成本统计），与本文件 §7 标准逐条核对

## 6. 安全规则（反 Goodhart / 反漂移）

1. 循环脚本启动时校验 `quality_dashboard.py`、`topology_metrics.py`、golden fixtures、`eval_policy.yaml` 的 hash，与基线记录不符即中止。
2. 任何变异不得删除测试、放宽断言、改阈值口径（genome 内阈值除外，且其变动本身被记录）。
3. 留出集结果只用于"回退触发"和"最终报告"，不进选择压力，避免间接过拟合。
4. 每代必须能通过 `quality_loop.py` 硬门禁；连续 5 轮无改进自动暂停并输出诊断，不空转烧钱。
5. prompt 变异产物需过 `fact_validator` 全量回归 + LLM-judge 抽检，防止"指标涨、文本烂"。
6. 成本护栏：每轮 LLM 调用数与 wall-clock 上限写死在 `eval_policy.yaml`。

## 7. 并入 main 的协商标准（预设）

满足以下全部条件后，发起并入评审：

1. 最优 genome 在内层集至少 2 项冻结指标显著提升（>0.02），无任何一项回归。
2. 留出集（三国/封神）无指标回归。
3. 通过 `quality_loop.py` + `benchmark_hierarchy.py` 全量门禁。
4. ≥50 轮进化的 journal 完整可审计，保留/拒绝决策可复现。
5. 新基础设施（`backend/scripts/evolve/`）自身有 pytest 覆盖，main 既有 79 个测试文件全绿。
6. 产出一份"哪些基因位最有效"的分析结论，供后续主线采用。

## 8. 风险与开放问题

- **评估成本**：内层集 3 部小说全量 rebuild + eval 若超过 ~30 分钟/轮，需做增量评估（只重跑受影响子树）或降频全量+高频抽样子集。
- **LLM 提议器质量**：本地 qwen3:8b 提议 prompt 变异可能太弱；预留云端中档模型（DeepSeek 档）做提议器、本地模型做被执行者的分工。
- **黄金集规模**：3 部小说、指标粒度较粗，小提升可能淹没在噪声里——必要时阶段 1 先扩标注（qa-review 工具已有）。
- **未读参考文**：见文首说明，若其余 3 篇包含与本设计冲突的主张（如不同的评估哲学），需修订本文。
