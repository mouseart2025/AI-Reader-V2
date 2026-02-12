# LLM 驱动的小说世界地图生成系统

## 技术研究报告

*从文本空间提取到交互式地图渲染的全栈技术方案*

---

## 概述

**本地 7B-14B 参数的 LLM 可以从小说中提取空间数据，驱动真实世界地理编码地图和程序化生成的奇幻世界，并在浏览器中进行游戏级交互渲染。** 完整技术栈组合了 Qwen2.5（通过 Ollama 运行）进行信息提取、约束满足算法进行虚构世界布局、CHGIS 等历史地名辞典处理中文小说、以及 MapLibre GL JS + Three.js 实现游戏品质的渲染效果。

目前没有单一开源项目能完成端到端的全流程，但每个组件都已存在成熟的开源库。架构上最相关的前沿工作是 Autodesk AI Research 的 **PlotMap**（2024），它解决了最难的部分——使用 12 种空间约束类型和进化优化算法，将故事中的地点放置到程序化地图上。

---

## 端到端流水线：五个阶段

系统架构流经五个独立阶段，每个阶段有明确的输入/输出边界：

```
小说文本 (.txt/.epub)
  → [1] 分块与预处理 (LlamaIndex + jieba/HanLP)
  → [2] LLM 空间信息提取 (Ollama + Qwen2.5, 结构化 JSON 输出)
  → [3] 世界模型构建 (知识图谱 + 约束求解)
  → [4] 地图生成 (地理编码 或 程序化地形生成)
  → [5] 浏览器渲染 (MapLibre GL JS + Three.js, 交互式叠加层)
```

**阶段 1：文档预处理。** LlamaIndex 的 `IngestionPipeline` 配合 `SentenceSplitter`（块大小 512-1024 token，100 token 重叠）支持中英文。中文文本使用 jieba（可加载自定义虚构地名词典）进行分词，HanLP 2.x 提供完整的分词+词性标注+命名实体识别流水线，在 MSRA 中文 NER 基准上达到 **95.2% F1**。共指消解（通过 spaCy 的 coreferee 插件）对小说至关重要——"那座城堡"、"那个要塞"和"它"可能都指向同一个地点。

**阶段 2：LLM 空间提取。** 使用 LLM 将地点、空间关系、地形描述和角色移动路径提取为结构化 JSON。建议采用两遍策略：第一遍按章节提取实体，第二遍跨章节聚合去重。Kineviz 的实体提取研究表明，包含 10 个以上 few-shot 示例和 "explanation" 字段（强制思维链推理）可以显著提高小模型的准确性。

**阶段 3：世界模型构建。** 根据小说是否对应真实地理而分支。现实世界设置通过地理编码将地名解析为坐标；虚构世界则将提取的空间约束（如"A城在B山脉以北，靠近一条河"）输入约束满足求解器，计算出合理的二维布局，然后作为程序化地形生成的种子。

**阶段 4-5：地图生成与浏览器渲染。** 生成视觉地图并在浏览器中交互渲染，包括可点击的章节标注、角色路径动画和随故事进展的战争迷雾渐进揭示。

---

## 使用本地 LLM 和中文 NLP 进行空间提取

### 推荐模型

**Qwen2.5-7B-Instruct 是此任务的最佳模型**——强大的中文能力、原生 JSON 结构化输出、128K 上下文窗口，在 Q4_K_M 量化下仅需约 4.5 GB 显存即可在 8GB GPU 上流畅运行。如果有 12GB+ 显存，**Qwen2.5-14B-Instruct** 在推理质量上有显著提升。两者均为 Apache 2.0 许可，通过 Ollama 一行命令即可获取：

```bash
ollama pull qwen2.5:14b
```

### 结构化输出

