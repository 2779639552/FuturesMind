"""
知乎扫码登录 (Playwright, 自动检测)
==================================
用法: python zhihu_login.py

1. 打开 Chromium 浏览器 → 访问 zhihu.com
2. 扫码或密码登录
3. 脚本自动检测登录成功 → 保存 storage_state → 关闭浏览器

知乎登录态持久化: cookies + localStorage (storage_state)
"""

import json
import sys
import time
from pathlib import Path

CREDENTIALS_DIR = Path(__file__).parent / "credentials"
CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = CREDENTIALS_DIR / "zhihu_login_state.json"
LOGIN_TIMEOUT = 180


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Need: pip install playwright && playwright install chromium")
        sys.exit(1)

    print("=" * 60)
    print("  Zhihu Cookie Tool (Auto-detect)")
    print("=" * 60)
    print()
    print("Opening browser...")
    print("Please scan QR code or login on zhihu.com")
    print(f"Waiting up to {LOGIN_TIMEOUT}s...")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://www.zhihu.com/signin")

        # Auto-detect login by checking for z_c0 cookie
        detected = False
        start = time.time()
        while time.time() - start < LOGIN_TIMEOUT:
            cookies = context.cookies()
            cookie_names = {c["name"] for c in cookies}
            if "z_c0" in cookie_names:
                detected = True
                break
            time.sleep(2)

        if not detected:
            print(f"\nTimeout! Login not detected within {LOGIN_TIMEOUT}s.")
            print("Cookies found:")
            for c in context.cookies():
                print(f"  {c['name']} = {str(c['value'])[:40]}...")
            browser.close()
            sys.exit(1)

        # Also check we're actually logged in by testing a simple API
        try:
            resp = page.evaluate("""async () => {
                const r = await fetch('/api/v4/me');
                return await r.json();
            }""")
            if resp.get("id"):
                print(f"  Logged in as: {resp.get('name', 'unknown')}")
            else:
                print("  Warning: login may be incomplete (no user id in /api/v4/me)")
        except Exception:
            print("  Warning: Could not verify login state via API")

        # Save storage_state
        state = context.storage_state()
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nDone! Login state saved to: {STATE_FILE}")
        print(f"  Cookies: {len(state.get('cookies', []))}")
        browser.close()

    print()
    print("Now run:")
    print("  python batch_collect.py --platform zhihu --per-kw 10")


if __name__ == "__main__":
    main()
