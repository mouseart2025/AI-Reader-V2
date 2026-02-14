"""Analysis service: orchestrates chapter-by-chapter analysis with progress broadcasting."""

import asyncio
import logging
import time
import uuid

from fastapi import WebSocket

from src.db import analysis_task_store, chapter_fact_store, entity_dictionary_store
from src.extraction.chapter_fact_extractor import ChapterFactExtractor, ExtractionError
from src.extraction.context_summary_builder import ContextSummaryBuilder
from src.extraction.fact_validator import FactValidator
from src.infra.config import OLLAMA_MODEL
from src.infra.llm_client import get_llm_client
from src.services import embedding_service
from src.services.visualization_service import invalidate_layout_cache
from src.services.world_structure_agent import WorldStructureAgent

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
        self._active_loops: set[str] = set()  # task_ids with currently-running loops

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

        # Ensure pre-scan is done before analysis (skip on force re-analyze)
        if not force:
            await self._ensure_prescan(novel_id)

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

        novel_id = task["novel_id"]
        await manager.broadcast(novel_id, {"type": "task_status", "status": "running"})

        # Only start a new loop if the old one has fully exited.
        # If the old loop is still running (finishing its current chapter after
        # pause was signalled), it will see the signal reset to "running" and
        # continue on its own — no new loop needed.
        if task_id not in self._active_loops:
            resume_from = task["current_chapter"] + 1
            chapter_end = task["chapter_end"]
            asyncio.create_task(self._run_loop(task_id, novel_id, resume_from, chapter_end))

    async def pause(self, task_id: str) -> None:
        """Signal a running task to pause after current chapter.

        Updates DB and broadcasts immediately so the UI responds instantly.
        The loop will finish the current chapter and then stop.
        """
        task = await analysis_task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        self._task_signals[task_id] = "paused"
        # Immediate DB + broadcast so frontend updates without waiting for the loop
        await analysis_task_store.update_task_status(task_id, "paused")
        await manager.broadcast(task["novel_id"], {"type": "task_status", "status": "paused"})

    async def cancel(self, task_id: str) -> None:
        """Signal a running task to cancel after current chapter.

        Updates DB and broadcasts immediately so the UI responds instantly.
        """
        task = await analysis_task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        self._task_signals[task_id] = "cancelled"
        # Immediate DB + broadcast so frontend updates without waiting for the loop
        await analysis_task_store.update_task_status(task_id, "cancelled")
        await manager.broadcast(task["novel_id"], {"type": "task_status", "status": "cancelled"})

    async def _ensure_prescan(self, novel_id: str) -> None:
        """Ensure pre-scan is done before analysis starts.

        - pending: trigger synchronously
        - running: wait up to 120s
        - failed: log warning, continue without dictionary
        - completed: no-op
        """
        status = await entity_dictionary_store.get_prescan_status(novel_id)

        if status == "completed":
            return

        if status == "pending":
            try:
                from src.extraction.entity_pre_scanner import EntityPreScanner
                scanner = EntityPreScanner()
                await scanner.scan(novel_id)
            except Exception:
                logger.warning("分析启动前预扫描失败，将以无词典模式继续", exc_info=True)
            return

        if status == "running":
            # Poll every 5s, up to 120s
            for _ in range(24):
                await asyncio.sleep(5)
                status = await entity_dictionary_store.get_prescan_status(novel_id)
                if status != "running":
                    break
            if status == "running":
                logger.warning("预扫描超时(120s)，将以无词典模式继续")
            return

        # status == "failed"
        logger.warning("预扫描状态为 failed，将以无词典模式继续")

    async def _run_loop(
        self,
        task_id: str,
        novel_id: str,
        chapter_start: int,
        chapter_end: int,
        force: bool = False,
    ) -> None:
        """Main analysis loop. Runs as a background asyncio task."""
        self._active_loops.add(task_id)
        try:
            await self._run_loop_inner(task_id, novel_id, chapter_start, chapter_end, force)
        finally:
            self._active_loops.discard(task_id)

    async def _run_loop_inner(
        self,
        task_id: str,
        novel_id: str,
        chapter_start: int,
        chapter_end: int,
        force: bool = False,
    ) -> None:
        """Inner analysis loop body."""
        total = chapter_end - chapter_start + 1
        stats = {"entities": 0, "relations": 0, "events": 0}

        # Initialize WorldStructureAgent (loads existing or creates default)
        world_agent = WorldStructureAgent(novel_id)
        try:
            await world_agent.load_or_init()
        except Exception as e:
            logger.warning("WorldStructureAgent init failed for %s: %s", novel_id, e)

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
                # DB status and broadcast already handled by pause()
                logger.info("Task %s loop stopping (paused) at chapter %d", task_id, chapter_num)
                return
            if signal == "cancelled":
                # DB status and broadcast already handled by cancel()
                logger.info("Task %s loop stopping (cancelled) at chapter %d", task_id, chapter_num)
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

            # Skip excluded chapters (user decision — always skip, even with force)
            if chapter.get("is_excluded"):
                logger.debug("Skipping excluded chapter %d", chapter_num)
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

                # Update world structure (never blocks pipeline)
                world_structure_updated = False
                try:
                    await world_agent.process_chapter(
                        chapter_num, chapter["content"], fact,
                    )
                    world_structure_updated = True
                except Exception as e:
                    logger.warning(
                        "World structure agent error for chapter %d: %s",
                        chapter_num, e,
                    )

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

                # Invalidate map layout cache (spatial data may have changed)
                await invalidate_layout_cache(novel_id)

                # Update cumulative stats
                stats["entities"] += len(fact.characters) + len(fact.locations)
                stats["relations"] += len(fact.relationships)
                stats["events"] += len(fact.events)

                # Broadcast chapter done
                await manager.broadcast(novel_id, {
                    "type": "chapter_done",
                    "chapter": chapter_num,
                    "status": "completed",
                    "world_structure_updated": world_structure_updated,
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
