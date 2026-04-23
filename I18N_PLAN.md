# Internationalization Integration Plan

This document tracks a phased plan for adding first-class internationalization
(i18n) across the full AI Reader V2 application.

The goal is to support multiple interface languages without changing analysis
quality, data models, or Chinese-language NLP assumptions unexpectedly. Chinese
remains the default UI locale until the project maintainers decide otherwise.

Longer term, the project should also support novels whose source language is not
Chinese. That work is tracked separately from UI translation because it affects
chapter splitting, prompts, NLP heuristics, entity normalization, tests, and data
contracts.

## Goals

- [ ] Add a single locale model shared by frontend, backend, desktop, and demo builds.
- [x] Keep `zh-CN` as the default and source locale during the first implementation phase.
- [x] Add locale packs without requiring repeated edits to feature components.
- [x] Separate UI text translation from AI-generated content and source novel content.
- [ ] Make backend user-facing messages locale-aware where they are shown directly in the UI.
- [ ] Keep extraction prompts, Chinese NLP rules, and analysis heuristics stable unless a locale-specific behavior is explicitly designed.
- [ ] Refactor analysis so source novel language is explicit and not hardcoded to Chinese assumptions.
- [ ] Support Vietnamese source novels through language-specific import, prompt, and heuristic adapters.
- [ ] Add tooling that prevents new hardcoded user-visible strings from spreading.

## Non-Goals

- [ ] Do not translate uploaded novels or demo novel content automatically.
- [ ] Do not translate extracted entity names, source quotes, or AI-generated analysis results by default.
- [ ] Do not rewrite the extraction pipeline for non-Chinese novels as part of basic UI localization.
- [ ] Do not replace Chinese NLP resources such as jieba dictionaries as part of basic UI localization.
- [ ] Do not silently translate source text, evidence, extracted names, or generated analysis output just because the UI locale changed.

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

## Language Dimension Architecture

- [ ] Separate the three language dimensions explicitly:
  - [ ] `ui_locale`: language of labels, navigation, dialogs, errors, and settings.
  - [ ] `source_language`: language of the uploaded novel or demo corpus.
  - [ ] `analysis_output_language`: language requested for generated summaries and optional prose.
- [ ] Keep `ui_locale` independent from `source_language`.
  - [ ] Switching UI language must not re-run analysis or mutate extracted data.
  - [ ] Importing a Vietnamese novel must not require switching the UI to Vietnamese.
- [ ] Store `source_language` in novel/project metadata.
  - [x] Add an explicit user override in import/settings.
  - [ ] Allow `auto` detection only as a convenience, not as the only source of truth.
  - [ ] Persist the detected and user-confirmed language separately if both are available.
- [ ] Keep machine-readable API values language-neutral.
  - [ ] Use stable enum IDs for entity types, relation categories, event types, tones, and location tiers.
  - [ ] Translate display labels at the UI/export layer.
  - [ ] Map language-specific synonyms to stable enum IDs during extraction.
- [ ] Define fallback behavior by language dimension.
  - [ ] Unknown `ui_locale` falls back to `zh-CN`.
  - [ ] Unknown `source_language` falls back to the current Chinese-oriented pipeline with a visible warning.
  - [ ] Unknown `analysis_output_language` defaults to source language unless the user chooses otherwise.

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
  - [x] `frontend/src/pages/ReadingPage.tsx`
  - [x] `frontend/src/pages/ScreenplayPage.tsx`
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
  - [x] `frontend/src/pages/GraphPage.tsx`
  - [x] `frontend/src/pages/MapPage.tsx`
  - [x] `frontend/src/pages/TimelinePage.tsx`
  - [x] `frontend/src/pages/FactionsPage.tsx`
  - [x] `frontend/src/pages/EncyclopediaPage.tsx`
  - [x] `frontend/src/pages/ConflictsPage.tsx`
  - [x] Shared visualization layout, geography panel, map quality panel, and GeoMap controls.
- [ ] Migrate chat and export pages.
  - [x] `frontend/src/pages/ChatPage.tsx`
  - [x] `frontend/src/pages/ExportPage.tsx`
  - [x] `frontend/src/components/chat/FloatingChatPanel.tsx`
  - [x] `frontend/src/lib/systemFaq.ts`
