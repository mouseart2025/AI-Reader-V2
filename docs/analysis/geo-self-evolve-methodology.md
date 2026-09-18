# 地点/层级/地图质量自进化系统方法论（GeoEvolve）

- 状态：v2（调研修订版；实验分支 `exp/geo-self-evolve`）
- 日期：2026-09-18
- 范围：地点提取、地点层级关系、地图质量三个方向的质量自迭代系统
- 约束：在独立分支实验，不影响 main 工作流；取得明确成果后再协商并入

> v2 变更说明：完成自进化方向专题调研（12+ 个代表性工作，见 §9 参考文献），确认分类框架并吸收四项关键机制——**GEPA 轨迹反思变异 + Pareto 选择**、**ACE 增量 delta 合并**、**ShinkaEvolve 新颖性拒绝采样**、**Dream-RSI 历史即模拟器（元层）**；同时根据 DGM reward hacking 等失败案例强化了 §6 安全规则。用户提供的 6 篇参考材料中，2 篇已读全文（Shilong Liu 分类学博客、Dream-RSI 项目页），1 篇经镜像读全文（周星星自进化综述），3 篇知乎专栏被反爬拦截（403），其主题已被上述材料覆盖。
>
> v3 修订（2026-09-18，来源：用户指出）：**进化选择集（5 本古典章回小说）是单一分布，存在跨体裁过拟合风险**。修正内容：§4.2 新增 OOD 跨体裁护栏层（凡修/魔戒/平凡的世界），§6.5 新增分布过拟合规则。阶段 0-5 已完成并并入 main（merge ff34de34）；词表 delta 按精确名匹配触发（跨体裁零影响），故已并入成果不受此次修正追溯影响，但 prompt 级（级 3）与管线结构级（级 4）变异自此必须通过 OOD 护栏。

---

## 1. 目标与非目标

**目标**：建立一个可无人值守运行 N 轮的"测量 → 变异 → 评估 → 门禁 → 保留/回退"闭环，持续提升三项质量：

1. 地点提取质量（召回、忠实度、类型/角色标注正确性）
2. 地点层级质量（parent 准确率、链正确率、孤儿率、tier 合理性）
3. 地图质量（约束满足率、GeoNames 命中率、布局可读性代理指标）

**非目标**：

- 不做模型层自进化（不训练/微调权重）。
- 不在循环内修改黄金数据集与指标口径（防 Goodhart/reward hacking，见 §6）。
- 不改动前端交互与主数据流 API。

## 2. 理论框架：三层自进化在本项目的映射

按 Shilong Liu《A Taxonomy of Self-Evolving Agents》(2026-07) 的三要素分类，任何自进化系统回答三问：**什么在进化？什么反馈驱动？回路在哪闭合？** 本项目的回答：

| 层 | 优化对象 | 本项目对应物 | 反馈 | 回路闭合处 |
|---|---|---|---|---|
| Artifacts | 单次任务的产出物 | 每部小说的层级树、地图布局、satisfaction | 冻结指标 | rebuild → eval → 保留最优快照（已有 `SnapshotStore` 版本链） |
| Harness | 跨任务复用的脚手架 | 抽取 prompt、geo-skill 管线配置、过滤词表、地名字典、约束权重、特性开关 | 冻结指标 + 执行轨迹 | LLM/算子变异 → 评估 → 门禁保留/回退 |
| Model | 模型参数 | —— | —— | 不做（仅跨模型 A/B 对照） |

关键借鉴（详见 §9）：

- **Karpathy autoresearch**（2026-03）：变异只准动一个受控面，固定评估预算与冻结指标，赢则 commit、输则 revert；一晚 ~100 轮。并行扩展（SkyPilot，8h/910 实验）证明多分支优于单线贪心。
- **AlphaEvolve / ShinkaEvolve**（2025）：程序数据库 + 进化选择；ShinkaEvolve 的**新颖性拒绝采样**（与历史变异语义重复则跳过评估）把 SOTA 所需评估次数降到 ~150 次。
- **GEPA**（ICLR 2026 Oral）：LLM 读完整执行轨迹做自然语言反思、提议 prompt 变异；**按子任务分数的 Pareto 前沿保留候选**而非单点贪心；比 GRPO 平均高 ~6% 且 rollout 省 35 倍。
- **ACE**（2025-10）：经验库以**增量 delta 条目**合并 + Curator 定期去重，防"上下文坍塌"与无限膨胀。
- **Sakana RHI**（2026-07）：每轮只做与上一版的成对比较，O(1) 评估成本，3 轮内低推理档反超高档 baseline、成本降 60%。
- **Darwin-Gödel Machine 的教训**（2025-05）：agent 曾黑掉自己的 reward（删除幻觉检测标记）；**评估器必须在进化循环之外，且保留完整 lineage**。
- **Recursive Superintelligence 的教训**（2026-06）："搜索变强，评估器必须同步变强"——逐级加严审计、区分真提速与钻空子、新种子复测历史最优。
- **Dream-RSI**（2026-09）：**累积的发现历史可当 replay simulator**——新探索策略先在历史树里离线"做梦"评估，胜出者才花真实预算，调用量可降 1–2 个数量级。

