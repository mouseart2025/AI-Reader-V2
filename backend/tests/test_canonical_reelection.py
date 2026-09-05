"""canonical 锚定口径升级 + re-election 测试(issue #70 缺陷1/缺陷2)。

缺陷1 — 子串锚定 ≠ 语义锚定:canonical 的「在原文出现」校验从裸子串
升级为分层锚定(mention > dict_person > substring 兜底记审计);
LLM 建议的 canonical 不在最优锚定层时改选层内最强成员。

缺陷2 — canonical re-election:既有 llm_merge 决策的 canonical 每次运行
按最新 mention 证据重估,严格更优才翻转(防抖动),手动锁定组跳过,
重选走同一 llm_merge override 通道 + entity_resolution_log 审计。

全部使用 mock LLM / 内存 DB,不打真实 API。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from unittest.mock import patch

from src.db import entity_override_store
from src.services import alias_resolver, entity_resolver, hallucination_filter
from src.services.entity_resolver import (
    _anchor_tier,
    collect_person_names,
    reelect_llm_merge_canonicals,
    resolve_novel,
    select_anchored_canonical,
)

NOVEL = "novel-reelect-test"


# ── Mock helpers(与 test_entity_resolver.py 同款式) ─────────────


class MockLLM:
    def __init__(self, decision_fn):
        self.decision_fn = decision_fn
        self.calls = 0

    async def generate(self, system, prompt, format=None, **kw):
        self.calls += 1
        members = [
            line[2:].replace(" (存疑)", "").strip()
            for line in prompt.splitlines()
            if line.startswith("- ")
        ]
        groups = self.decision_fn(members)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        return {"groups": groups}, usage


def _meta(entries: dict[str, dict]):
    """手工构造 name_meta;缺省键补 0/False。"""
    out = {}
    for name, e in entries.items():
        out[name] = {
            "freq": e.get("freq", 0),
            "dict_person_freq": e.get("dict_person_freq", 0),
            "in_dict": e.get("in_dict", False),
            "grounded": e.get("grounded", True),
            "mention_chapters": e.get("mention_chapters", 0),
            "alias_chapters": e.get("alias_chapters", 0),
        }
    return out


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


def _patch_db(memory_db):
    async def _proxy():
        return _NonClosing(memory_db)

    return [
        patch("src.db.sqlite_db.get_connection", _proxy),
        patch("src.db.entity_override_store.get_connection", _proxy),
        patch("src.services.alias_resolver.get_connection", _proxy),
        patch("src.services.hallucination_filter.get_connection", _proxy),
    ]


async def _seed_novel(memory_db, novel_id, chapters: list[tuple[str, list]]):
    """种入 novel + 若干 (chapter_text, characters) 章节。

    characters: [(name, [aliases...]), ...] 写入该章 chapter_facts。
    """
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (novel_id, "重选测试")
    )
    for i, (text, chars) in enumerate(chapters, start=1):
        cursor = await memory_db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content)"
            " VALUES (?, ?, ?, ?)",
            (novel_id, i, f"第{i}回", text),
        )
        fact = {
            "characters": [
                {"name": n, "new_aliases": aliases} for n, aliases in chars
            ]
        }
        await memory_db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json)"
            " VALUES (?, ?, ?)",
            (novel_id, cursor.lastrowid, json.dumps(fact, ensure_ascii=False)),
        )
    await memory_db.commit()


async def _run_resolve(memory_db, tmp_path, novel_id, llm=None):
    """以「所有名字互不相似」的 embedding 跑 resolve_novel(无新簇)。"""
    hallucination_filter.invalidate_cache(novel_id)
    alias_resolver.invalidate_alias_cache(novel_id)

    async def _noop_cost(_usage):
        return None

    def _orthogonal_embed(names):
        vecs = []
        for i, _n in enumerate(names):
            v = [0.0] * len(names)
            v[i] = 1.0
            vecs.append(v)
        return vecs

    patches = _patch_db(memory_db) + [
        patch("src.services.entity_resolver._record_llm_cost", _noop_cost),
    ]
    for p in patches:
        p.start()
    try:
        report = await resolve_novel(
            novel_id, llm=llm or MockLLM(lambda members: []),
            embed_fn=_orthogonal_embed,
            log_path=tmp_path / "er.jsonl",
        )
        rows = await entity_override_store.load_overrides(novel_id)
    finally:
        for p in patches:
            p.stop()
    return report, rows


def _read_log(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── mention 证据采集 ────────────────────────────────────────────


class TestMentionEvidence:
    @pytest.mark.asyncio
    async def test_collect_person_names_mention_counts(self, memory_db):
        """mention_chapters = 作为 characters[].name 出现的章数;
        alias_chapters = 出现在 new_aliases 的章数。"""
        novel = "novel-mention-count"
        await _seed_novel(memory_db, novel, [
            ("赵云大战长坂坡。", [("赵云", [])]),
            ("赵云七进七出,子龙勇冠三军。", [("赵云", ["子龙"])]),
            ("赵云归营。", [("赵云", [])]),
        ])
        patches = _patch_db(memory_db)
        for p in patches:
            p.start()
        try:
            hallucination_filter.invalidate_cache(novel)
            meta = await collect_person_names(novel)
        finally:
            for p in patches:
                p.stop()
        assert meta["赵云"]["mention_chapters"] == 3
        assert meta["赵云"]["alias_chapters"] == 0
        # 子龙 仅作为别名声明出现,不计 mention_chapters
        assert meta["子龙"]["mention_chapters"] == 0
        assert meta["子龙"]["alias_chapters"] == 1


# ── 锚定分层 ────────────────────────────────────────────────────


class TestAnchorTier:
    def test_mention_tier(self):
        meta = _meta({"赵云": {"grounded": True, "mention_chapters": 3}})
        assert _anchor_tier("赵云", meta) == 2

    def test_mention_but_ungrounded_is_unanchored(self):
        """幻觉名:进了 facts 但原文不可定位且不在词典 → 不算 mention。"""
        meta = _meta({"林小凡": {"grounded": False, "mention_chapters": 2}})
        assert _anchor_tier("林小凡", meta) == -1

    def test_dict_person_tier(self):
        meta = _meta({"观音菩萨": {"in_dict": True, "dict_person_freq": 200}})
        assert _anchor_tier("观音菩萨", meta) == 1

    def test_dict_non_person_is_substring_fallback(self):
        """词典 unknown/concept 条目(如「于马下」)只是裸子串级证据。"""
        meta = _meta({"于马下": {"in_dict": True, "dict_person_freq": 0}})
        assert _anchor_tier("于马下", meta) == 0

    def test_bare_substring_fallback(self):
        meta = _meta({"马下": {"grounded": True, "mention_chapters": 0}})
        assert _anchor_tier("马下", meta) == 0

    def test_ungrounded(self):
        meta = _meta({"银驮": {"grounded": False}})
        assert _anchor_tier("银驮", meta) == -1


# ── select_anchored_canonical ───────────────────────────────────


class TestSelectAnchoredCanonical:
    def test_llm_canonical_in_best_pool_kept(self):
        meta = _meta({
            "赵云": {"mention_chapters": 3},
            "子龙": {"mention_chapters": 1},
        })
        chosen, info = select_anchored_canonical(["赵云", "子龙"], meta, "赵云")
        assert chosen == "赵云"
        assert info["reselected"] is False
        assert info["anchor"] == "mention"

    def test_weaker_tier_llm_canonical_reselected(self):
        """LLM 选了裸子串级残缺名,组内有 mention 级成员 → 改选。"""
        meta = _meta({
            "史慈": {"in_dict": True, "dict_person_freq": 0},       # tier 0
            "太史慈": {"mention_chapters": 6},                      # tier 2
        })
        chosen, info = select_anchored_canonical(["史慈", "太史慈"], meta, "史慈")
        assert chosen == "太史慈"
        assert info["reselected"] is True
        assert info["anchor"] == "mention"

    def test_blocklisted_name_never_canonical(self):
        """「太后」mention 章数更高但命中 CANONICAL_BLOCKLIST → 不可选。"""
        meta = _meta({
            "何太后": {"mention_chapters": 3},
            "太后": {"mention_chapters": 4, "in_dict": True,
                     "dict_person_freq": 50},
        })
        chosen, info = select_anchored_canonical(["何太后", "太后"], meta)
        assert chosen == "何太后"
        assert "太后" not in info["pool"]

    def test_strongest_mention_wins_within_pool(self):
        meta = _meta({
            "献帝": {"mention_chapters": 5},
            "汉献帝": {"mention_chapters": 2},
        })
        chosen, _info = select_anchored_canonical(["汉献帝", "献帝"], meta)
        assert chosen == "献帝"

    def test_tie_is_deterministic(self):
        meta = _meta({
            "牧童": {"mention_chapters": 1, "freq": 1},
            "牧羊小童": {"mention_chapters": 1, "freq": 1},
        })
        c1, _ = select_anchored_canonical(["牧童", "牧羊小童"], meta)
        c2, _ = select_anchored_canonical(["牧羊小童", "牧童"], meta)
        assert c1 == c2  # 输入顺序不影响结果

    def test_all_ungrounded_returns_tier_minus_one(self):
        meta = _meta({
            "林小凡": {"grounded": False, "mention_chapters": 1},
            "银驮": {"grounded": False},
        })
        chosen, info = select_anchored_canonical(["林小凡", "银驮"], meta)
        assert info["tier"] == -1

    def test_dict_only_group_falls_back_with_label(self):
        """全组无 mention 证据 → dict_person / substring 兜底(记审计标签)。"""
        meta = _meta({
            "观音菩萨": {"in_dict": True, "dict_person_freq": 200},
            "南海观世音": {"in_dict": True, "dict_person_freq": 60},
        })
        chosen, info = select_anchored_canonical(
            ["观音菩萨", "南海观世音"], meta, "观音菩萨"
        )
        assert chosen == "观音菩萨"
        assert info["anchor"] == "dict_person"


# ── 新合并的锚定口径(缺陷1,集成) ────────────────────────────────


class TestNewMergeAnchor:
    @pytest.mark.asyncio
    async def test_fragment_canonical_reselected_to_mention_member(
        self, memory_db, tmp_path
    ):
        """缺陷1 场景:LLM 把裸子串级残缺名(词典 unknown 条目)选为
        canonical,组内存在 mention 级全名 → 改选全名并记审计。"""
        novel = "novel-anchor-reselect"
        await _seed_novel(memory_db, novel, [
            ("太史慈纵马出阵。", [("太史慈", [])]),
            ("太史慈引兵来援。", [("太史慈", [])]),
        ])
        # 词典里的裸子串级条目(子串存在但从未作为人名 mention)
        await memory_db.execute(
            "INSERT INTO entity_dictionary"
            " (novel_id, name, frequency, aliases, entity_type, source)"
            " VALUES (?, '史慈', 59, '[]', 'unknown', 'test')",
            (novel,),
        )
        await memory_db.commit()

        hallucination_filter.invalidate_cache(novel)
        alias_resolver.invalidate_alias_cache(novel)
        group_of = {"史慈": 0, "太史慈": 0}

        def embed(names):
            return [[1.0, 0.0] if group_of[n] == 0 else [0.0, 1.0]
                    for n in names]

        llm = MockLLM(lambda members: [
            {"canonical": "史慈", "members": members, "reason": "mock: 同一人物"},
        ])

        async def _noop_cost(_usage):
            return None

        patches = _patch_db(memory_db) + [
            patch("src.services.entity_resolver._record_llm_cost", _noop_cost),
        ]
        for p in patches:
            p.start()
        try:
            report = await resolve_novel(
                novel, llm=llm, embed_fn=embed,
                log_path=tmp_path / "er.jsonl",
            )
            rows = await entity_override_store.load_overrides(novel)
        finally:
            for p in patches:
                p.stop()

        assert report["merges"] == 1
        assert len(rows) == 1
        j = rows[0]["override_json"]
        # 改选 mention 级全名,而非 LLM 建议的裸子串残缺名
        assert j["canonical"] == "太史慈"
        assert j["grounded_reselected"] is True
        assert j["canonical_anchor"] == "mention"
        assert j["mention_chapters"]["太史慈"] == 2
        assert j["mention_chapters"]["史慈"] == 0

    @pytest.mark.asyncio
    async def test_dict_only_group_gets_fallback_anchor_audit(
        self, memory_db, tmp_path
    ):
        """全组无 mention 证据(纯词典组合并):允许但记 dict_person 审计标签。"""
        novel = "novel-anchor-dict-only"
        await memory_db.execute(
            "INSERT INTO novels (id, title) VALUES (?, ?)", (novel, "兜底测试")
        )
        cursor = await memory_db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content)"
            " VALUES (?, 1, '第一回', '正文没有候选名。')",
            (novel,),
        )
        await memory_db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json)"
            " VALUES (?, ?, ?)",
            (novel, cursor.lastrowid, json.dumps({"characters": []})),
        )
        for name, freq in [("观音菩萨", 200), ("南海观世音", 60)]:
            await memory_db.execute(
                "INSERT INTO entity_dictionary"
                " (novel_id, name, frequency, aliases, entity_type, source)"
                " VALUES (?, ?, ?, '[]', 'person', 'test')",
                (novel, name, freq),
            )
        await memory_db.commit()

        hallucination_filter.invalidate_cache(novel)
        alias_resolver.invalidate_alias_cache(novel)

        def embed(names):
            return [[1.0] for _n in names]

        llm = MockLLM(lambda members: [
            {"canonical": "观音菩萨", "members": members, "reason": "mock"},
        ])

        async def _noop_cost(_usage):
            return None

        patches = _patch_db(memory_db) + [
            patch("src.services.entity_resolver._record_llm_cost", _noop_cost),
        ]
        for p in patches:
            p.start()
        try:
            report = await resolve_novel(
                novel, llm=llm, embed_fn=embed,
                log_path=tmp_path / "er.jsonl",
            )
            rows = await entity_override_store.load_overrides(novel)
        finally:
            for p in patches:
                p.stop()

        assert report["merges"] == 1
        j = rows[0]["override_json"]
        assert j["canonical"] == "观音菩萨"
        assert j["canonical_anchor"] == "dict_person"
        assert j["grounded_reselected"] is False


# ── canonical re-election(缺陷2,集成) ────────────────────────────


async def _seed_llm_merge(memory_db, novel_id, canonical, members):
    await memory_db.execute(
        "INSERT INTO entity_overrides"
        " (novel_id, override_type, override_key, override_json)"
        " VALUES (?, 'llm_merge', ?, ?)",
        (
            novel_id, canonical,
            json.dumps({
                "members": members,
                "canonical": canonical,
                "reason": "mock: 同一人物",
                "prompt_version": "er-cluster-v1",
                "auto_snapshot": {m: m for m in members},
            }, ensure_ascii=False),
        ),
    )
    await memory_db.commit()


class TestReelection:
    @pytest.mark.asyncio
    async def test_late_full_name_triggers_reelection(self, memory_db, tmp_path):
        """缺陷2 场景:canonical 停在次要名(子龙,1 章 mention),
        全名(赵云,3 章 mention)证据更强 → 重选扶正。"""
        novel = "novel-reelect-flip"
        await _seed_novel(memory_db, novel, [
            ("赵云大战长坂坡,子龙救主。", [("赵云", []), ("子龙", [])]),
            ("赵云七进七出。", [("赵云", [])]),
            ("赵云归营。", [("赵云", [])]),
        ])
        await _seed_llm_merge(memory_db, novel, "子龙", ["赵云", "子龙"])

        report, rows = await _run_resolve(memory_db, tmp_path, novel)
        assert report["reelections"] == 1
        assert report["re_elected"] == [{"from": "子龙", "to": "赵云"}]
        assert report["llm_calls"] == 0  # 全部已 decided,不再调用 LLM

        assert len(rows) == 1  # 旧行被删,新行按新 canonical 写入
        row = rows[0]
        assert row["override_key"] == "赵云"
        j = row["override_json"]
        assert j["canonical"] == "赵云"
        assert j["re_elected"] is True
        assert j["previous_canonical"] == "子龙"
        assert j["canonical_anchor"] == "mention"
        assert set(j["members"]) == {"赵云", "子龙"}  # 合并组成员不变

        records = _read_log(tmp_path / "er.jsonl")
        reelect_log = [r for r in records
                       if r.get("event") == "canonical_reelection"]
        assert len(reelect_log) == 1
        assert reelect_log[0]["previous_canonical"] == "子龙"
        assert reelect_log[0]["new_canonical"] == "赵云"

    @pytest.mark.asyncio
    async def test_reelection_idempotent(self, memory_db, tmp_path):
        """防抖动:重选收敛后再跑不再翻转(幂等)。"""
        novel = "novel-reelect-idem"
        await _seed_novel(memory_db, novel, [
            ("赵云出战,子龙救主。", [("赵云", []), ("子龙", [])]),
            ("赵云归营。", [("赵云", [])]),
        ])
        await _seed_llm_merge(memory_db, novel, "子龙", ["赵云", "子龙"])

        r1, rows1 = await _run_resolve(memory_db, tmp_path, novel)
        assert r1["reelections"] == 1
        r2, rows2 = await _run_resolve(memory_db, tmp_path, novel)
        assert r2["reelections"] == 0  # 第二次运行无翻转
        assert len(rows2) == 1
        assert rows2[0]["override_json"]["canonical"] == "赵云"

    @pytest.mark.asyncio
    async def test_tie_does_not_flip(self, memory_db, tmp_path):
        """同分不翻转:mention 章数相同 → 保持现 canonical(防抖动)。"""
        novel = "novel-reelect-tie"
        await _seed_novel(memory_db, novel, [
            ("牧童指路,牧羊小童唱歌。", [("牧童", []), ("牧羊小童", [])]),
        ])
        await _seed_llm_merge(memory_db, novel, "牧童", ["牧童", "牧羊小童"])

        report, rows = await _run_resolve(memory_db, tmp_path, novel)
        assert report["reelections"] == 0
        assert rows[0]["override_json"]["canonical"] == "牧童"

    @pytest.mark.asyncio
    async def test_locked_group_not_reelected(self, memory_db, tmp_path):
        """手动 override 锁定的名字不参与自动重选(用户优先级最高)。"""
        novel = "novel-reelect-locked"
        await _seed_novel(memory_db, novel, [
            ("赵云出战,子龙救主。", [("赵云", []), ("子龙", [])]),
            ("赵云归营。", [("赵云", [])]),
        ])
        await _seed_llm_merge(memory_db, novel, "子龙", ["赵云", "子龙"])
        # 用户手动 merge 锁定「子龙」
        await memory_db.execute(
            "INSERT INTO entity_overrides"
            " (novel_id, override_type, override_key, override_json)"
            " VALUES (?, 'alias_merge', '子龙', ?)",
            (novel, json.dumps({
                "members": ["子龙", "阿斗的救命恩人"],
                "canonical": "子龙",
            }, ensure_ascii=False)),
        )
        await memory_db.commit()

        report, rows = await _run_resolve(memory_db, tmp_path, novel)
        assert report["reelections"] == 0
        llm_rows = [r for r in rows if r["override_type"] == "llm_merge"]
        assert llm_rows[0]["override_json"]["canonical"] == "子龙"  # 未翻转

    @pytest.mark.asyncio
    async def test_strongest_canonical_not_flipped(self, memory_db, tmp_path):
        """现 canonical 已是锚定最强成员 → 不动。"""
        novel = "novel-reelect-stable"
        await _seed_novel(memory_db, novel, [
            ("曹操起兵,孟德号令。", [("曹操", []), ("孟德", [])]),
            ("曹操进兵。", [("曹操", [])]),
        ])
        await _seed_llm_merge(memory_db, novel, "曹操", ["曹操", "孟德"])

        report, rows = await _run_resolve(memory_db, tmp_path, novel)
        assert report["reelections"] == 0
        assert rows[0]["override_json"]["canonical"] == "曹操"
        assert _read_log(tmp_path / "er.jsonl") == []  # 无重选审计记录

    @pytest.mark.asyncio
    async def test_weaker_tier_canonical_flipped_even_with_fewer_mentions(
        self, memory_db, tmp_path
    ):
        """锚定层优先于 mention 章数:裸子串级 canonical 让位于 mention 级成员。"""
        novel = "novel-reelect-tier"
        await _seed_novel(memory_db, novel, [
            ("太史慈纵马出阵。", [("太史慈", [])]),
        ])
        await memory_db.execute(
            "INSERT INTO entity_dictionary"
            " (novel_id, name, frequency, aliases, entity_type, source)"
            " VALUES (?, '史慈', 59, '[]', 'unknown', 'test')",
            (novel,),
        )
        await _seed_llm_merge(memory_db, novel, "史慈", ["史慈", "太史慈"])

        report, rows = await _run_resolve(memory_db, tmp_path, novel)
        assert report["reelections"] == 1
        assert rows[0]["override_json"]["canonical"] == "太史慈"

    @pytest.mark.asyncio
    async def test_reelection_does_not_widen_merge_group(self, memory_db, tmp_path):
        """防变宽:re-election 只评估 override 已记录的 members,不把 auto
        alias map 里的其他别名扩进合并组。"""
        novel = "novel-reelect-nowiden"
        await _seed_novel(memory_db, novel, [
            ("赵云出战,子龙救主。", [("赵云", ["赵子龙"]), ("子龙", [])]),
            ("赵云归营。", [("赵云", [])]),
        ])
        await _seed_llm_merge(memory_db, novel, "子龙", ["赵云", "子龙"])

        report, rows = await _run_resolve(memory_db, tmp_path, novel)
        assert report["reelections"] == 1
        j = rows[0]["override_json"]
        # 赵子龙 在 facts 里被声明为赵云别名,但不在 llm_merge members 内,
        # 重选不得把它扩进合并组
        assert set(j["members"]) == {"赵云", "子龙"}
