"""掘金 Cookie 获取工具 — 打开浏览器窗口，手动登录后自动提取 Cookie"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main() -> None:
    print("🔄 启动浏览器，请在弹出的窗口中登录掘金...")
    print("   登录完成后，脚本会自动提取 Cookie\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://juejin.cn/login")

        # 等待用户登录成功 — 检测页面跳转到首页或出现用户头像
        print("⏳ 等待登录... (登录成功后会自动继续)")
        try:
            await page.wait_for_url("https://juejin.cn/", timeout=300_000)  # 5 分钟超时
        except Exception:
            # 可能 URL 不完全匹配，检查是否已登录
            pass

        # 额外等待确保 cookie 写入
        await page.wait_for_timeout(3000)

        # 提取 cookie
        cookies = await context.cookies("https://juejin.cn")
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
        # 查找 juejin cookie 行并替换
        import re
        # 匹配 juejin 区块下的 cookie 行
        new_text = re.sub(
            r"(juejin:.*?cookie:\s*)(.*?)(\n)",
            lambda m: f"{m.group(1)}\"{cookie_str}\"{m.group(3)}",
            text,
            count=1,
            flags=re.DOTALL,
        )

        if new_text != text:
            config_path.write_text(new_text, encoding="utf-8")
            print(f"✅ 已自动写入 {config_path}")
        else:
            # 直接输出让用户手动粘贴
            print(f"\n📋 请手动粘贴到 config.yaml 的 juejin.cookie:")
            print(f"   cookie: \"{cookie_str}\"")
    else:
        print(f"\n📋 Cookie 值:")
        print(f"   {cookie_str}")


if __name__ == "__main__":
    asyncio.run(main())
