---
story_key: 21-1-geonames-zh-preprocessor
epic: 21
story: 1
title: "GeoNames 中文别名预处理工具"
status: done
date: 2026-02-20
---

# Story 21.1: GeoNames 中文别名预处理工具

## Story

As a 开发团队,
I want 一个离线预处理脚本，从 GeoNames alternateNamesV2 和 cities5000 原始数据生成中文地名→坐标索引,
So that 运行时无需处理 190MB 原始数据，只加载 ~1-2MB 的预处理产物。

## Acceptance Criteria

**AC1:** 从 `alternateNamesV2.txt` 提取 `isolanguage` 以 `zh` 开头的条目（zh / zh-CN / zh-Hant / zh-HK 等）
**AC2:** 与 `cities5000.txt` 通过 `geonameid` JOIN，获取每个中文地名的 (latitude, longitude, population, feature_code, country_code)
**AC3:** 同一 geonameid 的多个中文别名分别生成独立行
**AC4:** 输出为 TSV 文件 `backend/data/zh_geonames.tsv`，格式：`zh_name\tlat\tlng\tpop\tfeature_code\tcountry_code\tgeonameid`
**AC5:** 输出文件体积 < 2MB
**AC6:** 脚本包含进度日志：总提取行数、zh 条目数、JOIN 命中数、最终输出行数
**AC7:** 脚本在 `scripts/build_zh_geonames.py`，支持 `--alternate-names-path` 和 `--cities-path` 参数
**AC8:** 脚本在无输入文件时给出清晰错误提示和下载 URL
**AC9:** 去重：同一 `(zh_name, geonameid)` 组合只保留一行，优先保留 `isPreferredName=1` 的

## Tasks/Subtasks

- [x] T1: 创建 `scripts/build_zh_geonames.py` 脚本骨架（argparse + main 入口）
- [x] T2: 实现 cities5000.txt 解析器（加载 geonameid → coords/pop/feature/country 映射）
- [x] T3: 实现 alternateNamesV2.txt 流式过滤器（zh* 语言码提取 + geonameid JOIN）
- [x] T4: 实现去重逻辑（isPreferredName 优先）+ TSV 输出
- [x] T5: 添加进度日志 + 错误处理 + 下载 URL 提示
- [x] T6: 下载数据并运行脚本，验证产物 `backend/data/zh_geonames.tsv`

## Dev Notes

**数据格式：**
- `alternateNamesV2.txt`：`alternateNameId \t geonameid \t isolanguage \t alternate_name \t isPreferredName \t isShortName \t isColloquial \t isHistoric \t from \t to`
- `cities5000.txt`：标准 GeoNames 19 列 TSV（geonameid=col0, name=col1, lat=col4, lng=col5, feature_code=col7, country_code=col8, population=col14）

**过滤条件：** `isolanguage.startswith("zh")` AND `geonameid` 存在于 cities5000 中

**输出格式：** TSV，无 header，`zh_name\tlat\tlng\tpop\tfeature_code\tcountry_code\tgeonameid`

**下载 URL：**
- https://download.geonames.org/export/dump/alternateNamesV2.zip
- https://download.geonames.org/export/dump/cities5000.zip

## Dev Agent Record

### Implementation Plan
- 单文件实现（脚本足够简单，无需拆分模块）
- 四步流水线：load_cities → stream_zh_alternates → add_simplified_variants → write_tsv
- 流式处理 alternateNamesV2（730MB），不一次性加载到内存
- 去重使用 dict[(zh_name, geonameid)] 键，isPreferredName=1 优先
- 繁→简转换使用 opencc-python-reimplemented（dev dependency）

### Completion Notes
✅ Story 21.1 完成。

**运行结果（含繁→简）：**
- alternateNamesV2 总行数: 18,736,157
- zh* 条目数: 1,063,123
- JOIN 命中数: 22,590
- 去重后输出行数: 21,479
- 繁→简新增行数: 4,560
- 最终输出行数: 26,039
- 输出文件: 1,348 KB (< 2MB ✅)
- 处理时间: ~10 秒

**覆盖分析：**
- 24,901 个唯一中文地名
- 美国 3,930+ 条（城市级别，简体+繁体）
- 中国 3,550 条
- 德国 2,144 条
- 共覆盖 100+ 国家
- 关键简体名验证通过：纽约、伦敦、巴黎、柏林、东京、亚特兰大、内罗毕等 21/21

**未覆盖（由 _SUPPLEMENT_GEO 继续承担）：**
- 州/省名（ADM1 不在 cities5000 中）
- 台湾特有译法（纳许维尔、木比耳等）
- 虚构地名（绿弓镇）
- 地标/机构（白宫、哈佛大学）
- 洲/海洋/水体

## File List

- `scripts/build_zh_geonames.py` — 新增，预处理脚本（含 opencc 繁→简转换）
- `backend/data/zh_geonames.tsv` — 新增，预处理产物（26,039 行, 1.3MB）

## Change Log

- 2026-02-20: Story 21.1 实现完成。脚本 + 产物均已生成验证。
- 2026-02-20: 新增 Step 2.5 繁→简变体生成（opencc t2s），+4,560 行。纽约/伦敦等简体名覆盖确认。
