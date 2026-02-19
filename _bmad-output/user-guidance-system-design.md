# AI Reader V2 用户帮助与引导体系设计方案

> 设计日期：2026-02-17  
> 设计目标：降低功能发现成本，提升用户上手效率

---

## 一、现状分析

### 1.1 现有帮助元素

| 元素 | 状态 | 评价 |
|------|------|------|
| `SetupGuide` 环境配置引导 | ✅ 已实现 | 首次启动引导，覆盖Ollama安装检测 |
| 空状态提示 | ⚠️ 基础实现 | 有"请先分析小说"等提示，但缺少操作引导 |
| 错误提示 | ⚠️ 基础实现 | Toast提示，但缺少解决方案指引 |
| 帮助中心/文档 | ❌ 缺失 | 无统一帮助入口 |
| 功能引导/工具提示 | ❌ 缺失 | 新功能无引导说明 |
| 快捷操作提示 | ❌ 缺失 | 快捷键、手势等未暴露 |

### 1.2 用户痛点

```
【新用户】
├── "不知道怎么上传小说"
├── "上传后不知道要点击分析"
├── "分析了但不知道怎么看关系图"
└── "实体卡片弹不出来，以为没这个功能"

【进阶用户】
├── "不知道可以导出数据"
├── "不知道能调整地图布局"
├── "没发现可以按章节范围筛选"
└── "不知道有智能问答功能"

【专业用户】
├── "不清楚导出格式有什么区别"
├── "不知道如何使用剧本模式"
└── "不了解如何批量操作"
```

---

## 二、帮助体系总体架构

```
📚 AI Reader V2 帮助体系
│
├── 🎯 第一层：上下文引导（Contextual Guidance）
│   ├── 新功能高光（Feature Highlights）
│   ├── 工具提示（Tooltips）
│   ├── 空状态引导（Empty State Guidance）
│   └── 操作确认/解释（Action Confirmation）
│
├── 📖 第二层：结构化帮助（Structured Help）
│   ├── 帮助中心（Help Center）
│   ├── 功能指南（Feature Guides）
│   ├── 场景教程（Scenario Tutorials）
│   └── 常见问题（FAQ）
│
├── 🎓 第三层：主动教育（Proactive Education）
│   ├── 首次使用引导（Onboarding）
│   ├── 新功能引导（What's New）
│   ├── 进阶技巧提示（Pro Tips）
│   └── 邮件/推送教程（Lifecycle Education）
│
└── 🔍 第四层：自助支持（Self-Service Support）
    ├── 搜索式帮助（Searchable Docs）
    ├── 快捷键面板（Keyboard Shortcuts）
    ├── 故障排查（Troubleshooting）
    └── 反馈入口（Feedback & Support）
```

---

## 三、详细设计方案

### 3.1 上下文引导层（Layer 1）

#### 3.1.1 新功能高光（Feature Highlights）

**触发条件**：
- 首次进入某个页面
- 新功能上线后首次使用
- 功能长期未被使用后重新访问

**展示形式**：
```tsx
// 组件：FeatureHighlight.tsx
interface HighlightSpot {
  target: string;        // CSS选择器定位目标元素
  title: string;         // 高亮标题
  description: string;   // 说明文字
  position: 'top' | 'bottom' | 'left' | 'right';
  action?: {             // 可选操作按钮
    label: string;
    onClick: () => void;
  };
}

// 示例：首次进入阅读页
const readingPageHighlights: HighlightSpot[] = [
  {
    target: '[data-help="chapter-sidebar"]',
    title: "章节目录",
    description: "点击章节快速跳转，已分析的章节会显示绿色标记",
    position: "right"
  },
  {
    target: '[data-help="entity-highlight"]',
    title: "智能实体识别",
    description: "蓝色文字代表人物，点击可查看人物卡片",
    position: "bottom",
    action: {
      label: "试试看",
      onClick: () => simulateEntityClick()
    }
  },
  {
    target: '[data-help="chat-input"]',
    title: "智能问答",
    description: "按 Cmd+K 随时提问关于小说的任何问题",
    position: "top"
  }
];
```

