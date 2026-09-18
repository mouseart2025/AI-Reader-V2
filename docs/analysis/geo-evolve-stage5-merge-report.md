# GeoEvolve 阶段 5 —— 总结与并入谈判报告

- 日期：2026-09-18 · 分支：`exp/geo-self-evolve`（从 main@6d7d2391 切出）
- 规格：方法论 §5（阶段 5）/ §7（并入 main 的 6 条协商标准）
- 实验规模：74 代 journal 记录（阶段 0 骨架 2 + 阶段 1 词表 20 + 阶段 2 权重 34 + 阶段 3 prompt 15 + 特殊记录 3），总 LLM 成本 **$3.03**（阶段 3 $1.89 + 冻结基准 $0.56 + 阶段 5 复测 $0.13 + 阶段 0 dashboard $0.10 + judge 基线等 $0.32），阶段 1/2/4 零 LLM 成本
- 全部证据文件：`backend/scripts/evolve/`（baseline.json / evolution_journal.jsonl / vocab_delta.json / weights_state.json / prompt_state.json / frozen_manifest.json / out/replay_result.json / out/stage5_verification.json / out/stage3_review.md）

---

## 1. §7 六条协商标准逐条核对

| # | 标准 | 结论 | 证据 |
|---|---|---|---|
| 7.1 | Pareto 前沿最优在内层集 ≥2 项冻结指标显著改善（>0.02），无回归 | **部分达标** | 水浒 `geo.unresolved_rate` 0.7653→0.6870（−0.078，规则指标无噪声，复算逐值一致）✅；西游 `prompt.recall` 记录 +0.102 / 复测 +0.029（噪声底 0.09 内，方向为正、幅度不确定）⚠️；无回归 ✅（门禁拦下全部 21 次超噪回归）。严格意义上只有 1 项确定显著 |
| 7.2 | 留出集（三国/封神）与分小说记账无回归 | **达标** | journal 全 74 代逐代分小说记账；三国/封神零回归（geo 微改善 −0.0015/0，topo 护栏在口径修正后无越界）；阶段 2 gen31/37/41/44 四次"水浒涨红楼崩"被分小说门禁拦下（实证防遗忘有效） |
| 7.3 | 过 quality_loop + benchmark_hierarchy 全量门禁 | **达标** | `quality_loop.py --tag evolve-stage5-final` exit=0（golden 16/16，全指标 delta=0）；`benchmark_hierarchy.py` 西游/红楼/水浒 exit=0（errata 残留 31/23/141 为 DB 现状，与进化无关——进化不改 DB 层级） |
| 7.4 | ≥50 轮 journal 完整可审计；保留的提升可复测复现（含换种子） | **达标（附声明）** | 74 代，元层字段（policy_version/state_sha256/context_hash/judge verdicts 路径）新代自动带齐、历史代回填（71/74 重建指纹，context_hash 历史代 null 已声明）；`stage5_verify.py` 复测：topo 换序抖动 ≤0.0209=基线噪声底（pass）；prompt macro 复测 0.5074→0.4796（jitter 0.0278 ≤ 0.03 噪声底，pass 但**幅度在噪声带内**，见 §3 阴性分析） |
| 7.5 | evolve/ 自身 pytest 覆盖；main 既有测试全绿 | **达标** | evolve 测试 132 个（stage0 31 + stage1 27 + stage2 30 + stage3 26 + stage4 21 中含 manifest 等）覆盖 Pareto/门禁/回退/anti-hack/回放；全量套件 **1421 passed, 3 skipped**（2026-09-18，含全部进化产物在内的工作区） |
| 7.6 | "哪些基因位/机制最有效"分析结论 | **达标** | 见 §4 |

**总体结论：建议"部分并入"而非整体并入** —— 词表 delta 与基建证据充分；prompt 变异方向为正但幅度在噪声带内，建议并入但标注为"待黄金集扩标注后复测确认"；权重级为阴性结果，不产出并入项（只产出知识）。

---

## 2. 正式证据链（2026-09-18 复跑）

### 2.1 系统级门禁（当前工作区 = 最优 genome：142 条词表 delta + gen64 prompt + 默认权重）

```
quality_loop.py --tag evolve-stage5-final  → exit=0（golden 16/16，各指标 delta +0.0000，"未见回退"）
benchmark_hierarchy.py --novel=xiyouji     → exit=0（errata 残留 31；规则引擎 recall 48.4%）
benchmark_hierarchy.py --novel=honglou     → exit=0（残留 23；recall 78.3%）
benchmark_hierarchy.py --novel=shuihu      → exit=0（残留 141；recall 74.5%）
```