- [ ] Migrate demo pages.
  - [x] `frontend/src/app/DemoLayout.tsx`
  - [x] `frontend/src/pages/demo/DemoEncyclopediaPage.tsx`
  - [x] `frontend/src/pages/demo/DemoTimelinePage.tsx`
  - [x] `frontend/src/pages/demo/DemoGraphPage.tsx`
  - [x] `frontend/src/pages/demo/DemoMapPage.tsx`
  - [x] `frontend/src/pages/demo/DemoFactionsPage.tsx`
  - [x] `frontend/src/pages/demo/DemoExportPage.tsx` shell controls and format chooser.
  - [ ] `frontend/src/pages/demo/DemoReadingPage.tsx`
  - [ ] Review `DemoExportPage.tsx` preview/sample document strings separately; keep source-language sample content unless the project decides demo exports should localize mock document contents.
- [ ] Avoid translating source novel text, entity names, and quoted evidence.
- [x] Migrate frontend API-client fallback UI messages.
  - [x] Upload progress fallback errors.
  - [x] Hierarchy/spatial streaming fallback errors.
  - [x] Default chat title and Series Bible fallback filenames.

## Phase 4: Frontend Domain Labels And Formatting

- [ ] Centralize domain label maps.
  - [ ] Entity types.
  - [x] Encyclopedia concept categories.
  - [x] Relation categories.
  - [ ] Location tiers.
  - [x] Scene tones.
  - [x] Scene event types.
  - [x] Entity scene roles.
  - [x] Organization/faction labels.
  - [x] Conflict severity labels.
  - [x] Series Bible export module and template labels.
  - [x] Item, location, and organization card section/stat labels.
  - [x] Person card relation categories, section labels, scene labels, and stat labels.
  - [x] Shared onboarding/setup labels for contextual guide, discovery bar, tour bubble, cost preview, and inline LLM setup.
  - [x] Novel overview metadata, regex template labels, scene panel filters, and setup guide labels.
  - [x] Theme toggle and text preview controls.
  - [x] Shared spatial relation type/value labels.
- [ ] Keep API enum values stable and translate only display labels.
  - [x] Add frontend domain-label helpers that prefer stable IDs and fall back to legacy raw labels.
  - [x] Render scene panel, screenplay, timeline/storyline, graph, map, factions, and entity cards through stable IDs where backend IDs are available.
  - [x] Expose entity scene `role_id` and timeline `summary_template_id`/`summary_args` so frontend can localize scene badges and derived relation/item/org summaries without depending on backend-built Chinese prose.
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
  - [x] Current map rebuild v2 and spatial-completion SSE progress no longer emit Chinese-only prose in the active UI path; the legacy `world_structure` v1 rebuild route still needs the same treatment.
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

## Phase 8A: Multilingual Source Analysis Refactor

- [ ] Add an explicit source-language model.
  - [x] Define supported source language IDs: `zh-CN`, `vi`, `en`, and `auto`.
  - [x] Add `source_language` to novel/project metadata.
  - [x] Add import-time source language selection in the UI.
  - [x] Add backend request/response fields for source language during upload, preview, re-split, and confirm import.
  - [x] Add migration/defaulting for existing novels as `zh-CN` unless metadata says otherwise.
  - [x] Pass source language into analysis start/resume and downstream extraction services.
- [ ] Refactor chapter splitting into language-aware strategies.
  - [ ] Keep existing Chinese chapter templates stable.
  - [x] Add Vietnamese templates such as `Chương 1`, `Chương I`, `Hồi 1`, `Phần 1`, and common title variants.
  - [ ] Add English templates such as `Chapter 1`, `Part 1`, and `Book 1`.
  - [ ] Move regex presets into data/config rather than hardcoding one language in import code.
  - [x] Add split diagnosis metadata that includes the selected source language and matched template.
