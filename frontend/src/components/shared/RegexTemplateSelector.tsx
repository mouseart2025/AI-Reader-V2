import { useState } from "react"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const TEMPLATES = [
  { id: "blank_lines", label: "按空行分隔", regex: "\\n\\n\\n+" },
  { id: "separator", label: "按分隔线 (---/===)", regex: "^[*=\\-]{3,}" },
  { id: "numbered", label: "按编号标题 (1. / 2.)", regex: "^\\d+[.、]" },
  { id: "jp_chapter", label: '按"第X帖"', regex: "^第.{1,6}[帖贴]" },
  { id: "cn_numbered", label: "按中文数字 (一、二、)", regex: "^[一二三四五六七八九十百]+、" },
  { id: "custom", label: "自定义正则", regex: "" },
] as const

interface Props {
  onApply: (regex: string) => void
  disabled?: boolean
}

export function RegexTemplateSelector({ onApply, disabled }: Props) {
  const [selected, setSelected] = useState<string>("")
  const [customRegex, setCustomRegex] = useState("")

  const handleSelect = (templateId: string) => {
    setSelected(templateId)
    // Auto-apply for non-custom templates
    if (templateId !== "custom") {
      const tpl = TEMPLATES.find((t) => t.id === templateId)
      if (tpl) onApply(tpl.regex)
    }
  }

  const handleApplyCustom = () => {
    const regex = customRegex.trim()
    if (regex) onApply(regex)
  }

  return (
    <div className="space-y-2">
      <Label className="text-xs">正则模板</Label>
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Select
            value={selected}
            onValueChange={handleSelect}
            disabled={disabled}
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue placeholder="选择切分模板..." />
            </SelectTrigger>
            <SelectContent>
              {TEMPLATES.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {selected === "custom" && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleApplyCustom}
            disabled={disabled || !customRegex.trim()}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            应用
          </Button>
        )}
      </div>
      {selected === "custom" && (
        <div className="space-y-1">
          <Input
            value={customRegex}
            onChange={(e) => setCustomRegex(e.target.value)}
            placeholder="例如: ^第[\d]+章\s*(.*)"
            className="h-8 font-mono text-sm"
            disabled={disabled}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                handleApplyCustom()
              }
            }}
          />
          <p className="text-xs text-muted-foreground">
            正则将以 MULTILINE 模式逐行匹配，匹配位置作为章节分割点
          </p>
        </div>
      )}
    </div>
  )
}
