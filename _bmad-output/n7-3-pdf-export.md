# Story N7.3: PDF 导出

Status: review

## Story

As a 用户,
I want 导出专业排版的 PDF 文档,
So that 我可以分享只读版本的设定文档。

## Acceptance Criteria

1. **AC-1**: 生成含目录、页码、页眉的 PDF 文件
2. **AC-2**: 排版美观（标题层级、表格、列表格式正确）
3. **AC-3**: 使用 reportlab 生成
4. **AC-4**: 500 章导出 < 90 秒

## Tasks / Subtasks

- [x] Task 1: 添加 reportlab 依赖 — reportlab 4.4.10
- [x] Task 2: PDF 渲染器 (AC: #1~#3)
  - [x] 2.1 `backend/src/services/pdf_renderer.py` — Platypus 布局引擎
- [x] Task 3: API 端点 + 前端集成 (AC: #1)
  - [x] 3.1 series_bible 路由扩展支持 format=pdf
  - [x] 3.2 ExportPage 启用 PDF 格式卡片
- [x] Task 4: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- `pdf_renderer.py`: reportlab Platypus 渲染器，A4 页面，含页眉（标题）+ 页码
- CJK 字体自动检测: STSong-Light CID → macOS 系统字体 → Windows 字体 → Linux 字体
- 关系表: Table + TableStyle 蓝色表头 + 斑马行
- 所有文本用 `_esc()` 转义 XML 特殊字符
- 4 种导出格式全部可用: Markdown / Word / Excel / PDF
- 前端: 所有格式卡片启用，模块选择器通用

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/pdf_renderer.py` | 新增 — reportlab PDF 渲染器 |
| `backend/src/api/routes/series_bible.py` | format=pdf 分支 |
| `frontend/src/pages/ExportPage.tsx` | PDF 格式启用 + 导出逻辑 |
| `frontend/src/api/client.ts` | 默认文件名支持 .pdf |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
