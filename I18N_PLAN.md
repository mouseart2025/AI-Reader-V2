# Internationalization Integration Plan

This document tracks a phased plan for adding first-class internationalization
(i18n) across the full AI Reader V2 application.

The goal is to support multiple interface languages without changing analysis
quality, data models, or Chinese-language NLP assumptions unexpectedly. Chinese
remains the default locale until the project maintainers decide otherwise.

## Goals

- [ ] Add a single locale model shared by frontend, backend, desktop, and demo builds.
- [x] Keep `zh-CN` as the default and source locale during the first implementation phase.
- [x] Add locale packs without requiring repeated edits to feature components.
- [x] Separate UI text translation from AI-generated content and source novel content.
- [ ] Make backend user-facing messages locale-aware where they are shown directly in the UI.
- [ ] Keep extraction prompts, Chinese NLP rules, and analysis heuristics stable unless a locale-specific behavior is explicitly designed.
- [ ] Add tooling that prevents new hardcoded user-visible strings from spreading.

## Non-Goals

- [ ] Do not translate uploaded novels or demo novel content automatically.
- [ ] Do not translate extracted entity names, source quotes, or AI-generated analysis results by default.
- [ ] Do not rewrite the extraction pipeline for non-Chinese novels in the initial i18n work.
- [ ] Do not replace Chinese NLP resources such as jieba dictionaries as part of basic UI localization.

## Locale Architecture

- [x] Define supported locale IDs:
  - [x] `zh-CN` for Simplified Chinese.
  - [x] `en` for English.
  - [x] `vi` for Vietnamese.
- [x] Define fallback order:
  - [x] Active locale.
  - [x] `zh-CN`.
  - [x] Translation key or source text for missing entries.
- [ ] Define locale ownership:
  - [ ] Frontend owns user interface labels, buttons, empty states, dialogs, navigation, and validation hints.
  - [ ] Backend owns API error messages, progress stages, export labels, server-side validation messages, and generated document headings.
  - [ ] Shared API contracts pass stable codes where possible, not translated prose.
- [ ] Define locale persistence:
  - [x] Web build: local storage or existing settings store.
  - [ ] Desktop build: Tauri/store-backed settings if available.
  - [ ] Backend requests: explicit locale header or query value.
- [ ] Define locale negotiation:
  - [ ] User-selected language takes priority.
  - [ ] Browser/system language can be used only as an initial default.
  - [ ] Backend should not guess differently from frontend.

## Phase 0: Baseline Audit

- [ ] Inventory all frontend hardcoded user-visible strings.
  - [ ] `frontend/src/app`
  - [ ] `frontend/src/pages`
  - [ ] `frontend/src/components`
  - [ ] `frontend/src/desktop`
  - [ ] `frontend/src/api`
  - [ ] `frontend/src/lib`
  - [ ] `frontend/src/providers`
- [ ] Classify frontend strings:
  - [ ] Navigation labels.
  - [ ] Form labels and placeholders.
  - [ ] Button labels.
  - [ ] Toasts and status messages.
  - [ ] Empty/error/loading states.
  - [ ] Accessibility labels and titles.
  - [ ] Demo marketing or install prompts.
  - [ ] Domain taxonomy labels.
- [ ] Inventory backend user-facing strings.
  - [ ] API route error details.
  - [ ] Analysis progress stage names.
  - [ ] Export document labels and headings.
  - [ ] Backup/import/export validation messages.
  - [ ] Conflict detector severity labels and summaries.
  - [ ] Settings/provider validation messages.
- [ ] Classify backend strings:
  - [ ] Safe to localize immediately.
  - [ ] Data contract values that require stable enum mapping.
  - [ ] Prompt or NLP logic that should not be localized in the first pass.
- [ ] Add an audit report with counts by module and category.
- [ ] Decide the first target locale pack for the initial PR.

## Phase 1: Frontend I18n Foundation

- [x] Add a lightweight frontend i18n module.
  - [x] `frontend/src/i18n/index.tsx`
  - [x] `frontend/src/i18n/locales/zh-CN.ts`
  - [x] `frontend/src/i18n/locales/en.ts`
  - [x] `frontend/src/i18n/locales/vi.ts`
- [ ] Add a typed translation function or hook.
  - [x] Support plain strings.
  - [x] Support variable interpolation.
  - [ ] Support plural-like formatting where needed.
  - [x] Support missing key fallback.
- [x] Add an i18n provider near the app root.
  - [x] Integrate with `frontend/src/app/providers.tsx`.
  - [x] Expose current locale.
  - [x] Expose locale switching.
  - [x] Persist selected locale.
