"""Tests for cost estimation service."""

import pytest

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
