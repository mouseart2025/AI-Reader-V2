# Story N7.1: Word 导出

Status: review

## Story

As a 传统出版作者/编辑,
I want 将 Series Bible 导出为 Word 文档,
So that 我可以用编辑的标准工作流审阅。

## Acceptance Criteria

1. **AC-1**: 生成 .docx 文件，包含目录、页码、页眉
2. **AC-2**: 人物档案、关系表、地点层级等内容与 Markdown 版一致
3. **AC-3**: 使用 python-docx 生成
4. **AC-4**: 500 章导出 < 60 秒

## Tasks / Subtasks

- [x] Task 1: 添加 python-docx 依赖
- [x] Task 2: Word 渲染器 (AC: #1~#3)
  - [x] 2.1 `backend/src/services/docx_renderer.py` — 使用 SeriesBibleData 生成 .docx
- [x] Task 3: API 端点 + 前端集成 (AC: #1)
  - [x] 3.1 series_bible 路由扩展支持 format=docx
  - [x] 3.2 ExportPage 启用 Word 格式卡片
- [x] Task 4: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- `docx_renderer.py`: 完整 Word 渲染器，含标题页、TOC 域（Word 中右键更新）、页眉（小说标题）、页码、6 个模块
- 人物档案: 别称/外貌/能力/关系（含演变链）/经历/持有物品/出场统计
- 关系网络: Table Grid 表格，按权重排序 top 30
- 地点/物品/组织/时间线: 与 Markdown 版内容一致
- API: SeriesBibleRequest 新增 format 字段，format=docx 返回 .docx MIME
- 前端: Word 格式卡片已启用，模块选择共享，导出按钮文案动态切换

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/docx_renderer.py` | 新增 — Word 渲染器 |
| `backend/src/api/routes/series_bible.py` | format=docx 分支 |
| `frontend/src/pages/ExportPage.tsx` | Word 格式启用 + 导出逻辑 |
| `frontend/src/api/types.ts` | SeriesBibleRequest.format 字段 |
| `frontend/src/api/client.ts` | 默认文件名支持 .docx |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