- [x] Add a small language selector.
  - [x] Prefer Settings first.
  - [x] Add `zh-CN`, `en`, and `vi` options.
  - [x] Add China, United States, and Vietnam flag assets.
  - [ ] Desktop title bar or bookshelf entry can be added later.
- [ ] Add tests for translation lookup and fallback behavior.
- [x] Keep default behavior identical when locale is `zh-CN`.

## Phase 2: Frontend Core Shell Migration

- [x] Migrate route/loading/error shell strings.
  - [x] `frontend/src/app/router.tsx`
  - [x] `frontend/src/app/App.tsx` has no visible strings to migrate.
- [x] Migrate navigation layouts.
  - [x] `frontend/src/app/NovelLayout.tsx`
  - [x] `frontend/src/app/DesktopLayout.tsx`
  - [x] `frontend/src/app/DemoLayout.tsx`
- [ ] Migrate bookshelf and desktop landing surfaces.
  - [x] `frontend/src/pages/BookshelfPage.tsx`
  - [x] `frontend/src/desktop/BookshelfPage.tsx`
  - [x] `frontend/src/desktop/BookshelfCard.tsx`
  - [x] `frontend/src/desktop/DragDropOverlay.tsx`
  - [x] `frontend/src/desktop/SecurityGuide.tsx`
  - [ ] Migrate global shared components.
  - [x] Theme toggle labels.
  - [x] Welcome banner.
  - [x] Feature discovery and guided tour text.
  - [x] Cost preview dialog.
  - [x] Inline LLM setup and contextual guide card.
  - [x] Floating chat panel labels, quick questions, and system FAQ responses.
  - [x] Entity card drawer shell and card-section controls.
  - [x] Novel overview card.
- [ ] Verify both web and desktop shell builds.
  - [x] Web build with `npm run build`.
  - [ ] Desktop build.

## Phase 3: Frontend Feature Page Migration

- [ ] Migrate reading workflow.
  - [ ] `frontend/src/pages/ReadingPage.tsx`
  - [ ] `frontend/src/pages/ScreenplayPage.tsx`
  - [x] `frontend/src/components/shared/ScenePanel.tsx`
  - [x] `frontend/src/components/shared/TextPreviewPanel.tsx`
- [x] Migrate upload/import workflow.
  - [x] `frontend/src/components/shared/UploadDialog.tsx`
  - [x] Regex template labels.
  - [x] File validation messages.
  - [x] Split diagnosis messages.
  - [x] Duplicate import prompts.
- [ ] Migrate analysis workflow.
  - [x] `frontend/src/pages/AnalysisPage.tsx`
  - [x] Progress/status labels.
  - [x] Pause/resume/retry labels.
  - [x] Shared budget and model setup prompts.
  - [x] Setup guide labels.
- [ ] Migrate visualization pages.
  - [ ] `frontend/src/pages/GraphPage.tsx`
  - [ ] `frontend/src/pages/MapPage.tsx`
  - [ ] `frontend/src/pages/TimelinePage.tsx`
  - [ ] `frontend/src/pages/FactionsPage.tsx`
  - [ ] `frontend/src/pages/EncyclopediaPage.tsx`
  - [x] `frontend/src/pages/ConflictsPage.tsx`
  - [x] Shared visualization layout, geography panel, map quality panel, and GeoMap controls.
- [ ] Migrate chat and export pages.
  - [x] `frontend/src/pages/ChatPage.tsx`
  - [x] `frontend/src/pages/ExportPage.tsx`
  - [x] `frontend/src/components/chat/FloatingChatPanel.tsx`
  - [x] `frontend/src/lib/systemFaq.ts`
- [ ] Migrate demo pages.
  - [ ] `frontend/src/pages/demo/*`
- [ ] Avoid translating source novel text, entity names, and quoted evidence.
- [x] Migrate frontend API-client fallback UI messages.
  - [x] Upload progress fallback errors.
  - [x] Hierarchy/spatial streaming fallback errors.
  - [x] Default chat title and Series Bible fallback filenames.

## Phase 4: Frontend Domain Labels And Formatting

- [ ] Centralize domain label maps.
  - [ ] Entity types.
  - [x] Relation categories.
  - [ ] Location tiers.
  - [ ] Scene tones.
  - [ ] Scene event types.
  - [ ] Organization/faction labels.
  - [x] Conflict severity labels.
  - [x] Series Bible export module and template labels.
  - [x] Item, location, and organization card section/stat labels.
  - [x] Person card relation categories, section labels, scene labels, and stat labels.
  - [x] Shared onboarding/setup labels for contextual guide, discovery bar, tour bubble, cost preview, and inline LLM setup.
  - [x] Novel overview metadata, regex template labels, scene panel filters, and setup guide labels.
  - [x] Theme toggle and text preview controls.
  - [x] Shared spatial relation type/value labels.
