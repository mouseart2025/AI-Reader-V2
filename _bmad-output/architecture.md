---
stepsCompleted: [1, 2]
inputDocuments:
  - PRD.md
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
  - (ref) ai-reader/docs/CURRENT_SYSTEM_ARCHITECTURE.md
workflowType: 'architecture'
project_name: 'AI-Reader-V2'
user_name: 'leonfeng'
date: '2026-02-11'
---

# AI Reader V2 架构决策文档

_本文档通过逐步协作发现构建。各章节在完成每个架构决策后追加。_

---

## 1. 项目上下文分析

### 1.1 需求概览

PRD 定义了 **83 个功能项**（F-01 到 F-83），按 8 个模块组织：

| 模块 | P0 | P1 | P2 | 架构影响 |
|------|----|----|----|----|
| 书架管理 (F-01~06) | 3 | 3 | 0 | 文件解析、编码检测、章节切分引擎 |
| 小说阅读 (F-10~17) | 5 | 3 | 0 | 实体高亮渲染、实体卡片系统、阅读状态管理 |
| 实体卡片 (F-20~26) | 4 | 3 | 0 | 复杂数据聚合（7 种区块）、卡片间导航栈 |
| 知识图谱可视化 (F-30~40) | 4 | 4 | 3 | 4 种可视化引擎、视图联动、章节范围滑块共享状态 |
| 智能问答 (F-50~56) | 4 | 2 | 1 | 流式 LLM 输出、WebSocket、对话上下文管理 |
| 小说分析 (F-60~66) | 4 | 2 | 1 | 后台长任务、进度推送、断点续传 |
| 百科 (F-70~72) | 0 | 3 | 0 | 分类索引、全文搜索 |
| 系统 (F-80~83) | 1 | 3 | 0 | 环境检测、配置管理、数据导入导出 |

### 1.2 非功能需求核心约束

1. **完全本地部署** — 无网络依赖，所有数据和模型在本地
2. **Apple Silicon 优化** — M1/M2/M3/M4，MPS 加速
3. **内存约束** — 8GB 最低可用，需 ≤ 7GB（含模型）
4. **性能目标** — 章节切换 ≤ 1s，问答首字 ≤ 3s，图谱 500 节点流畅
5. **仅中文** — 规则、提示词和 NLP 处理均为中文
6. **隐私** — 无遥测、无外部请求

### 1.3 复杂度评估

- 项目复杂度：**高**
- 主要技术领域：**全栈 (Full-stack Web + AI/NLP + 数据可视化)**
- 预估架构组件：**~25 个**

### 1.4 关键架构挑战

1. **4 种异构可视化引擎** — 力导向图、节点区域空间地图（Canvas）、时间线、势力网络图，需要统一的数据接口和章节范围联动机制
2. **复合实体卡片系统** — 4 种实体卡片 + 概念浮层，7 种数据区块，卡片间导航栈（面包屑）、消歧选择
3. **LLM 分析流水线** — V1 的 10 步流水线需重构为可暂停/恢复/部分重试的任务系统
4. **混合存储统一** — V1 有 7 个重叠 Store（已知痛点），V2 需统一为清晰的存储模型
5. **实时进度推送** — 分析进度、流式问答都需要 WebSocket，且前后端需协调
6. **世界地图语义缩放** — 5 级缩放 + 区域渲染 + 人物轨迹动画，Canvas 性能要求高

### 1.5 跨领域关注点

- **章节范围状态** — 4 个可视化视图 + 时间线 + 筛选面板共享同一章节范围
- **实体系统** — 5 种实体类型的颜色编码、高亮、卡片弹出贯穿所有页面
- **部分分析提示** — 所有页面需处理"仅部分章节已分析"的降级状态
- **加载状态** — 骨架屏、逐步渲染、流式输出各有不同策略
- **错误处理** — LLM 不可用、分析失败等需要全局通知机制

### 1.6 与 V1 的关系

V2 是**新仓库新架构**。V1 的存储设计（7 个 Store）、紧耦合流水线和扁平化数据模型不适合 V2 需求，需全面重新设计。V1 在空间关系规则抽取方面的积累（120+ 正则模式）可作为参考，但不直接复用代码。

---

## 2. 核心架构决策：章节级结构化事实抽取

### 2.1 决策背景

V1 的抽取系统存在根本性的模型-需求不匹配：

**V1 的扁平化抽取模型：**

- 实体抽取：LLM 逐章提取实体名+类型，每章限 2000 字符，章节间无上下文
- 关系抽取：在实体列表上二次调用 LLM，每章限 20 个实体，12 种固定关系类型
- 质量保障：300+ 停用词、50+ 正则模式、硬编码后缀列表（规则比主逻辑更复杂）
- 结果：扁平的 `Entity(name, type, confidence)` 和 `Relation(source, type, target)`

**V2 PRD 需要的数据：**

| 数据类型 | V1 能力 | V2 需求 |
|---------|---------|---------|
| 关系演变链（师徒→友人→仇人，每步有章节） | 无 | 必须 |
| 物品流转链（炼制→赠予→使用→消耗） | 无 | 必须 |
| 能力成长线（练气→筑基→结丹） | 无 | 必须 |
| 外貌变化（按章节记录） | 无 | 必须 |
| 人物经历（每章：在哪+和谁+做了什么） | 无 | 必须 |
| 组织成员变动（加入→晋升→离开） | 无 | 必须 |
| 概念定义和分类 | 仅有 Concept 类型标记 | 需要定义+分类+关联 |
| 地点空间层级 | 部分（规则提取） | 必须 |