**UI示意**：
```
┌─────────────────────────────────────────┐
│  第1章 凡人修仙传                        │
│                                         │
│  韩立【高亮】在【药园】【高亮】中...      │
│       ▲                                 │
│       │  ╭──────────────────╮           │
│       └──│ 💡 智能实体识别   │           │
│          │ 蓝色文字代表人物  │           │
│          │ 点击可查看人物卡片│           │
│          │ [试试看] [知道了] │           │
│          ╰──────────────────╯           │
│                                         │
│  ─────────────────────────────────      │
│  问任何关于这本小说的问题... [?]         │
│                              ▲          │
│                              │          │
│                     ╭────────┴──────╮   │
│                     │ 💡 智能问答    │   │
│                     │ 按 Cmd+K 提问  │   │
│                     ╰───────────────╯   │
└─────────────────────────────────────────┘
```

#### 3.1.2 智能工具提示（Smart Tooltips）

**策略**：
- 非侵入式：鼠标悬停0.5秒后显示
- 渐进式：首次显示完整说明，后续显示简化版
- 可操作：提示中可包含快捷操作

**示例**：
```tsx
// 简化版（用户已熟悉后）
<Tooltip content="查看人物关系">
  <CharacterName>韩立</CharacterName>
</Tooltip>

// 完整版（首次使用）
<Tooltip 
  title="人物实体"
  content="点击查看完整人物卡片，包括关系、能力、经历等"
  shortcut="点击"
  visual={characterCardPreview}
>
  <CharacterName>韩立</CharacterName>
</Tooltip>
```

#### 3.1.3 空状态引导（Empty State Guidance）

**现有空状态 vs 改进**：

| 场景 | 现有提示 | 改进方案 |
|------|---------|---------|
| 书架为空 | "还没有导入小说" | + 上传按钮 + "查看如何准备小说文件"链接 |
| 章节未分析 | "此章节尚未分析" | + 分析按钮 + "分析有什么好处？"说明 |
| 图谱无数据 | "请先分析小说" | + 跳转分析按钮 + "了解知识图谱"视频 |
| 搜索结果为空 | 无 | "尝试使用更简单的关键词" + 搜索建议 |
| 导出无权限 | 无 | "升级到Pro以导出Word/Excel" + 功能对比 |

**改进后的空状态组件**：
```tsx
// EmptyState.tsx
interface EmptyStateProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  primaryAction?: {
    label: string;
    onClick: () => void;
  };
  secondaryAction?: {
    label: string;
    onClick: () => void;
  };
  helpLink?: string;      // 链接到帮助文档
  illustration?: string;  // 插图/动画
}

// 示例：未分析章节空状态
<EmptyState
  icon={<AnalysisIcon />}
  title="此章节尚未分析"
  description="分析后你可以：查看人物关系、地点层级、智能问答"
  primaryAction={{
    label: "开始分析",
    onClick: startAnalysis
  }}
  secondaryAction={{
    label: "批量分析全书",
    onClick: analyzeAll
  }}
  helpLink="/help/analysis-benefits"
  illustration="/illustrations/analysis-demo.gif"
/>
```

---

### 3.2 结构化帮助层（Layer 2）

#### 3.2.1 帮助中心（Help Center）

**入口位置**：
- 全局导航栏：问号图标 `?`
- 快捷键：`Cmd/Ctrl + Shift + ?`
- 右键菜单："帮助"

