"""Async client for Anthropic Claude API.

Anthropic uses a different protocol from OpenAI:
  - Auth:     x-api-key + anthropic-version headers (not Bearer)
  - Endpoint: POST /v1/messages (not /chat/completions)
  - System:   top-level "system" field (not a message role)
  - Response: content[0].text (not choices[0].message.content)
  - Tokens:   usage.input_tokens / output_tokens (not prompt/completion)
  - Stream:   typed SSE events (content_block_delta, message_stop)

Interface is intentionally identical to OpenAICompatibleClient so the
factory in llm_client.py can swap it in transparently.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx

from src.infra.llm_client import LLMError, LLMTimeoutError, LlmUsage, _extract_json
from src.infra.openai_client import _repair_truncated_json

logger = logging.getLogger(__name__)

# Anthropic API version header (required by the API)
_ANTHROPIC_VERSION = "2023-06-01"

# Reuse the cloud semaphore from openai_client (3 concurrent cloud calls)
from src.infra.openai_client import _get_cloud_semaphore  # noqa: E402


class AnthropicClient:
    """Async client for Anthropic Claude API."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _make_client(self, timeout: float | httpx.Timeout) -> httpx.AsyncClient:
        """Create httpx client bypassing system proxy (same rationale as OpenAI client)."""
        transport = httpx.AsyncHTTPTransport()
        return httpx.AsyncClient(transport=transport, timeout=timeout)

    async def generate(
        self,
        system: str,
        prompt: str,
        format: dict | None = None,  # noqa: A002
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: int = 120,
        num_ctx: int | None = None,  # ignored — Anthropic manages context internally
    ) -> tuple[str | dict, LlmUsage]:
        """Call Anthropic Messages API.

        Returns (content, usage) tuple. Content is dict when format is given, str otherwise.
        Note: Anthropic does not support a native JSON mode.  When format is given we
        rely on the model to produce valid JSON (Claude models do this reliably) and
        fall back to _extract_json for robustness.
        """
        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }

        sem = _get_cloud_semaphore()
        async with sem:
            logger.debug("Anthropic semaphore acquired for generate()")
            try:
                async with self._make_client(
                    httpx.Timeout(timeout, connect=10.0)
                ) as client:
                    resp = await client.post(
                        f"{self.base_url}/v1/messages",
                        json=payload,
                        headers=self._headers(),
                    )
                    resp.raise_for_status()
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"Anthropic API request timed out after {timeout}s"
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"Anthropic API HTTP error {exc.response.status_code}: "
                    f"{exc.response.text[:300]}"
                ) from exc

        data = resp.json()

        # Extract text content
        content_blocks = data.get("content", [])
        if not content_blocks:
            raise LLMError("Empty content in Anthropic API response")
        content: str = content_blocks[0].get("text", "")
        if not content:
            raise LLMError("Empty text in Anthropic API response")

        stop_reason = data.get("stop_reason", "")

        # Parse token usage (Anthropic uses different field names)
        usage_data = data.get("usage", {})
        usage = LlmUsage(
            prompt_tokens=usage_data.get("input_tokens", 0),
            completion_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
        )

        if format is not None:
            if stop_reason == "max_tokens":
                logger.warning(
                    "Anthropic output truncated (stop_reason=max_tokens), "
                    "attempting to repair JSON (%d chars)", len(content),
                )
                content = _repair_truncated_json(content)

            try:
                return json.loads(content), usage
            except json.JSONDecodeError:
                return _extract_json(content), usage

        return content, usage

    async def generate_stream(
        self,
        system: str,
        prompt: str,
        timeout: int = 180,
    ) -> AsyncIterator[str]:
        """Stream tokens from Anthropic Messages API.

        Anthropic SSE events we care about:
          event: content_block_delta
          data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"..."}}

          event: message_stop
          data: {"type":"message_stop"}

        Does NOT acquire the semaphore (same rationale as OpenAI streaming).
        """
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }

        logger.debug("Anthropic generate_stream() sending request (no semaphore)")
        async with self._make_client(
            httpx.Timeout(timeout, connect=10.0)
        ) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/messages",
                json=payload,
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    # SSE lines: "event: ..." or "data: ..."
                    if line.startswith("data: "):
                        raw = line[6:].strip()
                        if not raw:
                            continue
                        try:
                            chunk = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        chunk_type = chunk.get("type", "")
                        if chunk_type == "content_block_delta":
                            delta = chunk.get("delta", {})
                            if delta.get("type") == "text_delta":
                                token = delta.get("text", "")
                                if token:
                                    yield token
                        elif chunk_type == "message_stop":
                            break
                        elif chunk_type == "message_delta":
                            # Check if finished
                            if chunk.get("delta", {}).get("stop_reason"):
                                break
