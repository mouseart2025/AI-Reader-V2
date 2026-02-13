"""Async client for OpenAI-compatible APIs (Kimi, DeepSeek, Qwen Cloud, etc.)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx

from src.infra.llm_client import LLMError, LLMTimeoutError, _extract_json

logger = logging.getLogger(__name__)


def _repair_truncated_json(text: str) -> str:
    """Attempt to repair truncated JSON by closing open brackets/braces.

    When the API hits max_tokens, the JSON output is cut mid-stream.
    Strategy: find the last position that ends a complete JSON value at the
    array/object element level (i.e. after `}`, `]`, a quoted string value,
    number, true/false/null that follows a `:`), trim there, close brackets.
    """
    text = text.rstrip()

    # Scan the string to find "safe cut points" — positions after which we
    # could trim and still have all preceding elements be complete.
    # A safe cut point is right after a complete value that is an element in
    # an array or the value side of a key-value pair in an object.
    safe_cuts: list[int] = []
    stack: list[str] = []  # '{' or '['
    in_string = False
    escape = False
    after_colon = False  # True when we've seen ':' and awaiting value

    i = 0
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape = True
            i += 1
            continue
        if ch == '"':
            if in_string:
                in_string = False
                if after_colon or (stack and stack[-1] == "["):
                    safe_cuts.append(i + 1)
                    after_colon = False
            else:
                in_string = True
            i += 1
            continue
        if in_string:
            i += 1
            continue

        # Skip whitespace outside strings
        if ch in (" ", "\n", "\r", "\t"):
            i += 1
            continue

        if ch in ("{", "["):
            stack.append(ch)
            after_colon = False
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
            safe_cuts.append(i + 1)
            after_colon = False
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()
            safe_cuts.append(i + 1)
            after_colon = False
        elif ch == ":":
            after_colon = True
        elif ch == ",":
            after_colon = False
        elif after_colon or (stack and stack[-1] == "["):
            # Numbers, true, false, null
            j = i
            while j < len(text) and text[j] not in (",", "}", "]", " ", "\n", "\r", "\t"):
                j += 1
            safe_cuts.append(j)
            after_colon = False
            i = j
            continue
        i += 1

    # Pick the last safe cut point
    if safe_cuts:
        text = text[:safe_cuts[-1]]

    # Remove trailing comma
    text = text.rstrip().rstrip(",")

    # Remove trailing incomplete primitive values (e.g. "fal" from "false")
    # by checking if text ends with a non-quoted non-bracket token
    stripped = text.rstrip()
    if stripped and stripped[-1] not in ('"', '}', ']', '{', '['):
        # Might be a truncated number/literal — check if it's valid
        # Find start of the trailing token
        j = len(stripped) - 1
        while j > 0 and stripped[j - 1] not in (':', ',', '[', '{', ' ', '\n'):
            j -= 1
        trailing = stripped[j:]
        # Valid primitives: true, false, null, or a complete number
        valid_primitives = {"true", "false", "null"}
        try:
            float(trailing)
            is_valid = True
        except ValueError:
            is_valid = trailing in valid_primitives
        if not is_valid:
            # Remove the incomplete primitive and the preceding ':'
            text = stripped[:j].rstrip().rstrip(":").rstrip().rstrip(",")

    # Remove trailing orphaned key (key without value)
    # Patterns: ..., "key" or ..., "key": or ...{"key" or ...{"key":
    import re
    # Remove ,"key": or ,"key" at end
    text = re.sub(r',\s*"[^"]*"\s*:?\s*$', '', text)
    # Remove orphaned key right after opening brace: {"key": → {
    text = re.sub(r'(\{)\s*"[^"]*"\s*:?\s*$', r'\1', text)
    text = text.rstrip().rstrip(",")

    # Re-scan to find unclosed brackets
    stack = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ("{", "["):
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()

    closers = ""
    for opener in reversed(stack):
        closers += "]" if opener == "[" else "}"

    repaired = text + closers
    logger.info("Repaired truncated JSON: trimmed to %d chars, added %d closers",
                len(text), len(closers))
    return repaired


# Cloud APIs can handle parallel requests — allow up to 3 concurrent calls.
_cloud_semaphore: asyncio.Semaphore | None = None


def _get_cloud_semaphore() -> asyncio.Semaphore:
    """Lazily create semaphore in the running event loop."""
    global _cloud_semaphore
    if _cloud_semaphore is None:
        _cloud_semaphore = asyncio.Semaphore(3)
    return _cloud_semaphore


class OpenAICompatibleClient:
    """Async client for OpenAI-compatible APIs."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _make_client(self, timeout: float | httpx.Timeout) -> httpx.AsyncClient:
        """Create httpx client that bypasses system proxy.

        httpx reads http_proxy/https_proxy env vars by default. If the proxy
        is down (common with Clash/V2Ray toggling), all requests fail. Using a
        direct AsyncHTTPTransport bypasses env proxy entirely.
        """
        transport = httpx.AsyncHTTPTransport()
        return httpx.AsyncClient(transport=transport, timeout=timeout)

    async def generate(
        self,
        system: str,
        prompt: str,
        format: dict | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: int = 120,
        num_ctx: int | None = None,  # ignored, Ollama-only
    ) -> str | dict:
        """Call OpenAI-compatible chat completions API.

        Returns dict when format is given, str otherwise.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if format is not None:
            payload["response_format"] = {"type": "json_object"}

        sem = _get_cloud_semaphore()
        async with sem:
            logger.debug("Cloud semaphore acquired for generate()")
            try:
                async with self._make_client(
                    httpx.Timeout(timeout, connect=10.0)
                ) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=self._headers(),
                    )
                    resp.raise_for_status()
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"Cloud API request timed out after {timeout}s"
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"Cloud API HTTP error {exc.response.status_code}: "
                    f"{exc.response.text[:300]}"
                ) from exc

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise LLMError("Empty choices in cloud API response")

        choice = choices[0]
        content: str = choice.get("message", {}).get("content", "")
        if not content:
            raise LLMError("Empty content in cloud API response")

        finish_reason = choice.get("finish_reason", "")

        if format is not None:
            if finish_reason == "length":
                logger.warning(
                    "Cloud API output truncated (finish_reason=length), "
                    "attempting to repair JSON (%d chars)", len(content),
                )
                content = _repair_truncated_json(content)

            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return _extract_json(content)

        return content

    async def generate_stream(
        self,
        system: str,
        prompt: str,
        timeout: int = 180,
    ) -> AsyncIterator[str]:
        """Stream tokens from OpenAI-compatible chat completions API.

        Uses SSE format: `data: {...}` lines, terminated by `data: [DONE]`.
        Does NOT acquire the semaphore (same rationale as OllamaClient).
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

        logger.debug("generate_stream() sending request to cloud API (no semaphore)")
        async with self._make_client(
            httpx.Timeout(timeout, connect=10.0)
        ) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    # SSE format: "data: {json}" or "data: [DONE]"
                    if line.startswith("data: "):
                        line = line[6:]
                    if line.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield token
