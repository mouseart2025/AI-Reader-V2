---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories]
inputDocuments:
  - PRD-v1.0.md (v1.3)
  - _bmad-output/architecture.md
date: '2026-02-17'
scope: 仅未实现功能（已实现 FR-001~006, FR-012 不在范围内）
---

# AI Reader V2 - Epic Breakdown（未实现功能）

## Overview

本文档将 PRD v1.3 中**尚未实现**的功能需求分解为 11 个 Epic、41 个 Story。按用户价值组织，每个 Epic 可独立交付。

**已实现功能**（不在本文档范围）：书架管理(FR-001)、阅读高亮(FR-002)、实体卡片(FR-003)、AI 分析引擎(FR-004)、关系图(FR-005)、世界地图(FR-006)、智能问答(FR-012)、百科、实体预扫描。

---

## Requirements Inventory

### Functional Requirements（待实现）

```
FR-007: 时间线可视化——以章节为轴展示事件节点，支持角色/类型筛选和缩放 [P1]
FR-008: 势力图可视化——组织层级、势力间关系、成员归属，按章节范围查看演变 [P2]
FR-009: Series Bible 导出——Markdown/Word/Excel/PDF，内容模块选择 + 场景化模板 [P0/P1]
FR-010: 剧本模式——按场景组织，并列模式 + 独占模式 [P2, 实验性]
FR-011: 设定冲突检测——四类矛盾识别，严重程度排序，原文对比 [P2]
FR-013: 样本小说内置体验——西游记+三国演义预分析数据，首次启动零配置 [P0]
FR-014: 分析成本控制——预估/实时追踪/预算告警/章节明细 [P0-云端]
FR-015: 用户行为分析——本地匿名统计，用户可关闭 [P2]
IFR-001: LLM 配置产品化——Ollama 检测/模型推荐/API Key 安全存储/设置 UI [P0]
IFR-002: 桌面打包——Tauri 2.x + Python Sidecar + 签名 + 自动更新 [P0]
```

### NonFunctional Requirements

```
NFR-PERF-01: 启动 < 5秒    NFR-PERF-06: Markdown导出 < 30秒/500章
NFR-SEC-01: API Key 密钥库   NFR-SEC-04: 更新包签名验证
NFR-SEC-05: 无远程遥测       NFR-COMP-01: macOS 12+ / Win 10+ / Ubuntu 20.04+
NFR-UX-01: 隐私信任度 > 80%  NFR-UX-02: 首次体验完成率 > 80%
```

### FR Coverage Map

| FR | Epic | Story |
|----|------|-------|
| FR-013 | Epic 1 | 1.1~1.4 |
| IFR-001 | Epic 2 | 2.1~2.4 |
| FR-014 | Epic 3 | 3.1~3.4 |
| FR-009 (Markdown) | Epic 4 | 4.1~4.4 |
| IFR-002 | Epic 5 | 5.1~5.6 |
| NFR-USA-02 | Epic 5 | 5.6 |
| FR-007 | Epic 6 | 6.1~6.3 |
| FR-009 (Word/Excel/PDF) | Epic 7 | 7.1~7.3 |
| FR-008 | Epic 8 | 8.1~8.3 |
| FR-011 | Epic 9 | 9.1~9.4 |
| FR-010 | Epic 10 | 10.1~10.3 |
| FR-015 | Epic 11 | 11.1~11.3 |

## Epic List

| # | Epic | 优先级 | FR | Stories |
|---|------|--------|-----|---------|
| 1 | 样本小说零门槛体验 | P0 | FR-013 | 4 |
| 2 | LLM 配置管理 | P0 | IFR-001 | 4 |
| 3 | 分析成本控制 | P0-云端 | FR-014 | 4 |
| 4 | Series Bible Markdown 导出 | P0 | FR-009 | 4 |
| 5 | 桌面应用打包与发布 | P0 | IFR-002, NFR-USA-02 | 6 |
| 6 | 时间线可视化 | P1 | FR-007 | 3 |
| 7 | 高级格式导出（Word/Excel/PDF） | P1 | FR-009 | 3 |
| 8 | 势力图可视化 | P2 | FR-008 | 3 |
| 9 | 设定冲突检测 | P2 | FR-011 | 4 |
| 10 | 剧本模式 | P2 | FR-010 | 3 |
| 11 | 用户行为分析 | P2 | FR-015 | 3 |

