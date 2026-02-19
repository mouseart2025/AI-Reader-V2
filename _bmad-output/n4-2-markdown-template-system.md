# Story N4.2: Markdown 模板系统

Status: review

## Story

As a 网文作者,
I want 选择不同的 Markdown 导出模板,
So that 我可以得到适合自己工作流的格式。

## Acceptance Criteria

1. **AC-1**: 提供"网文作者套件"模板：人物设定卡、势力分布、时间线大纲
2. **AC-2**: 提供"通用模板"：完整世界观文档
3. **AC-3**: 输出为单个 .md 文件，含目录、分级标题
4. **AC-4**: 人物卡片包含：姓名、别称、外貌、关系、能力、经历
5. **AC-5**: 500 章小说完整 Markdown 导出 < 30 秒

## Tasks / Subtasks

- [x] Task 1: 后端模板系统 (AC: #1~#4)
  - [x] 1.1 `series_bible_renderer.py` — TEMPLATES registry + render_markdown(template=) 分发
  - [x] 1.2 "complete" 模板: 6 模块全量, 详细人物 profile（含持有物品）
  - [x] 1.3 "author" 模板: 紧凑人物卡 + 势力分布 + 时间线大纲（仅 high importance）
  - [x] 1.4 `series_bible.py` route — 增加 template 参数 + GET templates 端点

- [x] Task 2: 前端类型更新
  - [x] 2.1 `types.ts` — SeriesBibleRequest.template + SERIES_BIBLE_TEMPLATES 常量

- [x] Task 3: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- TEMPLATES dict: "complete"（通用模板 6 模块全量）, "author"（网文作者套件 3 模块精简）
- 通用模板人物卡：别称 + 外貌 + 能力 + 关系 + 经历 + 持有物品 + 统计
- 网文作者套件人物卡：别称 + 外貌(单条) + 能力(名称) + 关系(紧凑) + 经历(30 字摘要) + 出场
- 时间线大纲模式：仅 high importance 事件，粗体章节号
- 文件名含模板名称：`{title}_{模板名}.md`

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/series_bible_renderer.py` | 重构: TEMPLATES registry + 模板分发 + 独立渲染函数 |
| `backend/src/api/routes/series_bible.py` | template 参数 + GET templates 端点 |
| `frontend/src/api/types.ts` | SeriesBibleRequest.template + SERIES_BIBLE_TEMPLATES |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
