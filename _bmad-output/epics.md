---
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - PRD.md
  - _bmad-output/architecture.md
  - interaction-design/01-bookshelf.excalidraw
  - interaction-design/02-reading.excalidraw
  - interaction-design/03-graph.excalidraw
  - interaction-design/04-map.excalidraw
  - interaction-design/05-timeline.excalidraw
  - interaction-design/06-factions.excalidraw
  - interaction-design/07-chat.excalidraw
  - interaction-design/08-encyclopedia.excalidraw
  - interaction-design/09-analysis.excalidraw
  - interaction-design/10-settings.excalidraw
---

# AI-Reader-V2 - Epic 与 Story 拆分

## 概述

本文档将 PRD 的 83 个功能需求和架构决策拆分为可实施的 Epic 和 Story，用于指导开发实现。每个 Epic 交付完整的用户价值，每个 Story 可由单个开发 Agent 独立完成。

## 需求清单

### 功能需求 (FR)

**书架管理**
- FR-01 (F-01): 上传小说 (.txt / .md)，自动切分章节，显示预览页，确认后导入书架 [P0]
- FR-02 (F-02): 书架列表展示，显示封面、书名、分析进度、阅读进度 [P0]
- FR-03 (F-03): 删除小说，二次确认后删除小说及全部关联数据 [P0]
- FR-04 (F-04): 书架搜索/排序，支持按书名搜索，按时间/书名排序 [P1]
- FR-05 (F-05): 重复上传检测，同名小说上传时显示对比面板 [P1]
- FR-06 (F-06): 章节切分调整，预览页支持切换切分模式、增删切分点 [P1]

**小说阅读**
- FR-10 (F-10): 章节阅读，渲染章节文本，支持上下章翻页 [P0]
- FR-11 (F-11): 章节目录导航，侧边栏树形章节目录，支持卷>章多级折叠 [P0]
- FR-12 (F-12): 实体高亮，已分析章节中实体名按类别颜色高亮 [P0]
- FR-13 (F-13): 实体卡片弹出，点击高亮实体弹出对应卡片 [P0]
- FR-14 (F-14): 阅读进度记忆，记住每本书最后阅读章节 [P0]
- FR-15 (F-15): 章节引用跳转，卡片/问答中的章节引用可点击跳转 [P1]
- FR-16 (F-16): 阅读设置，字号、行距、主题调节 [P1]
- FR-17 (F-17): 全文搜索，在当前小说所有章节中搜索关键词 [P1]

**实体卡片**
- FR-20 (F-20): 人物卡片，展示基本信息、关系、能力、经历等 7 个区块 [P0]
- FR-21 (F-21): 地点卡片，展示基本信息、空间层级、到访人物、事件等 6 个区块 [P0]
- FR-22 (F-22): 物品卡片，展示基本信息、持有流转、使用记录等 6 个区块 [P1]
- FR-23 (F-23): 组织卡片，展示基本信息、成员、据点、大事记等 7 个区块 [P1]
- FR-24 (F-24): 卡片内截断与展开，各区块按规则截断 [P0]
- FR-25 (F-25): 卡片间互相跳转，卡片内实体名可点击跳转 [P0]
- FR-26 (F-26): 概念浮层，点击概念高亮弹出定义浮层 [P1]

**知识图谱可视化**
- FR-30 (F-30): 人物关系图，力导向图展示人物关系 [P0]
- FR-31 (F-31): 世界地图-空间视图，语义缩放和人物轨迹 [P1]
- FR-32 (F-32): 世界地图-层级视图，树状展示地点包含关系 [P0]
- FR-33 (F-33): 时间线，水平轴展示事件分布 [P1]
- FR-34 (F-34): 势力图，组织关系网络图 [P2]
- FR-35 (F-35): 章节范围滑块，全局滑块控制所有视图数据范围 [P0]
- FR-36 (F-36): 视图联动，四个视图之间数据联动 [P1]
- FR-37 (F-37): 筛选面板，各视图支持按类型/属性筛选 [P0]
- FR-38 (F-38): 路径查找，在关系图中查找两人物间最短路径 [P1]
- FR-39 (F-39): 轨迹动画播放，空间地图上人物轨迹动画播放 [P2]
- FR-40 (F-40): 手动调整地图布局，拖拽调整地点位置并持久化 [P2]

**智能问答**
- FR-50 (F-50): 自然语言问答，输入问题返回基于知识图谱的答案 [P0]
- FR-51 (F-51): 答案来源标注，每个答案标注来源章节 [P0]
- FR-52 (F-52): 答案中实体高亮，答案文本中实体名高亮可点击 [P0]
- FR-53 (F-53): 对话上下文，支持连续追问 [P1]
- FR-54 (F-54): 浮动面板问答，底部常驻输入框 [P0]
- FR-55 (F-55): 对话历史，保存/切换/删除历史对话 [P1]
- FR-56 (F-56): 对话导出，将对话导出为 Markdown [P2]

**小说分析**
- FR-60 (F-60): 全书分析，一键触发全书分析 [P0]
- FR-61 (F-61): 分析进度展示，显示进度条、当前章节、统计 [P0]
- FR-62 (F-62): 暂停/恢复分析 [P0]
- FR-63 (F-63): 取消分析，终止分析任务 [P0]
- FR-64 (F-64): 按范围分析，指定章节区间 [P1]
- FR-65 (F-65): 重新分析，对已分析章节重新分析 [P1]
- FR-66 (F-66): 按需分析，阅读到未分析章节时自动触发 [P2]

**百科**
- FR-70 (F-70): 百科页面，分类展示所有实体和概念 [P1]
- FR-71 (F-71): 概念词条，展示概念定义、原文摘录 [P1]
- FR-72 (F-72): 概念自动分类 [P1]

**系统**
- FR-80 (F-80): 首次使用引导，检测 Ollama 和模型状态 [P0]
- FR-81 (F-81): 设置页面，阅读设置、LLM 配置、数据管理 [P1]
- FR-82 (F-82): 分析数据导出 [P1]
- FR-83 (F-83): 分析数据导入 [P1]

### 非功能需求 (NFR)

