"""上下文窗口溢出 (exceed_context_size_error) 处理测试。

覆盖 issue #106:本地 Ollama 小模型 (qwen3:4b, num_ctx=16384) 的
prompt 超出上下文窗口时整章失败。修复后:
- llm_client 把 Ollama 400 溢出响应识别为 LLMContextOverflowError,
  并解析出 n_prompt_tokens / n_ctx;
- extractor 按比例缩短章节正文重试,保住章节;缩短仍超窗才判失败。

全部使用 fake LLM / fake HTTP,不打真实 Ollama。
"""

import httpx
import pytest

from src.extraction.chapter_fact_extractor import (
    ChapterFactExtractor,
    ExtractionError,
    ExtractionMeta,
)
from src.infra.llm_client import (
    LLMClient,
    LLMContextOverflowError,
    LLMError,
    LlmUsage,
    _parse_context_overflow,
)

# issue #106 截图中的真实报错体(Ollama 0.x 嵌套 JSON 格式)
_OLLAMA_OVERFLOW_BODY = (
    '{"error":"{\\"error\\":{\\"code\\":400,\\"message\\":\\"request (16466 '
    'tokens) exceeds the available context size (16384 tokens), try increasing '
    'it\\",\\"type\\":\\"exceed_context_size_error\\",'
    '\\"n_prompt_tokens\\":16466,\\"n_ctx\\":16384}}"}'
)

_OK_FACT = {
    "characters": [{"name": "宋江"}],
    "relationships": [],
    "locations": [],
    "events": [],
}


# ── _parse_context_overflow ──


def test_parse_overflow_full_body():
    err = _parse_context_overflow(400, _OLLAMA_OVERFLOW_BODY)
    assert isinstance(err, LLMContextOverflowError)
    assert err.prompt_tokens == 16466
    assert err.ctx_size == 16384


def test_parse_overflow_message_only():
    body = '{"error":"request (20000 tokens) exceeds the available context size (8192 tokens)"}'
    err = _parse_context_overflow(400, body)
    assert err is not None
    assert err.prompt_tokens == 20000
    assert err.ctx_size == 8192


def test_parse_overflow_ignores_other_errors():
    assert _parse_context_overflow(500, _OLLAMA_OVERFLOW_BODY) is None
    assert _parse_context_overflow(400, '{"error":"model not found"}') is None


# ── LLMClient.generate 的 400 接线 ──


class _FakeAsyncClient:
    """返回固定状态码/响应体的 httpx.AsyncClient 替身。"""

    def __init__(self, status: int, body: str):
        self._status = status
        self._body = body

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        req = httpx.Request("POST", url)
        return httpx.Response(self._status, content=self._body.encode(), request=req)


@pytest.mark.asyncio
async def test_generate_raises_typed_overflow(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(400, _OLLAMA_OVERFLOW_BODY))
    client = LLMClient(base_url="http://localhost:11434", model="qwen3:4b")
    with pytest.raises(LLMContextOverflowError) as exc_info:
        await client.generate(system="s", prompt="p")
    assert exc_info.value.prompt_tokens == 16466
    assert exc_info.value.ctx_size == 16384


@pytest.mark.asyncio
async def test_generate_other_400_stays_generic(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(400, '{"error":"bad request"}'))
    client = LLMClient(base_url="http://localhost:11434", model="qwen3:4b")
    with pytest.raises(LLMError) as exc_info:
        await client.generate(system="s", prompt="p")
    assert not isinstance(exc_info.value, LLMContextOverflowError)


# ── extractor 缩短重试 ──


class _OverflowOnceLLM:
    """prompt 过长时抛溢出,缩短/分段后正常返回;记录每次收到的正文量。"""

    def __init__(self, max_prompt_chars: int):
        self.max_prompt_chars = max_prompt_chars
        self.calls: list[int] = []
        self.chapter_chars_seen = 0  # 各次调用 prompt 里 "宋" 的总数

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        self.calls.append(len(prompt))
        if len(prompt) > self.max_prompt_chars:
            # 模拟 Ollama:报告与 prompt 长度成比例的 token 数
            raise LLMContextOverflowError(
                "request exceeds the available context size",
                prompt_tokens=len(prompt),
                ctx_size=self.max_prompt_chars,
            )
        self.chapter_chars_seen += prompt.count("宋")
        return dict(_OK_FACT), LlmUsage(100, 10, 110)


