"""Cost estimation and budget tracking for cloud LLM analysis."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from src.infra.config import LLM_MODEL, LLM_PROVIDER

_DEFAULT_MONTHLY_BUDGET_CNY = 50.0

# ── Pricing per 1M tokens (USD) ─────────────────────

_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1m, output_per_1m)
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}

_DEFAULT_PRICING = (0.50, 1.50)  # conservative fallback

# ── Token estimation constants ───────────────────────

# Chinese text: ~1.5 tokens per character (varies by tokenizer)
_TOKENS_PER_CHAR = 1.5

# Fixed prompt overhead per LLM call (system prompt + schema + few-shot examples)
# system_prompt: ~10KB + examples: ~21KB ≈ 31KB → ~47K tokens overhead
_PROMPT_OVERHEAD_TOKENS = 47_000

# Average output tokens per chapter (typical ChapterFact JSON)
_OUTPUT_TOKENS_PER_CHAPTER = 4_000

# Entity pre-scan LLM classification call (one-time)
_PRESCAN_INPUT_TOKENS = 25_000
_PRESCAN_OUTPUT_TOKENS = 5_000

# Context summary overhead per chapter (grows with analysis progress)
_CONTEXT_SUMMARY_CHARS = 2_000  # average context summary per chapter


@dataclass
class CostEstimate:
    """Cost estimation result."""

    provider: str
    model: str
    chapter_count: int
    total_words: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    estimated_cost_usd: float
    estimated_cost_cny: float
    includes_prescan: bool
    input_price_per_1m: float
    output_price_per_1m: float


def get_pricing(model: str) -> tuple[float, float]:
    """Get (input_per_1m, output_per_1m) pricing for a model."""
    # Try exact match first
    if model in _PRICING:
        return _PRICING[model]
    # Try prefix match (e.g., "deepseek-chat-v2" → "deepseek-chat")
    for key, pricing in _PRICING.items():
        if model.startswith(key.split("-")[0]):
            return pricing
    return _DEFAULT_PRICING


def estimate_analysis_cost(
    chapter_count: int,
    total_words: int,
    include_prescan: bool = True,
    provider: str | None = None,
    model: str | None = None,
) -> CostEstimate:
    """Estimate the cost of analyzing chapters with cloud LLM.

    Args:
        chapter_count: Number of chapters to analyze.
        total_words: Total word count across all chapters.
        include_prescan: Whether entity pre-scan LLM call is included.
        provider: LLM provider override (default: current config).
        model: Model name override (default: current config).
    """
    effective_provider = provider or LLM_PROVIDER
    effective_model = model or LLM_MODEL

    input_price, output_price = get_pricing(effective_model)

    # Average chars per chapter
    avg_chars_per_chapter = total_words / max(chapter_count, 1)

    # Per-chapter input tokens: chapter text + context summary + prompt overhead
    # Prompt overhead is amortized per call (each chapter = 1 call for short, 2-3 for long)
    per_chapter_text_tokens = avg_chars_per_chapter * _TOKENS_PER_CHAR
    per_chapter_context_tokens = _CONTEXT_SUMMARY_CHARS * _TOKENS_PER_CHAR
    per_chapter_input = (
        _PROMPT_OVERHEAD_TOKENS + per_chapter_text_tokens + per_chapter_context_tokens
    )

    # Total tokens
    total_input = int(per_chapter_input * chapter_count)
    total_output = _OUTPUT_TOKENS_PER_CHAPTER * chapter_count

    if include_prescan:
        total_input += _PRESCAN_INPUT_TOKENS
        total_output += _PRESCAN_OUTPUT_TOKENS

    total_tokens = total_input + total_output

    # Cost calculation
    cost_usd = (total_input / 1_000_000) * input_price + (
        total_output / 1_000_000
    ) * output_price
    cost_cny = cost_usd * 7.2  # approximate USD→CNY

    return CostEstimate(
        provider=effective_provider,
        model=effective_model,
        chapter_count=chapter_count,
        total_words=total_words,
        estimated_input_tokens=total_input,
        estimated_output_tokens=total_output,
        estimated_total_tokens=total_tokens,
        estimated_cost_usd=round(cost_usd, 4),
        estimated_cost_cny=round(cost_cny, 2),
        includes_prescan=include_prescan,
        input_price_per_1m=input_price,
        output_price_per_1m=output_price,
    )


# ── Monthly budget & usage tracking ──────────────────


def _monthly_key() -> str:
    """Return user_state key for current month, e.g. 'cost_2026_02'."""
    return f"cost_{datetime.now().strftime('%Y_%m')}"


async def get_monthly_budget() -> float:
    """Get monthly budget in CNY. Default ¥50."""
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        row = await conn.execute(
            "SELECT value FROM app_settings WHERE key='budget_monthly_cny'",
        )
        result = await row.fetchone()
        if result:
            try:
                return float(result[0])
            except (ValueError, TypeError):
                pass
    finally:
        await conn.close()
    return _DEFAULT_MONTHLY_BUDGET_CNY


async def set_monthly_budget(amount_cny: float) -> None:
    """Set monthly budget in CNY."""
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('budget_monthly_cny', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (str(amount_cny),),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_monthly_usage() -> dict:
    """Get current month's cumulative usage.

    Returns dict with keys: usd, cny, input_tokens, output_tokens.
    """
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        row = await conn.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (_monthly_key(),),
        )
        result = await row.fetchone()
        if result:
            try:
                return json.loads(result[0])
            except (json.JSONDecodeError, TypeError):
                pass
    finally:
        await conn.close()
    return {"usd": 0.0, "cny": 0.0, "input_tokens": 0, "output_tokens": 0}


async def add_monthly_usage(
    cost_usd: float, cost_cny: float, input_tokens: int, output_tokens: int,
) -> dict:
    """Add to current month's cumulative usage. Returns updated totals."""
    from src.db.sqlite_db import get_connection

    key = _monthly_key()
    current = await get_monthly_usage()
    current["usd"] = round(current["usd"] + cost_usd, 4)
    current["cny"] = round(current["cny"] + cost_cny, 2)
    current["input_tokens"] = current["input_tokens"] + input_tokens
    current["output_tokens"] = current["output_tokens"] + output_tokens

    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, json.dumps(current)),
        )
        await conn.commit()
    finally:
        await conn.close()

    return current