- NFR-01: 完全本地部署，无需联网
- NFR-02: macOS 12.0+ (Apple Silicon M1/M2/M3/M4)
- NFR-03: 浏览器兼容 Chrome 100+ / Safari 16+ / Edge 100+
- NFR-04: 章节分析速度 ≤ 30s/章 (M1 16GB)，≤ 50s/章 (M1 8GB)
- NFR-05: 问答首字 ≤ 3s（流式输出）
- NFR-06: 阅读页加载 ≤ 1s（章节切换）
- NFR-07: 图谱渲染 ≤ 3s（500 节点流畅）
- NFR-08: 内存占用 ≤ 7GB（含模型，8GB 设备）
- NFR-09: 数据本地持久化，应用关闭不丢失
- NFR-10: 完全隐私，无遥测、无外部请求
- NFR-11: 支持导入导出单本小说分析数据

### 架构要求

- ARCH-01: 前后端分离 — React 18 + TypeScript (Vite) 前端 + Python FastAPI 后端
- ARCH-02: 两数据库 — SQLite (主存储) + ChromaDB (向量检索)
- ARCH-03: ChapterFact 结构化抽取 — 每章一次 LLM 调用产出 ChapterFact JSON
- ARCH-04: 聚合按需计算 — 实体档案从 ChapterFact 实时聚合，不持久化
- ARCH-05: Ollama + Qwen 2.5 本地 LLM 推理
- ARCH-06: WebSocket — 分析进度推送 + 流式问答
- ARCH-07: 可视化引擎 — @react-force-graph-2d / Pixi.js v8 / 自研 Canvas 时间线
- ARCH-08: 状态管理 — Zustand stores
- ARCH-09: UI — shadcn/ui + Tailwind CSS

### FR 覆盖映射

| FR | Epic |
|----|------|
| FR-01, FR-02, FR-03 | Epic 1 (书架与上传) |
| FR-04, FR-05, FR-06 | Epic 1 (书架与上传) |
| FR-80 | Epic 1 (书架与上传) |
| FR-60, FR-61, FR-62, FR-63 | Epic 2 (小说分析) |
| FR-64, FR-65, FR-66 | Epic 2 (小说分析) |
| FR-10, FR-11, FR-14 | Epic 3 (阅读与实体) |
| FR-12, FR-13, FR-15, FR-16, FR-17 | Epic 3 (阅读与实体) |
| FR-20, FR-21, FR-24, FR-25 | Epic 3 (阅读与实体) |
| FR-22, FR-23, FR-26 | Epic 3 (阅读与实体) |
| FR-30, FR-32, FR-35, FR-37 | Epic 4 (可视化) |
| FR-31, FR-33, FR-36, FR-38 | Epic 4 (可视化) |
| FR-34, FR-39, FR-40 | Epic 4 (可视化) |
| FR-50, FR-51, FR-52, FR-54 | Epic 5 (智能问答) |
| FR-53, FR-55, FR-56 | Epic 5 (智能问答) |
| FR-70, FR-71, FR-72 | Epic 6 (百科与设置) |
| FR-81, FR-82, FR-83 | Epic 6 (百科与设置) |

## Epic 列表

### Epic 1: 书架与小说上传
用户可以上传小说文件，系统自动切分章节，通过书架界面管理所有导入的小说。首次使用时系统引导用户完成环境配置。
**FRs:** FR-01, FR-02, FR-03, FR-04, FR-05, FR-06, FR-80

### Epic 2: 小说分析引擎
用户可以对已上传的小说触发 AI 分析，系统逐章提取结构化事实（ChapterFact），实时展示分析进度，支持暂停/恢复/取消操作。
**FRs:** FR-60, FR-61, FR-62, FR-63, FR-64, FR-65, FR-66

### Epic 3: 阅读体验与实体卡片
用户可以阅读小说章节，已分析章节中的实体名自动高亮，点击可查看详细的实体卡片（人物/地点/物品/组织/概念）。
**FRs:** FR-10, FR-11, FR-12, FR-13, FR-14, FR-15, FR-16, FR-17, FR-20, FR-21, FR-22, FR-23, FR-24, FR-25, FR-26

### Epic 4: 知识图谱可视化
用户可以通过人物关系图、世界地图、时间线、势力图四种可视化视图从全局视角探索小说结构，所有视图通过章节范围滑块联动。
**FRs:** FR-30, FR-31, FR-32, FR-33, FR-34, FR-35, FR-36, FR-37, FR-38, FR-39, FR-40

### Epic 5: 智能问答
用户可以用自然语言对小说内容提问，系统基于知识图谱和原文检索给出流式回答，标注来源章节，支持连续追问。
**FRs:** FR-50, FR-51, FR-52, FR-53, FR-54, FR-55, FR-56

### Epic 6: 百科与系统设置
用户可以浏览小说的结构化知识百科（实体索引 + 概念词条），配置系统设置，导入/导出分析数据。
**FRs:** FR-70, FR-71, FR-72, FR-81, FR-82, FR-83

---

## Epic 1: 书架与小说上传

用户可以上传小说文件，系统自动切分章节，通过书架界面管理所有导入的小说。首次使用时系统引导用户完成环境配置。

### Story 1.1: 搭建前后端项目骨架

As a 开发者,
I want 搭建完整的前后端项目结构和开发环境,
So that 后续所有功能开发都有统一的基础框架。

**Acceptance Criteria:**

**Given** 空的项目仓库
**When** 执行项目初始化
**Then** 前端项目使用 Vite + React 18 + TypeScript 创建，配置 Tailwind CSS 和 shadcn/ui
**And** 后端项目使用 FastAPI 创建，配置 uvicorn + aiosqlite + chromadb 依赖
**And** SQLite 数据库初始化脚本包含 architecture.md 定义的全部 8 张表和索引
**And** 前端开发服务器 (`npm run dev`) 和后端服务器 (`uvicorn`) 均可正常启动
**And** 前端配置代理，将 `/api` 和 `/ws` 请求转发到后端
**And** 项目目录结构与 architecture.md 6.6 和 6.7 节一致
**And** 配置 CORS 允许本地开发跨域
**And** 后端包含健康检查端点 `GET /api/health`

**技术说明:**
- 前端: `npm create vite@latest frontend -- --template react-ts`
- 后端目录: `backend/src/`
- SQLite 数据库文件: `~/.ai-reader-v2/data.db`
- ChromaDB 数据目录: `~/.ai-reader-v2/chroma/`

---