**V1 能力覆盖率约 20%。**

### 2.2 决策：采用章节级结构化事实抽取（ChapterFact）

**核心思路转变：**

| 维度 | V1 | V2 决策 |
|------|----|----|
| 抽取单位 | "这本书有哪些实体/关系" | "这一章发生了什么事实" |
| 数据模型 | 扁平实体 + 扁平关系 | **ChapterFact（章节事实）** |
| 聚合方式 | 无 | ChapterFact → 聚合为实体档案 |
| LLM 调用/章 | 2 次（先实体、后关系） | **1 次**（统一提取） |
| 上下文 | 无（章节隔离） | 携带前序章节的实体状态摘要 |
| 关系类型 | 12 种固定枚举 | 自由文本，聚合层归类 |
| 增量更新 | 不支持 | 天然支持（重新抽取单章即可） |
| 错误恢复 | 不支持 | 天然支持（每章独立，失败可重试） |

### 2.3 ChapterFact 数据模型

每章产出一个 ChapterFact 结构，包含该章的全部结构化事实：

```
ChapterFact {
  chapter_id: int
  novel_id: str

  // ── 人物 ──────────────────────────────
  characters: [{
    name: str                       // 主要名称
    new_aliases: [str]              // 本章新出现的别称
    appearance: str | null          // 本章的外貌描写（仅当有描写时）
    abilities_gained: [{            // 本章获得/提升的能力
      dimension: str                //   "境界" / "技能" / "身份"
      name: str                     //   "练气三层" / "火球术" / "外门弟子"
      description: str              //   简要说明
    }]
    locations_in_chapter: [str]     // 本章到过的地点
  }]

  // ── 人物关系 ──────────────────────────
  relationships: [{
    person_a: str
    person_b: str
    relation_type: str              // 自由文本，如"师徒"、"同门"、"仇人"
    is_new: bool                    // 是否首次建立
    previous_type: str | null       // 如果关系变化，旧关系是什么
    evidence: str                   // 原文依据（1-2 句）
  }]

  // ── 地点 ──────────────────────────────
  locations: [{
    name: str
    type: str                       // 国家/城市/山脉/门派/建筑/房间/...
    parent: str | null              // 上级地点（如"药园"的 parent 是"七玄门"）
    description: str | null         // 本章的环境描写（仅当有描写时）
  }]

  // ── 物品事件 ──────────────────────────
  item_events: [{
    item_name: str
    item_type: str                  // 武器/丹药/法宝/材料/书籍/...
    action: str                     // 出现/获得/使用/赠予/消耗/丢失/损毁
    actor: str                      // 动作执行者
    recipient: str | null           // 接收者（赠予时）
    description: str | null         // 简要说明
  }]

  // ── 组织变动 ──────────────────────────
  org_events: [{
    org_name: str
    org_type: str                   // 门派/家族/势力/国家/...
    member: str | null              // 涉及成员
    role: str | null                // 职位/身份
    action: str                     // 加入/离开/晋升/阵亡/叛出/逐出
    description: str | null
    org_relation: {                 // 组织间关系变化（如有）
      other_org: str
      type: str                     // 盟友/敌对/从属/竞争
    } | null
  }]

  // ── 事件 ──────────────────────────────
  events: [{
    summary: str                    // 一句话概要
    type: str                       // 战斗/成长/社交/旅行/其他
    importance: str                 // high / medium / low
    participants: [str]             // 涉及人物
    location: str | null            // 发生地点
  }]

  // ── 新概念 ──────────────────────────
  new_concepts: [{
    name: str
    category: str                   // 修炼体系/种族/货币/功法/...
    definition: str                 // 从原文提炼的解释
    related: [str]                  // 关联概念
  }]
}
```

### 2.4 聚合层：ChapterFact → 实体档案

ChapterFact 是原始数据层。上层通过聚合产出 PRD 所需的实体档案：

```
全部 ChapterFact
  │
  ├── 按人物名聚合 → PersonProfile
  │   ├── 合并 aliases（按首次出现章节排序）
  │   ├── 收集 appearance 列表（按章节排序，展示外貌变化）
  │   ├── 收集 abilities_gained（按章节排序、按维度分组）
  │   ├── 从 relationships 构建关系演变链
  │   │   例: 韩立↔墨大夫: 初见(ch3) → 师徒(ch3) → 朋友(ch16) → 怀念(ch50)
  │   ├── 从 item_events 构建物品关联和流转
  │   ├── 从 events 构建人物经历（每章概要）
  │   └── 统计：出场章节数、首末出场、关联人物数...
  │
  ├── 按地点名聚合 → LocationProfile
  │   ├── 构建空间层级树（parent-child）
  │   ├── 收集 description 列表（按章节排序）
  │   ├── 反向聚合到访人物（从 characters.locations_in_chapter）
  │   │   区分常驻（出现 N 章以上）和到访
  │   ├── 从 events 构建地点事件
  │   └── 统计
  │
  ├── 按物品名聚合 → ItemProfile
  │   ├── 从 item_events 构建持有流转链
  │   │   例: 墨大夫 炼制(ch38) → 赠予 韩立(ch40) → 韩立 服用(ch45, 已消耗)
  │   ├── 收集 description（按章节排序）
  │   ├── 关联物品（共同出现在同一 item_events 中的其他物品）
  │   └── 统计
  │
  ├── 按组织名聚合 → OrgProfile
  │   ├── 从 org_events 构建成员变动历史
  │   ├── 构建组织层级和组织间关系
  │   ├── 从 locations 关联据点/领地
  │   └── 统计
  │
  ├── events 按章节汇总 → Timeline
  │
  └── new_concepts 汇总 → Encyclopedia（按 category 分类）
```

