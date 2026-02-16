# Epic 15: 导入可靠性 + 文本卫生 + 基准测试

任意 TXT 文件导入都能生成可用章节结构（即使没有章节标题），系统能检测和清理文本杂质，通过 1500 本基准集持续回归验证切分质量。

**依赖:** Epic 1（需要上传/切分基础设施）
**PRD:** `_bmad-output/prd-v0.14-import-quality.md`

---

## Milestone 1：章节切分永不失败

### Story 15.1: 基准测试脚本

As a 开发者,
I want 一个零 LLM 的批量测试脚本,
So that 切分规则修改后能用 1500 本 TXT 做回归测试。

**Acceptance Criteria:**

**Given** 指定目录下有 .txt 文件
**When** 运行 `python backend/scripts/benchmark_import.py <目录路径>`
**Then** 对每个文件执行 `decode_text()` → `split_chapters()` → 记录结果
**And** 输出 CSV：文件名、文件大小、编码、章节数、最大章字数、平均章字数、匹配模式
**And** 输出汇总 JSON：可用率、失败原因 Top 20、失败样本前 20
**And** 零 LLM 调用

**技术说明:**
- 新建 `backend/scripts/benchmark_import.py`
- 复用 `text_processor.decode_text()` + `chapter_splitter.split_chapters()`
- 可用定义：章节数 ≥ 3 且最大章字数 ≤ 50,000；或章节数 ≥ 2 且总字数 ≤ 80,000

---

### Story 15.2: 首次基准测试（手工步骤）

用 15.1 脚本对 1500 本 TXT 跑 baseline，记录可用率和失败原因分布，人工抽样 20 个失败样本归类根因。

---

### Story 15.3: 预处理增强

As a 系统,
I want 对上传文本进行更完善的预处理,
So that BOM、全角空格、多余空行不影响章节标题匹配。

**Acceptance Criteria:**

**Given** 上传的 UTF-8 文件带 BOM (`\xef\xbb\xbf`)
**When** `decode_text()` 处理
**Then** BOM 被自动去除

**Given** 章节标题前有全角空格
**When** `split_chapters()` 匹配标题
**Then** 全角空格不影响匹配（正则已有 `\s*` 前缀）

**Given** 文本中有连续 3+ 空行
**When** 预处理
**Then** 压缩为 2 空行

**技术说明:**
- 修改 `backend/src/utils/text_processor.py`：BOM 去除
- 修改 `backend/src/utils/chapter_splitter.py`：空行压缩

---

### Story 15.4: 新增 chapter_en 模式

As a 用户,
I want 英文小说的 CHAPTER/Part 标题能被正确识别,
So that 英文小说也能正确切分章节。

**Acceptance Criteria:**

**Given** TXT 文件包含 "CHAPTER 1" / "Chapter One" / "Part I" / "Prologue" / "Epilogue" 等标题
**When** 自动检测或手动选择 chapter_en 模式
**Then** 正确按英文章节标题切分

**技术说明:**
- 在 `_PATTERNS` 列表中 markdown 之前插入 `chapter_en` 模式
- 正则：`^\s*(?:CHAPTER|Chapter|PART|Part|PROLOGUE|Prologue|EPILOGUE|Epilogue)\s*[\d\sIVXLCDM]*[\.:\s—-]*(.*)$`

---

### Story 15.5: 新增 heuristic_title 模式

As a 系统,
I want 当所有正则模式都失败时能通过启发式规则检测短标题行,
So that 有短标题但不符合标准格式的 TXT 也能正确切分。

**Acceptance Criteria:**

**Given** TXT 文件的章节标题不符合任何正则模式但有明显短标题行
**When** 所有正则模式匹配 < 2 次
**Then** 启发式扫描短标题行：长度 ≤ 30 字、无正文标点、前后有空行
**And** 过滤间距过近（< 500 字）或过远（> 50,000 字）的候选
**And** 成功切分

**技术说明:**
- 实现为独立函数 `_heuristic_title_split()`
- 双遍扫描：标记候选 → 过滤不均匀候选
- 仅在正则全失败时作为退化策略

---

### Story 15.6: 新增 fixed_size fallback

As a 系统,
I want 当所有模式（含启发式）均失败时按字数自动切分,
So that 任何 TXT 都不会返回"全文"单章。

**Acceptance Criteria:**

**Given** 所有切分模式均失败
**When** fallback 触发
**Then** 按 ~8000 字/章在段落边界切分
**And** 章节标题自动生成："第 1 段"、"第 2 段"…
**And** 不在句中截断

**Given** 用户手动选择 fixed_size 模式
**When** 调用 re_split(mode="fixed_size")
**Then** 按 fixed_size 逻辑切分

**技术说明:**
- 替换 `split_chapters()` 中 line 147-156 的 fallback 逻辑
- 新增 `_fixed_size_split()` 函数
- 在段落边界（空行）切分

---

### Story 15.7: 切分诊断标签

As a 系统,
I want 在上传预览响应中返回切分诊断信息,
So that 用户能了解切分状况和推荐操作。

**Acceptance Criteria:**

**Given** 上传文件完成切分
**When** 返回预览响应
**Then** 包含 `diagnosis` 字段：tag + message + suggestion
**And** 诊断标签覆盖：OK / NO_HEADING_MATCH / SINGLE_HUGE_CHAPTER / HEADING_TOO_SPARSE / HEADING_TOO_DENSE / ENCODING_SUSPECT / FALLBACK_USED

