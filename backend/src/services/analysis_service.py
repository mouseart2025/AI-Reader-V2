"""Analysis service: orchestrates chapter-by-chapter analysis with progress broadcasting."""

import asyncio
import logging
import time
import uuid

from fastapi import WebSocket

from src.db import analysis_task_store, chapter_fact_store
from src.extraction.chapter_fact_extractor import ChapterFactExtractor, ExtractionError
from src.extraction.context_summary_builder import ContextSummaryBuilder
from src.extraction.fact_validator import FactValidator
from src.infra.config import OLLAMA_MODEL
from src.infra.llm_client import get_llm_client
from src.services import embedding_service

logger = logging.getLogger(__name__)


class _ConnectionManager:
    """Manage WebSocket connections per novel_id for analysis progress broadcasting."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, novel_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(novel_id, []).append(ws)

    def disconnect(self, novel_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(novel_id, [])
        if ws in conns:
            conns.remove(ws)

    async def broadcast(self, novel_id: str, data: dict) -> None:
        conns = self._connections.get(novel_id, [])
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            conns.remove(ws)


# Module-level singleton
manager = _ConnectionManager()


class AnalysisService:
    """Orchestrate full-novel chapter analysis."""

    def __init__(self):
        self.extractor = ChapterFactExtractor(get_llm_client())
        self.validator = FactValidator()
        self.context_builder = ContextSummaryBuilder()
        # Track running tasks for pause/cancel
        self._task_signals: dict[str, str] = {}  # task_id -> desired status

    async def start(
        self,
        novel_id: str,
        chapter_start: int,
        chapter_end: int,
        force: bool = False,
    ) -> str:
        """Start analysis, returns task_id. The analysis loop runs as a background task.

        If force=True, re-analyze even already-completed chapters.
        If force=False (default), skip chapters with analysis_status='completed'.
        """
        # Check if there's already a running task
        existing = await analysis_task_store.get_running_task(novel_id)
        if existing:
            raise ValueError(f"Novel {novel_id} already has an active task: {existing['id']}")

        task_id = str(uuid.uuid4())
        await analysis_task_store.create_task(task_id, novel_id, chapter_start, chapter_end)
        self._task_signals[task_id] = "running"

        # Launch background analysis loop
        asyncio.create_task(self._run_loop(task_id, novel_id, chapter_start, chapter_end, force))

        return task_id

    async def resume(self, task_id: str) -> None:
        """Resume a paused task."""
        task = await analysis_task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task["status"] != "paused":
            raise ValueError(f"Task {task_id} is not paused (status={task['status']})")

        await analysis_task_store.update_task_status(task_id, "running")
        self._task_signals[task_id] = "running"

        # Resume from current_chapter + 1
        resume_from = task["current_chapter"] + 1
        chapter_end = task["chapter_end"]
        novel_id = task["novel_id"]

        asyncio.create_task(self._run_loop(task_id, novel_id, resume_from, chapter_end))

        await manager.broadcast(novel_id, {"type": "task_status", "status": "running"})

    async def pause(self, task_id: str) -> None:
        """Signal a running task to pause after current chapter."""
        task = await analysis_task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        self._task_signals[task_id] = "paused"

    async def cancel(self, task_id: str) -> None:
        """Signal a running task to cancel after current chapter."""
        task = await analysis_task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        self._task_signals[task_id] = "cancelled"

    async def _run_loop(
        self,
        task_id: str,
        novel_id: str,
        chapter_start: int,
        chapter_end: int,
        force: bool = False,
    ) -> None:
        """Main analysis loop. Runs as a background asyncio task."""
        total = chapter_end - chapter_start + 1
        stats = {"entities": 0, "relations": 0, "events": 0}

        # Broadcast initial state immediately so frontend shows total count
        await manager.broadcast(novel_id, {
            "type": "progress",
            "chapter": chapter_start,
            "total": total,
            "done": 0,
            "stats": stats,
        })

        for chapter_num in range(chapter_start, chapter_end + 1):
            # Check for pause/cancel signal
            signal = self._task_signals.get(task_id, "running")
            if signal == "paused":
                await analysis_task_store.update_task_status(task_id, "paused")
                await manager.broadcast(novel_id, {"type": "task_status", "status": "paused"})
                logger.info("Task %s paused at chapter %d", task_id, chapter_num)
                return
            if signal == "cancelled":
                await analysis_task_store.update_task_status(task_id, "cancelled")
                await manager.broadcast(novel_id, {"type": "task_status", "status": "cancelled"})
                logger.info("Task %s cancelled at chapter %d", task_id, chapter_num)
                self._task_signals.pop(task_id, None)
                return

            # Get chapter content
            chapter = await analysis_task_store.get_chapter_content(novel_id, chapter_num)
            if not chapter:
                logger.warning("Chapter %d not found for novel %s, skipping", chapter_num, novel_id)
                # Still broadcast progress so frontend updates
                done_count = chapter_num - chapter_start + 1
                await manager.broadcast(novel_id, {
                    "type": "progress",
                    "chapter": chapter_num,
                    "total": total,
                    "done": done_count,
                    "stats": stats,
                })
                continue

            # Skip already-completed chapters unless force=True
            if not force and chapter["analysis_status"] == "completed":
                logger.debug("Skipping already-completed chapter %d", chapter_num)
                await analysis_task_store.update_task_progress(task_id, chapter_num)
                done_count = chapter_num - chapter_start + 1
                await manager.broadcast(novel_id, {
                    "type": "progress",
                    "chapter": chapter_num,
                    "total": total,
                    "done": done_count,
                    "stats": stats,
                })
                continue

            # Broadcast "processing" before LLM call so UI shows current chapter
            await manager.broadcast(novel_id, {
                "type": "processing",
                "chapter": chapter_num,
                "total": total,
            })

            chapter_pk = chapter["id"]
            start_ms = int(time.time() * 1000)

            try:
                # Build context summary
                context = await self.context_builder.build(novel_id, chapter_num)

                # Extract facts
                fact = await self.extractor.extract(
                    novel_id=novel_id,
                    chapter_id=chapter_num,
                    chapter_text=chapter["content"],
                    context_summary=context,
                )

                # Validate
                fact = self.validator.validate(fact)

                elapsed_ms = int(time.time() * 1000) - start_ms

                # Store
                await chapter_fact_store.insert_chapter_fact(
                    novel_id=novel_id,
                    chapter_id=chapter_pk,
                    fact=fact,
                    llm_model=OLLAMA_MODEL,
                    extraction_ms=elapsed_ms,
                )

                # Index embeddings in ChromaDB
                try:
                    fact_data = fact.model_dump()
                    fact_summary = embedding_service.build_fact_summary(fact_data)
                    embedding_service.index_chapter(
                        novel_id, chapter_num, chapter["content"], fact_summary
                    )
                    embedding_service.index_entities_from_fact(
                        novel_id, chapter_num, fact_data
                    )
                except Exception as e:
                    logger.warning("Embedding indexing failed for chapter %d: %s", chapter_num, e)

                # Update chapter status
                await analysis_task_store.update_chapter_analysis_status(
                    novel_id, chapter_num, "completed"
                )

                # Update cumulative stats
                stats["entities"] += len(fact.characters) + len(fact.locations)
                stats["relations"] += len(fact.relationships)
                stats["events"] += len(fact.events)

                # Broadcast chapter done
                await manager.broadcast(novel_id, {
                    "type": "chapter_done",
                    "chapter": chapter_num,
                    "status": "completed",
                })

            except ExtractionError as e:
                elapsed_ms = int(time.time() * 1000) - start_ms
                logger.error("Extraction failed for chapter %d: %s", chapter_num, e)
                await analysis_task_store.update_chapter_analysis_status(
                    novel_id, chapter_num, "failed"
                )
                await manager.broadcast(novel_id, {
                    "type": "chapter_done",
                    "chapter": chapter_num,
                    "status": "failed",
                    "error": str(e),
                })

            except Exception as e:
                elapsed_ms = int(time.time() * 1000) - start_ms
                logger.error("Unexpected error for chapter %d: %s", chapter_num, e)
                await analysis_task_store.update_chapter_analysis_status(
                    novel_id, chapter_num, "failed"
                )
                await manager.broadcast(novel_id, {
                    "type": "chapter_done",
                    "chapter": chapter_num,
                    "status": "failed",
                    "error": str(e),
                })

            # Update task progress
            await analysis_task_store.update_task_progress(task_id, chapter_num)

            # Broadcast overall progress
            done_count = chapter_num - chapter_start + 1
            await manager.broadcast(novel_id, {
                "type": "progress",
                "chapter": chapter_num,
                "total": total,
                "done": done_count,
                "stats": stats,
            })

        # All chapters processed
        await analysis_task_store.update_task_status(task_id, "completed")
        await manager.broadcast(novel_id, {
            "type": "task_status",
            "status": "completed",
            "stats": stats,
        })
        self._task_signals.pop(task_id, None)
        logger.info("Task %s completed for novel %s", task_id, novel_id)


# Module-level singleton
_service: AnalysisService | None = None


def get_analysis_service() -> AnalysisService:
    """Return module-level singleton AnalysisService."""
    global _service
    if _service is None:
        _service = AnalysisService()
    return _service