---

## Epic 1: 样本小说零门槛体验

**目标：** 用户安装后 5 分钟内体验产品核心价值，无需配置 LLM、无需准备小说、无需等待分析。
**成功标准：** NFR-UX-02 首次体验完成率 > 80%

### Story 1.1: 制作样本小说预分析数据包

As a 开发团队,
I want 为《西游记》前25回和《三国演义》前30回生成完整的预分析数据,
So that 样本数据质量与用户自行分析的结果一致。

**Acceptance Criteria:**

**Given** 《西游记》前25回和《三国演义》前30回的 TXT 文本
**When** 使用当前最优模型（qwen3:14b）执行完整分析流程
**Then** 生成 novel 元信息 + chapters + chapter_facts + entity_dictionary + world_structure 全量数据
**And** 导出为可导入的 JSON 数据包，两本样本总计压缩后 < 4MB
**And** 数据包含 ChapterFact、实体词典、世界结构、别名映射等全部分析产物

### Story 1.2: 首次启动自动导入样本数据

As a 新用户,
I want 首次启动应用时样本小说自动出现在书架上,
So that 我无需任何操作即可开始浏览。

**Acceptance Criteria:**

**Given** 用户首次启动应用（数据库为空）
**When** 应用完成初始化
**Then** 书架自动展示《西游记》和《三国演义》两本样本小说，标记为"📖 内置样本"
**And** 样本小说可直接点击进入阅读页，所有功能（实体高亮、关系图、地图、百科、问答）均可使用
**And** 导入过程 < 3 秒，不阻塞 UI
**And** 用户可从书架删除样本小说释放空间

### Story 1.3: 引导式功能展示（教学气泡）

As a 新用户,
I want 在样本小说中看到功能引导提示,
So that 我能快速了解各功能的入口和用法。

**Acceptance Criteria:**

**Given** 用户打开样本小说的阅读页
**When** 首次进入阅读页
**Then** 显示步骤 1/4 引导气泡："试试点击高亮的人物名称，查看 AI 自动生成的角色卡片"
**And** 用户点击"知道了"后进入下一步引导（关系图→世界地图→导出）
**And** 用户点击"不再提示"后关闭所有引导
**And** 引导状态持久化到 user_state 表，每个引导点只展示一次
**And** 仅在样本小说中显示引导气泡，用户自己上传的小说不显示

### Story 1.4: 功能发现底部导航条

As a 新用户,
I want 在样本小说阅读页底部看到功能入口,
So that 我能发现并跳转到关系图、地图等可视化页面。

**Acceptance Criteria:**

**Given** 用户在样本小说的阅读页
**When** 页面加载完成
**Then** 底部显示功能发现条：📊 关系图 / 🗺️ 世界地图 / 📅 时间线 / 📤 导出
**And** 点击可跳转到对应页面
**And** 完成 4 步引导后显示"✅ 体验完成！[上传我自己的小说] [继续探索]"

---

## Epic 2: LLM 配置管理

**目标：** 用户可以通过图形界面配置 LLM，无需编辑环境变量或配置文件。系统自动检测本地 Ollama 并推荐模型。
**依赖：** 无（独立 Epic）

### Story 2.1: Ollama 自动检测与状态显示

As a 用户,
I want 应用自动检测本地 Ollama 的安装和运行状态,
So that 我能了解当前环境是否就绪。

**Acceptance Criteria:**

