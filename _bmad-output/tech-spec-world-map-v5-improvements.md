---
title: '世界地图 V5 — 布局重构 + 副本合并 + 地点过滤 + 卷识别 + 地理面板'
slug: 'world-map-v5-improvements'
created: '2026-02-14'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python/FastAPI', 'React/TypeScript', 'MapLibre GL JS', 'scipy']
files_to_modify:
  - 'backend/src/services/map_layout_service.py'
  - 'backend/src/services/visualization_service.py'
  - 'backend/src/extraction/prompts/extraction_system.txt'
  - 'backend/src/extraction/fact_validator.py'
  - 'backend/src/utils/chapter_splitter.py'
  - 'frontend/src/components/visualization/NovelMap.tsx'
  - 'frontend/src/components/visualization/MapLayerTabs.tsx'
  - 'frontend/src/pages/MapPage.tsx'
  - 'frontend/src/api/types.ts'
code_patterns:
  - 'ConstraintSolver 已用独立的 _canvas_min_x/y _canvas_max_x/y，能量函数无正方形假设'
  - 'Portal 渲染已有完整的 ⊙ 图标 + 弹窗 + 跳转逻辑 (NovelMap.tsx:565-690)'
  - '_assign_volumes() 仅在 auto-detect 路径被调用 (chapter_splitter.py:153)'
  - 'location_count 在 _build_ws_summary() 动态计算 (visualization_service.py:540-556)'
test_patterns: []
---

# Tech-Spec: 世界地图 V5 — 多维度改进

**Created:** 2026-02-14

## Overview

### Problem Statement

用户在使用世界地图功能时发现 5 个问题：

1. **画布正方形导致环形分布**：`CANVAS_SIZE` 是单一数值，后端画布始终为正方形（默认 1000×1000）。地点在 Voronoi 区域布局 + overlap repulsion 下呈环形聚集在中心，浪费了大量水平空间。
2. **副本 tab 过多**：西游记有 10+ 个 layer tab（花果山福地、灵台方寸山、蟠桃园精舍等），很多只有 1 个地点。用户希望低地点数的副本合并到主世界显示入口，减少 tab 噪音。
3. **地点提取质量差**：提取 prompt 写的是"宁多勿漏"，`fact_validator.py` 没有过滤"山"、"河"等泛化单字词。这些泛化词污染地图和实体列表。
4. **卷识别失败**：平凡的世界 TXT 使用 markdown 格式 `# 第一部` / `## 第一章`，但 `_VOLUME_PATTERN` 正则 `^\s*第X[卷部集]` 不匹配 `#` 前缀行。同时缺少"章节号重置检测"策略。额外 bug：显式 mode 时 `_assign_volumes()` 不被调用。
5. **缺少地理上下文面板**：地图页面没有展示章节中地理描述原文的入口，AI 和用户都缺少空间关系的上下文参考。

### Solution

5 个独立但互补的改进，分为 Feature A-E：

- **A. 16:9 宽屏画布**：将 `CANVAS_SIZE` 改为 `CANVAS_WIDTH/CANVAS_HEIGHT`，默认 1600×900。
- **B. 地点提取过滤**：修改 LLM prompt + validator blocklist 双保险。
- **C. 卷识别增强**：正则修复 + 章节号重置检测 + 显式 mode bug 修复。
- **D. 副本智能合并**：≤1 地点的副本合并到主世界 portal 入口。
- **E. 地理面板**：地图页面侧边面板，展示各章节空间描述。

### Scope

**In Scope:**
- 后端画布从正方形改为 16:9 矩形（Feature A: 11 个后端函数 + 3 个前端锚点）
- 副本 tab 合并逻辑（Feature D: 后端标记 + 前端过滤 + portal 自动入口）
- LLM 提取 prompt 增加地点质量规则（Feature B: prompt + validator）
- 卷识别三修复（Feature C: 正则 + 重置检测 + bug fix）
- 地图页面地理上下文面板（Feature E: 新 API + 新组件）

