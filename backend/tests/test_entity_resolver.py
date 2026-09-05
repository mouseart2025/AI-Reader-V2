"""Tests for the LLM incremental entity resolver (Epic 2, FR-2.1–FR-2.4).

Covers:
- FR-2.1 embedding blocking: candidate-cluster recall (南海观世音/观音菩萨),
  LLM call count linear in person count, no LLM judgment outside clusters.
- FR-2.2 LLM batch clustering: decision log contents, llm_merge overrides
  written to the v072 entity_overrides channel, DELETE-undoable.
- FR-2.3 safety: hard-block (level 0) never merged even when the LLM says so;
  level 1 hint-only; anti-bridging (阮小二≠阮小七) enforced on the LLM path.
- FR-2.4 manual overrides lock names out of LLM decisions; decided names are
  skipped on rebuild (incremental, survives-rebuild); disabled flag = no-op.
- Byte-identical invariant: with no overrides and no LLM decisions,
  build_alias_map output equals the pre-Epic-2 result exactly.

All LLM/embedding calls are mocked — no real API access.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from unittest.mock import patch

from src.db import entity_override_store
from src.services import alias_resolver, entity_resolver
from src.services.entity_resolver import (
    build_candidate_clusters,
    build_cluster_prompt,
    decided_names_from_overrides,
    locked_names_from_overrides,
    name_merge_eligibility,
    partition_candidates,
    resolve_cluster,
    resolve_novel,
    validate_groups,
    PROMPT_VERSION,
)

NOVEL = "novel-er-test"


# ── Mock helpers ──────────────────────────────────────────────


def _one_hot_embed(group_of: dict[str, int], dim: int | None = None):
    """Deterministic mock embedding: names in the same group share a basis
    vector (cosine 1.0 in-group, 0.0 cross-group)."""
    dim = dim or (max(group_of.values(), default=-1) + 1)

    def embed(names: list[str]) -> list[list[float]]:
        vecs = []
        for n in names:
            v = [0.0] * dim
            v[group_of[n]] = 1.0
            vecs.append(v)
        return vecs

    return embed


class MockLLM:
    """Mock LLM returning canned clustering decisions per cluster.

    decision_fn(cluster_members) -> list of group dicts. Records prompts so
    tests can assert what was (not) sent to the LLM.
    """

    def __init__(self, decision_fn):
        self.decision_fn = decision_fn
        self.prompts: list[str] = []
        self.calls = 0

    async def generate(self, system, prompt, format=None, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        members = [
            line[2:].replace(" (存疑)", "").strip()
            for line in prompt.splitlines()
            if line.startswith("- ")
        ]
        groups = self.decision_fn(members)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        return {"groups": groups}, usage


def _meta(names_freqs: dict[str, int], dict_person: dict[str, int] | None = None):
    """Build name_meta: {name: {freq, dict_person_freq}}."""
    dict_person = dict_person or {}
    return {
        n: {"freq": f, "dict_person_freq": dict_person.get(n, 0)}
        for n, f in names_freqs.items()
    }


# ── FR-2.1 embedding blocking ─────────────────────────────────


class TestCandidateBlocking:
    def test_candidate_cluster_recall_guanyin(self):
        """已知欠并组 南海观世音/观音菩萨 必须落在同一候选簇 (FR-2.1 验收)。"""
        names = ["南海观世音", "观音菩萨", "观世音", "孙悟空", "猪八戒"]
        group_of = {"南海观世音": 0, "观音菩萨": 0, "观世音": 0,
                    "孙悟空": 1, "猪八戒": 2}
        clusters = build_candidate_clusters(names, _one_hot_embed(group_of))
        flat = {n for c in clusters for n in c}
        guanyin_cluster = next(
            (c for c in clusters if "观音菩萨" in c), None
        )
        assert guanyin_cluster is not None
        assert "南海观世音" in guanyin_cluster
        assert "观世音" in guanyin_cluster
        # 远距名字不进任何簇 — 簇外不做 LLM 判定
        assert "孙悟空" not in flat
        assert "猪八戒" not in flat

    def test_llm_calls_linear_not_quadratic(self):
        """NFR-2: LLM 判定调用量与人物数近似线性(= 候选簇数)。"""
        # 10 tight pairs + 80 singletons = 100 names → exactly 10 clusters
        group_of = {}
        for i in range(10):
            group_of[f"人物甲{i}"] = i
            group_of[f"人物乙{i}"] = i
        for i in range(80):
            group_of[f"路人{i}"] = 10 + i
        clusters = build_candidate_clusters(
            sorted(group_of), _one_hot_embed(group_of), threshold=0.9
        )
        assert len(clusters) == 10  # 10 LLM calls for 100 names, not 4950 pairs

    def test_threshold_and_topk_respected(self):
        """低于余弦阈值的名字不连边;top-k 限制近邻数量。"""
        # sim(a,b)=0.8 (shared basis component), sim(a,c)=0.0
        def embed(names):
            table = {
                "甲": [1.0, 0.0],
                "乙": [0.8, 0.6],   # cosine(甲,乙)=0.8
                "丙": [0.0, 1.0],   # cosine(甲,丙)=0.0
            }
            return [table[n] for n in names]

        assert build_candidate_clusters(
            ["甲", "乙", "丙"], embed, threshold=0.75
        ) == [["乙", "甲"]]
        assert build_candidate_clusters(
            ["甲", "乙", "丙"], embed, threshold=0.85
        ) == []


# ── FR-2.3 safety levels ──────────────────────────────────────


class TestSafetyLevels:
    def test_hard_block_never_merged_even_if_llm_says_so(self):
        """level-0 别名即使 LLM 判定应并也不并 (FR-2.3 验收)。"""
        meta = _meta({"孙悟空": 100, "菩萨": 30, "泼孽障": 5})
        cluster = ["孙悟空", "菩萨", "泼孽障"]
        groups = [
            {"canonical": "孙悟空",
             "members": ["孙悟空", "菩萨", "泼孽障"],
             "reason": "mock: 都是西游人物"},
        ]
        accepted, rejected = validate_groups(cluster, groups, meta)
        assert accepted == []
        assert len(rejected) == 1
        assert "hard-block" in rejected[0]["rejected_reason"]

    def test_dict_primary_promotion_allows_guanyin(self):
        """entity_dictionary 高频 person 主实体晋升:观音菩萨可参与合并。"""
        eligible, role = name_merge_eligibility("观音菩萨", dict_person_freq=200)
        assert (eligible, role) == (True, "merge")
        # 非晋升的 level-0 泛称仍然 block
        eligible, role = name_merge_eligibility("菩萨", dict_person_freq=0)
        assert eligible is False

    def test_level1_hint_only_never_merged(self):
        """level-1 存疑名仅提示:进不了候选,LLM 组里出现也被拒绝。"""
        eligible, role = name_merge_eligibility("猴")  # 单字 → level 1
        assert (eligible, role) == (True, "hint")
        meta = _meta({"孙悟空": 100, "猴": 5})
        mergeable, hints, _blocked = partition_candidates(meta)
        assert "猴" not in mergeable and "猴" in hints
        accepted, rejected = validate_groups(
            ["孙悟空", "猴"],
            [{"canonical": "孙悟空", "members": ["孙悟空", "猴"], "reason": "x"}],
            meta,
        )
        assert accepted == [] and rejected

    def test_anti_bridging_ruan_brothers_rejected(self):
        """防桥接约束在 LLM 路径生效:阮小二≠阮小七 (FR-2.3)。"""
        meta = _meta({"阮小二": 80, "阮小七": 75})
        accepted, rejected = validate_groups(
            ["阮小二", "阮小七"],
            [{"canonical": "阮小七", "members": ["阮小二", "阮小七"],
              "reason": "mock: 名字像"}],
            meta,
        )
        assert accepted == []
        assert "similar-name conflict" in rejected[0]["rejected_reason"]

    @pytest.mark.asyncio
    async def test_anti_bridging_enforced_at_override_apply(self):
        """即使 llm_merge 被写入 store,应用层也拒绝阮小二→阮小七(双层防御)。"""
        ov = [{
            "override_type": "llm_merge",
            "override_key": "阮小七",
            "override_json": {"members": ["阮小二", "阮小七"],
                              "canonical": "阮小七"},
        }]

        async def _load(_n):
            return ov

        alias_resolver.invalidate_alias_cache(NOVEL)
        with patch("src.db.entity_override_store.load_overrides", _load):
            out = await alias_resolver._apply_user_overrides(NOVEL, {})
        assert out == {}  # 防桥接:并案被拒

    def test_outside_cluster_members_rejected(self):
        """LLM 输出含簇外名字 → 拒绝(簇外不做判定的对称约束)。"""
        meta = _meta({"孙悟空": 100, "猪八戒": 90})
        accepted, rejected = validate_groups(
            ["孙悟空"],
            [{"canonical": "孙悟空", "members": ["孙悟空", "猪八戒"],
              "reason": "x"}],
            meta,
        )
        assert accepted == []
        assert "outside candidate cluster" in rejected[0]["rejected_reason"]


# ── FR-2.2 decision log + resolve_cluster ─────────────────────


class TestDecisionLog:
    @pytest.mark.asyncio
    async def test_decision_log_contents(self, tmp_path):
        """决策日志含输入簇/输出分组/理由/prompt 版本 (FR-2.2 验收)。"""
        log = tmp_path / "er_log.jsonl"
        meta = _meta({"观音菩萨": 200, "南海观世音": 60},
                     dict_person={"观音菩萨": 200})
        llm = MockLLM(lambda members: [
            {"canonical": "观音菩萨", "members": ["观音菩萨", "南海观世音"],
             "reason": "同一人物的不同尊称"},
        ])
        decision = await resolve_cluster(
            NOVEL, ["观音菩萨", "南海观世音"], meta, llm,
            log_path=log, record_cost=False,
        )
        assert decision["output_groups"][0]["canonical"] == "观音菩萨"

        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["input_cluster"] == ["观音菩萨", "南海观世音"]
        assert record["output_groups"][0]["members"] == ["南海观世音", "观音菩萨"]
        assert record["output_groups"][0]["reason"] == "同一人物的不同尊称"
        assert record["prompt_version"] == PROMPT_VERSION
        assert record["novel_id"] == NOVEL

    @pytest.mark.asyncio
    async def test_prompt_contains_cluster_and_hints(self):
        """批量 in-context prompt:簇成员逐条列出,存疑名带 (存疑) 标注。"""
        prompt = build_cluster_prompt(["观音菩萨", "南海观世音"], hints=["猴"])
        assert "- 观音菩萨" in prompt
        assert "- 南海观世音" in prompt
        assert "- 猴 (存疑)" in prompt


# ── FR-2.4 locked / decided names ─────────────────────────────


class TestOverridePartitioning:
    def test_locked_names_from_manual_overrides(self):
        overrides = [
            {"override_type": "alias_merge", "override_key": "沙僧",
             "override_json": {"members": ["沙僧", "沙悟净"], "canonical": "沙僧"}},
            {"override_type": "alias_split", "override_key": "八戒→(独立)",
             "override_json": {"source": "八戒", "aliases": ["夯货甲"], "to": None}},
            {"override_type": "entity_rename", "override_key": "少年",
             "override_json": {"to": "杨过"}},
            {"override_type": "llm_merge", "override_key": "观音菩萨",
             "override_json": {"members": ["观音菩萨", "南海观世音"],
                               "canonical": "观音菩萨"}},
        ]
        locked = locked_names_from_overrides(overrides)
        assert {"沙僧", "沙悟净", "八戒", "夯货甲", "少年", "杨过"} <= locked
        # llm_merge 不算手动锁定
        assert "观音菩萨" not in locked and "南海观世音" not in locked
        decided = decided_names_from_overrides(overrides)
        assert decided == {"观音菩萨", "南海观世音"}

    def test_locked_and_decided_excluded_from_candidates(self):
        meta = _meta({"沙僧": 50, "沙悟净": 30, "孙悟空": 100})
        mergeable, _h, _b = partition_candidates(
            meta, locked_names={"沙僧"}, decided_names={"沙悟净"}
        )
        assert mergeable == ["孙悟空"]


# ── Integration: resolve_novel with memory DB (all LLM/embedding mocked) ──


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


async def _seed_db(memory_db):
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试小说")
    )
    rows = [
        ("观音菩萨", 200, "[]", "person"),
        ("南海观世音", 60, "[]", "person"),
        ("阮小二", 80, "[]", "person"),
        ("阮小七", 75, "[]", "person"),
        ("孙悟空", 150, '["行者"]', "person"),
    ]
    for name, freq, aliases, etype in rows:
        await memory_db.execute(
            "INSERT INTO entity_dictionary"
            " (novel_id, name, frequency, aliases, entity_type, source)"
            " VALUES (?, ?, ?, ?, ?, 'test')",
            (NOVEL, name, freq, aliases, etype),
        )
    await memory_db.commit()


def _patch_db(memory_db):
    async def _proxy():
        return _NonClosing(memory_db)

    return [
        patch("src.db.sqlite_db.get_connection", _proxy),
        patch("src.db.entity_override_store.get_connection", _proxy),
        patch("src.services.alias_resolver.get_connection", _proxy),
    ]


@pytest.mark.asyncio
async def test_resolve_novel_end_to_end(memory_db, tmp_path):
    """FR-2.2/2.3/2.4 集成:观音并案写入 override 通道、阮氏兄弟防桥接、
    DELETE 可撤销、重跑增量跳过已决策名字。"""
    await _seed_db(memory_db)
    alias_resolver.invalidate_alias_cache(NOVEL)

    group_of = {"观音菩萨": 0, "南海观世音": 0, "阮小二": 1, "阮小七": 1,
                "孙悟空": 2}
    llm = MockLLM(lambda members: [
        {"canonical": "观音菩萨" if "观音菩萨" in members else members[0],
         "members": members, "reason": "mock merge"},
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
            NOVEL, llm=llm, embed_fn=_one_hot_embed(group_of),
            log_path=tmp_path / "er.jsonl",
        )

        # 两个簇(观音、阮氏)→ 2 次 LLM 调用,与人物数线性
        assert report["llm_calls"] == 2
        assert report["merges"] == 1  # 只有观音并案通过校验

        rows = await entity_override_store.load_overrides(NOVEL)
        assert len(rows) == 1
        row = rows[0]
        assert row["override_type"] == "llm_merge"
        j = row["override_json"]
        assert j["canonical"] == "观音菩萨"
        assert set(j["members"]) == {"观音菩萨", "南海观世音"}
        assert j["prompt_version"] == PROMPT_VERSION
        assert j["reason"] == "mock merge"
        assert j["input_cluster"] == ["南海观世音", "观音菩萨"]

        # 阮小二≠阮小七:防桥接,未写入任何 override
        assert all("阮小二" not in r["override_json"].get("members", []) for r in rows)

        # DELETE 撤销 (FR-2.2 验收)
        assert await entity_override_store.delete_override(NOVEL, row["id"]) is True
        assert await entity_override_store.load_overrides(NOVEL) == []
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_resolve_novel_incremental_skips_decided(memory_db, tmp_path):
    """survives-rebuild:第二次运行跳过已决策名字,不再重复调用 LLM。"""
    await _seed_db(memory_db)
    alias_resolver.invalidate_alias_cache(NOVEL)
    group_of = {"观音菩萨": 0, "南海观世音": 0, "阮小二": 1, "阮小七": 1,
                "孙悟空": 2}

    async def _noop_cost(_usage):
        return None

    # 第一次:LLM 只并观音(阮氏让 LLM 主动拒绝,避免干扰计数)
    llm1 = MockLLM(lambda members: (
        [{"canonical": "观音菩萨", "members": ["观音菩萨", "南海观世音"],
          "reason": "同一人物"}]
        if "观音菩萨" in members else []
    ))
    patches = _patch_db(memory_db) + [
        patch("src.services.entity_resolver._record_llm_cost", _noop_cost),
    ]
    for p in patches:
        p.start()
    try:
        r1 = await resolve_novel(
            NOVEL, llm=llm1, embed_fn=_one_hot_embed(group_of),
            log_path=tmp_path / "er.jsonl",
        )
        assert r1["llm_calls"] == 2

        # 第二次:观音已决策 → 只剩阮氏一个簇 → 1 次调用;override 不重复
        alias_resolver.invalidate_alias_cache(NOVEL)
        llm2 = MockLLM(lambda members: [])
        r2 = await resolve_novel(
            NOVEL, llm=llm2, embed_fn=_one_hot_embed(group_of),
            log_path=tmp_path / "er.jsonl",
        )
        assert r2["llm_calls"] == 1
        assert set(r2["skipped_decided"]) == {"观音菩萨", "南海观世音"}
        rows = await entity_override_store.load_overrides(NOVEL)
        assert len(rows) == 1  # 无重复决策
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_resolve_novel_disabled_is_noop(memory_db):
    """ENTITY_RESOLUTION_ENABLED=false → 完全 no-op (NFR-3)。"""
    await _seed_db(memory_db)
    llm = MockLLM(lambda members: [])
    patches = _patch_db(memory_db)
    for p in patches:
        p.start()
    try:
        with patch("src.infra.config.ENTITY_RESOLUTION_ENABLED", False):
            report = await resolve_novel(
                NOVEL, llm=llm, embed_fn=lambda n: [[0.0]] * len(n)
            )
        assert report["enabled"] is False
        assert llm.calls == 0
        assert await entity_override_store.load_overrides(NOVEL) == []
    finally:
        for p in patches:
            p.stop()


# ── Byte-identical invariant (FR-2.4 / NFR-3) ─────────────────

# 固定 fixture 下 build_alias_map 的改动前输出(由 v0.73 行为计算得出)。
# 无 override、无 LLM 决策时输出必须与此逐字节一致。
_EXPECTED_BASELINE_MAP = {
    "悟空": "孙悟空",
    "行者": "孙悟空",
    "大圣": "孙悟空",
    "孙行者": "孙悟空",
    "八戒": "猪八戒",
    "猪悟能": "猪八戒",
    "三藏": "唐僧",
    "沙悟净": "沙僧",
}


@pytest.mark.asyncio
async def test_build_alias_map_byte_identical_without_decisions(memory_db):
    """无 override 且无 LLM 决策时 build_alias_map 输出与改动前逐字节一致。"""
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试小说")
    )
    dict_rows = [
        ("孙悟空", 152, '["悟空","行者","大圣"]', "person"),
        ("猪八戒", 182, '["八戒","猪悟能"]', "person"),
        ("唐僧", 300, '["三藏"]', "person"),
    ]
    for name, freq, aliases, etype in dict_rows:
        await memory_db.execute(
            "INSERT INTO entity_dictionary"
            " (novel_id, name, frequency, aliases, entity_type, source)"
            " VALUES (?, ?, ?, ?, ?, 'test')",
            (NOVEL, name, freq, aliases, etype),
        )
    fact = {
        "characters": [
            {"name": "孙悟空", "new_aliases": ["孙行者"]},
            {"name": "沙僧", "new_aliases": ["沙悟净"]},
        ]
    }
    cursor = await memory_db.execute(
        "INSERT INTO chapters (novel_id, chapter_num, title, content)"
        " VALUES (?, 1, '第一回', '正文')",
        (NOVEL,),
    )
    await memory_db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json)"
        " VALUES (?, ?, ?)",
        (NOVEL, cursor.lastrowid, json.dumps(fact, ensure_ascii=False)),
    )
    await memory_db.commit()

    alias_resolver.invalidate_alias_cache(NOVEL)
    patches = _patch_db(memory_db)
    for p in patches:
        p.start()
    try:
        result = await alias_resolver.build_alias_map(NOVEL)
    finally:
        for p in patches:
            p.stop()
    assert result == _EXPECTED_BASELINE_MAP
    alias_resolver.invalidate_alias_cache(NOVEL)


# ── canonical-name 污染防线:B1 canonical grounded 校验 / B2 候选标注 / B3 日志 ──


def _patch_grounded_db(memory_db):
    """在 _patch_db 基础上补 hallucination_filter 的 get_connection。

    hallucination_filter 是 `from ... import get_connection` 直接绑定,
    必须单独 patch 其模块命名空间。
    """

    async def _proxy():
        return _NonClosing(memory_db)

    return _patch_db(memory_db) + [
        patch("src.services.hallucination_filter.get_connection", _proxy),
    ]


async def _seed_grounded_db(memory_db, novel_id: str, corpus_text: str,
                            fact_names: list[str],
                            dict_rows: list[tuple] | None = None):
    """种入 1 章原文 + chapter_facts 人物名(+ 可选词典条目)。"""
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (novel_id, "锚定测试")
    )
    cursor = await memory_db.execute(
        "INSERT INTO chapters (novel_id, chapter_num, title, content)"
        " VALUES (?, 1, '第一回', ?)",
        (novel_id, corpus_text),
    )
    fact = {"characters": [{"name": n} for n in fact_names]}
    await memory_db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json)"
        " VALUES (?, ?, ?)",
        (novel_id, cursor.lastrowid, json.dumps(fact, ensure_ascii=False)),
    )
    for name, freq, etype in dict_rows or []:
        await memory_db.execute(
            "INSERT INTO entity_dictionary"
            " (novel_id, name, frequency, aliases, entity_type, source)"
            " VALUES (?, ?, ?, '[]', ?, 'test')",
            (novel_id, name, freq, etype),
        )
    await memory_db.commit()


async def _run_grounded_resolve(memory_db, tmp_path, novel_id, llm):
    """以林惊羽/林小凡同簇的 mock embedding 跑 resolve_novel。"""
    from src.services import hallucination_filter

    hallucination_filter.invalidate_cache(novel_id)
    alias_resolver.invalidate_alias_cache(novel_id)
    group_of = {"林惊羽": 0, "林小凡": 0}

    async def _noop_cost(_usage):
        return None

    patches = _patch_grounded_db(memory_db) + [
        patch("src.services.entity_resolver._record_llm_cost", _noop_cost),
    ]
    for p in patches:
        p.start()
    try:
        report = await resolve_novel(
            novel_id, llm=llm, embed_fn=_one_hot_embed(group_of),
            log_path=tmp_path / "er.jsonl",
        )
        rows = await entity_override_store.load_overrides(novel_id)
    finally:
        for p in patches:
            p.stop()
    return report, rows


class TestGroundedCanonical:
    """B1–B3:ER canonical 原文锚定(canonical-name 污染防线)。"""

    @pytest.mark.asyncio
    async def test_collect_person_names_marks_grounded(self, memory_db):
        """B2:词典型名字直接 grounded;facts-only 名字按 corpus 锚定。"""
        novel = "novel-er-grounded-meta"
        await _seed_grounded_db(
            memory_db, novel,
            corpus_text="林惊羽仗剑而立,远处传来钟声。",
            fact_names=["林惊羽", "林小凡"],
            dict_rows=[("孙悟空", 150, "person")],
        )
        from src.services import hallucination_filter
        from src.services.entity_resolver import collect_person_names

        hallucination_filter.invalidate_cache(novel)
        patches = _patch_grounded_db(memory_db)
        for p in patches:
            p.start()
        try:
            meta = await collect_person_names(novel)
        finally:
            for p in patches:
                p.stop()

        # 词典型:快速路径,无需扫全文
        assert meta["孙悟空"]["in_dict"] is True
        assert meta["孙悟空"]["grounded"] is True
        # facts-only:按原文锚定
        assert meta["林惊羽"]["in_dict"] is False
        assert meta["林惊羽"]["grounded"] is True
        assert meta["林小凡"]["in_dict"] is False
        assert meta["林小凡"]["grounded"] is False

    @pytest.mark.asyncio
    async def test_grounded_canonical_written_normally(self, memory_db, tmp_path):
        """canonical grounded → override 正常写入,带 grounded 标志 (B1/B3)。"""
        novel = "novel-er-grounded-ok"
        await _seed_grounded_db(
            memory_db, novel,
            corpus_text="林惊羽仗剑而立,林小凡在旁抚琴。",
            fact_names=["林惊羽", "林小凡"],
        )
        llm = MockLLM(lambda members: [
            {"canonical": "林惊羽", "members": members, "reason": "同一人物"},
        ])
        report, rows = await _run_grounded_resolve(
            memory_db, tmp_path, novel, llm
        )
        assert report["merges"] == 1
        assert len(rows) == 1
        j = rows[0]["override_json"]
        assert j["canonical"] == "林惊羽"
        assert j["canonical_grounded"] is True
        assert j["grounded_reselected"] is False

    @pytest.mark.asyncio
    async def test_ungrounded_canonical_reselected(self, memory_db, tmp_path):
        """canonical 不 grounded 但组内有 grounded 成员 → 改选 (B1)。"""
        novel = "novel-er-grounded-reselect"
        # 原文只有 林惊羽;林小凡 是 LLM 幻觉名
        await _seed_grounded_db(
            memory_db, novel,
            corpus_text="林惊羽仗剑而立,远处传来钟声。",
            fact_names=["林惊羽", "林小凡"],
        )
        llm = MockLLM(lambda members: [
            {"canonical": "林小凡", "members": members, "reason": "同一人物"},
        ])
        report, rows = await _run_grounded_resolve(
            memory_db, tmp_path, novel, llm
        )
        assert report["merges"] == 1
        assert len(rows) == 1
        j = rows[0]["override_json"]
        # 改选为组内唯一 grounded 成员
        assert j["canonical"] == "林惊羽"
        assert j["canonical_grounded"] is True
        assert j["grounded_reselected"] is True
        assert rows[0]["override_key"] == "林惊羽"
        assert set(j["members"]) == {"林惊羽", "林小凡"}

    @pytest.mark.asyncio
    async def test_all_ungrounded_group_rejected(self, memory_db, tmp_path):
        """全组不 grounded → 拒绝,不写 override,决策日志含拒绝记录 (B1/B3)。"""
        novel = "novel-er-grounded-reject"
        await _seed_grounded_db(
            memory_db, novel,
            corpus_text="这一天,天色阴沉,大雨滂沱。",
            fact_names=["林惊羽", "林小凡"],
        )
        llm = MockLLM(lambda members: [
            {"canonical": "林小凡", "members": members, "reason": "同一人物"},
        ])
        report, rows = await _run_grounded_resolve(
            memory_db, tmp_path, novel, llm
        )
        assert report["merges"] == 0
        assert rows == []  # 无 override 写入

        log = tmp_path / "er.jsonl"
        records = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rejected = [
            r for rec in records for r in rec.get("rejected_groups", [])
        ]
        assert any(
            "no grounded member" in r.get("rejected_reason", "")
            for r in rejected
        )

    @pytest.mark.asyncio
    async def test_dict_names_fast_path_skips_corpus(self, memory_db, tmp_path):
        """词典型名字走 grounded 快速路径:不构建/扫描全书 corpus (B1)。"""
        novel = "novel-er-grounded-dict"
        await _seed_grounded_db(
            memory_db, novel,
            corpus_text="正文里没有候选名。",
            fact_names=["林惊羽", "林小凡"],
            dict_rows=[("林惊羽", 120, "person"), ("林小凡", 80, "person")],
        )
        llm = MockLLM(lambda members: [
            {"canonical": "林小凡", "members": members, "reason": "同一人物"},
        ])

        async def _boom(_novel_id):
            raise AssertionError("_get_corpus should not be called "
                                 "when all candidates are dict names")

        from src.services import hallucination_filter

        hallucination_filter.invalidate_cache(novel)
        alias_resolver.invalidate_alias_cache(novel)
        group_of = {"林惊羽": 0, "林小凡": 0}

        async def _noop_cost(_usage):
            return None

        patches = _patch_grounded_db(memory_db) + [
            patch("src.services.entity_resolver._record_llm_cost", _noop_cost),
            patch("src.services.hallucination_filter._get_corpus", _boom),
        ]
        for p in patches:
            p.start()
        try:
            report = await resolve_novel(
                novel, llm=llm, embed_fn=_one_hot_embed(group_of),
                log_path=tmp_path / "er.jsonl",
            )
            rows = await entity_override_store.load_overrides(novel)
        finally:
            for p in patches:
                p.stop()

        assert report["merges"] == 1
        assert len(rows) == 1
        j = rows[0]["override_json"]
        # 词典型 canonical 视为 grounded,不发生改选
        assert j["canonical"] == "林小凡"
        assert j["canonical_grounded"] is True
        assert j["grounded_reselected"] is False