### 2.5 LLM 调用策略

**每章一次 LLM 调用，携带前序上下文：**

```
┌─────────────────────────────────────────────┐
│ System Prompt                                │
│ - 角色：小说分析专家                           │
│ - 规则：仅抽取文本明确提及的事实，不推测         │
│ - 输出：严格按 ChapterFact JSON Schema        │
├─────────────────────────────────────────────┤
│ Context（前序状态摘要，自动从已分析章节生成）     │
│ - 已知人物：韩立（药园学徒）、墨大夫（管事）...  │
│ - 已知关系：韩立↔墨大夫(师徒)、韩立↔张铁(同门)  │
│ - 已知地点：七玄门 > 药园、练功房...             │
│ - 已知物品：小绿瓶（韩立持有）...               │
├─────────────────────────────────────────────┤
│ Chapter Text（完整章节文本，不截断）             │
│ Qwen 2.5 7B 上下文窗口 128K，足够处理完整章节   │
├─────────────────────────────────────────────┤
│ Output: ChapterFact JSON                     │
└─────────────────────────────────────────────┘
```

**前序状态摘要的生成规则：**

- 从已完成的 ChapterFact 中自动聚合
- 只包含活跃实体（最近 N 章出现过的人物/地点/物品）
- 控制在 2000 token 以内，避免挤压章节文本的空间
- 随着章节推进，旧的非活跃实体逐步淡出摘要

### 2.6 质量保障策略

**不再依赖规则堆积，改为分层保障：**

| 层级 | 策略 | 说明 |
|------|------|------|
| Schema 约束 | JSON Schema 强制格式 | Qwen 2.5 原生支持 structured output |
| Few-shot 示例 | Prompt 中包含 1-2 个完整 ChapterFact 示例 | 引导 LLM 理解预期粒度 |
| 轻量后验证 | 名称长度 2-10 字符、类型合法、引用人物在上下文中存在 | 替代 V1 的 300+ 规则 |
| 跨章一致性 | 别名合并（编辑距离）、实体消歧（上下文相似度） | 在聚合层处理 |
| 辅助规则 | 地点层级正则增强（复用 V1 空间规则精华） | 仅作为补充，非主要手段 |

### 2.7 架构优势

1. **PRD 需求完整覆盖** — ChapterFact 模型直接映射到 PRD 的 4 种实体卡片所需的全部数据
2. **天然增量更新** — 重新分析某章只需重新抽取该章的 ChapterFact，聚合层自动重建
3. **天然错误恢复** — 某章抽取失败不影响其他章节，可独立重试
4. **天然支持暂停/恢复** — 按章逐个处理，任何章节边界都是安全的暂停点
5. **LLM 调用效率** — 每章 1 次调用（V1 需要 2 次），且上下文利用更充分
6. **数据可溯源** — 所有数据锚定到具体章节，PRD 要求的"来源章节"开箱即用

---

## 3. 技术栈决策

### 3.1 总览

| 层级 | 技术 | 选型理由 |
|------|------|----------|
| **前端框架** | React 18 + TypeScript | 复杂状态管理生态最成熟；可视化库支持最广；Tauri 兼容 |
| **构建工具** | Vite | 开发体验快，HMR 即时生效 |
| **状态管理** | Zustand | 轻量、TypeScript 友好、支持 middleware；比 Redux 简洁 |
| **UI 组件库** | shadcn/ui + Tailwind CSS | 可定制、无运行时依赖、暗/亮主题原生支持 |
| **路由** | React Router v6 | 10 个页面的标准路由方案 |
| **后端框架** | Python FastAPI | PRD 指定，V1 已验证，异步性能好 |
| **WebSocket** | FastAPI WebSocket | 分析进度推送 + 流式问答 |
| **关系数据库** | SQLite (aiosqlite) | 本地部署、零配置、JSON 列支持、异步访问 |
| **向量数据库** | ChromaDB | V1 已验证，HNSW 索引，cosine 相似度 |
| **LLM** | Ollama + Qwen 2.5 (7B/14B) | 本地推理，structured output 支持 |
| **Embedding** | BGE-base-zh-v1.5 | 768 维中文向量，MPS 加速 |
| **ORM** | 不使用 ORM | 直接 SQL + dataclass，避免 ORM 在 JSON 列和聚合查询上的开销 |

### 3.2 前端可视化引擎

