"""Regression tests for Phase-1 entity candidate typing."""

from collections import Counter

from src.extraction.entity_pre_scanner import EntityPreScanner


def test_dialogue_patterns_do_not_turn_common_actions_into_people():
    scanner = EntityPreScanner()

    entries = scanner._merge_candidates(
        word_freq=Counter({"孟奇": 20, "开口": 50, "一口气": 40}),
        ngram_freq=Counter(),
        dialogue_names=Counter({"孟奇": 5, "开口": 10, "一口气": 2}),
        title_words=Counter(),
        naming_names=Counter(),
        suffix_types={},
        full_text="孟奇开口道。一口气说完。",
    )
    by_name = {entry.name: entry for entry in entries}

    assert by_name["孟奇"].entity_type == "person"
    assert by_name["开口"].entity_type == "unknown"
    assert by_name["一口气"].entity_type == "unknown"


def test_naming_pattern_does_not_assume_every_named_entity_is_a_person():
    scanner = EntityPreScanner()

    entries = scanner._merge_candidates(
        word_freq=Counter({"顾小桑": 20, "神话": 20}),
        ngram_freq=Counter(),
        dialogue_names=Counter(),
        title_words=Counter(),
        naming_names=Counter({"顾小桑": 3, "神话": 3}),
        suffix_types={},
        full_text="她名叫顾小桑。组织名为神话。",
    )
    by_name = {entry.name: entry for entry in entries}

    assert by_name["顾小桑"].entity_type == "person"
    assert by_name["神话"].entity_type == "unknown"