注：benchmark_hierarchy 测的是 DB 中的层级现状（进化不改 DB，评估全部在 scratch/冻结副本上），其 gold errata 残留数与进化前一致——它证明的是"进化没有污染主数据流"，这正是 §6 安全规则的目标。

### 2.2 换种子/换序复测（`stage5_verify.py`，产物 `out/stage5_verification.json`）

| 指标族 | 复测方式 | 结果 | 判定 |
|---|---|---|---|
| topo（阶段 2，当前=默认权重） | 换章节顺序 seed 7/23 重建 | 最大抖动 0.0056/0.0209 | ✅ ≤ 基线噪声底+阈值 0.0309（v2.2 口径） |
| prompt recall（阶段 3 gen64 变异） | 冻结 15 章快速层重跑（$0.13） | macro 0.5074→0.4796，jitter 0.0278；单本最大 jitter 0.073（西游） | ✅ 在噪声底内（macro≤0.03，单本≤0.09），但**相对基线 +0.013 的下界低于 0.03 显著线**——方向正、幅度不确定 |
| geo.unresolved_rate（阶段 1） | 确定性复算 | 水浒 0.687036 与 journal 逐值一致 | ✅ 规则指标无噪声 |

---

## 3. 阴性结果正式分析（阶段 2：权重/参数级）

**假设**：层级质量可通过外置权重的局部扰动（11 个参数，±10-60%）在冻结指标上获得 >0.02 的显著改善。

**实验**：34 代 live 进化（jitter ±10-20% → 停滞后自动切 probe ±30-60%，轮询+动量，单参数归因），全部分代门禁+Pareto 记账，0 LLM 成本。

**证据**：
1. 平台期：jitter 前 5 代指标**零变化**（连结构护栏都不动）——管线对小幅权重扰动不敏感（LLM base parents + name-containment + prior override 三个高优先通道遮蔽了票数变化）；
2. 权衡墙：所有产生变化的探针（gen31/33/37/41/44）都是"水浒涨、红楼崩"或反向，分小说无回归约束下无一通过；
3. 噪声底：默认参数自身换序抖动 0.0209（红楼 precision），与两次被接受改善的幅度（+0.0048）同数量级——被接受项均被 §6.3 复测正确降级；
4. 回放器确认：33 个已实现动作对动作空间（11 参数 × 2 方向 × 2 机制 × 父代态）太稀疏，选择策略间无法分辨高下（UNEXPLORED 主导）。

**结论**：默认权重在"分小说无回归 + 噪声底 0.02"约束下是 Pareto 最优（或极接近）。层级质量的杠杆不在权重而在**证据质量**（抽取 prompt）与**词表覆盖**——这与阶段 1/3 的阳性结果方向一致。

**对主线的建议**：不要为此调权重；若要提升层级指标，优先 (a) 扩大 golden 标注量以压低噪声底（当前 ~50-130 地点/本，抖动 ±0.02）、(b) 投 prompt/词表通道。

---

## 4. 基因位/机制有效性分析（§7.6）

| 基因位 | 投入 | 产出 | 单位成本产出 |
|---|---|---|---|
| L1 词表/字典（ACE delta） | 20 代 / $0 / 167s | 水浒未解析率 −0.078（显著、无噪声、复算一致）+ 142 条坐标字典 | **最高**（零成本 + 确定性收益） |
| L3 prompt（GEPA 反思） | 15 代 / $1.89(+基准 $0.56) / 1007s | macro recall +0.013~+0.041（方向正、幅度噪声带内）；产出 3 条通用规则 | 中（有成本 + 幅度待确认） |
| L2 权重/参数 | 34 代 / $0 / 180s | 阴性结果（默认即 Pareto 最优）；产出噪声底数据 + 外置钩子 | 无直接收益，但排除了一整片假设空间 |

**机制层面**：
- ACE 增量 delta + Curator 去重：零事故，142 条全部可追溯（条目级 generation/ancestor/frequency provenance）；
- anti-hack 黄金集检测：词表 58 条黑名单 + prompt 1 次拦截（gen55"两界山"），防泄漏实证有效；
- 分小说记账门禁：拦下全部 21 次灾难性遗忘候选；
- §6.3 复测降级：拦下 2 个噪声级伪改善（阶段 2）；
- 停滞护栏 + 机制切换：阶段 2 自动切换 jitter→probe（打破平台期获得真实信号），阶段 3 停滞警示入轨迹（打破提议器同质循环，3 代后产出唯一被接受变异）；
- **回放器策略证据（阶段 4）**：词表进化若用 max_marginal 策略，达同等总降幅只需 6 代（省 70% 预算）；round_robin/ucb1 需 11 代（省 45%）。已接 `--proposer-policy`。