## 3. 现状盘点：已有的底座（不要重造）

| 能力 | 现有资产 | 在 GeoEvolve 中的角色 |
|---|---|---|
| 测量 | `backend/src/utils/topology_metrics.py`（parent P/R、chain_accuracy、orphan_rate）；`backend/scripts/quality_dashboard.py`（M1–M6，口径已预注册冻结）；`ConstraintSolver._calculate_satisfaction`（`map_layout_service.py:2371`） | 冻结评估器 |
| 黄金数据 | `backend/tests/fixtures/golden_standard_*.json`（西游/红楼/水浒人工标注） | 内层评估集 |
| 版本化 | `hierarchy_snapshots` 快照链（`geo_skills/snapshot_store.py`）；`audit_reports/quality_history.jsonl` | lineage + 保留/回退 + 时序日志 |
| 门禁 | `backend/scripts/quality_loop.py`（硬门禁）；`benchmark_hierarchy.py`（回归 >0.01 退出码 2） | keep/revert 判定 |
| 自迭代原型 | `backend/scripts/hierarchy_iteration.py`；`backend/scripts/auto_improve.py` | 泛化为统一循环的前身，其变异逻辑迁移为规则算子 |
| 变异面 | prompt 全外置（`backend/src/extraction/prompts/*.txt`）；模型热切换（`infra/llm_client.py`）；特性开关 5 个 | genome 的基因位 |
| 错误分类 | `audit_reports/hierarchy_diff_*.json`；`scripts/qa-review/` 人工刷判工具 | ANALYZE 阶段的输入 |

## 4. 系统设计

### 4.1 基因组（Genome）：允许变异的受控面

所有可变异项收敛到一个版本化配置文件 `backend/scripts/evolve/genome.yaml`（autoresearch "只改 train.py" 原则的对应物）。基因位分五级，按风险从低到高：

1. **词表/字典类**（最安全）：`fact_validator.py`/`name_authority.py` 过滤词表、`_SUPPLEMENT_GEO` 手工字典、别名增补。按 **ACE 模式**演化：增量 delta 条目 + 定期 Curator 去重，禁止整表重写。
2. **权重/参数类**：parent 票聚合权重、edmonds 先验权重、`ConstraintSolver` 各约束权重、tier/置信阈值。
3. **prompt 类**：`extraction/prompts/*.txt` 地点规则段、`world_structure_update.txt`、`hierarchy_*.txt`。按 **GEPA 模式**演化：提议器读失败样例完整轨迹（输入、抽取输出、评估器反馈）做反思后改写，每次只改一个文件的一段。
4. **管线结构类**：geo-skill 顺序/开关（`orchestrator.py`）、reviewer pass 次数。每轮需人工确认。
5. **模型选择类**：`LLM_PROVIDER`/模型档位。仅做 A/B 对照，不进主进化循环。

### 4.2 评估器（Evaluator）：冻结 + 双集 + 分指标