**结构化输出是关键能力。** Ollama 现已支持通过 `format` 参数进行约束解码，接受 Pydantic JSON Schema，保证输出格式良好。`instructor` 库（python-useinstructor.com）增加了自动重试、验证和模式选择。对于批量处理整本小说，vLLM 通过 xgrammar 提供 **10 倍更快**的 JSON 约束解码速度，但需要 Linux + CUDA 环境。

提取 Schema 应捕获四种实体类型：

```python
class SpatialExtraction(BaseModel):
    locations: list[Location]        # 名称、类型、地形、描述
    relationships: list[Relationship] # 源、目标、关系 (north_of, near, contains...)
    routes: list[Route]              # 起点、终点、途经点、沿途地形、角色
    regions: list[Region]            # 名称、包含地点、描述
```

### 虚构地名的命名实体识别

**GLiNER**（github.com/urchade/GLiNER）是突出工具——一个零样本 NER 模型，推理时可指定自定义标签如 `fictional_city`、`mountain`、`river`。它是 BERT 大小的模型（可在 CPU 上运行），比基于 LLM 的 NER 便宜几个数量级，且可微调。**NuNER_Zero** 比 GLiNER-large 高出 3-4.5%，且能处理长实体跨度。

不过对于中文文本，2025 年的研究表明 LLM 在所有指标上都优于传统 NER 工具，因此直接使用 Qwen2.5 提取是中文小说最实用的选择。

### 中文 NLP 的特殊挑战

古典或半古典中文（常见于历史/修仙小说）缺乏词边界，使用古字会使现代分词器混乱。最佳方案有三种：

1. 字符级 BERT 模型跳过分词
2. 直接用 Qwen2.5 同时做分词和提取
3. jieba 加载预填虚构地名的自定义词典

**HanLP 2.x**（github.com/hankcs/HanLP，36K+ stars）提供最完整的中文 NLP 流水线，支持联合多任务学习。

### 知识图谱构建

Neo4j 配合 `neo4j-graphrag-python` 包提供 `SimpleKGPipeline`，可自动从文本中提取实体和关系。图谱模式应显式建模空间关系：

```cypher
(:Location)-[:NORTH_OF]->(:Location)
(:Character)-[:TRAVELS_TO {chapter, sequence}]->(:Location)
```

原型阶段用 Python 的 NetworkX 即可，生产环境迁移到 Neo4j。

---

## 基于叙事约束的程序化世界生成

最具技术挑战性的部分是生成一个尊重文本空间关系的合理奇幻地图。有三种方案，按复杂度递增：

### PlotMap（Autodesk AI Research, 2024）

最直接相关的系统。它定义了 **12 种约束类型**用于故事到地图的布局：基于距离的（近/远）、方向性的（东/南/西/北）、基于地形的（在海岸、在森林中）和分隔约束（被海洋或山脉分隔）。使用 **CMA-ES**（协方差矩阵自适应进化策略）在 Voronoi 地形上优化地点放置。系统显式使用 LLM 从自由文本中推导空间约束——正是我们需要的流水线。**已开源：github.com/AutodeskAILab/PlotMap。**

### Azgaar 奇幻地图生成器

（github.com/Azgaar/Fantasy-Map-Generator，5.3K stars，MIT 许可）最完整的开源程序化世界生成器。通过分层噪声的 Voronoi 多边形生成地形，然后模拟生物群落（温度/降水量）、河流、聚落（按地理评分）、道路（A* 寻路）、政治边界、文化和宗教。支持导出为 JSON 和 GeoJSON。2025 年 12 月的 PR 已添加 Ollama 集成。局限是作为交互式 Web 工具设计——程序化使用需要浏览器自动化（Puppeteer/Playwright）或提取其生成算法。

### MapGen4（Red Blob Games）

由 Amit Patel 开发，提供最干净的算法基础。TypeScript 编写，Apache v2 许可。使用泊松圆盘采样 → Delaunay/Voronoi 对偶网格 → 基于噪声的海拔 → 实时侵蚀/降雨模拟。架构清晰分离"约束"（图结构）和"多样性"（噪声），是最适合约束驱动生成的代码库。

