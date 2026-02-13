# AI-Reader-V2 世界地图 V2 架构设计

> 作者: Winston (Architect Agent)
> 日期: 2026-02-13
> 状态: 草案 v2 — 采纳渐进式代理方案

---

## 1. 问题陈述

当前的世界地图功能存在三个根本性限制：

### 1.1 平面化思维 — 缺乏宏观世界观

当前系统把所有地点（279个，以西游记为例）扔进一个平面约束求解器，像在白纸上撒豆子。但小说世界有**清晰的宏观结构**：

> "世界之间遂分为四大部洲：曰东胜神洲、曰西牛贺洲、曰南赡部洲、曰北俱芦洲。"

这定义了一个 **2x2 的大陆网格**。花果山不是"世界上的一个点"，而是"东胜神洲→傲来国→海中岛屿→花果山"这条层级路径的末端。

### 1.2 缺少副本/子世界概念

奇幻小说充满了**非平面空间**：
- 洞府（水帘洞、盘丝洞）— 从山上的洞口进入独立空间
- 天界（天宫、灵霄宝殿）— 垂直于地面的"上层"
- 冥界（地府、阴司）— "下层"
- 幻境/阵法空间 — 临时出现的口袋维度
- 海底宫殿（龙宫）— 水下空间

这些类似于 **游戏的副本（Instance）**：从世界地图某个入口进入独立的小地图。

### 1.3 约束求解器的力不从心

用单一的 `differential_evolution` 处理 279 个地点：
- LLM 方向信息质量不稳定（北/南偏多，东/西偏少）
- 所有地点在同一平面竞争空间 → 布局拥挤
- 无法表达"进入"、"传送"等空间转换
- 计算量大，效果仍然有限

---

## 2. 参考系统研究

### 2.1 魔兽世界 (WoW) 地图层级

WoW 使用 **7 级类型化层级**：

| 层级 | UIMapType | 示例 | 类比（小说） |
|------|-----------|------|-------------|
| 0 | Cosmic | 宇宙视图 | 世界观总览 |
| 1 | World | 艾泽拉斯星球 | 整个小说世界 |
| 2 | Continent | 卡利姆多 | 东胜神洲 |
| 3 | Zone | 杜隆塔尔 | 傲来国 |
| 4 | Dungeon | 黑石深渊 | 水帘洞 |
| 5 | Micro | 小型子区域 | 洞中某个厅堂 |
| 6 | Orphan | 战场/特殊空间 | 幻境、梦境 |

**关键设计**：WoW 区分 `ParentMapID`（空间上的物理父级 — 副本入口在哪个区域）和 `CosmeticParentMapID`（主题上的归属 — 副本在地图树中归类到哪里）。

### 2.2 原神 (Genshin) 层级模型

原神使用 **Tab 式多层地图**：

```
提瓦特大陆（主层）
  ├── 蒙德
  ├── 璃月
  ├── ...
渊下宫（地下层 — 独立 Tab）
层岩巨渊·地下矿区（地下层）
尘歌壶（副本层 — 仅在内部时可见）
```

**关键设计**：某些空间不是"大陆的子区域"，而是**平行层**，通过 Tab 切换。通过剧情解锁新 Tab（= 通过章节阅读解锁新地图层）。

### 2.3 Azgaar 幻想地图生成器

使用 **Voronoi 单元 + 邻接图** 作为空间基础，上面叠加多个 overlay 层（政治/文化/宗教/地形）。

**关键设计**：邻接图（哪些地点相邻）比坐标更重要。小说中精确坐标不存在，但邻接关系可以从文本提取。

### 2.4 PlotMap (Autodesk AI Research)

验证了 **约束满足 + 优化求解** 是生成故事地图的合理方法。定义了 12 种空间约束类型，我们已实现其中 5 种。

### 2.5 核心启示

| 启示 | 来源 | 对我们的意义 |
|------|------|-------------|
| 类型化层级 > 纯数字 level | WoW | 用 `layer_type` 替代 `level: int` |
| 平行层 > 嵌套子树 | 原神 | 天界/冥界是平行层，不是地理子区域 |
| 空间父级 ≠ 叙事父级 | WoW | 水帘洞空间上在花果山，叙事上属于孙悟空的据点 |
| 邻接图 > 坐标 | Azgaar | 先建邻接关系，再算坐标 |
| 分层求解 | 全部 | 先布局大陆，再布局大陆内的区域 |

---

## 3. 概念模型：多层级世界地图

### 3.1 核心概念

```
WorldMap（世界）
  ├── MapLayer（地图层）— 一个独立的可渲染地图平面
  │     ├── Region（区域）— 层内的大范围区域
  │     │     └── Location（地点）— 具体地点
  │     └── Portal（传送门）— 连接到其他层的入口
  └── MapLayer ...
```

**三个核心实体**：

1. **MapLayer（地图层）**：一个独立的二维空间。每个层有自己的坐标系统和布局。世界可以有多个层。
2. **Region（区域）**：层内的宏观区域划分。对应 WoW 的 Continent/Zone 概念。区域有明确的边界和方位关系。
3. **Portal（传送门）**：连接两个层的命名通道。如"南天门"连接地面层和天界层。

### 3.2 层类型