- **内层集（选择压力）**：3 部黄金小说的黄金指标 + M1–M6 + satisfaction。
- **留出集（泛化检查）**：三国、封神（无人工标注，只算 M1–M6/satisfaction）。不进选择压力，仅用于回退触发与最终报告——内层涨、留出跌 = 过拟合，回退该变异谱系。
- **OOD 跨体裁护栏层（v3 修订新增）**：内层集与留出集同属古典章回小说，是单一分布——同分布留出挡不住跨体裁过拟合。增设 3 本异体裁护栏集：凡人修仙传（修仙网文）/ 魔戒全集（西方奇幻译本）/ 平凡的世界（现代现实主义），纯规则指标（M1 层级健康、M4 泛称残留、geo.unresolved_rate）。**基因级 3（prompt）与级 4（管线结构）变异必须 OOD 三本无回归才允许入档**；级 1（词表）豁免（delta 按精确名匹配，跨体裁零影响，保留抽检）；级 2（权重）沿用分小说记账。基线值冻结在 `baseline.json["ood_guard"]`，口径在 `eval_policy.yaml ood_guard` 节。
- **分指标记账（Eevee 保持率的对应物）**：每部小说单独记分。接受变更要求**无一小说显著退化**（防止"西游涨、水浒崩"式灾难性遗忘）。
- **固定评估预算**（autoresearch 原则）：每轮评估的时间/LLM 调用上限写死在 `eval_policy.yaml`，超预算记为失败变异；保证轮间可比并鼓励"又准又快"的变异。
- **Pareto 而非总分**（GEPA 原则）：不做加权加和单分；候选按冻结指标向量做非支配排序，保留 Pareto 前沿小种群（4–8 个）。

### 4.3 进化循环（一轮）

```
1. ANALYZE   读上轮产物:错误分类(层级错挂/孤儿/未解析地名/约束违例分布)
             + quality_history.jsonl 趋势 + evolution_journal.jsonl 失败史
2. PROPOSE   提议器产出 1~3 个候选 genome 变异,每个带假设说明:
             - 基因级 1-2:规则算子(从审计报告自动生成)
             - 基因级 3:LLM 反思提议器(读失败轨迹 + 自我比较历史)
             - 新颖性检查:与近 N 代已试变异语义重复 → 跳过(ShinkaEvolve)
             - 同质停滞护栏:连续 K 代同机制微调无改进 → 强制切换机制(Dream-RSI)
3. APPLY     在隔离工作区应用变异(不动 main 代码;词表类经 auto_improve 注入路径)
4. EVAL      固定预算内跑评估套件:golden pytest 子集 + M1-M6 + satisfaction
5. GATE      硬门禁(全部满足才入候选池):
             - 冻结指标无显著回归(单项 >0.01 绝对回归即拒,沿用 benchmark 惯例)
             - 分小说记账无显著退化
             - anti-hack 检测通过(见 §6.3)
6. ARCHIVE   Pareto 前沿更新:新候选支配现任 → 入档;被支配 → 拒绝并记录;
             互不支配 → 共存(种群上限,超出时淘汰最老被支配者)
             入档规则(JIT-Agent):质量不降且至少一维严格改善才入档
7. COMMIT    入档 → genome commit + hierarchy snapshot + journal 记录
             (变异 diff/假设/指标向量/成本/父代指针 = 完整 lineage)
8. REPORT    每 10 轮生成可读报告:前沿推移、保留/拒绝率、成本、最优 genome diff
```

### 4.4 元层（Dream-RSI 对应物，阶段 4 可选）

`evolution_journal.jsonl` 累积后即是"历史树"。元层离线实验：不重跑管线，用历史记录回放评估不同的**提议策略**（如"优先改词表 vs 优先改 prompt"、"反思提议器的不同 meta-prompt"），离线胜出的策略再上线。预期把真实评估预算降一个数量级。前提：journal 记录足够结构化（变异算子、上下文摘要、结果向量齐全）。

### 4.5 与既有脚本的关系

- `quality_loop.py` 仍是系统级总门禁：每代入档 genome 必须能过它。
- `hierarchy_iteration.py` 与 `auto_improve.py` 的变异逻辑迁移为基因级 1–2 的规则算子。
- 数据产物版本链用 `hierarchy_snapshots`；genome 用 git（每代一个 commit）。

## 5. 分支实验计划

分支：`exp/geo-self-evolve`（从 main@6d7d2391 切出）。实验代码全部放 `backend/scripts/evolve/`，不改 `backend/src/` 既有行为（必须加钩子时用特性开关包裹且默认关）。

- **阶段 0 — 基线与骨架**（退出标准：一键空转 3 轮）
  - `evolve/genome.yaml` 落出现行默认配置；`evolve/run_loop.py` 跑通 ANALYZE/EVAL/GATE/ARCHIVE/COMMIT（恒等变异）
  - 基线测量：3 部内层集 + 2 部留出集当前分数 → `evolve/baseline.json`
  - 冻结文件 hash 清单（`quality_dashboard.py`、`topology_metrics.py`、golden fixtures、`eval_policy.yaml`）