### 推荐组合方案

使用 PlotMap 的约束满足算法计算命名地点的抽象 (x,y) 位置，然后以这些位置为锚点，驱动 Azgaar 风格或 MapGen4 风格的程序化生成——偏置高度图，使山脉、海岸线和河流出现在叙事需要的地方。

**核心程序化算法：** Perlin/Simplex 噪声生成高度图（以分形布朗运动分层，可配置倍频、持续度、间隙度）；Voronoi 图（通过 Delaunator，MapBox 的快速 Delaunay 库）生成区域多边形；水力侵蚀模拟产生真实地形（`dandrino/terrain-erosion-3-ways` 仓库实现了三种技术）；板块构造模拟（PlaTec/WorldEngine）用于大陆尺度的真实感。

---

## 真实世界与中国历史地点的地理编码

对于设定在真实地理中的小说，流水线必须将地名（包括古代、已变更或模糊的地名）解析为坐标。中文历史小说是最难的场景，因为行政区划在历朝历代被完全重构（如长安变为西安，府界频繁变动）。

### 中国历史地理信息系统（CHGIS）

由哈佛大学/复旦大学维护，覆盖**公元前 221 年至公元 1911 年**的时间序列数据。每条记录有时间有效性（begin_year, end_year）、多种名称形式（简体、繁体、拼音、威妥玛拼音）和精确坐标。版本 6 数据可从 Harvard Dataverse 免费获取（GIS shapefile 格式）。关联的 **TGAZ（时间地名辞典）** 位于 maps.cga.harvard.edu/tgaz，提供机器可读 API，接受 UTF-8 中文字符查询，返回带时间有效性的坐标（RDF/Linked Data 格式）。

### 地理编码优先级链

中文历史小说的地理编码级联应为：

```
CHGIS/TGAZ（含朝代上下文）
  → 世界历史地名辞典（220 万+ 地点）
  → GeoNames（1200 万+ 地名）
  → 高德地图 API（免费层：2000 请求/天，中国境内精度最佳）
  → Nominatim（最终兜底）
```

**重要实现细节：** 中国地图 API 返回偏移坐标（高德/Google 中国使用 GCJ-02，百度使用 BD-09），而非标准 WGS-84。`coordtransform` npm 库或 `eviltransform` 可处理转换。

### 历史地图叠加

**Allmaps** 平台（allmaps.org）是主要工具，使用 IIIF 协议通过 WebGL 在客户端进行历史地图图像的地理配准，有 MapLibre、Leaflet 和 OpenLayers 的插件。David Rumsey 地图收藏（20 万+ 地图，IIIF 兼容）和 Old Maps Online（50 万+ 地图）提供素材来源，包含可搜索的中国历史地图子集。

---

## 浏览器端游戏级渲染

真实世界地理和虚构世界的双重需求指向 **MapLibre GL JS + Three.js 混合架构**作为最优渲染方案。

### MapLibre GL JS

（BSD-2-Clause，github.com/maplibre/maplibre-gl-js）可同时处理两种模式。真实世界地图方面，渲染 OpenStreetMap 或 MapTiler 矢量瓦片，内置 3D 地形（`map.setTerrain()`）、最高 85° 的俯仰角和流畅的缩放/平移/旋转。奇幻地图方面，可加载自定义栅格瓦片（从生成的地图图像切片）或自定义矢量瓦片源，样式完全可编程。其自定义图层接口甚至允许将 Three.js 场景直接嵌入 MapLibre 的 WebGL 上下文中，并支持地形高程查询。

### Three.js 3D 地形引擎

为需要高度图渲染的奇幻世界提供 3D 地形引擎。工作流程：创建细分匹配高度图分辨率的 `PlaneGeometry`，通过顶点着色器采样高度图纹理置换顶点，应用纹理喷溅（通过 splat map 的 RGBA 通道实现草地/岩石/雪/水）。`THREE.Terrain` 包（npm: three.terrain.js）封装了 Diamond-Square、Perlin 等生成方法。水面渲染使用 Three.js 内置的 Water/Water2 对象实现反射和波纹效果。