**Out of Scope:**
- 前端 MapLibre 渲染引擎替换
- 新增 LLM 提取字段（`geography_notes`）— 未来可选
- 地图交互改进（拖拽、缩放行为变更）
- Terrain 地形图生成改动（保持正方形 PNG，前端映射适配）

## Context for Development

### Codebase Patterns

**画布系统：**
- `CANVAS_SIZE=1000`, `CANVAS_MIN=50`, `CANVAS_MAX=950` — map_layout_service.py:36-47
- `SPATIAL_SCALE_CANVAS` 值为单整数 — map_layout_service.py:41-47
- `_LAYER_CANVAS_SIZES` 副本画布尺寸为单整数 — map_layout_service.py:568-574
- `ConstraintSolver.__init__()` 已用独立的 `_canvas_min_x/y`, `_canvas_max_x/y` — 部分就绪
- 前端 `makeLngLatMapper(canvasSize: number)` 单数值投影 — NovelMap.tsx:46-55

**副本/Layer 系统：**
- Portal 渲染完整：⊙ 图标 + 弹窗 + 跳转 — NovelMap.tsx:565-690, 889-944
- `location_count` 在 `_build_ws_summary()` 动态计算 — visualization_service.py:540-556
- Tab UI 在 MapLayerTabs.tsx，`layers.length <= 1` 时隐藏

**地点提取：**
- Prompt: "宁多勿漏" — extraction_system.txt:27
- Validator: 仅过滤 `{角色名}+府邸/住所` — fact_validator.py:141-179

**卷识别：**
- `_VOLUME_PATTERN`: `^\s*第X[卷部集]` 不匹配 markdown 前缀
- `_assign_volumes()` 仅在 auto-detect 路径调用 — chapter_splitter.py:153
- Markdown mode 不区分 `#`/`##` 层级

### Files to Reference

| File | Purpose | 关键行号 |
| ---- | ------- | -------- |
| `backend/src/services/map_layout_service.py` | 画布常量、Voronoi/Lloyd/solver | 36-47, 121-311, 568-574, 687-880, 1213-1303 |
| `backend/src/services/visualization_service.py` | 地图 API、ws_summary | 267-537, 540-556 |
| `backend/src/extraction/prompts/extraction_system.txt` | LLM 提取规则 | 27-32 |
| `backend/src/extraction/fact_validator.py` | 地点验证 | 141-179 |
| `backend/src/utils/chapter_splitter.py` | 章节分割、卷识别 | 27-69, 83-154, 233-262 |
| `frontend/src/components/visualization/NovelMap.tsx` | makeLngLatMapper、Portal | 23-55, 211, 565-690 |
| `frontend/src/components/visualization/MapLayerTabs.tsx` | Tab UI | 1-56 |
| `frontend/src/pages/MapPage.tsx` | 地图页面容器 | 46-96, 168-184 |
| `frontend/src/api/types.ts` | MapData、MapLayerInfo | 218-293 |

### Technical Decisions

1. **画布 16:9**：默认 `CANVAS_WIDTH=1600, CANVAS_HEIGHT=900`。`SPATIAL_SCALE_CANVAS` 值改为 `(w, h)` 元组。
2. **提取过滤双保险**：prompt 规则 + validator blocklist。
3. **副本合并**：后端在 `_build_ws_summary()` 标记 `merged=true`；前端隐藏 merged tab，主世界自动添加 portal。
4. **地理面板**：汇总现有 `spatial_relationships[].narrative_evidence` + `locations[].description`，不改提取。
5. **卷识别**：正则前缀可选 `(?:#{1,3}\s+)?`；章节号重置检测作为补充策略。
6. **API**：`canvas_size` 改为 `{width: number, height: number}`。
7. **布局缓存**：`_LAYOUT_VERSION` 6→7 强制重新计算。

