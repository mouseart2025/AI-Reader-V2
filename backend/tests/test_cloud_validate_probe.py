"""「验证」必须验证「能不能跑」，而不只是「key 有没有效」。

GET /models 返回 200 只证明 key 被接受；模型已下线、参数被拒这类问题
只有在真正发一次 completion 时才会暴露（2026-11 Kimi K3.1 Pro 案例）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.settings import ValidateCloudRequest, validate_cloud_api


def _client_mock(*, get_status: int = 200, post_status: int = 200, post_body=None):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    get_resp = MagicMock(status_code=get_status)
    post_resp = MagicMock(status_code=post_status)
    post_resp.json.return_value = post_body or {}
    post_resp.text = ""

    client.get = AsyncMock(return_value=get_resp)
    client.post = AsyncMock(return_value=post_resp)
    return client


@pytest.mark.asyncio(loop_scope="session")
async def test_probe_uses_capability_rules_for_kimi_k3():
    req = ValidateCloudRequest(
        base_url="https://api.moonshot.cn/v1", api_key="sk-x", model="kimi-k3.1-pro"
    )
    client = _client_mock(get_status=200, post_status=200)

    with patch("src.api.routes.settings.httpx.AsyncClient", return_value=client):
        result = await validate_cloud_api(req)

    assert result["valid"] is True
    payload = client.post.await_args.kwargs["json"]
    assert "temperature" not in payload, "K3 rejects explicit sampling params"
    assert payload["max_completion_tokens"] == 64
    assert "max_tokens" not in payload


@pytest.mark.asyncio(loop_scope="session")
async def test_probe_surfaces_parameter_rejection():
    """The exact failure that used to hide behind a generic 503."""
    req = ValidateCloudRequest(
        base_url="https://api.moonshot.cn/v1", api_key="sk-x", model="kimi-k3.1-pro"
    )
    client = _client_mock(
        get_status=200,
        post_status=400,
        post_body={"error": {"message": "temperature must be 1.0"}},
    )

    with patch("src.api.routes.settings.httpx.AsyncClient", return_value=client):
        result = await validate_cloud_api(req)

    assert result["valid"] is False
    assert "temperature must be 1.0" in result["error"]
    assert "400" in result["error"]


@pytest.mark.asyncio(loop_scope="session")
async def test_probe_reports_payment_required():
    """402 means the key works but the account cannot pay — say so."""
    req = ValidateCloudRequest(
        base_url="https://api.deepseek.com", api_key="sk-x", model="deepseek-flash"
    )
    client = _client_mock(
        get_status=200,
        post_status=402,
        post_body={"error": {"message": "Insufficient Balance"}},
    )

    with patch("src.api.routes.settings.httpx.AsyncClient", return_value=client):
        result = await validate_cloud_api(req)

    assert result["valid"] is False
    assert "Insufficient Balance" in result["error"]


@pytest.mark.asyncio(loop_scope="session")
async def test_without_model_keeps_lightweight_behaviour():
    """No model selected -> no completion, so no accidental spend."""
    req = ValidateCloudRequest(base_url="https://api.example.com", api_key="sk-x")
    client = _client_mock(get_status=200)

    with patch("src.api.routes.settings.httpx.AsyncClient", return_value=client):
        result = await validate_cloud_api(req)

    assert result["valid"] is True
    client.post.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_skips_openai_style_probe():
    """Anthropic uses a different messages API; must not be probed as OpenAI."""
    req = ValidateCloudRequest(
        base_url="https://api.anthropic.com",
        api_key="sk-x",
        provider="anthropic",
        model="claude-sonnet-5-5",
    )
    client = _client_mock(get_status=200)

    with patch("src.api.routes.settings.httpx.AsyncClient", return_value=client):
        result = await validate_cloud_api(req)

    assert result["valid"] is True
    client.post.assert_not_awaited()
