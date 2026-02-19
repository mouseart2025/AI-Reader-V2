import type { CostEstimate } from "@/api/types"
import { Button } from "@/components/ui/button"
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
  const budgetEnabled = estimate && estimate.monthly_budget_cny > 0
  const remaining = budgetEnabled
    ? estimate.monthly_budget_cny - estimate.monthly_used_cny
    : Infinity
  const exceedsBudget = budgetEnabled && estimate.estimated_cost_cny > remaining

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>云端分析成本预估</AlertDialogTitle>
          <AlertDialogDescription>
            使用云端 API 分析将产生费用，请确认后继续。
          </AlertDialogDescription>
        </AlertDialogHeader>

        {loading ? (
          <p className="text-sm text-muted-foreground py-4">计算中...</p>
        ) : estimate ? (
          <div className="space-y-3 py-2">
            {/* Basic info */}
            <div className="grid grid-cols-2 gap-2 text-sm">
              <span className="text-muted-foreground">小说</span>
              <span className="font-medium">{estimate.novel_title}</span>
              <span className="text-muted-foreground">分析范围</span>
              <span>第 {estimate.chapter_range[0]}~{estimate.chapter_range[1]} 章（{estimate.chapter_count} 章）</span>
              <span className="text-muted-foreground">总字数</span>
              <span>{(estimate.total_words / 10000).toFixed(1)} 万字</span>
              <span className="text-muted-foreground">提供商 / 模型</span>
              <span className="font-mono text-xs">{estimate.model}</span>
            </div>

            {/* Token breakdown */}
            <div className="border rounded-md p-3 space-y-1.5">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">预估输入 Token</span>
                <span>{formatTokens(estimate.estimated_input_tokens)}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">预估输出 Token</span>
                <span>{formatTokens(estimate.estimated_output_tokens)}</span>
              </div>
              {estimate.includes_prescan && (
                <p className="text-[10px] text-muted-foreground">
                  含实体预扫描一次性调用
                </p>
              )}
              <div className="flex justify-between text-sm border-t pt-1.5 font-medium">
                <span>预估费用</span>
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
                  <span className="text-muted-foreground">本月已用</span>
                  <span>¥{estimate.monthly_used_cny.toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">月度预算</span>
                  <span>¥{estimate.monthly_budget_cny.toFixed(0)}</span>
                </div>
                <div className="flex justify-between font-medium">
                  <span>剩余预算</span>
                  <span className={remaining <= 0 ? "text-red-600" : ""}>
                    ¥{Math.max(0, remaining).toFixed(2)}
                  </span>
                </div>
                {exceedsBudget && (
                  <p className="text-xs text-red-600 pt-1">
                    本次预估费用 (¥{estimate.estimated_cost_cny}) 超出剩余预算，继续分析可能导致超支。
                  </p>
                )}
              </div>
            )}

            {/* Pricing info */}
            <p className="text-[10px] text-muted-foreground">
              定价: 输入 ${estimate.input_price_per_1m}/1M tokens, 输出 ${estimate.output_price_per_1m}/1M tokens。
              实际费用可能浮动 ±30%。
            </p>
          </div>
        ) : null}

        <AlertDialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={onConfirm} disabled={loading}>
            {exceedsBudget ? "超出预算，仍然开始" : "确认开始分析"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