```python
class LayerType(str, Enum):
    OVERWORLD = "overworld"       # 主世界/地面 — 小说的主要地理空间
    CELESTIAL = "celestial"       # 天界/仙界
    UNDERWORLD = "underworld"     # 冥界/地府
    UNDERWATER = "underwater"     # 海底（如龙宫）
    INSTANCE = "instance"         # 副本/洞府/独立空间
    POCKET = "pocket"             # 口袋维度（幻境、阵法空间、梦境）
```

### 3.3 区域层级（以西游记为例）

```
世界 (WorldMap)
│
├── 主世界层 (overworld)
│   ├── 东胜神洲 (Region, 方位=东)
│   │   ├── 傲来国 (Sub-region)
│   │   │   └── 花果山 (Location) → Portal: 水帘洞入口
│   │   ├── 南赡部洲 (Sub-region, 方位=南)
│   │   │   └── 长安城 (Location)
│   │   └── ...
│   ├── 西牛贺洲 (Region, 方位=西)
│   │   ├── 灵山 (Location) — 旅程终点
│   │   ├── 火焰山 (Location)
│   │   ├── 西梁女国 (Sub-region)
│   │   └── ...
│   ├── 南赡部洲 (Region, 方位=南)
│   ├── 北俱芦洲 (Region, 方位=北)
│   ├── 东洋大海 (Region, type=海域)
│   ├── 西海 (Region, type=海域)
│   └── 南海 (Region, type=海域)
│
├── 天界层 (celestial)
│   ├── 凌霄宝殿
│   ├── 蟠桃园
│   ├── 兜率宫
│   └── ...
│   Portal: 南天门 ←→ 主世界
│
├── 冥界层 (underworld)
│   ├── 森罗殿
│   ├── 枉死城
│   └── ...
│   Portal: 幽冥界入口 ←→ 主世界
│
├── 水帘洞 (instance)
│   Portal: 水帘洞瀑布 ←→ 花果山
│
├── 东海龙宫 (instance, underwater)
│   Portal: 东海海底 ←→ 东洋大海
│
└── 盘丝洞 (instance)
    Portal: 盘丝洞口 ←→ 主世界某处
```

---

## 4. 数据模型设计

### 4.1 新增 Pydantic 模型

```python
# backend/src/models/world_map.py

class LayerType(str, Enum):
    OVERWORLD = "overworld"
    CELESTIAL = "celestial"
    UNDERWORLD = "underworld"
    UNDERWATER = "underwater"
    INSTANCE = "instance"
    POCKET = "pocket"

class WorldRegion(BaseModel):
    """宏观区域 — 对应大陆、大洲、海域等宏观地理单元"""
    name: str                           # "东胜神洲"
    cardinal_direction: str | None       # "east" / "west" / "south" / "north" / None
    region_type: str                     # "continent" / "ocean" / "kingdom" / "wilderness"
    parent_region: str | None = None     # 嵌套区域的父级
    description: str = ""               # LLM 提取的描述

class MapLayer(BaseModel):
    """独立的地图层 — 有自己的坐标系和布局"""
    layer_id: str                       # 唯一标识
    name: str                           # "主世界" / "天界" / "水帘洞"
    layer_type: LayerType
    description: str = ""
    # 层内的区域划分
    regions: list[WorldRegion] = []

class Portal(BaseModel):
    """两个地图层之间的连接通道"""
    name: str                           # "南天门"
    source_layer: str                   # 源层 ID
    source_location: str                # 源层中的地点名
    target_layer: str                   # 目标层 ID
    target_location: str | None = None  # 目标层中的到达地点
    is_bidirectional: bool = True
    first_chapter: int = 0              # 首次出现的章节

class WorldStructure(BaseModel):
    """整个小说的世界结构 — 从前几章提取的宏观信息"""
    novel_id: str
    layers: list[MapLayer]
    portals: list[Portal]
    # 地点到区域的映射
    location_region_map: dict[str, str] = {}    # location_name → region_name
    # 地点到层的映射
    location_layer_map: dict[str, str] = {}     # location_name → layer_id
```

### 4.2 增强 ChapterFact 提取

在现有的 `SpatialRelationship` 之外，新增 **世界结构声明** 提取：

```python
class WorldDeclaration(BaseModel):
    """世界结构声明 — 从文本中提取的宏观世界观信息"""
    declaration_type: str  # "region_division" / "layer_exists" / "portal" / "region_position"
    content: dict          # 具体内容，按类型不同结构不同
    narrative_evidence: str = ""
    confidence: str = "medium"

# 示例：
# declaration_type: "region_division"
# content: {
#   "parent": "世界",
#   "children": ["东胜神洲", "西牛贺洲", "南赡部洲", "北俱芦洲"],
#   "division_basis": "方位"
# }
#
# declaration_type: "portal"
# content: {
#   "portal_name": "南天门",
#   "from_layer": "地面",
#   "to_layer": "天界",
#   "bidirectional": true
# }
```

### 4.3 数据库 Schema 增强

