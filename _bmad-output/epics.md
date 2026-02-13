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

## Epic 7: 世界地图 V2 — 多层级世界结构

用户可以看到包含宏观区域划分（如四大部洲）、多层空间（天界/冥界/洞府副本）、传送门连接的多层级世界地图，系统通过渐进式世界结构代理在分析过程中自动构建世界观。

**依赖:** Epic 2（需要分析流水线）+ Epic 4 Stories 4.8-4.10（已有世界地图 V1 基础）
**架构文档:** `_bmad-output/world-map-v2-architecture.md`

### Story 7.1: WorldStructure 数据模型与存储层

As a 系统,
I want 定义世界结构的数据模型并提供数据库存储,
So that 世界结构代理有持久化的数据基础。

**Acceptance Criteria:**

**Given** 系统启动
**When** 数据库初始化
**Then** 新增 `world_structures` 表和 `layer_layouts` 表

**Given** WorldStructure 模型已定义
**When** 调用 world_structure_store.save(novel_id, structure)
**Then** WorldStructure JSON 持久化到数据库
**And** 调用 load(novel_id) 可恢复完整对象

**技术说明:**
- 新增 `backend/src/models/world_structure.py`:
  - `LayerType(Enum)`: overworld, celestial, underworld, underwater, instance, pocket
  - `WorldRegion(BaseModel)`: name, cardinal_direction, region_type, parent_region, description
  - `MapLayer(BaseModel)`: layer_id, name, layer_type, description, regions
  - `Portal(BaseModel)`: name, source_layer, source_location, target_layer, target_location, is_bidirectional, first_chapter
  - `WorldBuildingSignal(BaseModel)`: signal_type, chapter, raw_text_excerpt, extracted_facts, confidence
  - `WorldStructure(BaseModel)`: novel_id, layers, portals, location_region_map, location_layer_map
- 新增 `backend/src/db/world_structure_store.py`: save/load/delete CRUD
- 在 `sqlite_db.py` `_SCHEMA_SQL` 中添加两张新表
- 坐标系遵循上北下南左西右东惯例: +x=东(右), +y=北(上)

---

### Story 7.2: WorldStructureAgent 信号扫描与启发式更新

As a 系统,
I want 在每章分析后扫描世界观构建信号并进行轻量启发式更新,
So that 系统能自动识别天界/冥界/洞府等空间层和宏观区域划分。

**Acceptance Criteria:**

**Given** 某章 ChapterFact 已提取并验证
**When** 调用 agent._scan_signals(chapter_num, chapter_text, fact)
**Then** 返回 WorldBuildingSignal 列表，检测以下信号类型：
  - `region_division`: 关键词 "分为"/"划为" + 洲/大陆/界/域 等
  - `layer_transition`: 角色进入天界/地府/海底等非地理空间
  - `instance_entry`: 角色进入洞府/阵法/秘境等封闭空间
  - `macro_geography`: 新出现的宏观地点类型（洲/域/界/国）
**And** 每个信号包含原文摘录（≤200字）

**Given** 信号扫描完成但不触发 LLM
**When** 调用 agent._apply_heuristic_updates(chapter_num, fact)
**Then** 基于关键词自动分配地点到层：
  - 名含 天宫/天庭/天门 等 → celestial 层
  - 名含 地府/冥界/幽冥 等 → underworld 层
  - type 含 洞/府/宫 且有明确入口 → instance 层候选
  - parent 是已知区域 → 分配到该区域

**技术说明:**
- 新增 `backend/src/services/world_structure_agent.py`
- `_scan_signals()` 纯本地执行，不调用 LLM
- `_apply_heuristic_updates()` 更新 WorldStructure 并持久化
- 信号规则可配置，支持不同小说类型

---

### Story 7.3: WorldStructureAgent LLM 增量更新

As a 系统,
I want 当检测到高置信度世界观信号时调用 LLM 增量更新世界结构,
So that 系统能理解"世界分为四大部洲"等宏观世界观声明。

**Acceptance Criteria:**

