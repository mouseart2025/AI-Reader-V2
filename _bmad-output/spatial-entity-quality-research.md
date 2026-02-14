# 空间实体提取质量研究 — 从穷举过滤到结构性验证

> 版本: 1.0 | 日期: 2026-02-14
> 状态: ready-for-dev

## 1. 问题陈述

AI Reader V2 的地图功能从小说文本中提取地点实体和空间关系，构建世界地图。
当前系统存在大量**假阳性**——非地名的词被错误提取为地点实体，导致地图上充满噪声，
空间关系混乱，用户体验严重受损。

### 1.1 错误分类

基于凡人修仙传 Ch2-Ch5 的实测，假阳性分为 5 类：

| 类型 | 示例 | 根因 |
|------|------|------|
| **相对位置词** | 山上、村外、场中、门口、山外、场外、城中 | 方位后缀被误认为地名的一部分 |
| **无名泛指** | 村子、小城、镇子、石屋、最高的屋子、水面 | 无专名的通名或描述性短语 |
| **概念域** | 江湖、世俗界、自己的地界、天下、人间 | 抽象概念而非物理地点 |
| **描述性短语** | 两股势力的交接边缘处、对面人群中、七玄门这边 | 叙述性描述，不是实体名 |
| **物品/载具** | 小舟 | 非地点类实体 |

人物提取也有类似问题：

| 类型 | 示例 | 根因 |
|------|------|------|
| **泛称代词** | 众人、其他人、旁人、对方、大家 | LLM 未遵守"不提取泛称"规则 |
| **截断称呼** | 愣子（应为二愣子）、胖子（应为韩胖子） | LLM 截断了完整称呼 |
| **纯职务** | 堂主、长老 | 无姓氏的职务头衔 |
| **重名歧义** | 石屋（多地点可能都有石屋） | 无上级归属的通用建筑名 |

### 1.2 当前方法的局限性

当前采用**穷举黑名单**方式：在 `fact_validator.py` 中维护 `_GENERIC_GEO_WORDS`（单字）
和 `_GENERIC_GEO_PHRASES`（多字）两个 frozenset。

缺陷：
- **不可持续**：每发现一个新假阳性就需要手动添加，永远追不上 LLM 的"创造力"
- **无结构性**：不理解中文地名的形态学规律，逐词匹配而非模式匹配
- **无上下文感知**：无法判断"小城"在特定上下文中指代"青牛镇"
- **无角色区分**：所有提取的地点在地图上权重相同，不区分"场景"和"提及"

---

## 2. 研究基础

### 2.1 中文地名形态学

中文地名遵循 **专名 + 通名** 结构（Harvard CHGIS、Wikipedia "Place names in China"）：

```
花果山 = 花果(专名/specific) + 山(通名/generic)   ✓ 地名
青牛镇 = 青牛(专名) + 镇(通名)                    ✓ 地名
水帘洞 = 水帘(专名) + 洞(通名)                    ✓ 地名
灵霄宝殿 = 灵霄宝(专名) + 殿(通名)               ✓ 地名

山上  = ∅(无专名) + 山(通名) + 上(方位后缀)        ✗ 相对位置
村子  = ∅(无专名) + 村(通名) + 子(虚化后缀)        ✗ 无名泛指
小城  = 小(形容词) + 城(通名)                      ✗ 无名泛指
石屋  = 石(材质修饰) + 屋(通名)                    ✗ 无名泛指（除非上下文明确唯一）
```

**关键判别规则**：一个合法地名必须有**非通名的专名部分**（≥1 个语义上特指某地的字符）。

### 2.2 通名体系

