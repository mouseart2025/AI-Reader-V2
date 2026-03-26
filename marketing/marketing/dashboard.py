"""仪表盘 — 数据采集、效果分析、爆款预警、运行日志"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from marketing.config import get_config
from marketing.db import get_db
from marketing.logger import get_logger

log = get_logger("dashboard")


# ── M3.1 数据采集 ─────────────────────────────────────


async def collect_metrics() -> int:
    """采集所有已发布内容的互动数据"""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT id, novel_title, platform, publish_url, step_outputs "
        "FROM content_items WHERE status = 'published' AND publish_url IS NOT NULL",
    )

    if not rows:
        print("没有已发布的内容")
        return 0

    collected = 0
    for row in rows:
        content_id = row[0]
        platform = (row[2] or "").split(",")[0]
        publish_url = row[3]
        step_outputs = json.loads(row[4] or "{}")

        article_id = step_outputs.get("publish", {}).get("article_id", "")

        try:
            metrics = await _fetch_platform_metrics(platform, publish_url, article_id)
            if metrics:
                now = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    "INSERT INTO metrics "
                    "(content_id, platform, collected_at, views, likes, comments, shares, bookmarks, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        content_id,
                        platform,
                        now,
                        metrics.get("views", 0),
                        metrics.get("likes", 0),
                        metrics.get("comments", 0),
                        metrics.get("shares", 0),
                        metrics.get("bookmarks", 0),
                        json.dumps(metrics),
                    ),
                )
                await db.commit()
                collected += 1
                log.info(
                    "采集 #%d [%s]: views=%d likes=%d comments=%d",
                    content_id, platform,
                    metrics.get("views", 0),
                    metrics.get("likes", 0),
                    metrics.get("comments", 0),
                )
        except Exception as e:
            log.warning("采集 #%d [%s] 失败: %s", content_id, platform, e)

    print(f"采集完成: {collected}/{len(rows)} 条")

    # 采集后检查爆款
    await check_viral_alerts()

    return collected


async def _fetch_platform_metrics(
    platform: str,
    publish_url: str,
    article_id: str,
) -> dict[str, int] | None:
    """从平台 API 获取互动数据"""
    if platform == "juejin" and article_id:
        return await _fetch_juejin_metrics(article_id)
    if platform == "twitter" and article_id:
        return await _fetch_twitter_metrics(article_id)
    log.debug("平台 %s 暂不支持自动采集", platform)
    return None


async def _fetch_juejin_metrics(article_id: str) -> dict[str, int] | None:
    """掘金文章数据"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.juejin.cn/content_api/v1/article/detail",
                json={"article_id": article_id},
            )
            data = resp.json()
            if data.get("err_no") == 0:
                info = data.get("data", {}).get("article_info", {})
                return {
                    "views": info.get("view_count", 0),
                    "likes": info.get("digg_count", 0),
                    "comments": info.get("comment_count", 0),
                    "bookmarks": info.get("collect_count", 0),
                    "shares": 0,
                }
    except Exception as e:
        log.warning("掘金数据采集失败: %s", e)
    return None


async def _fetch_twitter_metrics(tweet_id: str) -> dict[str, int] | None:
    """Twitter 推文数据"""
    cfg = get_config()
    bearer = cfg.get("platforms", {}).get("twitter", {}).get("bearer_token", "")
    if not bearer:
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.twitter.com/2/tweets/{tweet_id}",
                headers={"Authorization": f"Bearer {bearer}"},
                params={"tweet.fields": "public_metrics"},
            )
            if resp.status_code == 200:
                metrics = resp.json().get("data", {}).get("public_metrics", {})
                return {
                    "views": metrics.get("impression_count", 0),
                    "likes": metrics.get("like_count", 0),
                    "comments": metrics.get("reply_count", 0),
                    "shares": metrics.get("retweet_count", 0),
                    "bookmarks": metrics.get("bookmark_count", 0),
                }
    except Exception as e:
        log.warning("Twitter 数据采集失败: %s", e)
    return None


# ── M3.3 爆款预警 ─────────────────────────────────────