**结构**：
```
帮助中心 (/help)
├── 🔍 搜索帮助文档
│
├── 🚀 快速开始
│   ├── 安装与环境配置
│   ├── 上传你的第一本小说
│   ├── 开始分析
│   └── 基础操作指南
│
├── 📖 功能指南
│   ├── 📚 书架管理
│   │   ├── 上传小说
│   │   ├── 章节切分调整
│   │   └── 数据导入/导出
│   │
│   ├── 📖 阅读与实体
│   │   ├── 阅读界面介绍
│   │   ├── 实体高亮与卡片
│   │   ├── 人物/地点/物品卡片详解
│   │   └── 概念百科
│   │
│   ├── 🕸️ 可视化分析
│   │   ├── 人物关系图使用指南
│   │   ├── 世界地图操作说明
│   │   ├── 时间线功能
│   │   ├── 势力图介绍
│   │   └── 视图联动与筛选
│   │
│   ├── 💬 智能问答
│   │   ├── 如何有效提问
│   │   ├── 问答技巧
│   │   └── 对话管理
│   │
│   └── 📤 Series Bible 导出（Pro）
│       ├── 导出格式对比
│       ├── Markdown导出
│       ├── Word/PDF导出
│       ├── Excel导出
│       └── 剧本模式使用指南
│
├── 🎓 场景教程
│   ├── 网文作者：管理长篇设定
│   ├── 编剧：小说改编剧本
│   ├── 游戏策划：世界观梳理
│   └── 编辑：跨书设定管理
│
├── ❓ 常见问题（FAQ）
│   ├── 分析相关
│   ├── 性能与存储
│   ├── 隐私与安全
│   └── 故障排查
│
└── 📞 获取支持
    ├── 提交反馈
    ├── 报告问题
    ├── 功能建议
    └── 联系开发者
```

#### 3.2.2 功能对比与选择指南

**示例：导出格式选择器**：
```
┌─────────────────────────────────────────────────────┐
│ 选择导出格式                                         │
│                                                      │
│  ╭─────────────╮  ╭─────────────╮  ╭─────────────╮  │
│  │   Markdown  │  │    Word     │  │    Excel    │  │
│  │             │  │             │  │             │  │
│  │ [预览图标]  │  │ [预览图标]  │  │ [预览图标]  │  │
│  │             │  │             │  │             │  │
│  │ 适合：网文   │  │ 适合：出版   │  │ 适合：改编   │  │
│  │ 作者个人使用 │  │ 编辑协作    │  │ 数据整理    │  │
│  │             │  │             │  │             │  │
│  │ [选择]      │  │ [选择]      │  │ [选择]      │  │
│  │             │  │             │  │             │  │
│  │ 轻量、易编辑 │  │ 正式、可打印 │  │ 结构化数据  │  │
│  ╰─────────────╯  ╰─────────────╯  ╰─────────────╯  │
│                                                      │
│  [详细对比表] [查看示例文件]                         │
└─────────────────────────────────────────────────────┘
```

#### 3.2.3 交互式教程（Interactive Tutorials）

**实现方式**：沙盒模式 + 步骤引导

```tsx
// Tutorial: "你的第一次实体探索"
const entityExplorationTutorial: TutorialStep[] = [
  {
    id: "intro",
    title: "发现实体",
    content: "AI Reader 会自动识别小说中的人物、地点、物品。让我们来找找看！",
    action: "点击继续"
  },
  {
    id: "highlight",
    title: "识别实体",
    content: "看到蓝色的'韩立'了吗？这是人物实体的标记。",
    highlight: "[data-entity='韩立']",
    waitFor: "entity-click",
    hint: "点击高亮的'韩立'"
  },
  {
    id: "card",
    title: "人物卡片",
    content: "右侧滑出了人物卡片，展示了韩立的基本信息、关系、能力等。",
    highlight: "[data-component='entity-drawer']",
    action: "探索卡片"
  },
  {
    id: "relation",
    title: "查看关系",
    content: "点击'墨大夫'，看看韩立和他是什么关系。",
    highlight: "[data-relation='墨大夫']",
    waitFor: "navigation",
    hint: "点击关系链中的'墨大夫'"
  },
  {
    id: "complete",
    title: "完成！",
    content: "你已经掌握了实体浏览的基本操作。继续阅读或尝试点击其他实体！",
    action: "结束教程"
  }
];
```

---

### 3.3 主动教育层（Layer 3）

#### 3.3.1 渐进式首次引导（Progressive Onboarding）

**策略**：不打断用户，在合适时机展示

```
【第1次启动】
└── SetupGuide: 环境配置检测

【上传第一本小说后】
└── 引导: "点击分析按钮开始提取实体"

【分析完成首次进入阅读页】
└── Highlight: "点击蓝色高亮查看人物"

【阅读超过10分钟后】
└── Tooltip: "试试按 Cmd+K 提问"

【首次打开关系图】
└── Highlight: "拖拽节点调整布局，滚轮缩放"

【使用3天后】
└── Pro Tips: "发现导出功能了吗？生成你的Series Bible"
```