- [ ] Keep API enum values stable and translate only display labels.
- [ ] Add date/time/number formatting helpers.
  - [ ] Dates in Settings and backups.
  - [ ] Chapter counts and word counts.
  - [ ] File sizes.
  - [ ] Token/cost formatting.
- [ ] Add locale-aware document titles.
  - [x] App shell document titles for migrated layouts.
- [ ] Add missing translation detection in development.

## Phase 5: Backend I18n Foundation

- [ ] Add backend locale utilities.
  - [ ] Locale parsing.
  - [ ] Fallback handling.
  - [ ] Translation lookup.
  - [ ] Interpolation.
- [ ] Add backend locale files.
  - [ ] `backend/src/i18n/locales/zh-CN.json`
  - [ ] `backend/src/i18n/locales/en.json`
- [ ] Define how the frontend passes locale to backend.
  - [ ] `Accept-Language` header.
  - [ ] Optional explicit `X-AI-Reader-Locale` header.
  - [ ] WebSocket initialization message or query parameter.
- [ ] Add request-level locale resolution for REST APIs.
- [ ] Add connection-level locale resolution for WebSocket APIs.
- [ ] Add tests for backend locale fallback.
- [ ] Keep backend logs in a stable developer language or structured codes.

## Phase 6: Backend User-Facing API Messages

- [ ] Replace direct API error prose with stable error codes plus localized messages.
- [ ] Localize upload/import/export errors.
- [ ] Localize backup validation messages.
- [ ] Localize settings validation messages.
- [ ] Localize Ollama/cloud provider health messages where shown in the UI.
- [ ] Localize analysis progress stages.
  - [ ] Context building.
  - [ ] AI extraction.
  - [ ] Data validation.
  - [ ] World-structure update.
  - [ ] Scene analysis.
  - [ ] Hierarchy optimization.
  - [ ] Overview generation.
  - [ ] Completion.
- [ ] Localize WebSocket event messages without changing event names.
- [ ] Keep machine-readable event types unchanged.

## Phase 7: Backend Exports And Generated Documents

- [ ] Localize document export headings.
  - [ ] Markdown.
  - [ ] DOCX.
  - [ ] XLSX.
  - [ ] PDF.
- [ ] Localize template names and descriptions.
- [ ] Localize table headers.
- [ ] Localize section headings.
- [ ] Localize footer text and generated-by labels.
- [ ] Add locale option to export requests.
- [ ] Ensure exported filenames can be localized safely.
- [ ] Preserve entity names, source quotes, and analysis data in original language unless the user explicitly requests translation.

## Phase 8: AI Prompts, Analysis Output, And Locale Boundaries

- [ ] Document which prompts remain Chinese-specific.
- [ ] Add prompt comments explaining why extraction prompts are not basic UI i18n.
- [ ] Decide whether UI locale should influence generated summaries.
- [ ] If localized AI output is supported later, add explicit settings:
  - [ ] Analysis output language.
  - [ ] UI language.
  - [ ] Source text language.
- [ ] Keep source-language extraction as a separate project from UI localization.
- [ ] Avoid silent translation of evidence or entity names.
- [ ] Add tests to ensure locale changes do not alter core extraction contracts unexpectedly.

## Phase 9: Tooling And Quality Gates

- [x] Add a small local i18n toolkit for extraction, checking, and synchronization.
  - [x] Place scripts under `frontend/scripts/i18n/`.
  - [x] Keep tools dependency-light and runnable from npm scripts or Node directly.
  - [x] Document tool usage in this plan and `README_EN.md`.
- [x] Add an `extract` tool to discover candidate user-visible strings.
  - [x] Scan frontend `.ts` and `.tsx` files with a TypeScript-aware parser.
  - [x] Detect JSX text nodes, string literals in common UI props, toast messages, dialog text, placeholders, titles, and aria labels.
  - [ ] Scan backend `.py` files for likely user-facing error, progress, export, and validation messages.
  - [x] Output a reviewable report with file path, line number, source string, suggested key, and category.
  - [x] Avoid auto-editing source files in the first version; generate candidates for manual review.