PRD 定义了 4 种可视化视图，各有不同的渲染需求：

| 视图 | 引擎 | 选型理由 |
|------|------|----------|
| **人物关系图** | @react-force-graph-2d (基于 d3-force + Canvas) | 500 节点流畅；节点拖拽、缩放、hover 内置；2D Canvas 性能优于 SVG |
| **世界地图** | Pixi.js v8 + @pixi/react | PRD 建议高性能 2D Canvas；语义缩放需自定义层级控制；羊皮纸风格纹理渲染 |
| **时间线** | 自研 Canvas 组件 (基于 d3-scale + d3-axis) | 需要章节轴 + 多泳道 + 框选缩放，现有时间线库不支持小说场景的定制需求 |
| **势力图** | 复用 @react-force-graph-2d | 与关系图同引擎，节点/边样式不同，可共享底层 |

**共享数据层**：4 个视图共享同一个 `ChapterRangeContext`（章节范围状态），通过 Zustand store 联动。

### 3.3 前端关键依赖

| 用途 | 库 | 说明 |
|------|-----|------|
| HTTP 客户端 | ky (或 fetch 封装) | 轻量，TypeScript 友好 |
| WebSocket 客户端 | 原生 WebSocket | 无需额外库 |
| 图标 | lucide-react | shadcn/ui 配套，树摇优化 |
| Markdown 渲染 | react-markdown | 问答回答的 Markdown 渲染 |
| 虚拟滚动 | @tanstack/react-virtual | 长章节列表、百科词条列表的性能 |
| 图表统计 | recharts | 分析页面的简单统计图表 |

### 3.4 不采用的方案及理由

| 被否决方案 | 理由 |
|-----------|------|
| Vue / Svelte | React 在复杂可视化场景的生态更丰富（force-graph、pixi-react 等） |
| Next.js | 本项目是 SPA + localhost，不需要 SSR/SSG |
| Neo4j | 本地部署过重；图查询需求可用 SQLite 聚合 + 内存计算覆盖 |
| PostgreSQL | 需要独立服务进程，不适合本地单用户工具 |
| D3.js 直接操作 DOM | 与 React 虚拟 DOM 冲突；通过 Canvas 库间接使用 D3 的计算能力 |
| Tiled / Phaser | PRD 明确说明世界地图不是 tile 地图 |
| SQLAlchemy ORM | ChapterFact 是深嵌套 JSON，ORM 映射复杂且低效 |

---

## 4. 系统分层架构

### 4.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│  前端层 (React + TypeScript)                                     │
│  ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ 页面组件    │ │ 实体卡片  │ │ 可视化    │ │ 状态管理         │  │
│  │ (10 pages) │ │ 系统     │ │ (4 引擎)  │ │ (Zustand stores) │  │
│  └─────┬──────┘ └────┬─────┘ └────┬─────┘ └──────┬───────────┘  │
│        └──────────────┴────────────┴──────────────┘              │
│                          │ REST + WebSocket                      │
├──────────────────────────┼──────────────────────────────────────┤
│  API 层 (FastAPI)        │                                       │
│  ┌───────────────────────┴───────────────────────────────────┐  │
│  │  REST Routes              │  WebSocket Handlers            │  │
│  │  /api/novels              │  /ws/analysis/{novel_id}       │  │
│  │  /api/novels/:id/chapters │  /ws/chat/{session_id}         │  │
│  │  /api/novels/:id/entities │                                │  │
│  │  /api/novels/:id/graph    │  Pydantic Schemas              │  │
│  │  /api/novels/:id/map      │  (Request/Response Models)     │  │
│  │  /api/novels/:id/timeline │                                │  │
│  │  /api/novels/:id/chat     │                                │  │
│  │  /api/settings            │                                │  │
│  └───────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│  服务层 (Business Logic)                                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
│  │ NovelService │ │AnalysisService│ │ EntityAggregator         │ │
│  │ - 上传解析   │ │ - 任务队列    │ │ - ChapterFact → Profile  │ │
│  │ - 章节切分   │ │ - 进度追踪    │ │ - PersonProfile          │ │
│  │ - 编码检测   │ │ - 暂停/恢复   │ │ - LocationProfile        │ │
│  │ - 重复检测   │ │ - 失败重试    │ │ - ItemProfile            │ │
│  └──────────────┘ └──────────────┘ │ - OrgProfile             │ │
│  ┌──────────────┐ ┌──────────────┐ │ - Timeline               │ │
│  │ QueryService │ │  VizService  │ │ - Encyclopedia           │ │
│  │ - 混合检索   │ │ - 关系图数据  │ └──────────────────────────┘ │
│  │ - 推理链     │ │ - 地图数据    │                              │
│  │ - 流式输出   │ │ - 时间线数据  │                              │
│  │ - 来源标注   │ │ - 势力图数据  │                              │
│  └──────────────┘ └──────────────┘                              │
├─────────────────────────────────────────────────────────────────┤
│  抽取层 (Extraction)                                             │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  ChapterFactExtractor                                      │ │
│  │  - LLM 调用 (structured output)                            │ │
│  │  - ContextSummaryBuilder (前序状态摘要)                     │ │
│  │  - FactValidator (轻量后验证)                               │ │
│  │  - SpatialRuleEnhancer (地点层级正则增强，可选)             │ │
│  └────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│  存储层 (Storage)                                                │
│  ┌──────────────────────┐  ┌─────────────────────────────────┐  │
│  │  SQLite               │  │  ChromaDB                       │  │
│  │  - novels             │  │  - chapter_embeddings           │  │
│  │  - chapters           │  │  - entity_embeddings            │  │
│  │  - chapter_facts      │  │    (语义检索用)                  │  │
│  │  - conversations      │  │                                 │  │
│  │  - user_state         │  └─────────────────────────────────┘  │
│  │  (聚合视图按需计算)    │                                      │
│  └──────────────────────┘                                       │
├─────────────────────────────────────────────────────────────────┤
│  基础设施层 (Infrastructure)                                     │
│  ┌──────────────┐ ┌────────────────┐ ┌────────────────────────┐ │
│  │ LLMClient    │ │ EmbeddingClient│ │ ConfigManager          │ │
│  │ (Ollama API) │ │ (BGE + MPS)    │ │ (YAML → Settings)     │ │
│  └──────────────┘ └────────────────┘ └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 关键设计原则

