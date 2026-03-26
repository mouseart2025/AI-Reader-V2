"""CSDN Cookie 获取工具 — 打开浏览器窗口，手动登录后自动提取 Cookie"""

import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main() -> None:
    print("🔄 启动浏览器，请在弹出的窗口中登录 CSDN...")
    print("   登录完成后，脚本会自动提取 Cookie\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://passport.csdn.net/login")

        # 等待登录成功 — 跳转到 csdn.net 首页或个人页
        print("⏳ 等待登录... (登录成功后会自动继续，5 分钟超时)")
        try:
            await page.wait_for_url(re.compile(r"https://(www\.)?csdn\.net"), timeout=300_000)
        except Exception:
            pass

        # 额外等待确保 cookie 写入
        await page.wait_for_timeout(3000)

        # 提取 cookie
        cookies = await context.cookies("https://csdn.net")
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        await browser.close()

    if not cookie_str:
        print("❌ 未获取到 Cookie，请确认已成功登录")
        sys.exit(1)

    print(f"\n✅ Cookie 获取成功 ({len(cookies)} 个)")

    # 更新 config.yaml
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        # 替换 csdn cookie 行
        new_text = re.sub(
            r"(csdn:.*?cookie:\s*)(.*?)(\n)",
            lambda m: f"{m.group(1)}\"{cookie_str}\"{m.group(3)}",
            text,
            count=1,
            flags=re.DOTALL,
        )

        if new_text != text:
            config_path.write_text(new_text, encoding="utf-8")
            print(f"✅ 已自动写入 {config_path}")
        else:
            print(f"\n📋 请手动粘贴到 config.yaml 的 csdn.cookie:")
            print(f"   cookie: \"{cookie_str}\"")
    else:
        print(f"\n📋 Cookie 值:")
        print(f"   {cookie_str}")


if __name__ == "__main__":
    asyncio.run(main())
