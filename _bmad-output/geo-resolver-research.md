# 地理类型检测 + 真实坐标匹配 — 技术调研报告

> 日期: 2026-02-16 | 作者: Winston (Architect Agent)

## 一、背景与目标

当前世界地图的布局完全由 `ConstraintSolver`（scipy.optimize）根据小说文本中的空间关系约束计算坐标。对于现实题材小说（如《水浒传》、金庸武侠），地名大多对应真实地理位置，但系统无法利用这一点——算出的坐标与现实地理毫无关联。

**目标**：让系统自动评估地理数据类型，在现实地理模式下匹配真实坐标数据库，绘制与现实一致的地图。

## 二、现状评估

系统已有基础设施可以复用：

| 已有能力 | 位置 | 可复用程度 |
|---|---|---|
| 体裁检测 | `WorldStructureAgent._detect_genre()` | 已能区分 fantasy/wuxia/historical/urban/realistic |
| 地点数据 | ChapterFact locations + entity_dictionary | 地名、类型、别名、父子关系齐全 |
| 布局切换点 | `visualization_service.get_map_data()` L541-571 | 已有分支：layered vs global solver |
| 空间尺度 | `WorldStructure.spatial_scale` | cosmic/continental/national/urban/local |
| 前端投影 | NovelMap.tsx SVG + D3 zoom | 坐标系完全由后端提供，前端只管渲染 |

**关键洞察**：前端完全不关心坐标来源。后端 layout 返回 `[{name, x, y, radius}]`，前端直接渲染。只需在后端增加一条新的布局路径——"真实地理坐标投影"——与现有 ConstraintSolver 并行即可。**前端零改动**。

## 三、地理类型检测方案

### 两阶段检测

**阶段 1：体裁预判（已有）**

现有 `_detect_genre()` 已能给出 `novel_genre_hint`。当结果为 `historical`、`wuxia`、`realistic`、`urban` 时，进入候选状态。`fantasy` 直接走原有约束求解器。

**阶段 2：地名匹配率验证（新增）**

在分析完成后（或前 N 章分析后），将所有提取的地名去匹配地理数据库：

```
match_ratio = 匹配到真实坐标的地名数 / 总地名数（排除 building 级别）
```

| match_ratio | 判定 | 策略 |
|---|---|---|
| ≥ 0.5 | **写实地理 (REALISTIC)** | 匹配到的地名用真实坐标，未匹配的用约束求解相对定位 |
| 0.2 ~ 0.5 | **混合地理 (MIXED)**（如金庸小说） | 同上，但匹配阈值更宽松 |
| < 0.2 | **幻想地理 (FANTASY)** | 全部走 ConstraintSolver |

无需用户干预——系统自动检测、自动选择布局策略。

## 四、地理坐标数据库选型

### 核心层：GeoNames CN（推荐）

| 属性 | 值 |
|---|---|
| 数据量 | CN.zip 约 30MB，解压后 ~10 万条中国地理实体 |
| 覆盖 | 省/市/县/区/镇/村 + 山/河/湖/岛等自然地物 |
| 字段 | name, asciiname, alternatenames, lat, lng, feature_class, feature_code, population, admin1-4 |
| **关键优势** | `alternatenames` 字段包含中文名 + 拼音 + 多种别名 |
| 许可 | CC BY 4.0，免费商用 |
| Python 使用 | 加载到内存 dict 或 SQLite |
| 离线 | 完全离线，符合项目"数据本地化"原则 |

来源: https://www.geonames.org/ / https://download.geonames.org/export/dump/CN.zip

### 补充层：中国行政区划数据