### Story 1.2: 实现小说上传与章节切分

As a 用户,
I want 上传一个 .txt 或 .md 文件，系统自动切分章节并显示预览,
So that 我可以将小说导入系统进行后续分析。

**Acceptance Criteria:**

**Given** 系统已启动
**When** 用户通过上传接口提交一个 .txt 或 .md 文件
**Then** 后端自动检测文件编码（支持 UTF-8、GBK、GB2312）
**And** 按优先级尝试 5 种章节切分模式（"第X章"、"第X回"、数字序号、Markdown 标题、分隔线）
**And** 提取书名（从文件名）和作者（从内容匹配"作者：XXX"）
**And** 返回预览数据：章节列表（序号+标题+字数）、总章节数、总字数、书名、作者
**And** 如果切分结果异常（仅 1 章或单章超 5 万字），返回中包含 warning 字段
**And** 文件超过 100MB 时返回 warning 提示文件较大

**Given** 预览数据已返回
**When** 用户确认导入
**Then** 小说和全部章节写入数据库，novels 记录包含 file_hash（SHA256）
**And** 返回创建的 novel 对象

**API:**
- `POST /api/novels/upload` — 上传文件，返回预览
- `POST /api/novels/confirm` — 确认导入

---

### Story 1.3: 实现书架页面

As a 用户,
I want 在书架页面看到所有已导入的小说，并能进行基本管理操作,
So that 我可以快速找到和打开想要阅读/分析的小说。

**Acceptance Criteria:**

**Given** 系统中已有导入的小说
**When** 用户访问书架页面（`/`）
**Then** 以卡片列表形式展示所有小说，每张卡片显示：封面（书名+作者生成）、书名、作者、章节数、分析进度条、阅读进度、最后打开时间
**And** 默认按最后打开时间排序

**Given** 书架页面已加载
**When** 用户点击某本小说卡片
**Then** 跳转到该小说的阅读页面 `/novel/:id/read`

**Given** 书架中有多本小说
**When** 用户在搜索框输入关键词
**Then** 按书名和作者实时过滤小说列表（FR-04）

**Given** 用户右键点击或长按小说卡片
**When** 选择"删除"
**Then** 弹出二次确认对话框，确认后删除小说及全部关联数据（FR-03）

**Given** 书架为空
**When** 用户访问书架页面
**Then** 显示空状态引导：居中提示"还没有导入小说"+ 醒目上传按钮

**API:**
- `GET /api/novels` — 获取小说列表
- `DELETE /api/novels/{id}` — 删除小说

---

### Story 1.4: 实现上传预览与确认流程（前端）

As a 用户,
I want 在上传后看到章节切分预览，确认书名和作者后导入,
So that 我可以在导入前检查切分结果是否合理。

**Acceptance Criteria:**

**Given** 用户在书架页点击上传按钮
**When** 选择 .txt 或 .md 文件
**Then** 显示上传进度

**Given** 后端返回预览数据
**When** 预览页加载
**Then** 显示书名和作者输入框（可编辑），章节列表（序号+标题+字数），总章节数和总字数
**And** 如果预览数据包含 warning，显示黄色警告提示

**Given** 预览页已展示
**When** 用户点击"确认导入"
**Then** 调用确认接口，成功后跳转回书架页，新小说出现在列表顶部

**Given** 用户选择了非 .txt/.md 文件
**When** 上传
**Then** 即时提示"仅支持 .txt / .md 格式"

---

### Story 1.5: 实现重复检测与排序功能

As a 用户,
I want 上传同名小说时看到对比面板，并能按不同方式排序书架,
So that 我可以避免重复导入并快速找到想要的小说。

**Acceptance Criteria:**

**Given** 用户上传一本与已有小说同名的文件
**When** 预览页显示
**Then** 额外显示对比面板：已有版本和新上传版本的章节数、字数、导入时间
**And** 提供三个选项："覆盖已有版本"、"作为新书导入"、"取消"（FR-05）
**And** 选择"覆盖"会删除旧版本的全部分析数据

**Given** 书架页面
**When** 用户切换排序方式
**Then** 支持按"最后打开时间"、"书名"、"章节数"排序（FR-04）

---

### Story 1.6: 实现首次使用引导

As a 首次使用的用户,
I want 系统检测环境并引导我完成配置,
So that 我可以顺利使用系统的 AI 分析功能。

**Acceptance Criteria:**

**Given** 用户首次打开应用
**When** 应用加载
**Then** 系统自动检测 Ollama 是否已安装并运行
**And** 检测所需模型（qwen2.5:7b）是否已下载

**Given** Ollama 未安装或未运行
**When** 检测结果返回
**Then** 显示引导页面，分步骤指导：安装 Ollama → 启动服务 → 下载模型

**Given** 环境检测通过
**When** 进入应用
**Then** 直接进入空书架页面

**API:**
- `GET /api/settings/health-check` — 检测 Ollama 状态和模型列表

---

### Story 1.7: 实现章节切分调整

As a 用户,
I want 在预览页调整章节切分方式,
So that 自动切分不理想时我可以手动修正。

**Acceptance Criteria:**

**Given** 上传预览页已展示
**When** 用户点击"调整切分"
**Then** 显示切分模式选择（5 种模式），可选择切换模式重新切分（FR-06）
**And** 支持在章节列表中删除或新增切分点
**And** 支持输入自定义正则表达式重新切分
**And** 调整后实时更新预览列表

---

## Epic 2: 小说分析引擎

用户可以对已上传的小说触发 AI 分析，系统逐章提取结构化事实（ChapterFact），实时展示分析进度，支持暂停/恢复/取消操作。

### Story 2.1: 实现 LLM 客户端与 ChapterFact 抽取器

As a 系统,
I want 通过 Ollama API 调用 LLM 按章节提取结构化事实,
So that 每章产出一个完整的 ChapterFact JSON。

**Acceptance Criteria:**

**Given** Ollama 服务运行中，Qwen 2.5 模型已下载
**When** 调用 ChapterFactExtractor.extract(chapter_text, context_summary)
**Then** 发送包含 system prompt、前序上下文摘要和章节全文的请求
**And** 要求 LLM 以 ChapterFact JSON Schema 格式输出（structured output）
**And** 返回解析后的 ChapterFact 对象，包含 characters、relationships、locations、item_events、org_events、events、new_concepts 七个数组

