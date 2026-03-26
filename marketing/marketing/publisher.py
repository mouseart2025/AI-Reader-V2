"""发布器 — 多平台内容发布、审批工作流"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from marketing.config import get_config, get_platform_config
from marketing.db import (
    get_content_item,
    get_content_items_by_status,
    get_db,
    update_content_item,
)
from marketing.logger import get_logger
from marketing.publishers.base import BasePublisher, ContentPayload, PublishResult

log = get_logger("publisher")

# ── Publisher 注册 ─────────────────────────────────────

_PUBLISHERS: dict[str, type[BasePublisher]] = {}


def _register_publishers() -> None:
    """注册所有可用的发布模块"""
    from marketing.publishers.juejin import JuejinPublisher
    from marketing.publishers.twitter import TwitterPublisher

    _PUBLISHERS["juejin"] = JuejinPublisher
    _PUBLISHERS["twitter"] = TwitterPublisher


def get_publisher(platform: str) -> BasePublisher:
    """获取指定平台的发布器实例"""
    if not _PUBLISHERS:
        _register_publishers()

    cls = _PUBLISHERS.get(platform)
    if not cls:
        raise ValueError(f"不支持的平台: {platform} (可选: {list(_PUBLISHERS.keys())})")

    try:
        cfg = get_platform_config(platform)
    except (KeyError, FileNotFoundError):
        cfg = {}

    return cls(cfg)


# ── 发布流程 ───────────────────────────────────────────


def _build_payload(item: dict[str, Any], platform: str) -> ContentPayload:
    """从 content_item 构建发布内容"""
    step_outputs = json.loads(item.get("step_outputs", "{}"))

    # 获取文案内容
    content_data = step_outputs.get("content", {})
    articles = content_data.get("articles", [])

    # 优先匹配平台
    article = None
    for a in articles:
        if a.get("platform") == platform:
            article = a
            break
    if not article and articles:
        article = articles[0]

    title = article.get("title", item["novel_title"]) if article else item["novel_title"]
    tags = article.get("tags", []) if article else []

    # 读取文案正文
    body = ""
    if article and article.get("file"):
        try:
            from pathlib import Path
            body = Path(article["file"]).read_text(encoding="utf-8")
        except Exception:
            pass

    # 截图路径
    screenshots = step_outputs.get("screenshots", {})
    images = [f["path"] for f in screenshots.get("files", []) if "path" in f]

    return ContentPayload(
        content_id=item["id"],
        title=title,
        body=body,
        tags=tags,
        images=images,
        platform=platform,
        novel_title=item["novel_title"],
    )


async def publish_one(
    content_id: int,
    platform: str,
    dry_run: bool = False,
) -> bool:
    """发布单条内容"""
    db = await get_db()
    item = await get_content_item(db, content_id)

    if not item:
        log.error("content_id=%d 不存在", content_id)
        return False

    # 审批检查
    if item["status"] != "approved" and not dry_run:
        print(f"⚠️  内容 #{content_id} 状态为 '{item['status']}'，需要先审批")
        print(f"   运行: python -m marketing review approve {content_id}")
        return False

    publisher = get_publisher(platform)
    payload = _build_payload(item, platform)

    if dry_run:
        preview = await publisher.dry_run(payload)
        print(f"\n{preview}")
        return True

    # 验证凭证
    valid = await publisher.validate_credentials()
    if not valid:
        log.error("%s 凭证验证失败", platform)
        return False

    # 发布
    result = await publisher.publish_with_retry(payload)

    if result.success:
        await update_content_item(
            db, content_id,
            status="published",
            publish_url=result.publish_url,
            published_at=datetime.now(timezone.utc).isoformat(),
            step_outputs={
                "publish": {
                    "platform": platform,
                    "url": result.publish_url,
                    "article_id": result.article_id,
                },
            },
        )
        print(f"✅ 发布成功: {result.publish_url}")
        return True
    else:
        log.error("发布失败: %s", result.error)
        return False


# ── 审批工作流 (M2.2) ─────────────────────────────────


async def show_review_list() -> list[dict[str, Any]]:
    """显示待审批内容列表"""
    db = await get_db()
    items = await get_content_items_by_status(db, "content_ready")

    if not items:
        print("\n✅ 没有待审批的内容")
        return []

    print(f"\n📋 待审批内容 ({len(items)} 篇)")
    print(f"{'ID':>4}  {'小说':<16}  {'平台':<12}  {'角度':<10}  {'创建时间':<12}")
    print("-" * 60)

    for item in items:
        print(
            f"{item['id']:>4}  {item['novel_title']:<16s}  "
            f"{(item.get('platform') or '-'):<12s}  "
            f"{(item.get('narrative_angle') or '-'):<10s}  "
            f"{item['created_at'][:10]:<12s}"
        )

    print(f"\n操作: approve <ID> | reject <ID> | edit <ID> | approve all")
    return items


async def approve_content(content_id: int) -> bool:
    """审批通过"""
    db = await get_db()
    item = await get_content_item(db, content_id)
    if not item:
        print(f"⚠️  内容 #{content_id} 不存在")
        return False
    if item["status"] != "content_ready":
        print(f"⚠️  内容 #{content_id} 状态为 '{item['status']}'，无需审批")
        return False

    await update_content_item(db, content_id, status="approved")
    print(f"✅ #{content_id} 《{item['novel_title']}》已批准")
    return True


async def reject_content(content_id: int) -> bool:
    """审批拒绝"""
    db = await get_db()
    item = await get_content_item(db, content_id)
    if not item:
        print(f"⚠️  内容 #{content_id} 不存在")
        return False

    await update_content_item(db, content_id, status="rejected")
    print(f"❌ #{content_id} 《{item['novel_title']}》已拒绝")
    return True


async def edit_content(content_id: int) -> bool:
    """编辑文案后标记为已审批"""
    db = await get_db()
    item = await get_content_item(db, content_id)
    if not item:
        print(f"⚠️  内容 #{content_id} 不存在")
        return False

    step_outputs = json.loads(item.get("step_outputs", "{}"))
    articles = step_outputs.get("content", {}).get("articles", [])

    if not articles or not articles[0].get("file"):
        print(f"⚠️  内容 #{content_id} 没有关联的文案文件")
        return False

    filepath = articles[0]["file"]
    editor = os.environ.get("EDITOR", "vim")

    print(f"📝 打开编辑器: {editor} {filepath}")
    subprocess.run([editor, filepath])

    await update_content_item(db, content_id, status="approved")
    print(f"✅ #{content_id} 编辑完成并已批准")
    return True


async def approve_all() -> int:
    """批量审批通过所有待审批内容"""
    db = await get_db()
    items = await get_content_items_by_status(db, "content_ready")
    count = 0
    for item in items:
        await update_content_item(db, item["id"], status="approved")
        count += 1
    print(f"✅ 已批量审批 {count} 篇内容")
    return count


# ── CLI 入口 ───────────────────────────────────────────


async def run_publisher(
    platform: str | None = None,
    content_id: int | None = None,
    dry_run: bool = False,
    cron: bool = False,
) -> None:
    """发布入口"""
    if content_id and platform:
        await publish_one(content_id, platform, dry_run)
        return

    if dry_run and platform:
        # dry-run 验证凭证
        publisher = get_publisher(platform)
        valid = await publisher.validate_credentials()
        print(f"{'✅' if valid else '❌'} {platform} 凭证{'有效' if valid else '无效'}")
        return

    # 发布所有已审批内容
    db = await get_db()
    items = await get_content_items_by_status(db, "approved")
    if not items:
        print("没有待发布的内容")
        return

    target_platform = platform or "xiaohongshu"
    success = 0
    for item in items:
        ok = await publish_one(item["id"], target_platform, dry_run)
        if ok:
            success += 1

    print(f"\n发布完成: {success}/{len(items)} 成功")


async def run_review(
    action: str | None = None,
    content_id: int | None = None,
) -> None:
    """审批入口"""
    if action is None:
        await show_review_list()
        return

    if action == "approve" and content_id:
        await approve_content(content_id)
    elif action == "approve" and content_id is None:
        await approve_all()
    elif action == "reject" and content_id:
        await reject_content(content_id)
    elif action == "edit" and content_id:
        await edit_content(content_id)
    else:
        print("用法: review approve|reject|edit <ID>")
