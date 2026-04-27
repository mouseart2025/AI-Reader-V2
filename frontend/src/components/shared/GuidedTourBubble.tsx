import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"

interface GuidedTourBubbleProps {
  step: number
  totalSteps: number
  message: string
  onNext: () => void
  onDismiss: () => void
  /** Position relative to the target element */
  position?: "top" | "bottom"
}

export function GuidedTourBubble({
  step,
  totalSteps,
  message,
  onNext,
  onDismiss,
  position = "bottom",
}: GuidedTourBubbleProps) {
  const { t } = useI18n()
  const isLast = step + 1 === totalSteps

  return (
    <div
      className={`absolute z-50 w-72 rounded-lg bg-gray-900/95 p-3 text-white shadow-lg ${
        position === "top" ? "bottom-full mb-2" : "top-full mt-2"
      }`}
    >
      {/* Arrow */}
      <div
        className={`absolute left-6 h-0 w-0 border-x-[6px] border-x-transparent ${
          position === "top"
            ? "top-full border-t-[6px] border-t-gray-900/95"
            : "bottom-full border-b-[6px] border-b-gray-900/95"
        }`}
      />

      {/* Step indicator */}
      <div className="mb-1 text-xs text-white/60">
        {step + 1}/{totalSteps}
      </div>

      {/* Message */}
      <p className="mb-3 text-sm leading-relaxed">{message}</p>

      {/* Actions */}
      <div className="flex items-center justify-between">
        <button
          className="text-xs text-white/50 hover:text-white/80"
          onClick={onDismiss}
        >
          {t("shared.guidedTour.dismiss")}
        </button>
        <Button
          size="sm"
          variant="secondary"
          className="h-7 px-3 text-xs"
          onClick={onNext}
        >
          {isLast ? t("shared.guidedTour.finish") : t("shared.guidedTour.next")}
        </Button>
      </div>
    </div>
  )
}
