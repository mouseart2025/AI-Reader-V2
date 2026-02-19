# Story N4.3: 导出中心 UI

Status: review

## Story

As a 用户,
I want 在导航栏有导出入口，进入后选择格式、模块、模板,
So that 导出过程直观便捷。

## Acceptance Criteria

1. **AC-1**: 导航栏新增"导出"入口
2. **AC-2**: 格式选择卡片（Markdown 可用，Word/Excel/PDF 显示"即将推出"）
3. **AC-3**: 选择格式后显示内容模块勾选和模板下拉
4. **AC-4**: 提供"导出"按钮，下载 .md 文件

## Tasks / Subtasks

- [x] Task 1: ExportPage 组件 (AC: #2~#4)
  - [x] 1.1 `ExportPage.tsx` — 4 格式卡片 + 2 模板选择 + 6 模块勾选 + 导出按钮

- [x] Task 2: 路由 + 导航 (AC: #1)
  - [x] 2.1 `router.tsx` — lazy import ExportPage + /export/:novelId route
  - [x] 2.2 `NovelLayout.tsx` — NAV_TABS 加 { key: "export", label: "导出" }

- [x] Task 3: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- ExportPage：格式选择网格（Markdown 可用 + 3 个 disabled），模板选择（通用/网文作者），模块勾选 3x2 grid
- Word/Excel/PDF 卡片显示"即将推出"且 disabled
- 导航栏"导出"按钮在"问答"之后

### Files Changed

| File | Change |
|------|--------|
| `frontend/src/pages/ExportPage.tsx` | 新建: 导出中心页面 |
| `frontend/src/app/router.tsx` | 新增 ExportPage lazy import + route |
| `frontend/src/app/NovelLayout.tsx` | NAV_TABS 加 export |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
