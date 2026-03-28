# AI Reader V2 — AI 小说分析可视化工具

[![Version](https://img.shields.io/badge/version-0.64.0-blue)](https://github.com/mouseart2025/AI-Reader-V2)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![GitHub Stars](https://img.shields.io/github/stars/mouseart2025/AI-Reader-V2?style=social)](https://github.com/mouseart2025/AI-Reader-V2)
[![Python](https://img.shields.io/badge/python-≥3.9-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/node-≥22-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![React](https://img.shields.io/badge/react-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/typescript-5.9-3178c6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Ollama](https://img.shields.io/badge/ollama-supported-FF6B35)](https://ollama.com/)
[![Tauri](https://img.shields.io/badge/tauri-2-FFC131?logo=tauri&logoColor=white)](https://v2.tauri.app/)

> **[English Version](./README_EN.md)**

**开源 AI 小说分析工具** — 上传任意 TXT/Markdown 小说，AI 自动提取人物关系、地点层级、事件时间线，生成交互式知识图谱、世界地图、时间线等多维可视化。支持本地 Ollama 和云端 LLM，数据 100% 本地存储，无需联网。

适用于：网文分析、小说世界观整理、文学研究、创作辅助、角色关系梳理、剧情梳理、同人创作参考。

<p align="center">
  <a href="https://ai-reader.cc"><strong>官网</strong></a> ·
  <a href="https://ai-reader.cc/demo/honglou/graph?v=3"><strong>在线体验</strong></a> ·
  <a href="#快速开始"><strong>快速开始</strong></a> ·
  <a href="#桌面应用下载"><strong>桌面下载</strong></a>
</p>

## 核心功能

### 🕸️ 人物关系知识图谱

力导向关系网络图，自动识别 70+ 种关系类型（血亲、师徒、同盟、敌对...），六大分类着色。实体别名智能合并（孙悟空 = 美猴王 = 行者 = 齐天大圣），支持路径查找、分类过滤、边权重调节。

<img src="https://ai-reader.cc/assets/feature-graph.png" width="720" alt="人物关系图谱 - AI Reader 自动生成的小说角色关系网络" />

### 🗺️ 小说世界地图自动生成

从文本全自动构建多层级交互式地图。天界/冥界/海底/秘境多空间层、传送门连接、程序化地形（生物群落 + 河流 + 道路 + 大陆架）、人物轨迹动画回放、rough.js 手绘风格渲染。**v0.59 新增：LLM 宏观方位锚定 + 三重水域检测 + 海岸线覆盖保证 + 道路跨海过滤。**

<img src="https://ai-reader.cc/assets/feature-map.png" width="720" alt="小说世界地图 - AI 自动生成的虚构世界地图" />

### ⏳ 多泳道时间线 / 故事线视图

多源事件聚合（角色登场、物品流转、关系变迁、组织变动），智能降噪过滤，情绪基调标签，章节自动折叠。故事线泳道视图追踪多角色并行叙事线。

<img src="https://ai-reader.cc/assets/feature-timeline.png" width="720" alt="小说时间线 - 多角色叙事时间线可视化" />

### 📖 小说百科全书

五类实体分类浏览（人物/地点/物品/组织/概念），地点层级树与空间关系面板，场景索引定位原文，世界观总览。

<img src="https://ai-reader.cc/assets/feature-encyclopedia.png" width="720" alt="小说百科 - 人物地点物品组织百科全书" />

### 更多功能

- 🖥️ **桌面应用** — Tauri 2 原生桌面客户端，下载即用，全功能离线运行
- 📚 **书架管理** — 拖拽上传 .txt/.md，智能章节切分（50+ 格式），搜索排序，导入/导出/全量备份
- 🔍 **实体预扫描** — jieba 中文分词 + LLM 分类，生成高频实体词典提升提取质量
- 📖 **智能阅读** — 实体高亮（5 类着色），别名解析，书签系统，场景/剧本面板
- ⚔️ **势力图** — 组织架构与势力关系网络
- 💬 **RAG 智能问答** — 基于原文的检索增强问答，流式对话，答案来源溯源
- 📤 **设定集导出** — Markdown / Word / Excel / PDF 四种格式，可选模板
- 🤖 **多 LLM 支持** — 本地 Ollama（qwen3:8b 等）+ 10 大云端供应商（DeepSeek、MiniMax、Claude、OpenAI、Gemini 等）
- 📊 **全链路分析管线** — 实体预扫描 → 逐章提取 → 聚合 → 可视化，异步执行、暂停恢复、失败重试、Token 预算自动缩放

## 适用场景

| 场景 | 说明 |
|------|------|
| 网文/小说世界观整理 | 自动梳理人物关系、地点层级、势力分布 |
| 文学研究 | 角色关系网络分析、叙事结构可视化 |
| 创作辅助 | 设定集导出、世界观一致性检查 |
| 同人/二创参考 | 快速了解原著角色关系和世界观 |
| 读书笔记 | 阅读中高亮标注、书签、场景索引 |
| 教学演示 | 可视化展示小说结构 |

## 开发故事

📝 [全程不写一行代码，我如何用 AI 做出一个复杂的小说分析系统](https://zhuanlan.zhihu.com/p/2016598051163218226) — 从零到一的完整开发历程

## 桌面应用下载

无需配置开发环境，下载即用。内置 Python 后端，只需安装 [Ollama](https://ollama.com/) 或配置云端 API。

| 平台 | 下载 | 架构 |
|------|------|------|
| macOS | [AI Reader_0.63.3_aarch64.dmg](https://github.com/mouseart2025/AI-Reader-V2/releases/download/v0.63.3/AI.Reader_0.63.3_aarch64.dmg) | Apple Silicon (M1/M2/M3/M4) |
| Windows | [AI Reader_0.63.3_x64-setup.exe](https://github.com/mouseart2025/AI-Reader-V2/releases/download/v0.63.3/AI.Reader_0.63.3_x64-setup.exe) | x86_64 |

> **macOS 首次打开提示"已损坏"？** 在终端运行：`xattr -cr "/Applications/AI Reader.app"`，然后重新打开即可。
>
> 更多版本请查看 [Releases](https://github.com/mouseart2025/AI-Reader-V2/releases) 页面。

## 快速开始

**环境要求：** Python 3.9+ / Node.js 22+ / [uv](https://docs.astral.sh/uv/) / [Ollama](https://ollama.com/)（或云端 API）

```bash
# 1. 启动 Ollama（本地 LLM）
ollama pull qwen3:8b && ollama serve

# 2. 启动后端
cd backend && uv sync && uv run uvicorn src.api.main:app --reload

# 3. 启动前端（新终端）
cd frontend && npm install && npm run dev
```

打开 http://localhost:5173 即可使用。上传 TXT 小说 → 分析 → 查看可视化。

> 不想本地部署？试试 [在线 Demo](https://ai-reader.cc/demo/honglou/graph?v=3)，含红楼梦和西游记完整分析数据。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 19 + TypeScript 5.9 + Vite 7 + Tailwind CSS 4 + shadcn/ui |
| 桌面 | Tauri 2（Rust）+ Python sidecar（PyInstaller 打包） |
| 可视化 | D3.js + SVG（地图）/ react-force-graph-2d（图谱）/ react-leaflet（地理） |
| 状态管理 | Zustand 5 |
| 后端 | Python + FastAPI（async）+ aiosqlite |
| 数据库 | SQLite（结构化数据）+ ChromaDB（向量检索） |
| LLM | Ollama（本地）或 OpenAI 兼容 API（云端，支持 DeepSeek/MiniMax/Claude/OpenAI/Gemini 等 10 大供应商） |
| 中文 NLP | jieba 分词 + 实体预扫描 |

## 版本记录

| 版本 | 日期 | 主要更新 |
|------|------|---------|
| v0.63.3 | 2026-03-27 | 别名canonical修正(预扫描实体优先+通用词blocklist+称谓降级) + 西游记层级手动修正61处(P:95.3%) + demo数据更新 + 英文landing page + ChiNovelKE benchmark发布 + 344 tests |
| v0.63.2 | 2026-03-27 | 实体卡片500修复 + 评估基础设施(eval_dashboard+标注模板+消融脚本) + FactValidator消融开关 + 344 tests |
| v0.63.1 | 2026-03-26 | 骨架缓存(超时自动复用) + 安全阈值精细化(LLM审查驱动) + region→continent救援 + 角色共现降噪(阈值3→5+kingdom排除+跨洲过滤) + 黄金标准别名修正 + 投票间接continent推断 + 累积P:37.7%→65.6%(+27.9pp) |
| v0.63.0 | 2026-03-26 | 地点归属质量跃迁 — 拓扑质量指标(5项+黄金标准) + 传递性闭包校验 + 时序权重衰减 + 地点别名归一化 + 后缀排名扩充(11新后缀+府歧义修复) + LLM审查增强(evidence+uncertain+并发限制) + Genre-aware地点规则(3题材) + rebuild安全阈值 + kingdom→continent救援 + 骨架max_tokens修复(根因+18pp) + 344 tests |
| v0.62.0 | 2026-03-25 | Contains四层防御(FactValidator suffix rank自动修正+prompt后缀层级表+CoT逐条校验+负面示例) + LLM截断JSON自动修复 + max_tokens 8K→16K + .air导出文件名(小说名_日期) + 269 tests |
| v0.61.0 | 2026-03-24 | Prompt Registry(核心能力保护) + Scene Graph CoT空间推理 + contains方向修复(3个示例反转) + LLM输出容错(数组响应+abilities字符串) + .air导出修复(fetch+blob) + 云端免密切换 + 269 tests |
| v0.60.0 | 2026-03-24 | 数据质量体系 — ProfileQualityChecker(关系突变+自引用+参与者修复) + LLM聚合审查(opt-in) + Genre-aware验证(修仙/武侠/现实分化) + 空间悬空引用过滤 + prompt负面示例 + 云模型更新(MiniMax M2.7/Gemini 2.5/GPT-4.1) + 269 tests |
| v0.59.1 | 2026-03-24 | ProfileQualityChecker Phase 1+2 + 云模型版本更新 + 251 tests |
| v0.59.0 | 2026-03-23 | 地图质量大版本 — LLM宏观方位锚定(MacroSkeleton directions) + 三重水域检测(icon+type+parent链) + 递归归陆(3轮) + 海岸线覆盖保证(Chaikin收缩补偿) + 道路跨海过滤(land_mask采样) + Solver容量40→80 + 能量函数自适应权重 + 方向提示LLM anchor×3 + 道路性能优化(roughjs→SVG, top 150) + non-scaling-stroke海岸线 + 218 tests |
| v0.58.0 | 2026-03-23 | 跨章节空间补全(LLM gap检测+方位距离补全) + 空间尺度自适应(9级画布) + 智能重绘(层级重建+空间补全一键执行) + 约束增强(轨迹邻接+传递推导) + underwater层检测 + 父级层传播 + 海中地点自动归陆 + 192 tests |
| v0.57.0 | 2026-03-22 | 测试体系(151 tests+CI) + 大陆合并(18→5) + 道路网络(Delaunay MST) + 时间线↔地图联动(flyTo) + 全量坐标补全(824/824) + 别名 canonical 优化(3字全名优先) |
| v0.56.1 | 2026-03-21 | 桌面端 9 项修复 — Ch.X 导航 404、Tab 顺序调整、通用地名消歧扩充、CJK 字形变体归一化、空间关系中文化 |
| v0.56.0 | 2026-03-21 | 世界层级重检测 + 领地跨海过滤 + 大陆架淡化 |
| v0.55.0 | 2026-03-20 | 时间线故事线视图 + 关系图路径着色 |
| v0.54.0 | 2026-03-19 | FTUE 新用户首次体验改造 — 预装数据、AI 助手、内联配置 |
| v0.53.0 | 2026-03-18 | 章节切分大幅改进 — 文体预检测、智能推断、50+ 格式支持 |
| v0.52.0 | 2026-03-15 | 智能问答增强 + 别名合并修复 |
| v0.51.0 | 2026-03-14 | 章节切分预览增强 + Windows CI 修复 |
| v0.50.0 | 2026-03-13 | 别名爆炸修复 + Windows DLL 兼容 |
| v0.49.0 | 2026-03-12 | GitHub Actions CI/CD + 桌面安装包瘦身(218→75MB) |
| v0.48.0 | 2026-03-10 | 世界地图增强 — 冲突检测、轨迹路径点、渐进式求解 |
| v0.47.0 | 2026-03-10 | 地图质量透明化 + 桌面端个性化 |
| v0.46.0 | 2026-03-10 | 章节拆分修复 + VoT 空间推理增强 |
| v0.45.0 | 2026-03-08 | 文档系统（13 页产品文档） |
| v0.44.0 | 2026-03-08 | Tauri 2 桌面应用 + Python sidecar 集成 |
| v0.43.0 | 2026-03-06 | .air 分析数据导出/导入 + 小说概览 |

<details>
<summary>更早版本</summary>

| 版本 | 日期 | 主要更新 |
|------|------|---------|
| v0.42.0 | 2026-02-28 | 导出功能升级 — 4 格式模板选择器 |
| v0.41.0 | 2026-02-26 | 书架升级 — 搜索排序、拖拽上传 |
| v0.40.0 | 2026-02-24 | 阅读页升级 — 实体高亮、场景面板 |
| v0.39.0 | 2026-02-22 | 关系图升级 — 分类过滤、暗色适配 |
| v0.38.0 | 2026-02-20 | 时间线升级 — 智能降噪、关系变化事件 |
| v0.37.0 | 2026-02-18 | 百科升级 — 实体卡片、场景索引 |
| v0.36.0 | 2026-02-16 | 地图绘制优化 — 海岸线、子节点分散 |
| v0.35.0 | 2026-02-14 | 地图层级 — LLM 自我反思验证 |

</details>

## 文档

- 📋 [贡献指南](./CONTRIBUTING.md) — 开发环境搭建、代码规范、PR 流程
- 🏗️ [技术架构](./CLAUDE.md) — 完整架构设计、代码约定、数据模型
- 💼 [商业许可](./LICENSE-COMMERCIAL.md) — 商业使用条款

## License

[GNU Affero General Public License v3.0](./LICENSE) (AGPL-3.0)

个人、教育和研究用途免费。商业闭源部署请参阅 [商业许可](./LICENSE-COMMERCIAL.md)。

---

**关键词：** 小说分析工具 / 网文分析 / AI 阅读器 / 知识图谱生成 / 人物关系图 / 小说世界地图 / 时间线可视化 / NLP 文本分析 / LLM 应用 / Ollama / 中文小说 / 网络小说工具 / 角色关系梳理 / 世界观整理 / novel analysis / knowledge graph / character relationship