- [ ] Refactor prompt construction into language-aware prompt adapters.
  - [x] Keep schema instructions and JSON output contracts language-neutral.
  - [x] Keep evidence quotes and source names in the original source language.
  - [x] Add prompt templates or prompt fragments for Chinese, Vietnamese, and English source text.
  - [x] Add source-language-specific guidance for names, aliases, honorifics, locations, organizations, and chapter references.
  - [x] Add forced segmented recovery after repeated single-pass JSON parse failures so long Vietnamese/Chinese chapters can still complete analysis.
  - [ ] Add a policy for generated summaries: source-language by default, optional explicit output language later.
- [ ] Refactor NLP and heuristic code behind source-language adapters.
  - [ ] Isolate Chinese-specific resources such as jieba dictionaries, Chinese keyword arrays, and Chinese location suffix matching.
  - [x] Guard Chinese jieba pre-scan and Chinese numeric-prefix name correction behind `source_language=zh-CN`/`auto`.
  - [x] Add Vietnamese normalization that preserves diacritics and handles common casing/punctuation in the pre-scan adapter.
  - [x] Add Vietnamese name guidance for multi-word names, titles, dialogue speakers, and naming phrases.
  - [x] Add Vietnamese chapter-reference parsing for `chương`, `hồi`, `phần`, and numeric/roman variants.
  - [x] Add language-specific stopword and keyword lists only through adapter modules for the new Vietnamese pre-scan path.
- [ ] Refactor domain classification away from raw Chinese labels.
  - [ ] Keep stable internal IDs for event types, scene tones, relation categories, location kinds, and organization types.
    - [x] Add backend stable IDs for core chapter-fact event types, relation types, location kinds, organization types, item types, and item/org actions.
    - [x] Add stable IDs for scene tones, scene event types, and scene time-of-day labels in both LLM and rule-based scene outputs.
  - [ ] Map Chinese extraction labels to stable IDs.
    - [x] Normalize existing Chinese chapter-fact labels to canonical labels and `*_id` fields before persistence.
    - [x] Normalize existing Chinese scene tone/event labels to canonical labels and scene `*_id` fields when saving and reading scenes.
  - [ ] Map Vietnamese extraction labels to stable IDs.
    - [x] Normalize common Vietnamese relation, event, location, organization, item, and action labels in `FactValidator`.
    - [x] Normalize common Vietnamese scene tone/event/time labels through the scene label adapter.
  - [ ] Ensure frontend visualizations render stable IDs through i18n labels instead of raw extraction labels.
    - [x] Expose backend `relation_type_id`, `type_id`, `org_type_id`, and timeline `type_id` in graph, map, factions, and timeline payloads without removing legacy labels.
    - [x] Render scene panel, screenplay mode, entity scene snippets, timeline list/storyline, graph relation labels, map location types, faction org labels, and entity cards from stable IDs with raw-label fallback.
    - [x] Normalize encyclopedia concept subcategory IDs in backend stats/list/detail payloads and render encyclopedia sidebar/detail labels from stable IDs instead of mixed raw Chinese/Vietnamese/English category strings.
  - [ ] Add fallback display for unknown labels without corrupting stored data.
    - [x] Preserve unknown labels while assigning namespaced fallback IDs such as `relation.*`, `location.*`, `org.*`, and `item.*`.
    - [x] Preserve unknown frontend labels by falling back to the raw backend label when no i18n key exists for a stable ID.
- [ ] Refactor entity extraction and normalization for multilingual novels.
  - [ ] Preserve original entity names exactly as extracted from source text.
  - [ ] Store aliases separately from canonical names and avoid language-specific merging unless confidence is high.
  - [ ] Avoid assuming Chinese-style two or three character names.
  - [x] Guard alias safety and nickname/title heuristics so Chinese name-length rules do not soft-block Vietnamese names.
  - [x] Guard `FactValidator` person-name and alias-length heuristics so Vietnamese multi-word names and aliases survive validation.
  - [x] Add non-CJK generic person filters for Vietnamese/English title-like references such as `vị tướng`, `người lính`, and `soldier`.
  - [x] Avoid Chinese pre-scan assumptions for Vietnamese source novels by using a Vietnamese adapter instead of jieba/CJK n-grams.
  - [x] Merge same-identity non-CJK case variants across aggregated entity lists, encyclopedia entries, entity profile resolution, and chapter highlight canonical links.
  - [x] Reuse aggregated type voting in encyclopedia stats and entries so obvious locations stop leaking into the person bucket.
  - [ ] Add confidence and provenance where alias merging is language-sensitive.
  - [ ] Add manual correction paths for aliases and canonical entity names.
