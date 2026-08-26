# Judge 评分口径 v1(冻结)

> 依据:PRD Epic 3(FR-3.1–FR-3.4)。
> 状态:**v1 冻结**。任何维度定义、评分标准、校准阈值、prompt 口径的变更,必须升版本号(v2)并新建文档,旧版本文档不改。
> 代码落点:`backend/scripts/judge_extraction_faithfulness.py`(评分 + 校准全流程)、`backend/scripts/quality_dashboard.py::compute_m5`(Q0 消费)、`backend/src/extraction/chapter_fact_extractor.py::span_located`(evidence 定位的唯一实现)。

## 1. 定位与边界(NFR-1,最高优先级)

- judge 分数**只作相对指标**(管线版本间对比、回归监测),**绝不写入任何论文冻结数字**(4,941 节点 gold、63/279 等一律不触碰)。
- judge 是自动评测回路,不替代人工复核:其产出经 `quality_audit.py` 同款闭环(`audit_reports/` + review.html)供人工抽检。
- 所有 judge 决策落审计日志(NFR-5),采样种子固定 `SEED=42`(沿用 `quality_dashboard.py` 约定)。

## 2. 三维度定义与评分标准

对每章的 chapter_facts(本版覆盖 relationships 与 events 两类条目),judge 按三个**平行维度**独立评分,各 0.0–1.0:

| 维度 | 定义 | 评分标准 |
|---|---|---|
| `precision`(精确度) | 抽取的关系/事件本身是否都有原文支持、类型/内容是否正确 | 1.0 = 全部条目有原文支持且类型正确;每发现一条错抽/编造/类型错误即按比例扣分 |
| `faithfulness`(忠实度) | 每条 evidence span 是否真实出自原文、且能直接支持对应条目 | 1.0 = 全部 evidence 逐字出自原文且支持对应条目;evidence 缺失、伪造(原文中不存在)、张冠李戴(存在但不支持该条)均扣分 |
| `comprehensiveness`(完整性) | 原文中明确写出的重要人物关系/事件被抽取覆盖的比例 | 1.0 = 无明显遗漏;只统计**明确写出且重要**的关系/事件,琐碎过渡不计遗漏 |

配套口径:

- **本地 span 预检**(不调 LLM):每条 evidence 用 `span_located()`(去空白子串匹配)在**全章原文**中定位,产出 `evidence_coverage`(非空 evidence 占比)与 `span_located_rate`(可定位占比)。送 judge 的原文可截断(`MAX_CONTENT_CHARS=12000`),但本地预检不受截断影响。
- **M5 综合分** = 三维度均值的算术平均,无任何维度加权。
- judge 逐条裁定(`item_verdicts`)中 `supported=false` 的条目,以及本地预检不可定位的 evidence,转为与 `quality_audit.py` 同构的 findings(`unsupported_by_text` / `evidence_not_locatable`),进入 review.html 人工复核。

## 3. Prompt 版本 judge-v1(2026-08-26)口径

- 版本字符串:`judge-v1(2026-08-26)`,写入每条审计日志与每份报告的 `prompt_version` / `judge_prompt_version` 字段。
- **评分 prompt**:输入为章节原文(截断)+ 待评条目清单(序号 + 种类 + 标签 + evidence,缺失标 `(缺失)`);输出严格 JSON,含三维度 `{score, reason}` 与逐条 `item_verdicts`(label 用序号,reason 中文)。`temperature=0.0`。
- **校准 prompt**:输入为"论断 + 原文语境"(IAA 任务的 `context_snippets`,单条截断 600 字),judge 二值判定 `supported`(语境直接支持为 true;矛盾或完全无法支持为 false),每批 20 条。
- prompt 文本以代码中的 `JUDGE_SYSTEM` / `JUDGE_USER_TEMPLATE` / `CALIBRATE_SYSTEM` / `CALIBRATE_USER_TEMPLATE` 常量为准(入库常量,口径可追溯);任何修改必须升 prompt 版本字符串。

## 4. 校准规则(FR-3.3)

- **数据约定**:沿用 `compute_iaa.py` —— 标注员 B 文件(`paper/iaa/iaa_annotation_*.json`,默认取最新)+ IAA 任务三类条目(locations/characters/relations);人工标签取自标注员 B(locations/characters 用 `is_valid`,relations 用 `type_agrees`),标签缺失的条目跳过。
- **相关性指标**:judge 二值判定 vs 人工标签的 **Cohen's kappa**,复用 `compute_iaa.cohens_kappa` 同一实现(口径一致);同时报告 observed_agreement 与分类别 kappa。
- **校准阈值**:`KAPPA_THRESHOLD = 0.40`(Landis & Koch "moderate" 下限)。
- **降级规则**:校准报告缺失、样本为空、或 kappa < 0.40 时,Q0 仪表盘的 M5 一律标记为 **"未校准"**(`compute_m5` 的 `calibration_label`);此时 M5 分数仍可展示,但只作参考,不得用于版本间达/未达标判定。
- 校准报告落 `backend/audit_reports/judge_calibration.json`(机器可读,Q0 消费)+ `.md`(人读)。

## 5. 决策审计日志(JSONL,NFR-5)

路径:`backend/audit_reports/judge_decisions_log.jsonl`,与 `entity_resolution_log.jsonl` 同目录、同款模式(每行一条 JSON,追加写)。每章一条记录,字段:

| 字段 | 类型 | 说明 |
|---|---|---|
| `timestamp` | str | UTC ISO-8601 写入时间 |
| `prompt_version` | str | prompt 版本(见 §3) |
| `novel_id` / `title` | str | 小说标识 |
| `chapter_num` | int | 章节号 |
| `scores` | object | `{precision, faithfulness, comprehensiveness}` 三维分数(0.0–1.0 或 null) |
| `total_items` | int | 本章待评条目数(关系 + 事件) |
| `evidence_coverage` | float \| null | 非空 evidence 占比(本地预检) |
| `span_located_rate` | float \| null | evidence 可在原文定位占比(本地预检) |

评分报告(`judge_faithfulness_{slug}_{date}.json`)额外含 `findings` 字段,与 `quality_audit.py` 报告同构(`entity_name` / `entity_type` / `error_type` / `confidence` / `reason` / `fix_target`),`generate_review_page.py` 可直接消费。

## 6. 版本与变更

- v1 冻结日期:2026-08-26(W4–W5,Epic 3)。
- 变更流程:新建 `judge-rubric-vN.md` → 同步 `PROMPT_VERSION`、`KAPPA_THRESHOLD`、judge prompt 常量与测试 → PRD Epic 3 验收记录中注明版本。