```sql
-- 世界结构缓存（从 ChapterFact 聚合 + LLM 宏观分析）
CREATE TABLE IF NOT EXISTS world_structures (
    novel_id        TEXT PRIMARY KEY REFERENCES novels(id) ON DELETE CASCADE,
    structure_json   TEXT NOT NULL,   -- WorldStructure 序列化
    source_chapters  TEXT NOT NULL,   -- 用于生成结构的章节范围
    created_at       TEXT DEFAULT (datetime('now'))
);

-- 每层独立的布局缓存
CREATE TABLE IF NOT EXISTS layer_layouts (
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    layer_id        TEXT NOT NULL,
    chapter_hash    TEXT NOT NULL,
    layout_json     TEXT NOT NULL,
    layout_mode     TEXT NOT NULL DEFAULT 'hierarchy',
    terrain_path    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (novel_id, layer_id, chapter_hash)
);
```

---

## 5. 渐进式世界结构代理 (Progressive World Structure Agent)

### 5.1 为什么不用后置合成

**已否决的方案**：先完成所有章节的 ChapterFact 提取，再从汇总数据推断世界结构。

**问题**：结构化提取是有损过程。以西游记开篇为例：

> "感盘古开辟三皇治世五帝定伦世界之间遂分为四大部洲：曰东胜神洲曰西牛贺洲曰南赡部洲曰北俱芦洲。"

经过 `ChapterFactExtractor` 后，这段文本变成：

```python
LocationFact(name="东胜神洲", type="洲", parent=None)
LocationFact(name="西牛贺洲", type="洲", parent=None)
LocationFact(name="南赡部洲", type="洲", parent=None)
LocationFact(name="北俱芦洲", type="洲", parent=None)
```

关键的宏观结构信息已经丢失：
- "世界分为四大部洲" — 四个洲构成世界的一级划分
- "东/西/南/北" — 方位名蕴含的空间布局信息
- 暗示的世界地图结构：一个中心，四方各有一洲

再如 "海外有一国土名曰傲来国。国近大海海中有一座山唤为花果山"，提取后变成：

```python
LocationFact(name="傲来国", type="国")
LocationFact(name="花果山", type="山", parent="傲来国")
SpatialRelationship(source="傲来国", target="大海", relation_type="adjacent")
```

"海外"、"海中有一座山" 这些关键地理描述被压缩成了一个 `adjacent` 关系，后置合成时 LLM 无法从 279 个地点 + 269 条空间关系中还原这些结构性理解。

### 5.2 渐进式代理方案

核心思路：**在章节分析过程中，部署一个专门的 World Structure Agent，它阅读原文、识别世界观信号、逐步构建并完善 WorldStructure。**

```
┌───────────────────────────────────────────────────────────────┐
│                    analysis_service._run_loop_inner()          │
│                                                               │
│  for chapter_num in range(start, end+1):                      │
│    │                                                          │
│    ├── context = context_builder.build(novel_id, chapter_num) │
│    │   └── 包含当前 WorldStructure 摘要（新增）                  │
│    │                                                          │
│    ├── fact = extractor.extract(chapter_text, context)         │
│    │                                                          │
│    ├── fact = validator.validate(fact)                         │
│    │                                                          │
│    ├── ★ world_agent.process_chapter(                         │
│    │       chapter_num, chapter_text, fact                     │
│    │   )                                                      │
│    │   ├── 1. 信号扫描（轻量，每章）                              │
│    │   ├── 2. 若有信号 → LLM 世界结构更新                        │
│    │   └── 3. 持久化更新后的 WorldStructure                      │
│    │                                                          │
│    └── store(fact), broadcast(progress)                        │
└───────────────────────────────────────────────────────────────┘
```

### 5.3 Agent 的两阶段处理

每一章经过 Agent 时，分两步处理：

#### 阶段 A: 信号扫描（轻量，纯本地，每章执行）

无需 LLM 调用，用关键词和规则扫描原文 + 已提取 ChapterFact，检测世界观构建信号：

```python
class WorldBuildingSignal(BaseModel):
    signal_type: str         # region_division / layer_transition / portal /
                             # instance_entry / macro_geography / world_declaration
    chapter: int
    raw_text_excerpt: str    # 触发信号的原文片段（≤200 字）
    extracted_facts: list    # 相关的 LocationFact / SpatialRelationship
    confidence: str          # high / medium / low

# 信号检测规则
SIGNAL_RULES = {
    # 1. 世界划分声明
    "region_division": {
        "keywords": ["分为", "划为", "有.*个大陆", "有.*部洲", "有.*界",
                     "四大部洲", "九州", "五大洲", "七大王国"],
        "pattern": r"(分|划)为[\d一二三四五六七八九十]+[大]?(部洲|大陆|界|域|国)",
    },
    # 2. 空间层转换（角色进入非地理空间）
    "layer_transition": {
        "keywords": ["上了天", "升上天", "到天宫", "进了地府", "入冥界",
                     "潜入海底", "飞上", "坠入"],
        "location_types": ["天宫", "天庭", "天界", "地府", "冥界", "海底", "龙宫"],
    },
    # 3. 副本入口（角色进入封闭独立空间）
    "instance_entry": {
        "keywords": ["走进洞", "入洞", "进了洞", "进入阵", "踏入",
                     "打开了门", "传送"],
        "location_types_re": r"(洞|府|宫|阵|秘境|幻境|禁地)",
    },
    # 4. 宏观地理声明（描述大尺度空间关系的叙述）
    "macro_geography": {
        "triggers": "new_location_with_macro_type",  # 新出现的 洲/域/界/国 类型地点
        "look_for_context": True,   # 回溯原文查找该地名附近的地理描述
    },
}
```