**技术说明:**
- 修改 `backend/src/api/schemas/novels.py`：新增 SplitDiagnosis 模型
- 修改 `backend/src/services/novel_service.py`：在 parse_upload/re_split 中计算诊断
- 修改 `UploadPreviewResponse`：新增 diagnosis 可选字段

---

### Story 15.8: 前端诊断展示增强

As a 用户,
I want 在上传预览页看到切分诊断信息和推荐修复操作,
So that 切分失败时我知道原因和解决方法。

**Acceptance Criteria:**

**Given** 预览响应包含诊断信息
**When** 预览页渲染
**Then** 根据诊断 tag 显示对应提示：
  - `FALLBACK_USED`：蓝色提示条说明已自动退化
  - `SINGLE_HUGE_CHAPTER` / `NO_HEADING_MATCH`：醒目的"一键按字数切分"按钮
  - `HEADING_TOO_DENSE`：建议切换模式
  - `ENCODING_SUSPECT`：提示编码异常

**Given** MODE_LABELS 需要支持新模式
**When** 下拉菜单渲染
**Then** 包含 chapter_en / heuristic_title / fixed_size 选项

**技术说明:**
- 修改 `frontend/src/components/shared/UploadDialog.tsx`
- 修改 `frontend/src/api/types.ts`：新增 SplitDiagnosis 类型
- 新增 MODE_LABELS 条目

---

### Story 15.9: 第二次基准测试（手工步骤）

用 15.1 脚本在 S3-S8 完成后重跑 1500 本，对比 baseline，验证可用率 ≥ 90%。

---

## Milestone 2：文本卫生检测与清理

### Story 15.10: 杂质检测引擎

As a 系统,
I want 检测上传文本中的非正文内容（URL/推广/模板/装饰线/重复尾注）,
So that 用户能在导入前了解文本卫生状况。

**Acceptance Criteria:**

**Given** 上传的 TXT 含有 URL/推广链接/站点模板/装饰线
**When** 调用 `detect_noise(text, chapters)`
**Then** 返回 HygieneReport：total_suspect_lines、by_category 统计、前 10 样例

**技术说明:**
- 新建 `backend/src/utils/text_sanitizer.py`
- 5 类检测：url / promo / template / decoration / repeated
- 前 4 类纯正则/关键词；第 5 类跨章节比对末尾行

---

### Story 15.11: 清理函数

As a 系统,
I want 按杂质报告清理文本,
So that 清理后的文本不含被标记的杂质行。

**Acceptance Criteria:**

**Given** 杂质检测报告
**When** 调用 `clean_text(text, report, mode="conservative")`
**Then** conservative 模式：只删除 confidence ≥ 0.8 的行
**And** aggressive 模式：删除所有 suspect 行
**And** 返回清理后的文本

---

### Story 15.12: 导入流程集成

As a 系统,
I want 在上传预览中包含卫生报告并提供清理后重切 API,
So that 用户可以选择清理杂质后再切分。

**Acceptance Criteria:**

**Given** 上传文件
**When** `parse_upload()` 处理
**Then** 响应中包含 hygiene_report（如有杂质）

**Given** 用户选择清理
**When** 调用 `POST /api/novels/clean-and-resplit`
**Then** 执行 clean_text → split_chapters → 更新缓存 → 返回新预览

**技术说明:**
- 修改 `novel_service.py`：在 parse_upload 中调用 detect_noise
- 新增 `clean_and_resplit()` 方法
- 修改 `novels.py` 路由：新增 clean-and-resplit 端点
- 修改 `UploadPreviewResponse`：新增 hygiene_report 可选字段

---

### Story 15.13: 前端卫生报告展示

As a 用户,
I want 在上传预览页看到杂质检测报告并能一键清理,
So that 我可以在导入前清理网络小说中的推广内容。

**Acceptance Criteria:**

**Given** 预览响应包含 hygiene_report
**When** 预览页渲染
**Then** 显示橙色提示条：杂质行数量 + 按类型统计
**And** 展开可查看前 10 个样例（行号 + 内容 + 类型）
**And** 提供"清理并重新切分"按钮
**And** 点击按钮后调用 clean-and-resplit API 并刷新章节列表

---

### Story 15.14: 基准测试增加卫生列

As a 开发者,
I want 基准测试 CSV 包含杂质统计列,
So that 能评估基准集的文本卫生状况。

**Acceptance Criteria:**

**Given** 基准测试脚本
**When** 运行
**Then** CSV 新增列：noise_total, noise_url, noise_promo, noise_template, noise_decoration, noise_repeated
**And** 汇总新增：含杂质文件比例、各类杂质分布

---

## 实施顺序

1. S15.1 基准测试脚本
2. S15.3 预处理增强
3. S15.4 chapter_en 模式
4. S15.5 heuristic_title 模式
5. S15.6 fixed_size fallback
6. S15.7 切分诊断标签
7. S15.8 前端诊断展示
8. S15.10 杂质检测引擎
9. S15.11 清理函数
10. S15.12 导入流程集成
11. S15.13 前端卫生报告展示
12. S15.14 基准测试增加卫生列