async def check_viral_alerts() -> list[dict[str, Any]]:
    """检测爆款内容（互动量 > 平台均值 × 3）"""
    db = await get_db()

    # 各平台平均互动量
    avg_rows = await db.execute_fetchall(
        "SELECT platform, AVG(likes + comments + shares) as avg_interaction "
        "FROM metrics GROUP BY platform",
    )
    platform_avg: dict[str, float] = {}
    for row in avg_rows:
        platform_avg[row[0]] = row[1] or 0

    # 最新采集的数据
    latest_rows = await db.execute_fetchall(
        "SELECT m.content_id, m.platform, m.likes, m.comments, m.shares, "
        "c.novel_title "
        "FROM metrics m "
        "JOIN content_items c ON c.id = m.content_id "
        "WHERE m.collected_at = ("
        "  SELECT MAX(collected_at) FROM metrics WHERE content_id = m.content_id"
        ")",
    )

    alerts: list[dict[str, Any]] = []
    for row in latest_rows:
        content_id, platform, likes, comments, shares, title = row
        interactions = (likes or 0) + (comments or 0) + (shares or 0)
        avg = platform_avg.get(platform, 0)

        if avg > 0 and interactions > avg * 3:
            multiplier = round(interactions / avg, 1)
            alert_data = {
                "multiplier": multiplier,
                "platform": platform,
                "interactions": interactions,
                "avg": round(avg, 1),
                "title": title,
            }

            # 检查是否已有此预警
            existing = await db.execute_fetchall(
                "SELECT 1 FROM alerts WHERE content_id = ? AND alert_type = 'viral' "
                "AND acknowledged = 0",
                (content_id,),
            )
            if not existing:
                now = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    "INSERT INTO alerts (content_id, alert_type, data_json, created_at) "
                    "VALUES (?, 'viral', ?, ?)",
                    (content_id, json.dumps(alert_data), now),
                )
                await db.commit()

                print(
                    f"🔥 爆款预警: 《{title}》[{platform}] "
                    f"互动量 {interactions} = {multiplier}x 平均值"
                )
                alerts.append(alert_data)

    return alerts


# ── M3.2 效果汇总 ─────────────────────────────────────


async def show_summary() -> None:
    """多维效果汇总"""
    db = await get_db()

    # 按平台汇总
    print("\n📊 按平台汇总")
    print(f"{'平台':<12}  {'发布数':>6}  {'总互动':>8}  {'平均互动':>8}")
    print("-" * 40)

    rows = await db.execute_fetchall(
        "SELECT c.platform, COUNT(DISTINCT c.id), "
        "SUM(COALESCE(m.likes,0) + COALESCE(m.comments,0) + COALESCE(m.shares,0)), "
        "AVG(COALESCE(m.likes,0) + COALESCE(m.comments,0) + COALESCE(m.shares,0)) "
        "FROM content_items c "
        "LEFT JOIN metrics m ON m.content_id = c.id "
        "AND m.collected_at = (SELECT MAX(collected_at) FROM metrics WHERE content_id = c.id) "
        "WHERE c.status = 'published' "
        "GROUP BY c.platform",
    )
    for row in rows:
        platform = row[0] or "-"
        count = row[1] or 0
        total = int(row[2] or 0)
        avg = round(row[3] or 0, 1)
        print(f"{platform:<12s}  {count:>6}  {total:>8}  {avg:>8.1f}")

    # 按叙事角度汇总
    print("\n📊 按叙事角度汇总")
    print(f"{'角度':<12}  {'发布数':>6}  {'平均互动':>8}")
    print("-" * 30)

    rows = await db.execute_fetchall(
        "SELECT c.narrative_angle, COUNT(DISTINCT c.id), "
        "AVG(COALESCE(m.likes,0) + COALESCE(m.comments,0) + COALESCE(m.shares,0)) "
        "FROM content_items c "
        "LEFT JOIN metrics m ON m.content_id = c.id "
        "AND m.collected_at = (SELECT MAX(collected_at) FROM metrics WHERE content_id = c.id) "
        "WHERE c.status = 'published' "
        "GROUP BY c.narrative_angle",
    )
    for row in rows:
        angle = row[0] or "-"
        count = row[1] or 0
        avg = round(row[2] or 0, 1)
        print(f"{angle:<12s}  {count:>6}  {avg:>8.1f}")