1. **两数据库原则**：只使用 SQLite + ChromaDB，不再有 7 个 Store
2. **聚合按需计算**：实体档案（Profile）从 ChapterFact 实时聚合，不持久化为独立表——保证数据一致性，避免同步问题
3. **前后端分离**：前端 SPA 通过 REST + WebSocket 与后端通信，可独立开发和部署
4. **抽取层独立**：ChapterFactExtractor 不直接依赖存储层，产出 ChapterFact JSON 后由服务层写入
5. **无图数据库**：关系图谱数据从 ChapterFact 的 relationships 聚合计算，路径查找在内存中用 BFS 完成（数据规模在千级别，不需要专门的图数据库）

### 4.3 V1 → V2 存储简化

| V1 (7 个 Store) | V2 (2 个) | 数据去向 |
|------------------|-----------|---------|
| UnifiedStore (SQLite) | **SQLite** | 保留为主存储 |
| VectorStore (ChromaDB) | **ChromaDB** | 保留为向量检索 |
| GraphStore (NetworkX) | ~~删除~~ | 关系数据从 ChapterFact.relationships 聚合 |
| SpatialStore (SQLite) | ~~删除~~ | 空间数据从 ChapterFact.locations 聚合 |
| WorldMapStore (SQLite) | ~~删除~~ | 合并到 ChapterFact.locations |
| TimelineStore (SQLite) | ~~删除~~ | 时间线从 ChapterFact.events 聚合 |
| MetadataDB (SQLite) | ~~删除~~ | 合并到主 SQLite |

---

## 5. 数据模型设计

### 5.1 SQLite 表结构

```sql
-- ═══════════════════════════════════════════
-- 核心表
-- ═══════════════════════════════════════════

CREATE TABLE novels (
    id              TEXT PRIMARY KEY,         -- UUID
    title           TEXT NOT NULL,
    author          TEXT,
    file_hash       TEXT,                     -- 文件 SHA256，用于重复检测
    total_chapters  INTEGER DEFAULT 0,
    total_words     INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE chapters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    chapter_num     INTEGER NOT NULL,         -- 章节序号（全书唯一）
    volume_num      INTEGER,                  -- 卷号（可为 NULL，无卷结构时）
    volume_title    TEXT,                     -- 卷标题
    title           TEXT NOT NULL,            -- 章节标题
    content         TEXT NOT NULL,            -- 章节全文
    word_count      INTEGER DEFAULT 0,
    analysis_status TEXT DEFAULT 'pending',   -- pending/analyzing/completed/failed
    analyzed_at     TEXT,
    UNIQUE(novel_id, chapter_num)
);

CREATE TABLE chapter_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    chapter_id      INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    fact_json       TEXT NOT NULL,            -- ChapterFact 完整 JSON
    llm_model       TEXT,                     -- 使用的模型名
    extracted_at    TEXT DEFAULT (datetime('now')),
    extraction_ms   INTEGER,                  -- 抽取耗时（毫秒）
    UNIQUE(novel_id, chapter_id)              -- 每章仅一条记录，重新分析时覆盖
);

-- ═══════════════════════════════════════════
-- 问答
-- ═══════════════════════════════════════════

CREATE TABLE conversations (
    id              TEXT PRIMARY KEY,         -- UUID
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    title           TEXT,                     -- 对话标题（自动从首条消息生成）
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,            -- user / assistant
    content         TEXT NOT NULL,
    sources_json    TEXT,                     -- 来源章节和原文片段 JSON
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ═══════════════════════════════════════════
-- 用户状态
-- ═══════════════════════════════════════════

CREATE TABLE user_state (
    novel_id        TEXT PRIMARY KEY REFERENCES novels(id) ON DELETE CASCADE,
    last_chapter    INTEGER,                  -- 最后阅读章节
    scroll_position REAL,                     -- 滚动位置百分比
    chapter_range   TEXT,                     -- 可视化章节范围 JSON: [start, end]
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- ═══════════════════════════════════════════
-- 分析任务
-- ═══════════════════════════════════════════

CREATE TABLE analysis_tasks (
    id              TEXT PRIMARY KEY,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    status          TEXT DEFAULT 'pending',   -- pending/running/paused/completed/cancelled
    chapter_start   INTEGER NOT NULL,
    chapter_end     INTEGER NOT NULL,
    current_chapter INTEGER,                  -- 当前正在处理的章节
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- ═══════════════════════════════════════════
-- 索引
-- ═══════════════════════════════════════════

CREATE INDEX idx_chapters_novel      ON chapters(novel_id, chapter_num);
CREATE INDEX idx_chapter_facts_novel ON chapter_facts(novel_id);
CREATE INDEX idx_messages_conv       ON messages(conversation_id, created_at);
CREATE INDEX idx_analysis_novel      ON analysis_tasks(novel_id, status);
```

