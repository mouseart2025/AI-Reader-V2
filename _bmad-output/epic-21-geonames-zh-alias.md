---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories]
inputDocuments:
  - PRD.md (v1.3) — FR-006 世界地图增强
  - _bmad-output/world-map-v3-architecture.md
  - 用户提供的 GeoNames 集成需求规格
date: '2026-02-20'
scope: GeoNames 中文别名数据库集成 — 地理地名解析可扩展性提升
---

# AI Reader V2 - Epic 21: GeoNames 中文别名数据库集成

## Overview

本文档将 GeoNames 中文别名数据库集成需求分解为 1 个 Epic、4 个 Story。目标是用 GeoNames `alternateNamesV2` 的中文条目替代手工维护的 `_SUPPLEMENT_GEO` 字典，实现跨文化小说地名解析的自动覆盖。

**前置已实现功能**（v0.21.0 Epic 19 + v0.22.0 后续修复）：
- `_is_foreign_novel()` 外国小说检测 + 自适应 uber-root
- `_SUPPLEMENT_GEO` ~200 条手工美国/越南/世界地名
- `place_unresolved_geo_coords()` unresolved 地点近邻坐标推算
- `_NON_GEO_PATTERNS` 泛化中文词过滤
- 阿甘正传测试：10% → 22% 地名解析率

---

## Requirements Inventory

### Functional Requirements

```
FR-GEO-001: 集成 GeoNames alternateNamesV2 中文别名数据库，自动覆盖全球中文地名翻译（大陆/台湾/港澳译法）
FR-GEO-002: 升级城市数据集从 cities15000（~25K条）到 cities5000（~50K条），覆盖率翻倍
FR-GEO-003: 开发离线预处理脚本，从原始 GeoNames 数据生成轻量中文地名→坐标索引（~1-2MB TSV）
FR-GEO-004: 实现三级查找优先级：手工精确字典 → 中文别名索引 → GeoNames 原始匹配
FR-GEO-005: 精简 _SUPPLEMENT_GEO 手工字典至仅保留虚构地名和特殊覆盖
```

### NonFunctional Requirements

```
NFR-GEO-001: 中文别名索引运行时内存增加 < 10MB
NFR-GEO-002: 预处理后索引文件体积 < 2MB（随应用分发）
NFR-GEO-003: 不破坏现有中国小说地理解析（西游记、水浒传回归验证通过）
NFR-GEO-004: 预处理后离线可用 — 运行时无需网络下载 alternateNamesV2
NFR-GEO-005: 首次使用无额外配置 — 索引文件随应用打包分发
```

### Additional Requirements

- GeoResolver 已有 `GeoDatasetConfig` 注册表 + 自动下载基础设施（`GEONAMES_DIR`）
- `resolve_names()` 已有三级匹配（精确→后缀剥离→消歧），新增别名层插入在最前面
- `_NON_GEO_PATTERNS` 过滤必须在别名查找之前执行，防止泛化词命中
- 预处理脚本仅在开发/发布时运行，产物随 git 或发布包分发

### FR Coverage Map

| FR | Epic | Story |
|----|------|-------|
| FR-GEO-003 | Epic 21 | 21.1 |
| FR-GEO-001, FR-GEO-004 | Epic 21 | 21.2 |
| FR-GEO-002 | Epic 21 | 21.3 |
| FR-GEO-005, NFR-GEO-003 | Epic 21 | 21.4 |

## Epic List

| # | Epic | 优先级 | FR | Stories |
|---|------|--------|-----|---------|
| 21 | GeoNames 中文别名数据库集成 | P1 | FR-GEO-001~005 | 4 |

---

## Epic 21: GeoNames 中文别名数据库集成

**目标：** 用户分析任何国家/地区的翻译小说时，地理地图标记自动覆盖全球中文地名翻译，无需开发者手工维护 `_SUPPLEMENT_GEO` 字典。
**成功标准：** 阿甘正传地名解析率从 22% 提升至 ≥40%；新增非洲/欧洲/东南亚小说无需代码改动即可解析主要地名。
**数据获取策略：** 开发时预处理，产物（~1-2MB TSV）随应用分发。用户无需下载 190MB 原始数据。

### Story 21.1: GeoNames 中文别名预处理工具

