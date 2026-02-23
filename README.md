# AI Reader V2

[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)

本地部署的智能小说阅读理解系统。利用 LLM 将小说文本转化为结构化知识图谱，提供关系图、多层级世界地图、时间线等多维可视化视图，以及基于原文的自然语言问答。

支持本地 Ollama 和云端 OpenAI 兼容 API 两种 LLM 推理模式，数据全部存储在本机。

## 功能概览

- **书架管理** — 上传 .txt/.md 小说，自动章节切分，重复检测，数据导入/导出
- **实体预扫描** — 分析前自动统计扫描 + LLM 分类，生成高频实体词典，注入分析流水线提升提取质量
- **智能阅读** — 实体高亮（人物/地点/物品/组织/概念），别名自动高亮并解析到同一人物卡片，阅读进度记忆。人物卡片关系按分类分组显示（血亲/亲密/师承/社交/敌对），多条证据可折叠查看
- **实体别名合并** — 自动识别同一实体的多个称呼（如孙悟空/美猴王/行者/齐天大圣），在关系图、实体列表、阅读页中合并为单一节点。三级安全过滤（硬禁/软禁/安全）防止亲属称呼、泛称（妖精/那怪）和集体名词造成错误合并。规范名选择优先短名（正式人名 2-3 字）
- **知识图谱** — 力导向人物关系图，按章节范围探索关系演变，节点合并别名显示，关系类型自动规范化，边颜色按分类着色（亲属/亲密/师承/友好/敌对），多关系类型展示
- **多层级世界地图** — 宏观区域划分（如四大部洲）、多空间层（天界/冥界/洞府副本）、传送门连接、区域内约束求解布局、程序化地形生成、人物轨迹动画、三态战争迷雾、用户可编辑世界结构、16:9 宽画布、智能副本合并、地理上下文面板（Tab 切换）、场景转换分析增强地点层级、两步层级重建（预览变更→确认应用）、嵌套 Convex Hull 领地轮廓（手绘风格边缘扰动）、地形语义纹理层（山脉三角/河流波浪/森林树丛/沙漠点阵/洞穴钟乳石，按地点 icon 自动散布）、暗色层专属大气纹理（天界星空/冥界岩雾/海底波浪/副本漩涡/灵界紫雾）、区域名称弯曲文字（SVG textPath 沿弧线排列）、地点点击直接打开实体卡片
- **时间线** — 多泳道事件时间线，按重要度/类型筛选
- **势力图** — 组织架构与势力关系网络
- **百科全书** — 分类浏览所有概念/功法/物品，全文搜索，地点层级树形视图
- **智能问答** — 流式对话，RAG 检索增强，答案来源溯源，对话历史管理
- **多 LLM 后端** — 支持本地 Ollama 和云端 OpenAI 兼容 API（DeepSeek、通义千问等），Token 预算根据模型上下文窗口自动缩放（8K~128K 线性插值）
- **数据管理** — 全量导出/导入，分析数据清除，环境健康检查

## 世界地图技术方案

世界地图是本项目的核心亮点，实现了从小说文本到多层级交互式地图的全自动流水线：

```
小说文本 → LLM 提取 (空间关系 + 世界观声明)
         → WorldStructureAgent (信号扫描 + 启发式 + LLM 增量更新)
         → 区域级约束求解布局 + 层独立布局
         → 程序化地形生成
         → D3.js + SVG 多层渲染 / react-leaflet 地理地图渲染
```

### V2 架构：多层级世界结构

V2 引入了 WorldStructure 数据模型和渐进式世界观构建代理，解决了三个核心问题：

**问题 1：缺乏宏观世界观理解** — 以西游记为例，开篇即声明"世界分为四大部洲"，V1 无法表达这种宏观结构。V2 通过 WorldStructureAgent 在分析过程中渐进式构建世界观，自动识别区域划分并按方位布局（东胜神洲→东侧，西牛贺洲→西侧）。

**问题 2：缺少"副本"概念** — 天界、冥界、洞府等非平面空间在 V1 中被平铺在一个平面上。V2 引入 MapLayer 模型，支持 overworld/sky/underground/sea/pocket/spirit 六种层类型，每层独立画布布局，通过 Portal（传送门）连接。

