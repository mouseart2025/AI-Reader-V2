"""Async Ollama HTTP API client with structured output support."""

import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

from src.infra.config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)


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
    ) -> str | dict:
        """Call Ollama chat API. Returns dict when format is given, str otherwise."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if format is not None:
            payload["format"] = format

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

        if format is not None:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return _extract_json(content)

        return content

    async def generate_stream(
        self,
        system: str,
        prompt: str,
        timeout: int = 60,
    ) -> AsyncIterator[str]:
        """Stream tokens from Ollama chat API."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

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
_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return module-level singleton LLMClient."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