**Given** 用户打开设置页 > AI 引擎面板，或首次启动进入配置向导
**When** 页面加载时
**Then** 自动检测 Ollama 状态：已安装已运行 / 已安装未运行 / 未安装
**And** 显示可用模型列表（已下载的模型名称和大小）
**And** 未安装时提供"下载安装 Ollama"链接（打开 ollama.com）
**And** 已安装未运行时提供"启动 Ollama"按钮
**And** 检测过程 < 2 秒，失败时显示具体错误

### Story 2.2: 模型推荐与下载引导

As a 用户,
I want 系统根据我的硬件推荐合适的分析模型,
So that 我能选择最适合的模型而不需要了解技术细节。

**Acceptance Criteria:**

**Given** Ollama 已安装但没有可用的分析模型
**When** 进入模型推荐界面
**Then** 显示硬件检测结果（如"Apple M2 Pro, 16GB 内存"）
**And** 推荐 2-3 个模型（如 qwen3:4b / qwen3:8b / qwen3:14b），标注推荐级别和内存要求
**And** 用户点击"下载"后执行 `ollama pull` 并显示下载进度
**And** 下载完成后自动设为默认模型

### Story 2.3: 云端 LLM API Key 配置

As a 用户,
I want 通过界面配置云端 LLM 的 API Key 和提供商,
So that 我可以使用 DeepSeek 等云端服务进行分析。

**Acceptance Criteria:**

**Given** 用户在设置页选择"云端 API"模式
**When** 输入 API Key 和选择提供商
**Then** 提供商下拉列表包含 DeepSeek、OpenAI 等预设（自动填充 Base URL 和默认模型）
**And** 输入 API Key 后可点击"验证"测试连通性
**And** 验证成功显示 ✅，失败显示具体错误
**And** API Key 通过 `keyring` 库存储到系统密钥库（macOS Keychain / Windows Credential Manager / Linux libsecret）
**And** API Key 绝不以明文写入配置文件或数据库
**And** fallback: 无系统密钥库时使用 AES-256-GCM 加密文件存储

### Story 2.4: AI 引擎设置面板

As a 用户,
I want 在设置页有统一的 AI 引擎配置界面,
So that 我可以随时切换本地/云端模式和调整参数。

**Acceptance Criteria:**

**Given** 用户打开设置页 > AI 引擎
**When** 页面加载
**Then** 显示两个模式切换：本地 Ollama（推荐）/ 云端 API
**And** 本地模式显示：状态、模型选择下拉、地址、测试连接按钮
**And** 云端模式显示：提供商、API Key（掩码）、模型、Base URL
**And** 高级选项：最大 Token 数、请求超时、并发请求数
**And** "保存"后配置立即生效（无需重启应用）
**And** "恢复默认"还原为本地 Ollama + qwen3:8b

---

## Epic 3: 分析成本控制

**目标：** 云端 LLM 用户可以预知、追踪和控制分析费用，避免意外高额消费。
**依赖：** Epic 2（云端 LLM 配置）

### Story 3.1: 成本预估模型

As a 云端 LLM 用户,
I want 在分析前看到预估费用,
So that 我可以决定是否继续或调整分析范围。

**Acceptance Criteria:**

**Given** 用户使用云端 LLM 点击"开始分析"
**When** 弹出成本预览弹窗
**Then** 显示：小说名称、分析范围（章节数/字数）、LLM 提供商
**And** 显示预估 Token 消耗（输入/输出分开）和预估费用（基于提供商定价）
**And** 显示含/不含实体预扫描的费用差异
**And** 显示"实际费用可能浮动 ±30%"提示
**And** 显示本月已用预算和预算剩余
**And** 提供"切换到本地 Ollama 可免费分析"选项
**And** 用户确认后开始分析，取消则返回

### Story 3.2: 实时成本追踪

As a 云端 LLM 用户,
I want 在分析过程中看到实时费用,
So that 我可以随时决定是否暂停分析。

**Acceptance Criteria:**

