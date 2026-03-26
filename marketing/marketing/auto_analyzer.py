"""自动分析 — 调用 AI Reader API 上传小说并执行分析"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from marketing.config import get_config
from marketing.db import (
    get_content_items_by_status,
    get_db,
    update_content_item,
)
from marketing.logger import get_logger

log = get_logger("analyzer")

_DEFAULT_TIMEOUT_MIN = 30


class AIReaderClient:
    """AI Reader REST API 客户端"""

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url.rstrip("/")

    async def upload_novel(self, txt_path: Path) -> int:
        """上传 TXT 文件，返回 novel_id"""
        async with httpx.AsyncClient(timeout=60) as client:
            with open(txt_path, "rb") as f:
                resp = await client.post(
                    f"{self.base_url}/api/novels/upload",
                    files={"file": (txt_path.name, f, "text/plain")},
                )
                resp.raise_for_status()

        data = resp.json()
        novel_id = data.get("id") or data.get("novel_id")
        if not novel_id:
            raise ValueError(f"上传响应缺少 novel_id: {data}")
        log.info("上传成功: %s → novel_id=%s", txt_path.name, novel_id)
        return int(novel_id)

    async def start_analysis(self, novel_id: int) -> dict[str, Any]:
        """触发分析任务"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/api/novels/{novel_id}/analyze",
            )
            resp.raise_for_status()

        data = resp.json()
        log.info("分析已启动: novel_id=%d", novel_id)
        return data

    async def get_analysis_status(self, novel_id: int) -> dict[str, Any]:
        """查询分析状态"""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.base_url}/api/novels/{novel_id}/analysis-status",
            )
            resp.raise_for_status()
        return resp.json()

    async def monitor_analysis(
        self,
        novel_id: int,
        timeout_min: int = _DEFAULT_TIMEOUT_MIN,
    ) -> dict[str, Any]:
        """通过 WebSocket 监控分析进度，返回最终状态"""
        import websockets  # type: ignore[import-untyped]

        ws_url = f"ws://{self.base_url.split('://')[-1]}/ws/analysis/{novel_id}"
        deadline = time.monotonic() + timeout_min * 60

        log.info("连接 WebSocket: %s (超时 %d 分钟)", ws_url, timeout_min)

        try:
            async with websockets.connect(ws_url) as ws:
                async for raw in ws:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"分析超时 ({timeout_min} 分钟)")

                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")

                    if msg_type == "progress":
                        d = msg.get("data", {})
                        current = d.get("current", 0)
                        total = d.get("total", 0)
                        pct = d.get("percentage", 0)
                        log.info("分析进度: %d/%d (%.1f%%)", current, total, pct)

                    elif msg_type == "chapter_done":
                        d = msg.get("data", {})
                        log.info(
                            "章节完成: #%s %s",
                            d.get("chapter_num", "?"),
                            d.get("title", ""),
                        )

                    elif msg_type == "task_status":
                        d = msg.get("data", {})
                        status = d.get("status", "")
                        if status in ("completed", "completed_with_errors"):
                            log.info("分析完成: status=%s", status)
                            return d
                        if status == "failed":
                            raise RuntimeError(f"分析失败: {d}")

        except ImportError:
            log.warning("websockets 未安装，使用轮询模式")
            return await self._poll_status(novel_id, timeout_min)

        raise RuntimeError("WebSocket 连接意外关闭")

    async def _poll_status(
        self,
        novel_id: int,
        timeout_min: int,
    ) -> dict[str, Any]:
        """降级轮询模式（当 websockets 不可用时）"""
        deadline = time.monotonic() + timeout_min * 60

        while time.monotonic() < deadline:
            status = await self.get_analysis_status(novel_id)
            state = status.get("status", "")

            if state in ("completed", "completed_with_errors"):
                log.info("分析完成: %s", state)
                return status
            if state == "failed":
                raise RuntimeError(f"分析失败: {status}")

            log.info("分析中... (状态: %s)", state)
            await asyncio.sleep(10)

        raise TimeoutError(f"分析超时 ({timeout_min} 分钟)")


# ── TXT 文件查找 ───────────────────────────────────────


def find_novel_txt(title: str) -> Path | None:
    """在配置的小说目录中查找匹配的 TXT 文件"""
    cfg = get_config()
    novels_dir = Path(cfg.get("novels_dir", "./novels"))

    if not novels_dir.exists():
        return None

    # 精确匹配
    exact = novels_dir / f"{title}.txt"
    if exact.exists():
        return exact

    # 模糊匹配
    for txt in novels_dir.glob("*.txt"):
        if title in txt.stem:
            return txt

    return None


# ── 主流程 ─────────────────────────────────────────────


async def analyze_one(
    content_id: int,
    title: str,
    ai_reader: AIReaderClient,
    timeout_min: int = _DEFAULT_TIMEOUT_MIN,
) -> bool:
    """分析单本小说，返回是否成功"""
    db = await get_db()

    # 查找 TXT 文件
    txt_path = find_novel_txt(title)
    if not txt_path:
        log.error("找不到《%s》的 TXT 文件", title)
        await update_content_item(
            db, content_id,
            status="analysis_failed",
            step_outputs={"analysis": {"error": f"TXT 文件未找到: {title}"}},
        )
        return False

    start = time.monotonic()

    try:
        # 上传
        novel_id = await ai_reader.upload_novel(txt_path)
        await update_content_item(
            db, content_id,
            step_outputs={"analysis": {"novel_id": novel_id}},
        )

        # 启动分析
        await ai_reader.start_analysis(novel_id)
        await update_content_item(db, content_id, status="analyzing")

        # 监控进度
        result = await ai_reader.monitor_analysis(novel_id, timeout_min)
        duration = round(time.monotonic() - start, 1)

        # 获取章节数
        chapters = result.get("total_chapters", result.get("total", 0))

        await update_content_item(
            db, content_id,
            status="analyzed",
            novel_id=novel_id,
            step_outputs={
                "analysis": {
                    "novel_id": novel_id,
                    "chapters": chapters,
                    "duration_seconds": duration,
                },
            },
        )
        log.info("《%s》分析完成: %d 章, %.0f 秒", title, chapters, duration)
        return True

    except Exception as e:
        duration = round(time.monotonic() - start, 1)
        log.error("《%s》分析失败: %s", title, e)
        await update_content_item(
            db, content_id,
            status="analysis_failed",
            step_outputs={
                "analysis": {
                    "error": str(e),
                    "duration_seconds": duration,
                },
            },
        )
        return False


async def run_analyzer(retry: bool = False) -> None:
    """分析入口：处理所有待分析的选题"""
    db = await get_db()
    cfg = get_config()

    base_url = cfg.get("ai_reader", {}).get("base_url", "http://localhost:8000")
    timeout = cfg.get("ai_reader", {}).get("analysis_timeout_min", _DEFAULT_TIMEOUT_MIN)
    ai_reader = AIReaderClient(base_url)

    # 获取待分析的内容
    if retry:
        items = await get_content_items_by_status(db, "analysis_failed")
        log.info("重试模式: %d 个失败的分析任务", len(items))
    else:
        items = await get_content_items_by_status(db, "selected")
        log.info("发现 %d 个待分析的选题", len(items))

    if not items:
        print("没有待分析的内容")
        return

    success = 0
    for item in items:
        ok = await analyze_one(
            item["id"], item["novel_title"], ai_reader, timeout,
        )
        if ok:
            success += 1

    print(f"\n分析完成: {success}/{len(items)} 成功")
