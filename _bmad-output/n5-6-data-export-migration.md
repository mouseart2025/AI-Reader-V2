# Story N5.6: 数据导出与迁移

Status: review

## Story

As a 用户,
I want 一键导出全部数据为可迁移的格式,
So that 我可以在新设备上恢复所有小说和分析结果。

## Acceptance Criteria

1. **AC-1**: 生成包含所有小说、章节、分析结果、用户配置的 JSON 数据包
2. **AC-2**: 数据包以 .zip 格式压缩，文件名含导出日期
3. **AC-3**: 提供"导入数据"入口，选择 .zip 文件后恢复全部数据
4. **AC-4**: 导入时检测冲突（已存在的小说）并提示"覆盖/跳过"
5. **AC-5**: 导入/导出不包含 API Key

## Tasks / Subtasks

- [x] Task 1: 全量备份/恢复服务 (AC: #1, #2, #5)
  - [x] 1.1 `backup_service.py` — export_all() 导出所有小说为 ZIP (manifest.json + novels/*.json)
  - [x] 1.2 preview_backup_import() — 预览 ZIP 内容 + 冲突检测
  - [x] 1.3 import_all() — 按 conflict_mode (skip/overwrite) 逐本导入

- [x] Task 2: API 端点 (AC: #3, #4)
  - [x] 2.1 `backup.py` — GET /backup/export (ZIP 下载) + POST /backup/import/preview + POST /backup/import/confirm

- [x] Task 3: 前端 UI (AC: #3, #4)
  - [x] 3.1 SettingsPage.tsx — "全量备份"段落: 导出按钮 + 恢复文件选择 + 预览 (小说列表+冲突标记) + "跳过/覆盖"导入 + 结果显示

- [x] Task 4: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- ZIP 结构：manifest.json (元数据) + novels/<id>.json (复用 export_service 格式)
- 文件名：ai-reader-v2-backup-YYYYMMDD.zip
- API Key 不包含在备份中（export_novel 不导出 infra/config 设置）
- 冲突检测基于小说标题匹配
- 导入支持两种冲突模式：skip（跳过已存在）/ overwrite（覆盖已存在）
- 预览显示每本小说的冲突状态 + 章节数

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/backup_service.py` | 新建: 全量备份/恢复服务 |
| `backend/src/api/routes/backup.py` | 新建: 备份 API 端点 |
| `backend/src/api/main.py` | 注册 backup router |
| `frontend/src/api/types.ts` | 添加 BackupPreview, BackupImportResult 类型 |
| `frontend/src/api/client.ts` | 添加 backupExportUrl, previewBackupImport, confirmBackupImport |
| `frontend/src/pages/SettingsPage.tsx` | 添加"全量备份"UI 段落 |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