**Given** 云端分析正在进行
**When** 分析进度页面加载
**Then** 在进度条下方显示实时统计：已用 Token、已花费、预估剩余、预估总计
**And** 每完成一章更新一次成本数据
**And** 显示本月预算使用进度条
**And** Token 消耗和费用数据随 WebSocket 进度消息一起推送

### Story 3.3: 预算告警机制

As a 云端 LLM 用户,
I want 在接近预算上限时收到告警,
So that 我不会无意中超出预算。

**Acceptance Criteria:**

**Given** 用户在设置中配置了月度预算上限
**When** 本月消费达到预算的 80%
**Then** Toast 提示"本月云端预算已用 80%，剩余 ¥X"
**And** 达到 100% 时弹窗确认："已达预算上限。[继续] [暂停] [切换本地]"
**And** 单次分析预估超出剩余预算时，分析前提示并提供选项
**And** 预算设置在设置页 > 使用统计中，默认值 ¥50/月

### Story 3.4: 分析成本明细

As a 云端 LLM 用户,
I want 分析完成后查看按章节的费用明细,
So that 我可以了解成本构成。

**Acceptance Criteria:**

**Given** 一次云端分析已完成
**When** 用户打开设置 > 使用统计 > 分析记录
**Then** 显示每章的输入 Token、输出 Token、费用、发现实体数
**And** 显示合计行
**And** 支持导出为 CSV
**And** 显示分析时间段和使用的 LLM 模型

---

## Epic 4: Series Bible Markdown 导出

**目标：** 用户可以将分析结果一键导出为结构化的 Markdown 设定文档，适合网文作者日常使用。
**依赖：** 无（使用已有分析数据）

### Story 4.1: 导出引擎核心架构

As a 用户,
I want 一个可扩展的导出引擎,
So that 当前支持 Markdown，未来可扩展 Word/Excel/PDF。

**Acceptance Criteria:**

**Given** 一本已完成分析的小说
**When** 用户触发导出
**Then** 后端 ExportService 从 EntityAggregator 获取人物/地点/物品/组织 Profile 数据
**And** 从 VizService 获取关系图、地图、时间线数据
**And** 支持按章节范围导出（chapter_start / chapter_end）
**And** 支持选择导出模块：人物档案、势力分布、地图标注、时间线
**And** 导出 API: `POST /api/novels/{id}/export` 返回文件下载

### Story 4.2: Markdown 模板系统

As a 网文作者,
I want 选择不同的 Markdown 导出模板,
So that 我可以得到适合自己工作流的格式。

**Acceptance Criteria:**

**Given** 用户选择 Markdown 格式导出
**When** 选择模板
**Then** 提供"网文作者套件"模板：人物设定卡、势力分布、时间线大纲
**And** 提供"通用模板"：完整世界观文档
**And** Markdown 输出为单个 .md 文件，含目录、分级标题
**And** 人物卡片包含：姓名、别称、外貌、关系、能力、经历
**And** 500 章小说完整 Markdown 导出 < 30 秒

### Story 4.3: 导出中心 UI

As a 用户,
I want 在导航栏有导出入口，进入后选择格式、模块、模板,
So that 导出过程直观便捷。

**Acceptance Criteria:**

**Given** 用户在任意小说功能页面
**When** 点击导航栏"导出"
**Then** 进入导出中心页面（/novel/:id/export）
**And** 显示格式选择卡片（Markdown 可用，Word/Excel/PDF 显示"即将推出"）
**And** 选择格式后显示内容模块勾选和模板下拉
**And** 提供"预览"按钮（在新窗口预览 Markdown 渲染效果）
**And** 提供"导出"按钮，下载 .md 文件

### Story 4.4: 导出内容质量

As a 用户,
I want 导出的 Markdown 内容准确反映分析结果,
So that 我可以直接用于创作参考。

**Acceptance Criteria:**

**Given** 一本分析完成的小说
**When** 导出完整 Markdown
**Then** 人物档案中的关系演变链与实体卡片一致
**And** 别称列表完整（包含 AliasResolver 合并的所有别称）
**And** 地点层级正确反映 WorldStructure 的 location_parents
**And** 无乱码、无截断、Markdown 语法正确

