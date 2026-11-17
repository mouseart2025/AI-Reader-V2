"""Cloud model catalog: provider presets + per-model request capabilities.

This is the single source of truth for two things that used to drift apart:

1. the provider/model list shown in Settings (previously ``CLOUD_PROVIDERS``
   inside ``api/routes/settings.py``)
2. the request-shape constraints applied by ``OpenAICompatibleClient``

Why a separate module
---------------------
Model line-ups go stale every 3-6 months (see ``scripts/audit_hardcoded_externals.py``).
Keeping the list next to the request constraints means a refresh is one file,
and a reviewer can see "this model forbids sampling params" right beside the
model id. Before this, the list said nothing about constraints, so a new model
could be added and still fail on the first call (issue: Kimi K3.1 Pro returned
503 because ``temperature`` was always sent).

Data provenance
---------------
Model ids below were cross-checked on 2026-11-17 against vendor docs plus
aggregator/price listings. They are the vendors' short aliases, not dated
snapshots. If a vendor renames one, users can still pick 「自定义」 and type the
id by hand, so a stale entry degrades to "one option does not work" instead of
bricking the settings page.

Deliberately NOT modelled here
------------------------------
- context window / max output tokens: nothing consumes them yet. Recording
  unread metadata would create dead fields; the context-budget rework is a
  separate task.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bump when the list changes; surfaced through GET /api/settings/cloud/providers
# so the UI (and bug reports) can tell which catalogue a user is on.
PRESET_VERSION = "2026-11"
MODEL_DATA_AS_OF = "2026-11-17"


@dataclass(frozen=True)
class ModelCapability:
    """How to shape a chat-completions request for one model family."""

    #: Whether explicit ``temperature`` / ``top_p`` / ``n`` are accepted.
    #: Kimi K3 pins them server-side; sending any other value is an error.
    sampling_params: bool = True

    #: Name of the output-cap field. Newer families moved to
    #: ``max_completion_tokens`` and deprecate ``max_tokens``.
    max_tokens_field: str = "max_tokens"

    #: ``json_object`` (legacy, broadly supported) | ``json_schema`` (strict) |
    #: ``none`` (do not send the field).
    response_format: str = "json_object"


DEFAULT_CAPABILITY = ModelCapability()

# Longest-prefix wins, so "kimi-k3" matches "kimi-k3.1-pro-highspeed" too.
_MODEL_RULES: tuple[tuple[str, ModelCapability], ...] = (
    # ── Moonshot / Kimi ────────────────────────────────────────────────
    # K3 family: the official parameter reference lists no temperature/top_p/n
    # (they are fixed server-side), and max_tokens is deprecated in favour of
    # max_completion_tokens. response_format=json_object is documented as
    # supported (it is only unsafe to combine with Partial Mode, which we do
    # not use); json_schema is left for a later round because it would require
    # the extraction schema to satisfy strict-mode rules we have not verified.
    (
        "kimi-k3",
        ModelCapability(
            sampling_params=False,
            max_tokens_field="max_completion_tokens",
            response_format="json_object",
        ),
    ),
    # K2.6 / K2.7-code share the fixed-sampling rule and the new cap field.
    (
        "kimi-k2",
        ModelCapability(
            sampling_params=False,
            max_tokens_field="max_completion_tokens",
            response_format="json_object",
        ),
    ),
    # ── Others ────────────────────────────────────────────────────────
    # No vendor-specific constraint confirmed yet: keep the pre-2026-11
    # behaviour (send sampling params, max_tokens, json_object). Being
    # conservative here matters — a wrong "improvement" breaks working setups.
    (
        "glm-5",
        ModelCapability(response_format="none"),  # GLM historically rejects it
    ),
    ("yi-", ModelCapability(response_format="none")),
)


def resolve_capability(model: str) -> ModelCapability:
    """Return the request constraints for ``model``.

    Unknown models fall back to :data:`DEFAULT_CAPABILITY`, which reproduces
    the previous behaviour exactly. This is what keeps custom/self-hosted
    endpoints working.
    """
    name = (model or "").strip().lower()
    best: tuple[str, ModelCapability] | None = None
    for prefix, capability in _MODEL_RULES:
        if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, capability)
    return best[1] if best else DEFAULT_CAPABILITY


# ── Provider presets ──────────────────────────────────────────────────
# Default = same tier as before (mini/flash/sonnet class), NOT the most
# expensive flagship: this app extracts every chapter of a novel, so cost
# scales with chapter count. Users who want the flagship can switch.
PROVIDERS: list[dict] = [
    # 国产模型
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        # Verified against GET /models on 2026-11-17 with a real key: the
        # account listed exactly ["deepseek-flash", "deepseek-v4-pro"].
        # "deepseek-v4.1-flash" (seen on aggregator sites) does not exist.
        "default_model": "deepseek-flash",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        # Kept only so an already-saved "deepseek-chat" keeps working if the
        # vendor still routes the alias. The endpoint no longer lists it, so
        # the UI flags it as legacy rather than pretending it is healthy.
        "legacy_models": ["deepseek-chat"],
        "api_format": "openai",
    },
    {
        "id": "minimax",
        "name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "default_model": "MiniMax-M3",
        "models": ["MiniMax-M3", "MiniMax-M3.1-Flash", "MiniMax-M2.7"],
        "api_format": "openai",
    },
    {
        "id": "qwen",
        "name": "阿里云百炼（Qwen）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen3.8-max",
        "models": ["qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus", "qwen-max"],
        "api_format": "openai",
    },
    {
        "id": "moonshot",
        "name": "Moonshot / Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k3.1-pro",
        # kimi-latest and the moonshot-v1 series were retired (2026-01-28 /
        # 2026-08-31); kimi-k2 series retires end of 2026.
        "models": [
            "kimi-k3.1-pro",
            "kimi-k3.1-pro-highspeed",
            "kimi-k3",
            "kimi-k2.7-code",
        ],
        "api_format": "openai",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-5.3",
        "models": ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5-turbo"],
        "api_format": "openai",
    },
    {
        "id": "siliconflow",
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3-0324",
        "models": ["deepseek-ai/DeepSeek-V3-0324", "Qwen/Qwen2.5-72B-Instruct", "Qwen/QwQ-32B"],
        "api_format": "openai",
    },
    {
        "id": "yi",
        "name": "零一万物（Yi）",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "default_model": "yi-large",
        "models": ["yi-large", "yi-large-turbo", "yi-medium"],
        "api_format": "openai",
    },
    # 海外模型
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-6-luna",
        "models": ["gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra"],
        "api_format": "openai",
    },
    {
        "id": "anthropic",
        "name": "Anthropic（Claude）",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-5-5",
        "models": [
            "claude-sonnet-5-5",
            "claude-opus-5-5",
            "claude-haiku-5-5",
            "claude-fable-5-2",
        ],
        "api_format": "anthropic",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-3.8-flash",
        "models": ["gemini-3.8-flash", "gemini-3.1-pro", "gemini-4-carbon"],
        "api_format": "openai",
    },
    # 本地 OpenAI 兼容服务（无需 API Key，需先在对应应用里加载模型）
    {
        "id": "lmstudio",
        "name": "LM Studio（本地）",
        "base_url": "http://localhost:1234/v1",
        "default_model": "",
        "models": [],
        "api_format": "openai",
    },
    {
        "id": "vllm",
        "name": "vLLM（本地）",
        "base_url": "http://localhost:8000/v1",
        "default_model": "",
        "models": [],
        "api_format": "openai",
    },
    {
        "id": "ollama_openai",
        "name": "Ollama OpenAI 端点（本地）",
        "base_url": "http://localhost:11434/v1",
        "default_model": "",
        "models": [],
        "api_format": "openai",
    },
    # 自定义
    {
        "id": "custom",
        "name": "自定义",
        "base_url": "",
        "default_model": "",
        "models": [],
        "api_format": "openai",
    },
]
