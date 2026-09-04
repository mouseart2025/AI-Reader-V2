"""Source Pass service: 独立二审 (source pass) 运行循环 (multi-pass MVP, issue #70 Epic 2).

对已分析完的小说做一遍独立重读。独立性由数据路径保证(不靠 prompt 自觉):
- context 只由 原文 + 本 pass 前序 facts(pass_chapter_facts 影子表)
  + 预扫描实体词典(D1) 构建,机制上读不到 chapter_facts 主表与
  world_structures;
- 跳过幻觉判定层(三审职责)、世界结构更新、场景提取、向量索引与全部
  分析后处理;
- 产物只写影子表 analysis_passes / pass_chapter_facts,对
  chapter_facts / world_structures / entity_dictionary / Chroma 零写入。
"""

import asyncio
import copy
import logging
import time
import uuid
from datetime import datetime, timezone

from src.db import analysis_pass_store, analysis_task_store, chapter_store
from src.db import entity_dictionary_store
from src.extraction.chapter_fact_extractor import ChapterFactExtractor
from src.extraction.context_summary_builder import ContextSummaryBuilder
from src.extraction.fact_validator import FactValidator
from src.extraction.name_resolver import NameResolver
from src.infra.llm_client import get_llm_client
from src.models.chapter_fact import ChapterFact
from src.services.analysis_service import _classify_error, manager
from src.services.cost_service import (
    SCOPE_SOURCE_PASS,
    add_monthly_usage,
    get_pricing,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """history 埋点时间戳(UTC, ISO 格式)。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_pass_llm(model_override: str | None = None):
    """D3: 默认沿用当前 LLM 配置;model_override 时复制 client 换 model。

    LLM client 无状态(每次调用独立 [system, user]),浅拷贝改 model
    即得到隔离的第二个「模型视角」(增强错误倾向差异)。
    """
    base = get_llm_client()
    if not model_override or model_override == getattr(base, "model", None):
        return base
    client = copy.copy(base)
    client.model = model_override
    return client


class SourcePassService:
    """Orchestrate independent source-pass re-read for a novel."""

    def __init__(self, llm=None):
        self._llm = llm  # 测试注入;None 时按 pass 配置取当前/override client
        self.context_builder = ContextSummaryBuilder()
        # pause/cancel 信号与活动循环跟踪(模式同 AnalysisService)
        self._pass_signals: dict[str, str] = {}  # pass_id -> desired status
        self._active_loops: set[str] = set()  # pass_ids with currently-running loops

    @staticmethod
    async def _broadcast(novel_id: str, pass_id: str, data: dict) -> None:
        """二审广播:全部消息带 pass_id 维度,不复用一审频道语义。

        消息类型用 pass_* 前缀(pass_progress / pass_processing /
        pass_chapter_done / pass_status),与一审的 progress/processing/
        chapter_done/task_status 区分;通道仍是 /ws/analysis/{novel_id}。
        """
        await manager.broadcast(novel_id, {**data, "pass_id": pass_id})

    async def start(
        self,
        novel_id: str,
        chapter_start: int | None = None,
        chapter_end: int | None = None,
        model_override: str | None = None,
        include_dictionary: bool = True,
    ) -> str:
        """启动二审,返回 pass_id。运行循环作为后台 asyncio 任务执行。

        单活互斥(与 AnalysisService 双向):有一审任务或其他活动 pass
        时拒绝启动。
        """
        existing_task = await analysis_task_store.get_running_task(novel_id)
        if existing_task:
            raise ValueError(
                f"Novel {novel_id} already has an active task: {existing_task['id']}"
            )
        existing_pass = await analysis_pass_store.get_active_pass(novel_id)
        if existing_pass:
            raise ValueError(
                f"Novel {novel_id} already has an active pass: {existing_pass['id']}"
            )

        chapters = await chapter_store.list_chapters(novel_id)
        if not chapters:
            raise ValueError(f"Novel {novel_id} has no chapters")
        chapter_start = chapter_start or 1
        chapter_end = chapter_end or chapters[-1]["chapter_num"]

        pass_id = str(uuid.uuid4())
        from src.infra import config as _cfg
        model_name = model_override or _cfg.get_model_name()
        config = {
            "model_override": model_override,  # D3
            "include_dictionary": include_dictionary,  # D1 开关
        }
        await analysis_pass_store.create_pass(
            pass_id, novel_id, chapter_start, chapter_end,
            kind=analysis_pass_store.PASS_KIND_SOURCE,
            model_name=model_name, config=config, provider=_cfg.LLM_PROVIDER,
        )
        self._pass_signals[pass_id] = "running"

        # Launch background pass loop
        asyncio.create_task(
            self._run_loop(pass_id, novel_id, chapter_start, chapter_end)
        )
        return pass_id

    async def resume(self, pass_id: str) -> None:
        """Resume a paused pass (从 current_chapter + 1 续跑)。"""
        pass_row = await analysis_pass_store.get_pass(pass_id)
        if not pass_row:
            raise ValueError(f"Pass {pass_id} not found")
        if pass_row["status"] != "paused":
            raise ValueError(
                f"Pass {pass_id} is not paused (status={pass_row['status']})"
            )

        await analysis_pass_store.update_pass_status(pass_id, "running")
        self._pass_signals[pass_id] = "running"

        novel_id = pass_row["novel_id"]
        await self._broadcast(novel_id, pass_id, {
            "type": "pass_status", "status": "running",
        })

        # Only start a new loop if the old one has fully exited.
        # If the old loop is still running (finishing its current chapter after
        # pause was signalled), it will see the signal reset to "running" and
        # continue on its own — no new loop needed.
        if pass_id not in self._active_loops:
            resume_from = (pass_row["current_chapter"] or 0) + 1
            asyncio.create_task(
                self._run_loop(
                    pass_id, novel_id, resume_from, pass_row["chapter_end"],
                )
            )

    async def pause(self, pass_id: str) -> None:
        """Signal a running pass to pause after current chapter.

        Updates DB and broadcasts immediately so the UI responds instantly.
        The loop will finish the current chapter and then stop.
        """
        pass_row = await analysis_pass_store.get_pass(pass_id)
        if not pass_row:
            raise ValueError(f"Pass {pass_id} not found")
        self._pass_signals[pass_id] = "paused"
        # Immediate DB + broadcast so frontend updates without waiting for the loop
        await analysis_pass_store.update_pass_status(pass_id, "paused")
        await self._broadcast(pass_row["novel_id"], pass_id, {
            "type": "pass_status", "status": "paused",
        })

    async def cancel(self, pass_id: str) -> None:
        """Signal a running pass to cancel after current chapter.

        Updates DB and broadcasts immediately so the UI responds instantly.
        """
        pass_row = await analysis_pass_store.get_pass(pass_id)
        if not pass_row:
            raise ValueError(f"Pass {pass_id} not found")
        self._pass_signals[pass_id] = "cancelled"
        # Immediate DB + broadcast so frontend updates without waiting for the loop
        await analysis_pass_store.update_pass_status(pass_id, "cancelled")
        await self._broadcast(pass_row["novel_id"], pass_id, {
            "type": "pass_status", "status": "cancelled",
        })

    async def _run_loop(
        self,
        pass_id: str,
        novel_id: str,
        chapter_start: int,
        chapter_end: int,
    ) -> None:
        """Source pass loop. Runs as a background asyncio task."""
        self._active_loops.add(pass_id)
        try:
            await self._run_loop_inner(pass_id, novel_id, chapter_start, chapter_end)
        finally:
            self._active_loops.discard(pass_id)

    async def _run_loop_inner(
        self,
        pass_id: str,
        novel_id: str,
        chapter_start: int,
        chapter_end: int,
    ) -> None:
        """Inner source-pass loop body (骨架复用主循环,副作用全跳过)。"""
        pass_row = await analysis_pass_store.get_pass(pass_id)
        if not pass_row:
            logger.error("Pass %s not found, loop aborted", pass_id)
            return
        pass_config = pass_row.get("config_json") or {}
        model_override = pass_config.get("model_override")
        include_dictionary = pass_config.get("include_dictionary", True)

        total = chapter_end - chapter_start + 1
        stats = {"entities": 0, "relations": 0, "events": 0}
        _succeeded = 0
        _failed = 0

        # Story 5.2 成本分账:口径与一审一致 —— token 始终记录,费用仅云端
        # 模式计;定价按本 pass 实际模型(model_override 时与一审不同价)。
        from src.infra import config as _cfg
        _is_cloud = _cfg.LLM_PROVIDER == "openai"
        _pass_model = pass_row.get("model_name") or _cfg.get_model_name()
        if _is_cloud:
            _input_price, _output_price = get_pricing(_pass_model)
        else:
            _input_price, _output_price = 0.0, 0.0

        # D3: 二审模型(默认沿用当前配置;model_override 换模型视角)
        llm = self._llm or _build_pass_llm(model_override)
        extractor = ChapterFactExtractor(llm)
        # 二审没有 world_structures 可读(genre_hint 是一审产物),规则清洗用无 genre 的 validator
        validator = FactValidator()

        # 数字前缀名纠正(与一审同口径;只读 entity_dictionary,D1)
        try:
            _dict_entries = await entity_dictionary_store.get_all(novel_id)
            _corrections: dict[str, str] = {}
            _dict_names = {e.name for e in _dict_entries}
            _NUM_PREFIXES = frozenset("一二三四五六七八九十")
            for entry in _dict_entries:
                name = entry.name
                if (
                    len(name) >= 3
                    and name[0] in _NUM_PREFIXES
                    and entry.entity_type == "person"
                ):
                    short_form = name[1:]
                    if short_form not in _dict_names:
                        _corrections[short_form] = name
            if _corrections:
                validator.set_name_corrections(_corrections)
        except Exception as e:
            logger.warning("Source pass: failed to build name corrections: %s", e)

        # D2: per-run NameResolver — 从词典初始化、逐章自行累积、不回写词典
        name_resolver = NameResolver()
        try:
            _dict_entries_for_resolver = await entity_dictionary_store.get_all(novel_id)
            name_resolver.load_from_entity_dictionary(_dict_entries_for_resolver)
        except Exception as e:
            logger.warning(
                "Source pass: failed to load NameResolver from entity_dictionary: %s", e,
            )

        # 本 pass 已完成的章节(resume/重启续跑时跳过;章节状态来自影子表,
        # 与一审的 chapters.analysis_status 无关)
        completed_nums = await analysis_pass_store.get_completed_chapter_nums(pass_id)

        # Story 2.1: context 数据源参数化 — 只读本 pass 的影子表,
        # 机制上读不到 chapter_facts 主表与 world_structures
        async def _pass_facts_provider(_novel_id: str) -> list[dict]:
            return await analysis_pass_store.get_pass_chapter_facts(pass_id)

        # Broadcast initial state immediately so frontend shows total count
        await self._broadcast(novel_id, pass_id, {
            "type": "pass_progress",
            "chapter": chapter_start,
            "total": total,
            "done": 0,
            "stats": stats,
        })

        for chapter_num in range(chapter_start, chapter_end + 1):
            # Check for pause/cancel signal
            signal = self._pass_signals.get(pass_id, "running")
            if signal == "paused":
                # DB status and broadcast already handled by pause()
                logger.info(
                    "Pass %s loop stopping (paused) at chapter %d", pass_id, chapter_num,
                )
                return
            if signal == "cancelled":
                # DB status and broadcast already handled by cancel()
                logger.info(
                    "Pass %s loop stopping (cancelled) at chapter %d", pass_id, chapter_num,
                )
                self._pass_signals.pop(pass_id, None)
                return

            # Get chapter content
            chapter = await analysis_task_store.get_chapter_content(novel_id, chapter_num)
            if not chapter:
                logger.warning(
                    "Chapter %d not found for novel %s, skipping", chapter_num, novel_id,
                )
                await self._broadcast(novel_id, pass_id, {
                    "type": "pass_progress",
                    "chapter": chapter_num,
                    "total": total,
                    "done": chapter_num - chapter_start + 1,
                    "stats": stats,
                })
                continue

            # Skip excluded chapters (与一审一致:用户排除的章节不读)
            if chapter.get("is_excluded"):
                await analysis_pass_store.update_pass_progress(pass_id, chapter_num)
                await self._broadcast(novel_id, pass_id, {
                    "type": "pass_progress",
                    "chapter": chapter_num,
                    "total": total,
                    "done": chapter_num - chapter_start + 1,
                    "stats": stats,
                })
                continue

            # Skip chapters already completed in this pass (resume 续跑)
            if chapter_num in completed_nums:
                await analysis_pass_store.update_pass_progress(pass_id, chapter_num)
                await self._broadcast(novel_id, pass_id, {
                    "type": "pass_progress",
                    "chapter": chapter_num,
                    "total": total,
                    "done": chapter_num - chapter_start + 1,
                    "stats": stats,
                })
                continue

            # Broadcast "processing" before LLM call so UI shows current chapter
            await self._broadcast(novel_id, pass_id, {
                "type": "pass_processing",
                "chapter": chapter_num,
                "total": total,
            })

            start_ms = int(time.time() * 1000)

            try:
                # Build context: 原文 + 本 pass 前序 facts + (D1) 词典;
                # 世界结构注入走「空」分支(location_parents/tiers 不传)
                context = await self.context_builder.build(
                    novel_id, chapter_num,
                    facts_provider=_pass_facts_provider,
                    include_world_structure=False,
                    include_dictionary=include_dictionary,
                )

                # Extract facts (独立 system prompt + 同构 schema)
                fact, usage, meta = await extractor.extract_source_pass(
                    novel_id=novel_id,
                    chapter_id=chapter_num,
                    chapter_text=chapter["content"],
                    context_summary=context,
                )

                # 规则清洗(FactValidator 复用);幻觉判定层(三审职责)跳过
                fact = validator.validate(fact)

                # D2: 二审命名解析(独立累积,不回写词典)
                fact = name_resolver.resolve(fact)
                name_resolver.accumulate_from_chapter(fact)

                elapsed_ms = int(time.time() * 1000) - start_ms

                # Story 5.2 成本分账:每章 usage 落 history 埋点(token 始终记,
                # 费用仅云端模式);云端模式下二审费用记独立月度 key
                # (cost_pass_*),不混入一审合计。
                _ch_cost_usd = 0.0
                _ch_cost_cny = 0.0
                if _is_cloud:
                    _ch_cost_usd = round(
                        (usage.prompt_tokens / 1_000_000) * _input_price
                        + (usage.completion_tokens / 1_000_000) * _output_price,
                        6,
                    )
                    _ch_cost_cny = round(_ch_cost_usd * 7.2, 4)
                    await add_monthly_usage(
                        _ch_cost_usd, _ch_cost_cny,
                        usage.prompt_tokens, usage.completion_tokens,
                        scope=SCOPE_SOURCE_PASS,
                    )

                # 写影子表(与 chapter_facts 同构;主表零写入)
                await analysis_pass_store.upsert_pass_chapter_fact(
                    pass_id, chapter["id"], fact, status="completed",
                )
                completed_nums.add(chapter_num)
                _succeeded += 1

                # Story 1.2 history 埋点: source range + source-only 完成时间戳
                # + 每章 findings 计数;Story 5.2 增补 usage 成本分账
                await analysis_pass_store.update_chapter_history(
                    pass_id, chapter_num, {
                        "chapter_id": chapter["id"],
                        "char_start": 0,
                        "char_end": (
                            meta.truncated_len if meta.is_truncated
                            else meta.original_len
                        ),
                        "is_truncated": meta.is_truncated,
                        "completed_at": _utc_now_iso(),
                        "extraction_ms": elapsed_ms,
                        "findings": {
                            "characters": len(fact.characters),
                            "relationships": len(fact.relationships),
                            "locations": len(fact.locations),
                            "events": len(fact.events),
                        },
                        "usage": {
                            "input_tokens": usage.prompt_tokens,
                            "output_tokens": usage.completion_tokens,
                            "cost_usd": _ch_cost_usd,
                            "cost_cny": _ch_cost_cny,
                            "model": _pass_model,
                        },
                    },
                )

                # Update cumulative stats
                stats["entities"] += len(fact.characters) + len(fact.locations)
                stats["relations"] += len(fact.relationships)
                stats["events"] += len(fact.events)

                await self._broadcast(novel_id, pass_id, {
                    "type": "pass_chapter_done",
                    "chapter": chapter_num,
                    "status": "completed",
                })

            except Exception as e:
                # 一章失败不阻塞:影子表记 failed,继续下一章
                err_type, err_msg = _classify_error(e)
                logger.error(
                    "Source pass chapter %d failed [%s]: %s",
                    chapter_num, err_type, e,
                )
                _failed += 1
                try:
                    await analysis_pass_store.upsert_pass_chapter_fact(
                        pass_id, chapter["id"],
                        ChapterFact(chapter_id=chapter_num, novel_id=novel_id),
                        status="failed", error=err_msg,
                    )
                except Exception as store_err:
                    logger.warning(
                        "Source pass: failed to record chapter failure: %s", store_err,
                    )
                await analysis_pass_store.update_chapter_history(
                    pass_id, chapter_num, {
                        "chapter_id": chapter["id"],
                        "error": err_msg,
                        "error_type": err_type,
                    },
                )
                await self._broadcast(novel_id, pass_id, {
                    "type": "pass_chapter_done",
                    "chapter": chapter_num,
                    "status": "failed",
                    "error": err_msg,
                    "error_type": err_type,
                })

            # Update pass progress
            await analysis_pass_store.update_pass_progress(pass_id, chapter_num)

            # Broadcast overall progress
            await self._broadcast(novel_id, pass_id, {
                "type": "pass_progress",
                "chapter": chapter_num,
                "total": total,
                "done": chapter_num - chapter_start + 1,
                "stats": stats,
            })

        # All chapters processed — 全部失败记 failed,否则 completed
        # (逐章失败明细在 pass_chapter_facts.status / history 里可查)
        final_status = "failed" if (_failed > 0 and _succeeded == 0) else "completed"
        await analysis_pass_store.update_pass_status(pass_id, final_status)
        if final_status == "completed":
            # Story 1.2: source-only 完成时间戳(gate 语义;AIR unlock 由
            # Epic 3 diff 生成动作逐章回填 air_unlocked_at)
            await analysis_pass_store.update_pass_history(
                pass_id, source_only_completed_at=_utc_now_iso(),
            )
        await self._broadcast(novel_id, pass_id, {
            "type": "pass_status",
            "status": final_status,
            "stats": stats,
        })
        self._pass_signals.pop(pass_id, None)
        logger.info("Pass %s %s for novel %s", pass_id, final_status, novel_id)


# Module-level singleton (multi-pass Epic 3): API 路由跨请求共享同一实例,
# pause/resume/cancel 的内存信号(_pass_signals/_active_loops)必须落在
# 启动循环的那个实例上才生效。
_service: SourcePassService | None = None


def get_source_pass_service() -> SourcePassService:
    """Return module-level singleton SourcePassService."""
    global _service
    if _service is None:
        _service = SourcePassService()
    return _service
