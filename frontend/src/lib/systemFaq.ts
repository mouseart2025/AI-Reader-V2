/**
 * System FAQ — keyword-based matching for common user questions.
 * No LLM dependency, pure frontend logic.
 */
import { translate, type TranslationKey } from "@/i18n"

interface FaqEntry {
  keywords: string[]
  answerKey: TranslationKey
  relatedKeys?: TranslationKey[]
}

const FAQ_ENTRIES: FaqEntry[] = [
  // === 功能类 ===
  {
    keywords: ["上传", "导入小说", "添加小说", "怎么添加", "upload", "import novel", "add novel", "tải truyện", "nhập truyện"],
    answerKey: "faq.upload.answer",
    relatedKeys: ["faq.quick.supportedFormats", "faq.quick.howToAnalyze"],
  },
  {
    keywords: ["支持什么格式", "文件格式", "txt", "md", "format", "file type", "định dạng", "tệp"],
    answerKey: "faq.formats.answer",
  },
  {
    keywords: ["分析", "开始分析", "怎么分析", "如何分析", "analyze", "analysis", "phân tích"],
    answerKey: "faq.analyze.answer",
    relatedKeys: ["faq.quick.analysisTime", "faq.quick.configureAi"],
  },
  {
    keywords: ["关系图", "图谱", "人物关系", "relationship graph", "graph", "sơ đồ quan hệ"],
    answerKey: "faq.graph.answer",
  },
  {
    keywords: ["地图", "世界地图", "小说地图", "map", "world map", "bản đồ"],
    answerKey: "faq.map.answer",
  },
  {
    keywords: ["时间线", "timeline", "事件"],
    answerKey: "faq.timeline.answer",
  },
  {
    keywords: ["百科", "人物百科", "encyclopedia", "bách khoa"],
    answerKey: "faq.encyclopedia.answer",
  },
  {
    keywords: ["阅读", "读书", "在线阅读", "read", "reading", "đọc"],
    answerKey: "faq.reading.answer",
  },
  {
    keywords: ["导出", "下载数据", "备份", "export", "backup", "xuất", "sao lưu"],
    answerKey: "faq.export.answer",
  },
  {
    keywords: ["势力", "阵营", "faction", "thế lực"],
    answerKey: "faq.factions.answer",
  },

  // === 配置类 ===
  {
    keywords: ["配置", "AI 引擎", "LLM", "设置 AI", "configure", "AI engine", "cấu hình"],
    answerKey: "faq.configure.answer",
    relatedKeys: ["faq.quick.whatIsOllama", "faq.quick.cloudApi"],
  },
  {
    keywords: ["Ollama", "本地模型", "本地 AI", "local model", "mô hình local"],
    answerKey: "faq.ollama.answer",
  },
  {
    keywords: ["云端", "API", "API Key", "密钥", "cloud", "khóa API"],
    answerKey: "faq.cloudApi.answer",
  },
  {
    keywords: ["DeepSeek", "deepseek"],
    answerKey: "faq.deepseek.answer",
  },

  // === 概念类 ===
  {
    keywords: ["预扫描", "实体预扫描", "pre-scan", "prescan", "quét trước"],
    answerKey: "faq.prescan.answer",
  },
  {
    keywords: ["分析时间", "需要多久", "多长时间", "分析速度", "how long", "duration", "mất bao lâu"],
    answerKey: "faq.analysisTime.answer",
  },
  {
    keywords: ["数据存在哪", "数据位置", "存储位置", "隐私", "where data", "privacy", "dữ liệu", "riêng tư"],
    answerKey: "faq.dataStorage.answer",
  },
  {
    keywords: ["章节切分", "切分", "拆分", "章节识别", "chapter split", "split", "tách chương"],
    answerKey: "faq.chapterSplit.answer",
  },
  {
    keywords: ["别名", "alias", "同一个人", "bí danh"],
    answerKey: "faq.alias.answer",
  },

  // === 使用类 ===
  {
    keywords: ["快捷键", "keyboard", "键盘", "phím tắt"],
    answerKey: "faq.shortcuts.answer",
  },
  {
    keywords: ["问答", "提问", "聊天", "chat", "hỏi đáp"],
    answerKey: "faq.chat.answer",
  },
  {
    keywords: ["样本", "预装", "内置", "示例", "sample", "demo", "mẫu"],
    answerKey: "faq.samples.answer",
  },
  {
    keywords: ["删除", "移除小说", "delete", "remove", "xóa"],
    answerKey: "faq.delete.answer",
  },
  {
    keywords: ["更新", "新版本", "升级", "update", "new version", "cập nhật"],
    answerKey: "faq.update.answer",
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
    answer: translate(bestMatch.answerKey),
    related: bestMatch.relatedKeys?.map((key) => translate(key)),
    confidence,
  }
}

/** Predefined quick questions for the chat panel */
const QUICK_QUESTION_KEYS = [
  "faq.quick.uploadNovel",
  "faq.quick.configureAi",
  "faq.quick.analysisTime",
  "faq.quick.dataStorage",
  "faq.quick.shortcuts",
] as const satisfies readonly TranslationKey[]

export function getQuickQuestions() {
  return QUICK_QUESTION_KEYS.map((key) => translate(key))
}

export const QUICK_QUESTIONS = [
  ...QUICK_QUESTION_KEYS,
]
