"""配置加载 — 支持 ${ENV_VAR} 环境变量引用"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")
_config: dict[str, Any] | None = None


def _resolve_env_vars(value: Any) -> Any:
    """递归解析配置值中的 ${VAR} 环境变量引用"""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), ""),
            value,
        )
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def _find_config_file() -> Path:
    """查找 config.yaml，优先当前目录，然后 marketing/ 目录"""
    candidates = [
        Path("config.yaml"),
        Path(__file__).parent.parent / "config.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "找不到 config.yaml。请复制 config.example.yaml 为 config.yaml 并填入配置。"
    )


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """加载并缓存配置"""
    global _config
    if _config is not None and path is None:
        return _config

    config_path = Path(path) if path else _find_config_file()
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    _config = _resolve_env_vars(raw)
    return _config


def get_config() -> dict[str, Any]:
    """获取已加载的配置，未加载则自动加载"""
    if _config is None:
        return load_config()
    return _config


def get_llm_config(role: str = "analysis") -> dict[str, Any]:
    """获取 LLM 配置 (role: 'copywriting' | 'analysis')"""
    cfg = get_config()
    return cfg["llm"][role]


def get_platform_config(platform: str) -> dict[str, Any]:
    """获取平台配置"""
    cfg = get_config()
    return cfg["platforms"][platform]


def get_output_dir() -> Path:
    """获取输出根目录"""
    cfg = get_config()
    return Path(cfg.get("output", {}).get("dir", "./output"))


def get_db_path() -> Path:
    """获取数据库路径"""
    cfg = get_config()
    return Path(cfg.get("output", {}).get("db_path", "./marketing.db"))