### 关键交互功能实现

**可点击标注：** MapLibre markers 配合 `bindPopup()`，包含章节链接；或 GeoJSON 图层配合 `onEachFeature` 点击处理。弹出窗口显示地点名称、描述、相关章节摘录和角色存在信息。

**角色路径动画：** `leaflet.motion`（Igor-Vladyka/leaflet.motion）提供最丰富的 API——沿路径移动的标记动画折线，可配置速度、顺序分段和暂停/恢复。MapLibre 下通过 `requestAnimationFrame` 沿 GeoJSON LineString 插值实现。

**战争迷雾（Fog of War）：** 最有效的方案使用多边形遮罩——一个覆盖整个世界的 `L.polygon`，已探索区域作为孔洞切出，随故事进展动态扩展。或者使用 WebGL 着色器方案，将迷雾渲染为全屏四边形，配合渐进更新的纹理遮罩。

**奇幻地图视觉风格：** CSS 滤镜（`sepia(0.8) contrast(1.1) saturate(0.7)`）在地图容器上产生羊皮纸效果。SVG `<filter>` 元素添加纸张纹理（`feTurbulence`）和手绘颤动效果（`feDisplacementMap`）。自定义 MapLibre 样式 JSON 配合大地色系调色板和衬线/手写字体完成整体美学。

### 性能优化

WebGL 在桌面端可实现 60fps 复杂地形渲染。关键优化包括：基于瓦片的加载（仅渲染可见瓦片）、通过四叉树细分的 LOD（近处高精度、远处低精度）、GPU 实例化处理重复对象（树木、建筑使用 `THREE.InstancedMesh`）、压缩纹理（KTX2/Basis Universal）以及 Web Workers 卸载地形生成。1024×1024 的高度图配合匹配的几何细分对实时渲染是可行的。

---

## 推荐技术栈

| 层级 | 主选方案 | 备选方案 | 备注 |
|------|----------|----------|------|
| **LLM 推理** | Ollama + Qwen2.5-14B (Q4_K_M) | vLLM（更高吞吐） | 8-9 GB 显存 |
| **结构化输出** | Pydantic + Ollama `format` 参数 | Instructor / Outlines | 保证 JSON Schema 合规 |
| **中文 NER** | HanLP 2.x + Qwen2.5 直接提取 | GLiNER（零样本多语言） | LLM 优于传统 NER |
| **文档处理** | LlamaIndex IngestionPipeline | LangChain extraction chains | SentenceSplitter, 1024 token |
| **共指消解** | Coreferee (spaCy 插件) | Maverick-coref | 小说必需 |
| **知识图谱** | Neo4j + neo4j-graphrag-python | NetworkX（原型阶段） | 空间关系 Schema |
| **历史地理编码** | CHGIS/TGAZ → WHG → GeoNames | Mordecai 3（仅英文） | 时间序列中国地理 |
| **现代地理编码** | Nominatim（免费）或 Mapbox | 高德地图 API（中国特化） | 注意 GCJ-02 坐标偏移 |
| **约束求解** | PlotMap (CMA-ES) | 力导向图 / 模拟退火 | 12 种空间约束类型 |
| **程序化地形** | Azgaar FMG (JSON 导出) | MapGen4 (TypeScript) | WorldEngine 用于仿真 |
| **地图渲染** | MapLibre GL JS | Leaflet.js（更简单, 2D） | 真实+自定义瓦片 |
| **3D 地形** | Three.js + THREE.Terrain | Babylon.js（更多内置） | 高度图+纹理喷溅 |
| **路径动画** | leaflet.motion | deck.gl TripLayer | 暂停/恢复, 可配速度 |
| **历史地图叠加** | Allmaps + IIIF | Georeferencer (MapTiler) | 客户端 WebGL 校正 |
| **Web 框架** | React/Vue + FastAPI 后端 | 纯 HTML/JS 原型 | Python 后端驱动 LLM |

