"""Tests for settings routes — Ollama detection, start, recommendations, and cloud config."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ConnectError, Request

from src.api.routes.settings import (
    CLOUD_PROVIDERS,
    AdvancedSettingsRequest,
    SwitchModeRequest,
    ValidateCloudRequest,
    _check_ollama,
    _get_total_ram_gb,
    get_cloud_providers,
    get_hardware,
    get_model_recommendations,
    restore_defaults,
    save_advanced_settings,
    start_ollama,
    switch_llm_mode,
    validate_cloud_api,
)
from src.infra.secret_store import (
    _try_keyring_load,
    _try_keyring_save,
)


# ── Ollama three-state detection ──────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_check_ollama_running():
    """When Ollama is installed and API responds, status should be 'running'."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "models": [
            {
                "name": "qwen3:8b",
                "size": 5365624832,
                "modified_at": "2025-06-01T00:00:00Z",
            },
            {
                "name": "qwen2.5:7b",
                "size": 4700000000,
                "modified_at": "2025-05-01T00:00:00Z",
            },
        ]
    }

    with patch("src.api.routes.settings.shutil") as mock_shutil:
        mock_shutil.which.return_value = "/usr/local/bin/ollama"
        with patch("src.api.routes.settings.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await _check_ollama()

    assert result["ollama_status"] == "running"
    assert result["ollama_running"] is True
    assert result["model_available"] is True
    assert len(result["available_models"]) == 2
    assert result["available_models"][0]["name"] == "qwen3:8b"
    assert result["available_models"][0]["size"] == 5365624832


@pytest.mark.asyncio(loop_scope="session")
async def test_check_ollama_installed_not_running():
    """When ollama binary exists but API is unreachable, status should be 'installed_not_running'."""
    with patch("src.api.routes.settings.shutil") as mock_shutil:
        mock_shutil.which.return_value = "/usr/local/bin/ollama"
        with patch("src.api.routes.settings.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=ConnectError(
                    "Connection refused",
                    request=Request("GET", "http://localhost:11434/api/tags"),
                )
            )
            mock_client_cls.return_value = mock_client

            result = await _check_ollama()

    assert result["ollama_status"] == "installed_not_running"
    assert result["ollama_running"] is False
    assert result["available_models"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_check_ollama_not_installed():
    """When ollama binary is not found, status should be 'not_installed'."""
    with patch("src.api.routes.settings.shutil") as mock_shutil:
        mock_shutil.which.return_value = None
        with patch("src.api.routes.settings.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=ConnectError(
                    "Connection refused",
                    request=Request("GET", "http://localhost:11434/api/tags"),
                )
            )
            mock_client_cls.return_value = mock_client

            result = await _check_ollama()

    assert result["ollama_status"] == "not_installed"
    assert result["ollama_running"] is False


