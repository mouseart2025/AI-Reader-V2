# AI Reader V2 — v0.14 版本需求文档（PRD 修订稿）

> 主题：导入可靠性 + 文本卫生 + 基准测试体系
> 版本：vNext-PRD-002（修订稿）
> 基于：原始 PRD vNext-PRD-001 + 代码库全量审查
> 日期：2026-02-15

---

## 1. 背景

### 1.1 现有能力（比预期更完善）

当前导入系统已具备：
- **5 种正则切分模式**：chapter_zh / section_zh / numbered / markdown / separator
- **自动选择**：按匹配数选最优模式
- **自定义正则**：用户可输入任意正则
- **前端预览**：5 阶段状态机（选择→上传→**预览**→去重→确认）
- **实时重切**：切换模式即时更新章节列表
- **非正文过滤**：6 条形态学规则 + 对话检测
- **章节排除**：复选框排除 + 可恢复
- **卷识别**：显式卷标记 + 隐式编号重置检测
- **编码检测**：UTF-8 / GB18030 自动识别
- **警告系统**：仅 1 章、超大章、可疑章、大文件

### 1.2 核心缺口（只有两个）

1. **没有 fallback 切分**：当所有 5 种正则都匹配不到 ≥2 次时，直接返回"全文"单章。缺少按字数切分的退化策略，也缺少英文标题和启发式标题检测。
2. **没有文本卫生检测/清理**：下载的网络小说常含 URL/推广/水印/分割线，污染分析质量。完全缺失。

### 1.3 工程诉求

已克隆 BlankRain/ebooks（1500+ 本 TXT）作为基准集。需要一个**零 LLM 调用**的批量测试脚本，评估切分改动的全局影响。

---

## 2. 目标与成功标准

### 目标

- **O1**：任意 TXT 导入都能生成可用章节结构（即使没有章节标题）
- **O2**：切分失败时系统能解释原因并推荐修复操作
- **O3**：检测到文本杂质时可预览、可清理、可撤销
- **O4**：用 1500 本基准集做持续回归

### 成功标准

| 指标 | 首轮目标 | 迭代后目标 |
|------|---------|-----------|
| 基准集可用率 | ≥ 90% | ≥ 95% |
| 仅 1 章超大章（>50k）比例 | ≤ 5% | ≤ 2% |
| 用户在预览页修复次数 | ≤ 3 次点击 | ≤ 2 次 |

**"可用"定义**：满足任一：
- A) `chapter_count ≥ 3` 且 `max_chapter_words ≤ 50,000`
- B) `chapter_count ≥ 2` 且 `total_words ≤ 80,000`（短篇/中篇）
- C) fallback 模式启用后：`chapter_count ≥ 5` 且各章字数 [2000, 20000] 覆盖率 ≥ 80%

---

## 3. 产品原则

1. **规则优先**：能正则/启发式解决的不用 LLM
2. **始终可用**：没有章节标题也能读、能问、能分析（退化策略明确）
3. **可解释可撤销**：切分/清理都能回退
4. **基准集为第一公民**：切分规则改动必须跑回归

---

## 4. 范围声明

### 本版本包含

- 章节切分引擎增强（新增 3 种模式 + fallback + 诊断）
- 文本卫生检测与清理
- 1500 本基准测试脚本
- 预处理增强（BOM/空白/空行）

### 本版本不包含（后续版本）

- 分析版本管理 + Diff + 回滚（→ v0.15+）
- 待审队列 Human-in-the-loop（→ v0.15+）
- 分析档位封装 Fast/Balanced/Accurate（→ v0.15+）
- 一致性审计工作台（→ v0.16+）
- Series Bible 导出（→ 独立版本）
- 批量导入 + 批量报告（→ P2）
- 高级净化规则包 + Legado 导入（→ P2）
- 章节合并/拆分/重命名 UI（→ P1 可选）

---

## 5. 功能需求

### Milestone 1：章节切分永不失败

#### S1. 基准测试脚本

**文件**：`backend/scripts/benchmark_import.py`

- 遍历指定目录下所有 .txt 文件
- 对每个文件：`decode_text()` → `split_chapters()` → 记录结果
- 输出 CSV：文件名、文件大小、编码、章节数、最大章字数、平均章字数、匹配模式、诊断标签
- 输出汇总 JSON：可用率、失败原因 Top 20 分布、失败样本前 20 列表
- 零 LLM 调用
- 验收：脚本可在 1500 本上跑通，输出正确统计

