/**
 * System FAQ — keyword-based matching for common user questions.
 * No LLM dependency, pure frontend logic.
 */

interface FaqEntry {
  keywords: string[]
  answer: string
  related?: string[]
}

const FAQ_ENTRIES: FaqEntry[] = [
  // === 功能类 ===
  {
    keywords: ["上传", "导入小说", "添加小说", "怎么添加"],
    answer:
      "点击书架页右上角的「上传小说」按钮，选择 .txt 或 .md 格式的文件即可。系统会自动检测章节结构并进行切分预览。您也可以直接将文件拖拽到书架页面上。",
    related: ["支持什么格式？", "怎么分析？"],
  },
  {
    keywords: ["支持什么格式", "文件格式", "txt", "md"],
    answer:
      "目前支持 .txt 和 .md 纯文本格式。请确保文件使用 UTF-8 编码。如果您有其他格式的小说，请先转为 TXT 格式。",
  },
  {
    keywords: ["分析", "开始分析", "怎么分析", "如何分析"],
    answer:
      "上传小说后，进入分析页面，点击「开始分析」。系统将逐章提取人物、关系、地点等信息。分析需要 AI 引擎支持，您可以选择云端 API（推荐，30 秒配置）或本地 Ollama。",
    related: ["分析需要多久？", "如何配置 AI 引擎？"],
  },
  {
    keywords: ["关系图", "图谱", "人物关系"],
    answer:
      "在小说详情页点击「关系图」标签即可查看。图谱展示人物之间的关系网络，支持筛选关系类型、调整节点数量。点击人物节点可查看详细资料。",
  },
  {
    keywords: ["地图", "世界地图", "小说地图"],
    answer:
      "「地图」标签展示小说中的地理空间关系。系统自动推断地点间的层级和方位，支持拖拽调整位置、编辑地名层级。对于现实题材小说，还能匹配真实地理坐标。",
  },
  {
    keywords: ["时间线", "timeline", "事件"],
    answer:
      "「时间线」标签按章节展示主要事件、人物出场、关系变化等。支持按类型筛选，可查看每章的情感基调。",
  },
  {
    keywords: ["百科", "人物百科", "encyclopedia"],
    answer:
      "「百科」标签提供所有实体（人物、地点、物品、组织）的详细档案。点击任意实体可查看完整资料卡，包括关系链、出场章节、别名等。",
  },
  {
    keywords: ["阅读", "读书", "在线阅读"],
    answer:
      "「阅读」标签提供沉浸式阅读体验。支持实体高亮（按 H 切换）、书签、阅读进度记忆。右侧可展开场景面板查看每个段落的场景分析。",
  },
  {
    keywords: ["导出", "下载数据", "备份"],
    answer:
      "支持多种导出方式：1) 单本小说导出为 .air 文件（含完整分析数据）；2) 全量备份（所有小说 + 设置）；3) 系列圣经导出（Markdown/Word/PDF/Excel 格式）。在书架页右上角菜单或导出页操作。",
  },
  {
    keywords: ["势力", "阵营", "faction"],
    answer:
      "「势力」标签展示小说中的组织和阵营划分。系统根据人物的组织归属和互动关系自动推断势力分布。",
  },

  // === 配置类 ===
  {
    keywords: ["配置", "AI 引擎", "LLM", "设置 AI"],
    answer:
      "进入设置页面或分析页面的内联配置器，选择 AI 引擎：\n• ☁️ 云端 API（推荐）：支持 DeepSeek、通义千问、MiniMax 等 10+ 提供商，30 秒完成配置\n• 💻 本地 Ollama：数据不出本机，需安装 Ollama 并下载模型（约 4GB）",
    related: ["什么是 Ollama？", "怎么用云端 API？"],
  },
  {
    keywords: ["Ollama", "本地模型", "本地 AI"],
    answer:
      "Ollama 是一个本地 AI 引擎，让您在自己的电脑上运行 AI 模型，数据完全不出本机。安装方法：访问 ollama.com 下载安装，然后运行 `ollama pull qwen3:8b` 下载推荐模型。需要约 4GB 磁盘空间。",
  },
  {
    keywords: ["云端", "API", "API Key", "密钥"],
    answer:
      "云端 API 是最快的配置方式。推荐使用 DeepSeek（性价比最高）：\n1. 访问 DeepSeek 官网注册账号\n2. 获取 API Key\n3. 在设置页面选择 DeepSeek，粘贴 Key 即可\n分析速度比本地快 5-10 倍。",
  },
  {
    keywords: ["DeepSeek", "deepseek"],
    answer:
      "DeepSeek 是推荐的云端 AI 引擎提供商，性价比最高。在设置页面选择「DeepSeek」，输入 API Key 即可使用。",
  },

  // === 概念类 ===
  {
    keywords: ["预扫描", "实体预扫描", "pre-scan"],
    answer:
      "实体预扫描是分析前的可选步骤。系统用分词技术扫描全文，提取高频人名、地名等，生成实体字典。这个字典会注入到后续分析中，帮助 AI 更准确地识别人物和地点。对于大部头小说建议开启。",
  },
  {
    keywords: ["分析时间", "需要多久", "多长时间", "分析速度"],
    answer:
      "分析时间取决于小说章节数和 AI 引擎：\n• 云端 API：每章约 5 秒，100 章约 8 分钟\n• 本地 Ollama：每章约 30 秒，100 章约 50 分钟\n分析过程支持暂停/继续，不影响已完成章节的结果。",
  },
  {
    keywords: ["数据存在哪", "数据位置", "存储位置", "隐私"],
    answer:
      "所有数据都存储在您的本地电脑上，路径为 ~/.ai-reader-v2/。包括 SQLite 数据库和向量数据库。云端模式仅将小说内容发送到您选择的 AI 提供商，不会上传到其他任何地方。",
  },
  {
    keywords: ["章节切分", "切分", "拆分", "章节识别"],
    answer:
      "系统自动识别章节结构（支持「第X章」「第X回」等多种格式）。上传后会显示切分预览，您可以：调整正则模板、手动添加分割点、搜索原文确认。高级选项默认折叠。",
  },
  {
    keywords: ["别名", "alias", "同一个人"],
    answer:
      "系统自动识别人物别名（如「孙悟空」「行者」「大圣」是同一人）。别名来源包括：实体预扫描的字典和每章提取的 new_aliases。如果发现别名识别有误，可在百科页面查看和反馈。",
  },

  // === 使用类 ===
  {
    keywords: ["快捷键", "keyboard", "键盘"],
    answer:
      "常用快捷键：\n• N：打开上传对话框\n• /：聚焦搜索框\n• H：切换实体高亮（阅读页）\n• S：切换场景面板（阅读页）\n• ←/→：切换章节\n• Esc：关闭对话框/清除搜索",
  },
  {
    keywords: ["问答", "提问", "聊天", "chat"],
    answer:
      "打开一本已分析的小说后，点击右下角的 AI 助手气泡即可提问。支持关于小说内容的任何问题，回答会附带出处引用。也可以在聊天页面进行更详细的对话。",
  },
  {
    keywords: ["样本", "预装", "内置", "示例"],
    answer:
      "AI Reader 预装了经典名著的完整分析数据，您可以直接浏览所有可视化功能，无需配置 AI 引擎。这些样本展示了系统的完整能力。",
  },
  {
    keywords: ["删除", "移除小说"],
    answer:
      "在书架页面，将鼠标悬停在小说卡片上，点击右下角的删除按钮即可。删除会同时清除该小说的所有分析数据、对话记录和书签。",
  },
  {
    keywords: ["更新", "新版本", "升级"],
    answer:
      "桌面版会在启动时自动检查新版本。如果有更新，书架页标题旁会显示版本提示。点击即可前往下载页面。您也可以访问 GitHub Releases 页面手动下载最新版本。",
  },
]