**Given** LLM 返回的 JSON 格式不正确
**When** 解析失败
**Then** 重试一次（修正提示），再次失败则标记该章为 failed

**Given** LLM 调用超时（>120s）
**When** 超时发生
**Then** 记录错误并标记该章为 failed，可后续重试

**技术说明:**
- 实现 `LLMClient` 类（Ollama HTTP API 封装）
- 实现 `ChapterFactExtractor` 类
- 实现 `FactValidator` 类（轻量后验证：名称长度、类型合法性）
- 编写 system prompt 和 few-shot 示例

---

### Story 2.2: 实现前序上下文摘要生成器

As a 系统,
I want 为每章分析自动生成前序章节的上下文摘要,
So that LLM 在分析当前章节时能识别已知实体和关系。

**Acceptance Criteria:**

**Given** 小说前 N 章已有 ChapterFact 数据
**When** 调用 ContextSummaryBuilder.build(novel_id, chapter_num)
**Then** 从已有 ChapterFact 聚合生成摘要，包含：
  - 已知人物列表（名称 + 当前身份/境界）
  - 已知关系（人物对 + 最新关系类型）
  - 已知地点（名称 + 类型 + 层级）
  - 已知物品（名称 + 当前持有人）
**And** 只包含最近 N 章出现过的活跃实体（非活跃实体淡出）
**And** 总量控制在 2000 token 以内

**Given** 分析第 1 章（无前序数据）
**When** 调用 build
**Then** 返回空摘要或仅包含"这是小说的第一章"提示

---

### Story 2.3: 实现分析任务管理与进度推送

As a 用户,
I want 触发全书分析后看到实时进度，并能暂停/恢复/取消,
So that 我可以掌控分析过程而不必等待完成。

**Acceptance Criteria:**

**Given** 用户选择一本已上传的小说
**When** 触发全书分析
**Then** 创建 analysis_task 记录（status=running）
**And** 后台开始逐章处理：前序摘要 → LLM 抽取 → 验证 → 写入 chapter_facts → 更新 chapters.analysis_status

**Given** 分析正在进行
**When** 通过 WebSocket `/ws/analysis/{novel_id}` 连接
**Then** 每完成一章推送进度消息：`{ type: "progress", chapter, total, stats: { entities, relations, events } }`
**And** 每章完成推送：`{ type: "chapter_done", chapter, status }`

**Given** 分析正在进行
**When** 用户发送暂停请求 `PATCH /api/analysis/{task_id}` `{status: "paused"}`
**Then** 当前章节处理完后退出循环，任务状态变为 paused（FR-62）
**And** WebSocket 推送 `{ type: "task_status", status: "paused" }`

**Given** 任务已暂停
**When** 用户发送恢复请求 `{status: "running"}`
**Then** 从 current_chapter + 1 继续分析

**Given** 分析正在进行
**When** 用户发送取消请求 `{status: "cancelled"}`
**Then** 任务终止，已分析数据保留（FR-63）

**API:**
- `POST /api/novels/{novel_id}/analyze` — 触发分析
- `PATCH /api/analysis/{task_id}` — 暂停/恢复/取消
- `GET /api/analysis/{task_id}` — 查询任务状态
- `WS /ws/analysis/{novel_id}` — 进度推送

---

### Story 2.4: 实现分析页面（前端）

As a 用户,
I want 在分析页面查看进度、触发分析、管理分析任务,
So that 我可以直观地管理小说的分析过程。

**Acceptance Criteria:**

**Given** 用户进入分析页面 `/novel/:id/analysis`
**When** 小说未分析
**Then** 显示"开始分析"按钮，点击触发全书分析

**Given** 分析正在进行
**When** 页面已加载
**Then** 通过 WebSocket 连接实时显示：进度条、当前章节/总章节、已提取实体/关系/事件数
**And** 显示"暂停"和"取消"按钮

**Given** 分析已完成
**When** 页面已加载
**Then** 显示分析完成状态和统计信息（总实体数、关系数、事件数、耗时）

**Given** 某些章节分析失败
**When** 页面已加载
**Then** 显示失败章节列表，每个失败章节提供"重试"按钮

---

### Story 2.5: 实现按范围分析与重新分析

As a 用户,
I want 指定章节范围进行分析或重新分析已分析章节,
So that 我可以灵活控制分析范围和更新分析质量。

**Acceptance Criteria:**

**Given** 分析页面
**When** 用户选择章节范围（起始章-结束章）并点击"分析"
**Then** 仅分析指定范围内未分析的章节（FR-64）

**Given** 分析页面
**When** 用户选择已分析的章节范围并点击"重新分析"
**Then** 弹出确认对话框，确认后重新抽取并覆盖已有 ChapterFact 数据（FR-65）
**And** 聚合缓存按 novel_id 失效

---

## Epic 3: 阅读体验与实体卡片

用户可以阅读小说章节，已分析章节中的实体名自动高亮，点击可查看详细的实体卡片（人物/地点/物品/组织/概念），卡片间支持导航跳转。

### Story 3.1: 实现阅读页面基础功能

As a 用户,
I want 阅读小说章节并通过目录导航,
So that 我可以像读书一样浏览小说内容。

**Acceptance Criteria:**

**Given** 用户点击书架中的某本小说
**When** 进入阅读页 `/novel/:id/read`
**Then** 显示小说的第一章（或上次阅读位置）内容
**And** 左侧显示章节目录侧栏：
  - 有卷结构的按卷>章两级折叠，卷标题显示分析进度（如"5/10 ✓"）
  - 无卷结构的退化为平铺章节列表
  - 当前章节高亮并自动展开所在卷
  - 每章标注分析状态图标（绿色=已分析、黄色=分析中、红色=失败、灰色=未分析）
  - 顶部章节搜索框
  - 侧栏可通过按钮折叠

**Given** 阅读页已加载
**When** 用户点击章节目录中的某章
**Then** 主区域切换到该章内容（FR-11）

**Given** 阅读页已加载
**When** 用户点击"下一章"/"上一章"按钮
**Then** 跳转到相邻章节（FR-10）

**Given** 用户阅读某章
**When** 离开页面或切换小说
**Then** 保存当前章节和滚动位置到 user_state 表，下次打开自动恢复（FR-14）

