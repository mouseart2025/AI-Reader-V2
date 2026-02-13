# Story 8.2: Phase 1 统计扫描引擎

Status: ready-for-dev

## Story

As a 系统,
I want 对全书文本进行统计扫描，提取高频实体候选词,
So that 后续 LLM 分类和词典注入有准确的候选数据来源。

## Acceptance Criteria

1. 实现 jieba 分词 + 词性过滤的词频统计，保留名词类（nr/ns/nz/n）且长度 2-8 字的词
2. 实现 2-4 字 n-gram 频率统计，只保留频次 ≥ 3 的 n-gram
3. 实现对话归属正则，从 "X道/说/曰/笑道..." 模式提取人名
4. 实现章节标题词提取
5. 实现后缀模式匹配，按后缀推断实体类型（location/org/item/person）
6. 实现多来源候选合并去重，来源优先级：dialogue > title > suffix > freq
7. 停用词过滤：过滤常见动词、副词、连词、量词等非实体高频词
8. 全书扫描性能：100 万字小说 Phase 1 完成 ≤ 15 秒
9. jieba 分词使用 `asyncio.to_thread()` 包装，不阻塞事件循环

## Tasks / Subtasks

- [ ] Task 1: 创建扫描引擎主模块 (AC: #1-#7, #9)
  - [ ] 新建 `backend/src/extraction/entity_pre_scanner.py`
  - [ ] 定义 `class EntityPreScanner`
  - [ ] 实现 `async def scan(novel_id: str) -> list[EntityDictEntry]` 主入口方法

- [ ] Task 2: 实现 jieba 词频统计 (AC: #1)
  - [ ] 方法 `_scan_word_freq(chapters: list[str]) -> Counter`
  - [ ] 使用 `jieba.posseg.cut()` 进行词性标注分词
  - [ ] 过滤条件：`len(word) >= 2 and len(word) <= 8`，词性以 `nr/ns/nz/n` 开头

- [ ] Task 3: 实现 n-gram 统计 (AC: #2)
  - [ ] 方法 `_scan_ngrams(chapters: list[str], min_n=2, max_n=4, min_freq=3) -> Counter`
  - [ ] 遍历全书文本生成字符级 n-gram
  - [ ] 过滤：纯标点/数字/空白组合，频次 < min_freq
  - [ ] 与 jieba 词频合并（取较大频次）

- [ ] Task 4: 实现对话归属正则 (AC: #3)
  - [ ] 方法 `_extract_dialogue_names(chapters: list[str]) -> Counter`
  - [ ] 正则模式：`[""「]...[""」] + X + 道/说/曰/笑道/叫道/问道/...`
  - [ ] 提取的 X 为人名，置信度 high，来源 dialogue

- [ ] Task 5: 实现章节标题词提取 (AC: #4)
  - [ ] 方法 `_extract_title_words(titles: list[str]) -> Counter`
  - [ ] 对每个标题用 jieba 分词，保留 2-6 字的中文词
  - [ ] 来源标记 title

- [ ] Task 6: 实现后缀模式匹配 (AC: #5)
  - [ ] 方法 `_match_suffix_patterns(candidates: Counter) -> dict[str, str]`
  - [ ] 定义 `_SUFFIX_RULES` 字典，包含 location/org/item/person 四类后缀
  - [ ] 对候选词检查后缀匹配，返回 {name: entity_type} 映射

- [ ] Task 7: 实现停用词过滤 (AC: #7)
  - [ ] 定义 `_STOPWORDS: set[str]`，包含常见非实体高频词（然后、但是、不过、因为、如果、已经、虽然、一个、什么、这个...）
  - [ ] 在合并阶段过滤停用词

- [ ] Task 8: 实现候选合并 (AC: #6)
  - [ ] 方法 `_merge_candidates(freq, ngrams, dialogue, titles, suffix_types) -> list[EntityDictEntry]`
  - [ ] 同名合并频次，取最高置信度来源
  - [ ] 为每个候选提取 sample_context（首次出现的句子，≤ 50 字）

## Dev Notes

- jieba 是同步 CPU 密集库。在 async scan() 中用 `await asyncio.to_thread(_sync_scan, ...)` 包装
- jieba 首次加载词典约 1-2 秒，后续调用无加载开销
- n-gram 统计对内存有一定压力（100 万字的 4-gram 组合量大），需要在计数时直接过滤低频项或使用滑动窗口
- 对话归属正则需注意：文言文用「」而非""，现代文用""——正则需同时匹配
- sample_context 提取策略：在全文中搜索候选词首次出现位置，截取前后各 25 字

### Project Structure Notes

- `backend/src/extraction/entity_pre_scanner.py` — 新文件，与 `chapter_fact_extractor.py` 同级

### References

- [Source: _bmad-output/entity-prescan-architecture.md#4.2 Phase 1 详细设计]
- [Source: backend/src/extraction/chapter_fact_extractor.py] — 同层模块参考

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
