"""Runtime configuration tests for the Codex provider."""

from unittest.mock import AsyncMock, patch

import pytest

from src.api.main import _restore_persisted_settings, health
from src.infra import config, llm_client
from src.infra.codex_exec_client import CodexExecClient
from src.services.world_structure_agent import WorldStructureAgent


def test_get_llm_client_builds_codex_adapter(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_BIN", "codex-test")
    monkeypatch.setattr(config, "CODEX_MODEL", "")
    monkeypatch.setattr(config, "CODEX_REASONING_EFFORT", "low")
    monkeypatch.setattr(config, "CODEX_MIN_TIMEOUT_SECONDS", 42)
    monkeypatch.setattr(llm_client, "_client", None)

    result = llm_client.get_llm_client()

    assert isinstance(result, CodexExecClient)
    assert result.codex_bin == "codex-test"
    assert result.codex_model == ""
    assert result.min_timeout_seconds == 42


@pytest.mark.asyncio
async def test_explicit_codex_env_wins_over_persisted_ui_mode(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")

    with patch(
        "src.db.sqlite_db.get_connection",
        new=AsyncMock(),
    ) as get_connection:
        await _restore_persisted_settings()

    get_connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_health_exposes_quota_sensitive_codex_profile(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_MODEL", "")
    monkeypatch.setattr(config, "CODEX_REASONING_EFFORT", "low")
    monkeypatch.setattr(config, "CODEX_EXAMPLE_COUNT", 1)
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 10)
    monkeypatch.setattr(config, "SCENE_LLM_ENABLED", False)
    monkeypatch.setattr(config, "AUXILIARY_LLM_ENABLED", False)
    monkeypatch.setattr(config, "VOT_SPATIAL_ENABLED", False)

    result = await health()

    assert result["llm_provider"] == "codex"
    assert result["llm_model"] == "codex-default"
    assert result["codex_profile"] == {
        "reasoning_effort": "low",
        "concurrency": 1,
        "example_count": 1,
        "max_batch_chapters": 10,
        "scene_llm_enabled": False,
        "auxiliary_llm_enabled": False,
        "vot_spatial_enabled": False,
    }


@pytest.mark.asyncio
async def test_health_omits_codex_profile_for_other_providers(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen-test")

    result = await health()

    assert result == {
        "status": "ok",
        "llm_provider": "ollama",
        "llm_model": "qwen-test",
    }


def test_auxiliary_flag_disables_world_structure_llm(monkeypatch):
    monkeypatch.setattr(config, "AUXILIARY_LLM_ENABLED", False)
    agent = WorldStructureAgent("novel")

    assert agent._should_trigger_llm(1, [], object()) is False
