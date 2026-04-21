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
  - [ ] `frontend/src/desktop/DragDropOverlay.tsx`
  - [ ] `frontend/src/desktop/SecurityGuide.tsx`
- [ ] Migrate global shared components.
  - [ ] Theme toggle labels.
  - [ ] Welcome banner.
  - [ ] Feature discovery and guided tour text.
  - [ ] Cost preview dialog.
- [ ] Verify both web and desktop shell builds.
  - [x] Web build with `npm run build`.
  - [ ] Desktop build.

## Phase 3: Frontend Feature Page Migration

- [ ] Migrate reading workflow.
  - [ ] `frontend/src/pages/ReadingPage.tsx`
  - [ ] `frontend/src/pages/ScreenplayPage.tsx`
  - [ ] `frontend/src/components/shared/ScenePanel.tsx`
  - [ ] `frontend/src/components/shared/TextPreviewPanel.tsx`
- [ ] Migrate upload/import workflow.
  - [ ] `frontend/src/components/shared/UploadDialog.tsx`
  - [ ] Regex template labels.
  - [ ] File validation messages.
  - [ ] Split diagnosis messages.
  - [ ] Duplicate import prompts.
- [ ] Migrate analysis workflow.
  - [ ] `frontend/src/pages/AnalysisPage.tsx`
  - [ ] Progress/status labels.
  - [ ] Pause/resume/retry labels.
  - [ ] Budget and model setup prompts.
- [ ] Migrate visualization pages.
  - [ ] `frontend/src/pages/GraphPage.tsx`
  - [ ] `frontend/src/pages/MapPage.tsx`
  - [ ] `frontend/src/pages/TimelinePage.tsx`
  - [ ] `frontend/src/pages/FactionsPage.tsx`
  - [ ] `frontend/src/pages/EncyclopediaPage.tsx`
  - [ ] `frontend/src/pages/ConflictsPage.tsx`
- [ ] Migrate chat and export pages.
  - [ ] `frontend/src/pages/ChatPage.tsx`
  - [ ] `frontend/src/pages/ExportPage.tsx`
- [ ] Migrate demo pages.
  - [ ] `frontend/src/pages/demo/*`
- [ ] Avoid translating source novel text, entity names, and quoted evidence.

## Phase 4: Frontend Domain Labels And Formatting

- [ ] Centralize domain label maps.
  - [ ] Entity types.
  - [ ] Relation categories.
  - [ ] Location tiers.
  - [ ] Scene tones.
  - [ ] Scene event types.
  - [ ] Organization/faction labels.
  - [ ] Conflict severity labels.
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

- [ ] Add a small local i18n toolkit for extraction, checking, and synchronization.
  - [ ] Place scripts under `scripts/i18n/` or `frontend/scripts/i18n/` depending on maintainer preference.
  - [ ] Keep tools dependency-light and runnable from npm scripts or Python/Node directly.
  - [ ] Document tool usage in this plan or a dedicated i18n contributor guide.
- [ ] Add an `extract` tool to discover candidate user-visible strings.
  - [ ] Scan frontend `.ts` and `.tsx` files with a TypeScript-aware parser when practical.
  - [ ] Detect JSX text nodes, string literals in common UI props, toast messages, dialog text, placeholders, titles, and aria labels.
  - [ ] Scan backend `.py` files for likely user-facing error, progress, export, and validation messages.
  - [ ] Output a reviewable report with file path, line number, source string, suggested key, and category.
  - [ ] Avoid auto-editing source files in the first version; generate candidates for manual review.
- [ ] Add a `check` tool to prevent missing or stale translation keys.
  - [ ] Fail when a translation key used in source is missing from any required locale file.
  - [ ] Fail or warn when locale files contain unused keys.
  - [ ] Detect duplicate keys and inconsistent interpolation variables across locales.
  - [ ] Detect hardcoded CJK UI strings outside allowlisted files and comments.
  - [ ] Support an allowlist for Chinese NLP data, prompts, fixtures, demo content, and tests.
- [ ] Add a `sync` tool to keep locale files aligned.
  - [ ] Add missing keys from the source locale into target locale files with empty values or source-text placeholders.
  - [ ] Preserve stable key ordering for clean diffs.
  - [ ] Preserve translator comments or metadata where possible.
  - [ ] Optionally remove or mark stale keys after review.
  - [ ] Generate a summary of added, removed, stale, and untranslated keys.
- [ ] Add a `report` tool for progress tracking.
  - [ ] Count translated, untranslated, stale, and missing keys by locale.
  - [ ] Count remaining hardcoded strings by module.
  - [ ] Emit Markdown for PR descriptions and review checklists.
- [ ] Add npm script wrappers.
  - [ ] `npm run i18n:extract`
  - [ ] `npm run i18n:check`
  - [ ] `npm run i18n:sync`
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
  - [ ] Upload/import full workflow migration.
  - [ ] Settings full migration.
  - [x] Language selector.
  - [x] `vi` locale pack and flag-backed language choices.
  - [ ] Initial `i18n:extract`, `i18n:check`, and `i18n:sync` tooling.
- [ ] Suggested PR 3:
  - [ ] Reading, analysis, visualization, chat, export page migration.
  - [ ] Domain label maps.
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
- [ ] Local tooling can extract candidate strings, check key coverage, sync locale files, and report translation progress.