#### S2. 首次基准测试

- 用 S1 脚本对 1500 本 TXT 跑 baseline
- 记录当前可用率和失败原因分布
- 人工抽样检查 20 个失败样本，归类根因
- 验收：有 baseline 报告，失败原因已归类

#### S3. 预处理增强

**文件**：`backend/src/utils/text_processor.py` + `chapter_splitter.py`

- UTF-8 BOM 显式去除（`\xef\xbb\xbf`）
- 全角空格→半角空格转换（用于标题匹配前）
- 连续 3+ 空行压缩为 2 空行
- 验收：包含 BOM 和全角空格的测试文件能正确处理

#### S4. 新增 `chapter_en` 模式

**文件**：`backend/src/utils/chapter_splitter.py`

正则：
```
^\s*(?:CHAPTER|Chapter|PART|Part|PROLOGUE|Prologue|EPILOGUE|Epilogue)\s*[\d\sIVXLCDM]*[\.:\s—-]*(.*)$
```
- 支持 CHAPTER 1 / Chapter One / Part I / Prologue / Epilogue 等
- 插入 `_PATTERNS` 列表中 markdown 之前
- 验收：英文小说 TXT 能正确按 CHAPTER 切分

#### S5. 新增 `heuristic_title` 模式

**文件**：`backend/src/utils/chapter_splitter.py`

启发式规则（行为标题的条件，需**全部满足**）：
- 行长 ≤ 30 字符（去除空白后）
- 不含句号、逗号、分号、冒号等正文标点（`。，；：！？…`）
- 前后有空行（或位于文件开头）
- 不是纯数字/纯标点/纯空白

实现为双遍扫描：
1. 第一遍：标记所有候选标题行
2. 第二遍：过滤间距过近（< 500 字）或过远（> 50000 字）的候选
3. 保留均匀分布的候选作为切分点

- 插入 `_PATTERNS` 之后作为独立策略（不参与正则优先级选择，仅在正则模式全部失败时启用）
- 验收：无标准章节标题但有短标题行的 TXT 能正确切分

#### S6. 新增 `fixed_size` fallback

**文件**：`backend/src/utils/chapter_splitter.py`

当所有模式（含启发式）均失败时：
- 按 ~8000 字/章切分（可配置）
- **在段落边界切分**（找最近的空行），不在句中截断
- 章节标题自动生成："第 1 段"、"第 2 段"…
- 触发条件：最佳模式匹配 < 2 次，**或**仅 1 章且 > 30,000 字

修改 `split_chapters()` 的 fallback 逻辑（当前 line 147-156）：
```python
# 现有：返回"全文"单章
# 改为：调用 fixed_size fallback
```

- 验收：无任何章节标题的纯文本 TXT 不再返回"全文"单章

#### S7. 切分诊断标签

**文件**：`backend/src/services/novel_service.py`

在 `parse_upload()` 返回中新增 `diagnosis` 字段：

```python
class SplitDiagnosis:
    tag: str          # 结构化标签
    message: str      # 面向用户的中文说明
    suggestion: str   # 推荐操作文案
```

诊断标签：
| 标签 | 触发条件 | 推荐操作 |
|------|---------|---------|
| `OK` | 切分正常 | 无 |
| `NO_HEADING_MATCH` | 所有正则 0 匹配 | "已自动按段落切分，您也可以尝试自定义正则" |
| `SINGLE_HUGE_CHAPTER` | 仅 1 章且 > 30k 字 | "建议使用按字数切分" |
| `HEADING_TOO_SPARSE` | 匹配 < 5 次但文本 > 100k 字 | "检测到的章节较少，可能遗漏了部分章节标题" |
| `HEADING_TOO_DENSE` | 章均字数 < 500 | "章节过多过短，可能误将正文识别为标题，建议切换模式" |
| `ENCODING_SUSPECT` | 解码使用 replace 且替换字符 > 0.5% | "文件编码可能异常，部分内容可能显示为乱码" |
| `FALLBACK_USED` | 使用了 fixed_size 或 heuristic 模式 | "未检测到标准章节标题，已按段落/字数自动切分" |