**API:**
- `GET /api/novels/{id}/chapters` — 章节列表（含分析状态）
- `GET /api/novels/{id}/chapters/{chapter_num}` — 章节内容
- `PUT /api/novels/{id}/user-state` — 保存阅读位置

---

### Story 3.2: 实现实体聚合服务（后端）

As a 系统,
I want 从 ChapterFact 聚合生成实体档案,
So that 前端可以获取完整的人物/地点/物品/组织数据。

**Acceptance Criteria:**

**Given** 小说已有 ChapterFact 数据
**When** 调用 EntityAggregator.aggregate_person(novel_id, person_name)
**Then** 从全部 ChapterFact 聚合生成 PersonProfile：
  - 合并 aliases（按首次出现章节排序）
  - 收集 appearance 列表（按章节排序）
  - 收集 abilities_gained（按维度分组、章节排序）
  - 从 relationships 构建关系演变链
  - 从 item_events 构建物品关联
  - 从 events 构建人物经历
  - 统计：出场章节数、首末出场、关联人物数等

**When** 调用 aggregate_location / aggregate_item / aggregate_org
**Then** 同理生成 LocationProfile / ItemProfile / OrgProfile

**Given** 聚合结果已生成
**When** 同一 novel_id 再次请求
**Then** 从 LRU 缓存返回（最大 100 个实体），新 ChapterFact 写入时按 novel_id 失效

**Given** 请求小说的全部实体列表
**When** 调用 get_all_entities(novel_id)
**Then** 扫描全部 ChapterFact，返回去重后的实体名+类型+出场章节数列表

**API:**
- `GET /api/novels/{id}/entities` — 实体列表
- `GET /api/novels/{id}/entities/{name}` — 单个实体完整档案

---

### Story 3.3: 实现实体高亮渲染

As a 用户,
I want 已分析章节中的实体名自动高亮显示,
So that 我在阅读时能一眼看出人物、地点、物品等。

**Acceptance Criteria:**

**Given** 用户阅读一个已分析完成的章节
**When** 章节内容渲染
**Then** 从该章的 ChapterFact 提取所有实体名（characters.name、locations.name、item_events.item_name、org_events.org_name、new_concepts.name）
**And** 在章节文本中匹配这些实体名，按类别着色高亮：
  - 人物=蓝色、地点=绿色、物品=橙色、组织=紫色、概念=灰色（FR-12）
**And** 高亮文本可点击

**Given** 用户阅读未分析的章节
**When** 章节内容渲染
**Then** 正常显示文本，无高亮

**API:**
- `GET /api/novels/{id}/chapters/{chapter_num}/entities` — 该章的实体名列表（用于高亮）

---

### Story 3.4: 实现人物卡片和地点卡片

As a 用户,
I want 点击高亮的人物名或地点名后在右侧看到详细的实体卡片,
So that 我可以快速了解该角色或地点的完整信息。

**Acceptance Criteria:**

**Given** 阅读页中某个高亮人物名被点击
**When** 实体卡片抽屉打开
**Then** 右侧滑出 420px 宽的抽屉，显示 PersonCard：
  - A. 基本信息：姓名、别称列表（首次出现章节）、默认占位头像
  - B. 外貌特征：按章节排序，默认倒序显示最近 3 条
  - C. 人物关系：关系演变链（默认前 10 条）
  - D. 能力：按维度分组（境界/技能/身份）
  - E. 物品关系：关联物品及持有状态变化
  - F. 经历：最近 5 章经历（倒序）
  - G. 数据统计：出场章节数等（折叠区域）
  每个区块按规则截断，超出显示"共 X 项 ▸ 查看全部"（FR-20, FR-24）

**Given** 高亮地点名被点击
**When** 实体卡片抽屉打开
**Then** 显示 LocationCard：
  - A. 基本信息、B. 空间层级、C. 环境描写、D. 到访人物、E. 发生事件、F. 统计（FR-21）

**Given** 卡片抽屉已打开
**When** 点击遮罩 / 关闭按钮 / Esc
**Then** 抽屉关闭

---

### Story 3.5: 实现卡片间导航与物品/组织/概念卡片

As a 用户,
I want 在卡片内点击实体名跳转到另一张卡片，并能通过面包屑回退,
So that 我可以在实体之间自由探索。

**Acceptance Criteria:**

**Given** 人物卡片中某个人物名被点击
**When** 点击触发
**Then** 抽屉内容替换为新人物的卡片（非叠加新抽屉）
**And** 顶部显示面包屑导航（如"韩立 > 墨大夫"），点击可回退
**And** 面包屑最多保留 10 层（FR-25）

**Given** 物品实体名被点击
**When** 卡片加载
**Then** 显示 ItemCard：基本信息、物品描述、持有流转链、使用记录、关联物品、统计（FR-22）

**Given** 组织实体名被点击
**When** 卡片加载
**Then** 显示 OrgCard：基本信息、组织描述、组织层级、成员（身份演变链）、据点、大事记、统计（FR-23）

**Given** 概念高亮被点击
**When** 点击触发
**Then** 弹出轻量浮层（非完整卡片），显示：概念名称、定义、分类、首次提及、关联概念
**And** 底部提供"在百科中查看"链接（FR-26）

---

### Story 3.6: 实现实体消歧与阅读设置

As a 用户,
I want 同名实体能让我选择查看哪一个，并能调整阅读显示设置,
So that 我不会混淆同名角色，且阅读体验符合我的偏好。

**Acceptance Criteria:**

**Given** 点击的实体名对应多个同名实体（如两个"张三"）
**When** 点击触发
**Then** 弹出消歧选择面板，列出各同名实体的简要描述和出场章节数
**And** 选择后打开对应实体的卡片

**Given** 阅读页面
**When** 用户打开阅读设置面板
**Then** 可调整：字号（小/中/大/特大）、行距（紧凑/正常/宽松）、主题（亮色/暗色）（FR-16）
**And** 设置实时生效并持久化

---

### Story 3.7: 实现章节引用跳转与全文搜索

As a 用户,
I want 点击章节引用直接跳转，并能搜索全书关键词,
So that 我可以快速在小说中定位目标内容。

**Acceptance Criteria:**

**Given** 卡片或问答中出现"第 X 章"格式的文本
**When** 用户点击
**Then** 跳转到阅读页对应章节（FR-15）