---

## Epic 5: 桌面应用打包与发布

**目标：** 用户可以在 macOS/Windows 上下载安装原生桌面应用，获得系统级体验，并可导出/导入全量数据实现跨设备迁移。
**依赖：** 无（打包现有功能）

### Story 5.1: Tauri 项目脚手架

As a 开发团队,
I want 将现有 React + FastAPI 项目集成到 Tauri 2.x 框架中,
So that 可以生成跨平台桌面应用。

**Acceptance Criteria:**

**Given** 现有的 frontend/ 和 backend/ 代码
**When** 初始化 Tauri 项目
**Then** Tauri 配置指向 frontend/ 的 Vite 构建产物
**And** Python 后端通过 PyInstaller 编译为 sidecar binary
**And** Tauri externalBin 配置引用 sidecar
**And** 开发模式下 `cargo tauri dev` 可同时启动前端和后端
**And** 窗口标题为"AI Reader V2"，最小尺寸 1024×768

### Story 5.2: Python Sidecar 打包

As a 开发团队,
I want 将 FastAPI 后端打包为独立可执行文件,
So that 用户无需安装 Python 环境。

**Acceptance Criteria:**

**Given** backend/ 下的 FastAPI 应用
**When** 使用 PyInstaller 打包
**Then** 生成 macOS ARM64、macOS x64、Windows x64 三个平台的 sidecar binary
**And** 包含所有依赖（aiosqlite, chromadb, jieba, numpy 等）
**And** `--exclude-module` 排除未使用的大型库
**And** sidecar 启动后监听指定端口，Tauri 前端通过 localhost 通信
**And** sidecar 体积 < 200MB（压缩后）

### Story 5.3: macOS 签名与公证

As a macOS 用户,
I want 下载的应用通过 Gatekeeper 验证,
So that 我可以正常安装而不需手动允许。

**Acceptance Criteria:**

**Given** macOS .dmg 安装包
**When** 用户双击安装
**Then** Gatekeeper 不弹出"未知开发者"警告
**And** 应用通过 Apple notarytool 公证
**And** sidecar binary 已单独 ad-hoc 签名后再由 Tauri 整体签名
**And** 使用 Apple Developer Program 证书（$99/年）

### Story 5.4: Windows 签名与安装

As a Windows 用户,
I want 安装时不看到 SmartScreen 警告,
So that 我信任这个应用是安全的。

**Acceptance Criteria:**

**Given** Windows .msi 安装包
**When** 用户双击安装
**Then** SmartScreen 不显示"未知发布者"警告
**And** 安装包使用 EV Code Signing Certificate 签名
**And** 安装后开始菜单有快捷方式
**And** 首次运行检测 WebView2，未安装时引导安装

### Story 5.5: 自动更新与 CI/CD

As a 用户,
I want 应用启动时自动检测更新,
So that 我始终使用最新版本。

**Acceptance Criteria:**

**Given** 应用启动
**When** 检测到新版本
**Then** 显示更新提示（版本号 + 更新说明）
**And** 用户可选"立即更新""稍后提醒""跳过此版本"
**And** 更新包签名验证通过后安装并重启
**And** GitHub Actions 自动构建 macOS ARM64/x64 + Windows x64 + Linux x64
**And** 构建产物发布到 GitHub Releases + 更新服务器 JSON manifest

### Story 5.6: 数据导出与迁移

As a 用户,
I want 一键导出全部数据为可迁移的格式,
So that 我可以在新设备上恢复所有小说和分析结果。

**Acceptance Criteria:**

**Given** 用户在设置页点击"导出数据"
**When** 导出开始执行
**Then** 生成包含所有小说、章节、分析结果、用户配置的 JSON 数据包
**And** 数据包以 .zip 格式压缩，文件名含导出日期
**And** 提供"导入数据"入口，选择 .zip 文件后恢复全部数据
**And** 导入时检测冲突（已存在的小说）并提示"覆盖/跳过/合并"
**And** 导入/导出不包含 API Key（安全考虑，需在新设备重新配置）