#### 阶段 B: LLM 世界结构更新（仅当信号触发时）

当阶段 A 检测到高置信度信号时，调用 LLM 更新 WorldStructure：

```python
# 触发条件（满足任一即触发 LLM 调用）
TRIGGER_CONDITIONS = [
    "chapter <= 5",                          # 前 5 章必调用（世界观通常在开篇建立）
    "signal.type == 'region_division'",       # 发现世界划分声明
    "signal.type == 'layer_transition' and layer_is_new",  # 首次进入新空间层
    "new_macro_locations >= 2",              # 发现 2+ 个宏观地点（洲/界/域）
    "chapter % 20 == 0",                     # 每 20 章做一次例行更新
]
```

LLM Prompt 设计（增量更新，非全量重建）：

```
你是一个小说世界观构建专家。你正在逐章阅读一部小说，渐进式构建世界地图结构。

## 当前世界结构
{current_world_structure_json}

## 本章世界观信号
以下是第 {chapter_num} 章中与世界地理相关的文本片段：

{signal_excerpts}

## 本章提取的地点
{locations_from_chapter_fact}

## 本章提取的空间关系
{spatial_relationships_from_chapter_fact}

## 你的任务
基于上述信号，判断是否需要更新世界结构。你可以执行以下操作：

### 可执行的操作
1. ADD_REGION: 添加新的宏观区域
   - name, cardinal_direction, region_type, parent_region
2. ADD_LAYER: 添加新的地图层
   - name, layer_type, description
3. ADD_PORTAL: 添加层间传送门
   - portal_name, source_layer, source_location, target_layer
4. ASSIGN_LOCATION: 将地点分配到区域或层
   - location_name, region_name, layer_id
5. UPDATE_REGION: 修改区域属性
   - name, field, new_value
6. NO_CHANGE: 本章无需更新世界结构

## 输出要求
严格 JSON，格式为操作列表：
{
  "operations": [
    {"op": "ADD_REGION", "data": {...}},
    {"op": "ASSIGN_LOCATION", "data": {...}},
    ...
  ],
  "reasoning": "简要说明为什么做出这些更新"
}
```

### 5.4 Agent 状态管理

WorldStructure 是一个**持久化的、逐步增长的**数据结构：

```python
class WorldStructureAgent:
    """渐进式世界结构构建代理"""

    def __init__(self, novel_id: str):
        self.novel_id = novel_id
        self.structure: WorldStructure | None = None
        self.signal_buffer: list[WorldBuildingSignal] = []
        self.llm = get_llm_client()

    async def load_or_init(self) -> None:
        """从数据库加载已有结构，或初始化空结构"""
        saved = await world_structure_store.load(self.novel_id)
        if saved:
            self.structure = saved
        else:
            self.structure = WorldStructure(
                novel_id=self.novel_id,
                layers=[MapLayer(
                    layer_id="overworld",
                    name="主世界",
                    layer_type=LayerType.OVERWORLD,
                )],
                portals=[],
            )

    async def process_chapter(
        self,
        chapter_num: int,
        chapter_text: str,
        fact: ChapterFact,
    ) -> None:
        """处理一章 — 扫描信号，按需调用 LLM 更新世界结构"""
        # 阶段 A: 信号扫描
        signals = self._scan_signals(chapter_num, chapter_text, fact)

        # 阶段 B: 判断是否触发 LLM 更新
        if self._should_trigger_llm(chapter_num, signals):
            operations = await self._call_llm_for_update(
                chapter_num, signals, fact
            )
            self._apply_operations(operations)
        else:
            # 即使不调用 LLM，也用启发式规则做轻量更新
            self._apply_heuristic_updates(chapter_num, fact)

        # 持久化
        await world_structure_store.save(self.novel_id, self.structure)

    def _scan_signals(
        self, chapter_num: int, text: str, fact: ChapterFact
    ) -> list[WorldBuildingSignal]:
        """纯本地信号扫描 — 关键词匹配 + 规则"""
        ...

    def _should_trigger_llm(
        self, chapter_num: int, signals: list[WorldBuildingSignal]
    ) -> bool:
        """判断是否需要调用 LLM"""
        ...

    def _apply_heuristic_updates(
        self, chapter_num: int, fact: ChapterFact
    ) -> None:
        """无需 LLM 的轻量更新 —— 基于关键词自动分配地点到层/区域"""
        # 例如: 名字含 "天宫/天庭" → 分配到 celestial 层
        # 名字含 "地府/冥界" → 分配到 underworld 层
        # parent 是已知区域 → 分配到该区域
        ...
```

### 5.5 与上下文构建器的集成

Agent 构建的世界结构可以反馈给 `ContextSummaryBuilder`，让后续章节的提取更准确：

