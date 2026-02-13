---
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - PRD.md
  - _bmad-output/architecture.md
  - backend/src/extraction/chapter_fact_extractor.py
  - backend/src/extraction/prompts/extraction_system.txt
  - backend/src/services/analysis_service.py
  - backend/src/extraction/fact_validator.py
  - backend/src/extraction/context_summary_builder.py
  - backend/src/services/novel_service.py
workflowType: 'feature-architecture'
project_name: 'AI-Reader-V2'
feature_name: 'Entity Pre-scan Dictionary'
user_name: 'leonfeng'
date: '2026-02-13'
---

# 实体预扫描词典 — 特性架构决策文档

_本文档描述在 AI-Reader-V2 分析流水线中新增"实体预扫描词典"模块的架构设计。_

---

## 1. 问题分析

### 1.1 核心痛点

| # | 痛点 | 影响 |
|---|------|------|
| 1 | LLM 逐章分析缺乏全局实体视角 | 前几章 context summary 为空，LLM 首次遇到冷门人名/地名容易遗漏 |
| 2 | 实体名称跨章节不一致 | 同一角色多个称呼（孙悟空/行者/齐天大圣），LLM 在不同章节使用不同主名，聚合层难以合并 |
| 3 | 无法利用全书统计信息 | 出现 8000 次的角色名和出现 1 次的角色名，在 LLM 提取时享有相同关注度 |
| 4 | 古典文言文小说提取难度远高于现代网文 | 人名与常用词重叠（"行者"）、称呼高度多变、地名古雅不在现代词库中 |

### 1.2 预扫描可利用的数据信号

| 优先级 | 信号 | 原理 | 价值 |
|--------|------|------|------|
| **P0** | 全书词频统计 (jieba + n-gram) | 角色名/地名高频重复出现，频率本身是强信号 | 高，覆盖面最广 |
| **P0** | 对话归属模式 | "X道/说/曰" — X 几乎必定是人名 | 高，准确率极高 |
| **P1** | 章节标题词提取 | 章回体标题必含核心实体 | 中高，低成本锚点 |
| **P1** | 后缀模式匹配 | "XX山/洞/宫/派" 暗示实体类型 | 中，同时提取实体和类型 |
| **P2** | 共现分析 | 同段落共现的高频词往往有关联 | 低到中，后续迭代 |
| **P2** | 首章世界观扫描 | 前几章集中介绍世界设定 | 中高，已有 world_declarations 覆盖 |

---

## 2. 方案选型

评估了三种方案：

| 维度 | 方案 A（纯统计） | **方案 B（统计+LLM）** | 方案 C（深度扫描） |
|------|-----------------|----------------------|------------------|
| LLM 调用次数 | 0 | **1** | 5-8 |
| 额外成本 | ¥0 | **¥0.01-0.02** | ¥0.1-0.3 |
| 耗时 | < 10s | **20-40s** | 2-5min |
| 实体识别覆盖率 | ~70% | **~90%** | ~95% |
| 别名关联能力 | 弱 | **中** | 强 |
| 实现复杂度 | 低 | **中** | 高 |

**选定方案 B：统计预扫描 + LLM 单次分类。**

理由：
1. Phase 1 统计扫描与方案 A 一致，可独立发挥价值
2. Phase 2 仅增加 1 次 LLM 调用（成本可忽略），分类准确率显著提升至 ~90%
3. 架构预留方案 C 的扩展空间（Phase 3/4 可作为后续迭代）
4. 对古典和现代小说都有效

---

## 3. 数据模型

### 3.1 新增表：entity_dictionary

```sql
CREATE TABLE entity_dictionary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,           -- 实体名称
    entity_type     TEXT,                    -- person/location/item/org/concept/unknown
    frequency       INTEGER DEFAULT 0,       -- 全书出现频次
    confidence      TEXT DEFAULT 'medium',   -- high/medium/low
    aliases         TEXT DEFAULT '[]',       -- JSON 数组: ["行者","齐天大圣"]
    source          TEXT NOT NULL,           -- 发现来源: freq/dialogue/title/suffix/llm
    sample_context  TEXT,                    -- 一段典型上下文（≤50字）
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(novel_id, name)
);

CREATE INDEX idx_entity_dict_novel ON entity_dictionary(novel_id, entity_type);
```

