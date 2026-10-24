"""LLM 用量归因：stage 上下文 + 逐次调用 JSONL 记录。

背景见 docs/analysis/analysis-cost-baseline-2026-10.md —— 每章总 token 无法回答
"钱花在抽取还是召回/场景/幻觉判定上"，所以调用点用 llm_stage(...) 打标签，
开启 AI_READER_LLM_USAGE_LOG 后每次调用落一行 JSONL。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.infra.llm_client import (
    LlmUsage,
    current_llm_stage,
    llm_stage,
    record_llm_usage,
)


def _usage(prompt: int = 100, completion: int = 50) -> LlmUsage:
    return LlmUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


def test_stage_context_manager_nests_and_resets():
    assert current_llm_stage() == "other"
    with llm_stage("extract"):
        assert current_llm_stage() == "extract"
        with llm_stage("vote"):
            assert current_llm_stage() == "vote"
        assert current_llm_stage() == "extract"
    assert current_llm_stage() == "other"


def test_stage_resets_even_when_block_raises():
    with pytest.raises(RuntimeError), llm_stage("recall"):
        raise RuntimeError("boom")
    assert current_llm_stage() == "other"


@pytest.mark.asyncio
async def test_stage_is_isolated_between_concurrent_tasks():
    """并发分析时，抽取 worker 与合并器的标签不能互相污染。"""
    seen: dict[str, str] = {}

    async def worker(name: str, stage: str) -> None:
        with llm_stage(stage):
            await asyncio.sleep(0)  # 让出控制权，制造交错
            seen[name] = current_llm_stage()

    await asyncio.gather(worker("a", "extract"), worker("b", "recall"))
    assert seen == {"a": "extract", "b": "recall"}


def test_record_usage_writes_stage_tagged_jsonl(tmp_path, monkeypatch):
    log = tmp_path / "llm_usage.jsonl"
    monkeypatch.setenv("AI_READER_LLM_USAGE_LOG", str(log))

    with llm_stage("recall"):
        record_llm_usage("deepseek-chat", _usage(1200, 300))
    record_llm_usage("deepseek-chat", _usage(5000, 800))

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2

    assert rows[0]["stage"] == "recall"
    assert rows[0]["model"] == "deepseek-chat"
    assert rows[0]["prompt_tokens"] == 1200
    assert rows[0]["completion_tokens"] == 300
    assert rows[0]["total_tokens"] == 1500
    assert rows[0]["truncated"] is False

    # 离开 with 后回到默认标签
    assert rows[1]["stage"] == "other"


def test_record_usage_is_noop_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_READER_LLM_USAGE_LOG", raising=False)
    record_llm_usage("deepseek-chat", _usage())
    # 没开日志就不该在目录里产生任何文件
    assert list(tmp_path.iterdir()) == []


def test_record_usage_never_raises_on_unwritable_path(tmp_path, monkeypatch):
    """用量日志失败绝不能打断分析。"""
    log_dir = tmp_path / "as_dir"
    log_dir.mkdir()
    monkeypatch.setenv("AI_READER_LLM_USAGE_LOG", str(log_dir))  # 目录当文件写 → OSError

    record_llm_usage("deepseek-chat", _usage())  # 不应抛异常


def test_record_usage_creates_parent_directories(tmp_path, monkeypatch):
    log = tmp_path / "nested" / "dir" / "llm_usage.jsonl"
    monkeypatch.setenv("AI_READER_LLM_USAGE_LOG", str(log))

    record_llm_usage("MiniMax-M2.7", _usage(10, 5))

    assert log.exists()
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["model"] == "MiniMax-M2.7"


def test_usage_log_records_prefix_cache_fields(tmp_path, monkeypatch):
    """DeepSeek 的 prompt_cache_hit/miss_tokens 必须落到日志里，否则 L3 无法量化。"""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("AI_READER_LLM_USAGE_LOG", str(log))

    usage = LlmUsage(
        prompt_tokens=1200, completion_tokens=300, total_tokens=1500,
        cache_hit_tokens=900, cache_miss_tokens=300,
    )
    record_llm_usage("deepseek-chat", usage)

    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["cache_hit_tokens"] == 900
    assert row["cache_miss_tokens"] == 300


def test_usage_defaults_to_zero_cache_tokens():
    """没有缓存字段的提供商（Ollama 等）保持 0，不制造假数据。"""
    usage = LlmUsage(prompt_tokens=10)
    assert usage.cache_hit_tokens == 0
    assert usage.cache_miss_tokens == 0
