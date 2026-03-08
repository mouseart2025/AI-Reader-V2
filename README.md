# AI Reader V2

[![Version](https://img.shields.io/badge/version-0.44.0-blue)](https://github.com/mouseart2025/AI-Reader-V2)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![GitHub Stars](https://img.shields.io/github/stars/mouseart2025/AI-Reader-V2?style=social)](https://github.com/mouseart2025/AI-Reader-V2)
[![Python](https://img.shields.io/badge/python-≥3.9-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/node-≥22-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![React](https://img.shields.io/badge/react-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/typescript-5.9-3178c6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Ollama](https://img.shields.io/badge/ollama-supported-FF6B35)](https://ollama.com/)
[![Tauri](https://img.shields.io/badge/tauri-2-FFC131?logo=tauri&logoColor=white)](https://v2.tauri.app/)

**本地部署的智能小说阅读理解系统** — 利用 LLM 将小说文本转化为结构化知识图谱，提供关系图、多层级世界地图、时间线等多维可视化，以及基于原文的智能问答。数据全部存储在本机。

**LLM-powered novel analysis system** — transforms novels into structured knowledge graphs with relationship maps, multi-layer world maps, timelines, and RAG-based Q&A. All data stays local.

<p align="center">
  <a href="https://ai-reader.cc"><strong>官网 Website</strong></a> ·
  <a href="https://ai-reader.cc/demo/honglou/graph?v=3"><strong>在线体验 Live Demo</strong></a> ·
  <a href="#快速开始-quick-start"><strong>快速开始 Quick Start</strong></a>
</p>

## 核心功能 Features

### 🕸️ 智能知识图谱

力导向人物关系图，自动识别 70+ 种关系类型，六大分类着色（血亲/亲密/师承/社交/敌对/其他），实体别名自动合并（孙悟空 = 美猴王 = 行者 = 齐天大圣）。

<img src="https://ai-reader.cc/assets/feature-graph.png" width="720" alt="知识图谱 Knowledge Graph" />

### 🗺️ 多层级世界地图

从小说文本全自动生成交互式地图。宏观区域划分、天界/冥界/洞府多空间层、传送门连接、程序化地形生成（生物群落 + 河流水系）、人物轨迹动画、rough.js 手绘风格渲染。

<img src="https://ai-reader.cc/assets/feature-map.png" width="720" alt="世界地图 World Map" />

### ⏳ 多泳道时间线

多源事件聚合（角色登场、物品流转、关系变迁、组织变动），智能降噪过滤，情绪基调标签，章节自动折叠。

<img src="https://ai-reader.cc/assets/feature-timeline.png" width="720" alt="时间线 Timeline" />

### 📖 百科全书

五类实体分类浏览（人物/地点/物品/组织/概念），地点层级树与空间关系面板，场景索引，世界观总览。

<img src="https://ai-reader.cc/assets/feature-encyclopedia.png" width="720" alt="百科全书 Encyclopedia" />

### 更多功能

- 🖥️ **桌面应用** — Tauri 2 原生桌面客户端，Python sidecar 自动启动，全功能离线运行
- 📚 **书架管理** — 拖拽上传 .txt/.md，自动章节切分，搜索排序，导入/导出/全量备份
- 🔍 **实体预扫描** — jieba 统计 + LLM 分类，生成高频实体词典提升提取质量
- 📖 **智能阅读** — 实体高亮（5 类着色），别名解析，书签系统，场景/剧本面板，快捷键导航
- ⚔️ **势力图** — 组织架构与势力关系网络
- 💬 **智能问答** — RAG 检索增强，流式对话，答案来源溯源
- 📤 **设定集导出** — Markdown / Word / Excel / PDF 四种格式，两种模板
- 🤖 **多 LLM 后端** — 本地 Ollama + 10 大云端供应商（DeepSeek、Claude、OpenAI、Gemini 等），Token 预算自动缩放
- 📊 **全链路分析** — 实体预扫描 → 章节提取 → 聚合 → 可视化，异步执行、暂停恢复、失败重试

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

> 不想本地部署？试试 [在线 Demo](https://ai-reader.cc/demo/honglou/graph?v=3)，含红楼梦和西游记完整分析数据。

## 技术栈 Tech Stack

| 层 | 技术 |
|----|------|
| 前端 | React 19 + TypeScript 5.9 + Vite 7 + Tailwind CSS 4 + shadcn/ui |
| 桌面 | Tauri 2（Rust）+ Python sidecar（PyInstaller 打包） |
| 可视化 | D3.js + SVG（地图）/ react-force-graph-2d（图谱）/ react-leaflet（地理） |
| 状态管理 | Zustand 5 |
| 后端 | Python + FastAPI + aiosqlite |
| 数据库 | SQLite + ChromaDB |
| LLM | Ollama（本地）或 OpenAI 兼容 API（云端，支持 10 大供应商） |
| 中文 NLP | jieba |

## 版本记录 Changelog

| 版本 | 日期 | 主要更新 |
|------|------|---------|
| v0.44.0 | 2026-03-08 | Tauri 2 桌面应用 + Python sidecar 集成，自定义图标，全功能离线运行 |
| v0.43.0 | 2026-03-06 | .air 分析数据导出/导入，小说概览卡片，LLM 自动生成简介，Demo 阅读模式 |
| v0.42.0 | 2026-02-28 | 导出功能升级 — 4 格式 (MD/Word/PDF/Excel)，章节范围选择，模板选择器 |
| v0.41.0 | 2026-02-26 | 书架升级 — 搜索排序、拖拽上传、进度指示、.air 导入导出 |
| v0.40.0 | 2026-02-24 | 阅读页升级 — 实体高亮、场景面板、书签系统、快捷键导航 |
| v0.39.0 | 2026-02-22 | 关系图升级 — 分类过滤、边权重、标签碰撞检测、暗色适配 |
| v0.38.0 | 2026-02-20 | 时间线升级 — 智能降噪、关系变化事件、情绪基调链接 |
| v0.37.0 | 2026-02-18 | 百科升级 — 实体卡片、场景索引、世界观 Tab、地点层级树 |
| v0.36.0 | 2026-02-16 | 地图绘制优化 — 海岸线稳定、子节点分散、大领地去填充 |
| v0.35.0 | 2026-02-14 | 地图层级 — LLM 自我反思验证 + Container/Peers 分离 |

## 文档 Documentation

- 📋 [贡献指南 Contributing](./CONTRIBUTING.md) — 开发环境搭建、代码规范、PR 流程
- 🏗️ [技术架构 Architecture](./CLAUDE.md) — 完整架构设计、代码约定、数据模型
- 💼 [商业许可 Commercial License](./LICENSE-COMMERCIAL.md) — 商业使用条款

## License

[GNU Affero General Public License v3.0](./LICENSE) (AGPL-3.0)

个人、教育和研究用途免费。商业闭源部署请参阅 [商业许可](./LICENSE-COMMERCIAL.md)。
