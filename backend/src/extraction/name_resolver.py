"""NameResolver — resolves character name variants to canonical forms.

Runs after FactValidator, before DB save. Ensures that within each ChapterFact,
all character references use canonical names (e.g., "行者" → "孙悟空").

This is the upstream fix for the alias fragmentation problem: by unifying names
at extraction time, downstream relation edges and alias groups are automatically
deduplicated.

Sources for canonical mapping (in priority order):
1. entity_dictionary aliases (from pre-scan phase)
2. Accumulated new_aliases from prior chapters
3. Current chapter's own new_aliases

Safety: only merges explicit alias mappings. No fuzzy matching.

v0.70.3: All name decisions delegated to name_authority (single source of truth).

Provenance (issue #70): resolve() 的每次改写落 JSONL 审计日志
(name_resolution_log.jsonl),记录 from/to/source/field,可追溯 canonical
名字来自哪次改写。fact_validator 的改名决策(alias-merge / 泛称消歧)
也写入同一通道(rule 字段区分)。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.models.chapter_fact import ChapterFact
from src.services.name_authority import (
    alias_safety_level,
    is_blocked_name,
    pick_canonical,
)

logger = logging.getLogger(__name__)

# 名字决策审计日志(JSONL,每行一条改写)— 与 audit_reports 其他产物同目录,
# 同样按 __file__ 定位(与 entity_resolver / hallucination_reviewer 同机制,
# sidecar 下 cwd 不影响)。
AUDIT_LOG_PATH = (
    Path(__file__).resolve().parents[2] / "audit_reports" / "name_resolution_log.jsonl"
)


def write_audit_records(records: list[dict], log_path: Path | None = None) -> Path | None:
    """批量追加审计记录到 JSONL(单次打开文件;空列表不写,返回 None)。

    复用 entity_resolver.write_decision_log 的 JSONL 追加模式,但面向
    「每章几十条改写」的批量场景,一次 open 写完。
    """
    if not records:
        return None
    path = log_path or AUDIT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps({"timestamp": ts, **rec}, ensure_ascii=False) + "\n")
    return path


class NameResolver:
    """Resolve character name variants to canonical forms in ChapterFact."""

    def __init__(self):
        self._canonical_map: dict[str, str] = {}  # alias → canonical
        self._freq: Counter = Counter()  # name → total mention count
        # 词典载入的实体名(来自原文子串,视为天然锚定,无需再校验)
        self._dict_names: set[str] = set()
        # canonical 污染防线:锚定失败的丢弃计数(内存统计,便于观测)
        self._ungrounded_dropped: Counter = Counter()
        # 映射来源(审计用):"dict" = 实体词典载入,"accumulated" = 章节累积
        self._map_source: dict[str, str] = {}

    def load_from_entity_dictionary(self, entries: list) -> None:
        """Load alias mappings from entity_dictionary (pre-scan phase).

        Args:
            entries: list of EntityDictionaryEntry with .name, .aliases, .entity_type

        Canonical selection delegated to name_authority.pick_canonical(),
        ensuring consistency with AliasResolver's downstream canonical choice.
        """
        # Build frequency map: primary entry name → prescan frequency
        entry_freq: dict[str, int] = {}
        for entry in entries:
            if entry.entity_type == "person" and not is_blocked_name(entry.name):
                entry_freq[entry.name] = entry.frequency
                self._dict_names.add(entry.name)

        for entry in entries:
            if entry.entity_type != "person":
                continue
            if is_blocked_name(entry.name):
                continue

            # Collect dict-primary candidates for canonical selection
            candidates = [entry.name]
            freq_map = {entry.name: entry.frequency}
            dict_primaries = {entry.name}
            for alias in (entry.aliases or []):
                if alias in entry_freq and not is_blocked_name(alias):
                    candidates.append(alias)
                    freq_map[alias] = entry_freq[alias]
                    dict_primaries.add(alias)

            # Use shared canonical selection — same logic as AliasResolver
            canonical = pick_canonical(candidates, freq_map, dict_primaries)

            # Map all aliases → canonical
            all_names = {entry.name} | set(entry.aliases or [])
            for name in all_names:
                if name and name != canonical and not is_blocked_name(name):
                    if alias_safety_level(name) >= 1:  # not hard-blocked
                        # Don't overwrite existing mapping to a higher-freq canonical
                        existing = self._canonical_map.get(name)
                        if existing and entry_freq.get(existing, 0) > freq_map.get(canonical, 0):
                            continue
                        self._canonical_map[name] = canonical
                        self._map_source[name] = "dict"

        logger.info("NameResolver loaded %d mappings from entity_dictionary",
                     len(self._canonical_map))

    def accumulate_from_chapter(
        self, fact: ChapterFact, chapter_text: str | None = None,
    ) -> None:
        """Accumulate alias mappings from a chapter's new_aliases fields.

        Called AFTER resolve() so canonical names are already applied.
        Builds up the mapping for subsequent chapters.

        canonical 污染防线(canonical-name pollution guard):传入 chapter_text 时,
        对 canonical 端(char.name)与 alias 端做双向原文锚定——名字在本章原文
        中不可定位(复用证据锚定的 span 定位口径)且非词典/既有映射的已确立名,
        则该别名声明丢弃(不进 canonical_map、从 new_aliases 剔除),记日志并计数。
        chapter_text=None 时保持旧行为(测试/离线兼容)。
        """
        if chapter_text is not None:
            self._accumulate_grounded(fact, chapter_text)
            return
        for char in fact.characters:
            name = char.name
            self._freq[name] += 1
            for alias in (char.new_aliases or []):
                if (alias and alias != name
                        and not is_blocked_name(alias)
                        and alias_safety_level(alias) >= 1):
                    existing = self._canonical_map.get(alias)
                    if existing and existing != name:
                        # Conflict: alias points to two different canonicals.
                        # Keep the one with higher frequency.
                        if self._freq.get(name, 0) >= self._freq.get(existing, 0):
                            self._canonical_map[alias] = name
                            self._map_source[alias] = "accumulated"
                        # else: keep existing
                    else:
                        if alias not in self._canonical_map:
                            self._map_source[alias] = "accumulated"
                        self._canonical_map[alias] = name

    def _name_grounded(self, name: str, chapter_text: str) -> bool:
        """名字是否锚定:本章原文可定位,或是词典/既有映射中的已确立名。

        - 词典名(预扫描原文子串)与既有 canonical 映射值天然锚定,不再校验;
        - "X·樵夫" 类消歧名按 "·" 后基本名再查一次(与幻觉判定层同口径)。
        """
        if name in self._dict_names or name in self._canonical_map.values():
            return True
        from src.extraction.chapter_fact_extractor import span_located
        if span_located(name, chapter_text):
            return True
        base = name.split("·")[-1]
        return base != name and span_located(base, chapter_text)

    def _accumulate_grounded(self, fact: ChapterFact, chapter_text: str) -> None:
        """accumulate_from_chapter 的锚定版本:双向原文锚定后才累积映射。"""
        from src.extraction.chapter_fact_extractor import span_located
        for char in fact.characters:
            name = char.name
            # canonical 端锚定:幻觉/拼接名不成为后续章节的 canonical 来源
            if not self._name_grounded(name, chapter_text):
                self._ungrounded_dropped["canonical"] += 1
                logger.info(
                    "NameResolver: canonical %r 本章原文不可定位,跳过别名累积",
                    name,
                )
                continue
            self._freq[name] += 1
            kept_aliases: list[str] = []
            aliases_changed = False
            for alias in (char.new_aliases or []):
                if (alias and alias != name
                        and not is_blocked_name(alias)
                        and alias_safety_level(alias) >= 1):
                    # alias 端锚定:词典/既有映射中的名视为已锚定
                    if (alias not in self._canonical_map
                            and alias not in self._dict_names
                            and not span_located(alias, chapter_text)):
                        self._ungrounded_dropped["alias"] += 1
                        aliases_changed = True
                        logger.info(
                            "NameResolver: 别名声明 %r→%r 原文不可定位,已丢弃",
                            alias, name,
                        )
                        continue
                    kept_aliases.append(alias)
                    existing = self._canonical_map.get(alias)
                    if existing and existing != name:
                        # Conflict: alias points to two different canonicals.
                        # Keep the one with higher frequency.
                        if self._freq.get(name, 0) >= self._freq.get(existing, 0):
                            self._canonical_map[alias] = name
                            self._map_source[alias] = "accumulated"
                        # else: keep existing
                    else:
                        if alias not in self._canonical_map:
                            self._map_source[alias] = "accumulated"
                        self._canonical_map[alias] = name
                else:
                    kept_aliases.append(alias)
            if aliases_changed:
                char.new_aliases = kept_aliases

    def resolve(self, fact: ChapterFact, *, log_path: Path | None = None) -> ChapterFact:
        """Apply canonical name mappings to all fields in a ChapterFact.

        Modifies fact in-place and returns it. 每次改写落一条审计记录
        (issue #70 provenance):{novel_id, chapter_id, field, from, to,
        source("dict"|"accumulated")},批量一次 append;不改写时不写文件。
        log_path 仅供测试重定向;默认写 AUDIT_LOG_PATH。
        """
        if not self._canonical_map:
            return fact

        mapped = self._canonical_map
        resolved_count = 0
        audit: list[dict] = []

        def _record(field: str, from_name: str, to_name: str) -> None:
            audit.append({
                "novel_id": fact.novel_id,
                "chapter_id": fact.chapter_id,
                "field": field,
                "from": from_name,
                "to": to_name,
                "source": self._map_source.get(from_name, "accumulated"),
                "rule": "name_resolve",
            })

        # 1. Resolve character names
        for char in fact.characters:
            canonical = mapped.get(char.name)
            if canonical:
                _record("characters", char.name, canonical)
                char.name = canonical
                resolved_count += 1

        # 2. Resolve relationship person_a / person_b
        for rel in fact.relationships:
            ca = mapped.get(rel.person_a)
            if ca:
                _record("relationships", rel.person_a, ca)
                rel.person_a = ca
                resolved_count += 1
            cb = mapped.get(rel.person_b)
            if cb:
                _record("relationships", rel.person_b, cb)
                rel.person_b = cb
                resolved_count += 1

        # 3. Resolve event participants
        for evt in fact.events:
            new_participants: list[str] = []
            for p in evt.participants:
                c = mapped.get(p)
                if c:
                    _record("events", p, c)
                    new_participants.append(c)
                else:
                    new_participants.append(p)
            evt.participants = new_participants

        # 4. Resolve item_events / org_events character references
        for ie in fact.item_events:
            if hasattr(ie, 'character') and ie.character:
                c = mapped.get(ie.character)
                if c:
                    _record("item_events", ie.character, c)
                    ie.character = c
        for oe in fact.org_events:
            if hasattr(oe, 'character') and oe.character:
                c = mapped.get(oe.character)
                if c:
                    _record("org_events", oe.character, c)
                    oe.character = c

        # 5. Clean new_aliases: remove blocked names
        for char in fact.characters:
            if char.new_aliases:
                char.new_aliases = [
                    a for a in char.new_aliases
                    if not is_blocked_name(a) and a != char.name
                ]

        if resolved_count > 0:
            logger.debug("NameResolver: resolved %d name references", resolved_count)

        write_audit_records(audit, log_path)

        return fact

    @property
    def mapping_count(self) -> int:
        return len(self._canonical_map)

    @property
    def ungrounded_drop_count(self) -> int:
        """锚定失败被丢弃的声明总数(canonical 端 + alias 端)。"""
        return sum(self._ungrounded_dropped.values())
