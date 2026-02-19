"""Tests for real-time cost tracking (N3.2).

Tests LLM client usage parsing and cost accumulation in analysis loop.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.llm_client import LLMClient, LlmUsage


@pytest.mark.asyncio
async def test_ollama_generate_returns_usage():
    """Ollama generate() should return (content, LlmUsage) tuple."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "message": {"content": "Hello world"},
        "prompt_eval_count": 150,
        "eval_count": 42,
    }

    client = LLMClient(base_url="http://localhost:11434", model="test-model")

    with patch("src.infra.llm_client.httpx.AsyncClient") as mock_httpx:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_ctx

        result, usage = await client.generate(
            system="You are helpful.",
            prompt="Say hello.",
        )

    assert result == "Hello world"
    assert isinstance(usage, LlmUsage)
    assert usage.prompt_tokens == 150
    assert usage.completion_tokens == 42
    assert usage.total_tokens == 192


@pytest.mark.asyncio
async def test_ollama_generate_returns_usage_with_format():
    """Ollama generate() with format should return (dict, LlmUsage)."""
    test_json = {"characters": ["Alice"]}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "message": {"content": json.dumps(test_json)},
        "prompt_eval_count": 500,
        "eval_count": 100,
    }

    client = LLMClient(base_url="http://localhost:11434", model="test-model")

    with patch("src.infra.llm_client.httpx.AsyncClient") as mock_httpx:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_ctx

        result, usage = await client.generate(
            system="Extract JSON.",
            prompt="Text here.",
            format={"type": "object"},
        )

    assert result == test_json
    assert usage.prompt_tokens == 500
    assert usage.completion_tokens == 100


@pytest.mark.asyncio
async def test_ollama_generate_missing_usage_fields():
    """Ollama generate() should default to 0 when usage fields are missing."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "message": {"content": "No usage data"},
        # No prompt_eval_count or eval_count
    }

    client = LLMClient(base_url="http://localhost:11434", model="test-model")

    with patch("src.infra.llm_client.httpx.AsyncClient") as mock_httpx:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_ctx

        result, usage = await client.generate(
            system="Test.",
            prompt="Test.",
        )

    assert result == "No usage data"
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0


@pytest.mark.asyncio
async def test_openai_generate_returns_usage():
    """OpenAI generate() should return (content, LlmUsage) tuple."""
    from src.infra.openai_client import OpenAICompatibleClient

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "total_tokens": 250,
        },
    }

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="test-model",
    )

    with patch.object(client, "_make_client") as mock_make:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=mock_response)
        mock_make.return_value = mock_ctx

        result, usage = await client.generate(
            system="You are helpful.",
            prompt="Say hi.",
        )

    assert result == "Hi"
    assert isinstance(usage, LlmUsage)
    assert usage.prompt_tokens == 200
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 250


@pytest.mark.asyncio
async def test_openai_generate_returns_usage_with_format():
    """OpenAI generate() with format should return (dict, LlmUsage)."""
    from src.infra.openai_client import OpenAICompatibleClient

    test_json = {"events": []}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [
            {"message": {"content": json.dumps(test_json)}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 300,
            "total_tokens": 1300,
        },
    }

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="test-model",
    )

    with patch.object(client, "_make_client") as mock_make:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=mock_response)
        mock_make.return_value = mock_ctx

        result, usage = await client.generate(
            system="Extract.",
            prompt="Text.",
            format={"type": "object"},
        )

    assert result == test_json
    assert usage.prompt_tokens == 1000
    assert usage.completion_tokens == 300


def test_cost_accumulation_formula():
    """Cost accumulation should match the expected formula."""
    from src.services.cost_service import get_pricing

    inp_price, out_price = get_pricing("deepseek-chat")

    # Simulate 3 chapters of usage
    total_input = 0
    total_output = 0
    total_cost = 0.0

    for _ in range(3):
        prompt_tokens = 50000
        completion_tokens = 4000
        total_input += prompt_tokens
        total_output += completion_tokens
        spent = (prompt_tokens / 1_000_000) * inp_price + (
            completion_tokens / 1_000_000
        ) * out_price
        total_cost += spent

    total_cost = round(total_cost, 4)
    total_cny = round(total_cost * 7.2, 2)

    # Verify
    expected_cost = round(
        3 * ((50000 / 1_000_000) * 0.27 + (4000 / 1_000_000) * 1.10), 4
    )
    assert total_cost == expected_cost
    assert total_input == 150000
    assert total_output == 12000

    # Remaining estimate: (total_cost / 3) * remaining
    remaining_chapters = 7
    avg = total_cost / 3
    est_remaining = round(avg * remaining_chapters, 4)
    assert est_remaining > 0
    assert est_remaining > total_cost  # 7 remaining > 3 done → remaining > spent
