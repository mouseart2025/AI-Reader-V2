"""Pipeline 运行器 — 管理运行ID、输出目录、状态流转"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from marketing.config import get_output_dir
from marketing.db import (
    create_content_item,
    create_pipeline_run,
    get_db,
    update_content_item,
    update_pipeline_run,
)
from marketing.logger import get_logger

log = get_logger("pipeline")


def generate_run_id() -> str:
    """生成唯一运行 ID"""
    return uuid.uuid4().hex[:12]


def create_run_dir(run_id: str) -> Path:
    """创建按日期+运行ID组织的输出目录"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_dir = get_output_dir() / today / f"run-{run_id}"
    for subdir in ("screenshots", "content", "data"):
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)
    log.info("输出目录: %s", run_dir)
    return run_dir


class PipelineContext:
    """Pipeline 运行上下文，管理单次运行的状态"""

    def __init__(self, db: aiosqlite.Connection, run_id: str, run_dir: Path) -> None:
        self.db = db
        self.run_id = run_id
        self.run_dir = run_dir
        self._step_timings: dict[str, float] = {}

    async def record_step(self, step: str, duration_seconds: float) -> None:
        """记录步骤耗时"""
        self._step_timings[step] = round(duration_seconds, 1)
        import json
        await update_pipeline_run(
            self.db, self.run_id,
            step_timings=json.dumps(self._step_timings),
        )

    async def fail(self, error: str) -> None:
        """标记 Pipeline 失败"""
        await update_pipeline_run(
            self.db, self.run_id,
            status="failed", error=error,
        )
        log.error("Pipeline %s 失败: %s", self.run_id, error)

    async def complete(self) -> None:
        """标记 Pipeline 完成"""
        await update_pipeline_run(self.db, self.run_id, status="completed")
        log.info("Pipeline %s 完成", self.run_id)


async def start_pipeline(novel_title: str | None = None) -> PipelineContext:
    """启动一次 Pipeline 运行"""
    run_id = generate_run_id()
    run_dir = create_run_dir(run_id)
    db = await get_db()

    await create_pipeline_run(
        db, run_id,
        novel_title=novel_title,
        output_dir=str(run_dir),
    )
    await update_pipeline_run(db, run_id, status="running")

    log.info("Pipeline 启动: %s (小说: %s)", run_id, novel_title or "待选题")
    return PipelineContext(db, run_id, run_dir)


async def run_full_pipeline(novel: str | None = None) -> None:
    """一键运行完整 Pipeline（占位，后续 Story 实现各步骤）"""
    ctx = await start_pipeline(novel_title=novel)
    log.info("完整 Pipeline 运行 — 各步骤将在后续 Story 中实现")
    log.info("运行 ID: %s", ctx.run_id)
    log.info("输出目录: %s", ctx.run_dir)
    await ctx.complete()
