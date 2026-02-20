---
story_key: 21-2-georesolver-zh-alias-layer
epic: 21
story: 2
title: "GeoResolver 中文别名查找层"
status: done
date: 2026-02-20
---

# Story 21.2: GeoResolver 中文别名查找层

## Story

As a 用户,
I want GeoResolver 在解析地名时自动查询中文别名索引,
So that 翻译小说中的中文地名（大陆译法、台湾译法）能自动匹配到正确坐标，无需手工维护。

## Acceptance Criteria

**AC1:** 懒加载 `zh_geonames.tsv` 到内存字典 `_zh_alias_index`
**AC2:** 同名多条目按 population 降序排列（消歧用）
**AC3:** `resolve_names()` 查找优先级：`_SUPPLEMENT_GEO` → `_zh_alias_index` → GeoNames 原始文件匹配
**AC4:** 中文别名查找在 `_NON_GEO_PATTERNS` 过滤之后执行
**AC5:** 消歧逻辑：同名多地取 population 最大者；如有 parent-proximity 信息，优先取距离 parent < 1000km 的
**AC6:** `_zh_alias_index` 仅在 world 数据集时加载
**AC7:** `_count_notable_matches()` 也查询 zh alias index

## Tasks/Subtasks

- [x] T1: 添加 `_zh_alias_index` 全局变量和 `_ZH_GEONAMES_TSV` 路径常量
- [x] T2: 实现 `_load_zh_alias_index()` 懒加载函数
- [x] T3: 实现 `_resolve_from_zh_alias()` 消歧函数（parent-proximity + population）
- [x] T4: 修改 `resolve_names()` 插入 Level 2 zh alias 查找
- [x] T5: 修改 `_count_notable_matches()` 添加 zh alias 检查
- [x] T6: 修复 Story 21.1 繁→简缺失问题（opencc t2s 变体生成），重新生成 TSV
- [x] T7: 端到端验证（纽约/伦敦/亚特兰大等 20+ 城市 resolve 通过）

## Dev Notes

**查找优先级（4 级）：**
1. `_SUPPLEMENT_CN` / `_SUPPLEMENT_GEO` — 手工精确覆盖（最高优先）
2. `_zh_alias_index` — 中文别名索引（26K 条，仅 world 数据集）
3. GeoNames 原始精确匹配
4. GeoNames 后缀剥离匹配 + parent-proximity 验证

**消歧策略：**
- 同名多条目（如 孟菲斯→Memphis TN + Memphis FL）
- 如有 parent_coord：选距离最近且 < 1000km 的
- 否则：选 population 最大的（TSV 已按 pop desc 排序）

**繁→简修复：**
GeoNames alternateNamesV2 中很多 zh-Hant 条目仅有繁体（紐約），无对应简体（纽约）。
Story 21.1 脚本新增 Step 2.5 使用 opencc t2s 转换，为每个繁体名生成简体变体。
新增 4,560 行，总计 26,039 行。

## Dev Agent Record

### Implementation Plan
- 在 `geo_resolver.py` 顶部添加全局变量和路径常量
- 三个新函数：`_load_zh_alias_index()`、`_resolve_from_zh_alias()`
- 修改两个现有方法：`resolve_names()` 和 `_count_notable_matches()`
- 复用已有 `_haversine_km()` 做距离计算

### Completion Notes
✅ Story 21.2 完成。

**验证结果：**
- Index 加载：24,901 unique names
- 20/20 关键城市 resolve 通过（纽约、伦敦、巴黎、柏林、东京、亚特兰大、内罗毕、达累斯萨拉姆、开罗、墨西哥城等）
- Python import 验证通过（uv run python -c "from services.geo_resolver import ..."）

## File List

- `backend/src/services/geo_resolver.py` — 修改，新增 zh alias 查找层（~80 行新增代码）

## Change Log

- 2026-02-20: Story 21.2 实现完成。中文别名查找层集成 + 端到端验证。
