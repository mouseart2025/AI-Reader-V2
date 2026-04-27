import type { TranslationKey, TranslationParams } from "@/i18n"
import { itemActionLabel, orgActionLabel, relationTypeLabel } from "./domainLabels"

type TFunc = (key: TranslationKey, params?: TranslationParams) => string

export type TimelineSummaryEvent = {
  summary: string
  summary_template_id?: string | null
  summary_args?: Record<string, unknown> | null
}

function argString(args: Record<string, unknown> | null | undefined, key: string): string {
  const value = args?.[key]
  return typeof value === "string" ? value : ""
}

export function timelineEventSummary(t: TFunc, event: TimelineSummaryEvent): string {
  const templateId = event.summary_template_id || ""
  const args = event.summary_args || {}

  switch (templateId) {
    case "character_appearance":
      return t("timeline.summary.characterAppearance", {
        person: argString(args, "person"),
      })
    case "item_transfer":
      return t("timeline.summary.itemTransfer", {
        actor: argString(args, "actor"),
        action: itemActionLabel(t, argString(args, "action_id"), argString(args, "action")),
        item: argString(args, "item"),
      })
    case "item_transfer_to":
      return t("timeline.summary.itemTransferTo", {
        actor: argString(args, "actor"),
        action: itemActionLabel(t, argString(args, "action_id"), argString(args, "action")),
        item: argString(args, "item"),
        recipient: argString(args, "recipient"),
      })
    case "org_change":
      return t("timeline.summary.orgChange", {
        member: argString(args, "member"),
        action: orgActionLabel(t, argString(args, "action_id"), argString(args, "action")),
        org: argString(args, "org"),
      })
    case "org_change_with_role":
      return t("timeline.summary.orgChangeWithRole", {
        member: argString(args, "member"),
        action: orgActionLabel(t, argString(args, "action_id"), argString(args, "action")),
        org: argString(args, "org"),
        role: argString(args, "role"),
      })
    case "relation_new":
      return t("timeline.summary.relationNew", {
        personA: argString(args, "person_a"),
        personB: argString(args, "person_b"),
        relation: relationTypeLabel(t, argString(args, "relation_type_id"), argString(args, "relation_type")),
      })
    case "relation_new_with_evidence":
      return t("timeline.summary.relationNewWithEvidence", {
        personA: argString(args, "person_a"),
        personB: argString(args, "person_b"),
        relation: relationTypeLabel(t, argString(args, "relation_type_id"), argString(args, "relation_type")),
        evidence: argString(args, "evidence_excerpt"),
      })
    case "relation_changed":
      return t("timeline.summary.relationChanged", {
        personA: argString(args, "person_a"),
        personB: argString(args, "person_b"),
        previous: relationTypeLabel(t, argString(args, "previous_type_id"), argString(args, "previous_type")),
        next: relationTypeLabel(t, argString(args, "relation_type_id"), argString(args, "relation_type")),
      })
    default:
      return event.summary
  }
}
