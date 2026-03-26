"""发布排期引擎 — 频率控制与定时发布"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from marketing.config import get_config, get_platform_config
from marketing.db import get_db
from marketing.logger import get_logger

log = get_logger("scheduler")


async def count_today_published(
    db: aiosqlite.Connection,
    platform: str,
) -> int:
    """统计今日已发布数量"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM content_items "
        "WHERE platform LIKE ? AND status = 'published' AND published_at LIKE ?",
        (f"%{platform}%", f"{today}%"),
    )
    return rows[0][0] if rows else 0


async def get_last_publish_time(
    db: aiosqlite.Connection,
    platform: str,
) -> datetime | None:
    """获取该平台最后一次发布时间"""
    rows = await db.execute_fetchall(
        "SELECT published_at FROM content_items "
        "WHERE platform LIKE ? AND status = 'published' "
        "ORDER BY published_at DESC LIMIT 1",
        (f"%{platform}%",),
    )
    if rows and rows[0][0]:
        return datetime.fromisoformat(rows[0][0])
    return None


async def next_available_slot(platform: str) -> datetime:
    """基于平台限制计算下一个可用发布时间"""
    db = await get_db()
    cfg = get_config()

    platform_cfg = cfg.get("platforms", {}).get(platform, {})
    schedule = cfg.get("schedule", {})

    max_daily = platform_cfg.get("max_daily", schedule.get("max_daily_per_platform", 2))
    min_interval = platform_cfg.get(
        "min_interval_hours",
        schedule.get("min_interval_hours", 4),
    )

    now = datetime.now(timezone.utc)

    # 检查今日发布数量
    today_count = await count_today_published(db, platform)
    if today_count >= max_daily:
        # 排到明天第一个发布时间
        tomorrow = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
        log.info(
            "%s 今日已达上限 (%d/%d)，排到明天 %s",
            platform, today_count, max_daily, tomorrow.isoformat(),
        )
        return tomorrow

    # 检查最小间隔
    last_time = await get_last_publish_time(db, platform)
    if last_time:
        min_next = last_time + timedelta(hours=min_interval)
        if min_next > now:
            log.info(
                "%s 需等待间隔，下次可发布: %s",
                platform, min_next.isoformat(),
            )
            return min_next

    return now


async def get_due_items(platform: str | None = None) -> list[dict[str, Any]]:
    """获取当前时间可发布的已审批内容"""
    db = await get_db()

    from marketing.db import get_content_items_by_status
    items = await get_content_items_by_status(db, "approved")

    if not items:
        return []

    now = datetime.now(timezone.utc)
    due: list[dict[str, Any]] = []

    for item in items:
        item_platform = item.get("platform", "").split(",")[0] or "xiaohongshu"
        if platform and item_platform != platform:
            continue

        slot = await next_available_slot(item_platform)
        if slot <= now:
            due.append(item)

    return due


def is_manual_platform(platform: str) -> bool:
    """检查平台是否配置为仅人工发布"""
    cfg = get_config()
    platform_cfg = cfg.get("platforms", {}).get(platform, {})
    return platform_cfg.get("publish_mode", "auto") == "manual"


async def prepare_manual_publish(
    content_id: int,
    platform: str,
) -> str | None:
    """为人工发布平台准备素材目录"""
    from pathlib import Path
    import json
    import shutil

    db = await get_db()
    from marketing.db import get_content_item
    item = await get_content_item(db, content_id)
    if not item:
        return None

    cfg = get_config()
    output_base = Path(cfg.get("output", {}).get("dir", "./output"))
    manual_dir = output_base / "manual_publish" / platform / f"content-{content_id}"
    manual_dir.mkdir(parents=True, exist_ok=True)

    step_outputs = json.loads(item.get("step_outputs", "{}"))

    # 复制文案文件
    articles = step_outputs.get("content", {}).get("articles", [])
    for article in articles:
        src = article.get("file")
        if src and Path(src).exists():
            shutil.copy2(src, manual_dir)

    # 复制截图
    screenshots = step_outputs.get("screenshots", {})
    for f in screenshots.get("files", []):
        src = f.get("path")
        if src and Path(src).exists():
            shutil.copy2(src, manual_dir)

    # 发布说明
    instructions = (
        f"# 发布说明\n\n"
        f"- 小说: {item['novel_title']}\n"
        f"- 平台: {platform}\n"
        f"- 角度: {item.get('narrative_angle', '-')}\n"
        f"- 内容 ID: {content_id}\n\n"
        f"## 步骤\n"
        f"1. 打开 {platform} 创作者后台\n"
        f"2. 将文案内容粘贴到编辑器\n"
        f"3. 上传截图作为配图\n"
        f"4. 发布后运行:\n"
        f"   python -m marketing publish --content-id {content_id} "
        f"--platform {platform}\n"
        f"   以记录发布 URL\n"
    )
    (manual_dir / "发布说明.md").write_text(instructions, encoding="utf-8")

    from marketing.db import update_content_item
    await update_content_item(db, content_id, status="manual_publish_pending")

    log.info("人工发布素材已准备: %s", manual_dir)
    return str(manual_dir)
