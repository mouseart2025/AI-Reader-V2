import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import type {
  AdjudicationVerdict,
  AnalysisPass,
  PassChapterDiff,
  PassDiffEntry,
  PassFieldDiff,
} from "@/api/types"
import { fetchPassDiff, submitAdjudication } from "@/api/client"
import { novelPath } from "@/lib/novelPaths"
import { usePassStore } from "@/stores/passStore"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const COLLECTION_LABELS: Record<string, string> = {
  characters: "人物",
  relationships: "关系",
  locations: "地点",
  events: "事件",
  item_events: "物品",
  org_events: "组织",
  new_concepts: "概念",
}

const FIELD_LABELS: Record<string, string> = {
  relation_type: "关系类型",
  polarity: "关系极性",
  rel_subtype: "关系子类",
  closeness: "亲密度",
  is_new: "是否新建立",
  previous_type: "原关系类型",
  evidence: "原文证据",
  type: "类型",
  parent: "父地点",
  role: "角色",
  participants: "参与者",
  location: "发生地点",
  importance: "重要性",
  item_type: "物品类型",
  actor: "动作主体",
  recipient: "接收者",
  org_type: "组织类型",
  category: "概念分类",
  new_aliases: "别名",
  appearance: "出场方式",
  abilities_gained: "获得能力",
  locations_in_chapter: "本章位置",
}

const CATEGORY_LABELS: Record<string, string> = {
  type: "类型分歧",
  identity: "指称分歧",
  boundary: "边界分歧",
  temporal: "时态分歧",
  resolution: "证据分歧",
  other: "其他",
}

const CATEGORY_COLORS: Record<string, string> = {
  type: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
  identity: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  boundary: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  temporal: "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300",
  resolution: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
  other: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
}

const VERDICT_LABELS: Record<AdjudicationVerdict, string> = {
  accept_main: "采纳一审",
  accept_pass: "采纳二审",
  neither: "两者皆否",
}

type GroupKey = "only_in_main" | "only_in_pass" | "different"

const GROUP_META: { key: GroupKey; label: string; hint: string; badgeClass: string }[] = [
  {
    key: "only_in_main",
    label: "仅一审有",
    hint: "二审没有提取到这些记录",
    badgeClass: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  },
  {
    key: "only_in_pass",
    label: "仅二审有",
    hint: "二审新发现的记录",
    badgeClass: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  },
  {
    key: "different",
    label: "双方不一致",
    hint: "两次抽取都有但字段不同",
    badgeClass: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  },
]

/** diff 条目 id:与裁决写回/恢复共用同一口径 */
function entryId(group: GroupKey, e: PassDiffEntry): string {
  return `${group}:${e.collection}:${e.key}`
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—"
  if (typeof v === "boolean") return v ? "是" : "否"
  if (Array.isArray(v)) {
    if (v.length === 0) return "—"
    return v
      .map((x) =>
        x && typeof x === "object"
          ? Object.values(x as Record<string, unknown>).filter(Boolean).join("/")
          : String(x),
      )
      .join("、")
  }
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}

/** 单条记录的简述(仅一审/仅二审条目用) */
function describeItem(collection: string, item: Record<string, unknown>): string {
  const s = (k: string) => (item[k] ? String(item[k]) : "")
  switch (collection) {
    case "characters":
      return [s("appearance"), s("description")].filter(Boolean).join(" · ")
    case "relationships":
      return `${s("person_a")} —${s("relation_type")}→ ${s("person_b")}`
    case "locations":
      return [s("type"), s("parent") && `父级:${s("parent")}`].filter(Boolean).join(" · ")
    case "events":
      return s("summary")
    case "item_events":
      return `${s("actor")} ${s("action")} ${s("item_name")}${s("recipient") ? ` → ${s("recipient")}` : ""}`
    case "org_events":
      return `${s("member")} ${s("action")} ${s("org_name")}`
    case "new_concepts":
      return [s("category"), s("definition")].filter(Boolean).join(" · ")
    default:
      return ""
  }
}

