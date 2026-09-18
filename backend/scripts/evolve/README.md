# GeoEvolve — 自进化系统实验代码

规格：`docs/analysis/geo-self-evolve-methodology.md`。阶段 0（基线与骨架）已完成，
本文档记录**阶段 1（词表/字典级自进化，ACE 模式）**的设计选型。

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
# 报告
.venv/bin/python scripts/evolve/run_loop.py --report
# 重建基线（含 geo.unresolved_rate）
.venv/bin/python scripts/evolve/build_baseline.py
```

产物：`baseline.json` / `vocab_delta.json` / `evolution_journal.jsonl`（git 跟踪）；
`out/`（dashboard 产物、frontier.json，gitignored）。
