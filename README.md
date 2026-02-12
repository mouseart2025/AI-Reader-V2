# AI Reader V2

本地部署的智能小说阅读理解系统。利用本地 LLM 将小说文本转化为结构化知识图谱，提供关系图、世界地图、时间线等多维可视化视图，以及基于原文的自然语言问答。

完全本地运行，无需云端 API，数据不出本机。

## 功能概览

- **书架管理** — 上传 .txt/.md 小说，自动章节切分，重复检测，数据导入/导出
- **智能阅读** — 实体高亮（人物/地点/物品/组织/概念），点击即查卡片，阅读进度记忆
- **知识图谱** — 力导向人物关系图，按章节范围探索关系演变
- **世界地图** — 基于约束求解的地点坐标计算，MapLibre GL JS 交互式渲染，程序化地形生成，人物轨迹动画，战争迷雾
- **时间线** — 多泳道事件时间线，按重要度/类型筛选
- **势力图** — 组织架构与势力关系网络
- **百科全书** — 分类浏览所有概念/功法/物品，全文搜索
- **智能问答** — 流式对话，RAG 检索增强，答案来源溯源，对话历史管理
- **数据管理** — 全量导出/导入，环境健康检查

## 世界地图技术方案

世界地图功能是本项目的核心亮点之一，实现了从小说文本到交互式地图的全自动流水线：

```
小说文本 → LLM 空间关系提取 → 约束求解布局 → 程序化地形生成 → MapLibre GL JS 渲染
```

### 技术路线

1. **空间信息提取** — 在章节分析时，LLM 额外提取地点间的 6 类空间关系（方位、距离、包含、相邻、分隔、地形），附带置信度和原文依据
2. **约束求解布局** — 参考 [PlotMap](https://github.com/AutodeskAILab/PlotMap)（Autodesk AI Research, 2024）的 CMA-ES 方法，使用 `scipy.optimize.differential_evolution` 全局优化，基于能量函数（方位惩罚 + 距离误差 + 包含违反 + 分隔违反 + 反重叠）计算地点 (x, y) 坐标
3. **程序化地形** — 基于 Voronoi 区域划分 + OpenSimplex 噪声生成地形底图，按地点类型分配生物群落颜色（山地/水域/森林/聚落/荒漠/沼泽/平原）
4. **浏览器渲染** — [MapLibre GL JS](https://maplibre.org/) 替代 force-graph，伪经纬度坐标映射保留原生缩放/平移，支持地形底图叠加、类型标记、渐进标签、轨迹动画、长按拖拽调整位置

约束不足时（< 3 条空间关系），自动退化为层级圆形布局，确保始终有可用的地图视图。

### 研究参考

项目目录下的两份技术研究报告为世界地图方案的设计提供了理论基础和技术选型依据：

| 文档 | 说明 |
|------|------|
| [`LLM驱动的小说世界地图生成系统_技术研究报告.md`](./LLM驱动的小说世界地图生成系统_技术研究报告.md) | 工程导向的技术方案。重点分析了 PlotMap 约束求解、MapLibre GL JS 渲染、CHGIS 中文历史地名数据库等关键技术，提出了五阶段端到端流水线架构。**本项目的世界地图实现主要基于此文档的技术路线。** |
| [`自动文学制图学：利用本地大语言模型从叙事文本构建交互式地理空间系统的技术框架研究报告.md`](./自动文学制图学：利用本地大语言模型从叙事文本构建交互式地理空间系统的技术框架研究报告.md) | 学术导向的综合研究（Gemini 撰写）。涵盖力导向图 + 方向约束、RCC-8 定性空间推理、Stable Diffusion ControlNet 地图美化等方案。其中旅行时间→距离换算公式（D = T × V × M_terrain）和小说类型策略表被本项目采纳。 |

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS v4 + shadcn/ui |
| 地图渲染 | MapLibre GL JS |
| 关系图/势力图 | react-force-graph-2d |
| 状态管理 | Zustand |
| 后端 | Python + FastAPI + aiosqlite |
| 约束求解 | SciPy (differential_evolution) + NumPy |
| 地形生成 | Pillow + OpenSimplex + SciPy (Voronoi) |
| 向量检索 | ChromaDB + sentence-transformers |
| 数据库 | SQLite (WAL mode) |
| LLM 推理 | Ollama (本地，默认 qwen3:8b) |

## 环境要求

- Node.js >= 22
- Python >= 3.9 + [uv](https://docs.astral.sh/uv/) 包管理器
- [Ollama](https://ollama.com/) 已安装并运行
- macOS (Apple Silicon 推荐) / Linux
- 建议 16GB+ 内存，8GB+ 显存（运行 qwen3:8b）

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
│       │   ├── routes/       # REST 端点 (novels, chapters, map, graph, ...)
│       │   └── websocket/    # 分析进度 + 聊天 WebSocket
│       ├── services/         # 业务逻辑
│       │   ├── analysis_service.py       # 章节分析编排
│       │   ├── visualization_service.py  # 图谱/地图/时间线数据聚合
│       │   ├── map_layout_service.py     # 约束求解 + 地形生成
│       │   ├── query_service.py          # RAG 问答
│       │   └── ...
│       ├── extraction/       # LLM 结构化提取
│       │   ├── chapter_fact_extractor.py
│       │   ├── fact_validator.py
│       │   └── prompts/      # 系统提示词 + few-shot 示例
│       ├── db/               # SQLite + ChromaDB 数据层
│       ├── models/           # Pydantic 数据模型 (ChapterFact 等)
│       ├── infra/            # 配置 + LLM 客户端
│       └── utils/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── app/              # App 入口 + 路由 + NovelLayout
│       ├── pages/            # 10 个页面
│       ├── components/
│       │   ├── visualization/  # NovelMap (MapLibre) + VisualizationLayout
│       │   ├── entity-cards/   # 实体卡片抽屉
│       │   ├── chat/           # 聊天组件
│       │   └── ui/             # shadcn/ui 基础组件
│       ├── stores/           # Zustand 状态 (chapterRange, entityCard, ...)
│       ├── api/              # 类型定义 + API 客户端
│       └── lib/
├── _bmad-output/             # BMad 架构文档 + Story 规划
└── *.md                      # 研究报告文档
```

## 数据存储

所有数据存储在 `~/.ai-reader-v2/` 目录下：

- `data.db` — SQLite 数据库（小说、章节、分析结果、对话、地图布局缓存等）
- `chroma/` — ChromaDB 向量数据库（语义搜索）
- `maps/{novel_id}/terrain.png` — 程序化生成的地形底图

## 版本

当前版本：**v0.5.0**

## License

Private