**问题 3：约束求解器的局限性** — 把 279 个地点（西游记）全部扔进一个求解器效果有限。V2 先布局宏观区域边界框，再在每个区域内独立求解，显著提升布局质量和求解效率。

### V5 改进：布局 + 提取质量 + 地理面板

V5 针对实际使用中发现的多个问题进行了系统性改进：

**布局优化** — 画布从 1000x1000 正方形改为 1600x900 宽屏比例（16:9），解决地点环形分布问题。移除 1D 叙事轴投影，改为均匀分布能量项 + 弱叙事顺序，实现区域内 2D 自然分布。

**提取质量** — 基于中文地名形态学研究（专名+通名结构），实现 10 条结构化规则替代穷举黑名单，精确过滤泛化地理词（山上、村外、小城）和通用人物称呼（众人、堂主）。已知地点注入 LLM 上下文实现指代消解（"小城"→"青牛镇"）。

**别名安全** — Union-Find 别名合并增加桥接词过滤，阻止亲属称呼（大哥、妈妈）和泛化称呼（老人、少年）造成的错误实体合并。

**智能副本合并** — 只有 1 个地点的副本层自动合并到主世界显示入口标记，减少无意义的空 Tab。

**地理上下文面板** — 新增侧边面板展示各章节的世界地理描述原文，帮助理解地图布局依据。

**卷识别** — 增强章节分割器的卷检测，支持章节编号重置（多个"第一章"）作为卷边界信号。

### 技术路线

