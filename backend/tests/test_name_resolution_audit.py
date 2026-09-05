"""C1/C2 名字决策 provenance 审计测试 (issue #70)。

C1: NameResolver.resolve() 每次改写落 name_resolution_log.jsonl
    (field/from/to/source 齐全);无改写不写文件;改写行为本身不变。
C2: FactValidator 的 alias-merge 吞并与泛称改名(「地点·泛称」)落同一通道,
    rule 字段区分。
"""

import json

from src.extraction.fact_validator import FactValidator
from src.extraction.name_resolver import NameResolver
from src.models.chapter_fact import (
    ChapterFact,
    CharacterFact,
    EventFact,
    LocationFact,
    RelationshipFact,
)
from src.models.entity_dict import EntityDictEntry


def _read_jsonl(path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestNameResolverAudit:
    """C1: resolve() 改写审计。"""

    def test_resolve_writes_audit_records(self, tmp_path):
        """改写产生审计记录:字段齐全,source=dict(实体词典载入)。"""
        log = tmp_path / "name_resolution_log.jsonl"
        nr = NameResolver()
        nr.load_from_entity_dictionary([
            EntityDictEntry(name="孙悟空", entity_type="person", frequency=100,
                            aliases=["行者"], source="freq"),
        ])
        fact = ChapterFact(
            chapter_id=3, novel_id="novel-x",
            characters=[CharacterFact(name="行者"), CharacterFact(name="唐僧")],
            relationships=[
                RelationshipFact(person_a="行者", person_b="唐僧",
                                 relation_type="师徒"),
            ],
            events=[EventFact(summary="行者打妖怪", type="战斗",
                              participants=["行者", "唐僧"])],
        )
        nr.resolve(fact, log_path=log)

        records = _read_jsonl(log)
        by_field: dict[str, int] = {}
        for r in records:
            assert r["novel_id"] == "novel-x"
            assert r["chapter_id"] == 3
            assert r["from"] == "行者"
            assert r["to"] == "孙悟空"
            assert r["source"] == "dict"
            assert r["rule"] == "name_resolve"
            assert r["timestamp"]
            by_field[r["field"]] = by_field.get(r["field"], 0) + 1
        # characters ×1 + relationships person_a ×1 + events participant ×1
        assert by_field == {"characters": 1, "relationships": 1, "events": 1}

        # 改写行为本身不变
        assert [c.name for c in fact.characters] == ["孙悟空", "唐僧"]
        assert fact.relationships[0].person_a == "孙悟空"
        assert fact.events[0].participants == ["孙悟空", "唐僧"]

    def test_accumulated_source(self, tmp_path):
        """章节累积的映射改写,source=accumulated。"""
        log = tmp_path / "log.jsonl"
        nr = NameResolver()
        nr.accumulate_from_chapter(ChapterFact(
            chapter_id=1, novel_id="n",
            characters=[CharacterFact(name="孙悟空", new_aliases=["齐天大圣"])],
        ))
        fact2 = ChapterFact(chapter_id=2, novel_id="n",
                            characters=[CharacterFact(name="齐天大圣")])
        nr.resolve(fact2, log_path=log)
        (rec,) = _read_jsonl(log)
        assert rec["source"] == "accumulated"
        assert rec["chapter_id"] == 2
        assert fact2.characters[0].name == "孙悟空"

    def test_no_rewrite_no_log(self, tmp_path):
        """有映射但本章无命中 → 不写文件。"""
        log = tmp_path / "log.jsonl"
        nr = NameResolver()
        nr._canonical_map = {"行者": "孙悟空"}
        fact = ChapterFact(chapter_id=1, novel_id="n",
                           characters=[CharacterFact(name="唐僧")],
                           relationships=[
                               RelationshipFact(person_a="唐僧", person_b="孙悟空",
                                                relation_type="师徒"),
                           ])
        nr.resolve(fact, log_path=log)
        assert not log.exists()

    def test_no_mapping_no_log(self, tmp_path):
        """空映射直接返回 → 不写文件。"""
        log = tmp_path / "log.jsonl"
        nr = NameResolver()
        fact = ChapterFact(chapter_id=1, novel_id="n",
                           characters=[CharacterFact(name="行者")])
        nr.resolve(fact, log_path=log)
        assert not log.exists()
        assert fact.characters[0].name == "行者"  # 行为不变

    def test_batch_append_single_write(self, tmp_path):
        """一章多处改写批量落同一文件;再次调用追加而非覆盖。"""
        log = tmp_path / "log.jsonl"
        nr = NameResolver()
        nr._canonical_map = {"行者": "孙悟空"}
        for ch in (1, 2):
            nr.resolve(ChapterFact(
                chapter_id=ch, novel_id="n",
                characters=[CharacterFact(name="行者")],
            ), log_path=log)
        records = _read_jsonl(log)
        assert len(records) == 2
        assert [r["chapter_id"] for r in records] == [1, 2]


class TestFactValidatorAudit:
    """C2: fact_validator 两类决策落同一审计通道。"""

    def test_alias_merge_logged(self, tmp_path):
        """alias-merge 吞并角色:记录 from=被吞并名,to=保留名。"""
        log = tmp_path / "fv.jsonl"
        validator = FactValidator(audit_log_path=log)
        fact = ChapterFact(
            chapter_id=5, novel_id="n1",
            characters=[
                CharacterFact(name="韩立", new_aliases=["二愣子"]),
                CharacterFact(name="二愣子"),
            ],
        )
        out = validator.validate(fact)

        # 行为不变:二愣子被并入韩立
        assert [c.name for c in out.characters] == ["韩立"]
        assert "二愣子" in out.characters[0].new_aliases

        merges = [r for r in _read_jsonl(log) if r["rule"] == "alias_merge"]
        assert len(merges) == 1
        rec = merges[0]
        assert rec["from"] == "二愣子"
        assert rec["to"] == "韩立"
        assert rec["source"] == "correction"
        assert rec["novel_id"] == "n1"
        assert rec["chapter_id"] == 5

    def test_generic_person_rename_logged(self, tmp_path):
        """泛称改名:樵夫 → 灵台方寸山·樵夫,记录规则名。"""
        log = tmp_path / "fv.jsonl"
        validator = FactValidator(audit_log_path=log)
        fact = ChapterFact(
            chapter_id=2, novel_id="n1",
            characters=[CharacterFact(name="樵夫")],
            locations=[LocationFact(name="灵台方寸山", type="山", role="setting")],
        )
        out = validator.validate(fact)

        # 行为不变:泛称按主场景消歧
        assert [c.name for c in out.characters] == ["灵台方寸山·樵夫"]

        renames = [
            r for r in _read_jsonl(log) if r["rule"] == "generic_person_rename"
        ]
        assert len(renames) == 1
        rec = renames[0]
        assert rec["from"] == "樵夫"
        assert rec["to"] == "灵台方寸山·樵夫"
        assert rec["source"] == "correction"
        assert rec["chapter_id"] == 2

    def test_no_decision_no_log(self, tmp_path):
        """无吞并无改名 → 不写文件。"""
        log = tmp_path / "fv.jsonl"
        validator = FactValidator(audit_log_path=log)
        fact = ChapterFact(chapter_id=1, novel_id="n1",
                           characters=[CharacterFact(name="孙悟空")])
        validator.validate(fact)
        assert not log.exists()

    def test_skip_validation_no_log(self, tmp_path):
        """ablation 模式(skip_validation)短路返回,不写审计。"""
        log = tmp_path / "fv.jsonl"
        validator = FactValidator(skip_validation=True, audit_log_path=log)
        fact = ChapterFact(
            chapter_id=1, novel_id="n1",
            characters=[
                CharacterFact(name="韩立", new_aliases=["二愣子"]),
                CharacterFact(name="二愣子"),
            ],
        )
        validator.validate(fact)
        assert not log.exists()
