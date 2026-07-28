"""Tests for the Codex batch-size safety boundary."""

import pytest
from fastapi import HTTPException

from src.api.routes.analysis import _enforce_codex_batch_limit
from src.infra import config


def test_codex_batch_limit_accepts_exact_limit(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 10)

    _enforce_codex_batch_limit(34, 43)


def test_codex_batch_limit_rejects_oversized_range(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 10)

    with pytest.raises(HTTPException) as exc_info:
        _enforce_codex_batch_limit(34, 44)

    assert exc_info.value.status_code == 400
    assert "10 章" in exc_info.value.detail
    assert "11 章" in exc_info.value.detail


def test_batch_limit_does_not_affect_other_providers(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 10)

    _enforce_codex_batch_limit(1, 100)


def test_zero_disables_codex_batch_limit(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 0)

    _enforce_codex_batch_limit(1, 100)