#### 3.3.2 新功能公告（What's New）

**组件**：`WhatsNewModal`

```
┌─────────────────────────────────────────┐
│  ✨ 新功能：剧本模式                      │
│                                         │
│  [预览图/GIF]                           │
│                                         │
│  现在你可以：                            │
│  • 将小说自动转换为场景化的剧本格式        │
│  • 并排对比原文和剧本                     │
│  • 按场景导航跳转                         │
│                                         │
│  [立即体验] [稍后再说] [不再显示]         │
│                                         │
│  [查看完整更新日志]                      │
└─────────────────────────────────────────┘
```

**触发规则**：
- 版本更新后首次启动
- 用户完成当前操作后（非打断式）
- 提供"不再显示"选项

#### 3.3.3 进阶技巧提示（Pro Tips）

**展示位置**：页面角落小卡片，可关闭

```
┌──────────────────────╮
│ 💡 Pro Tip            │
│                      │
│ 在关系图中按住Shift   │
│ 可以多选节点批量调整  │
│                      │
│ [知道了] [了解更多]  │
╰──────────────────────╯
```

**触发时机**：
- 用户多次使用某功能后展示进阶技巧
- 根据使用模式智能推荐
- 每周推送一条新技巧

---

### 3.4 自助支持层（Layer 4）

#### 3.4.1 快捷键面板

**触发**：`Cmd/Ctrl + K`（与问答共享，但前缀为`>`时显示命令面板）

```
┌─────────────────────────────────────────┐
│ > _                                     │
│                                         │
│  快捷键                                  │
│  ─────────────────                      │
│  ⌘K      打开智能问答                   │
│  ⌘⇧?     显示所有快捷键                 │
│  ⌘B      切换侧边栏                     │
│  ⌘F      全文搜索                       │
│  ⌘[ / ⌘] 上一章/下一章                  │
│                                         │
│  快速操作                                │
│  ─────────────────                      │
│  分析当前小说                           │
│  导出Series Bible                       │
│  切换到剧本模式                          │
│                                         │
│  最近访问                                │
│  ─────────────────                      │
│  凡人修仙传 - 第123章                   │
│  斗破苍穹 - 关系图                       │
└─────────────────────────────────────────┘
```

#### 3.4.2 故障排查向导

**交互式诊断**：
```
┌─────────────────────────────────────────┐
│ 🔧 故障排查                             │
│                                         │
│ 你遇到了什么问题？                       │
│                                         │
│  ○ 分析速度很慢/卡住                    │
│  ○ 实体识别不准确                       │
│  ○ 关系图显示异常                       │
│  ○ 导出失败                             │
│  ○ 其他问题                             │
│                                         │
│  [开始诊断]                             │
└─────────────────────────────────────────┘
```

**诊断流程示例（分析速度慢）**：
```
问题：分析速度很慢

检查1: Ollama是否正常运行？
    ├── 是 → 继续检查2
    └── 否 → 显示启动Ollama指引

检查2: 模型是否已下载？
    ├── 是 → 继续检查3
    └── 否 → 显示模型下载指引

检查3: 系统资源占用
    ├── CPU/内存充足 → 建议分批分析
    └── 资源紧张 → 建议关闭其他应用
```

#### 3.4.3 反馈与洞察

**反馈入口**：
- 每个页面右下角悬浮按钮
- 快捷键：`Cmd/Ctrl + Shift + F`
- 设置页面中的"反馈"选项

**反馈表单**：
```
┌─────────────────────────────────────────┐
│ 发送反馈                                │
│                                         │
│ 反馈类型：                               │
│ [Bug报告 ▼]                             │
│                                         │
│ 描述：                                   │
│ ┌─────────────────────────────────┐    │
│ │ 请描述你遇到的问题...            │    │
│ └─────────────────────────────────┘    │
│                                         │
│ 截图（可选）：                           │
│ [点击上传或粘贴]                        │
│                                         │
│ [ ] 包含日志信息（帮助诊断）             │
│                                         │
│ [发送反馈]                              │
└─────────────────────────────────────────┘
```