| 类别 | 通名字符 |
|------|----------|
| 行政区划 | 省、市、县、镇、村、州、府、国、邦 |
| 自然地理 - 山 | 山、峰、岭、崖、谷、坡 |
| 自然地理 - 水 | 河、江、湖、海、溪、泉、潭、洋 |
| 自然地理 - 林 | 林、森、丛 |
| 建筑/设施 | 城、楼、殿、宫、庙、寺、塔、洞、关、门、桥、台、阁、堂、院、府、庄、园 |
| 修仙/奇幻 | 界、域、洲、宗、派、教 |
| 地形 | 原、地、坪、滩、沙、漠、岛 |

### 2.3 方位后缀体系

当通名后面紧跟方位后缀时，整体表达的是**相对位置**而非地名：

| 方位后缀 | 含义 | 示例 |
|----------|------|------|
| 上、下 | 垂直方位 | 山上、楼下 |
| 里、内、中 | 内部 | 村里、城内、场中 |
| 外 | 外部 | 村外、城外、山外 |
| 前、后 | 前后方位 | 门前、院后 |
| 边、旁、畔 | 侧方 | 河边、湖畔、路旁 |
| 口 | 入口处 | 门口、洞口、路口 |
| 头、脚、顶 | 极端位置 | 山头、山脚、山顶 |

**例外**：当方位后缀前有专名时，整体可能是地名（如"城东"通常不是，但"关东"可能是）。

### 2.4 学术研究参考

