/**
 * DemoExportPage — previews the 4 export formats (MD / DOCX / XLSX / PDF).
 * Shows sample rendered output for each format as a conversion showcase.
 */
import { useState } from "react"
import { useDemoData } from "@/app/DemoContext"

type ExportFormat = "markdown" | "docx" | "xlsx" | "pdf"

interface FormatInfo {
  key: ExportFormat
  label: string
  icon: string
  ext: string
  description: string
  proOnly: boolean
}

const FORMATS: FormatInfo[] = [
  { key: "markdown", label: "Markdown", icon: "📝", ext: ".md", description: "通用纯文本格式，适合版本控制和知识库", proOnly: false },
  { key: "docx", label: "Word", icon: "📄", ext: ".docx", description: "排版精美的设定集文档，适合编辑和分享", proOnly: true },
  { key: "xlsx", label: "Excel", icon: "📊", ext: ".xlsx", description: "结构化数据表格，适合数据分析和筛选", proOnly: true },
  { key: "pdf", label: "PDF", icon: "📕", ext: ".pdf", description: "打印就绪的出版级设定集", proOnly: true },
]

export default function DemoExportPage() {
  const { novelInfo } = useDemoData()
  const [activeFormat, setActiveFormat] = useState<ExportFormat>("markdown")

  return (
    <div className="flex h-full flex-col">
      {/* Format selector bar */}
      <div className="flex items-center gap-3 border-b bg-white/80 px-4 py-2">
        <span className="text-xs text-gray-500">导出格式</span>
        <div className="flex gap-1">
          {FORMATS.map((f) => (
            <button
              key={f.key}
              onClick={() => setActiveFormat(f.key)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                activeFormat === f.key
                  ? "bg-blue-50 text-blue-700"
                  : "text-gray-500 hover:bg-gray-100"
              }`}
            >
              <span className="mr-1">{f.icon}</span>
              {f.label}
              {f.proOnly && <span className="ml-1 text-[10px] text-orange-500">Pro</span>}
            </button>
          ))}
        </div>
      </div>

      {/* Preview area */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-3xl">
          {/* Format description */}
          <div className="mb-6 text-center">
            <h2 className="text-lg font-bold text-gray-800">
              {FORMATS.find((f) => f.key === activeFormat)?.icon}{" "}
              {FORMATS.find((f) => f.key === activeFormat)?.label} 导出预览
            </h2>
            <p className="mt-1 text-sm text-gray-500">
              {FORMATS.find((f) => f.key === activeFormat)?.description}
            </p>
          </div>

          {/* Markdown preview */}
          {activeFormat === "markdown" && (
            <div className="rounded-lg border bg-white p-6 font-mono text-sm leading-relaxed text-gray-700">
              <p className="mb-4 text-lg font-bold">
                # {novelInfo.title} — 设定集 (Series Bible)
              </p>
              <p className="mb-2 text-gray-400">---</p>
              <p className="mb-4 font-bold">## 核心人物</p>
              <p className="mb-2 font-bold">### 贾宝玉</p>
              <p className="mb-1">- **别名**: 宝二爷、怡红公子、绛洞花主</p>
              <p className="mb-1">- **出场**: 第1回 — 第120回 (120 回)</p>
              <p className="mb-1">- **关系**: 林黛玉 (恋人)、薛宝钗 (妻子)、贾政 (父亲)...</p>
              <p className="mb-4 text-gray-500">
                &gt; 荣国府贾政之子，衔玉而诞。性情温和，喜与姐妹丫鬟厮混...
              </p>
              <p className="mb-2 font-bold">### 林黛玉</p>
              <p className="mb-1">- **别名**: 颦儿、潇湘妃子</p>
              <p className="mb-1">- **出场**: 第2回 — 第98回 (97 回)</p>
              <p className="mb-1">- **关系**: 贾宝玉 (恋人)、贾母 (外祖母)...</p>
              <p className="mb-4">...</p>
              <p className="mb-4 text-gray-400">---</p>
              <p className="mb-2 font-bold">## 关键地点</p>
              <p className="mb-2 font-bold">### 大观园</p>
              <p className="mb-1">- **类型**: 园林</p>
              <p className="mb-1">- **子地点**: 怡红院、潇湘馆、蘅芜苑、稻香村...</p>
              <p className="text-gray-400">...</p>
            </div>
          )}

          {/* DOCX preview */}
          {activeFormat === "docx" && (
            <div className="rounded-lg border bg-white p-8 shadow-sm">
              <div className="border-b pb-4 mb-6 text-center">
                <h1 className="text-2xl font-serif font-bold text-gray-900">{novelInfo.title}</h1>
                <p className="mt-1 text-sm text-gray-500 font-serif">设定集 · Series Bible</p>
                <p className="mt-2 text-xs text-gray-400">由 AI Reader V2 自动生成</p>
              </div>
              <h2 className="text-lg font-serif font-bold text-gray-800 mb-3 border-b pb-1">目录</h2>
              <ul className="mb-6 space-y-1 text-sm text-blue-600 font-serif">
                <li>一、核心人物 .......................... 2</li>
                <li>二、关键地点 .......................... 15</li>
                <li>三、重要物品 .......................... 22</li>
                <li>四、组织势力 .......................... 25</li>
                <li>五、事件时间线 ........................ 28</li>
                <li>六、人物关系总表 ...................... 35</li>
              </ul>
              <h2 className="text-lg font-serif font-bold text-gray-800 mb-3 border-b pb-1">一、核心人物</h2>
              <div className="mb-4">
                <h3 className="font-serif font-bold text-gray-700">贾宝玉</h3>
                <table className="mt-2 w-full text-sm border">
                  <tbody>
                    <tr className="border-b"><td className="px-2 py-1 font-medium bg-gray-50 w-24">别名</td><td className="px-2 py-1">宝二爷、怡红公子</td></tr>
                    <tr className="border-b"><td className="px-2 py-1 font-medium bg-gray-50">出场范围</td><td className="px-2 py-1">第1回 — 第120回</td></tr>
                    <tr><td className="px-2 py-1 font-medium bg-gray-50">主要关系</td><td className="px-2 py-1">林黛玉 (恋人)、薛宝钗 (妻子)</td></tr>
                  </tbody>
                </table>
              </div>
              <p className="text-sm text-gray-400 font-serif text-center mt-6">... 更多内容 ...</p>
            </div>
          )}

          {/* XLSX preview */}
          {activeFormat === "xlsx" && (
            <div className="rounded-lg border bg-white overflow-hidden">
              <div className="flex border-b bg-gray-50 text-xs">
                <span className="border-r px-3 py-1.5 font-medium text-green-700 bg-green-50">人物表</span>
                <span className="border-r px-3 py-1.5 text-gray-500">地点表</span>
                <span className="border-r px-3 py-1.5 text-gray-500">关系表</span>
                <span className="border-r px-3 py-1.5 text-gray-500">事件表</span>
                <span className="px-3 py-1.5 text-gray-500">物品表</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50 border-b">
                      <th className="px-3 py-2 text-left font-medium text-gray-600 border-r">姓名</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 border-r">别名</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 border-r">首次出场</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 border-r">出场回数</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 border-r">所属组织</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600">关系数</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-b hover:bg-blue-50"><td className="px-3 py-1.5 border-r font-medium">贾宝玉</td><td className="px-3 py-1.5 border-r">宝二爷、怡红公子</td><td className="px-3 py-1.5 border-r">1</td><td className="px-3 py-1.5 border-r">120</td><td className="px-3 py-1.5 border-r">荣国府</td><td className="px-3 py-1.5">45</td></tr>
                    <tr className="border-b hover:bg-blue-50"><td className="px-3 py-1.5 border-r font-medium">林黛玉</td><td className="px-3 py-1.5 border-r">颦儿、潇湘妃子</td><td className="px-3 py-1.5 border-r">2</td><td className="px-3 py-1.5 border-r">97</td><td className="px-3 py-1.5 border-r">荣国府</td><td className="px-3 py-1.5">32</td></tr>
                    <tr className="border-b hover:bg-blue-50"><td className="px-3 py-1.5 border-r font-medium">薛宝钗</td><td className="px-3 py-1.5 border-r">宝姐姐</td><td className="px-3 py-1.5 border-r">4</td><td className="px-3 py-1.5 border-r">95</td><td className="px-3 py-1.5 border-r">薛家</td><td className="px-3 py-1.5">28</td></tr>
                    <tr className="border-b hover:bg-blue-50"><td className="px-3 py-1.5 border-r font-medium">王熙凤</td><td className="px-3 py-1.5 border-r">凤姐、凤辣子</td><td className="px-3 py-1.5 border-r">3</td><td className="px-3 py-1.5 border-r">89</td><td className="px-3 py-1.5 border-r">荣国府</td><td className="px-3 py-1.5">35</td></tr>
                    <tr className="hover:bg-blue-50"><td className="px-3 py-1.5 border-r font-medium">贾母</td><td className="px-3 py-1.5 border-r">老太太、史太君</td><td className="px-3 py-1.5 border-r">3</td><td className="px-3 py-1.5 border-r">82</td><td className="px-3 py-1.5 border-r">荣国府</td><td className="px-3 py-1.5">26</td></tr>
                  </tbody>
                </table>
              </div>
              <div className="border-t bg-gray-50 px-3 py-2 text-xs text-gray-400 text-center">
                共 669 行 · 6 列 · 5 工作表
              </div>
            </div>
          )}

          {/* PDF preview */}
          {activeFormat === "pdf" && (
            <div className="rounded-lg border-2 border-gray-300 bg-white p-8 shadow-lg">
              <div className="mb-8 text-center border-b-2 border-gray-800 pb-6">
                <h1 className="text-3xl font-serif font-bold text-gray-900 tracking-widest">{novelInfo.title}</h1>
                <p className="mt-2 text-lg font-serif text-gray-600 tracking-wider">设定集</p>
                <p className="mt-4 text-sm text-gray-400">Series Bible · AI Reader V2 Generated</p>
              </div>
              <div className="grid grid-cols-2 gap-4 text-center text-sm mb-8">
                <div className="rounded border p-3"><p className="text-2xl font-bold text-blue-600">669</p><p className="text-gray-500">人物</p></div>
                <div className="rounded border p-3"><p className="text-2xl font-bold text-green-600">756</p><p className="text-gray-500">地点</p></div>
                <div className="rounded border p-3"><p className="text-2xl font-bold text-orange-600">776</p><p className="text-gray-500">关系</p></div>
                <div className="rounded border p-3"><p className="text-2xl font-bold text-purple-600">122</p><p className="text-gray-500">章回</p></div>
              </div>
              <p className="text-xs text-gray-400 text-center">第 1 页 / 共 42 页</p>
            </div>
          )}

          {/* Export CTA */}
          <div className="mt-8 rounded-lg border-2 border-dashed border-gray-300 bg-gray-50 p-6 text-center">
            <p className="mb-2 text-sm font-semibold text-gray-600">安装完整版即可导出你自己的小说设定集</p>
            <p className="mb-4 text-xs text-gray-400">
              Markdown (免费) · Word / Excel / PDF (Pro)
            </p>
            <div className="flex justify-center gap-3">
              <a
                href="https://github.com/mouseart2025/AI-Reader-V2"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block rounded-md bg-blue-500 px-6 py-2 text-sm font-semibold text-white hover:bg-blue-600 transition"
              >
                免费下载
              </a>
              <a
                href="https://ai-reader.cc/#download"
                className="inline-block rounded-md border border-gray-300 px-6 py-2 text-sm font-semibold text-gray-600 hover:border-blue-500 hover:text-blue-600 transition"
              >
                快速开始
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
