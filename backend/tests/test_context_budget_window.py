"""Context window → token budget.

The budget interpolates between 8K and 128K and clamps above 128K, so a 1M
model is *not* more generous than a 128K one. The only case that changes
behaviour is a model whose window is **smaller** than 128K — that must lower
the chapter budget instead of sending an oversized request.
"""

from __future__ import annotations

import pytest

from src.infra import config, context_budget, model_catalog


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_PROVIDER_FORMAT", "CONTEXT_WINDOW_SIZE"):
        monkeypatch.setattr(config, name, getattr(config, name))
    yield


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_cloud_model_defaults_to_128k(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_PROVIDER_FORMAT", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "totally-unknown-model")

    ctx = await context_budget.detect_and_update_context_window()

    assert ctx == 131072
    assert config.CONTEXT_WINDOW_SIZE == 131072


@pytest.mark.asyncio(loop_scope="session")
async def test_one_million_window_is_clamped_to_128k(monkeypatch):
    """1M and 128K must produce identical budgets — that is the contract."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_PROVIDER_FORMAT", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "kimi-k3.1-pro")

    ctx = await context_budget.detect_and_update_context_window()

    assert ctx == 131072
    # Compare the derived parameters, not the dataclass: `context_window`
    # itself echoes the input, so the whole-object equality would fail on a
    # field that has no behavioural meaning.
    big = context_budget.compute_budget(1_048_576)
    same = context_budget.compute_budget(131072)
    assert big.max_chapter_len == same.max_chapter_len
    assert big.extraction_num_ctx == same.extraction_num_ctx
    assert big.context_max_chars == same.context_max_chars
    assert big.segment_enabled == same.segment_enabled


@pytest.mark.asyncio(loop_scope="session")
async def test_small_declared_window_lowers_the_budget(monkeypatch):
    """A 32K model must not be fed a 128K-sized chapter."""
    monkeypatch.setattr(
        model_catalog,
        "_MODEL_RULES",
        (
            (
                "tiny-window-model",
                model_catalog.ModelCapability(context_window=32768),
            ),
        ),
    )
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_PROVIDER_FORMAT", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "tiny-window-model")

    ctx = await context_budget.detect_and_update_context_window()

    assert ctx == 32768
    assert config.CONTEXT_WINDOW_SIZE == 32768
    assert context_budget.compute_budget(ctx).max_chapter_len < 50000