1. **空间信息提取** — LLM 提取 7 类空间关系（方位、距离、包含、相邻、分隔、地形、夹在中间），以及可选的世界观声明（区域划分、空间层声明、传送门、区域方位）
2. **世界结构代理** — WorldStructureAgent 每章运行：关键词信号扫描 → 启发式层/区域分配 → 触发条件满足时调用 LLM 增量更新（ADD_REGION / ADD_LAYER / ADD_PORTAL / ASSIGN_LOCATION 等操作）
3. **小说类型自适应** — 自动检测小说类型（奇幻/武侠/历史/都市），调整信号检测灵敏度：奇幻启用多层检测，都市禁用副本检测，简单结构优雅退化为 V1 模式
4. **区域级约束求解** — 参考 [PlotMap](https://github.com/AutodeskAILab/PlotMap)（Autodesk AI Research, 2024）的 CMA-ES 方法，使用 `scipy.optimize.differential_evolution`，能量函数包含方位惩罚 + 距离误差 + 包含违反 + 分隔违反 + 夹在中间 + 反重叠 + 叙事轴 + 方位名称提示
5. **程序化地形** — Voronoi 区域划分 + OpenSimplex 噪声生成地形底图，按地点类型分配生物群落颜色
6. **浏览器渲染** — D3.js + SVG 多层 Tab 切换，区域边界半透明填充，传送门标记点击切层，三态战争迷雾（hidden/revealed/active）；真实地理模式使用 react-leaflet + CartoDB 瓦片地图
7. **用户编辑** — Override 机制存储用户修正（区域归属/传送门增删），LLM 重分析不覆盖用户编辑

约束不足时（< 3 条空间关系），自动退化为层级圆形布局，确保始终有可用的地图视图。

### 研究参考

| 文档 | 说明 |
|------|------|
| [`_bmad-output/world-map-v2-architecture.md`](./_bmad-output/world-map-v2-architecture.md) | **V2 架构设计文档**。多层级世界结构数据模型、WorldStructureAgent 信号扫描与 LLM 增量更新、分层布局引擎、前端多层交互设计。 |
| [`LLM驱动的小说世界地图生成系统_技术研究报告.md`](./LLM驱动的小说世界地图生成系统_技术研究报告.md) | 工程导向的技术方案。PlotMap 约束求解、MapLibre GL JS 渲染、CHGIS 中文历史地名等关键技术分析。**V1 地图实现主要基于此文档。** |
| [`自动文学制图学：利用本地大语言模型从叙事文本构建交互式地理空间系统的技术框架研究报告.md`](./自动文学制图学：利用本地大语言模型从叙事文本构建交互式地理空间系统的技术框架研究报告.md) | 学术导向的综合研究。旅行时间→距离换算公式和小说类型策略表被本项目采纳。 |
| [`_bmad-output/spatial-entity-quality-research.md`](./_bmad-output/spatial-entity-quality-research.md) | **空间实体提取质量研究**。中文地名形态学分析、SpatialML/ISO-Space 标注体系、三层防御架构设计。V5 提取质量改进基于此文档。 |

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS v4 + shadcn/ui |
| 地图渲染 | D3.js + SVG (小说地图) / react-leaflet + Leaflet (地理地图) |
| 关系图/势力图 | react-force-graph-2d |
| 状态管理 | Zustand |
| 后端 | Python + FastAPI + aiosqlite |
| 中文分词 | jieba (实体预扫描) |
| 约束求解 | SciPy (differential_evolution) + NumPy |
| 地形生成 | Pillow + OpenSimplex + SciPy (Voronoi) |
| 向量检索 | ChromaDB + sentence-transformers |
| 数据库 | SQLite (WAL mode) |
| LLM 推理 | Ollama (本地，默认 qwen3:8b) 或 OpenAI 兼容 API (云端) |

## 环境要求

- Node.js >= 22
- Python >= 3.9 + [uv](https://docs.astral.sh/uv/) 包管理器
- [Ollama](https://ollama.com/) 已安装并运行（本地模式），或配置 OpenAI 兼容 API（云端模式）
- macOS (Apple Silicon 推荐) / Linux
- 本地模式建议 16GB+ 内存，8GB+ 显存（运行 qwen3:8b）

## 快速开始

### 1. 启动 Ollama

```bash
ollama pull qwen3:8b
ollama serve
```

### 2. 启动后端

```bash
cd backend
uv sync
uv run uvicorn src.api.main:app --reload
```

后端运行在 `http://localhost:8000`，访问 `/api/health` 验证。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端运行在 `http://localhost:5173`，已配置代理转发 `/api` 和 `/ws` 到后端。

## 项目结构

```
AI-Reader-V2/
├── backend/
│   ├── pyproject.toml
│   └── src/
│       ├── api/              # FastAPI 路由 + WebSocket
│       │   ├── routes/       # REST 端点 (novels, chapters, map, graph, world_structure, ...)
│       │   └── websocket/    # 分析进度 + 聊天 WebSocket
│       ├── services/         # 业务逻辑
│       │   ├── analysis_service.py       # 章节分析编排 (含 WorldStructureAgent 集成)
│       │   ├── alias_resolver.py         # 实体别名解析 (Union-Find 合并别名组)
│       │   ├── entity_aggregator.py      # 实体聚合 (跨别名合并 Profile)
│       │   ├── relation_utils.py          # 关系类型规范化 + 分类 (共享模块)
│       │   ├── visualization_service.py  # 图谱/地图/时间线数据聚合 (按层获取)
│       │   ├── map_layout_service.py     # 区域级约束求解 + 层独立布局 + 地形生成
│       │   ├── world_structure_agent.py  # 世界结构代理 (信号扫描/启发式/LLM 增量更新)
│       │   ├── scene_transition_analyzer.py  # 场景转换图分析 (纯算法增强地点层级)
│       │   ├── location_hierarchy_reviewer.py # LLM 地点层级审查 (post-analysis)
│       │   ├── location_hint_service.py  # 地点方位名称推断
│       │   ├── query_service.py          # RAG 问答
│       │   └── ...
│       ├── extraction/       # LLM 结构化提取
│       │   ├── chapter_fact_extractor.py
│       │   ├── fact_validator.py
│       │   ├── entity_pre_scanner.py     # 实体预扫描 (jieba 统计 + LLM 分类)
│       │   └── prompts/      # 系统提示词 + few-shot 示例 + 世界结构更新模板
│       ├── db/               # SQLite + ChromaDB 数据层
│       │   ├── entity_dictionary_store.py        # 实体词典存储
│       │   ├── world_structure_store.py          # 世界结构持久化
│       │   ├── world_structure_override_store.py  # 用户编辑 override 存储
│       │   └── ...
│       ├── models/           # Pydantic 数据模型
│       │   ├── chapter_fact.py      # ChapterFact (含 WorldDeclaration)
│       │   ├── world_structure.py   # WorldStructure / MapLayer / Portal / WorldRegion
│       │   ├── entity_dict.py       # EntityDictEntry
│       │   └── ...
│       ├── infra/            # 配置 + LLM 客户端 + Token 预算自适应
│       └── utils/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── app/              # App 入口 + 路由 + NovelLayout
│       ├── pages/            # 页面 (MapPage 含层切换 + 世界编辑)
│       ├── components/
│       │   ├── visualization/  # NovelMap + MapLayerTabs + WorldStructureEditor
│       │   ├── entity-cards/   # 实体卡片抽屉
│       │   ├── chat/           # 聊天组件
│       │   └── ui/             # shadcn/ui 基础组件
│       ├── stores/           # Zustand 状态 (chapterRange, entityCard, ...)
│       ├── api/              # 类型定义 + API 客户端
│       └── lib/
├── _bmad-output/             # BMad 架构文档 + Story 规划 + Sprint 状态
└── *.md                      # 研究报告文档
```

## 数据存储

所有数据存储在 `~/.ai-reader-v2/` 目录下：

- `data.db` — SQLite 数据库（小说、章节、分析结果、实体词典、对话、世界结构、层布局缓存、用户 override 等）
- `chroma/` — ChromaDB 向量数据库（语义搜索）
- `maps/{novel_id}/terrain.png` — 程序化生成的地形底图

## 开发进度

| Epic | 描述 | Stories | 状态 |
|------|------|---------|------|
| Epic 1 | 书架与小说上传 | 7 | done |
| Epic 2 | 小说分析引擎 | 5 | done |
| Epic 3 | 阅读体验与实体卡片 | 7 | done |
| Epic 4 | 知识图谱可视化 | 7 | done |
| Epic 5 | 智能问答 | 5 | done |
| Epic 6 | 百科与系统设置 | 3 | done |
| Epic 7 | 世界地图 V2 — 多层级世界结构 | 12 | done |
| Epic 8 | 实体预扫描词典 | 5 | done |
| Epic 9 | 实体别名合并 | 1 | done |
| Epic 10 | 世界地图 V4 — 布局算法重构 + 提示词修复 | 2 | done |
| Epic 11 | 地图性能优化 (32s → 4s) + 非正文章节排除 | 2 | done |
| Epic 12 | 世界地图 V5 — 多维度改进 | 5 | done |
| Epic 13 | 地点层级修复 + 百科树形视图 | 2 | done |
| Epic 14 | 人物卡片与关系系统改进 | 3 | done |
| Epic 15 | 地理坐标匹配 — GeoNames 真实地名定位 | 1 | done |
| Epic 16 | 剧本模式集成到阅读页右侧面板 | 1 | done |
| Epic 17 | 场景转换分析 + LLM 层级审查 — 地点层级质量提升 | 2 | done |
| Epic 18 | 两步层级重建 + 地图布局质量 + 环路修复 | 4 | done |
| Epic 19 | 地理数据质量提升 — 空间信号挖掘 + 提取质量 + 上下文整合 | 11 | done |
| Epic 20 | Token 预算自适应 — 根据模型上下文窗口动态缩放 | 1 | done |
| Epic 21 | GeoNames 中文别名数据库集成 — 地理地名解析可扩展性提升 | 4 | done |
| Epic 22 | 前端性能优化 — 构建修复 + 渲染优化 + 虚拟化 + Bundle 瘦身 | 6 | done |
| Epic 23 | 地图引擎增量优化 — 力导向预布局 + 语义角色 + 冲突标记 + 约束锁定 | 5 | done |
| Epic 24 | 空间层级微观尺度修正 — 后缀补全 + 兜底降级 + 主导匹配 + 收养门槛 | 1 | done |
| Epic 25 | 地图视觉改进 — Q1-Q6 Quick Wins + M1 嵌套 Hull 领地 + M2 地形语义纹理 + M4 暗色层大气纹理 + M3 弯曲文字 + 地点点击开卡 | 3 | done |

共计 **25 个 Epic、105 个 Story**，全部完成。

## 版本

当前版本：**v0.25.5**

## License

本项目采用 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 许可证。允许非商业用途的分享和改编，需署名并以相同协议分发。