---

## Epic 6: 时间线可视化

**目标：** 用户可以在时间线视图中浏览小说事件的时间演进，快速把握故事脉络。
**依赖：** 无（使用已有 ChapterFact 数据）

### Story 6.1: 时间线数据聚合 API

As a 用户,
I want 后端提供时间线数据接口,
So that 前端可以渲染事件时间线。

**Acceptance Criteria:**

**Given** 一本已分析的小说
**When** 前端请求 `GET /api/novels/{id}/timeline?chapter_start=1&chapter_end=100`
**Then** 返回按章节排序的事件列表，每个事件包含：chapter_number, event_type, description, characters, location
**And** 事件类型分类：角色登场/退场、关键冲突、地点转移、物品交接、组织变动
**And** 关键事件（涉及 3+ 角色）标记为 is_major: true
**And** 响应时间 < 1 秒（500 章）

### Story 6.2: 时间线可视化组件

As a 用户,
I want 在时间线页面浏览小说事件的时间演进,
So that 我可以快速把握故事脉络。

**Acceptance Criteria:**

**Given** 时间线数据已加载
**When** 用户访问 /novel/:id/timeline
**Then** 横轴显示章节刻度，事件以圆点表示
**And** 关键事件以大节点高亮
**And** 悬停事件显示摘要 tooltip
**And** 点击事件展开详情卡片（原文摘要 + 涉及角色 + 章节链接）
**And** 500 章/2000 事件节点 < 3 秒渲染
**And** 滚动和缩放帧率 ≥ 30fps

### Story 6.3: 时间线筛选与缩放

As a 用户,
I want 按角色和事件类型筛选时间线,
So that 我可以聚焦特定角色或事件。

**Acceptance Criteria:**

**Given** 时间线已渲染
**When** 用户使用筛选面板
**Then** 可按角色多选筛选（仅显示所选角色相关事件）
**And** 可按事件类型多选筛选
**And** 鼠标滚轮/捏合缩放，从全书概览到单章详情
**And** 支持章节范围选择器（与其他可视化页面共享 chapterRangeStore）

---

## Epic 7: 高级格式导出（Word/Excel/PDF）

**目标：** 付费用户可以将分析结果导出为 Word、Excel、PDF 格式，满足传统出版、IP 改编、通用分享等场景需求。
**依赖：** Epic 4（导出引擎核心 + Markdown 模板系统）

### Story 7.1: Word 导出

As a 传统出版作者/编辑,
I want 将 Series Bible 导出为 Word 文档,
So that 我可以用编辑的标准工作流审阅。

**Acceptance Criteria:**

**Given** 用户在导出中心选择 Word 格式
**When** 点击导出
**Then** 生成 .docx 文件，包含目录、页码、页眉
**And** 人物档案、关系表、地点层级等内容与 Markdown 版一致
**And** 使用 python-docx 生成
**And** 500 章导出 < 60 秒

### Story 7.2: Excel 导出

As a IP 改编编辑/游戏策划,
I want 将分析结果导出为 Excel 表格,
So that 我可以用表格管理角色和设定数据。

**Acceptance Criteria:**

**Given** 用户在导出中心选择 Excel 格式
**When** 选择"游戏策划套件"模板
**Then** 生成 .xlsx 文件，包含多个 Sheet：角色表、势力表、物品/技能表、场景表
**And** 角色表列：名称、别称、性别、阵营、能力、首次登场章节
**And** 支持自定义字段选择
**And** 使用 openpyxl 生成

### Story 7.3: PDF 导出

As a 用户,
I want 导出专业排版的 PDF 文档,
So that 我可以分享只读版本的设定文档。

**Acceptance Criteria:**

