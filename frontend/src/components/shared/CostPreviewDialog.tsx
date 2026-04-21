import type { CostEstimate } from "@/api/types"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

interface CostPreviewDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  estimate: CostEstimate | null
  loading: boolean
  onConfirm: () => void
}

export function CostPreviewDialog({
  open,
  onOpenChange,
  estimate,
  loading,
  onConfirm,
}: CostPreviewDialogProps) {
  const { t } = useI18n()
  const budgetEnabled = estimate && estimate.monthly_budget_cny > 0
  const remaining = budgetEnabled
    ? estimate.monthly_budget_cny - estimate.monthly_used_cny
    : Infinity
  const exceedsBudget = budgetEnabled && estimate.estimated_cost_cny > remaining

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("shared.costPreview.title")}</AlertDialogTitle>
          <AlertDialogDescription>
            {t("shared.costPreview.description")}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {loading ? (
          <p className="text-sm text-muted-foreground py-4">{t("shared.costPreview.calculating")}</p>
        ) : estimate ? (
          <div className="space-y-3 py-2">
            {/* Basic info */}
            <div className="grid grid-cols-2 gap-2 text-sm">
              <span className="text-muted-foreground">{t("shared.costPreview.novel")}</span>
              <span className="font-medium">{estimate.novel_title}</span>
              <span className="text-muted-foreground">{t("shared.costPreview.analysisRange")}</span>
              <span>
                {t("shared.costPreview.chapterRange", {
                  start: estimate.chapter_range[0],
                  end: estimate.chapter_range[1],
                  count: estimate.chapter_count,
                })}
              </span>
              <span className="text-muted-foreground">{t("shared.costPreview.totalWords")}</span>
              <span>{t("shared.costPreview.wordCountWan", { count: (estimate.total_words / 10000).toFixed(1) })}</span>
              <span className="text-muted-foreground">{t("shared.costPreview.providerModel")}</span>
              <span className="font-mono text-xs">{estimate.model}</span>
            </div>

            {/* Token breakdown */}
            <div className="border rounded-md p-3 space-y-1.5">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">{t("shared.costPreview.inputTokens")}</span>
                <span>{formatTokens(estimate.estimated_input_tokens)}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">{t("shared.costPreview.outputTokens")}</span>
                <span>{formatTokens(estimate.estimated_output_tokens)}</span>
              </div>
              {estimate.includes_prescan && (
                <p className="text-[10px] text-muted-foreground">
                  {t("shared.costPreview.includesPrescan")}
                </p>
              )}
              <div className="flex justify-between text-sm border-t pt-1.5 font-medium">
                <span>{t("shared.costPreview.estimatedCost")}</span>
                <span>
                  ¥{estimate.estimated_cost_cny}
                  <span className="text-muted-foreground text-xs ml-1">
                    (${estimate.estimated_cost_usd})
                  </span>
                </span>
              </div>
            </div>

            {/* Budget info */}
            {budgetEnabled && (
              <div className={`rounded-md p-3 text-sm space-y-1 ${exceedsBudget ? "bg-red-50 border border-red-200 dark:bg-red-950/20 dark:border-red-900" : "bg-muted/30"}`}>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t("shared.costPreview.monthUsed")}</span>
                  <span>¥{estimate.monthly_used_cny.toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t("shared.costPreview.monthBudget")}</span>
                  <span>¥{estimate.monthly_budget_cny.toFixed(0)}</span>
                </div>
                <div className="flex justify-between font-medium">
                  <span>{t("shared.costPreview.remainingBudget")}</span>
                  <span className={remaining <= 0 ? "text-red-600" : ""}>
                    ¥{Math.max(0, remaining).toFixed(2)}
                  </span>
                </div>
                {exceedsBudget && (
                  <p className="text-xs text-red-600 pt-1">
                    {t("shared.costPreview.exceedsBudget", { cost: estimate.estimated_cost_cny })}
                  </p>
                )}
              </div>
            )}

            {/* Pricing info */}
            <p className="text-[10px] text-muted-foreground">
              {t("shared.costPreview.pricing", {
                input: estimate.input_price_per_1m,
                output: estimate.output_price_per_1m,
              })}
            </p>
          </div>
        ) : null}

        <AlertDialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button onClick={onConfirm} disabled={loading}>
            {exceedsBudget ? t("shared.costPreview.confirmOverBudget") : t("shared.costPreview.confirm")}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