class _AlwaysOverflowLLM:
    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        raise LLMContextOverflowError(
            "request exceeds the available context size",
            prompt_tokens=99999,
            ctx_size=8192,
        )


def _make_extractor(llm) -> ChapterFactExtractor:
    return ChapterFactExtractor(llm=llm)


@pytest.mark.asyncio
async def test_overflow_segments_chapter_and_keeps_full_coverage():
    """超窗首选降级:均分成多段分别抽取再合并,全文都被分析(不截断)。"""
    chapter_text = "宋" * 20000
    llm = _OverflowOnceLLM(max_prompt_chars=20000)
    extractor = _make_extractor(llm)
    meta = ExtractionMeta(original_len=len(chapter_text))

    fact, _usage = await extractor._extract_single(
        "system", "novel-1", 1, chapter_text, meta=meta,
    )

    assert fact.characters[0].name == "宋江"
    # 走了分段路径:多次调用且每段都放得下
    assert meta.segment_count >= 2
    assert not meta.is_truncated
    # 全文 20000 字都被送到过模型(分段合计 == 原文长度,无丢失)
    assert llm.chapter_chars_seen == len(chapter_text)


@pytest.mark.asyncio
async def test_overflow_falls_back_to_shrink_when_unsegmentable():
    """正文远超窗口(分段数超上限)时退化为缩短重试,并记录截断。"""
    chapter_text = "宋" * 60000
    llm = _OverflowOnceLLM(max_prompt_chars=12000)
    extractor = _make_extractor(llm)
    meta = ExtractionMeta(original_len=len(chapter_text))

    fact, _usage = await extractor._extract_single(
        "system", "novel-1", 1, chapter_text, meta=meta,
    )

    assert fact.characters[0].name == "宋江"
    assert meta.is_truncated
    assert meta.truncated_len < len(chapter_text)


@pytest.mark.asyncio
async def test_overflow_exhausted_raises_extraction_error():
    chapter_text = "宋" * 20000
    extractor = _make_extractor(_AlwaysOverflowLLM())

    with pytest.raises(ExtractionError) as exc_info:
        await extractor._extract_single(
            "system", "novel-1", 1, chapter_text, meta=ExtractionMeta(),
        )
    assert "context window" in str(exc_info.value)


@pytest.mark.asyncio
async def test_overflow_without_token_counts_halves_text():
    """err 不带 token 数时按默认 2 段 + 段内对半砍收敛,仍能成功。"""

    class _HalvingLLM:
        def __init__(self):
            self.calls = 0

        async def generate(self, system, prompt, format=None, temperature=0.1,
                           max_tokens=4096, timeout=120, num_ctx=None):
            self.calls += 1
            if "宋" * 6000 in prompt:
                raise LLMContextOverflowError("context exceeded")
            return dict(_OK_FACT), LlmUsage(100, 10, 110)

    llm = _HalvingLLM()
    extractor = _make_extractor(llm)
    fact, _ = await extractor._extract_single(
        "system", "novel-1", 1, "宋" * 20000, meta=ExtractionMeta(),
    )
    assert fact.characters[0].name == "宋江"
    assert llm.calls >= 2


@pytest.mark.asyncio
async def test_non_overflow_error_keeps_legacy_retry():
    """非溢出错误不受新逻辑影响:仍走 retry_len 截断重试并计 2 次尝试。"""

    class _FlakyLLM:
        def __init__(self):
            self.calls = 0

        async def generate(self, system, prompt, format=None, temperature=0.1,
                           max_tokens=4096, timeout=120, num_ctx=None):
            self.calls += 1
            if self.calls == 1:
                raise LLMError("some transient parse failure")
            return dict(_OK_FACT), LlmUsage(100, 10, 110)

    llm = _FlakyLLM()
    extractor = _make_extractor(llm)
    fact, _ = await extractor._extract_single(
        "system", "novel-1", 1, "宋" * 3000, meta=ExtractionMeta(),
    )
    assert fact.characters[0].name == "宋江"
    assert llm.calls == 2
