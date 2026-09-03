"""V-01 回归测试：sidecar 令牌鉴权中间件（src.api.main.SidecarAuthMiddleware）。

未配置令牌（web 直跑/开发模式）全放行；配置后：
- /api/* 要求 Authorization: Bearer <token>，否则 401
- /ws/* 要求 ?token=<token>，否则握手即关闭（4401）
- /api/health 与 OPTIONS 预检豁免
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.main import app
from src.infra import config

TOKEN = "0123456789abcdef" * 4  # 64 hex，模拟宿主生成的令牌


@pytest.fixture
def token_on(monkeypatch):
    monkeypatch.setattr(config, "SIDECAR_TOKEN", TOKEN)
    return TestClient(app)


def test_no_token_configured_allows_all(monkeypatch):
    """未配置令牌（web/开发模式）：不做任何鉴权。"""
    monkeypatch.setattr(config, "SIDECAR_TOKEN", "")
    client = TestClient(app)
    # 404 说明穿过了中间件到达路由层
    assert client.get("/api/definitely-not-exists").status_code == 404


def test_http_missing_or_wrong_token_rejected(token_on):
    assert token_on.get("/api/definitely-not-exists").status_code == 401
    assert token_on.get(
        "/api/definitely-not-exists",
        headers={"Authorization": "Bearer wrong-token"},
    ).status_code == 401


def test_http_valid_token_passes(token_on):
    # 404 = 通过中间件但路由不存在；证明鉴权放行
    assert token_on.get(
        "/api/definitely-not-exists",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ).status_code == 404


def test_health_exempt(token_on):
    assert token_on.get("/api/health").status_code == 200


def test_health_exposes_backend_version(token_on):
    """桌面端宿主启动 sidecar 后比对前后端版本(issue #71 版本握手)。"""
    from src.infra.version import BACKEND_VERSION

    body = token_on.get("/api/health").json()
    assert body["version"] == BACKEND_VERSION


def test_options_preflight_exempt(token_on):
    r = token_on.options(
        "/api/novels",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code != 401


def test_ws_rejected_without_token(token_on):
    with pytest.raises(WebSocketDisconnect):
        with token_on.websocket_connect("/ws/chat/test-session"):
            pass


def test_ws_rejected_with_wrong_token(token_on):
    with pytest.raises(WebSocketDisconnect):
        with token_on.websocket_connect("/ws/chat/test-session?token=wrong"):
            pass