**Given** 用户在导出中心选择 PDF 格式
**When** 点击导出
**Then** 生成含目录、页码、页眉的 PDF 文件
**And** 排版美观（标题层级、表格、列表格式正确）
**And** 使用 weasyprint 或 reportlab 生成
**And** 500 章导出 < 90 秒

---

## Epic 8: 势力图可视化

**目标：** 用户可以查看小说中势力阵营的层级结构和动态演变。
**依赖：** 无（使用已有 ChapterFact + WorldStructure 数据）

### Story 8.1: 势力数据聚合 API

As a 用户,
I want 后端提供势力/阵营数据接口,
So that 前端可以渲染势力图。

**Acceptance Criteria:**

**Given** 一本已分析的小说
**When** 请求 `GET /api/novels/{id}/factions?chapter_start=1&chapter_end=100`
**Then** 返回组织列表（含层级结构、成员列表）和组织间关系（同盟/敌对/从属）
**And** 每个组织含：name, type, members[], sub_orgs[], relations[]
**And** 支持章节范围过滤（仅该范围内出现的组织和关系）

### Story 8.2: 势力图可视化组件

As a 用户,
I want 在势力图页面查看阵营分布和关系,
So that 我可以理解小说中的势力格局。

**Acceptance Criteria:**

**Given** 势力数据已加载
**When** 用户访问 /novel/:id/factions
**Then** 显示树形/层级图，组织内部展示成员
**And** 势力间关系以连线表示：同盟=绿实线、敌对=红虚线、从属=灰箭头
**And** 节点大小按成员数量缩放
**And** 20 个组织/200 个成员 < 2 秒渲染

### Story 8.3: 势力演变与交互

As a 用户,
I want 通过章节范围滑块查看势力变化,
So that 我可以追踪势力格局的演变。

**Acceptance Criteria:**

**Given** 势力图已渲染
**When** 用户拖动章节范围滑块
**Then** 势力图动态更新，展示该范围内的势力格局
**And** 点击组织节点展开详情卡片
**And** 支持按类型筛选（政治/军事/宗教/家族）
**And** 支持组织内部层级展开/折叠

---

## Epic 9: 设定冲突检测

**目标：** 用户可以自动发现作品中的设定矛盾，保障内容一致性。
**依赖：** 无（使用已有 ChapterFact 数据）

### Story 9.1: 角色属性冲突检测引擎

As a 网文作者,
I want 系统自动检测角色属性矛盾,
So that 我能避免"吃书"。

**Acceptance Criteria:**

**Given** 一本已分析的小说
**When** 用户触发冲突检测
**Then** 检测角色年龄矛盾（如"第10章18岁 vs 第50章20岁但叙述仅隔1年"）
**And** 检测角色能力矛盾（如能力突然消失或矛盾描述）
**And** 每个冲突标注严重程度：严重/一般/提示
**And** 每个冲突附带矛盾的原文片段和章节号

### Story 9.2: 地点与关系冲突检测

As a 用户,
I want 系统检测地点层级和关系逻辑矛盾,
So that 我的世界观设定保持一致。

**Acceptance Criteria:**

**Given** 冲突检测正在执行
**When** 检测完成
**Then** 检测地点层级矛盾（如同一地点在不同章节有不同上级）
**And** 检测关系演变逻辑冲突（如角色已死但后续章节以活人出场）
**And** 检测时间线冲突（事件时间顺序矛盾）
**And** 所有冲突按严重程度排序输出

### Story 9.3: 冲突报告 UI

As a 用户,
I want 在专门的页面查看冲突检测结果,
So that 我可以逐一审查和处理。

**Acceptance Criteria:**

**Given** 冲突检测完成
**When** 用户进入冲突报告页面
**Then** 显示冲突列表，按严重程度排序
**And** 每个冲突展示：类型图标、描述、涉及章节
**And** 点击冲突展开原文对比视图（并排显示矛盾段落，高亮冲突文本）
**And** 提供筛选：全部/严重/一般/已处理
**And** 用户可标记冲突为"已知"或"忽略"

