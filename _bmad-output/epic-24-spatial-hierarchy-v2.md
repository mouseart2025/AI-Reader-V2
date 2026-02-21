# Epic 24: 空间层级微观尺度修正 (v0.25.0)

## Status: Implemented

## Problem

微观地点（小溪、穷山沟）与宏观地点（越国）被展平在同一层级（天下的直接子节点），导致地图混乱。

## Root Causes Addressed

1. `_tiered_catchall` 无条件收养 uber_root — 无 tier 检查
2. `_NAME_SUFFIX_TIER` 缺少 14 个微观后缀（沟/街/巷/居/墓/陵/桥等）
3. 主场景推断投票权重 weight=1 太弱，被噪声信号淹没
4. `_classify_tier` Layer 4 兜底过于宽松（city 而非 site）

## Stories Implemented

### D.1: `_NAME_SUFFIX_TIER` 微观后缀补全

**File**: `backend/src/services/world_structure_agent.py`

Added 14 new entries (84 → 101 total):
- 2-char: 码头, 渡口, 胡同 (all site)
- 1-char site: 沟, 街, 巷, 墓, 陵, 桥, 坝, 堡, 哨, 弄
- 1-char building: 居

### D.2: `_classify_tier` Layer 4 兜底降级 city → site

**File**: `backend/src/services/world_structure_agent.py`

Changed final `else` branch from `LocationTier.city.value` to `LocationTier.site.value`.
All recognizable city patterns (国, 城/镇/都/村, admin suffixes) are caught by earlier layers.

### D.3: 主场景推断投票权重 1 → 2

**File**: `backend/src/services/world_structure_agent.py`

Changed `+= 1` to `+= 2` in both:
- `_apply_heuristic_updates()` (live analysis)
- `_rebuild_parent_votes()` (hierarchy rebuild)

### D.4: `_tiered_catchall` tier-based 中间节点匹配

**File**: `backend/src/services/hierarchy_consolidator.py`

For site/building orphans (rank ≥ 5), before uber_root fallback, finds the dominant
intermediate node (uber_root's child with most descendants, ≥3 required) and adopts
the orphan under it.

### D.5: uber_root 收养 tier 门槛

**File**: `backend/src/services/hierarchy_consolidator.py`

Only city-level and above (rank ≤ 4) are adopted by uber_root. Site/building orphans
without a match remain as independent roots rather than polluting 天下's children.

## Files Changed

| File | Changes |
|------|---------|
| `backend/src/services/world_structure_agent.py` | D.1 (14 suffixes), D.2 (tier fallback), D.3 (vote weight) |
| `backend/src/services/hierarchy_consolidator.py` | D.4 (dominant matching), D.5 (tier gate) |
| `backend/pyproject.toml` | 0.24.2 → 0.25.0 |
| `frontend/package.json` | 0.24.2 → 0.25.0 |
| `CLAUDE.md` | Updated suffix count, vote weights, catchall docs |

## Verification

- `_get_suffix_rank("穷山沟")` → 5, `_get_suffix_rank("静心居")` → 6
- `_get_suffix_rank("越国")` → 2 (no regression)
- `_classify_tier("某某", "", None, 0)` → "site" (was "city")
- `_classify_tier("铜州", "", None, 0)` → "kingdom" (no regression)
- Import validation: both modules import cleanly