### 3.2 扩展列：novels.prescan_status

```sql
ALTER TABLE novels ADD COLUMN prescan_status TEXT DEFAULT 'pending';
-- 值域: pending / running / completed / failed
```

### 3.3 Pydantic 模型

```python
class EntityDictEntry(BaseModel):
    name: str
    entity_type: str = "unknown"   # person/location/item/org/concept/unknown
    frequency: int = 0
    confidence: str = "medium"     # high/medium/low
    aliases: list[str] = []
    source: str                    # freq/dialogue/title/suffix/llm
    sample_context: str | None = None
```

### 3.4 设计决策

- **独立表而非 JSON 字段**：需按 entity_type 筛选、按 frequency 排序，后续支持用户手动修正
- **不归属 chapter_facts 体系**：预扫描词典是全书级数据，不属于任何章节，生命周期独立

---

## 4. 算法流程

### 4.1 总体流程

```
novels.prescan_status = 'running'
         │
         ▼
  ┌──────────────────────────────────┐
  │  Phase 1: 统计扫描 (CPU-only)     │
  │                                    │
  │  1a. 加载全部章节文本               │
  │  1b. jieba 分词 + 词频统计          │
  │  1c. 2~4 字 n-gram 频率统计         │
  │  1d. 对话归属正则提取人名            │
  │  1e. 章节标题词提取                  │
  │  1f. 后缀模式匹配 + 类型推断         │
  │  1g. 合并去重 → 候选列表             │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Phase 2: LLM 分类 (1 次调用)     │
  │                                    │
  │  2a. 筛选 Top-300 候选              │
  │  2b. 每个候选附 1 段上下文(≤50字)   │
  │  2c. 构造分类 prompt                │
  │  2d. LLM 返回分类 + 别名关联        │
  │  2e. 合并 LLM 结果到候选列表         │
  └──────────────┬───────────────────┘
                 │
                 ▼
  写入 entity_dictionary 表
  novels.prescan_status = 'completed'
```

### 4.2 Phase 1 详细设计

#### 4.2.1 jieba 分词 + 词频统计

```python
import jieba.posseg as pseg
from collections import Counter

def scan_word_freq(chapters: list[str]) -> Counter:
    full_text = "\n".join(chapters)
    words = pseg.cut(full_text)
    counter = Counter()
    for word, flag in words:
        if len(word) < 2 or len(word) > 8:
            continue
        # 保留名词类：nr(人名) ns(地名) nz(专名) n(名词)
        if flag.startswith(('nr', 'ns', 'nz', 'n')):
            counter[word] += 1
    return counter
```

#### 4.2.2 n-gram 补充

jieba 可能把"东胜神洲"切成"东胜"+"神洲"。2-4 字 n-gram 统计可发现完整词组：

- 只保留频次 ≥ 3 的 n-gram
- 过滤纯标点/数字/停用词组合
- 与 jieba 词频合并

#### 4.2.3 对话归属正则

```python
_DIALOGUE_PATTERN = re.compile(
    r'[""「]([^""」]{1,200})[""」][，,。]?\s*'
    r'([\u4e00-\u9fff]{1,8})\s*'
    r'(?:道|说|曰|笑道|叫道|问道|答道|喝道|叹道|骂道|叫|喊道|怒道|惊道|忙道|急道|冷笑道)'
)
```

发现的人名置信度为 high，来源标记为 dialogue。

#### 4.2.4 章节标题词提取

从 `chapters.title` 字段提取 2-6 字的中文词组。章回体标题中的词几乎必定是核心实体。

#### 4.2.5 后缀模式匹配