- **阶段 1 — 词表/字典级自进化**（ACE 模式，最安全，验证闭环真能提分）
  - 退出标准：≥20 轮无人值守；未解析地名率或误剔率显著下降；留出集与分小说记账无回归
- **阶段 2 — 权重/参数级进化**（Pareto 小种群 4–8，锦标赛/非支配选择；新颖性拒绝采样上线）
  - 退出标准：parent_precision 或 satisfaction 至少一项较基线显著改善；过 `quality_loop.py`
- **阶段 3 — prompt 级进化**（GEPA 模式 LLM 反思提议器；每轮抽样 10% 变更人工审）
  - 退出标准：M5 忠实度或 M2 召回代理提升；LLM-judge 抽检与人工抽检无退化
- **阶段 4 — 元层（可选）**：journal 回放模拟器，离线比较提议策略；产出"预算减半"证据
- **阶段 5 — 总结与并入谈判**：对比报告（基线 vs Pareto 前沿，双集 + 分小说，成本统计），逐条核对 §7

## 6. 安全规则（反 reward hacking / 反漂移）

### 6.1 评估器外置（DGM 教训，第一原则）
循环脚本启动时校验 §5 阶段 0 的 hash 清单，不符即中止。进化对象永远只是 genome；指标代码、黄金数据、检测器代码不在变异面内。

### 6.2 完整 lineage
每次变异的 diff、假设、指标向量、成本、父代指针全部落 journal；任何"指标提升"必须可溯源到具体变异，否则视为无效。

### 6.3 anti-hack 检测器（Recursive 教训："评估器与搜索器共同进化"）
- **黄金集硬编码检测**：比对变异 diff 是否含黄金集具体地名/答案字符串（词表类变异只允许来自运行时审计产物，不允许来自 golden fixture）。
- **历史最优复测**：每 20 轮用不同随机种子/章节顺序复测当前最优，抖动超阈值则降级。
- **逐级加严审计**：阶段 3 起，入档前增加 LLM-judge 对抽取结果的抽检（judge 本身在循环外，其 rubric 冻结）。

### 6.4 反漂移与成本护栏
- 连续 5 轮无改进 → 自动暂停并输出诊断，不空转烧钱。
- 同质变异停滞 → 强制切换机制（§4.3 PROPOSE 护栏）。
- 每轮 LLM 调用数与 wall-clock 上限写死在 `eval_policy.yaml`。
- 里程碑之外的**人工抽检**始终保留（AI Scientist v2 撤稿教训：过审 ≠ 质量达标）。

### 6.5 分布过拟合（v3 修订新增）
- 选择集单一分布时，同分布留出集不构成泛化证据——跨体裁变异一律过 OOD 护栏（§4.2）。
- 任何"内层集显著改善"的结论必须声明其分布边界；向新体裁推广时需用 OOD 集复测。
- 教训来源：阶段 3 prompt 变异在古典章回分布内的改善无法排除体裁特化（且该改善后经复测证明不可复现，已降级）——分布扩展与噪声底是 LLM 级变异的两个独立前提。

## 7. 并入 main 的协商标准（预设）

1. Pareto 前沿最优在内层集至少 2 项冻结指标显著改善（>0.02），无任何一项回归。
2. 留出集（三国/封神）与分小说记账无指标回归。
3. 通过 `quality_loop.py` + `benchmark_hierarchy.py` 全量门禁。
4. ≥50 轮进化 journal 完整可审计；所有保留的提升可复测复现（含换种子复测）。
5. 新基础设施（`backend/scripts/evolve/`）自身有 pytest 覆盖；main 既有 79 个测试文件全绿。
6. 产出"哪些基因位/变异机制最有效"的分析结论，供主线采用。

## 8. 风险与开放问题