**Given** 阅读页面
**When** 用户使用全文搜索（搜索框或快捷键）输入关键词
**Then** 在当前小说所有章节中搜索，返回匹配位置列表（章节+上下文片段）
**And** 点击结果跳转到对应章节的匹配位置并高亮关键词（FR-17）

**API:**
- `GET /api/novels/{id}/search?q={keyword}` — 全文搜索

---

## Epic 4: 知识图谱可视化

用户可以通过人物关系图、世界地图、时间线、势力图四种可视化视图从全局视角探索小说结构，所有视图通过章节范围滑块联动。

### Story 4.1: 实现可视化数据接口（后端）

As a 系统,
I want 为四种可视化视图提供数据接口,
So that 前端可以获取按章节范围过滤的图谱/地图/时间线/势力数据。

**Acceptance Criteria:**

**Given** 小说已有 ChapterFact 数据
**When** 调用 `GET /api/novels/{id}/graph?chapter_start=1&chapter_end=50`
**Then** 返回该范围内的人物关系图数据：
  - nodes: `[{ id, name, type, chapter_count, org }]`
  - edges: `[{ source, target, relation_type, weight, chapters }]`

**When** 调用 `GET /api/novels/{id}/map?chapter_start=1&chapter_end=50`
**Then** 返回空间地图数据：
  - locations: `[{ id, name, type, parent, level, mention_count }]`
  - trajectories: `{ person_name: [{ location, chapter }] }`

**When** 调用 `GET /api/novels/{id}/timeline?chapter_start=1&chapter_end=50`
**Then** 返回时间线数据：
  - events: `[{ chapter, summary, type, importance, participants, location }]`
  - swimlanes: `{ person_name: [event_ids] }`

**When** 调用 `GET /api/novels/{id}/factions?chapter_start=1&chapter_end=50`
**Then** 返回势力图数据：
  - orgs: `[{ id, name, type, member_count }]`
  - relations: `[{ source, target, type, chapter }]`
  - members: `{ org_name: [{ person, role, status }] }`

---

### Story 4.2: 实现章节范围滑块与可视化页面框架

As a 用户,
I want 通过章节范围滑块控制所有视图的数据范围,
So that 我可以观察小说在不同阶段的状态。

**Acceptance Criteria:**

**Given** 用户进入任一可视化页面（关系图/世界地图/时间线/势力图）
**When** 页面加载
**Then** 顶部显示章节范围滑块，范围从第 1 章到已分析的最后一章
**And** 默认选择全部已分析范围

**Given** 用户拖动章节范围滑块
**When** 范围变化
**Then** 当前视图的数据自动更新为新范围
**And** chapterRangeStore 状态更新，其他视图切换过去时使用同一范围（FR-35）

**Given** 用户在可视化页面间切换（通过顶部标签栏）
**When** 切换到另一视图
**Then** 章节范围状态保持一致

**Given** 小说仅部分分析
**When** 进入可视化页面
**Then** 顶部提示"当前展示基于已分析的 X / Y 章数据"

---

### Story 4.3: 实现人物关系图

As a 用户,
I want 以力导向图查看人物之间的关系网络,
So that 我可以直观理解角色间的关系结构。

**Acceptance Criteria:**

**Given** 可视化数据已加载
**When** 关系图页面 `/novel/:id/graph` 渲染
**Then** 使用 @react-force-graph-2d 绘制力导向图
  - 节点大小按出场章节数映射
  - 节点颜色按组织归属区分
  - 节点标签显示姓名（密集时只显示主要人物，缩放后逐步显示）
  - 边粗细按互动章节数映射
  - 边颜色按关系大类区分（亲属暖色、友好绿色、敌对红色、组织蓝色）
  - 边标签显示最新关系类型（FR-30）

**Given** 关系图已渲染
**When** 用户交互
**Then** 支持：拖拽画布平移、滚轮缩放、拖拽节点、点击节点弹出实体卡片、悬浮节点高亮直接关系、双击节点进入聚焦模式

**Given** 关系图页面
**When** 用户打开筛选面板
**Then** 支持按实体类型、关系类型、组织归属、最少出场数筛选（FR-37）

---

### Story 4.4: 实现世界地图（层级视图 + 空间视图）

As a 用户,
I want 通过层级树和空间地图查看地点关系,
So that 我可以理解小说世界的空间结构。

**Acceptance Criteria:**

**Given** 地图数据已加载
**When** 世界地图页面 `/novel/:id/map` 渲染（层级视图标签）
**Then** 以可折叠树形结构展示地点层级关系
  - 节点大小按提及章节数映射
  - 节点颜色按地点类型区分
  - 点击节点弹出地点卡片
  - 双击展开/折叠子地点
  - 提供"在空间地图中定位"按钮（FR-32）

**Given** 用户切换到空间视图标签
**When** 空间地图渲染
**Then** 使用 Pixi.js v8 渲染节点-区域型空间示意图
  - 地点以图标节点形式按空间关系布局
  - 包含子地点的地点渲染为半透明区域色块
  - 支持 5 级语义缩放
  - 画布底色为羊皮纸质感
  - 支持拖拽平移、滚轮缩放、点击地点弹出卡片（FR-31）

**Given** 空间地图中
**When** 用户选择人物叠加轨迹
**Then** 显示该人物的移动轨迹（带箭头曲线 + 章节标注 + 渐变色）

---

### Story 4.5: 实现时间线

As a 用户,
I want 在时间轴上查看事件分布,
So that 我可以把握故事的节奏和关键时间点。

**Acceptance Criteria:**

**Given** 时间线数据已加载
**When** 时间线页面 `/novel/:id/timeline` 渲染
**Then** 使用自研 Canvas 组件（基于 d3-scale + d3-axis）绘制水平时间线
  - 横轴为章节编号
  - 事件显示为轴上圆点，大小按重要度映射
  - 颜色按事件类型区分（战斗红色、成长蓝色、社交绿色、旅行橙色）（FR-33）

**Given** 时间线已渲染
**When** 用户交互
**Then** 支持：悬浮事件节点显示摘要浮层、点击跳转到章节阅读、框选放大、拖拽平移、滚轮缩放

**Given** 用户切换到多泳道模式
**When** 选择多个人物
**Then** 每个人物一条横向轨道，展示各自的事件线（FR-33）

