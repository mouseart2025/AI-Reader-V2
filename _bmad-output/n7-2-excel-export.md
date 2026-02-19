# Story N7.2: Excel 导出

Status: review

## Story

As a IP 改编编辑/游戏策划,
I want 将分析结果导出为 Excel 表格,
So that 我可以用表格管理角色和设定数据。

## Acceptance Criteria

1. **AC-1**: 生成 .xlsx 文件，包含多个 Sheet：角色表、关系表、地点表、物品表、组织表、时间线
2. **AC-2**: 角色表列：名称、别称、首次登场、出场章数、能力、关系
3. **AC-3**: 使用 openpyxl 生成
4. **AC-4**: 支持模块选择（与 Markdown/Word 共享模块体系）

## Tasks / Subtasks

- [x] Task 1: 添加 openpyxl 依赖 — openpyxl 3.1.5
- [x] Task 2: Excel 渲染器 (AC: #1~#3)
  - [x] 2.1 `backend/src/services/xlsx_renderer.py` — 6 个 Sheet 对应 6 个模块
- [x] Task 3: API 端点 + 前端集成 (AC: #4)
  - [x] 3.1 series_bible 路由扩展支持 format=xlsx
  - [x] 3.2 ExportPage 启用 Excel 格式卡片
- [x] Task 4: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- `xlsx_renderer.py`: 6 Sheet 工作簿 — 角色表/关系表/地点表/物品表/组织表/时间线
- 角色表 7 列: 名称/别称/首次登场/出场章数/能力/关系/主要经历
- 关系表按权重排序 top 50, 地点含上下级/描述/到访者
- 蓝色表头 + 冻结首行 + 自动列宽(10~50 字符)
- API: format=xlsx 返回 .xlsx MIME
- 前端: Excel 格式卡片已启用，模块选择共享

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/xlsx_renderer.py` | 新增 — Excel 渲染器 |
| `backend/src/api/routes/series_bible.py` | format=xlsx 分支 |
| `frontend/src/pages/ExportPage.tsx` | Excel 格式启用 + 导出逻辑 |
| `frontend/src/api/client.ts` | 默认文件名支持 .xlsx |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
