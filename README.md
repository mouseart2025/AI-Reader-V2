# AI Reader V2

本地部署的智能小说阅读理解系统。将小说文本转化为结构化知识图谱，提供关系图、时间线、世界地图等多维可视化视图，以及基于原文的自然语言问答。

## 功能概览

- **书架管理** — 上传 .txt/.md 小说，自动章节切分
- **智能阅读** — 实体高亮、点击即查人物/地点/物品/组织卡片
- **知识图谱** — 力导向关系图，支持按章节范围探索关系演变
- **世界地图** — 语义缩放地图，人物轨迹动画
- **时间线** — 多泳道事件时间线
- **势力图** — 组织架构与势力关系网络
- **智能问答** — 流式对话，基于原文的精准回答与来源溯源
- **百科全书** — 分类浏览所有实体，全文搜索

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React + TypeScript + Vite + Tailwind CSS + shadcn/ui |
| 状态管理 | Zustand |
| 后端 | Python + FastAPI + aiosqlite |
| 存储 | SQLite + ChromaDB |
| LLM | Ollama (本地推理) |

## 环境要求

- Node.js >= 22
- Python >= 3.9
- [Ollama](https://ollama.com/) 已安装并运行
- macOS (Apple Silicon 推荐) / Linux

## 快速开始

### 1. 启动后端

```bash
cd backend
uv sync
uv run uvicorn src.api.main:app --reload
```

后端运行在 `http://localhost:8000`，访问 `/api/health` 验证。

### 2. 启动前端

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
│       ├── api/          # FastAPI 路由 + WebSocket
│       ├── services/     # 业务逻辑
│       ├── extraction/   # LLM 抽取
│       ├── db/           # SQLite + ChromaDB
│       ├── models/       # 数据模型
│       ├── infra/        # 配置与基础设施
│       └── utils/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── app/          # App 入口 + 路由
│       ├── pages/        # 10 个页面
│       ├── components/   # UI 组件
│       ├── stores/       # Zustand 状态
│       ├── api/          # API 客户端
│       ├── hooks/
│       └── lib/
└── _bmad-output/         # 架构文档
```

## 数据存储

所有数据存储在 `~/.ai-reader-v2/` 目录下：

- `data.db` — SQLite 数据库（小说、章节、分析结果、对话等）
- `chroma/` — ChromaDB 向量数据库（语义搜索）

## License

Private