[AreaCity-JsSpider-StatsGov](https://github.com/xiangyuecn/AreaCity-JsSpider-StatsGov) — 2025 年最新，省市区县乡镇四级，带坐标和行政边界 GeoJSON。可用于行政区划级别的精确匹配，以及生成省界轮廓底图。

### 历史地名层（可选扩展）

[CHGIS](https://chgis.fas.harvard.edu/) — 哈佛大学中国历史地理信息系统，14 万条历史地名 + 坐标。适合古典小说（水浒传、三国演义等）的古地名匹配。学术免费许可。

### 不推荐

- **在线 API（高德/百度/Google Geocoding）**：违反本地化原则，需要网络和 API Key
- **cpca 库**：仅覆盖省市区三级行政区划，不含山河湖等自然地物，不够用

## 五、地名匹配策略

### 三级匹配管线

```
Level 1: 精确匹配 — 地名完全等于 GeoNames.name 或 alternatenames 中的某一个
Level 2: 后缀剥离匹配 — "杭州城"→"杭州", "梁山泊"→"梁山"
Level 3: LLM 辅助匹配 — 将未匹配地名批量发给 LLM，要求返回现代地名对应
```

### 后缀剥离规则（针对中文小说）

- 行政后缀：城、府、州、县、镇、村、寨、营
- 地理后缀：山、河、江、湖、海、泊、谷、洞、林、峰
- 设施后缀：寺、庙、观、宫、殿、楼、阁、塔

### LLM 辅助匹配

分析完成后一次性调用，prompt 示例：

```
以下地名来自一本中国小说，请判断每个地名是否对应一个真实地理位置。
如果是，返回其现代标准地名；如果是虚构的，返回"虚构"。
格式: 原名 → 现代地名 或 虚构

地名列表：东京汴梁、梁山泊、景阳冈、阳谷县、清河县 ...
```

## 六、技术架构设计

### 新增模块

```
backend/src/services/
  geo_resolver.py          # 新文件：地名→坐标解析器
backend/data/
  geonames_cn.tsv          # GeoNames CN 数据（~30MB，首次运行自动下载）
```

### GeoResolver 核心接口

```python
class GeoType(str, Enum):
    REALISTIC = "realistic"   # ≥50% 地名匹配
    MIXED = "mixed"           # 20-50% 匹配
    FANTASY = "fantasy"       # <20% 匹配

class GeoResolver:
    def __init__(self):
        self._load_geonames()  # 加载到内存 dict: name → (lat, lng, feature_code)

    def detect_geo_type(self, location_names: list[str]) -> GeoType:
        """计算匹配率，返回地理类型"""

    def resolve(self, names: list[str]) -> dict[str, tuple[float, float] | None]:
        """批量解析地名 → (lat, lng) 或 None（Level 1 + Level 2）"""

    async def resolve_with_llm(self, unresolved: list[str], llm: LLMClient) -> dict[str, str]:
        """LLM 辅助匹配未解析地名 → 标准地名（Level 3）"""

    def project_to_canvas(
        self, coords: dict[str, tuple[float, float]],
        canvas_w: int, canvas_h: int, padding: int = 60,
    ) -> list[dict]:
        """经纬度 → 画布坐标（墨卡托投影，自动裁剪到数据边界框）"""
```

### 集成点

在 `visualization_service.get_map_data()` 的布局计算分支中增加：

```python
# 新增：尝试真实地理坐标
geo_resolver = GeoResolver()
geo_type = geo_resolver.detect_geo_type(location_names)

if geo_type in (GeoType.REALISTIC, GeoType.MIXED):
    resolved = geo_resolver.resolve(location_names)
    layout_data = geo_resolver.project_to_canvas(resolved, cw, ch)
    # 未匹配到的地名：约束求解器在已定位地名附近放置
    unresolved = [n for n in location_names if n not in resolved]
    if unresolved:
        _place_unresolved_near_neighbors(unresolved, layout_data, ...)
    layout_mode = "geographic"
else:
    # 原有 ConstraintSolver 路径
    layout_data, layout_mode, terrain_url = await _compute_or_load_layout(...)
```

### 墨卡托投影

不需要引入 Leaflet/Mapbox。简单墨卡托投影将经纬度映射到画布坐标：

```python
def _mercator_project(lat, lng, bbox, canvas_w, canvas_h, padding=60):
    x = (lng - bbox.min_lng) / (bbox.max_lng - bbox.min_lng) * (canvas_w - 2*padding) + padding
    def lat_to_y(lat_deg):
        return math.log(math.tan(math.pi/4 + math.radians(lat_deg)/2))
    y_min, y_max = lat_to_y(bbox.min_lat), lat_to_y(bbox.max_lat)
    y_norm = (lat_to_y(lat) - y_min) / (y_max - y_min)
    y = (1 - y_norm) * (canvas_h - 2*padding) + padding  # flip Y
    return x, y
```

### 前端改动

**零改动**。前端只接收 `layout: [{name, x, y}]`，不关心坐标来源。可选：在 MapPage 状态栏显示"地理模式: 写实/混合/幻想"。

## 七、可选增强：轻量底图

写实模式下，使用 matplotlib + 省界 GeoJSON 生成极简中国轮廓底图 PNG，替代当前 terrain。用户一眼即知是现实中国地图。

数据来源：AreaCity 的省界 GeoJSON（几 MB）。

## 八、权衡与风险

| 权衡点 | 分析 |
|---|---|
| 数据体积 | GeoNames CN ~30MB，可接受。首次使用时下载到 `~/.ai-reader-v2/data/` |
| 匹配准确率 | 精确+后缀剥离可覆盖 ~70% 常见地名，LLM 辅助可提升到 ~90% |
| 古典小说 | 水浒/三国等古地名需 LLM 辅助匹配，或后续集成 CHGIS 历史地名库 |
| 混合类型 | 金庸小说（真实城市+虚构门派）用 MIXED 模式处理，未匹配地名就近放置 |
| 性能 | GeoNames 内存查找表 ~100ms 初始化，单次匹配 <1ms，不影响用户体验 |
| 前端改动 | 零 |

## 九、实施路径

| Phase | 内容 | 依赖 |
|---|---|---|
| **Phase 1** | GeoResolver + GeoNames CN + 精确匹配 + 后缀剥离 + 墨卡托投影 + 集成到 visualization_service | 无 |
| **Phase 2** | LLM 辅助匹配未解析地名 | Phase 1 |
| **Phase 3** | 省界轮廓底图生成（可选） | Phase 1 |
| **Phase 4** | CHGIS 历史地名库集成（可选） | Phase 1 |