async def show_conversions() -> None:
    """转化漏斗展示"""
    db = await get_db()
    cfg = get_config()

    # 总曝光
    views_row = await db.execute_fetchall(
        "SELECT SUM(views) FROM metrics m "
        "JOIN content_items c ON c.id = m.content_id "
        "WHERE m.collected_at = (SELECT MAX(collected_at) FROM metrics WHERE content_id = m.content_id)",
    )
    total_views = int(views_row[0][0] or 0) if views_row else 0

    # GitHub Stars
    github_stars = 0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.github.com/repos/mouseart2025/AI-Reader-V2",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code == 200:
                github_stars = resp.json().get("stargazers_count", 0)
    except Exception:
        pass

    # 下载量
    downloads = 0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.github.com/repos/mouseart2025/AI-Reader-V2/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code == 200:
                for asset in resp.json().get("assets", []):
                    downloads += asset.get("download_count", 0)
    except Exception:
        pass

    print("\n📊 转化漏斗")
    print("=" * 40)
    print(f"  内容曝光:    {total_views:>8,}")
    print(f"  GitHub Stars: {github_stars:>8,}")
    print(f"  桌面下载:    {downloads:>8,}")
    print("=" * 40)


# ── M3.3 运行日志 ─────────────────────────────────────


async def show_logs(detail: str | None = None) -> None:
    """展示 Pipeline 运行日志"""
    db = await get_db()

    if detail:
        # 单次运行详情
        rows = await db.execute_fetchall(
            "SELECT * FROM pipeline_runs WHERE id = ?", (detail,)
        )
        if not rows:
            print(f"运行 {detail} 不存在")
            return

        run = dict(rows[0])
        print(f"\n📋 运行详情: {run['id']}")
        print(f"  创建时间: {run['created_at']}")
        print(f"  小说: {run.get('novel_title', '-')}")
        print(f"  状态: {run['status']}")
        print(f"  输出目录: {run.get('output_dir', '-')}")

        timings = json.loads(run.get("step_timings", "{}") or "{}")
        if timings:
            print("\n  步骤耗时:")
            for step, seconds in timings.items():
                print(f"    {step}: {seconds}s")

        if run.get("error"):
            print(f"\n  ❌ 错误: {run['error']}")
        return

    # 最近 20 次运行
    rows = await db.execute_fetchall(
        "SELECT id, created_at, novel_title, status, step_timings, error "
        "FROM pipeline_runs ORDER BY created_at DESC LIMIT 20",
    )

    if not rows:
        print("没有运行记录")
        return

    print(f"\n📋 Pipeline 运行日志 (最近 {len(rows)} 次)")
    print(f"{'ID':<14}  {'时间':<20}  {'小说':<16}  {'状态':<12}  {'错误'}")
    print("-" * 75)

    for row in rows:
        run_id = row[0]
        created = row[1][:19] if row[1] else ""
        title = row[2] or "-"
        status = row[3]
        error = (row[5] or "")[:30]

        status_icon = {"completed": "✅", "failed": "❌", "running": "🔄"}.get(
            status, "⏳"
        )
        print(f"{run_id:<14s}  {created:<20s}  {title:<16s}  {status_icon} {status:<8s}  {error}")

    print(f"\n详情: python -m marketing dashboard --detail <RUN_ID>")


async def show_alerts() -> None:
    """显示未确认的预警"""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT a.id, a.content_id, a.alert_type, a.data_json, a.created_at, "
        "c.novel_title "
        "FROM alerts a "
        "LEFT JOIN content_items c ON c.id = a.content_id "
        "WHERE a.acknowledged = 0 "
        "ORDER BY a.created_at DESC",
    )

    if not rows:
        print("没有未确认的预警")
        return

    print(f"\n🔔 未确认预警 ({len(rows)} 条)")
    for row in rows:
        alert_id = row[0]
        data = json.loads(row[3] or "{}")
        title = row[5] or "-"
        created = row[4][:19] if row[4] else ""

        if row[2] == "viral":
            print(
                f"  #{alert_id} 🔥 爆款: 《{title}》[{data.get('platform')}] "
                f"{data.get('interactions', 0)} 互动 ({data.get('multiplier', 0)}x) "
                f"— {created}"
            )
        else:
            print(f"  #{alert_id} {row[2]}: {title} — {created}")


# ── CLI 入口 ───────────────────────────────────────────


async def run_dashboard(
    collect: bool = False,
    summary: bool = False,
    conversions: bool = False,
    logs: bool = False,
    alerts: bool = False,
    detail: str | None = None,
) -> None:
    """仪表盘主入口"""
    if collect:
        await collect_metrics()
    elif summary:
        await show_summary()
    elif conversions:
        await show_conversions()
    elif logs or detail:
        await show_logs(detail)
    elif alerts:
        await show_alerts()
    else:
        # 默认展示汇总
        await show_summary()
        await show_alerts()