---

## Implementation Plan

### Tasks

依赖顺序：B（独立）→ C（独立）→ A（基础）→ D（依赖 A 的 canvas_size API 变更）→ E（依赖 MapPage 结构）

---

#### Feature B: 地点提取过滤（独立，最小改动优先）

- [ ] **Task 1: 修改 LLM 提取 prompt**
  - File: `backend/src/extraction/prompts/extraction_system.txt`
  - Action: 在地点提取规则（line 27-32）中添加第 6 条规则：
    ```
    6. **不要提取泛化地理词**：如"山""河""海""湖""江""路""城""门"等单字泛化词不是具体地名，不应提取。必须是完整的地名，如"花果山""黄河""东海"才应提取。
    ```
  - Notes: 保留"宁多勿漏"原则但增加质量约束

- [ ] **Task 2: fact_validator 增加泛化地理词 blocklist**
  - File: `backend/src/extraction/fact_validator.py`
  - Action: 在 `_validate_locations()` 函数（line 141）中添加 blocklist 检查：
    ```python
    _GENERIC_GEO_WORDS = frozenset({
        "山", "河", "海", "湖", "江", "路", "街", "村", "城",
        "门", "桥", "楼", "塔", "洞", "林", "谷", "岛", "关",
        "殿", "宫", "庙", "寺", "庄", "镇", "县", "省", "国",
    })
    ```
    在 `seen_names` 去重后添加：
    ```python
    if name in _GENERIC_GEO_WORDS:
        logger.debug("Dropping generic geo word: %s", name)
        continue
    ```
  - Notes: 仅过滤完全匹配的单字词。"山村"、"东海"等多字词不受影响

---

#### Feature C: 卷识别增强（独立）

- [ ] **Task 3: 修复 _VOLUME_PATTERN 支持 markdown 前缀**
  - File: `backend/src/utils/chapter_splitter.py`
  - Action: 修改 `_VOLUME_PATTERN`（line 74-77）：
    ```python
    _VOLUME_PATTERN = re.compile(
        r"^\s*(?:#{1,3}\s+)?第[零〇一二两三四五六七八九十百千万\d]+[卷部集][\s：:]*(.*)$",
        re.MULTILINE,
    )
    ```
    增加可选的 `(?:#{1,3}\s+)?` 前缀匹配 markdown header
  - Notes: `# 第一部`、`## 第一卷`、`第二部`（无前缀）均匹配

- [ ] **Task 4: 修复显式 mode 时不调用 _assign_volumes() 的 bug**
  - File: `backend/src/utils/chapter_splitter.py`
  - Action: 在 `split_chapters()` 函数的显式 mode 路径（line 116-127）中，`return` 前添加 `_assign_volumes(text, chapters)` 调用：
    ```python
    if mode:
        for mode_name, pattern in _PATTERNS:
            if mode_name == mode:
                matches = list(pattern.finditer(text))
                if len(matches) >= 2:
                    chapters = _split_by_matches(text, mode_name, matches)
                    _assign_volumes(text, chapters)  # 新增
                    return chapters
    ```
    同样修复 custom_regex 路径（line 107-113）

- [ ] **Task 5: 新增章节号重置检测策略**
  - File: `backend/src/utils/chapter_splitter.py`
  - Action: 在 `_assign_volumes()` 末尾添加重置检测逻辑：如果没有 volume 匹配但检测到重复的章节标题（如两个"第一章"），推断卷边界：
    ```python
    def _detect_volume_resets(chapters: list[ChapterInfo]) -> None:
        """推断卷边界：当章节标题中的章节号出现重置时。"""
        if not chapters or chapters[0].volume_num is not None:
            return  # 已有卷信息，不覆盖

        # 提取章节标题中的中文章节号
        _CH_NUM = re.compile(r"第([零〇一二两三四五六七八九十百千万\d]+)[章回节]")
        seen_titles: set[str] = set()
        vol_num = 1

        for ch in chapters:
            m = _CH_NUM.search(ch.title)
            if not m:
                continue
            ch_label = m.group(0)  # e.g. "第一章"
            if ch_label in seen_titles:
                # 章节号重复 → 新卷开始
                vol_num += 1
                seen_titles.clear()
            seen_titles.add(ch_label)
            ch.volume_num = vol_num
    ```
    在 `_assign_volumes()` 返回后调用此函数
  - Notes: 仅在 `_assign_volumes()` 未找到任何卷标记时生效

