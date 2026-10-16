# -*- coding: utf-8 -*-
"""
抖音扫码登录(持久 profile 版, 2026-09-15)
==========================================
storage_state 回放被抖音风控降级的解法:登录与后续采集共用同一个
launch_persistent_context 用户目录,cookie + 指纹态(localStorage/
IndexedDB/canvas 等)完整一致,不发生"换壳"。

用法:
    python douyin_login_profile.py        # 打开浏览器扫码,登录态留在 profile
    python probe_douyin_profile.py        # 用同一 profile 探测搜索/评论
"""

import sys
import time
from pathlib import Path

PROFILE_DIR = Path(__file__).parent / "credentials" / "douyin_profile"
LOGIN_TIMEOUT = 600


def open_profile_context(p, headless=False):
    """打开持久 profile 的浏览器上下文(登录/采集共用此函数的参数形态)。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1440, "height": 900},
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
              "--disable-dev-shm-usage"],
    )


def is_logged_in(context) -> bool:
    names = {c["name"] for c in context.cookies("https://www.douyin.com")}
    return "sessionid" in names or "sessionid_ss" in names


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Need: pip install playwright && playwright install chromium")
        sys.exit(1)

    print("=" * 60)
    print("  Douyin Login (persistent profile)")
    print("=" * 60)
    print(f"profile: {PROFILE_DIR}")

    with sync_playwright() as p:
        ctx = open_profile_context(p)
        if is_logged_in(ctx):
            print("\n已有登录态,无需重新扫码。直接关闭。")
            ctx.close()
            return 0
        page = ctx.new_page()
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
        print("请在浏览器中扫码登录(手机抖音)…")

        detected = False
        start = time.time()
        while time.time() - start < LOGIN_TIMEOUT:
            if is_logged_in(ctx):
                detected = True
                break
            time.sleep(2)
        if not detected:
            print(f"超时({LOGIN_TIMEOUT}s)未检测到登录。")
            ctx.close()
            return 1
        # 再等几秒让登录后的 localStorage 写完
        time.sleep(5)
        print("\n登录成功!状态已留在持久 profile 中。")
        ctx.close()
    print("\n下一步: python probe_douyin_profile.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
