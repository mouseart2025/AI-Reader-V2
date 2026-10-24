"""Tests for cost estimation service."""


from src.services.cost_service import CostEstimate, estimate_analysis_cost, get_pricing


def test_get_pricing_known_model():
    """Should return exact pricing for known models."""
    inp, out = get_pricing("deepseek-chat")
    assert inp == 0.27
    assert out == 1.10


def test_get_pricing_unknown_model():
    """Should return default pricing for unknown models."""
    inp, out = get_pricing("custom-unknown-model")
    assert inp == 0.50
    assert out == 1.50


def test_estimate_basic():
    """Should produce a valid cost estimate."""
    est = estimate_analysis_cost(
        chapter_count=10,
        total_words=50000,
        include_prescan=True,
        provider="openai",
        model="deepseek-chat",
    )
    assert isinstance(est, CostEstimate)
    assert est.chapter_count == 10
    assert est.total_words == 50000
    assert est.estimated_input_tokens > 0
    assert est.estimated_output_tokens > 0
    assert est.estimated_total_tokens == est.estimated_input_tokens + est.estimated_output_tokens
    assert est.estimated_cost_usd > 0
    assert est.estimated_cost_cny > 0
    assert est.includes_prescan is True


def test_estimate_no_prescan():
    """Without prescan, tokens should be lower."""
    with_prescan = estimate_analysis_cost(
        chapter_count=10, total_words=50000, include_prescan=True, model="deepseek-chat"
    )
    without_prescan = estimate_analysis_cost(
        chapter_count=10, total_words=50000, include_prescan=False, model="deepseek-chat"
    )
    assert without_prescan.estimated_input_tokens < with_prescan.estimated_input_tokens
    assert without_prescan.estimated_cost_usd < with_prescan.estimated_cost_usd


def test_estimate_scales_with_chapters():
    """More chapters should cost more."""
    small = estimate_analysis_cost(
        chapter_count=5, total_words=25000, include_prescan=False, model="deepseek-chat"
    )
    large = estimate_analysis_cost(
        chapter_count=50, total_words=250000, include_prescan=False, model="deepseek-chat"
    )
    assert large.estimated_cost_usd > small.estimated_cost_usd * 5


def test_estimate_single_chapter():
    """Should handle single chapter."""
    est = estimate_analysis_cost(
        chapter_count=1, total_words=3000, include_prescan=False, model="gpt-4o-mini"
    )
    assert est.estimated_cost_usd > 0
    assert est.input_price_per_1m == 0.15
    assert est.output_price_per_1m == 0.60


# ── 前缀缓存计价（2026-10 实测命中率 98%） ──


def test_cost_with_cache_is_lower_than_without():
    """命中缓存的部分按 cache 价计费，不能按输入价全额收。"""
    from src.services.cost_service import compute_cost_from_usage

    class _Usage:
        prompt_tokens = 10_000
        completion_tokens = 1_000

    cached = _Usage()
    cached.cache_hit_tokens = 9_000
    uncached = _Usage()
    uncached.cache_hit_tokens = 0

    usd_cached, _ = compute_cost_from_usage(cached, "deepseek-chat")
    usd_plain, _ = compute_cost_from_usage(uncached, "deepseek-chat")
    assert usd_cached < usd_plain
    # 9k 命中 + 1k 未命中 + 1k 输出：0.00063 + 0.00027 + 0.0011
    assert abs(usd_cached - 0.002) < 1e-6


def test_cost_ignores_cache_for_models_without_cache_pricing():
    """未知模型不给折扣，避免制造假节省。"""
    from src.services.cost_service import compute_cost_from_usage, get_cache_pricing

    class _Usage:
        prompt_tokens = 10_000
        completion_tokens = 1_000
        cache_hit_tokens = 9_000

    assert get_cache_pricing("some-unknown-model") == get_pricing("some-unknown-model")[0]
    usd, _ = compute_cost_from_usage(_Usage(), "some-unknown-model")
    inp, out = get_pricing("some-unknown-model")
    assert abs(usd - (0.01 * inp + 0.001 * out)) < 1e-6


def test_cache_hit_tokens_clamped_to_prompt_tokens():
    """提供商偶尔报出 hit > prompt，计价时不能变成负数 miss。"""
    from src.services.cost_service import compute_cost_from_usage

    class _Usage:
        prompt_tokens = 1_000
        completion_tokens = 0
        cache_hit_tokens = 5_000  # 明显异常

    usd, _ = compute_cost_from_usage(_Usage(), "deepseek-chat")
    assert usd >= 0