**Given** 筛选面板
**When** 用户筛选
**Then** 支持按事件类型、涉及人物、涉及地点、重要度阈值筛选

---

### Story 4.6: 实现势力图与视图联动

As a 用户,
I want 查看组织关系网络，并在各视图间联动导航,
So that 我可以从不同角度综合理解小说结构。

**Acceptance Criteria:**

**Given** 势力图数据已加载
**When** 势力图页面 `/novel/:id/factions` 渲染
**Then** 使用 @react-force-graph-2d 绘制组织关系网络
  - 节点大小按成员数映射，颜色按组织类型区分
  - 边样式按关系类型区分（盟友绿实线、敌对红实线、从属蓝虚线）
  - 双击节点展开内部结构（FR-34）

**Given** 用户在势力图中点击某组织
**When** 切换到关系图页面
**Then** 关系图自动筛选为该组织成员的关系网络（FR-36 视图联动）

**Given** 用户在关系图中选择某人物
**When** 切换到世界地图
**Then** 世界地图自动叠加该人物的移动轨迹（FR-36）

**Given** 用户在时间线中点击某事件节点
**When** 点击触发
**Then** 跳转到该章节的阅读页面（FR-36）

---

### Story 4.7: 实现路径查找与高级交互

As a 用户,
I want 查找两个人物间的最短关系路径，播放轨迹动画,
So that 我可以发现隐含的人物联系和移动模式。

**Acceptance Criteria:**

**Given** 关系图页面
**When** 用户选择第一个人物节点后 Shift+点击第二个节点（或搜索框输入两人名）
**Then** 系统计算最短关系路径（BFS），高亮路径上的节点和边（FR-38）

**Given** 空间地图中已选择人物轨迹
**When** 用户点击"播放"按钮
**Then** 轨迹按章节顺序逐段出现，章节滑块同步移动，可暂停/拖拽跳转
**And** 停留超过 N 章的地点标记为较大圆点（FR-39）

**Given** 空间地图中
**When** 用户长按 0.5 秒拖拽地点图标
**Then** 进入编辑模式，松手保存调整后的位置（FR-40）

---

## Epic 5: 智能问答

用户可以用自然语言对小说内容提问，系统基于知识图谱和原文检索给出流式回答，标注来源章节，支持连续追问。

### Story 5.1: 实现问答 Pipeline（后端）

As a 系统,
I want 构建混合检索 + LLM 推理的问答 Pipeline,
So that 能基于小说内容回答用户的自然语言问题。

**Acceptance Criteria:**

**Given** 小说已有 ChapterFact 和向量嵌入数据
**When** 调用 QueryService.query(novel_id, question, conversation_id)
**Then** 按以下步骤处理：
  1. 问题分析：从问题中提取实体名，分类问题类型
  2. 混合检索：
     - 向量检索 (ChromaDB)：语义搜索相关章节 (权重 0.5)
     - 实体检索：从 ChapterFact 中找相关实体事实 (权重 0.3)
     - 关键词检索：章节全文关键词匹配 (权重 0.2)
  3. 上下文构建：合并检索结果，拼接相关片段，附加对话历史
  4. LLM 推理：流式生成答案，要求标注来源章节
  5. 后处理：提取答案中的实体名和来源章节引用

**Given** 小说仅部分分析
**When** 回答生成
**Then** 答案末尾标注"基于已分析的 X 章内容"

**Given** 知识图谱中没有足够信息
**When** 回答生成
**Then** 明确回答"根据已分析的内容，暂未找到相关信息"

---

### Story 5.2: 实现向量嵌入生成

As a 系统,
I want 为章节文本和实体描述生成向量嵌入,
So that 问答时可以进行语义检索。

**Acceptance Criteria:**

**Given** 某章的 ChapterFact 已生成
**When** 分析完成后
**Then** 自动为该章文本生成嵌入向量，存入 ChromaDB `{novel_id}_chapters` collection
**And** 为该章中新出现的实体生成描述嵌入，存入 `{novel_id}_entities` collection

**Given** 嵌入模型为 BGE-base-zh-v1.5
**When** 生成嵌入
**Then** 使用 MPS 加速（Apple Silicon），768 维向量

**API:**
- 嵌入生成在分析流程中自动触发，无独立 API

---

### Story 5.3: 实现流式问答 WebSocket 与浮动面板

As a 用户,
I want 在任何小说页面通过底部输入框提问，看到答案逐字出现,
So that 我可以随时提问而不必离开当前页面。

**Acceptance Criteria:**

**Given** 用户在任何小说内页面
**When** 点击底部常驻输入框或按 Cmd/Ctrl+K
**Then** 浮动面板从底部滑出，占屏幕下方约 50% 高度（FR-54）
**And** 面板可拖拽调整高度
**And** 面板右上角提供"展开"按钮跳转到全屏对话页

**Given** 浮动面板已打开
**When** 用户输入问题并发送
**Then** 通过 WebSocket `/ws/chat/{session_id}` 发送查询
**And** 答案逐 token 流式显示
**And** 完成后显示来源章节列表（可点击跳转）和答案中的实体高亮（可点击弹出卡片）（FR-50, FR-51, FR-52）

**Given** 浮动面板打开
**When** 点击面板外区域或按 Esc
**Then** 面板收起

---

### Story 5.4: 实现对话管理与全屏对话页

As a 用户,
I want 管理多个对话历史，在全屏模式下深度问答,
So that 我可以就不同话题与系统对话并保留记录。

**Acceptance Criteria:**

**Given** 用户从浮动面板点击"展开"
**When** 进入全屏对话页 `/novel/:id/chat`
**Then** 左侧显示对话列表侧栏，右侧显示当前对话流
**And** 浮动面板中的对话在全屏页继续，上下文不丢失

**Given** 全屏对话页
**When** 用户点击"新建对话"
**Then** 创建新对话，清空上下文（FR-53）

**Given** 全屏对话页
**When** 用户在侧栏切换对话
**Then** 右侧显示选中对话的历史消息（FR-55）

**Given** 对话历史
**When** 用户删除某个对话
**Then** 从列表移除，数据从数据库删除

**Given** 用户连续追问（如"他后来呢？"）
**When** 对话上下文中有前一轮提到的人物
**Then** 系统理解代词指代，回答与上下文连续（FR-53）