/** Entity name patterns — if question contains these, it's likely about novel content */
const ENTITY_PATTERNS = /[\u4e00-\u9fff]{2,}(?:和|与|跟|对)[\u4e00-\u9fff]{2,}/

export interface FaqResult {
  answer: string
  related?: string[]
  confidence: number
}

/**
 * Match a user question against the FAQ database.
 * Returns the best match with confidence score, or null if no match.
 */
export function matchSystemFaq(question: string): FaqResult | null {
  const q = question.toLowerCase().trim()
  if (!q) return null

  let bestMatch: FaqEntry | null = null
  let bestScore = 0

  for (const entry of FAQ_ENTRIES) {
    let matchCount = 0
    for (const kw of entry.keywords) {
      if (q.includes(kw.toLowerCase())) {
        matchCount++
      }
    }
    if (matchCount === 0) continue

    const score = matchCount / entry.keywords.length
    if (score > bestScore) {
      bestScore = score
      bestMatch = entry
    }
  }

  if (!bestMatch || bestScore === 0) return null

  // Confidence: base from keyword match ratio
  let confidence = Math.min(bestScore * 1.5, 1.0)

  // If question also looks like a novel content question (contains entity-like names),
  // reduce confidence to prefer novel QA path
  if (ENTITY_PATTERNS.test(question)) {
    confidence *= 0.5
  }

  return {
    answer: bestMatch.answer,
    related: bestMatch.related,
    confidence,
  }
}

/** Predefined quick questions for the chat panel */
export const QUICK_QUESTIONS = [
  "怎么上传小说？",
  "如何配置 AI 引擎？",
  "分析需要多长时间？",
  "数据存在哪里？",
  "有哪些快捷键？",
]