---

## 5. 并入方案建议

| 成果 | 建议 | 理由 / 风险 / 回退 |
|---|---|---|
| 142 条词表 delta（geo_resolver.py 定界块） | **并入** | 水浒未解析率 −0.078 确定性收益；风险=坐标为"最近已解祖先"近似值（地图上点位精度限于父级）；回退=`git revert` 定界块或清空 vocab_delta.json 后 heal 重渲染（逐字节还原，有测试锁定） |
| prompt 三条规则（extraction_system.txt 2b/2c/2d） | **并入（附条件）** | 方向为正（两次测量都超基线）；幅度在噪声带内 → 附条件：黄金集扩标注或 M5 校准完成后复测确认；风险=可能多提泛指专名（generic_rate 护栏内）；回退=单文件 revert 或 prompt_state.json override 置空 + heal |
| 11 个参数外置钩子（evolve_params.py + 3 文件 11 处注入点） | **并入** | 默认关=行为逐字节不变（实测重建指标逐值一致 + 1421 测试全绿）；为未来调参/灰度实验留通道；风险=近乎零；回退=删钩子恢复字面量 |
| evolve/ 基建（run_loop/geo_vocab/weight_jitter/prompt_evolve/replay/fixtures/测试） | **并入** | 自包含于 scripts/evolve/，不触生产路径；冻结清单机制对主线也有防护价值；风险=仓库体积（fixtures ~200KB）；回退=整目录删除 |
| 噪声底数据与 eval_policy 口径 | **并入（随基建）** | 是后续一切质量实验的显著性基准 |
| 阶段 2 权重实验记录 | **留分支**（journal 已含全部 lineage） | 阴性结果有存档价值，但 weights_state.json 的 overrides 为空，无并入物 |
| out/ 运行产物（dashboard、review、replay_result） | **留分支/不入库** | gitignored 维持现状 |
| 阶段 0-2 的 frontier.json 等中间态 | **丢弃**（可再生） | replay/backfill 可从 journal 重建 |

### src 三处改动的生产行为变化明细

1. **`geo_resolver.py` 定界块**（+146 行 `_SUPPLEMENT_GEO.update({...})`）：生产行为变化=142 个水浒系地名从"未解析"变为"解析到最近祖先坐标"。影响面：地图管线的坐标解析（这些地点此前由 `place_unresolved_geo_coords` 在布局时近似放置，现在提前到解析层、确定性更强）。测试证据：全量 1421 passed；geo 复算与 journal 逐值一致。
2. **`extraction_system.txt` prompt**（+3 条规则 −1 空行）：生产行为变化=未来新书的抽取会多提"引述/比喻/旧名/方位参照中的专名"。**不影响已有五本的任何已存数据**（抽取结果已入库）；风险=泛称率可能上升（阶段 3 护栏内）；测试证据：golden 16/16；judge 抽检 0.333 ≥ 0.25 基线。
3. **`evolve_params.py` + 11 处钩子**：生产行为**逐字节不变**（默认关）；证据=钩子合入前后五本重建指标逐值一致 + 全量测试。仅在设 `EVOLVE_PARAMS_JSON` 环境变量时生效（仅进化评估子进程使用）。

---

## 6. 给用户（并入谈判）的决策点清单

1. **词表 delta 并入与否**：坐标是祖先近似值——可接受（建议并入）还是要人工抽检 142 条后再定？（抽检材料：`vocab_delta.json` 每条带 ancestor/frequency）
2. **prompt 变更并入的确认门槛**：接受"方向正+噪声带内"直接并入，还是要求先做 golden 扩标注 / M5 judge 校准再复测？（后者约需：扩标注工时 + ~$5 LLM 校准费）
3. **权重阴性结果是否需要在主线文档中登记**（防止未来重复探索）：建议写入主线 CHANGELOG 或 docs/analysis。
4. **evolve/ 基建并入 main 后的维护责任**：冻结清单会在主线更新评估器时报警——谁负责走 `--freeze` 流程？
5. **下一阶段预算**：若继续（词表阶段 1 用 max_marginal 策略扫其余四本，预计 $0 / ~5 分钟；prompt 阶段 3 第二轮约 $2），是否批准？回放器证据支持词表优先。