---

## 四、帮助内容规划

### 4.1 文档内容清单

| 文档 | 优先级 | 类型 | 长度 |
|------|--------|------|------|
| 快速开始指南 | P0 | 图文 | 5分钟阅读 |
| 上传小说教程 | P0 | 图文+视频 | 2分钟视频 |
| 分析功能详解 | P0 | 图文 | 3分钟阅读 |
| 阅读界面介绍 | P0 | 交互式引导 | 2分钟体验 |
| 实体卡片使用 | P0 | 图文 | 3分钟阅读 |
| 可视化视图指南 | P1 | 图文+视频 | 5分钟视频 |
| 智能问答技巧 | P1 | 图文 | 5分钟阅读 |
| 导出格式对比 | P1 | 对比表+示例 | 3分钟阅读 |
| 剧本模式教程 | P1 | 视频 | 8分钟视频 |
| 故障排查手册 | P1 | 交互式向导 | - |
| 高级技巧合集 | P2 | 图文 | 10分钟阅读 |
| API文档 | P2 | 技术文档 | - |

### 4.2 视频教程规划

```
📺 视频教程系列
├── 入门系列（每集2-3分钟）
│   ├── EP1: 安装与环境配置
│   ├── EP2: 上传你的第一本小说
│   ├── EP3: 开始分析
│   ├── EP4: 浏览人物关系
│   └── EP5: 智能问答入门
│
├── 进阶系列（每集5-8分钟）
│   ├── EP6: 深度使用实体卡片
│   ├── EP7: 可视化视图详解
│   ├── EP8: 世界地图与层级管理
│   ├── EP9: 导出Series Bible
│   └── EP10: 剧本模式使用指南
│
└── 场景系列（每集8-10分钟）
    ├── 网文作者：管理百万字设定
    ├── 编剧：从小说到剧本
    ├── 游戏策划：构建世界观文档
    └── 编辑：跨书设定一致性检查
```

---

## 五、技术实现建议

### 5.1 组件架构

```
components/help/
├── HelpCenter/                    # 帮助中心主页面
│   ├── index.tsx
│   ├── SearchPanel.tsx
│   ├── CategoryNav.tsx
│   └── ArticleView.tsx
│
├── Onboarding/                    # 首次引导
│   ├── SetupGuide.tsx            # 已有，环境配置
│   ├── FeatureHighlight.tsx      # 新功能高亮
│   ├── InteractiveTutorial.tsx   # 交互式教程
│   └── TooltipTour.tsx           # 步骤引导
│
├── ContextualHelp/                # 上下文帮助
│   ├── SmartTooltip.tsx          # 智能工具提示
│   ├── EmptyStateGuide.tsx       # 空状态引导
│   ├── ProTipCard.tsx            # 进阶技巧卡片
│   └── ActionHint.tsx            # 操作提示
│
├── SelfService/                   # 自助支持
│   ├── CommandPalette.tsx        # 命令面板
│   ├── KeyboardShortcuts.tsx     # 快捷键面板
│   ├── TroubleshootWizard.tsx    # 故障排查向导
│   └── FeedbackForm.tsx          # 反馈表单
│
└── hooks/
    ├── useOnboarding.ts          # 引导状态管理
    ├── useHelpSearch.ts          # 帮助搜索
    ├── useProgressiveReveal.ts   # 渐进展示
    └── useFeatureDiscovery.ts    # 功能发现统计
```

### 5.2 数据模型

```typescript
// 用户帮助状态
interface UserHelpState {
  // 已完成的引导
  completedTours: string[];
  
  // 已关闭的提示
  dismissedTips: string[];
  
  // 功能使用计数（用于智能提示）
  featureUsage: Record<string, number>;
  
  // 帮助文档阅读进度
  articleProgress: Record<string, number>;
  
  // 用户技能等级（用于个性化提示）
  skillLevel: 'beginner' | 'intermediate' | 'advanced';
  
  // 上次查看"What's New"的版本
  lastSeenVersion: string;
}

// 教程定义
interface Tutorial {
  id: string;
  name: string;
  targetPage: string;
  steps: TutorialStep[];
  trigger: 'manual' | 'auto' | 'conditional';
  condition?: (userState: UserHelpState) => boolean;
}
```