### Story 9.4: 冲突检测质量保障

As a 用户,
I want 冲突检测准确可靠,
So that 我不会被大量误报干扰。

**Acceptance Criteria:**

**Given** 冲突检测引擎
**When** 使用样本小说（西游记/三国演义）进行测试
**Then** 严重冲突召回率 > 80%
**And** 误报率 < 30%
**And** 单本 500 章小说检测完成 < 5 分钟

---

## Epic 10: 剧本模式

**目标：** 编剧用户可以在剧本视图中按场景浏览小说内容，辅助小说到剧本的改编。
**状态：** 实验性功能，需编剧用户验证后决定是否正式纳入。
**依赖：** 无

### Story 10.1: 场景自动提取

As a 编剧,
I want 系统自动将章节内容拆分为场景,
So that 我可以以场景为单位进行改编。

**Acceptance Criteria:**

**Given** 一本已分析的小说章节
**When** 用户进入剧本模式
**Then** 每章自动拆分为 1-N 个场景
**And** 场景边界基于地点变化、时间跳跃、人物出场变化识别
**And** 每个场景包含：标题、时间标记、地点、角色列表、描述、对话
**And** 场景数据缓存（避免重复提取）

### Story 10.2: 并列模式视图

As a 编剧,
I want 左侧原文右侧剧本场景列表并排显示,
So that 我可以对照原文理解场景划分。

**Acceptance Criteria:**

**Given** 用户在剧本模式并列视图
**When** 页面加载
**Then** 左侧显示原文（支持滚动）
**And** 右侧显示该章节所有场景列表
**And** 同步滚动：剧本场景与原文段落联动高亮
**And** 点击场景跳转到对应原文位置

### Story 10.3: 独占模式视图

As a 编剧,
I want 全屏显示单个场景,
So that 我可以沉浸式阅读场景内容。

**Acceptance Criteria:**

**Given** 用户切换到独占模式
**When** 选择一个场景
**Then** 全屏显示场景内容（标题 + 时间 + 地点 + 角色 + 描述 + 对话）
**And** 顶部场景标签导航可快速切换
**And** 支持键盘快捷键（←/→ 切换场景）

---

## Epic 11: 用户行为分析

**目标：** 产品团队获取匿名化使用数据，为产品迭代提供数据支撑，同时尊重用户隐私。
**依赖：** 无

### Story 11.1: 事件追踪基础设施

As a 产品团队,
I want 在前端建立事件追踪系统,
So that 可以记录用户的功能使用行为。

**Acceptance Criteria:**

**Given** 应用运行中
**When** 用户执行关键操作（上传、分析、导出、查看卡片、打开问答等）
**Then** 在本地 SQLite 记录匿名事件：event_type, metadata, timestamp
**And** 不记录小说内容、用户身份、IP 地址
**And** 事件数据仅本地存储，不自动上传
**And** 代码统一通过 `trackEvent()` 函数调用

### Story 11.2: 使用统计仪表盘

As a 产品团队,
I want 在设置页查看本地使用统计,
So that 可以了解功能使用情况。

**Acceptance Criteria:**

**Given** 用户使用一段时间后
**When** 打开设置 > 使用统计
**Then** 显示：功能使用频率排行、导出格式偏好占比、分析完成率
**And** 显示时间维度趋势图
**And** 数据完全匿名，仅用于产品优化

### Story 11.3: 隐私控制

As a 用户,
I want 可以完全关闭行为追踪,
So that 我的使用习惯不被记录。

**Acceptance Criteria:**

**Given** 用户打开设置 > 隐私
**When** 关闭"使用统计"开关
**Then** 停止记录所有行为事件
**And** 可选择删除已收集的历史数据
**And** 默认状态：开启（首次启动时告知用户并提供关闭选项）

---

*文档完成于 2026-02-17。共 11 个 Epic、41 个 Story。（IR 修复：拆分 Epic 6 + 新增 Story 5.6）*
