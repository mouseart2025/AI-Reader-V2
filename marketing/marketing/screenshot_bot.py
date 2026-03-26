"""截图机器人 — Playwright 自动截取 AI Reader 分析结果页面"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marketing.config import get_config
from marketing.db import (
    get_content_items_by_status,
    get_db,
    update_content_item,
)
from marketing.logger import get_logger

log = get_logger("screenshot")


@dataclass
class ScreenshotSpec:
    """单张截图规格"""
    name: str
    width: int
    height: int
    scale: float = 2.0


# 双尺寸: 竖版 3:4 + 横版 16:9
_SIZES = [
    ScreenshotSpec("vertical", 1080, 1440),
    ScreenshotSpec("horizontal", 1920, 1080),
]

_DEFAULT_PAGES = {
    "reading": {
        "path": "/read/{novel_id}",
        "wait_selector": ".prose",
        "clip_selector": None,
    },
    "graph": {
        "path": "/graph/{novel_id}",
        "wait_selector": "canvas",
        "clip_selector": None,
    },
    "map": {
        "path": "/map/{novel_id}",
        "wait_selector": "svg",
        "clip_selector": None,
    },
    "timeline": {
        "path": "/timeline/{novel_id}",
        "wait_selector": "svg",
        "clip_selector": None,
    },
    "encyclopedia": {
        "path": "/encyclopedia/{novel_id}",
        "wait_selector": ".space-y-6",
        "clip_selector": None,
    },
    "factions": {
        "path": "/factions/{novel_id}",
        "wait_selector": "canvas",
        "clip_selector": None,
    },
}


def _get_screenshot_config() -> dict[str, Any]:
    """获取截图配置，合并默认值"""
    cfg = get_config()
    ss_cfg = cfg.get("screenshots", {})
    return {
        "frontend_url": ss_cfg.get("frontend_url", "http://localhost:5173"),
        "wait_timeout": ss_cfg.get("wait_timeout", 15000),
        "stable_delay": ss_cfg.get("stable_delay", 3000),
        "pages": {**_DEFAULT_PAGES, **ss_cfg.get("pages", {})},
    }


async def take_screenshots(
    novel_id: int,
    novel_title: str,
    output_dir: Path,
) -> list[dict[str, str]]:
    """对一部小说截取所有页面的所有尺寸，返回截图路径列表"""
    from playwright.async_api import async_playwright

    ss_cfg = _get_screenshot_config()
    frontend_url = ss_cfg["frontend_url"]
    wait_timeout = ss_cfg["wait_timeout"]
    stable_delay = ss_cfg["stable_delay"]
    pages_cfg = ss_cfg["pages"]

    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, str]] = []

    async with async_playwright() as p:
        for page_type, page_cfg in pages_cfg.items():
            url_path = page_cfg["path"].replace("{novel_id}", str(novel_id))
            full_url = f"{frontend_url}{url_path}"
            wait_sel = page_cfg.get("wait_selector", "body")

            for spec in _SIZES:
                filename = f"{novel_title}_{page_type}_{spec.name}.png"
                filepath = screenshots_dir / filename

                try:
                    start = time.monotonic()

                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context(
                        viewport={"width": spec.width, "height": spec.height},
                        device_scale_factor=spec.scale,
                    )
                    page = await context.new_page()

                    await page.goto(full_url, wait_until="networkidle", timeout=wait_timeout)

                    # 等待关键元素出现
                    try:
                        await page.wait_for_selector(wait_sel, timeout=wait_timeout)
                    except Exception:
                        log.warning(
                            "%s: selector '%s' 未出现，使用当前页面状态",
                            page_type, wait_sel,
                        )

                    # 等待布局稳定（图谱/地图需要渲染时间）
                    delay = stable_delay
                    if page_type == "map":
                        delay = 10000  # 地图 rough.js 手绘渲染需要更长时间
                    elif page_type in ("graph", "factions"):
                        delay = 5000  # 力导向图布局稳定
                    await page.wait_for_timeout(delay)

                    # 地图页面：隐藏侧边栏+工具栏，让地图充满画面
                    if page_type == "map":
                        await page.evaluate("""() => {
                            // 隐藏侧边栏
                            document.querySelectorAll('aside, [class*="sidebar"], [class*="Sidebar"], [class*="panel"], [class*="Panel"]').forEach(el => el.style.display = 'none');
                            // 隐藏顶部导航栏
                            document.querySelectorAll('nav, header, [class*="navbar"], [class*="Navbar"], [class*="header"], [class*="Header"]').forEach(el => el.style.display = 'none');
                            // 隐藏工具栏按钮
                            document.querySelectorAll('[class*="toolbar"], [class*="Toolbar"], [class*="controls"], [class*="Controls"]').forEach(el => el.style.display = 'none');
                            // 让地图容器充满
                            document.querySelectorAll('[class*="map"], [class*="Map"], main').forEach(el => {
                                el.style.width = '100vw';
                                el.style.height = '100vh';
                                el.style.position = 'fixed';
                                el.style.top = '0';
                                el.style.left = '0';
                            });
                        }""")
                        await page.wait_for_timeout(1000)

                    # 截图
                    clip_sel = page_cfg.get("clip_selector")
                    if clip_sel:
                        element = await page.query_selector(clip_sel)
                        if element:
                            await element.screenshot(path=str(filepath))
                        else:
                            await page.screenshot(path=str(filepath), full_page=False)
                    else:
                        await page.screenshot(path=str(filepath), full_page=False)

                    elapsed = round((time.monotonic() - start) * 1000)
                    log.info(
                        "截图完成: %s (%dx%d, %dms)",
                        filename, spec.width, spec.height, elapsed,
                    )

                    results.append({
                        "page": page_type,
                        "size": spec.name,
                        "path": str(filepath),
                        "width": spec.width,
                        "height": spec.height,
                    })

                    await browser.close()

                except Exception as e:
                    log.error("截图失败 %s/%s: %s", page_type, spec.name, e)
                    results.append({
                        "page": page_type,
                        "size": spec.name,
                        "error": str(e),
                    })

    return results


async def screenshot_one(content_id: int, item: dict[str, Any]) -> bool:
    """为单个内容项生成截图"""
    db = await get_db()

    # 从 step_outputs 获取 novel_id
    step_outputs = json.loads(item.get("step_outputs", "{}"))
    analysis = step_outputs.get("analysis", {})
    novel_id = analysis.get("novel_id") or item.get("novel_id")

    if not novel_id:
        log.error("content_id=%d 缺少 novel_id", content_id)
        return False

    title = item["novel_title"]
    cfg = get_config()
    output_base = Path(cfg.get("output", {}).get("dir", "./output"))
    output_dir = output_base / f"content-{content_id}"

    try:
        results = await take_screenshots(novel_id, title, output_dir)
        successes = [r for r in results if "error" not in r]
        failures = [r for r in results if "error" in r]

        if failures:
            log.warning(
                "《%s》截图部分失败: %d/%d",
                title, len(failures), len(results),
            )

        await update_content_item(
            db, content_id,
            status="screenshots_ready",
            step_outputs={
                "screenshots": {
                    "count": len(successes),
                    "failed": len(failures),
                    "files": successes,
                },
            },
        )
        log.info("《%s》截图完成: %d 张成功", title, len(successes))
        return True

    except Exception as e:
        log.error("《%s》截图失败: %s", title, e)
        await update_content_item(
            db, content_id,
            status="screenshot_failed",
            step_outputs={"screenshots": {"error": str(e)}},
        )
        return False


async def run_screenshots() -> None:
    """截图入口：处理所有已分析的内容"""
    db = await get_db()
    items = await get_content_items_by_status(db, "analyzed")
    log.info("发现 %d 个待截图的内容", len(items))

    if not items:
        print("没有待截图的内容（需要先完成分析）")
        return

    success = 0
    for item in items:
        ok = await screenshot_one(item["id"], item)
        if ok:
            success += 1

    print(f"\n截图完成: {success}/{len(items)} 成功")