```python
# context_summary_builder.py 新增
class ContextSummaryBuilder:
    async def build(self, novel_id: str, chapter_num: int) -> str:
        # ... 现有逻辑 ...

        # 新增：将世界结构信息加入上下文
        world_structure = await world_structure_store.load(novel_id)
        if world_structure:
            sections.append(self._format_world_structure(world_structure))

        return "\n\n".join(sections)

    def _format_world_structure(self, ws: WorldStructure) -> str:
        """将世界结构格式化为上下文摘要"""
        lines = ["### 已知世界结构"]
        for layer in ws.layers:
            regions = [r for r in (layer.regions or []) if r.name]
            if layer.layer_type == LayerType.OVERWORLD and regions:
                lines.append(f"- 主世界区域: {', '.join(r.name for r in regions)}")
            else:
                lines.append(f"- {layer.name} ({layer.layer_type})")
        for portal in ws.portals[:10]:
            lines.append(f"- 传送门: {portal.name} ({portal.source_layer} ↔ {portal.target_layer})")
        return "\n".join(lines)
```

### 5.6 LLM 调用频率控制

渐进式代理的 LLM 调用开销需要控制，不能每章都调用：

| 触发条件 | 预计频率（100章小说） | 说明 |
|---------|---------------------|------|
| 前 5 章强制触发 | 5 次 | 世界观通常在开篇建立 |
| region_division 信号 | 1-3 次 | 世界划分声明不多 |
| 首次 layer_transition | 2-5 次 | 首次进入天界/冥界/海底等 |
| 每 20 章例行更新 | 5 次 | 兜底检查 |
| **总计** | **~15-20 次** | 远少于 100 次（每章调用）|

对比：
- 当前方案: 100 次 LLM 调用（全部用于 ChapterFact 提取）
- 新方案: ~115-120 次（100 次提取 + 15-20 次世界结构更新）
- 增加 ~15-20% 的 LLM 调用量，换取可靠的世界结构

### 5.7 回退策略

如果 Agent 未能构建有效的 WorldStructure（LLM 输出质量差或分析中断）：

1. **启发式 fallback**：用关键词规则自动分类（现有的 celestial/underworld 检测逻辑可以升级为此）
2. **平面布局 fallback**：回退到当前的全局约束求解器布局
3. **用户手动编辑**：提供 UI 让用户手动指定区域划分和层级结构

---

## 6. 分层布局算法

### 6.0 地图方位原则：上北下南、左西右东

**所有地图布局必须遵循标准地图方位惯例**：画布上方=北，下方=南，左侧=西，右侧=东。

这不仅是制图学传统，更是读者的直觉预期。当小说提到"东胜神洲在东"时，读者期望它出现在地图右侧；"西牛贺洲在西"应在地图左侧。

坐标系统定义：

```
画布坐标 [0, 1000] × [0, 1000]
  +x → 东（右）    x=0 是最西端，x=1000 是最东端
  +y → 北（上）    y=0 是最南端，y=1000 是最北端

前端 MapLibre 映射：x → 经度（东西），y → 纬度（南北）
  MapLibre 天然是上北下南渲染，无需额外翻转。
```

**约束**：
- 方向约束 `north_of` → dy > 0（A 在 B 北面 → A 的 y 坐标更大）
- 方向约束 `east_of` → dx > 0（A 在 B 东面 → A 的 x 坐标更大）
- 区域布局中 `cardinal_direction: "east"` → 分配到画布右侧
- narrative axis 能量项不应覆盖明确的方位约束（方位约束优先级更高）

### 6.1 总体策略：自顶向下分层求解

```
Step 1: 世界层布局
  — 在 [0, 1000] 画布上安排各大区域的位置和大小
  — 使用区域间的方位关系（东胜神洲在东，西牛贺洲在西）
  — 每个区域分配一个矩形边界框

Step 2: 区域内布局
  — 在每个区域的边界框内，布局该区域的地点
  — 使用区域内的空间约束（方向、距离、包含等）
  — 约束求解器只处理该区域内的 10-50 个地点

Step 3: 副本层独立布局
  — 每个 Instance 层有自己的小画布
  — 使用层内的空间关系独立布局
  — 较简单，通常只有 3-15 个地点

Step 4: 传送门标注
  — 在主世界层标注传送门入口位置
  — 传送门图标可点击切换到目标层
```

### 6.2 区域布局算法

区域级布局比地点级简单得多——通常只有 4-8 个区域，且有明确的方位关系。

```python
def layout_regions(regions: list[WorldRegion], canvas_size: int = 1000) -> dict:
    """基于方位分配区域边界框"""
    # 方位 → 画布象限映射
    DIRECTION_ZONES = {
        "east":  (600, 200, 950, 800),   # (x1, y1, x2, y2)
        "west":  (50, 200, 400, 800),
        "south": (200, 50, 800, 350),
        "north": (200, 650, 800, 950),
        "center": (300, 300, 700, 700),
    }

    region_bounds = {}
    for region in regions:
        direction = region.cardinal_direction or "center"
        bounds = DIRECTION_ZONES.get(direction, DIRECTION_ZONES["center"])
        region_bounds[region.name] = bounds

    # 处理方位冲突和重叠（多个区域同方位）
    # ... 细分逻辑 ...

    return region_bounds
```

### 6.3 区域内约束求解

每个区域独立运行约束求解器，优势：

| 指标 | 当前（全局） | 新方案（分区域） |
|------|-------------|-----------------|
| 地点数/求解 | 100-279 | 10-50 |
| 参数维度 | 200-558 | 20-100 |
| 求解时间 | 10-30s | 1-3s/区域 |
| 布局质量 | 拥挤、混乱 | 区域内有序、全局有结构 |