---

#### Feature A: 16:9 宽屏画布（基础改动）

- [ ] **Task 6: 后端画布常量改为 width/height**
  - File: `backend/src/services/map_layout_service.py`
  - Action:
    1. 替换常量（line 36-47）：
       ```python
       CANVAS_WIDTH = 1600
       CANVAS_HEIGHT = 900
       CANVAS_MIN_X = 50
       CANVAS_MAX_X = CANVAS_WIDTH - 50
       CANVAS_MIN_Y = 50
       CANVAS_MAX_Y = CANVAS_HEIGHT - 50
       ```
    2. `SPATIAL_SCALE_CANVAS` 值改为元组：
       ```python
       SPATIAL_SCALE_CANVAS: dict[str, tuple[int, int]] = {
           "cosmic": (8000, 4500),
           "continental": (4800, 2700),
           "national": (3200, 1800),
           "urban": (1600, 900),
           "local": (800, 450),
       }
       ```
    3. `_LAYER_CANVAS_SIZES` 改为元组：
       ```python
       _LAYER_CANVAS_SIZES: dict[str, tuple[int, int]] = {
           "pocket": (480, 270),
           "sky": (960, 540),
           "underground": (960, 540),
           "sea": (960, 540),
           "spirit": (640, 360),
       }
       ```
    4. `DIRECTION_ZONES` 按 1600×900 重新计算
    5. `_CELESTIAL_Y_RANGE` 和 `_UNDERWORLD_Y_RANGE` 使用 `CANVAS_MAX_Y`/`CANVAS_MIN_Y`
  - Notes: 保持 16:9 比例贯穿所有画布尺寸

- [ ] **Task 7: 更新布局函数签名 canvas_size → (width, height)**
  - File: `backend/src/services/map_layout_service.py`
  - Action: 修改以下函数的签名和内部逻辑：
    1. `_compute_region_seeds(regions, canvas_width, canvas_height)` — margin/usable 分 x/y 计算
    2. `_lloyd_relax(seeds, canvas_width, canvas_height)` — mirror 用 cw/ch 分别处理
    3. `_layout_regions(regions, canvas_width, canvas_height)` — bounds 用 cw×ch
    4. `_clip_polygon_to_canvas(polygon, canvas_width, canvas_height)` — 裁剪矩形用 cw/ch
    5. `_distort_polygon_edges(polygon, canvas_width, canvas_height)` — amplitude 用 min(cw,ch)
    6. `generate_voronoi_boundaries(region_layout, canvas_width, canvas_height)` — mirror 用 cw/ch
    7. `_solve_layer()` — 从 `_LAYER_CANVAS_SIZES` 取元组
    8. `compute_layered_layout()` — 从 `SPATIAL_SCALE_CANVAS` 取元组
    9. `_solve_overworld_by_region()` — 传 cw/ch 到 `_layout_regions`
    10. `compute_chapter_hash()` — hash 包含 width 和 height
  - Notes: `ConstraintSolver` 的 `canvas_bounds` 参数已支持矩形，只需确保调用方传入正确的 `(min_x, min_y, max_x, max_y)`