- [x] Add a `check` tool to prevent missing or stale translation keys.
  - [x] Fail when a translation key used in source is missing from any required locale file.
  - [ ] Fail or warn when locale files contain unused keys.
  - [x] Detect duplicate keys and inconsistent interpolation variables across locales.
  - [x] Detect hardcoded CJK UI strings outside locale files and comments.
  - [ ] Support an allowlist for Chinese NLP data, prompts, fixtures, demo content, and tests.
    - [x] Allowlist frontend demo novel metadata as source content.
    - [x] Ignore non-UI Set membership data, CSS class maps, HTML entity icon tokens, code snippets, regex patterns, and translation-key map keys.
    - [x] Ignore frontend test files and intentional heuristic keyword arrays.
    - [ ] Add broader allowlist categories for prompts, fixtures, and NLP dictionaries.
- [x] Add a `sync` tool to keep locale files aligned.
  - [x] Add missing keys from the source locale into target locale files with source-text placeholders.
  - [x] Preserve stable key ordering for clean diffs.
  - [ ] Preserve translator comments or metadata where possible.
  - [x] Remove stale target keys after matching the source locale.
  - [x] Generate a summary of added, removed, stale, and untranslated keys.
- [ ] Add a `report` tool for progress tracking.
  - [ ] Count translated, untranslated, stale, and missing keys by locale.
  - [ ] Count remaining hardcoded strings by module.
  - [ ] Emit Markdown for PR descriptions and review checklists.
- [x] Add npm script wrappers.
  - [x] `npm run i18n:extract`
  - [x] `npm run i18n:check`
  - [x] `npm run i18n:sync`
  - [ ] `npm run i18n:report`
- [ ] Add CI integration for the checker after the initial migration stabilizes.
  - [ ] Run `i18n:check` on pull requests.
  - [ ] Keep `i18n:extract` and `i18n:report` as manual contributor tools unless maintainers want CI artifacts.
- [ ] Add a script to scan frontend files for hardcoded CJK UI strings.
- [ ] Add a script to scan backend user-facing modules for hardcoded CJK messages.
- [ ] Allowlist intentional Chinese NLP data and prompt files.
- [ ] Add CI checks for missing translation keys.
- [ ] Add CI checks for unused translation keys.
- [ ] Add TypeScript checks for translation key safety if the chosen approach supports it.
- [ ] Add unit tests for locale switching.
- [ ] Add integration tests for REST locale headers.
- [ ] Add WebSocket locale tests for analysis progress messages.
- [ ] Add export snapshot tests per locale.

## Phase 10: Migration And PR Strategy

- [ ] Keep each PR reviewable and focused.
- [ ] Suggested PR 1:
  - [x] Frontend i18n foundation.
  - [x] `zh-CN` and `en` locale files.
  - [x] App shell and navigation migration.
  - [x] Documentation updates.
- [ ] Suggested PR 2:
  - [x] Bookshelf web/desktop landing migration.
  - [x] Upload/import full workflow migration.
  - [ ] Settings full migration.
  - [x] Language selector.
  - [x] `vi` locale pack and flag-backed language choices.
  - [x] Initial `i18n:extract`, `i18n:check`, and `i18n:sync` tooling.
- [ ] Suggested PR 3:
  - [ ] Reading, analysis, visualization, chat, export page migration.
    - [x] Chat page and floating chat panel.
    - [x] Export page.
    - [x] Entity card drawer shell and item/location/organization cards.
    - [x] Person card and entity scene snippets.
    - [x] Shared onboarding/setup components.
    - [x] Novel overview card, regex template selector, scene panel, and setup guide.
    - [x] Theme toggle and text preview panel.
    - [x] Welcome banner and shared visualization panels.
    - [x] Analysis workflow page and conflict-detection page.
  - [x] Series Bible export labels.
  - [ ] Remaining domain label maps.
- [ ] Suggested PR 4:
  - [ ] Backend locale foundation.
  - [ ] API and WebSocket localized messages.
- [ ] Suggested PR 5:
  - [ ] Export document localization.
  - [ ] Backend tests and snapshots.
- [ ] Suggested PR 6:
  - [ ] Hardcoded string scanning in CI.
  - [ ] Cleanup and documentation.

## Acceptance Checklist

- [x] The app can switch locale without reload where practical.
- [x] Missing translations fall back predictably.
- [x] UI language does not change uploaded novel content.
- [x] UI language does not change extracted entity IDs, names, or API enum values.
- [x] Web, desktop, and demo builds use the same frontend locale layer.
- [ ] REST APIs and WebSockets can return localized user-facing messages.
- [ ] Exported documents can be generated in the selected UI/export locale.
- [ ] Tests cover frontend fallback, backend fallback, and export localization.
- [x] Documentation explains the difference between UI language, source language, and AI output language.
- [ ] Contributors can add a new locale by adding locale files and passing checks.
- [x] Local tooling can extract candidate strings, check key coverage, and sync locale files.
- [ ] Local tooling can report translation progress.
