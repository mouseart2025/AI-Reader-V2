/**
 * SecurityGuide — 安全放行引导组件
 * macOS Gatekeeper / Windows SmartScreen 未签名应用放行步骤
 * 优先级 P0：直接影响首次安装成功率
 */

import { Shield, ExternalLink, CheckCircle } from "lucide-react"

interface SecurityGuideProps {
  onDone: () => void
}

const GITHUB_URL = "https://github.com/mouseart2025/AI-Reader-V2"

/** Detect macOS vs Windows at runtime */
function detectPlatform(): "macos" | "windows" | "other" {
  const ua = navigator.userAgent.toLowerCase()
  if (ua.includes("mac")) return "macos"
  if (ua.includes("win")) return "windows"
  return "other"
}

function MacGuide() {
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-200">macOS Gatekeeper 放行步骤</h3>
      <Step num={1} text="右键点击 AI Reader 应用图标（不要双击）" />
      <Step num={2} text='在弹出菜单中选择「打开」' />
      <Step num={3} text='在确认对话框中点击「打开」按钮' />
      <p className="text-xs text-slate-500">
        只需操作一次，之后可以正常双击打开应用
      </p>
    </div>
  )
}

function WindowsGuide() {
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-200">Windows SmartScreen 放行步骤</h3>
      <Step num={1} text='在 SmartScreen 弹窗中点击「更多信息」' />
      <Step num={2} text='点击「仍要运行」按钮' />
      <p className="text-xs text-slate-500">
        只需操作一次，之后可以正常启动应用
      </p>
    </div>
  )
}

function Step({ num, text }: { num: number; text: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-slate-700 bg-slate-800 p-3">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-500/20 text-xs font-bold text-blue-400">
        {num}
      </span>
      <p className="text-sm text-slate-300">{text}</p>
    </div>
  )
}

export function SecurityGuide({ onDone }: SecurityGuideProps) {
  const platform = detectPlatform()

  return (
    <div className="mx-auto max-w-lg space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Shield className="size-8 text-blue-400" />
        <div>
          <h2 className="text-lg font-bold text-slate-100">安全放行指南</h2>
          <p className="text-sm text-slate-400">
            AI Reader 是开源安全软件
          </p>
        </div>
      </div>

      {/* Explanation */}
      <p className="text-sm text-slate-400">
        由于未购买代码签名证书，系统可能会显示安全提示。请按照以下步骤放行应用：
      </p>

      {/* Platform-specific guide */}
      {platform === "macos" && <MacGuide />}
      {platform === "windows" && <WindowsGuide />}
      {platform === "other" && (
        <>
          <MacGuide />
          <div className="border-t border-slate-700" />
          <WindowsGuide />
        </>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pt-2">
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-blue-400 transition"
        >
          <ExternalLink className="size-4" />
          了解更多
        </a>
        <button
          onClick={onDone}
          className="flex items-center gap-1.5 rounded-md bg-blue-500 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 transition"
        >
          <CheckCircle className="size-4" />
          我已完成
        </button>
      </div>
    </div>
  )
}
