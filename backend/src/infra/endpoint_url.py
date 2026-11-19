"""Endpoint classification shared by config and the settings routes.

Why this module exists
----------------------
The previous checks used ``"anthropic.com" in url``. That is a substring test,
not a host test: ``https://anthropic.com.evil.test/v1`` and
``https://evil.test/?next=anthropic.com`` both match, so a request meant for
Anthropic could be shaped as an Anthropic call against a different host.
CodeQL flags the pattern as ``py/incomplete-url-substring-sanitization``.
"""

from __future__ import annotations

from urllib.parse import urlparse


def is_anthropic_endpoint(provider_id: str, url: str) -> bool:
    """Return True when ``url`` really points at Anthropic.

    A provider id of ``anthropic`` is authoritative (the user picked the
    preset); otherwise only the parsed hostname counts, exact or as a
    subdomain — never a substring of the path or query.
    """
    if (provider_id or "").strip().lower() == "anthropic":
        return True
    host = (urlparse(url or "").hostname or "").lower()
    return host == "anthropic.com" or host.endswith(".anthropic.com")