### 6.4 narrative axis 的改进

有了区域划分后，narrative axis 变得更自然：
- 不再需要在全局布局中"猜测"旅行方向
- 区域之间的顺序由章节进度决定（第1章在东胜神洲→最后到西牛贺洲）
- 区域内的地点顺序由该区域内的章节进度决定

---

## 7. 前端交互设计

### 7.1 多层地图 UI

参考原神的 Tab 切换模式：

```
┌─────────────────────────────────────────────────┐
│ [主世界] [天界] [冥界] [水帘洞] [龙宫]  ← Tab 栏 │
├─────────────────────────────────────────────────┤
│                                                 │
│         ┌───────────┐   ┌───────────┐          │
│         │ 东胜神洲   │   │ 西牛贺洲   │          │
│         │  ·花果山   │   │  ·灵山    │          │
│         │  ·长安    │   │  ·火焰山   │          │
│         │  ⊙水帘洞入口│   │           │          │
│         └───────────┘   └───────────┘          │
│                                                 │
│         ┌───────────┐   ┌───────────┐          │
│         │ 南赡部洲   │   │ 北俱芦洲   │          │
│         └───────────┘   └───────────┘          │
│                                                 │
│  [Legend] ⊙=传送门入口  ·=地点  ─=轨迹          │
└─────────────────────────────────────────────────┘
```

### 7.2 区域边界显示

在主世界层，大区域用半透明填充色和虚线边界标识：

```typescript
// 区域边界 GeoJSON 层
map.addLayer({
    id: "region-fills",
    type: "fill",
    paint: {
        "fill-color": ["get", "regionColor"],
        "fill-opacity": 0.08,
    },
})
map.addLayer({
    id: "region-borders",
    type: "line",
    paint: {
        "line-color": ["get", "regionColor"],
        "line-opacity": 0.3,
        "line-dasharray": [4, 4],
        "line-width": 1.5,
    },
})
// 区域名称标注（大字体，低透明度）
map.addLayer({
    id: "region-labels",
    type: "symbol",
    layout: {
        "text-field": ["get", "name"],
        "text-size": 18,
    },
    paint: {
        "text-color": ["get", "regionColor"],
        "text-opacity": 0.4,
    },
})
```

### 7.3 传送门交互

传送门在地图上显示为特殊图标（旋涡/门的形状），点击后：

```
1. 弹出 Popup：
   ┌──────────────────────────┐
   │ ⊙ 南天门                │
   │ 通往：天界               │
   │ [进入天界地图]  [查看卡片] │
   └──────────────────────────┘

2. 点击"进入天界地图" → Tab 切换到天界层
3. 天界层的地图用不同的背景色/氛围标识
```

### 7.4 副本地图视图

点击副本入口后，切换到副本独立地图：

```
┌─────────────────────────────────────────────────┐
│ ← 返回主世界 · 花果山      [水帘洞] 内部地图     │
├─────────────────────────────────────────────────┤
│                                                 │
│    ┌─── 水帘洞内部 ───────────────────┐         │
│    │                                   │         │
│    │    ·铁板桥    ·石猴王座           │         │
│    │         ·瀑布入口                 │         │
│    │    ·宝库                          │         │
│    │                                   │         │
│    └───────────────────────────────────┘         │
│                                                 │
│  背景色: 偏暗/洞穴色调                           │
└─────────────────────────────────────────────────┘
```

### 7.5 渐进式解锁（Fog of War 增强）

当前的 fog of war 只有 "可见/半透明" 两态。增强为三态：

| 状态 | 视觉效果 | 条件 |
|------|---------|------|
| 隐藏 | 完全不显示 | 地点在当前章节范围之后才首次出现 |
| 已揭示 | 灰色轮廓，名称可见 | 地点在之前章节提到过，但不在当前章节范围 |
| 活跃 | 完整显示，可交互 | 地点在当前章节范围内有活动 |

---

## 8. 数据流设计

### 8.1 分析阶段数据流（渐进式）

