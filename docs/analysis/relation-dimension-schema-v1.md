# 关系维度 Schema v1(冻结)

> 依据:PRD Epic 1(FR-1.1 / FR-1.4)+ 调研 A3 节(NCRE/CREDI,arXiv 2507.04852)。
> 状态:**v1 冻结**。任何取值增删、判定指引变更、派生映射调整,必须升版本号(v2)并新建文档,旧版本文档不改。
> 代码落点:`backend/src/models/chapter_fact.py::RelationshipFact`(三个 Optional 字段)、`backend/src/services/relation_utils.py::derive_category_from_dimensions`(维度→旧六类派生)。

## 1. 设计原则

旧 schema 用单一 `relation_type` + 六类 category(family / intimate / hierarchical / social / hostile / other)硬塞所有关系,文化特定关系(结拜、师门、辈分)无独立落位,导致类型级准确率低(水浒基线 28%)。v1 按 NCRE 思路拆为三个**平行维度**,每维独立判定:

- `polarity`:情感极性
- `rel_subtype`:细化关系类型(文化特定关系在此独立落位)
- `closeness`:亲疏程度

旧六类 category 不再人工标注,而是由 `rel_subtype` **派生**(见 §5),前端四视图零改动(NFR-3)。

## 2. `polarity` 取值表

| 取值 | 定义 | 判定指引 |
|---|---|---|
| `positive` | 双方关系总体友善、亲密、互利 | 结拜、婚恋、同盟、报恩默认 positive;存在明确敌意描述时除外 |
| `negative` | 双方关系敌对、仇恨、逼迫 | 敌对默认 negative;逼婚、情敌等逼迫/冲突关系归 negative |
| `neutral` | 事务性、无明确情感倾向,或文本未给出倾向 | 主从、君臣-上下级等制度性关系默认 neutral,除非文本明确写出亲疏好恶 |

## 3. `rel_subtype` 取值表(13 个,冻结)

每 subtype 一句话定义 + 一个中文小说例子(水浒/西游/红楼优先)。

| rel_subtype | 定义 | 例子 | 派生 category |
|---|---|---|---|
| `辈分-亲属` | 血缘或姻亲的辈分关系(含夫妻,夫妻为平辈姻亲) | 《红楼》贾母与贾宝玉:祖孙 | family |
| `结拜` | 无血缘者依仪式结为拟制血亲(义结金兰、拜把子) | 《水浒》宋江与武松结拜为义兄弟 | intimate |
| `婚恋` | 双向的恋爱/情人关系 | 《红楼》贾宝玉与林黛玉相恋 | intimate |
| `爱慕` | 单方面的恋慕、暗恋、求亲,未获对方回应 | 《红楼》贾瑞单恋王熙凤 | social |
| `师门-师徒` | 师父与弟子之间的垂直授受关系 | 《西游》唐僧与孙悟空:师徒 | hierarchical |
| `师门-同门` | 同一师门下的平辈关系(师兄弟、同门) | 《西游》孙悟空与猪八戒:师兄弟 | social |
| `主从` | 主人与仆从、雇佣与被雇佣的垂直关系 | 《红楼》贾宝玉与袭人:主仆 | hierarchical |
| `君臣-上下级` | 政治/组织内的垂直统属关系 | 《水浒》宋徽宗与高俅:君臣 | hierarchical |
| `同盟` | 为共同目标结成的对等联盟 | 《水浒》梁山泊与二龙山联手抗官军 | social |
| `朋友-社交` | 平辈友人、同僚、邻里、世交等横向社交关系 | 《水浒》鲁智深与林冲结为好友 | social |
| `恩怨-报恩` | 基于恩惠/救助的亏欠与报答关系 | 《水浒》金翠莲父女感念鲁智深救命之恩 | social |
| `敌对` | 仇恨、对抗、加害与被害关系 | 《水浒》林冲与高俅:迫害结仇 | hostile |
| `其他` | 以上 12 类均不覆盖的关系 | 《西游》观音灌溉人参果树一类叙事性关联 | other |

判定指引:

1. **先判文化特定关系**:结拜、师门(师徒/同门)、辈分-亲属优先判定,不得硬塞入"朋友"或"其他"。
2. **双向 vs 单向**:婚恋必须双向;单向一律 `爱慕`,不得升级为 `婚恋`(对应旧规则"爱慕 ≠ intimate")。
3. **垂直 vs 水平**:师门内,师徒是垂直(hierarchical),同门是水平(social);同门不得并入师徒。
4. **subtype 与 relation_type 的关系**:`relation_type` 仍是自由文本细类(父子、祖孙、嫂叔……),`rel_subtype` 是其上位维度槽位;二者不互相替代。

## 4. `closeness` 取值表

| 取值 | 定义 | 判定指引 |
|---|---|---|
| `close` | 日常密切往来、利益/情感强绑定 | 共同行动、互相托付、频繁同场互动 |
| `distant` | 名义上有关系但少有实际往来 | 仅提及名分、远亲、多年未见 |
| `unknown` | 文本信息不足以判定亲疏 | 默认取值;无证据时不猜 |

## 5. 维度 → 旧六类 category 派生映射(FR-1.4 实现依据)

| rel_subtype | category |
|---|---|
| 辈分-亲属 | family |
| 结拜 | intimate |
| 婚恋 | intimate |
| 爱慕 | social |
| 师门-师徒 | hierarchical |
| 师门-同门 | social |
| 主从 | hierarchical |
| 君臣-上下级 | hierarchical |
| 同盟 | social |
| 朋友-社交 | social |
| 恩怨-报恩 | social |
| 敌对 | hostile |
| 其他 | other |

映射规则(与 `relation_utils.py::derive_category_from_dimensions` 一致):

- v1 **仅由 `rel_subtype` 派生**;`polarity` / `closeness` 是正交维度,供视图与置信度使用,不影响 category。
- `rel_subtype` 缺失或超出上表取值时,派生函数返回 `None`,调用方必须回退到旧路径 `classify_relation_category(normalize_relation_type(...))`——保证无维度数据时行为与改动前逐字节一致(NFR-3)。
- 上表与 `_RELATION_CATEGORY` 中对应旧类型的归类保持一致(结拜=intimate、师兄弟/同门=social、爱慕=social、夫妻=family),避免新旧路径产出冲突。

## 6. 版本与变更

- v1 冻结日期:2026-08-26(W1)。
- 变更流程:新建 `relation-dimension-schema-vN.md` → 同步模型注释、`derive_category_from_dimensions` 映射表与测试 → PRD Epic 1 验收记录中注明版本。
