/**
 * "体验版" badge — shown only on pre-release builds (version contains beta/rc/
 * alpha). Signals to users they're on the experimental channel where features
 * like alias editing live, separate from the stable release.
 */
const VERSION = __APP_VERSION__
const IS_PRERELEASE = /-(beta|rc|alpha)/i.test(VERSION)

export function BetaBadge({ className = "" }: { className?: string }) {
  if (!IS_PRERELEASE) return null
  return (
    <span
      title={`体验版 v${VERSION} — 包含尚在打磨的新功能（如别名手动编辑）`}
      className={`rounded-full border border-amber-500/60 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400 ${className}`}
    >
      体验版
    </span>
  )
}
