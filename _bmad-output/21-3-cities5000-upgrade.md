---
story_key: 21-3-cities5000-upgrade
epic: 21
story: 3
title: "城市数据集升级 cities15000 → cities5000"
status: done
date: 2026-02-20
---

# Story 21.3: 城市数据集升级 cities15000 → cities5000

## Story

As a 用户,
I want GeoResolver 使用更全面的城市数据集,
So that 人口在 5000-15000 之间的中小城市也能被匹配到。

## Acceptance Criteria

**AC1:** 修改 `DATASET_WORLD` 的 `url` 和 `zip_member` 字段从 cities15000 → cities5000
**AC2:** 如果用户已有旧的 `cities15000.txt` 缓存文件，首次使用时自动下载 `cities5000.txt`
**AC3:** 现有中国小说（西游记）的 CN 数据集路径不受影响
**AC4:** `zh_geonames.tsv` 预处理脚本（Story 21.1）已使用 cities5000 作为输入源
**AC5:** 更新文档中的引用（CLAUDE.md, 模块 docstring）

## Tasks/Subtasks

- [x] T1: 修改 `DATASET_WORLD` 配置（url + zip_member + description）
- [x] T2: 更新模块 docstring 和相关注释中的 cities15000 引用
- [x] T3: 更新 CLAUDE.md 中的数据集描述
- [x] T4: 验证自动下载逻辑（新文件名不同，会触发重新下载）
- [x] T5: 验证 import 正常

## Dev Notes

**自动迁移：** `_ensure_data()` 通过 `_tsv_path()` 检测文件是否存在。由于文件名从
`cities15000.txt` 变为 `cities5000.txt`，即使旧文件存在，新文件不存在也会触发自动下载。
无需额外迁移逻辑。

**对 zh_geonames.tsv 的影响：** Story 21.1 的预处理脚本已使用 cities5000.txt 作为输入，
因此 zh_geonames.tsv 中的 geonameid JOIN 已覆盖 pop >= 5000 的城市。

## Dev Agent Record

### Completion Notes
✅ Story 21.3 完成。改动极小（配置 + 注释 + 文档）。

## File List

- `backend/src/services/geo_resolver.py` — 修改 DATASET_WORLD 配置 + 注释
- `CLAUDE.md` — 更新 GeoResolver 数据集描述，新增 zh alias index 文档

## Change Log

- 2026-02-20: Story 21.3 完成。cities15000 → cities5000 升级。