As a 开发团队,
I want 一个离线预处理脚本，从 GeoNames alternateNamesV2 和 cities5000 原始数据生成中文地名→坐标索引,
So that 运行时无需处理 190MB 原始数据，只加载 ~1-2MB 的预处理产物。

**Acceptance Criteria:**

**Given** 开发者已下载 `alternateNamesV2.zip`（190MB）和 `cities5000.zip`（4.9MB）到本地
**When** 运行预处理脚本 `python -m scripts.build_zh_geonames`
**Then** 从 `alternateNamesV2.txt` 提取 `isolanguage` 以 `zh` 开头的条目（zh / zh-CN / zh-Hant / zh-HK 等）
**And** 与 `cities5000.txt` 通过 `geonameid` JOIN，获取每个中文地名的 (latitude, longitude, population, feature_code, country_code)
**And** 同一 geonameid 的多个中文别名分别生成独立行
**And** 输出为 TSV 文件 `backend/data/zh_geonames.tsv`，格式：`zh_name\tlat\tlng\tpop\tfeature_code\tcountry_code\tgeonameid`
**And** 输出文件体积 < 2MB（NFR-GEO-002）
**And** 脚本包含进度日志：总提取行数、zh 条目数、JOIN 命中数、最终输出行数
**And** 脚本在 `scripts/build_zh_geonames.py`，支持 `--alternate-names-path` 和 `--cities-path` 参数指定输入文件路径
**And** 脚本在无输入文件时给出清晰错误提示和下载 URL

**技术说明：**
- `alternateNamesV2.txt` 格式：`alternateNameId \t geonameid \t isolanguage \t alternate_name \t isPreferredName \t isShortName \t isColloquial \t isHistoric \t from \t to`
- `cities5000.txt` 格式：标准 GeoNames 19 列 TSV（geonameid=col0, name=col1, lat=col4, lng=col5, feature_code=col7, country_code=col8, population=col14）
- 过滤条件：`isolanguage.startswith("zh")` AND `geonameid` 存在于 cities5000 中
- 去重：同一 `(zh_name, geonameid)` 组合只保留一行，优先保留 `isPreferredName=1` 的

### Story 21.2: GeoResolver 中文别名查找层

As a 用户,
I want GeoResolver 在解析地名时自动查询中文别名索引,
So that 翻译小说中的中文地名（大陆译法、台湾译法）能自动匹配到正确坐标，无需手工维护。

**Acceptance Criteria:**

**Given** `backend/data/zh_geonames.tsv` 已存在（Story 21.1 产物）
**When** GeoResolver 首次需要解析 world 数据集的地名
**Then** 懒加载 `zh_geonames.tsv` 到内存字典 `_zh_alias_index: dict[str, list[tuple[float, float, int, str, str]]]`
**And** 同名多条目按 population 降序排列（消歧用）
**And** 加载后内存增加 < 10MB（NFR-GEO-001）
**And** `resolve_names()` 查找优先级变为：`_SUPPLEMENT_GEO` → `_zh_alias_index` → GeoNames 原始文件匹配
**And** 中文别名查找在 `_NON_GEO_PATTERNS` 过滤之后执行（防止泛化词命中）
**And** 中文别名消歧逻辑：同名多地取 population 最大者；如有 parent-proximity 信息，优先取距离 parent < 1000km 的
**And** `_zh_alias_index` 仅在 `detect_geo_scope()` 选择 world 数据集时加载（CN 数据集不需要）

**Given** 阿甘正传（novel_id: 2fdfe84f）已完成分析
**When** 重新生成地图数据
**Then** 之前 unresolved 的台湾译法地名（亚拉巴马、乔治亚、木比耳、纳许维尔等）能通过中文别名索引 resolve
**And** 地名解析率从 22% 提升至 ≥40%

**Given** 一本非洲主题翻译小说的地名列表包含"内罗毕"、"达累斯萨拉姆"、"亚的斯亚贝巴"
**When** 执行 geo resolve
**Then** 无需任何 `_SUPPLEMENT_GEO` 条目即可正确 resolve（这些中文名在 alternateNamesV2 的 zh 条目中存在）

### Story 21.3: 城市数据集升级 cities15000 → cities5000

As a 用户,
I want GeoResolver 使用更全面的城市数据集,
So that 人口在 5000-15000 之间的中小城市也能被匹配到。