**Given** 信号扫描检测到高置信度信号
**When** 满足触发条件（前5章必触发 / region_division 信号 / 首次 layer_transition / 每20章例行更新）
**Then** 调用 LLM，输入包含：
  - 当前 WorldStructure JSON
  - 本章世界观信号原文摘录
  - 本章提取的 locations 和 spatial_relationships
**And** LLM 输出增量操作列表：ADD_REGION / ADD_LAYER / ADD_PORTAL / ASSIGN_LOCATION / UPDATE_REGION / NO_CHANGE
**And** 操作应用到 WorldStructure 并持久化

**Given** LLM 调用失败（超时/JSON解析错误）
**When** 错误发生
**Then** 记录日志但不中断分析流水线，WorldStructure 保持上一次成功状态

**Given** 100章小说
**When** 全书分析完成
**Then** LLM 世界结构更新调用次数 ≤ 25 次（约增加15-25%开销）

**技术说明:**
- 新增 LLM prompt 模板: `backend/src/extraction/prompts/world_structure_update.txt`
- 使用 structured output + schema 约束 LLM 输出格式
- `_should_trigger_llm()` 实现触发条件判断
- `_call_llm_for_update()` 调用 LLM 并解析操作
- `_apply_operations()` 将操作应用到 WorldStructure

---

### Story 7.4: 分析流水线集成与上下文反馈

As a 系统,
I want 将 WorldStructureAgent 嵌入分析流水线，并将世界结构反馈到提取上下文,
So that 后续章节的提取能利用已构建的世界知识。

**Acceptance Criteria:**

**Given** 分析流水线运行中
**When** 每章完成 fact_validator.validate(fact) 后
**Then** 调用 world_agent.process_chapter(chapter_num, chapter_text, fact)
**And** 流水线正常继续，agent 错误不阻塞分析

**Given** WorldStructure 包含区域和层信息
**When** context_summary_builder.build() 被调用
**Then** 上下文中包含"已知世界结构"摘要段落：
  - 主世界区域列表（名称+方位）
  - 已知地图层列表
  - 已知传送门列表（前10个）

**Given** 用户强制重新分析（force=True）
**When** 分析开始
**Then** WorldStructure 在现有基础上增量更新（不清空重建）

**验证标准:** 用西游记前10章测试，Agent 应能识别：
  - 四大部洲（东胜神洲/西牛贺洲/南赡部洲/北俱芦洲）及其方位
  - 天界层（天宫/凌霄殿等）
  - 冥界层（地府/阎罗殿等）

**技术说明:**
- 修改 `backend/src/services/analysis_service.py` `_run_loop_inner()`
- 在 validator 之后、store 之前注入 agent 调用
- 修改 `backend/src/extraction/context_summary_builder.py` 添加世界结构摘要
- Agent 初始化在 AnalysisService.__init__() 中

---

### Story 7.5: WorldStructure API 端点

As a 前端,
I want 通过 API 获取小说的世界结构数据,
So that 地图页面可以渲染多层级地图。

**Acceptance Criteria:**

**Given** 小说已分析且 WorldStructure 已构建
**When** 调用 `GET /api/novels/{novel_id}/world-structure`
**Then** 返回完整 WorldStructure：layers, portals, regions, location_region_map, location_layer_map

**Given** 小说未分析或 WorldStructure 为空
**When** 调用 API
**Then** 返回默认结构（仅包含 overworld 层，无区域划分）

**技术说明:**
- 新增 `backend/src/api/routes/world_structure.py`
- 在 `main.py` 注册路由
- 返回 WorldStructure 的 JSON 序列化

---

### Story 7.6: 区域级布局引擎

As a 系统,
I want 基于 WorldStructure 的区域划分在画布上分配区域边界框,
So that 地点布局有宏观结构而不是平铺在一个平面上。

**Acceptance Criteria:**

**Given** WorldStructure 包含区域划分（如四大部洲）
**When** 调用区域级布局
**Then** 每个区域分配到画布对应方位的矩形边界框：
  - cardinal_direction="east" → 画布右侧
  - cardinal_direction="west" → 画布左侧
  - cardinal_direction="south" → 画布下方
  - cardinal_direction="north" → 画布上方
**And** 遵循上北下南左西右东惯例

