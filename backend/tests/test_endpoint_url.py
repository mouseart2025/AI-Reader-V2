"""Host matching must not be a substring test.

The old check was ``"anthropic.com" in url``, which accepts
``https://anthropic.com.evil.test`` and ``https://evil.test/?next=anthropic.com``.
CodeQL reports that pattern as py/incomplete-url-substring-sanitization.
"""

from __future__ import annotations

import pytest

from src.infra.endpoint_url import is_anthropic_endpoint


@pytest.mark.parametrize(
    "url",
    [
        "https://api.anthropic.com",
        "https://api.anthropic.com/v1",
        "https://anthropic.com",
        "https://eu.api.anthropic.com/v1",
    ],
)
def test_real_anthropic_hosts_match(url):
    assert is_anthropic_endpoint("", url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://anthropic.com.evil.test/v1",      # dotted suffix trick
        "https://evil.test/?next=anthropic.com",   # substring in query
        "https://evil.test/anthropic.com",         # substring in path
        "https://notanthropic.com/v1",
        "",
    ],
)
def test_lookalike_urls_are_rejected(url):
    assert is_anthropic_endpoint("", url) is False


def test_provider_id_is_authoritative():
    """A user-chosen preset may point at a proxy we cannot classify by host."""
    assert is_anthropic_endpoint("anthropic", "https://my-claude-proxy.example.com") is True
    assert is_anthropic_endpoint("openai", "https://api.openai.com/v1") is False
