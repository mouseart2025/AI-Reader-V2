"""QA 守卫测试：chroma 向量索引生命周期对齐 + 实体停用词过滤（issue #56 后续修复）。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.analysis import clear_analysis_data
from src.api.routes.chapters import ChapterExcludeRequest, exclude_chapters
from src.services.query_service import _resolve_question_entities


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    conn.close = AsyncMock()
    return conn


# ── clear_analysis_data 同步删除 chroma 索引 ──────────


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_analysis_data_deletes_chroma_collections():
    """清除分析数据时应同时删除该 novel 的 chroma collections。"""
    conn = _mock_conn()
    with (
        patch("src.api.routes.analysis.novel_store") as mock_novel_store,
        patch("src.api.routes.analysis.analysis_task_store") as mock_task_store,
        patch("src.api.routes.analysis.chapter_fact_store") as mock_fact_store,
        patch("src.api.routes.analysis.get_connection", new=AsyncMock(return_value=conn)),
        patch("src.api.routes.analysis.embedding_service") as mock_embedding,
    ):
        mock_novel_store.get_novel = AsyncMock(return_value={"id": "n1", "title": "测试"})
        mock_task_store.get_latest_task = AsyncMock(return_value=None)
        mock_fact_store.delete_chapter_facts = AsyncMock()

        result = await clear_analysis_data("n1")

        assert result["ok"] is True
        mock_embedding.delete_novel_collections.assert_called_once_with("n1")


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_analysis_data_chroma_failure_not_propagated():
    """chroma 删除抛异常（如未初始化）时路由不应 500。"""
    conn = _mock_conn()
    with (
        patch("src.api.routes.analysis.novel_store") as mock_novel_store,
        patch("src.api.routes.analysis.analysis_task_store") as mock_task_store,
        patch("src.api.routes.analysis.chapter_fact_store") as mock_fact_store,
        patch("src.api.routes.analysis.get_connection", new=AsyncMock(return_value=conn)),
        patch("src.api.routes.analysis.embedding_service") as mock_embedding,
    ):
        mock_novel_store.get_novel = AsyncMock(return_value={"id": "n1", "title": "测试"})
        mock_task_store.get_latest_task = AsyncMock(return_value=None)
        mock_fact_store.delete_chapter_facts = AsyncMock()
        mock_embedding.delete_novel_collections.side_effect = RuntimeError("chroma down")

        result = await clear_analysis_data("n1")

        assert result["ok"] is True


# ── 章节排除同步删除 chroma 向量 ─────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_exclude_chapters_deletes_embeddings():
    """排除章节时应删除对应 ch_{n} 向量。"""
    with (
        patch("src.api.routes.chapters.novel_store") as mock_novel_store,
        patch("src.api.routes.chapters.chapter_store") as mock_chapter_store,
        patch("src.api.routes.chapters.embedding_service") as mock_embedding,
    ):
        mock_novel_store.get_novel = AsyncMock(return_value={"id": "n1"})
        mock_chapter_store.set_chapters_excluded = AsyncMock()
        mock_chapter_store.delete_chapter_facts = AsyncMock(return_value=2)
        mock_chapter_store.list_chapters = AsyncMock(return_value=[])

        req = ChapterExcludeRequest(chapter_nums=[3, 5], excluded=True)
        await exclude_chapters("n1", req)

        mock_embedding.delete_chapter_embeddings.assert_called_once_with("n1", [3, 5])


@pytest.mark.asyncio(loop_scope="session")
async def test_exclude_chapters_embedding_failure_not_propagated():
    """chroma 删除抛异常时排除路由不应失败。"""
    with (
        patch("src.api.routes.chapters.novel_store") as mock_novel_store,
        patch("src.api.routes.chapters.chapter_store") as mock_chapter_store,
        patch("src.api.routes.chapters.embedding_service") as mock_embedding,
    ):
        mock_novel_store.get_novel = AsyncMock(return_value={"id": "n1"})
        mock_chapter_store.set_chapters_excluded = AsyncMock()
        mock_chapter_store.delete_chapter_facts = AsyncMock(return_value=1)
        mock_chapter_store.list_chapters = AsyncMock(return_value=[])
        mock_embedding.delete_chapter_embeddings.side_effect = RuntimeError("chroma down")

        req = ChapterExcludeRequest(chapter_nums=[3], excluded=True)
        result = await exclude_chapters("n1", req)

        assert result["chapters"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_restore_chapters_does_not_touch_embeddings():
    """恢复章节不删除向量（重分析会重新索引）。"""
    with (
        patch("src.api.routes.chapters.novel_store") as mock_novel_store,
        patch("src.api.routes.chapters.chapter_store") as mock_chapter_store,
        patch("src.api.routes.chapters.embedding_service") as mock_embedding,
    ):
        mock_novel_store.get_novel = AsyncMock(return_value={"id": "n1"})
        mock_chapter_store.set_chapters_excluded = AsyncMock()
        mock_chapter_store.list_chapters = AsyncMock(return_value=[])

        req = ChapterExcludeRequest(chapter_nums=[3], excluded=False)
        await exclude_chapters("n1", req)

        mock_embedding.delete_chapter_embeddings.assert_not_called()


# ── 实体停用词过滤 ───────────────────────────────────


def test_stopword_alias_not_matched_via_substring():
    """"西游记的主人公是谁"不应因子串"公主"误命中别名对应实体。"""
    all_entities = {"公主"}
    alias_map = {"公主": "孔雀公主"}
    result = _resolve_question_entities("西游记的主人公是谁", all_entities, alias_map)
    assert result == []


def test_question_with_real_entity_unaffected():
    """"公主孔雀明王"这类含真实实体的问法仍能解析出该实体。"""
    all_entities = {"公主", "孔雀明王"}
    alias_map = {"公主": "孔雀明王"}
    result = _resolve_question_entities("公主孔雀明王的来历", all_entities, alias_map)
    assert result == ["孔雀明王"]


def test_normal_entity_matching_still_works():
    """非停用词实体的常规匹配与别名解析不受影响。"""
    all_entities = {"孙悟空", "齐天大圣"}
    alias_map = {"齐天大圣": "孙悟空"}
    result = _resolve_question_entities("齐天大圣和孙悟空是什么关系", all_entities, alias_map)
    assert result == ["孙悟空"]