```
  for each chapter:
  ┌──────────────────────────────────────────────────────────┐
  │                                                          │
  │  ┌─────────────┐                                        │
  │  │ 章节原文     │                                        │
  │  └──────┬──────┘                                        │
  │         │                                                │
  │  ┌──────▼──────────────────┐   ┌───────────────────┐    │
  │  │ context_builder.build() │◀──│ WorldStructure    │    │
  │  │ (包含世界结构摘要)        │   │ (已有的世界知识)    │    │
  │  └──────┬──────────────────┘   └──────▲────────────┘    │
  │         │ context                      │                 │
  │  ┌──────▼──────────────────┐           │                 │
  │  │ chapter_fact_extractor  │           │                 │
  │  │ (现有, 不修改)           │           │                 │
  │  └──────┬──────────────────┘           │                 │
  │         │ ChapterFact                  │                 │
  │  ┌──────▼──────────────────┐           │                 │
  │  │ fact_validator          │           │                 │
  │  │ (现有, 不修改)           │           │                 │
  │  └──────┬──────────────────┘           │                 │
  │         │ validated fact               │                 │
  │  ┌──────▼──────────────────┐           │                 │
  │  │ ★ world_structure_agent │───────────┘                 │
  │  │  A. 信号扫描(原文+fact) │                              │
  │  │  B. LLM 更新(若触发)    │                              │
  │  └──────┬──────────────────┘                             │
  │         │                                                │
  │  ┌──────▼──────┐                                        │
  │  │ store(fact)  │                                        │
  │  └─────────────┘                                        │
  └──────────────────────────────────────────────────────────┘

  分析完成后:
  ┌──────────────────────────────────────────────────────────┐
  │                                                          │
  │  WorldStructure ──┐                                      │
  │                    ├──▶ 分层布局引擎                       │
  │  ChapterFact[] ───┘    ├── 区域级布局                     │
  │  (空间关系聚合)          ├── 区域内约束求解                  │
  │                        ├── 副本独立布局                    │
  │                        └── 传送门标注                     │
  │                              │                           │
  │                        LayeredMapData                    │
  │                              │                           │
  │                        MapLibre + Tab 切换                │
  └──────────────────────────────────────────────────────────┘
```

**关键区别**：WorldStructure 不是分析完成后"从结果推断"，而是在分析过程中"与原文共建"。Agent 能看到原文，所以能捕获结构化提取丢失的宏观信息。

### 8.2 API 响应结构

```typescript
interface LayeredMapData {
    // 世界结构
    world_structure: {
        layers: MapLayerInfo[]
        portals: PortalInfo[]
        regions: RegionInfo[]
    }

    // 当前显示层的数据（默认 overworld）
    current_layer: {
        layer_id: string
        locations: MapLocation[]
        layout: MapLayoutItem[]
        layout_mode: "constraint" | "hierarchy"
        terrain_url: string | null
        region_boundaries: RegionBoundary[]  // 区域边界多边形
    }

    // 轨迹数据（跨层）
    trajectories: Record<string, TrajectoryPoint[]>

    // 分析范围
    analyzed_range: [number, number]
}

interface MapLayerInfo {
    layer_id: string
    name: string
    layer_type: LayerType
    location_count: number
    is_unlocked: boolean  // 在当前章节范围内是否有活动
}

interface PortalInfo {
    name: string
    source_layer: string
    source_location: string
    target_layer: string
    first_chapter: number
}

interface RegionBoundary {
    region_name: string
    color: string
    bounds: { x1: number; y1: number; x2: number; y2: number }
    // 或多边形顶点用于更精确的边界
}
```

---

## 9. 增量实施路径

### Phase 1: 世界结构代理 + 数据模型

**目标**：实现 WorldStructureAgent，在分析流水线中渐进构建世界结构。

- [ ] 新增 Pydantic 模型: `WorldStructure` / `MapLayer` / `Portal` / `WorldRegion` / `WorldBuildingSignal`
- [ ] 新增 `world_structure_store.py` — WorldStructure 的数据库 CRUD
- [ ] 新增 `world_structure_agent.py` — 核心代理逻辑
  - [ ] `_scan_signals()` — 信号扫描（关键词 + 规则）
  - [ ] `_should_trigger_llm()` — 触发条件判断
  - [ ] `_call_llm_for_update()` — LLM 增量更新调用
  - [ ] `_apply_heuristic_updates()` — 启发式轻量更新
- [ ] 新增世界结构更新 LLM prompt 模板
- [ ] 在 `analysis_service._run_loop_inner()` 中注入 Agent 调用
- [ ] 增强 `context_summary_builder.build()` — 加入世界结构摘要
- [ ] 新增 `world_structures` 数据库表
- [ ] 新增 API: `GET /api/novels/{id}/world-structure`

**验证标准**：用西游记前 10 章测试，Agent 应能识别四大部洲划分 + 天界层 + 冥界层。

**风险**：LLM 更新操作格式不稳定（JSON 解析失败）。
**缓解**：(1) 操作格式用 structured output + schema 约束 (2) 解析失败时记录日志但不中断分析流程 (3) 启发式规则作为 fallback。

### Phase 2: 分层布局引擎

**目标**：基于 WorldStructure 进行分区域分层布局，替代全局平面求解。

- [ ] 重构 `map_layout_service.py` — 分层求解架构
- [ ] 实现区域级布局（区域方位 → 区域边界框分配）
- [ ] 实现区域内约束求解（在边界框内运行现有 solver）
- [ ] 实现副本层独立布局（小画布，独立坐标系）
- [ ] 传送门位置标注
- [ ] 新增 `layer_layouts` 数据库表（每层独立缓存）
- [ ] 兼容性：当 WorldStructure 为空（仅 overworld）时，回退到当前全局布局

### Phase 3: 前端多层交互

**目标**：Tab 切换 + 区域边界 + 传送门 UI。

- [ ] 新增 `MapLayerTabs` 组件 — Tab 栏切换地图层
- [ ] 新增区域边界 GeoJSON 层（fill + line + symbol）
- [ ] 新增传送门图标（⊙ 标记）+ 点击切换到目标层
- [ ] 副本地图视图（不同背景色/氛围）
- [ ] 三态 Fog of War（hidden / revealed / active）
- [ ] 跨层轨迹标注（层切换点用特殊标记）
- [ ] 更新 `MapData` TypeScript 类型