- 验收：各诊断标签在对应场景下正确触发

#### S8. 前端诊断展示增强

**文件**：`frontend/src/components/shared/UploadDialog.tsx`

- 在预览页显示诊断信息（tag + message + suggestion）
- 当 tag 为 `SINGLE_HUGE_CHAPTER` 或 `NO_HEADING_MATCH` 时，显示醒目的"一键按字数切分"按钮
- 当 tag 为 `FALLBACK_USED` 时，显示蓝色提示条说明已自动退化
- 验收：失败场景下用户能看到原因和修复建议

#### S9. 第二次基准测试

- 用 S1 脚本在 S3-S8 完成后重跑 1500 本
- 对比 baseline：可用率变化、失败原因变化
- 验证达到首轮目标（≥ 90%）
- 验收：有对比报告，可用率显著提升

---

### Milestone 2：文本卫生检测与清理

#### S10. 杂质检测引擎

**文件**：`backend/src/utils/text_sanitizer.py`（新建）

5 类检测模式：

```python
class NoiseCategory(str, Enum):
    URL = "url"              # https://... / www.xxx.com / xxx.cn
    PROMO = "promo"          # 公众号/微信/QQ群/关注/下载APP/书友群
    TEMPLATE = "template"    # "本书由...整理" / "更多...请访问" / "手机用户请到...阅读"
    DECORATION = "decoration" # -------- / ======== / ※※※※※
    REPEATED = "repeated"    # 每章末尾相同的 N 行（跨 ≥50% 章节出现）

class SuspectLine:
    line_num: int
    content: str
    category: NoiseCategory
    confidence: float  # 0.0-1.0

class HygieneReport:
    total_suspect_lines: int
    by_category: dict[str, int]  # 按类型统计
    samples: list[SuspectLine]   # 前 10 个样例
```

- 前 4 类：纯正则/关键词匹配，逐行检测
- 第 5 类（repeated）：取每章最后 5 行，跨章节比对，出现在 ≥50% 章节中的行标记为重复尾注
- 验收：包含推广链接的 TXT 能正确检出

#### S11. 清理函数

**文件**：`backend/src/utils/text_sanitizer.py`

```python
def clean_text(text: str, report: HygieneReport, mode: str = "conservative") -> str:
    """
    conservative: 只删除 confidence ≥ 0.8 的行
    aggressive: 删除所有 suspect 行
    """
```

- 按行删除匹配行
- 返回清理后的文本
- 验收：清理后的文本不含被标记的杂质行

#### S12. 导入流程集成

**文件**：`backend/src/services/novel_service.py`

在 `parse_upload()` 中：
1. `decode_text()` → 原始文本
2. `detect_noise()` → 卫生报告
3. 若 `total_suspect_lines > 0`，将报告加入响应
4. 切分仍使用原始文本（不自动清理）

新增 API：`POST /api/novels/clean-and-resplit`
- 接收 file_hash + 清理模式
- 执行 `clean_text()` → `split_chapters()` → 更新缓存
- 返回新的章节预览

- 验收：导入含杂质 TXT 时响应中包含卫生报告

#### S13. 前端卫生报告展示

**文件**：`frontend/src/components/shared/UploadDialog.tsx`

在预览页，当卫生报告非空时：
- 显示橙色提示条："检测到 {N} 行疑似非正文内容（推广链接 {n1} / 站点模板 {n2} / ...）"
- 展开可查看前 10 个样例（带行号和匹配类型）
- "清理并重新切分"按钮 → 调用 clean-and-resplit API → 刷新章节列表
- 验收：用户能看到杂质报告、查看样例、一键清理

#### S14. 基准测试增加卫生列

**文件**：`backend/scripts/benchmark_import.py`

- CSV 新增列：noise_total, noise_url, noise_promo, noise_template, noise_decoration, noise_repeated
- 汇总新增：含杂质文件比例、各类杂质分布
- 验收：1500 本基准集的杂质统计可用

---

### Milestone 3（可选增强）

#### S15. 章节合并操作

前端预览页支持选中多个相邻章节 → 合并为一章。

#### S16. 章节重命名

前端预览页支持点击章节标题直接编辑。

#### S17. 编码异常详细诊断