### 5.2 ChapterFact 存储策略

ChapterFact 以 JSON 文本存储在 `chapter_facts.fact_json` 列中。

**为什么不拆分为关系型表？**

1. ChapterFact 是深嵌套结构（characters 内含 abilities_gained 数组），拆分为 10+ 张表反而增加复杂度
2. 查询主要是"按 novel_id 聚合全部"或"按 chapter_id 取单条"，不需要对 JSON 内部字段建索引
3. SQLite 的 `json_extract()` 函数可在需要时查询 JSON 内部字段
4. 导入导出天然简单——直接导出 JSON 行即可

### 5.3 聚合查询模式

实体档案不持久化为表，而是从 ChapterFact 按需聚合。以下是关键聚合的实现思路：

**PersonProfile 聚合**（Python 服务层伪代码）：

```python
def aggregate_person(novel_id: str, person_name: str) -> PersonProfile:
    facts = db.get_all_chapter_facts(novel_id)  # 全部 ChapterFact

    profile = PersonProfile(name=person_name)
    for fact in facts:
        chapter = fact.chapter_id
        # 从 characters 数组找到该人物
        for char in fact.characters:
            if char.name == person_name or person_name in char.new_aliases:
                profile.aliases.extend((alias, chapter) for alias in char.new_aliases)
                if char.appearance:
                    profile.appearances.append((chapter, char.appearance))
                profile.abilities.extend((chapter, a) for a in char.abilities_gained)
                profile.chapters_appeared.add(chapter)

        # 从 relationships 找到涉及该人物的关系
        for rel in fact.relationships:
            if person_name in (rel.person_a, rel.person_b):
                other = rel.person_b if rel.person_a == person_name else rel.person_a
                profile.add_relationship_event(other, chapter, rel)

        # 从 item_events 找到该人物的物品关联
        for item_ev in fact.item_events:
            if item_ev.actor == person_name or item_ev.recipient == person_name:
                profile.add_item_event(chapter, item_ev)

        # 从 events 找到该人物参与的事件
        for event in fact.events:
            if person_name in event.participants:
                profile.add_experience(chapter, event)

    return profile
```

**性能考虑**：

- 2000 章小说，每章 ChapterFact 约 2-5 KB JSON → 全部加载约 4-10 MB → 内存聚合可行
- 频繁访问的聚合结果可在内存中缓存（LRU cache），新 ChapterFact 写入时失效
- 首次加载一本小说的全部 ChapterFact 约 50-100ms（SQLite 顺序读）

### 5.4 ChromaDB Collections

| Collection | 内容 | 用途 |
|-----------|------|------|
| `{novel_id}_chapters` | 章节文本的嵌入向量 | 语义检索：问答时找相关章节 |
| `{novel_id}_entities` | 实体描述的嵌入向量 | 语义检索：问答时找相关实体 |

只需 2 个 collection（V1 有 4 个），实体描述从聚合后的 Profile 生成。

---

## 6. 关键模块设计

### 6.1 分析任务系统

**核心问题**：分析一本 2000 章的小说需要数小时，必须支持暂停/恢复/取消/进度推送。

**设计**：

```
用户触发分析
    │
    ▼
AnalysisService.start(novel_id, chapter_range)
    │
    ├── 创建 analysis_task 记录 (status=running)
    │
    ▼
逐章循环:
    ├── 检查任务状态（是否被暂停/取消）
    │   ├── paused → 保存当前进度，退出循环
    │   └── cancelled → 保存当前进度，退出循环
    │
    ├── ContextSummaryBuilder.build(novel_id, chapter_num)
    │   └── 从已有 ChapterFact 聚合前序摘要
    │
    ├── ChapterFactExtractor.extract(chapter_text, context_summary)
    │   └── LLM 调用 → ChapterFact JSON
    │
    ├── FactValidator.validate(chapter_fact)
    │   └── 轻量检查，修正明显问题
    │
    ├── 写入 chapter_facts 表
    │
    ├── 更新 chapters.analysis_status = 'completed'
    │
    ├── 更新 analysis_task.current_chapter
    │
    ├── WebSocket 推送进度:
    │   { chapter: 84, total: 200, entities: 1245, relations: 3567 }
    │
    └── 继续下一章...
```

**暂停/恢复机制**：
- 暂停：前端发送 `PATCH /api/analysis/{task_id}` `{status: "paused"}`，后端在当前章节处理完后退出循环
- 恢复：前端发送 `PATCH /api/analysis/{task_id}` `{status: "running"}`，后端从 `current_chapter + 1` 继续
- 已分析的章节数据永远不丢失

