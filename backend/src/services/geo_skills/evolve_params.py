"""GeoEvolve 阶段 2 —— 权重/参数注入钩子（默认关，生产行为不变）。

机制：
  - 进化评估子进程设置环境变量 EVOLVE_PARAMS_JSON（指向参数 JSON）→
    evolve_param() 返回 JSON 中的注入值；
  - 生产路径不设置该变量 → evolve_param() 原样返回代码里的 default
    （即原硬编码字面量），行为与注入机制引入前逐字节一致。

使用方式（各 skill 内，把原字面量包一层）：
    w = evolve_param("vote_builder.peer_discount", 0.33)

single source of truth 是 genome（backend/scripts/evolve/genome.yaml level 2 +
weights_state.json）；本模块只是读取通道，不做任何写操作。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_ENV_KEY = "EVOLVE_PARAMS_JSON"

# (path, mtime, data) — 文件内容缓存，mtime 变化即重读（EVAL 每代换文件）
_cache: tuple[str | None, float, dict] = (None, 0.0, {})


def _load_overrides() -> dict:
    path = os.environ.get(_ENV_KEY)
    if not path:
        return {}
    global _cache
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache[0] != path or _cache[1] != mtime:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        _cache = (path, mtime, data if isinstance(data, dict) else {})
    return _cache[2]


def evolve_param(key: str, default):
    """取注入参数：开关关返回 default（原代码值），开关开返回注入值（类型对齐）。

    类型对齐规则：按 default 的类型做强转（bool/int/float），
    int 类参数由算子负责在写入 JSON 前取整（见 genome.yaml level 2 声明）。
    """
    val = _load_overrides().get(key)
    if val is None:
        return default
    if isinstance(default, bool):
        return bool(val)
    if isinstance(default, int):
        # int 默认值：覆盖值照原样返回（权重域接受 float；纯 int 参数由
        # 算子按 genome 声明在写入 JSON 前取整），default 本身原样返回
        return val if isinstance(val, (int, float)) else int(val)
    if isinstance(default, float):
        return float(val)
    return val


def reset_cache() -> None:
    """测试用：清空缓存。"""
    global _cache
    _cache = (None, 0.0, {})