# ── Model size info ──────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_check_ollama_model_size_info():
    """available_models should include size and modified_at."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "models": [
            {
                "name": "llama3:8b",
                "size": 4200000000,
                "modified_at": "2025-07-01T12:00:00Z",
            }
        ]
    }

    with patch("src.api.routes.settings.shutil") as mock_shutil:
        mock_shutil.which.return_value = "/usr/local/bin/ollama"
        with patch("src.api.routes.settings.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await _check_ollama()

    model = result["available_models"][0]
    assert model["name"] == "llama3:8b"
    assert model["size"] == 4200000000
    assert model["modified_at"] == "2025-07-01T12:00:00Z"


# ── Ollama start endpoint ────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_start_ollama_not_installed():
    """Should return error when Ollama is not installed."""
    with patch("src.api.routes.settings.shutil") as mock_shutil:
        mock_shutil.which.return_value = None
        result = await start_ollama()

    assert result["success"] is False
    assert "未安装" in result["error"]


@pytest.mark.asyncio(loop_scope="session")
async def test_start_ollama_success():
    """Should start Ollama and poll until available."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("src.api.routes.settings.shutil") as mock_shutil:
        mock_shutil.which.return_value = "/usr/local/bin/ollama"
        with patch("src.api.routes.settings.subprocess.Popen") as mock_popen:
            with patch("src.api.routes.settings.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                with patch("src.api.routes.settings.asyncio.sleep", new_callable=AsyncMock):
                    result = await start_ollama()

    assert result["success"] is True
    mock_popen.assert_called_once()


# ── Hardware detection ───────────────────────────────


def test_get_total_ram_gb():
    """Should return positive RAM value on macOS/Linux."""
    ram = _get_total_ram_gb()
    assert ram > 0


@pytest.mark.asyncio(loop_scope="session")
async def test_get_hardware():
    """Should return hardware info dict."""
    result = await get_hardware()
    assert "total_ram_gb" in result
    assert "platform" in result
    assert "arch" in result
    assert result["total_ram_gb"] > 0


# ── Model recommendations ───────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_recommendations_32gb():
    """With 32GB RAM, should recommend qwen3:14b and include all models."""
    with patch("src.api.routes.settings._get_total_ram_gb", return_value=32.0):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        with patch("src.api.routes.settings.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await get_model_recommendations()

    assert result["total_ram_gb"] == 32.0
    recs = result["recommendations"]
    assert len(recs) == 3
    recommended = [r for r in recs if r["recommended"]]
    assert len(recommended) == 1
    assert recommended[0]["name"] == "qwen3:14b"


@pytest.mark.asyncio(loop_scope="session")
async def test_recommendations_16gb():
    """With 16GB RAM, should recommend qwen3:8b and exclude 14b."""
    with patch("src.api.routes.settings._get_total_ram_gb", return_value=16.0):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        with patch("src.api.routes.settings.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await get_model_recommendations()

    recs = result["recommendations"]
    assert len(recs) == 2  # 4b + 8b, no 14b
    recommended = [r for r in recs if r["recommended"]]
    assert recommended[0]["name"] == "qwen3:8b"


@pytest.mark.asyncio(loop_scope="session")
async def test_recommendations_8gb():
    """With 8GB RAM, should recommend qwen3:4b only."""
    with patch("src.api.routes.settings._get_total_ram_gb", return_value=8.0):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        with patch("src.api.routes.settings.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await get_model_recommendations()

    recs = result["recommendations"]
    assert len(recs) == 1  # only 4b
    assert recs[0]["name"] == "qwen3:4b"
    assert recs[0]["recommended"] is True


# ── Cloud provider presets ───────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_cloud_providers_list():
    """Should return all cloud provider presets."""
    result = await get_cloud_providers()
    providers = result["providers"]
    assert len(providers) == 3
    ids = [p["id"] for p in providers]
    assert "deepseek" in ids
    assert "openai" in ids
    assert "custom" in ids


def test_cloud_providers_have_required_fields():
    """Each provider preset should have id, name, base_url, default_model."""
    for p in CLOUD_PROVIDERS:
        assert "id" in p
        assert "name" in p
        assert "base_url" in p
        assert "default_model" in p


# ── Cloud API validation ────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_validate_cloud_empty_key():
    """Should reject empty API key."""
    req = ValidateCloudRequest(base_url="https://api.example.com", api_key="")
    result = await validate_cloud_api(req)
    assert result["valid"] is False
    assert "不能为空" in result["error"]


@pytest.mark.asyncio(loop_scope="session")
async def test_validate_cloud_empty_url():
    """Should reject empty base URL."""
    req = ValidateCloudRequest(base_url="", api_key="sk-test123")
    result = await validate_cloud_api(req)
    assert result["valid"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_validate_cloud_success():
    """Should return valid=True when API responds 200."""
    req = ValidateCloudRequest(base_url="https://api.example.com", api_key="sk-test")
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("src.api.routes.settings.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        result = await validate_cloud_api(req)

    assert result["valid"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_validate_cloud_unauthorized():
    """Should return valid=False with message when API returns 401."""
    req = ValidateCloudRequest(base_url="https://api.example.com", api_key="sk-bad")
    mock_response = MagicMock()
    mock_response.status_code = 401

    with patch("src.api.routes.settings.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        result = await validate_cloud_api(req)

    assert result["valid"] is False
    assert "401" in result["error"]


# ── Keyring secret store ────────────────────────────


def test_keyring_save_fallback_on_error():
    """When keyring raises, _try_keyring_save should return False."""
    mock_kr = MagicMock()
    mock_kr.set_password.side_effect = Exception("no backend")
    with patch.dict("sys.modules", {"keyring": mock_kr}):
        result = _try_keyring_save("sk-test")
    assert result is False


def test_keyring_load_fallback_on_error():
    """When keyring raises, _try_keyring_load should return None."""
    mock_kr = MagicMock()
    mock_kr.get_password.side_effect = Exception("no backend")
    with patch.dict("sys.modules", {"keyring": mock_kr}):
        result = _try_keyring_load()
    assert result is None


# ── Mode switching ──────────────────────────────────


def _mock_get_connection():
    """Create a mock async context manager for get_connection()."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.commit = AsyncMock()
    mock_conn.fetchone = AsyncMock(return_value=None)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


@pytest.mark.asyncio(loop_scope="session")
async def test_switch_to_ollama():
    """Should switch to Ollama mode and update runtime config."""
    req = SwitchModeRequest(mode="ollama", ollama_model="qwen3:4b")

    with patch("src.db.sqlite_db.get_connection", _mock_get_connection()):
        with patch("src.infra.config.switch_to_ollama"):
            result = await switch_llm_mode(req)

    assert result["success"] is True
    assert result["mode"] == "ollama"


@pytest.mark.asyncio(loop_scope="session")
async def test_switch_invalid_mode():
    """Should reject invalid mode."""
    req = SwitchModeRequest(mode="invalid")
    result = await switch_llm_mode(req)
    assert result["success"] is False
    assert "无效模式" in result["error"]


@pytest.mark.asyncio(loop_scope="session")
async def test_advanced_settings_valid():
    """Should accept valid max_tokens."""
    req = AdvancedSettingsRequest(max_tokens=4096)

    with patch("src.db.sqlite_db.get_connection", _mock_get_connection()):
        with patch("src.infra.config.update_max_tokens"):
            result = await save_advanced_settings(req)

    assert result["success"] is True
    assert result["max_tokens"] == 4096


@pytest.mark.asyncio(loop_scope="session")
async def test_advanced_settings_out_of_range():
    """Should reject out-of-range max_tokens."""
    req = AdvancedSettingsRequest(max_tokens=100)
    result = await save_advanced_settings(req)
    assert result["success"] is False
    assert "1024" in result["error"]


@pytest.mark.asyncio(loop_scope="session")
async def test_restore_defaults():
    """Should reset to defaults."""
    with patch("src.db.sqlite_db.get_connection", _mock_get_connection()):
        with patch("src.infra.config.switch_to_ollama") as mock_switch:
            with patch("src.infra.config.update_max_tokens") as mock_tokens:
                result = await restore_defaults()

    assert result["success"] is True
    mock_switch.assert_called_once_with("qwen3:8b")
    mock_tokens.assert_called_once_with(8192)