### 6.2 实体卡片聚合系统

**核心问题**：4 种实体卡片，每种有 6-7 个数据区块，需要从全部 ChapterFact 实时聚合。

**API 设计**：

```
GET /api/novels/{novel_id}/entities/{entity_name}
    → 返回完整实体档案 (PersonProfile / LocationProfile / ...)

GET /api/novels/{novel_id}/entities/{entity_name}/relationships
    → 返回关系演变链（独立端点，支持分页）

GET /api/novels/{novel_id}/entities/{entity_name}/experiences
    → 返回人物经历（独立端点，支持分页）
```

**缓存策略**：

```
EntityAggregator
    │
    ├── 内存缓存 (LRU, 按 novel_id + entity_name)
    │   - 缓存完整的聚合结果
    │   - 新 ChapterFact 写入时按 novel_id 失效
    │   - 最大缓存 100 个实体
    │
    └── 聚合计算
        - 遍历全部 chapter_facts
        - 按实体名过滤和汇总
        - 返回结构化 Profile
```

### 6.3 可视化数据接口

**章节范围联动**：

所有可视化 API 接受 `chapter_start` 和 `chapter_end` 参数，只返回该范围内的数据：

```
GET /api/novels/{id}/graph?chapter_start=1&chapter_end=50
    → 返回第 1-50 章的人物关系图数据
    {
      nodes: [{ id, name, type, chapter_count, org }],
      edges: [{ source, target, relation_type, weight, chapters }]
    }

GET /api/novels/{id}/map?chapter_start=1&chapter_end=50
    → 返回第 1-50 章的空间地图数据
    {
      locations: [{ id, name, type, parent, level, mention_count, x, y }],
      trajectories: { person_name: [{ location, chapter }] }
    }

GET /api/novels/{id}/timeline?chapter_start=1&chapter_end=50
    → 返回第 1-50 章的事件时间线数据
    {
      events: [{ chapter, summary, type, importance, participants, location }],
      swimlanes: { person_name: [event_ids] }
    }

GET /api/novels/{id}/factions?chapter_start=1&chapter_end=50
    → 返回第 1-50 章的势力图数据
    {
      orgs: [{ id, name, type, member_count }],
      relations: [{ source, target, type, chapter }],
      members: { org_name: [{ person, role, status }] }
    }
```

**数据生成**：全部从 ChapterFact 聚合。过滤 `chapter_id <= chapter_end && chapter_id >= chapter_start` 后聚合。

### 6.4 问答（QA）Pipeline

```
用户问题
    │
    ▼
QueryService.query(novel_id, question, conversation_id)
    │
    ├── 1. 问题分析
    │   - 实体识别（从问题中提取实体名）
    │   - 问题分类（实体/关系/事件/空间/比较/统计/推理/开放）
    │
    ├── 2. 混合检索
    │   ├── 向量检索: ChromaDB 语义搜索相关章节 (权重 0.5)
    │   ├── 实体检索: 从 ChapterFact 中找到相关实体的事实 (权重 0.3)
    │   └── 关键词检索: 章节全文关键词匹配 (权重 0.2)
    │
    ├── 3. 上下文构建
    │   - 合并检索结果，去重排序
    │   - 拼接相关章节片段和实体事实
    │   - 如有对话历史，附加最近 N 轮
    │
    ├── 4. LLM 推理（流式输出）
    │   - System prompt: 基于原文回答，标注来源章节
    │   - Context: 检索到的相关内容
    │   - Question: 用户问题
    │   - 通过 WebSocket 流式推送 token
    │
    └── 5. 后处理
        - 提取答案中的实体名 → 标记为可交互
        - 提取来源章节引用 → 标记为可跳转
        - 保存到 messages 表
```

### 6.5 WebSocket 协议

两个 WebSocket 端点：

**分析进度** (`/ws/analysis/{novel_id}`)：

```json
// 服务端 → 客户端
{ "type": "progress", "chapter": 84, "total": 200,
  "stats": { "entities": 1245, "relations": 3567, "events": 892 } }
{ "type": "chapter_done", "chapter": 84, "status": "completed" }
{ "type": "chapter_done", "chapter": 85, "status": "failed", "error": "LLM timeout" }
{ "type": "task_status", "status": "paused" }
{ "type": "task_status", "status": "completed" }
```

**流式问答** (`/ws/chat/{session_id}`)：

```json
// 客户端 → 服务端
{ "type": "query", "novel_id": "xxx", "conversation_id": "yyy", "question": "韩立的师傅是谁？" }

// 服务端 → 客户端
{ "type": "token", "content": "韩立" }
{ "type": "token", "content": "有两位" }
{ "type": "token", "content": "师傅：" }
...
{ "type": "done", "sources": [{ "chapter": 3, "text": "..." }, { "chapter": 25, "text": "..." }],
  "entities": ["韩立", "墨大夫", "李化元"] }
```

### 6.6 前端目录结构

