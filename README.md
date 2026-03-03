# AI Reader V2

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/python-≥3.9-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/node-≥22-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![React](https://img.shields.io/badge/react-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/typescript-5.9-3178c6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Ollama](https://img.shields.io/badge/ollama-supported-FF6B35)](https://ollama.com/)

**本地部署的智能小说阅读理解系统。** 利用 LLM 将小说文本转化为结构化知识图谱，提供关系图、多层级世界地图、时间线等多维可视化，以及基于原文的智能问答。数据全部存储在本机。

<!-- TODO: 在线体验 Demo（Epic C2 完成后启用）-->
<!-- [在线体验 Demo](https://demo.ai-reader.com) -->

## 核心亮点 Highlights

**🗺️ 多层级世界地图** — 从小说文本全自动生成交互式地图。宏观区域划分、天界/冥界/洞府多空间层、传送门连接、程序化地形生成（生物群落 + 河流水系）、人物轨迹动画、rough.js 手绘风格渲染。

**🕸️ 智能知识图谱** — 力导向人物关系图，自动识别 70+ 种关系类型，六大分类着色（血亲/亲密/师承/社交/敌对/其他），实体别名自动合并（孙悟空 = 美猴王 = 行者 = 齐天大圣）。

**🤖 多 LLM 后端** — 支持本地 Ollama 和 10 大云端供应商（DeepSeek、Anthropic/Claude、OpenAI、Google Gemini 等），Token 预算根据模型上下文窗口自动缩放。

**📊 全链路分析** — 实体预扫描词典 → 章节结构化提取 → 关系/地点/事件聚合 → 多维可视化，全程异步、可暂停恢复、失败自动重试。

<!-- TODO: 功能截图展示 -->
<!-- ![功能截图](docs/screenshots/overview.png) -->

## 功能列表 Features

- 📚 **书架管理** — 拖拽上传 .txt/.md，自动章节切分，搜索排序，导入/导出/全量备份
- 🔍 **实体预扫描** — jieba 统计 + LLM 分类，生成高频实体词典提升提取质量
- 📖 **智能阅读** — 实体高亮（5 类着色），别名解析，书签系统，场景面板，快捷键导航
- 🕸️ **知识图谱** — 力导向关系图，关系分类过滤，组织归属推断，暗色模式适配
- 🗺️ **世界地图** — 多层级结构，约束求解布局，手绘风格渲染，轨迹动画，高清导出
- ⏳ **时间线** — 多泳道事件线，智能降噪，关系变化事件，情绪基调标签
- ⚔️ **势力图** — 组织架构与势力关系网络
- 📖 **百科全书** — 分类浏览，全文搜索，地点层级树，空间关系面板，场景索引
- 💬 **智能问答** — RAG 检索增强，流式对话，答案来源溯源
- 📤 **设定集导出** — Markdown / Word / Excel / PDF 四种格式，两种模板
- 📊 **质量监控** — 实时 ETA，性能测试，模型质量对比

## 快速开始 Quick Start

**环境要求：** Python 3.9+ / Node.js 22+ / [uv](https://docs.astral.sh/uv/) / [Ollama](https://ollama.com/)（或云端 API）

```bash
# 1. 启动 Ollama
ollama pull qwen3:8b && ollama serve

# 2. 启动后端
cd backend && uv sync && uv run uvicorn src.api.main:app --reload

# 3. 启动前端（新终端）
cd frontend && npm install && npm run dev
```

打开 http://localhost:5173 即可使用。

## 技术栈 Tech Stack

| 层 | 技术 |
|----|------|
| 前端 | React 19 + TypeScript 5.9 + Vite 7 + Tailwind CSS 4 + shadcn/ui |
| 可视化 | D3.js + SVG（地图）/ react-force-graph-2d（图谱）/ react-leaflet（地理） |
| 状态管理 | Zustand 5 |
| 后端 | Python + FastAPI + aiosqlite |
| 数据库 | SQLite + ChromaDB |
| LLM | Ollama（本地）或 OpenAI 兼容 API（云端，支持 10 大供应商） |
| 中文 NLP | jieba |

## 文档 Documentation

- 📋 [贡献指南 Contributing](./CONTRIBUTING.md) — 开发环境搭建、代码规范、PR 流程
- 🏗️ [技术架构 Architecture](./CLAUDE.md) — 完整架构设计、代码约定、数据模型
- 💼 [商业许可 Commercial License](./LICENSE-COMMERCIAL.md) — 商业使用条款

## License

[GNU Affero General Public License v3.0](./LICENSE) (AGPL-3.0)

个人、教育和研究用途免费。商业闭源部署请参阅 [商业许可](./LICENSE-COMMERCIAL.md)。

当前版本：**v0.43.0** · 40 个 Epic · 208 个 Story · 全部完成