---

## 已有项目与研究现状

多个现有项目涉及该系统的部分功能，但没有一个覆盖完整流水线：

### StoryMapJS

Knight Lab（西北大学）开发，最接近现有的地点锚定叙事工具。支持超大图片模式用于自定义奇幻/历史地图图像，输出 JSON，开源。可作为 LLM 生成的空间数据的展示层，但没有提取或生成能力。

### MapStory（arXiv:2505.21966, 2025）

架构上最相关的学术工作——使用 LLM 规划器-研究者 Agent 将自然语言脚本转换为 GeoJSON 操作和地图动画。其场景分解和对话式研究者方法直接启发了提取流水线的设计。

### Word2World（arXiv:2405.06686, 2024）

展示了 LLM 同时作为叙事生成器和地图设计师，从故事产生基于瓦片的 2D 游戏地图。其扩展 Word2Minecraft（2025）将范围拓展到 3D 环境。

### 港科大"郑和航海"项目

中文特定的案例——一个多层地图混合虚构和历史的 16 世纪地理，配合 NLP 推导的角色互动网络。

### 学术基础

苏黎世联邦理工学院的"欧洲文学地图集"项目定义了五种核心空间元素：设置（setting）、投射空间（projected space）、路线（route）、航路点（waypoint）、标记（marker）。2021 年"知识图谱叙事制图"论文提出了基于 KG 的地理增强用于 GIS 集成。

**当前空白：** 没有现有项目组合了 LLM 空间提取 → 约束满足布局 → 程序化地形生成 → 交互式浏览器渲染。这是一个真正的机会。最接近的组合是将 PlotMap 的约束求解器 + Azgaar 的地形生成器 + MapLibre 的渲染器组装起来，用 Qwen2.5/Ollama 驱动提取。

---

## 硬件需求

**最低配置：** 16 GB 内存 + 8 GB 显存 GPU（RTX 3060/4060），可运行 Qwen2.5-7B Q4 量化。

**推荐配置：** 32 GB 内存 + 12-16 GB 显存（RTX 4070/4080），可运行 Qwen2.5-14B。

纯 CPU 推理通过 llama.cpp 可行，但速度慢 5-10 倍——对于批量小说处理可以接受。

---

## 结论与建议

小说到地图的流水线在技术上是完全可行的，所有组件都以开源库的形式存在。最高价值的起点是**提取到约束的桥梁**：让 Qwen2.5 可靠地从小说章节中产生结构化的空间 JSON，然后将这些约束输入 PlotMap 的 CMA-ES 求解器或力导向图布局。这是最需要原创工程工作的地方——上游（文档处理）和下游（渲染）都使用成熟的库。

三个设计决策将最大程度影响最终效果：

**第一，虚构/现实检测门。** 流水线早期判断是对地名辞典进行地理编码还是程序化生成——混合模式（带有真实地理和虚构地点的历史奇幻小说）需要同时走两条路径。

**第二，约束 Schema。** 定义空间关系如何在提取和生成之间表示的系统核心数据契约——PlotMap 的 12 种约束分类法是最强的起点。

**第三，渐进揭示。** 将战争迷雾与章节进度关联，将地图从静态参考转变为叙事体验——这是项目从单纯的可视化工具变为真正新颖的阅读伴侣的关键所在。

对于中文小说，Qwen2.5（阿里巴巴开发，同级别中文性能最强）与 CHGIS/TGAZ（公元前 221 年至 1911 年中国历史地理）的组合创造了一个独特而强大的技术栈，这是现有项目都未曾组装过的。GitHub 上的 `luoxuhai/chinese-novel` 中国古典小说数据库提供了现成的测试语料。
