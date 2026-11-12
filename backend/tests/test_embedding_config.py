"""Tests for the configurable embedding providers (issue #20)."""

import pytest

from src.infra import config, secret_store
from src.services import embedding_service


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch):
    """Keep global config untouched across tests."""
    for name in (
        "EMBEDDING_PROVIDER", "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL",
        "EMBEDDING_CLOUD_MODEL", "EMBEDDING_DIMENSIONS",
    ):
        monkeypatch.setattr(config, name, getattr(config, name))
    yield


def test_default_llm_secret_slot_unchanged():
    """Existing installs must keep reading the same keychain/DB entries."""
    assert secret_store._slot_names("llm") == ("llm-api-key", "llm_api_key_b64")


def test_embedding_secret_slot_is_isolated():
    key_name, db_key = secret_store._slot_names("embedding")
    assert key_name == "embedding-api-key"
    assert db_key == "embedding_api_key_b64"
    assert (key_name, db_key) != secret_store._slot_names("llm")


def test_update_embedding_config_resets_cached_function(monkeypatch):
    embedding_service._embed_fn = object()
    calls = []
    monkeypatch.setattr(
        embedding_service, "reset_embed_fn", lambda: calls.append(True)
    )
    config.update_embedding_config(
        provider="openai", api_key="k", base_url="https://example/v1",
        model="text-embedding-3-small", dimensions=768,
    )
    assert calls == [True]
    assert config.EMBEDDING_PROVIDER == "openai"
    assert config.EMBEDDING_DIMENSIONS == 768
    embedding_service._embed_fn = None


def test_signature_changes_with_embedding_space():
    config.update_embedding_config("local", "", "", "text-embedding-3-small")
    before = config.embedding_signature()
    config.update_embedding_config(
        "openai", "k", "https://example/v1", "text-embedding-3-large"
    )
    after = config.embedding_signature()
    assert before != after
    assert "text-embedding-3-large" in after


def test_incomplete_remote_config_fails_loudly_not_silent_local_fallback(monkeypatch):
    """A quietly ignored setting is worse than an explicit error."""
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "")
    monkeypatch.setattr(config, "EMBEDDING_CLOUD_MODEL", "m")
    with pytest.raises(RuntimeError, match="配置不完整"):
        embedding_service._get_remote_embed_fn()


def test_ollama_provider_uses_default_local_url(monkeypatch):
    captured = {}

    class FakeFn:
        def __init__(self, url, model_name):
            captured["url"] = url
            captured["model"] = model_name

    import chromadb.utils.embedding_functions as ef

    monkeypatch.setattr(ef, "OllamaEmbeddingFunction", FakeFn, raising=False)
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "")
    monkeypatch.setattr(config, "EMBEDDING_CLOUD_MODEL", "nomic-embed-text")
    fn = embedding_service._get_remote_embed_fn()
    assert isinstance(fn, FakeFn)
    assert captured == {"url": "http://localhost:11434", "model": "nomic-embed-text"}


@pytest.fixture
def embedding_client(monkeypatch):
    """HTTP-level smoke harness: real router, isolated DB / keychain."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import settings as settings_routes
    from src.db import sqlite_db

    store: dict[str, str] = {}

    async def fake_read():
        return dict(store)

    async def fake_load(slot="llm"):
        return "sk-test-12345678" if slot == "embedding" else ""

    async def fake_save(key, slot="llm"):
        return "database"

    class FakeConn:
        async def execute(self, sql, params=()):
            if sql.strip().startswith("INSERT"):
                key, value = params
                store[key] = value
            return self

        async def commit(self):
            return None

        async def close(self):
            return None

    async def fake_conn():
        return FakeConn()

    monkeypatch.setattr(settings_routes, "_read_embedding_settings", fake_read)
    monkeypatch.setattr(secret_store, "load_api_key", fake_load)
    monkeypatch.setattr(secret_store, "save_api_key", fake_save)
    monkeypatch.setattr(sqlite_db, "get_connection", fake_conn)

    app = FastAPI()
    app.include_router(settings_routes.router)
    with TestClient(app) as client:
        yield client, store


def test_get_embedding_config_masks_api_key(embedding_client):
    client, _ = embedding_client
    body = client.get("/api/settings/embedding/config").json()
    assert body["has_api_key"] is True
    assert body["api_key_masked"] == "sk-t****5678"
    assert "sk-test-12345678" not in str(body)


def test_save_embedding_config_rejects_unknown_provider(embedding_client):
    client, _ = embedding_client
    r = client.post("/api/settings/embedding/config", json={"provider": "bogus"})
    assert r.status_code == 400


def test_first_provider_switch_warns_about_stale_index(embedding_client):
    """Old installs have no stored signature but DO have a local-built index."""
    client, store = embedding_client
    assert "embedding_signature" not in store
    r = client.post("/api/settings/embedding/config", json={
        "provider": "openai", "base_url": "https://example/v1",
        "model": "text-embedding-3-small", "dimensions": 768,
    })
    assert r.status_code == 200
    assert r.json()["index_warning"] is True
    assert store["embedding_provider"] == "openai"


def test_repeat_save_of_same_config_does_not_warn(embedding_client):
    client, _ = embedding_client
    payload = {
        "provider": "openai", "base_url": "https://example/v1",
        "model": "text-embedding-3-small", "dimensions": 768,
    }
    assert client.post("/api/settings/embedding/config", json=payload).json()["index_warning"] is True
    assert client.post("/api/settings/embedding/config", json=payload).json()["index_warning"] is False