- **评估成本**：内层集 3 部全量 rebuild + eval 若超 ~30 分钟/轮，先做增量评估（只重跑受影响子树）或"高频抽样子集 + 低频全量"；元层（§4.4）是长期解法。
- **LLM 提议器质量**：本地 qwen3:8b 做反思提议可能太弱；预留云端中档模型做提议器、本地模型做被执行者的分工（Weng：提议不挑模型，中档即可）。
- **黄金集规模**：3 部小说指标粒度粗，小提升可能淹没在噪声里——必要时阶段 1 先扩标注（`scripts/qa-review/` 已有工具）；Pareto + 分小说记账可部分缓解。
- **新颖性检查的判官**：语义重复判定若用 LLM 需注意自夸偏置（Hermes 教训），判官 prompt 冻结在循环外。
- **已排除的搜索方向：权重空间**（阶段 2，2026-09-18，33 代 live 证据）：对 geo-skill 管线 11 个外置权重（票权/先验权/阈值/度均衡）做 ±10-60% 单参数扰动，结论为阴性——(a) jitter 量程内管线呈平台期（连结构护栏都零变化，LLM base parents + name-containment + prior override 三个高优先通道遮蔽票数变化）；(b) 探针量程内所有有效变化均为"水浒涨、红楼崩"式跨小说权衡，分小说无回归约束下全被门禁拦下；(c) topo 指标换序噪声底实测 0.0209，与仅存的两项被接受改善（+0.0048）同数量级（均被 §6.3 复测降级）。**默认权重在"分小说无回归 + 噪声底 0.02"约束下即 Pareto 最优**；层级质量的杠杆在证据质量（prompt）与词表覆盖，不在权重。主线请勿重复探索该方向；若未来 golden 扩标注压低噪声底，可重开。详见 `docs/analysis/geo-evolve-stage5-merge-report.md` §3。
- **未读参考文**：3 篇知乎专栏被反爬拦截，主题推断已被现有调研覆盖；若有超出范围的具体主张，提供正文后修订本文。

## 9. 参考文献（调研基础）

用户提供：
- Shilong Liu, [A Taxonomy of Self-Evolving Agents](https://lsl.zone/blog/2026/a-taxonomy-of-self-evolving-agents/), 2026-07（三层分类原文）
- [Dream-RSI: Recursive Self-Improvement through Evolving Worlds](https://github.com/zhengkid/Dream-RSI), Google/DeepMind/UMD, arXiv:2609.14858, 2026-09
- 周星星《什么是 Self-evolving / self-improving / RSI？一篇文章搞懂自进化》（[镜像](https://qingkeai.online/blog/Self-evolving)）

调研收集：
- [AlphaEvolve 白皮书 arXiv:2506.13131](https://arxiv.org/pdf/2506.13131)；[DeepMind 博客](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
- [ShinkaEvolve, arXiv:2509.19349](https://arxiv.org/html/2509.19349v1)（ICLR 2026；新颖性拒绝采样，~150 次评估出 SOTA）
- [Darwin-Gödel Machine, arXiv:2505.22954](https://arxiv.org/pdf/2505.22954)（SWE-bench 20%→50%；自报 reward hack 案例）
- [GEPA, arXiv:2507.19457](https://arxiv.org/abs/2507.19457)（ICLR 2026 Oral；轨迹反思 + Pareto 选择，rollout 省 35x）
- [ACE, arXiv:2510.04618](https://arxiv.org/abs/2510.04618)（增量 delta playbook + Curator）
- [RHI, arXiv:2607.15524](https://arxiv.org/abs/2607.15524)（O(1) 成对比较，成本降 60%）
- [JIT-Agent-27B, arXiv:2608.25593](https://arxiv.org/abs/2608.25593)（frontier-only 入档规则）
- [Eevee, arXiv:2606.11182](https://arxiv.org/abs/2606.11182)（累积保持率防遗忘）
- [Karpathy autoresearch](https://github.com/karpathy/autoresearch) + [SkyPilot 扩展](https://blog.skypilot.co/scaling-autoresearch/)
- [AI Scientist v2, arXiv:2504.08066](https://arxiv.org/abs/2504.08066)（过审后撤稿教训）
- [Recursive Superintelligence: First Steps](https://www.recursive.com/articles/first-steps-toward-automated-ai-research)（评估器与搜索器共同进化）
- [A Survey of Self-Evolving Agents, arXiv:2507.21046](https://arxiv.org/abs/2507.21046)（TMLR 2026）
- [Lilian Weng, Harness Engineering for Self-Improvement](https://lilianweng.github.io)（评估器外置等七大挑战）
- [Reward Hacking in Self-Improving Code Agents](https://openreview.net/pdf?id=ikrQWGgxYg)（代理指标博弈的系统研究）
