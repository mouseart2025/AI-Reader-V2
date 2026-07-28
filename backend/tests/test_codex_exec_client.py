"""Tests for the Codex CLI LLM adapter."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.infra.codex_exec_client import (
    CodexExecClient,
    _build_instruction,
    _parse_jsonl,
)
from src.infra.llm_client import LLMError, LLMTimeoutError


def test_parse_jsonl_extracts_final_message_and_usage():
    output = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "test"}),
        json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"answer":"ok"}'},
        }),
        json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 123, "output_tokens": 45},
        }),
    ]).encode()

    content, usage = _parse_jsonl(output)

    assert content == '{"answer":"ok"}'
    assert usage.prompt_tokens == 123
    assert usage.completion_tokens == 45
    assert usage.total_tokens == 168


def test_structured_instruction_forbids_tools_and_placeholders():
    instruction = _build_instruction(
        "system",
        "prompt",
        max_tokens=100,
        structured=True,
    )
    assert "Do not inspect the filesystem" in instruction
    assert "never use ellipses" in instruction
    assert "complete, parseable JSON" in instruction


def test_default_command_does_not_pin_a_model():
    command = CodexExecClient(model="")._command(None)

    assert "--model" not in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert 'web_search="disabled"' in command
    assert command[-1] == "-"


@pytest.mark.asyncio
async def test_generate_uses_strict_schema_and_parses_json():
    process = AsyncMock()
    process.returncode = 0
    event = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": '{"answer":"ok"}'},
    }
    completed = {
        "type": "turn.completed",
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
    process.communicate.return_value = (
        (json.dumps(event) + "\n" + json.dumps(completed) + "\n").encode(),
        b"",
    )

    with patch("asyncio.create_subprocess_exec", return_value=process) as create:
        client = CodexExecClient(
            codex_bin="/usr/local/bin/codex",
            model="gpt-test",
            min_timeout_seconds=1,
        )
        result, usage = await client.generate(
            system="system",
            prompt="prompt",
            format={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )

    assert result == {"answer": "ok"}
    assert usage.total_tokens == 12
    args = create.call_args.args
    assert args[0] == "/usr/local/bin/codex"
    assert "--output-schema" in args
    assert args[args.index("--model") + 1] == "gpt-test"
    assert 'model_reasoning_effort="low"' in args
    process.communicate.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_skips_non_strict_schema_but_still_parses_json():
    process = AsyncMock()
    process.returncode = 0
    event = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": '{"answer":"ok"}'},
    }
    process.communicate.return_value = (
        (json.dumps(event) + "\n").encode(),
        b"",
    )

    with patch("asyncio.create_subprocess_exec", return_value=process) as create:
        client = CodexExecClient(min_timeout_seconds=1)
        result, _usage = await client.generate(
            system="system",
            prompt="prompt",
            format={"type": "object"},
        )

    assert result == {"answer": "ok"}
    assert "--output-schema" not in create.call_args.args


@pytest.mark.asyncio
async def test_generate_reports_error_without_echoing_agent_output():
    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (
        (
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "private novel output"},
            })
            + "\n"
            + json.dumps({"type": "error", "message": "authentication required"})
        ).encode(),
        b"",
    )

    with patch("asyncio.create_subprocess_exec", return_value=process):
        client = CodexExecClient(min_timeout_seconds=1)
        with pytest.raises(LLMError, match="authentication required") as exc_info:
            await client.generate(system="system", prompt="prompt")

    assert "private novel output" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_generate_kills_process_on_timeout():
    process = AsyncMock()
    process.returncode = None
    process.communicate.side_effect = asyncio.TimeoutError
    process.kill = Mock()

    with patch("asyncio.create_subprocess_exec", return_value=process):
        client = CodexExecClient(min_timeout_seconds=0)
        with pytest.raises(LLMTimeoutError):
            await client.generate(system="system", prompt="prompt", timeout=0)

    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_kills_process_when_caller_cancels():
    process = AsyncMock()
    process.returncode = None
    process.communicate.side_effect = asyncio.CancelledError
    process.kill = Mock()

    with patch("asyncio.create_subprocess_exec", return_value=process):
        client = CodexExecClient(min_timeout_seconds=1)
        with pytest.raises(asyncio.CancelledError):
            await client.generate(system="system", prompt="prompt")

    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