- [ ] **Task 8: 更新 visualization_service.py**
  - File: `backend/src/services/visualization_service.py`
  - Action:
    1. 导入新常量 `CANVAS_WIDTH, CANVAS_HEIGHT`
    2. `get_map_data()` 中 `_ws_cs` 改为元组解包 `_ws_cw, _ws_ch = SPATIAL_SCALE_CANVAS.get(...)`
    3. 所有调用 `_layout_regions`、`generate_voronoi_boundaries`、`compute_chapter_hash` 传入 width/height
    4. API 响应中 `canvas_size` 改为：
       ```python
       "canvas_size": {"width": _ws_cw, "height": _ws_ch},
       ```
  - Notes: 需要同步更新 `_compute_or_load_layout()` 函数（如果存在）

- [ ] **Task 9: 更新前端类型和投影**
  - File: `frontend/src/api/types.ts`, `frontend/src/components/visualization/NovelMap.tsx`
  - Action:
    1. `types.ts` 中 `MapData.canvas_size` 改为 `canvas_size?: { width: number; height: number }`
    2. `NovelMap.tsx` 中：
       - 移除 `DEFAULT_CANVAS_SIZE = 1000`，改为 `DEFAULT_CANVAS = { width: 1600, height: 900 }`
       - `makeLngLatMapper(canvasWidth, canvasHeight)` — 用 `Math.max(w,h)` 计算 extentDeg，x/y 分别用 `w/2`、`h/2` 计算中心
       - 所有 `canvasSize` 引用改为解构 `{ width, height }`
       - Terrain 坐标映射用 width/height 分别计算
    3. `NovelMapProps` 中 `canvasSize` 改为 `canvasSize?: { width: number; height: number }`
    4. `MapPage.tsx` 中传递 `canvasSize={mapData?.canvas_size}`（类型已变化）

- [ ] **Task 10: 布局版本升级**
  - File: `backend/src/services/map_layout_service.py`
  - Action: `_LAYOUT_VERSION = 6` → `_LAYOUT_VERSION = 7`
  - Notes: 强制所有缓存失效并重新计算

---

#### Feature D: 副本智能合并（依赖 Task 8 的 API 变更）

- [ ] **Task 11: 后端标记 merged layers**
  - File: `backend/src/services/visualization_service.py`
  - Action: 修改 `_build_ws_summary()`（line 540-556），为每个 layer 增加 `merged` 字段：
    ```python
    merged = (
        layer.layer_id != "overworld"
        and loc_count <= 1
    )
    layer_summaries.append({
        ...existing fields...,
        "merged": merged,
    })
    ```
    对 merged 的 layer，自动创建 portal 入口数据（如果不存在）：
    ```python
    # 在 get_map_data() 中：将 merged layers 的地点添加为主世界的 portal 入口
    for layer_info in layer_summaries:
        if layer_info["merged"] and layer_info["location_count"] == 1:
            # 找到该 layer 的唯一地点，添加为主世界的 portal feature
            loc_name = next(
                (name for name, lid in ws.location_layer_map.items()
                 if lid == layer_info["layer_id"]),
                None
            )
            if loc_name:
                portals_response.append({
                    "name": f"进入{layer_info['name']}",
                    "source_layer": "overworld",
                    "source_location": loc_name,
                    "target_layer": layer_info["layer_id"],
                    "target_layer_name": layer_info["name"],
                    "target_location": loc_name,
                    "is_bidirectional": True,
                })
    ```
  - Notes: merged layer 的地点仍在主世界 layout 中，portal 图标指向该 layer 的独立视图

