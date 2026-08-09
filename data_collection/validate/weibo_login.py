"""
微博扫码登录 (Playwright, 自动检测)
==================================
用法: python weibo_login.py

1. 打开 Chromium 浏览器 → 访问 m.weibo.cn
2. 在浏览器中扫码或密码登录
3. 脚本自动检测登录成功 → 保存 Cookie → 关闭浏览器

Cookie 有效期 7-30 天, 过期后重新运行此脚本。
"""

import sys
import time
from pathlib import Path

CREDENTIALS_DIR = Path(__file__).parent / "credentials"
CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_FILE = CREDENTIALS_DIR / "weibo_cookie.txt"
LOGIN_TIMEOUT = 180  # 最长等待 3 分钟


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要安装 Playwright:")
        print("  pip install playwright")
        print("  playwright install chromium")
        sys.exit(1)

    print("=" * 60)
    print("  Weibo Cookie Tool (Auto-detect)")
    print("=" * 60)
    print()
    print("Opening browser...")
    print("Please scan QR code or login with password on m.weibo.cn")
    print(f"Waiting up to {LOGIN_TIMEOUT}s for login...")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 430, "height": 932},
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://m.weibo.cn/")

        # 自动轮询检测 SUB Cookie
        detected = False
        start = time.time()
        while time.time() - start < LOGIN_TIMEOUT:
            cookies = context.cookies()
            cookie_names = {c["name"] for c in cookies}
            if "SUB" in cookie_names:
                detected = True
                break
            time.sleep(2)

        if not detected:
            print("\nTimeout! Login not detected within {LOGIN_TIMEOUT}s.")
            print("Cookies found:")
            for c in context.cookies():
                print(f"  {c['name']} = {c['value'][:30]}...")
            browser.close()
            sys.exit(1)

        # 提取 + 保存
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in context.cookies())

        if "SUB" not in cookie_str:
            print("\nSUB cookie not found after login detection!")
            browser.close()
            sys.exit(1)

        COOKIE_FILE.write_text(cookie_str, encoding="utf-8")
        print(f"\nDone! Cookie saved to: {COOKIE_FILE}")
        print("  SUB: present")
        print(f"  Length: {len(cookie_str)} chars")
        browser.close()

    print()
    print("Now run:")
    print("  python batch_collect.py --platform weibo --per-kw 20")


if __name__ == "__main__":
    main()
