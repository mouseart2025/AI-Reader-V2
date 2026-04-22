/**
 * SecurityGuide — 安全放行引导组件
 * macOS Gatekeeper / Windows SmartScreen 未签名应用放行步骤
 * 优先级 P0：直接影响首次安装成功率
 */

import { Shield, ExternalLink, CheckCircle } from "lucide-react"
import { useI18n } from "@/i18n"

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
  const { t } = useI18n()

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-200">{t("desktop.security.macosTitle")}</h3>
      <Step num={1} text={t("desktop.security.macosStep1")} />
      <Step num={2} text={t("desktop.security.macosStep2")} />
      <Step num={3} text={t("desktop.security.macosStep3")} />
      <p className="text-xs text-slate-500">
        {t("desktop.security.macosNote")}
      </p>
    </div>
  )
}

function WindowsGuide() {
  const { t } = useI18n()

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-200">{t("desktop.security.windowsTitle")}</h3>
      <Step num={1} text={t("desktop.security.windowsStep1")} />
      <Step num={2} text={t("desktop.security.windowsStep2")} />
      <p className="text-xs text-slate-500">
        {t("desktop.security.windowsNote")}
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
  const { t } = useI18n()
  const platform = detectPlatform()

  return (
    <div className="mx-auto max-w-lg space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Shield className="size-8 text-blue-400" />
        <div>
          <h2 className="text-lg font-bold text-slate-100">{t("desktop.security.title")}</h2>
          <p className="text-sm text-slate-400">
            {t("desktop.security.subtitle")}
          </p>
        </div>
      </div>

      {/* Explanation */}
      <p className="text-sm text-slate-400">
        {t("desktop.security.description")}
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
          {t("desktop.security.learnMore")}
        </a>
        <button
          onClick={onDone}
          className="flex items-center gap-1.5 rounded-md bg-blue-500 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 transition"
        >
          <CheckCircle className="size-4" />
          {t("desktop.security.done")}
        </button>
      </div>
    </div>
  )
}
