"""Analysis service: orchestrates chapter-by-chapter analysis with progress broadcasting."""

import asyncio
import logging
import time
import uuid

from fastapi import WebSocket

from src.db import analysis_task_store, chapter_fact_store, entity_dictionary_store
from src.db import world_structure_store
from src.extraction.chapter_fact_extractor import ChapterFactExtractor, ExtractionError
from src.extraction.context_summary_builder import ContextSummaryBuilder
from src.extraction.fact_validator import FactValidator
from src.extraction.scene_llm_extractor import SceneLLMExtractor
from src.infra.config import LLM_MODEL, LLM_PROVIDER, OLLAMA_MODEL
from src.infra.llm_client import LlmUsage, get_llm_client
from src.models.world_structure import WorldStructure
from src.services.cost_service import add_monthly_usage, get_monthly_budget, get_monthly_usage, get_pricing
from src.services import embedding_service
from src.services.hierarchy_consolidator import consolidate_hierarchy
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
        # Inject novel_id so the frontend can filter stale/cross-novel messages
        payload = {**data, "novel_id": novel_id}
        for ws in conns:
            try:
                await ws.send_json(payload)
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
        self.scene_extractor = SceneLLMExtractor(get_llm_client())
        # Track running tasks for pause/cancel
        self._task_signals: dict[str, str] = {}  # task_id -> desired status
        self._active_loops: set[str] = set()  # task_ids with currently-running loops

    @staticmethod
    async def _broadcast_stage(novel_id: str, chapter: int, label: str) -> None:
        """Broadcast a stage label for the current chapter processing step."""
        await manager.broadcast(novel_id, {
            "type": "stage",
            "chapter": chapter,
            "stage_label": label,
        })

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

        # Cost tracking (cloud mode only)
        from src.infra import config as _cfg
        is_cloud = _cfg.LLM_PROVIDER == "openai"
        cost_stats: dict = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "total_cost_cny": 0.0,
            "estimated_remaining_usd": 0.0,
            "estimated_remaining_cny": 0.0,
            "is_cloud": is_cloud,
            "monthly_used_cny": 0.0,
            "monthly_budget_cny": 0.0,
        }
        if is_cloud:
            _input_price, _output_price = get_pricing(_cfg.LLM_MODEL or "")
            # Load initial monthly usage and budget
            _monthly_usage = await get_monthly_usage()
            _monthly_budget = await get_monthly_budget()
            cost_stats["monthly_used_cny"] = _monthly_usage.get("cny", 0.0)
            cost_stats["monthly_budget_cny"] = _monthly_budget
        else:
            _input_price, _output_price = 0.0, 0.0
        _chapters_done_with_cost = 0

        # Pre-compute stats from existing chapter facts so resumed analysis
        # shows cumulative counts (not zeros) for already-completed chapters.
        if not force:
            existing_facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
            for ef in existing_facts:
                ch_id = ef.get("chapter_id", 0)
                if chapter_start <= ch_id <= chapter_end:
                    fact_data = ef.get("fact", {})
                    stats["entities"] += len(fact_data.get("characters", [])) + len(fact_data.get("locations", []))
                    stats["relations"] += len(fact_data.get("relationships", []))
                    stats["events"] += len(fact_data.get("events", []))

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
                # Build context summary (inject location hierarchy if available)
                await self._broadcast_stage(novel_id, chapter_num, "构建上下文")
                _loc_parents = (
                    world_agent.structure.location_parents
                    if world_agent.structure else None
                )
                context = await self.context_builder.build(
                    novel_id, chapter_num, location_parents=_loc_parents,
                )

                # Extract facts
                await self._broadcast_stage(novel_id, chapter_num, "AI 提取中")
                fact, chapter_usage = await self.extractor.extract(
                    novel_id=novel_id,
                    chapter_id=chapter_num,
                    chapter_text=chapter["content"],
                    context_summary=context,
                )

                # Accumulate cost (cloud mode)
                if is_cloud:
                    cost_stats["total_input_tokens"] += chapter_usage.prompt_tokens
                    cost_stats["total_output_tokens"] += chapter_usage.completion_tokens
                    spent_usd = (
                        (chapter_usage.prompt_tokens / 1_000_000) * _input_price
                        + (chapter_usage.completion_tokens / 1_000_000) * _output_price
                    )
                    spent_cny = spent_usd * 7.2
                    cost_stats["total_cost_usd"] = round(
                        cost_stats["total_cost_usd"] + spent_usd, 4
                    )
                    cost_stats["total_cost_cny"] = round(
                        cost_stats["total_cost_usd"] * 7.2, 2
                    )
                    _chapters_done_with_cost += 1
                    remaining = total - (chapter_num - chapter_start + 1)
                    if _chapters_done_with_cost > 0 and remaining > 0:
                        avg_cost = cost_stats["total_cost_usd"] / _chapters_done_with_cost
                        cost_stats["estimated_remaining_usd"] = round(avg_cost * remaining, 4)
                        cost_stats["estimated_remaining_cny"] = round(
                            cost_stats["estimated_remaining_usd"] * 7.2, 2
                        )
                    else:
                        cost_stats["estimated_remaining_usd"] = 0.0
                        cost_stats["estimated_remaining_cny"] = 0.0

                    # Persist to monthly usage
                    updated = await add_monthly_usage(
                        spent_usd, spent_cny,
                        chapter_usage.prompt_tokens,
                        chapter_usage.completion_tokens,
                    )
                    cost_stats["monthly_used_cny"] = updated.get("cny", 0.0)

                # Validate
                await self._broadcast_stage(novel_id, chapter_num, "验证数据")
                fact = self.validator.validate(fact)

                # Update world structure (never blocks pipeline)
                await self._broadcast_stage(novel_id, chapter_num, "更新世界结构")
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

                await self._broadcast_stage(novel_id, chapter_num, "保存数据")
                elapsed_ms = int(time.time() * 1000) - start_ms

                # Per-chapter cost (cloud mode)
                _ch_cost_usd = 0.0
                _ch_cost_cny = 0.0
                if is_cloud:
                    _ch_cost_usd = round(
                        (chapter_usage.prompt_tokens / 1_000_000) * _input_price
                        + (chapter_usage.completion_tokens / 1_000_000) * _output_price,
                        6,
                    )
                    _ch_cost_cny = round(_ch_cost_usd * 7.2, 4)

                # Store fact first (INSERT OR REPLACE creates the row)
                await chapter_fact_store.insert_chapter_fact(
                    novel_id=novel_id,
                    chapter_id=chapter_pk,
                    fact=fact,
                    llm_model=OLLAMA_MODEL,
                    extraction_ms=elapsed_ms,
                    input_tokens=chapter_usage.prompt_tokens,
                    output_tokens=chapter_usage.completion_tokens,
                    cost_usd=_ch_cost_usd,
                    cost_cny=_ch_cost_cny,
                )

                # Scene extraction via LLM (non-fatal)
                # Must run AFTER insert_chapter_fact so the row exists for UPDATE
                await self._broadcast_stage(novel_id, chapter_num, "场景分析")
                try:
                    scenes = await self.scene_extractor.extract(
                        chapter["content"], chapter_num, fact,
                    )
                    if scenes:
                        await chapter_fact_store.update_scenes(
                            novel_id, chapter_pk, scenes,
                        )
                except Exception as e:
                    logger.warning(
                        "场景提取失败 (chapter %d): %s", chapter_num, e,
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
            progress_msg: dict = {
                "type": "progress",
                "chapter": chapter_num,
                "total": total,
                "done": done_count,
                "stats": stats,
            }
            if is_cloud:
                progress_msg["cost"] = cost_stats
            await manager.broadcast(novel_id, progress_msg)

        # ── Post-analysis: location hierarchy enhancement ──
        try:
            await self._broadcast_stage(novel_id, chapter_end, "优化地点层级")

            all_scenes = await chapter_fact_store.get_all_scenes(novel_id)

            if all_scenes and world_agent.structure:
                # Part A: Scene transition analysis (pure algorithm, zero LLM cost)
                from src.services.scene_transition_analyzer import SceneTransitionAnalyzer
                analyzer = SceneTransitionAnalyzer()
                scene_votes, scene_analysis = analyzer.analyze(all_scenes)

                # Inject scene-derived votes
                if scene_votes:
                    world_agent.inject_external_votes(scene_votes)

                # Part B: LLM hierarchy review (only when orphan roots >= 3)
                orphan_count = _count_orphan_roots(world_agent.structure)
                if orphan_count >= 3:
                    from src.services.location_hierarchy_reviewer import LocationHierarchyReviewer
                    reviewer = LocationHierarchyReviewer()
                    review_votes = await reviewer.review(
                        location_tiers=world_agent.structure.location_tiers,
                        current_parents=world_agent.structure.location_parents,
                        scene_analysis=scene_analysis,
                        novel_genre_hint=world_agent.structure.novel_genre_hint,
                    )
                    if review_votes:
                        world_agent.inject_external_votes(review_votes)

                # Re-resolve parents and consolidate hierarchy
                world_agent.structure.location_parents = world_agent._resolve_parents()
                world_agent.structure.location_parents, world_agent.structure.location_tiers = (
                    consolidate_hierarchy(
                        world_agent.structure.location_parents,
                        world_agent.structure.location_tiers,
                        novel_genre_hint=world_agent.structure.novel_genre_hint,
                        parent_votes=world_agent._parent_votes,
                    )
                )
                await world_structure_store.save(world_agent.novel_id, world_agent.structure)

                logger.info("Post-analysis hierarchy enhancement done for %s", novel_id)
        except Exception as e:
            logger.warning("Post-analysis hierarchy enhancement failed: %s", e)
            # Non-fatal: continue to mark completed

        # All chapters processed
        await analysis_task_store.update_task_status(task_id, "completed")
        completed_msg: dict = {
            "type": "task_status",
            "status": "completed",
            "stats": stats,
        }
        if is_cloud:
            completed_msg["cost"] = cost_stats
        await manager.broadcast(novel_id, completed_msg)
        self._task_signals.pop(task_id, None)
        logger.info("Task %s completed for novel %s", task_id, novel_id)


def _count_orphan_roots(structure: WorldStructure) -> int:
    """Count locations with no parent that are not top-level tiers."""
    children = set(structure.location_parents.keys())
    all_locs = set(structure.location_tiers.keys())
    roots = all_locs - children
    return sum(
        1 for r in roots
        if structure.location_tiers.get(r, "city") not in ("world", "continent")
    )


# Module-level singleton
_service: AnalysisService | None = None


def get_analysis_service() -> AnalysisService:
    """Return module-level singleton AnalysisService."""
    global _service
    if _service is None:
        _service = AnalysisService()
    return _service


def refresh_service_clients() -> None:
    """Rebuild LLM clients in the singleton so new tasks use the updated config.

    Running tasks keep their existing client references (won't be interrupted).
    """
    if _service is None:
        return
    _service.extractor = ChapterFactExtractor(get_llm_client())
    _service.scene_extractor = SceneLLMExtractor(get_llm_client())