**Given** 多个区域同方位（如多个国家都在"东"）
**When** 方位冲突
**Then** 同方位区域在该象限内等分细分

**Given** WorldStructure 为空或仅有 overworld 无区域
**When** 调用布局
**Then** 回退到当前全局约束求解布局（兼容 V1）

**技术说明:**
- 重构 `backend/src/services/map_layout_service.py` 新增 `_layout_regions()` 方法
- 区域边界框作为后续区域内约束求解的空间约束

---

### Story 7.7: 区域内约束求解与副本独立布局

As a 系统,
I want 在每个区域边界框内独立运行约束求解器，副本层用独立小画布布局,
So that 布局质量提升且求解效率更高。

**Acceptance Criteria:**

**Given** 区域边界框已分配
**When** 对每个区域运行约束求解
**Then** 仅使用该区域内地点的空间约束
**And** 地点坐标限制在区域边界框范围内
**And** 单区域地点数 10-50，参数维度 20-100

**Given** WorldStructure 包含 instance 类型层（如水帘洞）
**When** 副本层布局
**Then** 使用独立的 [0, 300] 小画布布局
**And** 副本内地点使用层内空间关系

**Given** 天界层或冥界层
**When** 层布局
**Then** 使用独立画布，背景色/氛围与 overworld 不同

**技术说明:**
- 在 `map_layout_service.py` 中新增 `_solve_region()` 和 `_solve_layer()`
- 复用现有 ConstraintSolver，但限制边界范围
- 传送门位置标注在源层的出发地点附近

---

### Story 7.8: 地图 API V2 与层布局缓存

As a 前端,
I want 通过 API 按层获取地图布局数据,
So that 前端可以按需加载各层的地图。

**Acceptance Criteria:**

**Given** 小说有多层世界结构
**When** 调用 `GET /api/novels/{id}/map?layer_id=overworld&chapter_start=1&chapter_end=100`
**Then** 返回该层的布局数据：
  - locations（该层的地点列表）
  - layout（布局坐标）
  - layout_mode
  - terrain_url
  - region_boundaries（区域边界框列表）
  - portals（该层的传送门列表）

**Given** 未指定 layer_id
**When** 调用地图 API
**Then** 默认返回 overworld 层数据 + world_structure 概要

**Given** 层布局已缓存
**When** 再次请求相同层和章节范围
**Then** 从 layer_layouts 表返回缓存数据

**技术说明:**
- 修改 `backend/src/api/routes/map.py` 添加 layer_id 参数
- 修改 `backend/src/services/visualization_service.py` 支持按层获取数据
- 新增 layer_layouts 缓存表（在 Story 7.1 中已创建）

---

### Story 7.9: 前端类型更新与 Tab 切换 UI

As a 用户,
I want 在地图页面通过 Tab 栏切换不同地图层（主世界/天界/冥界/副本）,
So that 我可以浏览小说世界的不同空间层。

**Acceptance Criteria:**

**Given** 地图页面加载
**When** 小说有多层世界结构
**Then** 地图上方显示 Tab 栏，列出所有已解锁的地图层
**And** 默认显示 overworld（主世界）层
**And** 每个 Tab 显示层名称和地点数量

**Given** 用户点击某个 Tab（如"天界"）
**When** 切换层
**Then** 地图内容切换到该层的布局数据
**And** 背景色根据层类型变化：celestial 用深蓝/金色调，underworld 用暗紫色调，instance 用洞穴色调

**Given** 章节范围内某层没有活动（无地点提及）
**When** Tab 栏渲染
**Then** 该层 Tab 显示为灰色禁用状态

**技术说明:**
- 更新 `frontend/src/api/types.ts` 添加 LayeredMapData / MapLayerInfo / PortalInfo / RegionBoundary 类型
- 更新 `frontend/src/api/client.ts` 添加 fetchMapData layer_id 参数
- 新增 `frontend/src/components/visualization/MapLayerTabs.tsx`
- 修改 `frontend/src/pages/MapPage.tsx` 集成 Tab 切换逻辑

---

### Story 7.10: 区域边界、传送门 UI 与增强 Fog of War

