# 路线图 Roadmap

> 最近更新：2026-11-19 · 当前版本 **v0.87.0**（正式版；macOS Apple Silicon / Windows x64 安装包已发布）
> 这是方向性计划，不是承诺：优先级会随用户反馈和实际质量数据调整。

## 版本节奏

- **每 2–3 周**一个 patch / minor
- 每个版本必须：后端 + 前端测试全绿、内置 demo 数据可正常打开、三平台安装包冒烟
- 质量优先于新功能：只有当「抽取准确率 / 成本 / 速度」有实测改善时才进版

## 近期

### v0.85.1 · 安全收口（已发布 2026-10-30）
- **依赖告警 65 → 0**：`requires-python` 提到 3.11（Python 3.10 已 EOL），`uv.lock` 里 py<3.11 的 `pillow 12.3.0` 兜底分支消失、Pillow 13 全域生效；filelock 4.2.4、setuptools 84.2.1、opentelemetry 1.45.1、vitest 4.1.12、`@hono/node-server` 2.1.5。
- **CodeQL 接入**：python + javascript-typescript 双语言，首轮 16 条基线告警已逐条 triage。
- 安装包随本版重出（macOS Apple Silicon / Windows x64）。

### v0.87.0 · 自定义 Embeddings + 模型名单换代（已发布 2026-11-19）
- **自定义 embedding 渠道（#20）**：`local` / OpenAI 兼容端点 / Ollama 三选一，API Key 独立槽位，远程配置不完整时 fail-loud；换模型后旧向量索引不兼容，本期只提示重建。
- **云端模型名单换代（#28）**：预设迁到 `model_catalog.py` 单一源，逐家对官方来源核实（Moonshot / DeepSeek 用真实 key 实测 `/models`）；新增 `ModelCapability` 按模型能力构造请求（K3 不下发 temperature、改 `max_completion_tokens`），未知模型回落旧行为。
- **错误透传**：上游 400/402/413/429 原样传给前端，`apiFetch` 开始读 `detail`；「验证」在已选模型时发一次真实 completion，参数不兼容当场暴露。
- **安全**：hono / @hono/node-server / opentelemetry-api 升级，漏洞告警 8 → 1。

### v0.86.0 · 导出修复 + 预扫描合并（已发布 2026-11-19）
- **`.air` 导出修复（#27）**：`WorldStructure.virtual_locations` 是 `set`，python 模式 `model_dump()` 原样带入导出包 ⇒ `json.dumps` 抛 `TypeError`，**v0.77.0 起约 2 个月、9 个正式版**的 `.air` 与 `format=json` 导出全部 500。备份导出同因静默产出空包（`novel_count: 0`），现已改为失败可见、全部失败即报错。
- **测试脚手架根因**：`mock_get_connection` 曾把 `_build_precomputed` 换成返回 `None` 的桩，1775 个测试未覆盖该分支；新增 `real_precompute_connection` 与 4 个回归用例。
- **预扫描候选合并（#18）**：`entity_overrides` 覆盖层合并同人候选，可逐条撤销。
- **ruff 目标 py310 → py311（#12）**、oauthlib 4.0.1（high）。

### v0.85.2 · 截断修复 + 开关实测（已发布 2026-10-31）
- **DeepSeek 长章截断修复**：客户端对 `deepseek` 的 8192 输出上限提到 16384，15 章实测截断 7/15 → 0/15，**事件抽取 +46%**。
- **优化开关实测（三轮复核）**：场景合并 −13.6% token / −13% 成本（−9.3%~−17.5%），关系数在噪声内、场景数 −22.8%；窗口并发 k=2 **2.16x**、k=3 **2.49x**。
- **发版流程固化**：`RELEASING.md`（Release / Mirror 两个 workflow 处于 disabled，推 tag 不会出包）。
- 剩余：chromadb 上游修复跟踪（#11，月度巡检）。

## 中期

| 版本 | 方向 | 目标窗口 | 说明 |
|---|---|---|---|
| v0.86 | **人工修正闭环** | 11 月下旬 – 12 月中 | 预扫描候选合并 ✅（#18）；事件级编辑 #17；词条编辑与「保存位置」范围待确认（#19）；#50 导出保存位置已在 v0.83 完成 |
| v0.87 | **学术与复现** | 12 月下旬 – 1 月中 | DOI / CITATION、gold 数据版本化、Docker 一键部署（#27） |
| v0.88 | **地图余项** | 2 月 | 封神主世界地理化（天界/龙宫保留为层标签）、跨语言同名地点消歧、逐章“诺第留斯号”航线/船内子图 |

## 探索中（不排期）

- 地图瓦片化（按缩放级别渲染高分辨率地形）——已有原型，仍存在与主烘焙图的数值偏差
- 多语言界面（#11）——取决于使用场景是否出现英文需求
- 动态知识库同步（#56）
- 多小说批量分析队列（当前按单本任务 + 失败章节重试；批量的可见/可恢复需要独立设计）

## 明确不做

- 云端托管本体的 SaaS（本项目定位是本地优先；数据不上传）
- 账号体系 / 遥测
- 未经实测验证的「更聪明的 prompt」式新功能

## 如何影响方向

- 提 issue 时说明**具体小说 + 具体章节 + 期望结果**，比抽象需求更容易被排进版本
- 标注为 `enhancement` 的 issue 会参与每版开工时的取舍
- 质量类问题（抽取错误、层级错乱、地图错位）优先级高于功能请求

## License

AGPL v3。桌面版二进制可在[下载页](https://ai-reader.cc/#download)获取；对应源代码见[公开仓库](https://github.com/mouseart2025/AI-Reader-V2)的对应 tag。
