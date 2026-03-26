"""LLM 客户端 — 支持 OpenAI 兼容 API + Anthropic API"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

from marketing.config import get_llm_config
from marketing.logger import get_logger

log = get_logger("llm")

# 模型定价（每百万 token，人民币）
_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_M, output_per_M)
    "deepseek-chat": (1.0, 2.0),
    "deepseek-reasoner": (4.0, 16.0),
    "claude-sonnet-4-20250514": (21.0, 105.0),
    "claude-haiku-4-5-20251001": (7.0, 35.0),
    "qwen-plus": (2.0, 6.0),
    "MiniMax-M2.5": (1.0, 8.0),
}


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cost_yuan: float
    model: str
    latency_ms: int


class LLMClient:
    """统一 LLM 客户端，支持 OpenAI 兼容 + Anthropic 格式"""

    def __init__(self, role: str = "analysis") -> None:
        cfg = get_llm_config(role)
        self.provider = cfg.get("provider", "openai")
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = cfg["api_key"]
        self.model = cfg["model"]
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.role = role

        if not self.api_key:
            log.warning("LLM API Key 为空 (role=%s)，请检查配置", role)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """发送聊天请求，自动重试"""
        max_tok = max_tokens or self.max_tokens

        for attempt in range(3):
            try:
                return await self._call(messages, temperature, max_tok, json_mode)
            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
                wait = 30 * (2 ** attempt)
                log.warning(
                    "LLM 调用失败 (attempt %d/3): %s — %d 秒后重试",
                    attempt + 1, e, wait,
                )
                if attempt == 2:
                    raise
                await asyncio.sleep(wait)

        raise RuntimeError("LLM 调用失败，已超过最大重试次数")

    async def _call(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResponse:
        start = time.monotonic()

        if self.provider == "anthropic":
            return await self._call_anthropic(messages, temperature, max_tokens, start)
        return await self._call_openai(messages, temperature, max_tokens, json_mode, start)

    async def _call_openai(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        start: float,
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        # 推理模型（如 MiniMax-M2.5）会返回 <think>...</think> 包裹的思考过程
        # 提取 </think> 之后的实际内容
        if "<think>" in content and "</think>" in content:
            content = content.split("</think>", 1)[-1].strip()
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost = self._calc_cost(input_tokens, output_tokens)
        latency = int((time.monotonic() - start) * 1000)

        log.info(
            "LLM [%s] %s: %d+%d tokens, ¥%.4f, %dms",
            self.role, self.model, input_tokens, output_tokens, cost, latency,
        )

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_yuan=cost,
            model=self.model,
            latency_ms=latency,
        )

    async def _call_anthropic(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        start: float,
    ) -> LLMResponse:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        # 分离 system message
        system_text = ""
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text += msg["content"] + "\n"
            else:
                user_messages.append(msg)

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": user_messages,
        }
        if system_text.strip():
            body["system"] = system_text.strip()

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()

        data = resp.json()
        content = data["content"][0]["text"]
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cost = self._calc_cost(input_tokens, output_tokens)
        latency = int((time.monotonic() - start) * 1000)

        log.info(
            "LLM [%s] %s: %d+%d tokens, ¥%.4f, %dms",
            self.role, self.model, input_tokens, output_tokens, cost, latency,
        )

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_yuan=cost,
            model=self.model,
            latency_ms=latency,
        )

    def _calc_cost(self, input_tokens: int, output_tokens: int) -> float:
        pricing = _PRICING.get(self.model, (1.0, 2.0))
        input_cost = input_tokens / 1_000_000 * pricing[0]
        output_cost = output_tokens / 1_000_000 * pricing[1]
        return input_cost + output_cost


async def test_connection() -> None:
    """测试 LLM 连接"""
    for role in ("analysis", "copywriting"):
        try:
            cfg = get_llm_config(role)
        except (KeyError, FileNotFoundError):
            print(f"⏭️  [{role}] 配置不存在，跳过")
            continue

        if not cfg.get("api_key"):
            print(f"⚠️  [{role}] API Key 为空，跳过")
            continue

        print(f"🔄 测试 [{role}] {cfg['model']}...")
        client = LLMClient(role)
        try:
            resp = await client.chat(
                [{"role": "user", "content": "Say 'hello' in one word."}],
                max_tokens=10,
            )
            print(
                f"✅ [{role}] 成功: \"{resp.content.strip()}\" "
                f"({resp.input_tokens}+{resp.output_tokens} tokens, "
                f"¥{resp.cost_yuan:.4f}, {resp.latency_ms}ms)"
            )
        except Exception as e:
            print(f"❌ [{role}] 失败: {e}")


if __name__ == "__main__":
    if "--test" in sys.argv:
        asyncio.run(test_connection())
    else:
        print("用法: python -m marketing.llm_client --test")