As a 用户,
I want 在主世界地图上看到区域边界划分和传送门入口标记,
So that 我能直观理解小说世界的宏观结构和空间层级。

**Acceptance Criteria:**

**Given** overworld 层有区域划分
**When** 地图渲染
**Then** 区域以半透明填充色 + 虚线边界标识
**And** 区域名称以大字体低透明度标注在区域中心

**Given** 地图上有传送门位置（如南天门）
**When** 地图渲染
**Then** 传送门显示为特殊图标（⊙ 标记）
**And** 点击传送门弹出 Popup，显示"通往：[目标层名]"和"进入地图"按钮
**And** 点击"进入地图"切换到目标层的 Tab

**Given** 章节范围滑动
**When** 某地点尚未在当前范围出现但在之前章节出现过
**Then** 地点显示为灰色轮廓（已揭示状态），而非完全透明
**And** 当前范围未出现且之前也未出现的地点完全隐藏

**技术说明:**
- 修改 `frontend/src/components/visualization/NovelMap.tsx`:
  - 新增 region-fills / region-borders / region-labels GeoJSON 层
  - 新增 portals GeoJSON 层（circle + symbol）
  - Fog of War 增强为三态：hidden(不显示) / revealed(灰色) / active(完整)
- 传送门点击 → Tab 切换联动

---

### Story 7.11: 用户编辑世界结构

As a 用户,
I want 手动调整世界结构（区域归属、传送门增删、区域方位）,
So that 我可以修正 LLM 生成的错误。

**Acceptance Criteria:**

**Given** 地图页面或专用编辑页面
**When** 用户拖拽地点到另一个区域
**Then** 更新 location_region_map，存为 user override

**Given** 用户在传送门面板中
**When** 添加新传送门
**Then** 指定名称、源层+地点、目标层，保存到 WorldStructure

**Given** 用户编辑了世界结构
**When** 保存
**Then** 编辑存为 override，优先于 LLM 生成的结构
**And** 下次 LLM 更新不覆盖用户 override

**技术说明:**
- 新增 `PUT /api/novels/{id}/world-structure/overrides` API
- 新增 `world_structure_overrides` 数据库表
- 前端新增编辑面板组件

---

### Story 7.12: 提取增强与通用性优化

As a 系统,
I want 在 ChapterFact 中可选地提取世界观声明，并针对不同类型小说优化,
So that 世界结构质量更高、方案更通用。

**Acceptance Criteria:**

**Given** extraction prompt 已增强
**When** LLM 提取 ChapterFact
**Then** 可选地提取 `world_declarations` 字段（区域划分声明/层声明/传送门声明）

**Given** 不同类型小说
**When** 世界结构构建
**Then** 方案优雅处理：
  - 奇幻/修仙: 多层世界（天界/地下/副本）
  - 历史/武侠: 主要是地理平面 + 少量副本
  - 都市: 单层城市地理，无副本
  - 简单结构: 仅 overworld，无区域划分

**技术说明:**
- 修改 `backend/src/models/chapter_fact.py` 添加可选 `world_declarations` 字段
- 修改 extraction prompt 添加世界观声明提取指令
- 新增 `InBetween` 空间约束类型
- 地点语义位置提示（东洋大海 → 东方）

---

## 验证总结

## Epic 8: 实体预扫描词典

在小说导入后、LLM 逐章分析前，自动对全书文本进行统计扫描和 LLM 分类，生成高频实体词典，注入分析流水线提升提取质量。

**依赖:** Epic 2（需要分析流水线）
**架构文档:** `_bmad-output/entity-prescan-architecture.md`

### Story 8.1: 数据模型与存储层

As a 系统,
I want 定义实体预扫描词典的数据模型并提供数据库存储,
So that 预扫描引擎有持久化的数据基础。

**Acceptance Criteria:**

**Given** 系统启动
**When** 数据库初始化
**Then** 新增 `entity_dictionary` 表（novel_id, name, entity_type, frequency, confidence, aliases, source, sample_context）
**And** `novels` 表新增 `prescan_status` 列

**Given** EntityDictEntry 模型已定义
**When** 调用 entity_dictionary_store.insert_batch(novel_id, entries)
**Then** 批量插入词典条目，支持 INSERT OR REPLACE