- [ ] **Task 12: 前端类型 + Tab 过滤**
  - File: `frontend/src/api/types.ts`, `frontend/src/components/visualization/MapLayerTabs.tsx`
  - Action:
    1. `types.ts` 中 `MapLayerInfo` 增加 `merged?: boolean`
    2. `MapLayerTabs.tsx` 中：
       - 将 layers 分为 `mainLayers`（非 merged）和 `mergedLayers`（merged）
       - 只为 `mainLayers` 渲染标签页
       - 如果有 `mergedLayers`，渲染一个"更多副本"下拉按钮，点击展开列表
       ```tsx
       const mainLayers = layers.filter(l => !l.merged)
       const mergedLayers = layers.filter(l => l.merged)

       // 主标签页
       {mainLayers.map(layer => <TabButton ... />)}

       // "更多副本"下拉（如果有）
       {mergedLayers.length > 0 && (
         <DropdownMenu>
           <DropdownMenuTrigger>更多 ({mergedLayers.length})</DropdownMenuTrigger>
           <DropdownMenuContent>
             {mergedLayers.map(layer => (
               <DropdownMenuItem onClick={() => onLayerChange(layer.layer_id)}>
                 {layer.name} ({layer.location_count})
               </DropdownMenuItem>
             ))}
           </DropdownMenuContent>
         </DropdownMenu>
       )}
       ```
  - Notes: 用 shadcn/ui 的 DropdownMenu 组件

---

#### Feature E: 地理面板（依赖 MapPage 结构）

- [ ] **Task 13: 后端地理上下文 API**
  - File: `backend/src/services/visualization_service.py` 或新建 `backend/src/api/routes/geography.py`
  - Action: 在 `get_map_data()` 返回数据中增加 `geography_context` 字段：
    ```python
    # 收集地理上下文
    geo_context: list[dict] = []
    for fact in facts:
        ch = fact.chapter_id
        entries: list[dict] = []
        # 地点描述
        for loc in fact.locations:
            if loc.description:
                entries.append({
                    "type": "location",
                    "name": loc.name,
                    "text": loc.description,
                })
        # 空间关系证据
        for sr in fact.spatial_relationships:
            if sr.narrative_evidence:
                entries.append({
                    "type": "spatial",
                    "name": f"{sr.source} → {sr.target}",
                    "text": sr.narrative_evidence,
                })
        if entries:
            geo_context.append({"chapter": ch, "entries": entries})

    result["geography_context"] = geo_context
    ```
  - Notes: 直接嵌入 map API 响应，不新建端点。数据来源全部是现有 ChapterFact 字段。

- [ ] **Task 14: 前端地理面板组件**
  - File: 新建 `frontend/src/components/visualization/GeographyPanel.tsx`
  - Action: 创建可折叠的侧边面板组件：
    ```tsx
    interface GeographyPanelProps {
      context: { chapter: number; entries: { type: string; name: string; text: string }[] }[]
      visible: boolean
      onClose: () => void
    }
    ```
    - 按章节分组展示
    - 每条记录显示：类型图标（地点/空间关系）+ 名称 + 原文
    - 支持搜索过滤
    - 面板从右侧滑出（类似现有的 entity drawer）
  - Notes: 使用 shadcn/ui ScrollArea + Collapsible

- [ ] **Task 15: 集成地理面板到 MapPage**
  - File: `frontend/src/pages/MapPage.tsx`, `frontend/src/api/types.ts`
  - Action:
    1. `types.ts` 中 `MapData` 增加 `geography_context` 字段
    2. `MapPage.tsx` 中：
       - 增加 `showGeoPanel` 状态
       - 在地图工具栏增加"地理"按钮（地球图标）
       - 渲染 `<GeographyPanel>` 组件
    ```tsx
    const [showGeoPanel, setShowGeoPanel] = useState(false)

    // 工具栏
    <Button onClick={() => setShowGeoPanel(!showGeoPanel)}>
      <Globe className="h-4 w-4" />
      地理
    </Button>

    // 面板
    <GeographyPanel
      context={mapData?.geography_context ?? []}
      visible={showGeoPanel}
      onClose={() => setShowGeoPanel(false)}
    />
    ```

---

### Acceptance Criteria

#### Feature B: 地点提取过滤