当 `decode_text()` 使用了 `errors="replace"` 且替换字符数 > 0 时，在前端显示乱码字符数量和位置。

---

## 6. API 变更

### 新增/修改响应字段

`POST /api/novels/upload` — UploadPreviewResponse 新增：
```json
{
  "diagnosis": {
    "tag": "FALLBACK_USED",
    "message": "未检测到标准章节标题，已按段落自动切分",
    "suggestion": "您也可以尝试自定义正则表达式"
  },
  "hygiene_report": {
    "total_suspect_lines": 42,
    "by_category": { "url": 15, "promo": 20, "template": 5, "decoration": 2 },
    "samples": [
      { "line_num": 156, "content": "更多精彩请关注公众号XXX", "category": "promo", "confidence": 0.95 }
    ]
  }
}
```

### 新增端点

`POST /api/novels/clean-and-resplit`
```json
// Request
{ "file_hash": "abc123", "clean_mode": "conservative" }
// Response: 同 UploadPreviewResponse（更新后的章节列表）
```

`GET /api/novels/split-modes` 新增返回：
```json
["chapter_zh", "section_zh", "chapter_en", "numbered", "markdown", "separator", "heuristic_title", "fixed_size"]
```

---

## 7. 技术方案

### 7.1 改动文件清单

| 文件 | 改动类型 | Milestone |
|------|---------|-----------|
| `backend/src/utils/chapter_splitter.py` | 修改：+3 模式 + fallback + 预处理 | M1 |
| `backend/src/utils/text_processor.py` | 修改：BOM + 全角空格 | M1 |
| `backend/src/utils/text_sanitizer.py` | **新建**：杂质检测 + 清理 | M2 |
| `backend/src/services/novel_service.py` | 修改：诊断 + 卫生集成 | M1+M2 |
| `backend/src/api/routes/novels.py` | 修改：新端点 + 响应扩展 | M1+M2 |
| `backend/scripts/benchmark_import.py` | **新建**：基准测试脚本 | M1 |
| `frontend/src/api/types.ts` | 修改：新字段类型 | M1+M2 |
| `frontend/src/components/shared/UploadDialog.tsx` | 修改：诊断展示 + 卫生报告 | M1+M2 |

### 7.2 数据模型

无 DB schema 变更。所有新增字段在 API 响应层，不影响持久化。

### 7.3 向后兼容

- `diagnosis` 和 `hygiene_report` 为可选字段（None 时不影响已有逻辑）
- `split_chapters()` 函数签名不变，新模式通过 mode 参数传入
- 前端对新字段做可选处理（`?` / `??`）

---

## 8. 风险

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| `heuristic_title` 误将正文当标题 | 中 | 中 | 严格约束（≤30字、无标点、前后空行）+ 仅在正则全失败时启用 |
| `fixed_size` 在段落中间截断 | 低 | 中 | 在最近的空行处切分 |
| 杂质清理误删正文 | 低 | 高 | 默认只标记不删除；用户确认后才清理；保守模式 confidence ≥ 0.8 |
| 基准集含非小说文本 | 中 | 低 | 首次跑完后人工抽样分类，标记非小说文件 |
| 英文模式与 numbered 冲突 | 低 | 低 | chapter_en 优先级高于 numbered |

---

## 9. 验收检查表

### 导入验收
- [ ] 任意 TXT 导入后，章节数不为 0
- [ ] 若仅 1 章且 > 30k 字，触发 fallback 并显示诊断
- [ ] 用户点击"一键按字数切分"后得到合理章节
- [ ] 含英文 CHAPTER 标题的 TXT 能正确切分
- [ ] 无任何标题的纯文本不返回"全文"单章
- [ ] 包含 BOM 的 UTF-8 文件正常处理
- [ ] 全角空格不影响章节标题匹配

### 卫生验收
- [ ] 含推广链接的 TXT 显示卫生报告
- [ ] 报告包含匹配类型、数量、样例
- [ ] 点击"清理并重新切分"后章节列表更新
- [ ] 清理不影响原始文件（缓存层操作）

### 基准测试验收
- [ ] 脚本对 1500 本 TXT 跑通
- [ ] CSV 和汇总 JSON 输出格式正确
- [ ] 可用率统计与人工抽样一致
- [ ] 规则修改后可对比前后 diff