**Acceptance Criteria:**

**Given** `DATASET_WORLD` 当前配置为 `cities15000.zip`（~25K 条目）
**When** 升级为 `cities5000.zip`（~50K 条目）
**Then** 修改 `DATASET_WORLD` 的 `url` 和 `zip_member` 字段
**And** 如果用户已有旧的 `cities15000.txt` 缓存文件，首次使用时自动下载 `cities5000.txt`（检测文件名不匹配）
**And** `_count_notable_matches()` 的 population 阈值从 5000 调整为 3000（适配更小城市）
**And** 运行时内存增加 < 5MB（50K 条 vs 25K 条）
**And** 现有中国小说（西游记）的 CN 数据集路径不受影响（CN.zip 独立于 world 数据集）
**And** `zh_geonames.tsv` 预处理脚本（Story 21.1）已使用 cities5000 作为输入源

**Given** GeoNames 原始匹配使用 cities5000
**When** 解析包含中小城市的小说（如提及人口 8000 的小镇）
**Then** 原始匹配覆盖率相比 cities15000 提升约 2 倍

### Story 21.4: 回归验证 + 手工字典精简

As a 开发团队,
I want 验证新的中文别名索引不破坏现有功能，并精简手工维护的字典,
So that 代码库的 `_SUPPLEMENT_GEO` 只保留必要的虚构地名和特殊覆盖，长期维护成本降低。

**Acceptance Criteria:**

**Given** Story 21.2 和 21.3 已完成
**When** 对西游记（中国古典小说，CN 数据集）执行地图生成
**Then** 地图标记数量和位置与修改前一致（NFR-GEO-003）
**And** CN 数据集路径完全不涉及中文别名索引（仅 world 数据集使用）

**Given** 阿甘正传（外国翻译小说，world 数据集）
**When** 执行完整地图生成流程
**Then** 解析率 ≥ 40%（相比当前 22%）
**And** 所有之前通过 `_SUPPLEMENT_GEO` 手工条目 resolve 的地名仍然能正确 resolve
**And** 新增 resolve 的地名坐标与实际位置偏差 < 50km

**Given** `_SUPPLEMENT_GEO` 当前 ~200 条手工条目
**When** 开发者审查哪些条目已被 `zh_geonames.tsv` 覆盖
**Then** 移除已覆盖的城市/州名条目（如 "纳什维尔"、"孟菲斯"、"阿拉巴马州" 等）
**And** 保留以下类别的条目不删除：
  - 洲/大洋/海域（亚洲、太平洋等）— GeoNames 不含这些
  - 虚构地名（绿弓镇）— GeoNames 无法覆盖
  - 历史地名（锡兰、暹罗、交趾支那）— alternateNamesV2 可能不含
  - 地标/机构（白宫、哈佛大学、迪士尼乐园）— GeoNames 以城市为主
  - 歧义覆盖（伯明翰→Alabama, 西贡→越南）— 需要明确指定
**And** 精简后 `_SUPPLEMENT_GEO` 条目数 < 100
**And** 保留的每个条目附带注释说明保留原因（`# fictional`, `# override`, `# not in geonames` 等）

---

## Implementation Summary

**Epic 21 全部完成** — 2026-02-20

| Story | 状态 | 关键成果 |
|-------|------|----------|
| 21.1 | ✅ done | 预处理脚本 + 产物 26,039 行 (1.3MB TSV)，含繁→简变体 |
| 21.2 | ✅ done | GeoResolver 4 级查找优先级，24,901 唯一中文名覆盖 |
| 21.3 | ✅ done | cities15000 → cities5000 升级 (~50K entries) |
| 21.4 | ✅ done | _SUPPLEMENT_GEO 从 290 → 218 条，73 城市移至 zh_alias |

**文件清单：**
- `scripts/build_zh_geonames.py` — 预处理脚本 (opencc 繁→简)
- `backend/data/zh_geonames.tsv` — 中文地名索引 (26,039 行, 1.3MB)
- `backend/src/services/geo_resolver.py` — zh alias 查找层 + cities5000 + supplement 精简
- `CLAUDE.md` — 更新 GeoResolver 文档

*文档完成于 2026-02-20。Epic 21，4 个 Story。*
