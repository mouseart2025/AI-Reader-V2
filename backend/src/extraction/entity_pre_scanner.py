"""Entity pre-scan: full-text statistical scanning + LLM classification.

Phase 1 (this module): CPU-only statistical scan using jieba, n-gram,
dialogue attribution, chapter titles, and suffix patterns.
Phase 2 (prescan_prompts.py + LLM call): LLM classification of candidates.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter

from src.db import entity_dictionary_store
from src.db.sqlite_db import get_connection
from src.models.entity_dict import EntityDictEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Suffix rules for entity type inference
# ---------------------------------------------------------------------------
_SUFFIX_RULES: dict[str, list[str]] = {
    "location": [
        "山", "洞", "洲", "国", "城", "宫", "殿", "府", "寺", "庙", "观", "院",
        "阁", "楼", "塔", "谷", "崖", "峰", "岭", "河", "海", "湖", "泊", "关",
        "门", "桥", "镇", "村", "庄", "寨", "营", "港", "岛", "坊", "台", "池",
        "林", "园", "原", "坡", "涧",
    ],
    "org": [
        "派", "宗", "帮", "会", "盟", "教", "族", "军", "营", "卫", "堂",
        "门派", "宗门",
    ],
    "item": [
        "丹", "药", "剑", "刀", "枪", "珠", "鼎", "炉", "符", "阵", "经", "诀",
        "功", "术", "法", "戟", "斧", "锤", "弓", "甲", "袍", "环", "镯", "钟",
        "琴", "笛", "扇", "杖", "棍", "鞭", "索", "旗", "印", "册", "图", "镜",
    ],
    "person": [
        "真人", "道人", "仙人", "大师", "长老", "掌门", "圣人", "大王",
        "将军", "元帅", "太子", "公主", "娘娘", "老祖", "仙子", "童子",
        "居士", "道长", "法师", "尊者", "菩萨", "罗汉",
    ],
}

# ---------------------------------------------------------------------------
# Stopwords — common non-entity high-frequency words
# ---------------------------------------------------------------------------
_STOPWORDS: set[str] = {
    # Verbs / adverbs
    "然后", "但是", "不过", "因为", "如果", "已经", "虽然", "而且", "或者",
    "所以", "因此", "于是", "不是", "就是", "只是", "可是", "还是", "只有",
    "不能", "不会", "没有", "不要", "可以", "应该", "能够", "需要", "知道",
    "觉得", "认为", "看到", "听到", "感觉", "发现", "开始", "继续", "出来",
    "出去", "进来", "进去", "起来", "下去", "回来", "过来", "过去", "上去",
    "不得", "不了", "下来", "进了", "到了", "去了", "来了", "走了", "在此",
    # Pronouns / determiners
    "什么", "这个", "那个", "这些", "那些", "自己", "他们", "她们", "我们",
    "你们", "大家", "别人", "其他", "所有", "一切", "任何", "每个", "这样",
    "那样", "怎么", "怎样", "如何", "为何", "为什么", "哪里", "哪个",
    "那里", "这里", "何处", "何人", "何事", "此处", "此人", "此事",
    # Measure words / time / common words
    "一个", "两个", "一些", "一下", "一次", "一时", "此时", "当时", "顿时",
    "这时", "那时", "同时", "时候", "时间", "地方", "东西", "事情", "问题",
    "了一", "了个", "着一", "了这", "一面", "一阵", "一身", "一般", "一番",
    # Conjunctions / particles
    "不禁", "只见", "忽然", "突然", "果然", "居然", "竟然", "终于", "马上",
    "立刻", "随即", "正是", "便是", "却是", "原来", "本来", "看来", "说道",
    "一声", "一看", "只道", "心中", "心想", "暗想", "想到", "说完", "心下",
    # Common verbs in classical Chinese
    "不曾", "须是", "却说", "话说", "且说", "只因", "正在", "休得", "休要",
    "如今", "只是", "甚么", "如何", "那厮", "这厮",
    # Common nouns that are NOT entity names
    "众人", "小人", "哥哥", "兄弟", "妇人", "老爷", "丫头", "夫人", "师父",
    "弟子", "师兄", "师弟", "师姐", "师妹", "徒弟", "父亲", "母亲", "兄长",
    "大人", "老人", "小姐", "公子", "先生", "姑娘", "奴婢", "丞相", "使者",
    "一人", "二人", "三人", "四人", "五人", "十人", "百人",
    "好汉", "英雄", "壮士", "头领", "先锋", "军马", "人马", "军兵",
    "官兵", "兵马", "将士", "弟兄", "朋友", "客人", "主人",
    # Common verbs appearing as nouns in jieba
    "听得", "看了", "看得", "说了", "听了", "走了", "来了", "去了",
    "只得", "不敢", "不知", "不见", "不曾", "只管", "只得",
    "见了", "问道", "答道", "回道", "叫道", "今日", "商议",
    "如此", "如此这般", "只好", "便去", "便道", "上前",
    # Directional / positional
    "里面", "外面", "上面", "下面", "前面", "后面", "左右", "身边",
    "面前", "旁边", "其中", "之中", "之上", "之下", "之间",
    "陛下", "殿下", "阁下",  # Honorifics, not entity names
    # Counters / numbers in text
    "一个", "两个", "三个", "四个", "五个", "十个", "几个",
    "个人", "二人", "多人", "十余", "数十", "百余",
    # Common non-entity words in classical Chinese novels
    "官人", "那人", "大喜", "大怒", "上山", "下山", "回来",
    "那妇人", "此人", "这人", "一行", "太公", "军士", "军兵",
    "知府", "管营", "差人", "衙内",
    # Common words that appear before dialogue verbs but aren't names
    "对方", "无法", "原本", "全都", "有点", "立刻", "马上", "似乎",
    "果然", "只好", "最后", "终于", "突然", "忽然", "随后", "接着",
    "竟然", "居然", "顿时", "于是", "赶紧", "连忙", "急忙", "不由",
    "身份", "老者", "少女", "女子", "男子", "大汉", "少年", "青年",
    "仙师", "道友", "前辈", "晚辈", "小子", "丫头", "老头", "老人家",
    "天子", "庄客", "小弟",
    # Dialogue verb prefixes (can be mistaken for speaker names)
    "冷笑", "微笑", "苦笑", "嗤笑", "大笑", "大叫", "大喝", "大怒",
    "消息", "人家", "总算", "差点", "师傅", "中年人", "少妇", "儒生",
    "老道", "的说", "有些", "根本", "当然", "一定", "这才", "那就",
    "这就", "就要", "将要", "正要", "又要", "老夫", "本座", "在下",
    # More dialogue/action false positives
    "命令", "现在", "多谢", "黑脸", "血光", "大哥", "也不", "小心",
    "赶快", "快点", "快些", "随便", "难道", "到底", "究竟", "好像",
    "不过", "只要", "只能", "不如", "还有", "没想到", "想不到",
}

# Patterns to strip from candidates (e.g., "宋江道" -> "宋江")
_DIALOGUE_VERB_SUFFIX = re.compile(
    r'^([\u4e00-\u9fff]{2,6})'
    r'(道|说|曰|笑道|叫道|问道|答道|喝道|叹道|骂道|喊道|怒道|惊道|忙道|急道)$'
)

# ---------------------------------------------------------------------------
# Dialogue attribution patterns
# ---------------------------------------------------------------------------
# Dialogue verb list (shared across patterns)
_DIALOGUE_VERBS = (
    r'(?:道|说|曰|笑道|叫道|问道|答道|喝道|叹道|骂道|喊道|怒道|惊道|忙道|急道'
    r'|冷笑道|大叫道|大喝道|大笑道|冷冷道|淡淡道|沉声道|低声道|高声道'
    r'|嗤笑道|微笑道|苦笑道|轻声道|朗声道|厉声道|柔声道|冷声道|冷哼道)'
)

# Pattern 1: "dialogue" X道 (classical)
_DIALOGUE_PATTERN = re.compile(
    r'[\u201c\u201d""「『]([^""」』\u201c\u201d]{1,200})[\u201c\u201d""」』]\s*[，,。]?\s*'
    r'([\u4e00-\u9fff]{2,6})\s*'
    + _DIALOGUE_VERBS
)

# Pattern 2: X道："dialogue" (modern, speaker before colon+quote)
_SPEAKER_BEFORE_PATTERN = re.compile(
    r'([\u4e00-\u9fff]{2,6})\s*'
    + _DIALOGUE_VERBS
    + r'\s*[：:]\s*[\u201c""「『]'
)

# Pattern 3: X + dialogue verb (modern web novel, no quote needed)
# Requires name to appear after punctuation/line break (not mid-sentence)
# This catches "，韩立说道" but not "某某冷笑道" (where 冷笑 is part of verb)
_BARE_SPEAKER_PATTERN = re.compile(
    r'(?:^|[，。！？；：、\n\r""」』])\s*'
    r'([\u4e00-\u9fff]{2,4})'
    + _DIALOGUE_VERBS,
    re.MULTILINE,
)


class EntityPreScanner:
    """Scans full novel text to build an entity dictionary."""

    async def scan(self, novel_id: str) -> list[EntityDictEntry]:
        """Main entry: run Phase 1 statistical scan, save results to DB.

        Phase 2 (LLM classification) is called after Phase 1 if available.
        """
        logger.info("预扫描开始: novel_id=%s", novel_id)
        await entity_dictionary_store.update_prescan_status(novel_id, "running")

        try:
            # Load all chapter texts and titles
            chapters, titles = await self._load_chapters(novel_id)
            if not chapters:
                logger.warning("预扫描跳过: 无章节数据 novel_id=%s", novel_id)
                await entity_dictionary_store.update_prescan_status(novel_id, "completed")
                return []

            full_text = "\n".join(chapters)

            # Phase 1: Statistical scan (CPU-bound, run in thread)
            candidates = await asyncio.to_thread(
                self._phase1_scan, chapters, titles, full_text
            )

            logger.info(
                "Phase 1 完成: %d 个候选实体, novel_id=%s",
                len(candidates), novel_id,
            )

            # Phase 2: LLM classification (implemented in Story 8.3)
            try:
                candidates = await self._classify_with_llm(candidates)
                logger.info(
                    "Phase 2 完成: %d 个实体, novel_id=%s",
                    len(candidates), novel_id,
                )
            except Exception:
                logger.warning(
                    "Phase 2 LLM 分类失败，使用 Phase 1 统计结果",
                    exc_info=True,
                )

            # Save to DB
            await entity_dictionary_store.delete_all(novel_id)
            await entity_dictionary_store.insert_batch(novel_id, candidates)
            await entity_dictionary_store.update_prescan_status(novel_id, "completed")

            logger.info(
                "预扫描完成: %d 个实体已写入词典, novel_id=%s",
                len(candidates), novel_id,
            )
            return candidates

        except Exception:
            logger.error("预扫描失败: novel_id=%s", novel_id, exc_info=True)
            await entity_dictionary_store.update_prescan_status(novel_id, "failed")
            raise

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def _load_chapters(
        self, novel_id: str
    ) -> tuple[list[str], list[str]]:
        """Load all chapter contents and titles from DB."""
        conn = await get_connection()
        try:
            cursor = await conn.execute(
                """
                SELECT title, content FROM chapters
                WHERE novel_id = ?
                ORDER BY chapter_num
                """,
                (novel_id,),
            )
            rows = await cursor.fetchall()
            chapters = [row["content"] for row in rows]
            titles = [row["title"] for row in rows]
            return chapters, titles
        finally:
            await conn.close()

    # ------------------------------------------------------------------
    # Phase 1: Statistical scan (all sync, run via to_thread)
    # ------------------------------------------------------------------

    def _phase1_scan(
        self,
        chapters: list[str],
        titles: list[str],
        full_text: str,
    ) -> list[EntityDictEntry]:
        """Run all Phase 1 statistical scans and merge candidates."""
        # 1a. jieba word frequency
        word_freq = self._scan_word_freq(full_text)

        # 1b. n-gram frequency
        ngram_freq = self._scan_ngrams(full_text)

        # 1c. Dialogue attribution
        dialogue_names = self._extract_dialogue_names(full_text)

        # 1d. Chapter title words
        title_words = self._extract_title_words(titles)

        # 1e. Suffix pattern matching
        all_names = set(word_freq.keys()) | set(ngram_freq.keys()) | set(dialogue_names.keys()) | set(title_words.keys())
        suffix_types = self._match_suffix_patterns(all_names)

        # 1f. Merge all sources
        candidates = self._merge_candidates(
            word_freq, ngram_freq, dialogue_names, title_words,
            suffix_types, full_text,
        )

        return candidates

    def _scan_word_freq(self, full_text: str) -> Counter:
        """jieba POS-tagged word frequency: keep nouns 2-8 chars.

        Caps input at 1M chars for performance. For longer novels, the first
        1M chars capture sufficient frequency distribution.
        """
        import jieba.posseg as pseg

        # Cap for performance: 1M chars ≈ 100 万字
        text = full_text[:1_000_000] if len(full_text) > 1_000_000 else full_text

        counter: Counter = Counter()
        for word, flag in pseg.cut(text):
            if len(word) < 2 or len(word) > 8:
                continue
            if word in _STOPWORDS:
                continue
            # nr=person name, ns=place, nz=proper noun, n=general noun
            if flag.startswith(("nr", "ns", "nz", "n")):
                counter[word] += 1

        # If text was capped, extrapolate frequencies
        if len(full_text) > 1_000_000:
            ratio = len(full_text) / 1_000_000
            for word in counter:
                counter[word] = int(counter[word] * ratio)

        return counter

    def _scan_ngrams(
        self, full_text: str, min_n: int = 2, max_n: int = 4, min_freq: int = 5
    ) -> Counter:
        """Character-level n-gram frequency to catch words jieba missed.

        Optimized: splits text into CJK-only segments first, then scans
        segments rather than the entire raw text. Caps input at 500K chars.
        """
        # Cap text for performance (500K chars ≈ 50 万字, enough for patterns)
        text = full_text[:500_000] if len(full_text) > 500_000 else full_text

        # Split into pure-CJK segments (skip punctuation, whitespace, etc.)
        segments = re.findall(r'[\u4e00-\u9fff]{2,}', text)

        counter: Counter = Counter()
        for seg in segments:
            for n in range(min_n, min(max_n + 1, len(seg) + 1)):
                for i in range(len(seg) - n + 1):
                    gram = seg[i:i + n]
                    counter[gram] += 1

        # Filter low frequency and stopwords
        return Counter({
            gram: freq for gram, freq in counter.items()
            if freq >= min_freq and gram not in _STOPWORDS
        })

    def _extract_dialogue_names(self, full_text: str) -> Counter:
        """Extract speaker names from dialogue attribution patterns.

        Uses three patterns to cover classical, modern, and web novel styles.
        """
        counter: Counter = Counter()

        # Pattern 1: "..." X道 (classical)
        for m in _DIALOGUE_PATTERN.finditer(full_text):
            name = m.group(2).strip()
            if 2 <= len(name) <= 6 and name not in _STOPWORDS:
                counter[name] += 1

        # Pattern 2: X道："..." (modern with colon)
        for m in _SPEAKER_BEFORE_PATTERN.finditer(full_text):
            name = m.group(1).strip()
            if 2 <= len(name) <= 6 and name not in _STOPWORDS:
                counter[name] += 1

        # Pattern 3: X道/X说道 (bare, no quotes needed — catches web novels)
        for m in _BARE_SPEAKER_PATTERN.finditer(full_text):
            name = m.group(1).strip()
            if 2 <= len(name) <= 4 and name not in _STOPWORDS:
                counter[name] += 1

        return counter

    def _extract_title_words(self, titles: list[str]) -> Counter:
        """Extract meaningful words from chapter titles."""
        import jieba

        counter: Counter = Counter()
        for title in titles:
            # Remove common chapter prefixes like "第X章", "第X回"
            cleaned = re.sub(r'^第[一二三四五六七八九十百千万零\d]+[章回节卷篇][\s：:]*', '', title)
            cleaned = re.sub(r'^\d+[\s\.、：:]+', '', cleaned)
            if not cleaned:
                continue

            for word in jieba.cut(cleaned):
                if len(word) < 2 or len(word) > 6:
                    continue
                if word in _STOPWORDS:
                    continue
                # Keep Chinese words only
                if all('\u4e00' <= c <= '\u9fff' for c in word):
                    counter[word] += 1

        return counter

    def _match_suffix_patterns(
        self, candidates: set[str]
    ) -> dict[str, str]:
        """Match suffix patterns to infer entity types.

        For single-char suffixes, the prefix must be >= 2 chars
        (e.g., "梁山泊" OK, "下山" rejected).
        """
        result: dict[str, str] = {}
        for name in candidates:
            if len(name) < 3:
                continue
            for entity_type, suffixes in _SUFFIX_RULES.items():
                for suffix in suffixes:
                    prefix_len = len(name) - len(suffix)
                    if prefix_len < 2:
                        continue
                    if name.endswith(suffix):
                        result[name] = entity_type
                        break
                if name in result:
                    break
        return result

    def _merge_candidates(
        self,
        word_freq: Counter,
        ngram_freq: Counter,
        dialogue_names: Counter,
        title_words: Counter,
        suffix_types: dict[str, str],
        full_text: str,
    ) -> list[EntityDictEntry]:
        """Merge all sources into a deduplicated candidate list."""
        # Clean dialogue verb suffixes: "宋江道" -> redirect freq to "宋江"
        for source in (word_freq, ngram_freq):
            to_redirect: list[tuple[str, str, int]] = []
            for name, freq in list(source.items()):
                m = _DIALOGUE_VERB_SUFFIX.match(name)
                if m:
                    clean_name = m.group(1)
                    to_redirect.append((name, clean_name, freq))
            for dirty, clean, freq in to_redirect:
                del source[dirty]
                source[clean] = source.get(clean, 0) + freq

        # Deduplicate n-grams: remove substrings, then only keep n-grams NOT in jieba
        # (the whole point of n-grams is to catch words jieba missed)
        jieba_names = set(word_freq.keys())
        deduped_ngrams = self._dedup_ngrams(ngram_freq, jieba_names)
        # Only keep n-grams that jieba didn't find (supplementary role)
        ngram_only = Counter({
            gram: freq for gram, freq in deduped_ngrams.items()
            if gram not in jieba_names
        })

        # Collect all unique names
        all_names: set[str] = set()
        all_names.update(word_freq.keys())
        all_names.update(ngram_only.keys())
        all_names.update(dialogue_names.keys())
        all_names.update(title_words.keys())

        # Dynamic minimum frequency based on text length
        text_len = len(full_text)
        if text_len > 1_000_000:
            min_freq = 10
        elif text_len > 200_000:
            min_freq = 5
        else:
            min_freq = 3

        # Build entries
        entries: dict[str, EntityDictEntry] = {}
        for name in all_names:
            if name in _STOPWORDS:
                continue

            # Merge frequency from all sources (take max between jieba and ngram)
            freq = max(word_freq.get(name, 0), ngram_only.get(name, 0))
            # Add dialogue and title counts to boost signal
            freq = max(freq, dialogue_names.get(name, 0), title_words.get(name, 0))

            # High-confidence sources (dialogue, title) use lower threshold
            is_high_signal = name in dialogue_names or name in title_words
            threshold = max(2, min_freq // 2) if is_high_signal else min_freq
            if freq < threshold:
                continue

            # Determine best source (priority: dialogue > title > suffix > freq)
            if name in dialogue_names:
                source = "dialogue"
                confidence = "high"
            elif name in title_words:
                source = "title"
                confidence = "high"
            elif name in suffix_types:
                source = "suffix"
                confidence = "medium"
            elif name in word_freq:
                source = "freq"
                confidence = "medium" if word_freq[name] >= 10 else "low"
            else:
                source = "ngram"
                confidence = "low"

            # Entity type from suffix or default
            entity_type = suffix_types.get(name, "unknown")
            # Dialogue names are persons
            if name in dialogue_names and entity_type == "unknown":
                entity_type = "person"

            # Extract sample context
            sample_context = self._extract_sample_context(name, full_text)

            entries[name] = EntityDictEntry(
                name=name,
                entity_type=entity_type,
                frequency=freq,
                confidence=confidence,
                aliases=[],
                source=source,
                sample_context=sample_context,
            )

        # Sort by frequency descending, cap at 500 candidates
        sorted_entries = sorted(entries.values(), key=lambda e: e.frequency, reverse=True)
        return sorted_entries[:500]

    @staticmethod
    def _dedup_ngrams(ngram_freq: Counter, jieba_names: set[str]) -> Counter:
        """Remove n-grams that are substrings of known jieba words or longer n-grams.

        If "哈姆莱特" exists (jieba or ngram), remove "哈姆莱", "姆莱特", "哈姆", "莱特" etc.
        Optimized: only check longer words (3+ chars) and generate their substrings directly.
        """
        if not ngram_freq:
            return ngram_freq

        # Collect all longer words (3+ chars) from jieba and ngrams
        long_words = [w for w in (jieba_names | set(ngram_freq.keys())) if len(w) >= 3]

        substrings_to_remove: set[str] = set()
        for word in long_words:
            word_freq = max(ngram_freq.get(word, 0), 1)
            # Generate all substrings of this word
            for start in range(len(word)):
                for end in range(start + 2, len(word)):
                    sub = word[start:end]
                    if sub in ngram_freq and sub != word:
                        if ngram_freq[sub] <= word_freq * 1.2:
                            substrings_to_remove.add(sub)

        return Counter({
            gram: freq for gram, freq in ngram_freq.items()
            if gram not in substrings_to_remove
        })

    def _extract_sample_context(
        self, name: str, full_text: str, context_len: int = 25
    ) -> str | None:
        """Extract a sample context around the first occurrence of a name."""
        idx = full_text.find(name)
        if idx < 0:
            return None
        start = max(0, idx - context_len)
        end = min(len(full_text), idx + len(name) + context_len)
        snippet = full_text[start:end].replace("\n", " ").strip()
        return snippet

    # ------------------------------------------------------------------
    # Phase 2: LLM classification
    # ------------------------------------------------------------------

    async def _classify_with_llm(
        self, candidates: list[EntityDictEntry]
    ) -> list[EntityDictEntry]:
        """Phase 2: Use LLM to classify candidates and identify aliases."""
        from src.extraction.prescan_prompts import build_classification_prompt
        from src.infra.llm_client import get_llm_client

        if not candidates:
            return candidates

        system_prompt, user_prompt = build_classification_prompt(candidates)

        llm = get_llm_client()
        result = await llm.generate(
            system=system_prompt,
            prompt=user_prompt,
            format={"type": "object"},
            temperature=0.1,
            max_tokens=8192,
        )

        if isinstance(result, str):
            import json
            result = json.loads(result)

        return self._merge_llm_results(candidates, result)

    def _merge_llm_results(
        self,
        candidates: list[EntityDictEntry],
        llm_result: dict,
    ) -> list[EntityDictEntry]:
        """Merge LLM classification results back into candidates."""
        # Build lookup by name
        by_name: dict[str, EntityDictEntry] = {c.name: c for c in candidates}

        # Update entity types from LLM
        for entity in llm_result.get("entities", []):
            name = entity.get("name", "")
            if name in by_name:
                if entity.get("type"):
                    by_name[name].entity_type = entity["type"]
                if entity.get("confidence"):
                    by_name[name].confidence = entity["confidence"]

        # Process alias groups
        for group in llm_result.get("alias_groups", []):
            if not isinstance(group, list) or len(group) < 2:
                continue
            for name in group:
                if name in by_name:
                    others = [n for n in group if n != name]
                    by_name[name].aliases = others

        # Remove rejected words
        rejected = set(llm_result.get("rejected", []))
        result = [c for c in candidates if c.name not in rejected]

        return result