### 5.3 个性化提示策略

```typescript
// 根据用户行为智能提示
function getProTip(userState: UserHelpState, currentPage: string): ProTip | null {
  // 用户已使用导出3次，提示Excel功能
  if (userState.featureUsage['export'] >= 3 && 
      !userState.dismissedTips.includes('excel-export')) {
    return {
      id: 'excel-export',
      title: '试试Excel导出',
      content: '游戏策划可以使用Excel导出整理NPC数据表',
      target: '/help/export-excel'
    };
  }
  
  // 用户经常查看人物关系，提示路径查找
  if (userState.featureUsage['graph'] >= 5 && 
      !userState.completedTours.includes('path-finding')) {
    return {
      id: 'path-finding',
      title: '查找人物关系路径',
      content: '想知道A和B是如何认识的？试试路径查找功能',
      target: '/help/path-finding'
    };
  }
  
  return null;
}
```

---

## 六、实施路线图

### Phase 1: 基础帮助（Sprint 1-2）

```
Week 1-2
├── 帮助中心框架
│   ├── 页面路由
│   ├── 搜索功能
│   └── 文章渲染
├── 快速开始文档
│   ├── 安装指南
│   ├── 上传教程
│   └── 基础操作
└── 快捷键面板
```

### Phase 2: 上下文引导（Sprint 3-4）

```
Week 3-4
├── 工具提示系统
│   └── SmartTooltip组件
├── 空状态优化
│   └── EmptyStateGuide组件
├── 首次引导增强
│   ├── 阅读页引导
│   ├── 图谱页引导
│   └── 分析页引导
└── 反馈入口
```

### Phase 3: 进阶教育（Sprint 5-6）

```
Week 5-6
├── Pro Tips系统
├── 新功能公告
├── 交互式教程框架
│   └── Tutorial引擎
├── 视频教程制作
│   ├── 入门系列
│   └── 导出功能详解
└── 故障排查向导
```

### Phase 4: 智能优化（Sprint 7+）

```
Week 7+
├── 使用分析
├── 个性化提示
├── 高级教程
│   └── 场景系列视频
├── 社区FAQ整合
└── 持续内容更新
```

---

## 七、成功指标

| 指标 | 目标 | 测量方式 |
|------|------|---------|
| 帮助中心访问率 | >30% | 页面访问统计 |
| 首次引导完成率 | >80% | 引导完成事件 |
| 功能发现时间 | <3分钟 | 用户行为追踪 |
| 支持请求减少 | -50% | 反馈数量对比 |
| 用户满意度 | >4.0/5 | 应用内NPS调查 |
| 视频教程观看率 | >40% | 视频播放统计 |

---

## 八、总结

### 核心设计原则

1. **非侵入式**：帮助不应打断用户的核心 workflow
2. **上下文感知**：在正确的时间、正确的地点提供帮助
3. **渐进式披露**：从简单到复杂，逐步引导用户
4. **可操作性**：帮助内容应包含明确的行动指引
5. **个性化**：根据用户类型和行为提供定制化帮助

### 与产品功能的协同

| 产品功能 | 配套帮助 |
|---------|---------|
| Series Bible导出 | 导出格式选择器、场景教程、视频指南 |
| 剧本模式 | 交互式教程、并排对比说明、编剧场景教程 |
| 数据表视图 | 功能高亮、Excel导出教程、游戏策划案例 |
| 设定冲突检测 | 结果解释、修复建议、最佳实践 |

### 下一步行动

1. **Sprint 1**: 搭建帮助中心框架，编写快速开始文档
2. **设计**: 制作工具提示和空状态引导的UI设计
3. **内容**: 录制入门系列视频（3-5集）
4. **开发**: 实现SmartTooltip和FeatureHighlight组件
5. **测试**: 邀请新用户测试引导流程，收集反馈

---

*设计完成于 2026-02-17*