### Phase 4: 增强与迭代

**目标**：提高世界结构质量 + 用户编辑 + 性能优化。

- [ ] 用户手动编辑世界结构的 UI（区域归属拖拽、传送门增删）
- [ ] 增强 extraction prompt — 在 ChapterFact 中新增可选的 `world_declarations` 字段
- [ ] 增加 `InBetween` 空间约束类型
- [ ] 增加地点语义位置提示（东洋大海 → 东方 hints）
- [ ] 性能优化：区域内增量更新布局
- [ ] 多小说通用性测试（红楼梦、射雕、斗破苍穹等不同风格）

---

## 10. 关键技术决策

### 决策 1: 世界观构建的时机

**选项 A**：渐进式代理 — 在分析过程中逐章构建（推荐）
**选项 B**：后置合成 — 分析完成后从结构化数据推断（已否决）

**选择 A**。原因：
- 结构化提取是有损过程，宏观世界观信息在提取时已丢失
- Agent 能看到原文，可以捕获 "世界分为四大部洲" 这类声明
- 渐进构建允许世界结构反馈到后续章节的提取上下文中
- 通过信号扫描 + 条件触发，LLM 调用仅增加 15-20%

### 决策 2: 区域边界的生成方式

**选项 A**：用 Voronoi 图从区域内地点生成自然边界
**选项 B**：用矩形边界框（简单但不够美观）
**选项 C**：用凸包 + 缓冲区

**推荐 Phase 2 用 B, Phase 4 升级到 A**。矩形简单可靠，Voronoi 后续可以增加。

### 决策 3: 层切换是 API 级还是前端级

**选项 A**：前端一次获取所有层数据，本地切换
**选项 B**：每次切层调用 API `?layer_id=xxx` 获取该层数据

**推荐 B**。原因：
- 大型小说可能有 10+ 层，全部加载过慢
- 每层数据独立缓存，按需加载
- API: `GET /api/novels/{id}/map?layer_id=overworld&chapter_start=1&chapter_end=100`

### 决策 4: WorldStructure 是否可以手动编辑

**推荐是**。LLM 生成的世界结构可能有错误（如把花果山归到西牛贺洲），用户应该能：
- 调整地点的区域归属
- 添加/删除传送门
- 调整区域方位

这些编辑存储为 override，优先于 LLM 生成的结构。

---

## 11. 与现有架构的兼容性

### 不破坏原则

- 现有的 `ChapterFact` 模型不修改，新增字段可选
- 现有的 `get_map_data()` API 保持兼容
- 新增 `get_layered_map_data()` 作为 V2 API
- `WorldStructure` 不可用时，自动 fallback 到当前全局布局
- 前端通过 feature flag 或 API 版本检测决定使用哪种地图模式

### 迁移路径

```
V1 (当前): 全局平面地图 → 所有地点在一个画布
     ↓  (Phase 1-2 完成后)
V1.5: 有区域边界的平面地图 → 同一画布但有区域划分
     ↓  (Phase 3 完成后)
V2: 多层地图 → Tab 切换 + 传送门 + 副本
```

---

## 12. 开放问题

1. **信号扫描的召回率**：关键词 + 规则能否覆盖所有小说的世界观表达方式？古典小说（"分为四大部洲"）和现代网文（"三界九天"、"上界下界"）的表达差异很大。可能需要为不同小说类型定制信号规则集。

2. **副本识别的边界**：什么样的地点算"副本"？水帘洞明显是，但"某个客栈的地下室"算不算？需要定义清晰的启发式规则 + LLM 判断的组合策略。

3. **跨层轨迹的可视化**：孙悟空从地面→天界→地面→冥界→地面这样的跨层轨迹如何可视化？可能需要在轨迹面板中标注层切换点（如 "⊙ 经南天门进入天界"）。

4. **Agent 的 LLM Context 预算**：世界结构更新 prompt 需要包含当前 WorldStructure + 信号文本 + 本章 fact。随着分析推进，WorldStructure 可能增长到很大。需要设计摘要/截断策略，确保不超过 qwen3:8b 的 16K context。

5. **通用性**：西游记有四大部洲这样清晰的宏观结构，但不同类型小说差异很大：
   - 《红楼梦》：主要是"荣国府→各院落"的建筑层级，可能只有 overworld 一层
   - 修仙网文：通常有 "凡界→修真界→仙界→神界" 的多层结构
   - 都市小说：可能只有现实城市地理，不需要副本/层级
   - Agent 应该能优雅处理"世界结构很简单"的情况（只有 overworld，没有区域划分）

6. **重新分析的行为**：如果用户 force 重新分析已有章节，Agent 应该如何处理？
   - 选项 A：重建 WorldStructure（丢失之前的手动编辑）
   - 选项 B：在现有 WorldStructure 上增量更新
   - 倾向 B，但需要处理冲突（如 LLM 这次认为花果山在不同区域）

7. **Agent 调用失败的容错**：Agent 的 LLM 调用失败（超时/格式错误）不应阻塞整个分析流水线。需要用 try/except 包裹，失败时记录日志并继续分析。

---

*本文档为讨论稿 v2（已采纳渐进式代理方案），请审阅后反馈意见。*
