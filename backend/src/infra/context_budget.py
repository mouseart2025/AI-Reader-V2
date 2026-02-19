"""Token budget auto-scaling based on model context window size.

Detects the model's context window at startup and after model switches,
then computes all LLM budget parameters via linear interpolation:
  8K context  -> conservative (current "local" values)
  128K context -> generous (current "cloud" values)
  In between  -> linear interpolation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Interpolation anchors
_CTX_MIN = 8192      # 8K  -> min values
_CTX_MAX = 131072    # 128K -> max values


def _scale(ctx: int, min_val: int, max_val: int) -> int:
    """8K -> min_val, 128K -> max_val, linear interpolation, clamped."""
    t = max(0.0, min(1.0, (ctx - _CTX_MIN) / (_CTX_MAX - _CTX_MIN)))
    return int(min_val + t * (max_val - min_val))


@dataclass(frozen=True)
class TokenBudget:
    """All LLM budget parameters derived from context_window size."""

    context_window: int

    # chapter_fact_extractor.py
    max_chapter_len: int
    retry_len: int
    segment_enabled: bool
    extraction_num_ctx: int

    # context_summary_builder.py
    context_max_chars: int
    char_limit: int
    rel_limit: int
    loc_limit: int
    item_limit: int
    max_hierarchy_chains: int
    world_summary_chars: int

    # scene_llm_extractor.py
    scene_max_chapter_len: int

    # world_structure_agent.py
    ws_max_tokens: int
    ws_timeout: int

    # location_hierarchy_reviewer.py
    hierarchy_timeout: int


def compute_budget(context_window: int) -> TokenBudget:
    """Pure function: compute all budget parameters from context_window."""
    ctx = context_window

    return TokenBudget(
        context_window=ctx,
        # chapter_fact_extractor
        max_chapter_len=_scale(ctx, 8000, 50000),
        retry_len=_scale(ctx, 6000, 30000),
        segment_enabled=ctx >= 32768,
        extraction_num_ctx=min(ctx, _scale(ctx, 16384, 32768)),
        # context_summary_builder
        context_max_chars=_scale(ctx, 6000, 18000),
        char_limit=_scale(ctx, 30, 60),
        rel_limit=_scale(ctx, 20, 40),
        loc_limit=_scale(ctx, 30, 80),
        item_limit=_scale(ctx, 15, 30),
        max_hierarchy_chains=_scale(ctx, 10, 15),
        world_summary_chars=_scale(ctx, 800, 1500),
        # scene_llm_extractor
        scene_max_chapter_len=_scale(ctx, 8000, 50000),
        # world_structure_agent
        ws_max_tokens=_scale(ctx, 4096, 8192),
        ws_timeout=_scale(ctx, 120, 180),
        # location_hierarchy_reviewer
        hierarchy_timeout=_scale(ctx, 90, 120),
    )


def get_budget() -> TokenBudget:
    """Read config.CONTEXT_WINDOW_SIZE and return current budget."""
    from src.infra import config

    return compute_budget(config.CONTEXT_WINDOW_SIZE)


async def detect_context_window_ollama(base_url: str, model: str) -> int | None:
    """POST /api/show -> parse model_info.*.context_length.

    Returns context window size in tokens, or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{base_url}/api/show",
                json={"name": model},
            )
            if resp.status_code != 200:
                return None

            data = resp.json()

            # Primary: model_info.*.context_length
            model_info = data.get("model_info", {})
            for key, value in model_info.items():
                if key.endswith(".context_length") and isinstance(value, (int, float)):
                    return int(value)

            # Fallback: parse "parameters" string for num_ctx line
            params_str = data.get("parameters", "")
            if params_str:
                for line in params_str.splitlines():
                    line = line.strip()
                    if line.startswith("num_ctx"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[-1])

    except Exception as e:
        logger.debug("Failed to detect Ollama context window: %s", e)

    return None


async def detect_and_update_context_window() -> int:
    """Detect context window and update config.CONTEXT_WINDOW_SIZE.

    Detection order:
    1. Cloud mode -> 131072 (128K default)
    2. Ollama -> POST /api/show
    3. Failure -> 8192 (conservative fallback)
    """
    from src.infra import config

    ctx: int | None = None

    if config.LLM_PROVIDER == "openai":
        ctx = 131072
        logger.info(
            "Context window: %d (cloud mode, model=%s)",
            ctx, config.get_model_name(),
        )
    else:
        ctx = await detect_context_window_ollama(
            config.OLLAMA_BASE_URL, config.OLLAMA_MODEL,
        )
        if ctx is not None:
            logger.info(
                "Context window detected: %d for model %s",
                ctx, config.OLLAMA_MODEL,
            )
        else:
            ctx = 8192
            logger.warning(
                "Context window detection failed for %s, using fallback %d",
                config.OLLAMA_MODEL, ctx,
            )

    config.update_context_window(ctx)
    return ctx