```python
_SUFFIX_RULES = {
    "location": ["山","洞","洲","国","城","宫","殿","府","寺","庙","观","院",
                  "阁","楼","塔","谷","崖","峰","岭","河","海","湖","泊","关",
                  "门","桥","镇","村","庄","寨","营","港"],
    "org":      ["派","门","宗","帮","会","盟","教","族","军","营","卫","堂","阁"],
    "item":     ["丹","药","剑","刀","枪","珠","鼎","炉","符","阵","经","诀",
                  "功","术","法"],
    "person":   ["真人","道人","仙人","大师","长老","掌门","圣人","大王",
                  "将军","元帅","太子","公主","娘娘"],
}
```

#### 4.2.6 候选合并

多来源发现同一实体时的合并策略：
- 同名词条合并频次，取最高置信度来源
- 来源优先级：dialogue > title > suffix > freq

### 4.3 Phase 2 详细设计

#### LLM 分类 Prompt

```
你是一个小说实体分类专家。以下是从一部小说中自动提取的高频专有名词候选列表。
请对每个候选词进行分类，并识别可能的别名关系。

## 候选列表
| 词 | 出现频次 | 来源 | 上下文示例 |
|---|---------|------|----------|
| 韩立 | 8234 | dialogue | "韩立道：'师兄请便。'" |
| 七玄门 | 456 | suffix | "七玄门位于太南山脉" |
...

## 输出要求
输出 JSON:
{
  "entities": [
    {"name": "韩立", "type": "person", "confidence": "high", "aliases": []},
    ...
  ],
  "alias_groups": [
    ["孙悟空", "行者", "齐天大圣", "猴王", "石猴"],
    ...
  ],
  "rejected": ["然后", "不过"]
}
```

候选数量控制：Top-300，每条附 ≤50 字上下文，总输入 ≈ 3000-5000 token。

---

## 5. 集成设计

### 5.1 触发时机

```
用户上传 → parse_upload() → 预览 → confirm_import()
                                         │
                                         ▼ (新增)
                              asyncio.create_task(prescan.scan(novel_id))
                                         │
用户点击"开始分析" → AnalysisService.start()
                         │
                         ▼ (新增)
              检查 prescan_status:
                pending  → 同步触发预扫描
                running  → 等待完成(timeout=120s)
                failed   → 降级为无词典模式
                completed → 加载词典
                         │
              逐章循环 → ContextSummaryBuilder.build()
                              │ (新增)
                              ▼
                         注入词典摘要到 context
                              │
                         ChapterFactExtractor.extract()
                              │
                         ...后续不变...
```

### 5.2 Context 注入格式

```markdown
## 本书高频实体参考
以下实体在全书中高频出现，提取时请特别注意不要遗漏（仅供参考，仍以原文为准）：
- 韩立（person，出现8234次）
- 七玄门（org，出现456次） 别名：七玄派
- 墨大夫（person，出现312次）
- 太南小会（event，出现89次）
...
```

Token 预算：Top-100 实体 × 30 字/条 ≈ 3000 字 ≈ 2000 token。在 context summary 的 18K char 预算内。

### 5.3 API

```
POST /api/novels/{novel_id}/prescan           → 手动触发预扫描
GET  /api/novels/{novel_id}/prescan           → 查询预扫描状态
GET  /api/novels/{novel_id}/entity-dictionary → 获取词典内容 (?type=person&limit=50)
```

---

## 6. 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新建** | `backend/src/extraction/entity_pre_scanner.py` | 核心模块：Phase 1 统计扫描 + Phase 2 LLM 分类 |
| **新建** | `backend/src/extraction/prescan_prompts.py` | LLM 分类 prompt 模板 |
| **新建** | `backend/src/db/entity_dictionary_store.py` | 词典表 CRUD |
| **新建** | `backend/src/api/routes/prescan.py` | 预扫描 API 路由 |
| **修改** | `backend/src/db/sqlite_db.py` | 新增 entity_dictionary 表 + novels.prescan_status 列 |
| **修改** | `backend/src/services/novel_service.py` | confirm_import() 后触发预扫描 |
| **修改** | `backend/src/services/analysis_service.py` | start() 检查预扫描状态 + 加载词典 |
| **修改** | `backend/src/extraction/context_summary_builder.py` | build() 注入词典摘要 |
| **修改** | `backend/src/api/main.py` | 注册 prescan 路由 |
| **修改** | `backend/pyproject.toml` | 新增 jieba 依赖 |