| 研究方向 | 关键文献 | 核心发现 |
|----------|----------|----------|
| 中文小说 NER | [arXiv:2311.15509](https://arxiv.org/abs/2311.15509) — 263K 实体，260 部小说，13 种类型 | 地点实体模式因小说类型差异极大；标注准则要求"必须指代小说中的具体实体" |
| 空间角色标注 | [SemEval-2013 Task 3](https://experts.arizona.edu/en/publications/semeval-2013-task-3-spatial-role-labeling) — Spatial Role Labeling | 三要素：Trajector（被定位物）、Landmark（参照物）、Spatial Indicator（空间信号词） |
| 空间标注模式 | [SpatialML](https://aclanthology.org/L08-1017/)、ISO-Space | 地名接地 + 拓扑关系 + 运动事件的标准化标注体系 |
| 叙事场景检测 | [Zehe et al., EACL 2021](https://aclanthology.org/2021.eacl-main.276/) | 场景 = 时空连续 + 角色不变；BERT 场景分割 F1 仅 24%（仍是开放问题） |
| 事件-地点追踪 | [EMNLP 2023](https://aclanthology.org/2023.emnlp-main.544.pdf) | 维护"位置状态"（location state）贯穿叙事，直到明确切换 |
| 非命名空间实体 | [CEUR-WS Vol-3834](https://ceur-ws.org/Vol-3834/paper59.pdf) | 文学文本中非专名空间实体分类：interior/natural/rural/urban |
| 指代消解 | [Jurafsky & Martin Ch.26](https://web.stanford.edu/~jurafsky/slp3/26.pdf) | 文学语料 83% 指代消解标注集中在人物；地点指代消解严重不足 |
| 长文档指代 | [FantasyCoref, CRAC 2021](https://aclanthology.org/2021.crac-1.3/) | 童话故事 211 篇标注，文档长度是 OntoNotes 的 2.5 倍；长文档指代消解是开放挑战 |

### 2.5 核心洞察

1. **形态学是第一道防线**：中文地名的"专名+通名"结构提供了确定性判别规则，
   不需要 LLM 参与就能过滤大部分假阳性
2. **指代消解需要上下文**：LLM 是目前最实用的指代消解工具，但需要注入已知地点列表
   才能有效将"小城"→"青牛镇"
3. **角色区分是地图质量的关键**：区分"场景地点"和"提及地点"能大幅减少地图噪声
4. **场景检测仍是开放问题**：学术界尚无成熟方案，但 LLM + 结构化提取已是最实用的近似

---

## 3. 解决方案：三层防御体系

### 3.1 第 1 层：形态学验证（FactValidator，确定性过滤）

**原理**：用规则替代穷举黑名单，基于中文地名结构规律进行模式匹配。

**判别决策树**：

```
输入: 提取的地点名 name
  │
  ├─ len(name) == 1 且 name ∈ 通名集合?
  │    └─ YES → 过滤（单字通名，如"山""河"）
  │
  ├─ name ∈ 硬编码概念词?（江湖、天下、世界、人间、凡间...）
  │    └─ YES → 过滤（概念域）
  │
  ├─ name 包含"的"?
  │    └─ YES → 过滤（描述性短语，如"自己的地界""最高的屋子"）
  │
  ├─ len(name) > 7?
  │    └─ YES → 过滤（过长的描述性短语）
  │
  ├─ name 匹配 [通名/泛修饰] + [方位后缀] 模式?
  │    └─ YES → 过滤（相对位置，如"山上""村外""门口"）
  │
  ├─ name 匹配 [泛修饰] + [通名] 模式?（小城、大山、这个村子...）
  │    └─ YES → 过滤（无名泛指）
  │
  ├─ name ∈ 载具/物品词?（小舟、马车、轿子...）
  │    └─ YES → 过滤（非地点）
  │
  └─ 通过 → 保留
```

**泛修饰词**：小、大、一个、那个、这个、某、老、新、旧

**实现方式**：在 `fact_validator.py` 的 `_validate_locations()` 中，
用上述规则替代（或补充）现有的 `_GENERIC_GEO_WORDS` 和 `_GENERIC_GEO_PHRASES` 黑名单。
黑名单作为最后兜底，规则作为主要过滤器。

### 3.2 第 2 层：已知地点注入（Prompt，指代消解）

**原理**：将前序章节累积的所有**已通过验证的具体地名**注入 extraction prompt，
使 LLM 能将泛指（"小城""那座山""此地"）解析为已知地名。

**注入格式**（追加到 extraction_system.txt 的 `{context}` 部分之后）：

```
## 已知地点（用于指代消解）
本章中如果出现"小城""那座山""此地"等泛称，若上下文明确指代以下某个已知地名，
请直接使用该地名，不要将泛称作为独立地点提取。

| 地名 | 类型 | 上级 |
|------|------|------|
| 青牛镇 | 镇 | — |
| 七玄门 | 门派 | 彩霞山 |
| 彩霞山 | 山 | 镜州 |
| 清客院 | 建筑 | 七玄门 |
| 炼骨崖 | 地点 | 七玄门 |
```

**数据来源**：从 `chapter_facts` 表中聚合所有 `chapter_id < current_chapter` 的
LocationFact.name（去重后），附带 type 和 parent。

**实现位置**：`chapter_fact_extractor.py` 的 `_build_user_prompt()` 方法。

**Token 预算控制**：限制注入地点数量（如 ≤100 个），按 mention_count 降序排列，
优先注入高频地点。

### 3.3 第 3 层：地点角色标注（LocationFact schema 扩展）

**原理**：给每个提取的地点标注其在本章叙事中的**角色**，使地图能区分权重。

**新增字段**：`LocationFact.role`

| role 值 | 含义 | 地图表现 |
|---------|------|----------|
| `setting` | 叙事实际发生在此地（人物在这里活动） | 正常显示，高权重 |
| `referenced` | 被提及但叙事不在此处（回忆、讨论、计划前往） | 半透明或虚线边框 |
| `origin` | 人物来历背景（"他是XX人"） | 仅在人物卡片中显示 |

**数据模型变更**：

```python
# models/chapter_fact.py
class LocationFact(BaseModel):
    name: str
    type: str = ""
    parent: str | None = None
    description: str = ""
    role: str = "setting"  # "setting" | "referenced" | "origin"
```

**prompt 变更**（extraction_system.txt 地点规则新增）：

```
11. role 字段标注地点角色：
    - "setting"：本章叙事实际发生在该地点（角色在此处活动、对话、战斗）
    - "referenced"：本章提及但叙事不在此处（被回忆、讨论、计划前往、历史介绍）
    - "origin"：作为人物来历背景提及（"他是XX人""来自XX"）
```

**前端变更**：`NovelMap.tsx` 根据 role 调整地点图标透明度或样式。

---

## 4. 人物提取质量改进

### 4.1 完整称呼规则

**问题**：LLM 截断称呼（"愣子"←"二愣子"，"胖子"←"韩胖子"）。

**解决**：prompt 中已添加规则 6（使用完整称呼）。validator 中可增加启发式检查：
如果提取的人名是某个已知 alias 的后缀子串，尝试用完整 alias 替换。

### 4.2 泛称/职务过滤

**已实现**：`_GENERIC_PERSON_WORDS` 黑名单过滤"众人""其他人"等。

**补充规则**：纯职务词（"堂主""长老""弟子""护法""掌门"）在无姓氏前缀时过滤。
实现为后缀匹配：如果 name 全由职务词组成且无姓氏字符，则过滤。

---

## 5. 实施计划

| 优先级 | 层 | 任务 | 影响文件 | 影响范围 |
|--------|---|------|----------|----------|
| P0 | 1 | 形态学验证替代黑名单 | `fact_validator.py` | 所有新提取立即生效 |
| P0 | 2 | 已知地点注入 prompt | `chapter_fact_extractor.py`, `extraction_system.txt` | 所有新提取 |
| P1 | 3 | LocationFact.role 字段 | `chapter_fact.py`, `extraction_system.txt`, `NovelMap.tsx` | 需要数据模型变更 |
| P1 | — | 人物职务词过滤 | `fact_validator.py` | 所有新提取 |
| P2 | — | 地点别名合并 | `alias_resolver.py` | 需要扩展 Union-Find 到地点 |

### 5.1 生效条件

- 第 1 层（形态学验证）：**重启后端**后对所有新提取的 ChapterFact 生效
- 第 2 层（已知地点注入）：**重新分析**受影响章节后生效
- 第 3 层（角色标注）：需要**数据模型迁移 + 重新分析**
- 已有数据：需要对已分析的小说执行 force 重新分析

### 5.2 验证方法

1. 对凡人修仙传 Ch2-5 执行 force 重新分析
2. 检查 ChapterFact 中不再包含：山上、村外、门口、小城、镇子、江湖、众人等
3. 检查"小城"是否被正确解析为"青牛镇"（第 2 层）
4. 检查地图上地点数量是否明显减少、空间关系是否更合理

---

## 6. 参考文献

- [A Corpus for NER in Chinese Novels with Multi-genres](https://arxiv.org/abs/2311.15509) — 263K 实体标注
- [SpatialML: Annotation Scheme for Spatial Expressions](https://aclanthology.org/L08-1017/)
- [SemEval-2013 Task 3: Spatial Role Labeling](https://experts.arizona.edu/en/publications/semeval-2013-task-3-spatial-role-labeling)
- [Detecting Scenes in Fiction (EACL 2021)](https://aclanthology.org/2021.eacl-main.276/)
- [Event-Location Tracking in Narratives (EMNLP 2023)](https://aclanthology.org/2023.emnlp-main.544.pdf)
- [Non-Named Spatial Entities in Literary Text (CEUR-WS 2024)](https://ceur-ws.org/Vol-3834/paper59.pdf)
- [Coreference Resolution (Jurafsky & Martin Ch.26)](https://web.stanford.edu/~jurafsky/slp3/26.pdf)
- [FantasyCoref (CRAC 2021)](https://aclanthology.org/2021.crac-1.3/)
- [Chinese Place Names (Harvard CHGIS)](https://chgis.fas.harvard.edu/data/skinner/metadata/ChinesePlaceNames.htm)
- [Place Names in China (Wikipedia)](https://en.wikipedia.org/wiki/Place_names_in_China)
- [RB-TRNet: Toponym Recognition from Chinese Text](https://www.tandfonline.com/doi/full/10.1080/10095020.2024.2440079)