- [ ] Refactor map/location processing for multilingual inputs.
  - [ ] Move Chinese location suffix fallback rules into the Chinese adapter.
  - [x] Add Vietnamese location keyword mapping for terms such as `núi`, `sông`, `suối`, `hồ`, `làng`, `thành`, `kinh đô`, `chùa`, `đền`, and `phủ` in the pre-scan adapter.
  - [x] Guard `FactValidator` Chinese location length and contains-suffix rank rules so they do not reject Vietnamese locations with diacritics.
  - [x] Add non-CJK generic location filters for Vietnamese/English relative references such as `bên sông`, `trong thành`, and `riverbank`.
  - [x] Drop standalone non-CJK head nouns such as `thôn`, `làng`, `đường`, and `núi` during location validation and referenced-location auto-add so Vietnamese noun heads do not become phantom locations.
  - [x] Add Vietnamese prefix-based fallback type inference for referenced locations such as `sông Hồng`, `bến Chương Dương`, `chùa Phổ Minh`, and `thành Thăng Long`.
  - [x] Guard `WorldStructureAgent._get_suffix_rank` so Chinese suffix-rank morphology does not apply to non-CJK names.
  - [x] Add Vietnamese prefix-based tier and icon fallback for world-structure map locations.
  - [ ] Keep map icon and terrain IDs language-neutral.
  - [x] Test Vietnamese location names with diacritics in map API labels, hierarchy metadata, trajectories, spatial constraints, and geography context.
  - [ ] Test that Vietnamese location names with diacritics render correctly in map labels and search.
- [ ] Add multilingual analysis fixtures and regression tests.
  - [ ] Keep a small Chinese fixture to prove no regression.
  - [x] Add a small synthetic Vietnamese splitter fixture.
  - [x] Add a small synthetic Vietnamese heuristic fixture for pre-scan names, organizations, and locations.
  - [x] Add a small Vietnamese public-domain or synthetic full analysis fixture with 5-10 chapters.
  - [x] Add expected outputs for chapter splitting, key entities, aliases, relations, locations, and timeline events.
    - [x] Chapter splitting, key entities, relations, locations, organizations, factions, and timeline events.
    - [x] Alias-specific expected outputs and merge/conflict regression cases.
    - [x] Map-specific expected outputs for location metadata, hierarchy, trajectories, spatial constraints, and context strings.
  - [ ] Add tests proving UI locale changes do not alter analysis output.
  - [x] Add tests proving `source_language=vi` uses Vietnamese chapter split adapters.
  - [x] Add tests proving `source_language=vi` uses Vietnamese prompt adapters.
  - [x] Add tests proving `source_language=vi` guards Chinese heuristic adapters.
- [ ] Add user-facing safeguards for non-Chinese analysis.
  - [x] Show source-language selection during import.
  - [ ] Warn when `auto` detection is low-confidence.
  - [ ] Show current source language in project settings.
  - [ ] Make re-analysis explicit when changing source language after import.
  - [ ] Document that multilingual source support is separate from UI localization.
- [ ] Add contributor documentation for new source-language adapters.
  - [ ] Required splitter presets.
  - [ ] Required prompt fragments.
  - [ ] Required synonym maps to stable domain IDs.
  - [ ] Required fixture and regression coverage.
  - [ ] Known unsupported language-specific assumptions.

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
  - [x] Current map rebuild/spatial-completion modules were reduced first by removing Chinese-only SSE progress in the active v2 UI path.
- [ ] Allowlist intentional Chinese NLP data and prompt files.
- [ ] Add source-language adapter checks.
  - [ ] Report Chinese-specific heuristics that are still used without a `source_language` guard.
  - [ ] Report raw Chinese domain labels crossing API/UI boundaries where stable IDs should be used.
  - [ ] Validate that each supported source language has splitter presets, prompt fragments, synonym maps, and fixtures.
  - [ ] Validate Vietnamese fixture coverage for import, analysis, graph, map, timeline, and encyclopedia outputs.