- [ ] **AC 1**: Given extraction_system.txt 已更新, When LLM 分析包含"到了一座山上"的章节, Then 提取结果不包含 name="山" 的地点
- [ ] **AC 2**: Given fact_validator 有 blocklist, When ChapterFact 中含 name="山" 的 LocationFact, Then validator 过滤掉该条目并 log debug
- [ ] **AC 3**: Given 正常地名"花果山", When 经过 validator, Then 保留不被过滤（blocklist 仅匹配完全相等的单字词）

#### Feature C: 卷识别

- [ ] **AC 4**: Given TXT 文件包含 `# 第一部\n## 第一章\n...`, When 上传并自动分割, Then 第一章的 volume_num=1, volume_title 不为空
- [ ] **AC 5**: Given TXT 文件包含两个"第一章"但无卷标记, When 上传并分割, Then 第二个"第一章"开始 volume_num=2
- [ ] **AC 6**: Given 使用显式 mode="markdown" 分割, When 文件含 `# 第一部`, Then _assign_volumes() 正确识别卷信息
- [ ] **AC 7**: Given TXT 文件含 `第一卷`（无 markdown 前缀）, When 上传, Then 卷识别行为与之前一致（向后兼容）

#### Feature A: 16:9 画布

- [ ] **AC 8**: Given 后端启动, When 请求西游记地图, Then API 返回 `canvas_size: {width: X, height: Y}` 且 width/height 比例为 16:9
- [ ] **AC 9**: Given 地图加载完成, When 查看地点分布, Then 地点不再呈环形聚集，利用整个宽屏空间分布
- [ ] **AC 10**: Given 切换到副本 layer（如天宫）, When 查看布局, Then 副本画布也是 16:9 比例

#### Feature D: 副本合并

- [ ] **AC 11**: Given 西游记有 ≤1 地点的副本 layers, When 查看地图 tab, Then 这些 layer 不显示为独立 tab
- [ ] **AC 12**: Given merged 副本, When 查看主世界地图, Then 对应位置显示 portal ⊙ 入口图标
- [ ] **AC 13**: Given 有 merged 副本存在, When 查看 tab 栏, Then 显示"更多 (N)"按钮，点击展开副本列表

#### Feature E: 地理面板

- [ ] **AC 14**: Given 地图页面, When 点击"地理"按钮, Then 右侧滑出面板展示各章节地理描述
- [ ] **AC 15**: Given 地理面板打开, When 搜索"东海", Then 过滤显示包含"东海"的条目
- [ ] **AC 16**: Given 地图 API 响应, When 检查数据, Then 包含 `geography_context` 字段，按章节分组

## Additional Context

### Dependencies

无新外部依赖。使用现有 shadcn/ui 的 DropdownMenu、ScrollArea、Collapsible 组件。

### Testing Strategy

1. **地点过滤**：重新分析 2 章，检查实体列表不含"山""河"等单字地名
2. **卷识别**：准备两个测试文件：(a) markdown 格式 `# 第一部` (b) 无卷标记但有重复"第一章"
3. **画布**：删除 `map_layouts` + `layer_layouts` 缓存，重新加载西游记地图截图对比
4. **副本合并**：验证西游记 tab 从 10+ 减少到合理数量（仅天宫、地府等大副本保留 tab）
5. **地理面板**：打开面板验证章节分组显示、搜索过滤功能

### Notes

- **风险：画布变更范围大**。建议先完成 B、C（独立且改动小），确认无误后再做 A。A 改动后所有地图缓存失效，需要全量重新计算。
- **向后兼容**：`canvas_size` API 变更是 breaking change。前端必须同步更新。建议 A 的所有 Task（6-10）在一个 commit 中完成。
- **卷识别重置检测**是启发式策略，可能对某些小说产生误判（如章节标题不含章节号）。仅在无卷标记时启用。
- **地点 blocklist 可能需要迭代**：初始列表覆盖常见泛化词，实际使用中可能需要调整。建议列表放在常量区方便维护。