```
src/
├── app/                        # 应用入口、路由、全局 Provider
│   ├── App.tsx
│   ├── router.tsx
│   └── providers.tsx
├── pages/                      # 10 个页面组件
│   ├── BookshelfPage.tsx
│   ├── ReadingPage.tsx
│   ├── GraphPage.tsx
│   ├── MapPage.tsx
│   ├── TimelinePage.tsx
│   ├── FactionsPage.tsx
│   ├── ChatPage.tsx
│   ├── EncyclopediaPage.tsx
│   ├── AnalysisPage.tsx
│   └── SettingsPage.tsx
├── components/                 # 可复用组件
│   ├── entity-cards/           # 实体卡片系统
│   │   ├── EntityDrawer.tsx    # 右侧抽屉容器 + 面包屑导航
│   │   ├── PersonCard.tsx
│   │   ├── LocationCard.tsx
│   │   ├── ItemCard.tsx
│   │   ├── OrgCard.tsx
│   │   └── ConceptPopover.tsx
│   ├── visualization/          # 4 种可视化
│   │   ├── ForceGraph.tsx      # 人物关系图 + 势力图
│   │   ├── WorldMap.tsx        # 空间地图 (Pixi.js)
│   │   ├── HierarchyMap.tsx    # 层级地图
│   │   └── Timeline.tsx        # 时间线
│   ├── shared/                 # 通用组件
│   │   ├── ChapterRangeSlider.tsx
│   │   ├── FilterPanel.tsx
│   │   ├── QABar.tsx           # 底部常驻问答栏
│   │   ├── QAFloatingPanel.tsx
│   │   ├── ChapterSidebar.tsx  # 树形章节目录
│   │   ├── EntityHighlight.tsx # 实体高亮文本渲染
│   │   └── TopNav.tsx
│   └── ui/                     # shadcn/ui 基础组件
├── stores/                     # Zustand 状态管理
│   ├── novelStore.ts           # 当前小说状态
│   ├── chapterRangeStore.ts    # 章节范围（4 视图共享）
│   ├── entityDrawerStore.ts    # 实体卡片抽屉状态 + 导航栈
│   ├── analysisStore.ts        # 分析进度状态
│   └── chatStore.ts            # 对话状态
├── api/                        # API 客户端
│   ├── client.ts               # REST 请求封装
│   ├── websocket.ts            # WebSocket 连接管理
│   └── types.ts                # API 类型定义
├── hooks/                      # 自定义 React Hooks
│   ├── useEntity.ts
│   ├── useGraph.ts
│   ├── useAnalysis.ts
│   └── useChat.ts
└── lib/                        # 工具函数
    ├── entity-colors.ts        # 实体类型 → 颜色映射
    └── format.ts               # 格式化工具
```

### 6.7 后端目录结构

```
src/
├── api/                        # FastAPI 路由
│   ├── main.py                 # 应用入口、CORS、生命周期
│   ├── routes/
│   │   ├── novels.py           # 小说 CRUD + 上传
│   │   ├── chapters.py         # 章节读取
│   │   ├── entities.py         # 实体卡片数据
│   │   ├── graph.py            # 关系图数据
│   │   ├── map.py              # 世界地图数据
│   │   ├── timeline.py         # 时间线数据
│   │   ├── factions.py         # 势力图数据
│   │   ├── chat.py             # 问答 REST 端点
│   │   ├── analysis.py         # 分析任务管理
│   │   └── settings.py         # 设置
│   ├── websocket/
│   │   ├── analysis_ws.py      # 分析进度推送
│   │   └── chat_ws.py          # 流式问答
│   └── schemas/                # Pydantic 请求/响应模型
├── services/                   # 业务逻辑
│   ├── novel_service.py        # 上传、解析、章节切分
│   ├── analysis_service.py     # 分析任务调度
│   ├── entity_aggregator.py    # ChapterFact → 实体档案聚合
│   ├── query_service.py        # 问答 Pipeline
│   └── viz_service.py          # 可视化数据生成
├── extraction/                 # 抽取层
│   ├── chapter_fact_extractor.py   # LLM 调用 + JSON 解析
│   ├── context_summary_builder.py  # 前序状态摘要生成
│   ├── fact_validator.py           # 轻量后验证
│   ├── spatial_rule_enhancer.py    # 地点层级正则增强
│   └── prompts/
│       ├── extraction_system.txt   # System prompt
│       └── extraction_examples.json # Few-shot 示例
├── db/                         # 存储层
│   ├── sqlite_db.py            # SQLite 连接管理 + 迁移
│   ├── chapter_fact_store.py   # ChapterFact CRUD
│   ├── novel_store.py          # 小说/章节 CRUD
│   ├── conversation_store.py   # 对话 CRUD
│   └── vector_store.py         # ChromaDB 封装
├── models/                     # 数据模型 (dataclass)
│   ├── chapter_fact.py         # ChapterFact 及子结构
│   ├── profiles.py             # PersonProfile, LocationProfile, ...
│   ├── novel.py                # Novel, Chapter
│   └── conversation.py         # Conversation, Message
├── infra/                      # 基础设施
│   ├── llm_client.py           # Ollama API 封装
│   ├── embedding_client.py     # BGE 嵌入计算
│   └── config.py               # 配置加载
└── utils/                      # 工具
    ├── text_processor.py       # 编码检测、章节切分
    └── chapter_splitter.py     # 章节切分规则引擎
```