- [ ] Add CI checks for missing translation keys.
- [ ] Add CI checks for unused translation keys.
- [ ] Add TypeScript checks for translation key safety if the chosen approach supports it.
- [ ] Add unit tests for locale switching.
- [ ] Add integration tests for REST locale headers.
- [ ] Add WebSocket locale tests for analysis progress messages.
- [ ] Add export snapshot tests per locale.
- [ ] Add multilingual source regression tests.
  - [ ] `source_language=zh-CN` fixture remains compatible with existing behavior.
  - [x] `source_language=vi` fixture splits chapters and preserves core entities through graph, map, timeline, encyclopedia, and factions aggregation.
  - [x] `source_language=vi` fixture validates full LLM extraction output against expected entities.
  - [ ] UI locale changes do not change fixture outputs.

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
  - [x] Settings full migration.
    - [x] `SettingsPage` AI engine, benchmark history, budget, analysis records, data import, backup, privacy, footer, and usage-event labels now render through locale keys.
    - [x] `note.txt` audit no longer shows a large remaining hardcoded cluster in `SettingsPage`.
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
    - [x] Reading page, screenplay page, and factions page.
    - [x] Timeline page and relationship graph page.
    - [x] Timeline/storyline derived summaries and entity scene role badges now render through template/ID helpers instead of backend-composed Chinese strings.
    - [x] Encyclopedia page and map page.
    - [x] Demo layout and demo visualization pages except DemoReading and DemoExport preview sample content.
  - [x] Series Bible export labels.
  - [ ] Remaining domain label maps.
- [ ] Suggested PR 4:
  - [x] Source-language metadata and import UI.
  - [x] Language-aware chapter splitting.
  - [ ] Chinese adapter extraction of current hardcoded assumptions.
  - [ ] Vietnamese splitter/prompt/heuristic adapter prototype.
    - [x] Vietnamese splitter and prompt-fragment prototype.
    - [x] Vietnamese pre-scan heuristic adapter prototype.
  - [ ] Small Vietnamese fixture and regression tests.
    - [x] Synthetic Vietnamese splitter and pre-scan heuristic tests.
    - [x] Full synthetic analysis fixture with expected split, graph, map, timeline, encyclopedia, and factions outputs.
    - [x] Alias-specific expected outputs and merge/conflict regression cases.
    - [x] Map-specific expected outputs.
- [ ] Suggested PR 5:
  - [ ] Backend locale foundation.
  - [ ] API and WebSocket localized messages.
- [ ] Suggested PR 6:
  - [ ] Export document localization.
  - [ ] Backend tests and snapshots.
- [ ] Suggested PR 7:
  - [ ] Hardcoded string scanning in CI.
  - [ ] Cleanup and documentation.

## Acceptance Checklist

- [x] The app can switch locale without reload where practical.
- [x] Missing translations fall back predictably.
- [x] UI language does not change uploaded novel content.
- [x] UI language does not change extracted entity IDs, names, or API enum values.
- [x] Source novel language is stored explicitly per novel/project.
- [x] A Vietnamese source novel can be imported with a Vietnamese chapter splitter.
- [ ] Vietnamese source analysis preserves original Vietnamese names, evidence, and diacritics.
- [ ] Chinese-specific NLP and heuristic assumptions are isolated behind `source_language=zh-CN` adapters.
- [ ] Stable domain IDs are used internally instead of raw Chinese labels crossing layers.
- [x] Web, desktop, and demo builds use the same frontend locale layer.
- [ ] REST APIs and WebSockets can return localized user-facing messages.
- [ ] Exported documents can be generated in the selected UI/export locale.
- [ ] Tests cover frontend fallback, backend fallback, and export localization.
- [x] Documentation explains the difference between UI language, source language, and AI output language.
- [ ] Contributors can add a new locale by adding locale files and passing checks.
- [x] Local tooling can extract candidate strings, check key coverage, and sync locale files.
- [ ] Local tooling can report translation progress.
