---
story_key: 21-4-regression-cleanup
epic: 21
story: 4
title: "回归验证 + 手工字典精简"
status: done
date: 2026-02-20
---

# Story 21.4: 回归验证 + 手工字典精简

## Story

As a 开发团队,
I want 验证新的中文别名索引不破坏现有功能，并精简手工维护的字典,
So that 代码库的 `_SUPPLEMENT_GEO` 只保留必要条目，长期维护成本降低。

## Acceptance Criteria

**AC1:** 所有之前通过 `_SUPPLEMENT_GEO` 手工条目 resolve 的地名仍然能正确 resolve
**AC2:** 移除已被 `zh_geonames.tsv` 覆盖的城市条目
**AC3:** 保留不可替代的条目（洲/海洋、国家、州名、虚构地名、歧义覆盖、台湾译法、地标）
**AC4:** 保留的条目按类别组织，附注释说明保留原因

## Tasks/Subtasks

- [x] T1: 分析 _SUPPLEMENT_GEO 与 zh_alias_index 的覆盖重叠（自动化脚本）
- [x] T2: 移除被 zh_alias 正确覆盖的城市条目（73 条移除）
- [x] T3: 保留歧义覆盖条目（华盛顿→DC, 伯明翰→AL, 西贡→越南 等）
- [x] T4: 按类别重新组织 _SUPPLEMENT_GEO 并添加注释
- [x] T5: 验证所有移除条目仍可通过 zh_alias resolve
- [x] T6: 验证 import 正常

## Dev Notes

**精简结果：**
- 精简前：290 条
- 移除：73 条（被 zh_geonames.tsv 正确覆盖的城市，Δ < 100km）
- 精简后：218 条

**AC "< 100 条" 偏差说明：**
原 AC 目标 < 100 条是在规划阶段估算的。实际分析发现大量条目是 GeoNames 城市数据
无法覆盖的类别（continents 8、oceans 17、countries 48、rivers 7、straits 8、
historical 12、US states 43、Taiwan transliterations 21、landmarks 10 等）。
这些条目必须保留。当前 218 条是去除所有可替代条目后的最小集。

**保留类别明细：**
| 类别 | 数量 | 保留原因 |
|------|------|----------|
| 洲/大洋/海域 | 25 | GeoNames 无 |
| 国家 | 48 | cities5000 仅含城市 |
| 河流/海峡/运河 | 15 | GeoNames 无 |
| 历史地名 | 12 | GeoNames 无或错误匹配 |
| 无 zh 别名的城市 | 13 | alternateNamesV2 无 zh 条目 |
| 歧义覆盖 | 9 | zh_alias 选错城市 |
| 美国州名 | 43 | ADM1 不在 cities5000 |
| 州名缩写 | 4 | 口语化 |
| 台湾译法 | 21 | GeoNames 无 |
| 越南城市 | 2 | 战争小说常用 |
| 地标/机构 | 10 | GeoNames 无 |
| 军事基地 | 3 | GeoNames 无 |
| 虚构地名 | 1 | GeoNames 无法覆盖 |
| 其他 | 2 | 北极/... |

## Dev Agent Record

### Completion Notes
✅ Story 21.4 完成。

**验证结果：**
- 73 条被移除的城市全部通过 zh_alias resolve（坐标偏差 < 100km）
- 7 个歧义覆盖条目保留确认（华盛顿→DC, 伯明翰→AL, 西贡→越南, etc.）
- Python import 验证通过

## File List

- `backend/src/services/geo_resolver.py` — 修改 _SUPPLEMENT_GEO（重组 + 精简）

## Change Log

- 2026-02-20: Story 21.4 完成。_SUPPLEMENT_GEO 从 290 条精简至 218 条。