**API:**
- `GET /api/novels/{id}/conversations` — 对话列表
- `POST /api/novels/{id}/conversations` — 创建对话
- `DELETE /api/conversations/{id}` — 删除对话
- `GET /api/conversations/{id}/messages` — 消息列表

---

### Story 5.5: 实现对话导出

As a 用户,
I want 将对话内容导出为 Markdown 文件,
So that 我可以保存和分享问答结果。

**Acceptance Criteria:**

**Given** 全屏对话页中有对话内容
**When** 用户点击"导出"
**Then** 生成 Markdown 文件下载，包含对话标题、时间、全部问答内容和来源标注（FR-56）

---

## Epic 6: 百科与系统设置

用户可以浏览小说的结构化知识百科（实体索引 + 概念词条），配置系统设置，导入/导出分析数据。

### Story 6.1: 实现百科页面

As a 用户,
I want 浏览小说中所有实体和概念的分类索引,
So that 我可以系统性地了解小说的知识体系。

**Acceptance Criteria:**

**Given** 小说已有分析数据
**When** 用户进入百科页面 `/novel/:id/encyclopedia`
**Then** 左侧显示分类导航树：
  - 全部 (N)
    - 人物 (N)
    - 地点 (N)
    - 物品 (N)
    - 组织 (N)
    - 概念 (N)
      - 修炼体系 / 种族 / 货币 / 功法 / 其他 (子分类)
  每个分类标注条目数量（FR-70, FR-72）

**Given** 用户选择某分类
**When** 右侧内容区更新
**Then** 显示该分类下的词条列表：名称、类型、简要定义（一行）、首次出现章节
**And** 支持按名称/首次出现章节排序

**Given** 用户在搜索框输入
**When** 搜索
**Then** 按名称模糊搜索，实时过滤（FR-70）

**Given** 用户点击人物/地点/物品/组织词条
**When** 点击
**Then** 弹出对应实体卡片（复用 Epic 3 的卡片系统）

**Given** 用户点击概念词条
**When** 点击
**Then** 展示概念详情：名称、分类、定义、首次提及、原文摘录（1-3 条附章节出处）、关联概念、关联实体（FR-71）

**API:**
- `GET /api/novels/{id}/encyclopedia` — 分类统计
- `GET /api/novels/{id}/encyclopedia/entries?category={cat}&sort={field}` — 词条列表
- `GET /api/novels/{id}/encyclopedia/{name}` — 概念详情

---

### Story 6.2: 实现设置页面

As a 用户,
I want 在设置页面配置 LLM、阅读偏好和管理数据,
So that 我可以根据自己的设备和偏好定制系统。

**Acceptance Criteria:**

**Given** 用户进入设置页面 `/settings`
**When** 页面加载
**Then** 显示以下设置分区：

1. **LLM 配置**：
   - 显示 Ollama 运行状态
   - 模型选择（检测已下载模型列表）
   - 各模型推荐配置标注

2. **阅读偏好**：
   - 字号、行距、主题（亮/暗）设置（与阅读页联动）

3. **数据管理**：
   - 显示各小说的数据大小
   - 提供"导出分析数据"和"导入分析数据"入口（FR-81）

**Given** 用户修改设置
**When** 保存
**Then** 设置持久化并即时生效

**API:**
- `GET /api/settings` — 获取设置
- `PUT /api/settings` — 更新设置
- `GET /api/settings/models` — 可用模型列表

---

### Story 6.3: 实现数据导入导出

As a 用户,
I want 导出某本小说的全部分析数据，并能在另一台机器导入,
So that 我可以迁移数据而不必重新分析。

**Acceptance Criteria:**

**Given** 用户在设置页或分析页选择某本小说
**When** 点击"导出分析数据"
**Then** 生成 JSON 文件下载，包含：novel 元信息、全部 chapters、全部 chapter_facts、user_state（FR-82）

**Given** 用户点击"导入分析数据"
**When** 选择一个导出的 JSON 文件
**Then** 验证文件格式，显示预览（小说名、章节数、数据量）
**And** 确认后导入到数据库，如已存在同名小说则提示覆盖或新建（FR-83）

**API:**
- `GET /api/novels/{id}/export` — 导出数据
- `POST /api/novels/import` — 导入数据

---

## 验证总结

### FR 覆盖率

| 模块 | FRs | 覆盖 Story |
|------|-----|------------|
| 书架 (F-01~06) | 6/6 | 1.2, 1.3, 1.4, 1.5, 1.7 |
| 系统 (F-80) | 1/1 | 1.6 |
| 分析 (F-60~66) | 7/7 | 2.1, 2.3, 2.4, 2.5 |
| 阅读 (F-10~17) | 8/8 | 3.1, 3.3, 3.6, 3.7 |
| 卡片 (F-20~26) | 7/7 | 3.4, 3.5 |
| 可视化 (F-30~40) | 11/11 | 4.1~4.7 |
| 问答 (F-50~56) | 7/7 | 5.1~5.5 |
| 百科 (F-70~72) | 3/3 | 6.1 |
| 系统 (F-81~83) | 3/3 | 6.2, 6.3 |

**全部 53 个 FR（83 个功能项去重后的唯一功能）100% 覆盖。**

### Epic 依赖关系

```
Epic 1 (书架上传) ← 无依赖，独立可用
  │
  ▼
Epic 2 (分析引擎) ← 依赖 Epic 1（需要有小说才能分析）
  │
  ├──▶ Epic 3 (阅读卡片) ← 依赖 Epic 2（需要 ChapterFact 数据）
  │
  ├──▶ Epic 4 (可视化) ← 依赖 Epic 2（需要 ChapterFact 数据）
  │
  ├──▶ Epic 5 (问答) ← 依赖 Epic 2（需要 ChapterFact + 向量数据）
  │
  └──▶ Epic 6 (百科设置) ← 依赖 Epic 2（百科需要分析数据）

Epic 3/4/5/6 之间无互相依赖，可并行开发。
```

### Story 无前向依赖检查

每个 Epic 内的 Story 仅依赖前序 Story，不依赖后续 Story。✅

---

*Epic 与 Story 拆分完成。下一步：Sprint 规划 (SP 工作流) 或直接进入开发 (DS 工作流)。*