**技术说明:**
- 新增 `backend/src/models/entity_dict.py`
- 新增 `backend/src/db/entity_dictionary_store.py`
- 修改 `backend/src/db/sqlite_db.py`
- 修改 `backend/pyproject.toml` 新增 jieba 依赖

---

### Story 8.2: Phase 1 统计扫描引擎

As a 系统,
I want 对全书文本进行统计扫描提取高频实体候选词,
So that 后续 LLM 分类和词典注入有准确的候选数据来源。

**Acceptance Criteria:**

**Given** 小说已导入（chapters 表有数据）
**When** 调用 EntityPreScanner 的 Phase 1
**Then** 执行 jieba 分词+词频统计、n-gram 统计、对话归属正则、章节标题提取、后缀模式匹配
**And** 合并去重后输出候选列表
**And** 100 万字小说扫描 ≤ 15 秒

**技术说明:**
- 新增 `backend/src/extraction/entity_pre_scanner.py`
- jieba 使用 `asyncio.to_thread()` 包装

---

### Story 8.3: Phase 2 LLM 分类

As a 系统,
I want 用 LLM 对候选词进行分类和别名关联,
So that 词典中的实体类型和别名信息更准确。

**Acceptance Criteria:**

**Given** Phase 1 产出候选列表
**When** 调用 Phase 2 LLM 分类
**Then** 取 Top-300 候选 + 上下文，单次 LLM 调用返回分类+别名组+拒绝词
**And** LLM 失败时降级为仅 Phase 1 结果

**技术说明:**
- 新增 `backend/src/extraction/prescan_prompts.py`
- 复用现有 `get_llm_client()` 工厂

---

### Story 8.4: 流水线集成

As a 用户,
I want 预扫描词典自动集成到分析流水线中,
So that 每次分析都能利用全书实体参考信息。

**Acceptance Criteria:**

**Given** 小说导入确认后
**When** confirm_import() 返回
**Then** 自动后台触发预扫描

**Given** 用户点击"开始分析"
**When** prescan_status 不是 completed
**Then** 等待或触发预扫描（超时 120s 后降级）

**Given** ContextSummaryBuilder.build() 被调用
**When** 词典存在
**Then** context 末尾注入"本书高频实体参考"段落（Top-100 实体）

**技术说明:**
- 修改 `novel_service.py`、`analysis_service.py`、`context_summary_builder.py`

---

### Story 8.5: API 路由与注册

As a 开发者/前端,
I want 通过 REST API 查询预扫描状态和词典内容,
So that 前端可以展示预扫描进度和词典数据。

**Acceptance Criteria:**

**Given** API 已注册
**When** 调用 `POST /api/novels/{id}/prescan`
**Then** 触发预扫描

**When** 调用 `GET /api/novels/{id}/prescan`
**Then** 返回预扫描状态和词典条目数

**When** 调用 `GET /api/novels/{id}/entity-dictionary`
**Then** 返回词典内容，支持按类型筛选

**技术说明:**
- 新增 `backend/src/api/routes/prescan.py`
- 修改 `backend/src/api/main.py` 注册路由

---

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
  │    │
  │    └──▶ Epic 7 (世界地图V2) ← 依赖 Epic 2（分析流水线）+ Epic 4.8-4.10（地图V1基础）
  │
  ├──▶ Epic 5 (问答) ← 依赖 Epic 2（需要 ChapterFact + 向量数据）
  │
  └──▶ Epic 6 (百科设置) ← 依赖 Epic 2（百科需要分析数据）

Epic 3/4/5/6 之间无互相依赖，可并行开发。
Epic 7 内部 Story 顺序: 7.1→7.2→7.3→7.4→7.5 (Phase 1) → 7.6→7.7→7.8 (Phase 2) → 7.9→7.10 (Phase 3) → 7.11→7.12 (Phase 4)
```

### Story 无前向依赖检查

每个 Epic 内的 Story 仅依赖前序 Story，不依赖后续 Story。✅

---

*Epic 与 Story 拆分完成。下一步：Sprint 规划 (SP 工作流) 或直接进入开发 (DS 工作流)。*
