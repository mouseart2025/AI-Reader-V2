# 路线图 Roadmap

> 最近更新：2026-10-23 · 当前版本 **v0.82.0**
> 这是方向性计划，不是承诺：优先级会随用户反馈和实际质量数据调整。

## 版本节奏

- **每 2–3 周**一个 patch / minor
- 每个版本必须：后端 + 前端测试全绿、内置 demo 数据可正常打开、三平台安装包冒烟
- 质量优先于新功能：只有当「抽取准确率 / 成本 / 速度」有实测改善时才进版

## 近期

### v0.83 · 稳定与合规
- 依赖清账、安装包体积审计（当前 dmg 127MB，其中运行时 98MB）
- AGPL 源码获取路径明示（见下载页）
- 三平台安装包冒烟与文档校对

### v0.84 · 地图与回归发布
- 地图渲染改进（地形明暗、大陆/海陆分层、区域与标注的可读性）
- README / 文档刷新，公开路线图
- 面向社区的回归发布（含中英文更新说明）

## 中期

| 版本 | 方向 | 说明 |
|---|---|---|
| v0.85 | **成本与速度** | 分析单本章节的开销与耗时；批处理的可见与可恢复（[#78](https://github.com/mouseart2025/AI-Reader-V2/issues/78)、[#51](https://github.com/mouseart2025/AI-Reader-V2/issues/51)） |
| v0.86 | **学术与复现** | DOI / CITATION、gold 数据版本化、Docker 一键部署（[#27](https://github.com/mouseart2025/AI-Reader-V2/issues/27)） |
| v0.87 | **人工修正闭环** | 事件级编辑、词条编辑与设置保存位置、预扫描候选合并（[#66](https://github.com/mouseart2025/AI-Reader-V2/issues/66)、[#50](https://github.com/mouseart2025/AI-Reader-V2/issues/50)、[#42](https://github.com/mouseart2025/AI-Reader-V2/issues/42)） |

## 探索中（不排期）

- 地图瓦片化（按缩放级别渲染高分辨率地形）——已有原型，仍存在与主烘焙图的数值偏差
- 多语言界面（[#11](https://github.com/mouseart2025/AI-Reader-V2/issues/11)）——取决于使用场景是否出现英文需求
- 动态知识库同步（[#56](https://github.com/mouseart2025/AI-Reader-V2/issues/56)）

## 明确不做

- 云端托管本体的 SaaS（本项目定位是本地优先；数据不上传）
- 账号体系 / 遥测
- 未经实测验证的「更聪明的 prompt」式新功能

## 如何影响方向

- 提 issue 时说明**具体小说 + 具体章节 + 期望结果**，比抽象需求更容易被排进版本
- 标注为 `enhancement` 的 issue 会参与每版开工时的取舍
- 质量类问题（抽取错误、层级错乱、地图错位）优先级高于功能请求

## License

AGPL v3。桌面版二进制可在[下载页](https://ai-reader.cc/#download)获取；对应源代码见仓库，或按下载页说明书面申请。
