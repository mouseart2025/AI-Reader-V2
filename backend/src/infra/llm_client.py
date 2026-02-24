"""Async LLM client with Ollama and OpenAI-compatible API support."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from src.infra.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER, OLLAMA_BASE_URL, OLLAMA_MODEL

if TYPE_CHECKING:
    from src.infra.anthropic_client import AnthropicClient
    from src.infra.openai_client import OpenAICompatibleClient

logger = logging.getLogger(__name__)


@dataclass
class LlmUsage:
    """Token usage from an LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


# Global semaphore to serialize Ollama calls (single GPU processes one request at a time).
# Without this, concurrent novel analyses cause timeout cascades.
_llm_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazily create semaphore in the running event loop."""
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(1)
    return _llm_semaphore


class LLMError(Exception):
    """Base exception for LLM client errors."""


class LLMTimeoutError(LLMError):
    """Raised when LLM request times out."""


class LLMParseError(LLMError):
    """Raised when JSON parsing of LLM response fails."""


def _extract_json(text: str) -> dict:
    """Try to extract JSON from text that may contain markdown fences or extra text."""
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find the first JSON object
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise LLMParseError(f"Failed to extract JSON from LLM response: {text[:200]}...")


class LLMClient:
    """Async client for Ollama API with structured output support."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def generate(
        self,
        system: str,
        prompt: str,
        format: dict | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: int = 120,
        num_ctx: int | None = None,
    ) -> tuple[str | dict, LlmUsage]:
        """Call Ollama chat API. Returns (content, usage) tuple.

        Content is dict when format is given, str otherwise.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        options: dict = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        # Allow caller to override context window (default 4096 is too small for
        # long chapters + system prompt + schema in structured output mode)
        if num_ctx is not None:
            options["num_ctx"] = num_ctx
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,  # Disable thinking mode (qwen3) to get content directly
            "options": options,
        }
        if format is not None:
            payload["format"] = format

        sem = _get_semaphore()
        async with sem:
            logger.debug("LLM semaphore acquired for generate()")
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout, connect=10.0)
                ) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/chat",
                        json=payload,
                    )
                    resp.raise_for_status()
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"Ollama request timed out after {timeout}s"
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"Ollama HTTP error {exc.response.status_code}: {exc.response.text[:300]}"
                ) from exc

        data = resp.json()
        content: str = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError("Empty response from Ollama")

        # Parse token usage from Ollama response
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)
        usage = LlmUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        if format is not None:
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
        """Stream tokens from Ollama chat API.

        NOTE: This method does NOT acquire the LLM semaphore. Chat/QA streaming
        must remain responsive even when analysis is running. Ollama handles
        concurrent request queueing internally — the chat request will wait in
        Ollama's queue until the current analysis chunk finishes, then stream
        immediately. This avoids indefinite Python-level blocking.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "think": False,  # Disable thinking mode (qwen3) for streaming
        }

        logger.debug("generate_stream() sending request (no semaphore)")
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0)
        ) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break


# Module-level singleton
_client: LLMClient | OpenAICompatibleClient | AnthropicClient | None = None


def get_llm_client() -> LLMClient | OpenAICompatibleClient | AnthropicClient:
    """Return module-level singleton LLM client based on LLM_PROVIDER config."""
    global _client
    if _client is None:
        if LLM_PROVIDER == "openai":
            if not LLM_API_KEY:
                raise ValueError("LLM_API_KEY is required when LLM_PROVIDER=openai")
            if not LLM_BASE_URL:
                raise ValueError("LLM_BASE_URL is required when LLM_PROVIDER=openai")
            from src.infra import config as _cfg
            if _cfg.LLM_PROVIDER_FORMAT == "anthropic":
                from src.infra.anthropic_client import AnthropicClient
                _client = AnthropicClient(
                    base_url=LLM_BASE_URL,
                    api_key=LLM_API_KEY,
                    model=LLM_MODEL or "claude-sonnet-4-5",
                )
            else:
                from src.infra.openai_client import OpenAICompatibleClient
                _client = OpenAICompatibleClient(
                    base_url=LLM_BASE_URL,
                    api_key=LLM_API_KEY,
                    model=LLM_MODEL or "gpt-4o",
                )
        else:
            _client = LLMClient()
    return _client