---

## 7. 验证

### 7.1 需求覆盖

| 痛点 | 解决方式 | 状态 |
|------|---------|------|
| LLM 缺乏全局视角 | 词典注入 context | ✅ |
| 实体名称不一致 | Phase 2 LLM 识别 alias_groups | ✅ |
| 无法利用全书统计 | Phase 1 全书词频 + 对话归属 + 后缀匹配 | ✅ |
| 古典文言文难度高 | n-gram 补偿 + 后缀规则覆盖古典地名 | ⚠️ 部分（需实测） |
| 前几章 context 为空 | 词典从第 1 章开始可用 | ✅ |

### 7.2 风险评估

| 风险 | 等级 | 缓解策略 |
|------|------|---------|
| jieba 对古典文本分词质量差 | 中 | n-gram 补充 + 后续支持自定义词典 |
| LLM 分类调用失败 | 低 | 降级为仅 Phase 1 统计结果 |
| 词典注入占用过多 context token | 低 | 硬限 Top-100 实体，≤ 2000 token |
| 预扫描与分析并发竞争 | 低 | 导入后自动触发，分析启动前检查 |
| 误判非实体词为实体 | 中 | Phase 2 LLM rejected 列表 + 停用词过滤 |
| 词典导致 LLM 过度依赖参考列表 | 低 | prompt 标注"仅供参考，仍以原文为准" |

### 7.3 降级策略

预扫描为增强层，任何环节失败不影响核心分析：

- `prescan_status == "failed"` → 分析正常启动，无词典注入
- `prescan_status == "pending"` + 用户直接分析 → 同步触发预扫描，超时 120s 后降级
- Phase 2 LLM 失败 → 使用 Phase 1 纯统计结果

### 7.4 增量更新

| 场景 | 处理方式 |
|------|---------|
| 重新分析 (force=true) | 不重新预扫描（原文未变，词典仍有效） |
| 清除分析数据 | 保留词典（词典不属于分析结果） |
| 小说删除 | 级联删除词典（ON DELETE CASCADE） |

---

## 8. 性能估算

| 指标 | 估算 | 依据 |
|------|------|------|
| Phase 1 耗时 | 5-15 秒 | 100 万字 jieba ~5s + 正则 ~3s + n-gram ~5s |
| Phase 2 耗时 | 10-30 秒 | DeepSeek 单次调用 3000-5000 token |
| 总耗时 | 15-45 秒 | Phase 1 + Phase 2 |
| LLM 成本 | ¥0.01-0.02 | DeepSeek-chat 价格 |
| 词典大小 | 100-500 条 | 依小说体量和类型而异 |
| 内存占用 | < 50 MB | jieba 词典 ~30MB + 全书文本 ~5MB |
| 词典注入增加的 token | ~2000 | Top-100 实体 × 30 字/条 |

---

## 9. 实施阶段

### Phase 1（本次实现）

1. DB 迁移：entity_dictionary 表 + novels.prescan_status 列
2. entity_pre_scanner.py：统计扫描 + LLM 分类
3. entity_dictionary_store.py：词典 CRUD
4. context_summary_builder.py：注入词典
5. analysis_service.py：启动前检查预扫描
6. novel_service.py：导入后触发预扫描
7. prescan API 路由
8. 端到端验证：西游记第 1 回对比（有/无词典）

### Phase 2（后续迭代）

1. 前端词典查看页面
2. 前端预扫描进度展示
3. 用户手动修正词典条目 (PATCH API)
4. 自定义 jieba 用户词典加载
5. 共现分析增强

---

*架构决策完成。下一步：创建 Epic 和 Story。*