/** 回原文定位用的摘录:优先 evidence 原文,其次事件摘要,最后条目键 */
function highlightSnippet(e: PassDiffEntry): string {
  const rec = e.item ?? e.main ?? e.pass ?? {}
  const ev = rec.evidence
  if (typeof ev === "string" && ev.trim()) return ev.trim().slice(0, 30)
  const summary = rec.summary
  if (typeof summary === "string" && summary.trim()) {
    return summary.trim().slice(0, 30)
  }
  return e.key
}

function FieldDiffTable({ fields }: { fields: PassFieldDiff[] }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-muted-foreground text-left">
          <th className="py-1 pr-2 font-normal">字段</th>
          <th className="py-1 pr-2 font-normal">一审</th>
          <th className="py-1 font-normal">二审</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => (
          <tr key={f.field} className="border-t">
            <td className="py-1 pr-2 whitespace-nowrap">
              {FIELD_LABELS[f.field] ?? f.field}
              <span
                className={cn(
                  "ml-1 rounded px-1 py-0.5 text-[10px]",
                  CATEGORY_COLORS[f.category] ?? CATEGORY_COLORS.other,
                )}
              >
                {CATEGORY_LABELS[f.category] ?? f.category}
              </span>
            </td>
            <td className="max-w-40 py-1 pr-2 break-all">{fmtValue(f.main)}</td>
            <td className="max-w-40 py-1 break-all">{fmtValue(f.pass)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * 单次二审的章节 diff 浏览:章节列表(差异计数徽章)+ 分组 diff 面板 +
 * 回原文跳转 + 人工裁决(只写 history 埋点)。Epic 4 Story 4.2。
 */
export function PassDiffView({
  novelId,
  pass,
  onClose,
}: {
  novelId: string
  pass: AnalysisPass
  onClose: () => void
}) {
  const navigate = useNavigate()
  const loadPasses = usePassStore((s) => s.load)

  const chaptersHist = useMemo(
    () => pass.history_json?.chapters ?? {},
    [pass.history_json],
  )
  // 已覆盖章节(history 有记录):completed_at 非空 = 成功;有 error = 失败
  const chapterNums = useMemo(() => {
    const nums: number[] = []
    for (let n = pass.chapter_start; n <= pass.chapter_end; n++) nums.push(n)
    return nums
  }, [pass.chapter_start, pass.chapter_end])

  const [diffs, setDiffs] = useState<Record<number, PassChapterDiff | null>>({})
  const [loadingCounts, setLoadingCounts] = useState(false)
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null)
  const [chapterError, setChapterError] = useState<string | null>(null)
  /** `${chapter}:${entryId}` → verdict(含从 history adjudication_log 恢复的) */
  const [verdicts, setVerdicts] = useState<Record<string, AdjudicationVerdict>>(
    () => {
      const init: Record<string, AdjudicationVerdict> = {}
      for (const [num, h] of Object.entries(chaptersHist)) {
        for (const log of h.adjudication_log ?? []) {
          init[`${num}:${log.entry_id}`] = log.verdict
        }
      }
      return init
    },
  )
  const [submitting, setSubmitting] = useState<string | null>(null)
  const [adjError, setAdjError] = useState<string | null>(null)

  // 组件级 diff 缓存:批量拉取覆盖章节的 diff 以填充计数徽章(小批并发)
  const fetchedRef = useRef(false)
  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    const covered = chapterNums.filter((n) => chaptersHist[String(n)]?.completed_at)
    if (covered.length === 0) return
    let cancelled = false
    setLoadingCounts(true)
    void (async () => {
      const BATCH = 6
      for (let i = 0; i < covered.length; i += BATCH) {
        const batch = covered.slice(i, i + BATCH)
        const results = await Promise.allSettled(
          batch.map((n) => fetchPassDiff(novelId, pass.id, n)),
        )
        if (cancelled) return
        setDiffs((prev) => {
          const next = { ...prev }
          results.forEach((r, j) => {
            next[batch[j]] = r.status === "fulfilled" ? r.value : null
          })
          return next
        })
      }
      if (!cancelled) setLoadingCounts(false)
    })()
    return () => {
      cancelled = true
    }
  }, [chapterNums, chaptersHist, novelId, pass.id])

  const selectChapter = useCallback(
    async (n: number) => {
      setSelectedChapter(n)
      setChapterError(null)
      if (diffs[n] !== undefined) return
      try {
        const d = await fetchPassDiff(novelId, pass.id, n)
        setDiffs((prev) => ({ ...prev, [n]: d }))
      } catch (e) {
        setDiffs((prev) => ({ ...prev, [n]: null }))
        setChapterError(e instanceof Error ? e.message : String(e))
      }
    },
    [diffs, novelId, pass.id],
  )

  const adjudicate = useCallback(
    async (chapter: number, e: PassDiffEntry, group: GroupKey, verdict: AdjudicationVerdict) => {
      const id = entryId(group, e)
      const stateKey = `${chapter}:${id}`
      setSubmitting(stateKey)
      setAdjError(null)
      try {
        await submitAdjudication(novelId, pass.id, {
          chapter,
          entry_id: id,
          verdict,
        })
        setVerdicts((prev) => ({ ...prev, [stateKey]: verdict }))
        // 刷新 pass 列表,让 history 裁决计数反映到 UI
        void loadPasses(novelId)
      } catch (err) {
        setAdjError(err instanceof Error ? err.message : String(err))
      } finally {
        setSubmitting(null)
      }
    },
    [novelId, pass.id, loadPasses],
  )

  const goToText = useCallback(
    (chapter: number, e: PassDiffEntry) => {
      const snippet = highlightSnippet(e)
      navigate(
        novelPath(
          novelId,
          "read",
          `chapter=${chapter}&highlight=${encodeURIComponent(snippet)}`,
        ),
      )
    },
    [navigate, novelId],
  )

  const diff = selectedChapter !== null ? diffs[selectedChapter] : undefined
  const totalCounts = useMemo(() => {
    let main = 0
    let passOnly = 0
    let different = 0
    for (const d of Object.values(diffs)) {
      if (!d) continue
      main += d.counts.only_in_main
      passOnly += d.counts.only_in_pass
      different += d.counts.different
    }
    return { main, passOnly, different }
  }, [diffs])

  function renderEntry(group: GroupKey, e: PassDiffEntry, chapter: number) {
    const id = entryId(group, e)
    const stateKey = `${chapter}:${id}`
    const verdict = verdicts[stateKey]
    const record = e.item ?? e.main ?? {}
    const desc = group === "different"
      ? ""
      : describeItem(e.collection, record as Record<string, unknown>)
    return (
      <li key={id} className="rounded-md border p-2.5 text-sm space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="text-[10px] font-normal">
            {COLLECTION_LABELS[e.collection] ?? e.collection}
          </Badge>
          <span className="font-medium">{e.key}</span>
          {group === "different" &&
            (e.fields ?? []).slice(0, 3).map((f) => (
              <span
                key={f.field}
                className={cn(
                  "rounded px-1 py-0.5 text-[10px]",
                  CATEGORY_COLORS[f.category] ?? CATEGORY_COLORS.other,
                )}
              >
                {CATEGORY_LABELS[f.category] ?? f.category}
              </span>
            ))}
        </div>
        {desc && <p className="text-muted-foreground text-xs">{desc}</p>}
        {group === "different" && e.fields && e.fields.length > 0 && (
          <FieldDiffTable fields={e.fields} />
        )}
        <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
          <Button
            variant="ghost"
            size="xs"
            className="text-primary"
            onClick={() => goToText(chapter, e)}
          >
            查看原文
          </Button>
          <span className="text-border">|</span>
          {verdict ? (
            <span className="text-muted-foreground text-xs">
              已裁决:{VERDICT_LABELS[verdict]}
            </span>
          ) : (
            (Object.keys(VERDICT_LABELS) as AdjudicationVerdict[]).map((v) => (
              <Button
                key={v}
                variant="outline"
                size="xs"
                disabled={submitting === stateKey}
                onClick={() => adjudicate(chapter, e, group, v)}
              >
                {VERDICT_LABELS[v]}
              </Button>
            ))
          )}
        </div>
      </li>
    )
  }

  return (
    <div className="rounded-md border p-3 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">
          章节差异对比
          <span className="text-muted-foreground ml-2 text-xs font-normal">
            已统计:仅一审 {totalCounts.main} · 仅二审 {totalCounts.passOnly} ·
            不一致 {totalCounts.different}
            {loadingCounts && "(统计中...)"}
          </span>
        </h3>
        <Button variant="ghost" size="xs" onClick={onClose}>
          收起
        </Button>
      </div>

      <p className="text-muted-foreground text-xs">
        裁决仅写入统计记录,不改动正式分析结果;需改动正式结果时请在实体卡中使用「人工修正」。
      </p>

      {/* 章节列表 + 差异计数徽章 */}
      <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
        {chapterNums.map((n) => {
          const h = chaptersHist[String(n)]
          const covered = !!h?.completed_at
          const failed = !!h?.error
          const d = diffs[n]
          const count = d
            ? d.counts.only_in_main + d.counts.only_in_pass + d.counts.different
            : null
          return (
            <button
              key={n}
              disabled={!covered}
              onClick={() => selectChapter(n)}
              title={
                failed
                  ? `第${n}章二审失败:${h?.error ?? ""}`
                  : covered
                    ? `第${n}章`
                    : `第${n}章二审尚未覆盖`
              }
              className={cn(
                "flex items-center gap-1 rounded border px-2 py-1 text-xs",
                selectedChapter === n
                  ? "border-primary bg-accent"
                  : "hover:bg-accent",
                !covered && "cursor-not-allowed opacity-40",
                failed && "border-red-300 text-red-600 dark:border-red-800",
              )}
            >
              {n}
              {count !== null && count > 0 && (
                <span className="rounded-full bg-amber-100 px-1 text-[10px] text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                  {count}
                </span>
              )}
              {count === 0 && (
                <span className="text-[10px] text-green-600 dark:text-green-400">
                  ✓
                </span>
              )}
            </button>
          )
        })}
      </div>

      {adjError && (
        <p className="text-destructive text-xs">裁决记录失败:{adjError}</p>
      )}

      {/* 选中章节的 diff 面板 */}
      {selectedChapter !== null && (
        <div className="space-y-3 border-t pt-3">
          <h4 className="text-sm font-medium">第 {selectedChapter} 章</h4>
          {chapterError && (
            <p className="text-destructive text-sm">{chapterError}</p>
          )}
          {diff === undefined && !chapterError && (
            <p className="text-muted-foreground text-sm">加载中...</p>
          )}
          {diff === null && !chapterError && (
            <p className="text-muted-foreground text-sm">本章无法生成对比。</p>
          )}
          {diff &&
            diff.counts.only_in_main +
              diff.counts.only_in_pass +
              diff.counts.different ===
              0 && (
              <p className="rounded-md border border-green-200 bg-green-50 p-2 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/20 dark:text-green-300">
                本章无差异,两次抽取结果一致。
              </p>
            )}
          {diff &&
            GROUP_META.map((g) => {
              const entries = diff[g.key]
              if (entries.length === 0) return null
              return (
                <div key={g.key}>
                  <div className="mb-1.5 flex items-center gap-2">
                    <span
                      className={cn(
                        "rounded px-1.5 py-0.5 text-xs font-medium",
                        g.badgeClass,
                      )}
                    >
                      {g.label}({entries.length})
                    </span>
                    <span className="text-muted-foreground text-xs">
                      {g.hint}
                    </span>
                  </div>
                  <ul className="space-y-2">
                    {entries.map((e) => renderEntry(g.key, e, selectedChapter))}
                  </ul>
                </div>
              )
            })}
        </div>
      )}
    </div>
  )
}
