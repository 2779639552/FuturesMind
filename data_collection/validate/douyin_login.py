"""
抖音扫码登录(Playwright 持久化 profile)
=========================================
用法: python douyin_login.py

【为什么是持久化 profile】
抖音把 sessionid 与设备指纹(s_v_web_id / __ac_nonce)绑定。旧做法是
"临时上下文扫码 → 存 storage_state → 采集时整包加载",实测(2026-09-15)在
无头和有头下**都会**落"验证码中间页" —— 跨上下文搬会话 = 会话劫持特征。
本脚本改为在 credentials/douyin_profile 这份持久化 profile 内登录,
登录态与指纹一起留在原地,采集适配器复用同一份 profile。

流程:
  1. 以持久化 profile 打开可见 Chromium → 访问 douyin.com
  2. 手机抖音扫码登录 → 自动检测 sessionid
  3. 就地验证搜索页能否渲染出结果(若弹滑块,请在该窗口内手动划一下)
  4. 关闭浏览器(释放 profile 锁),登录态已留在 profile 目录

登录成功后:
  python batch_collect.py --platform douyin --per-kw 5 --since 2026-09-08
"""

import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from platforms.douyin_adapter import PROFILE_DIR, launch_douyin_context  # noqa: E402

LOGIN_TIMEOUT = 300   # 扫码等待上限(秒)
VERIFY_TIMEOUT = 150  # 搜索页验证等待上限(秒,留给手动过滑块)
SEARCH_KW = "期货交易"
SEARCH_URL = "https://www.douyin.com/search/%s?type=video" % SEARCH_KW


def _session_names(ctx) -> set:
    return {c["name"] for c in ctx.cookies()}


def _has_session(ctx) -> bool:
    names = _session_names(ctx)
    return "sessionid" in names or "sessionid_ss" in names


def _dump_cookies(ctx) -> None:
    for c in ctx.cookies()[:15]:
        print("  %s = %s..." % (c["name"], str(c["value"])[:40]))


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Need: pip install playwright && playwright install chromium")
        return 1

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("  Douyin Login Tool (persistent profile)")
    print("=" * 60)
    print("profile: %s" % PROFILE_DIR)
    print()

    with sync_playwright() as p:
        ctx = launch_douyin_context(p, str(PROFILE_DIR), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if _has_session(ctx):
            print("检测到已有登录态,直接进入验证(如需换账号请先删除上面 profile 目录)")
        else:
            print("Opening browser... 请用手机抖音 App 扫码登录")
            print("等待上限 %ds" % LOGIN_TIMEOUT)
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded")

        # ① 等登录
        start = time.time()
        while not _has_session(ctx):
            if time.time() - start > LOGIN_TIMEOUT:
                print("\n超时:未检测到 sessionid。当前 Cookie:")
                _dump_cookies(ctx)
                ctx.close()
                return 1
            time.sleep(2)

        print("\n登录检测成功(sessionid 已就位),开始验证搜索页...")

        # ② 就地验证搜索页:能渲染出视频卡片才算真通
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45000)
        ok = False
        captcha_seen = False
        vstart = time.time()
        while time.time() - vstart < VERIFY_TIMEOUT:
            title = page.title() or ""
            if "验证码" in title:
                captcha_seen = True
                print("  检测到验证码中间页 —— 请在浏览器窗口内完成滑块验证(继续等待)...")
            else:
                n = page.evaluate(
                    "() => document.querySelectorAll('a[href*=\"/video/\"]').length")
                if n and n > 0:
                    ok = True
                    print("  搜索页正常渲染,命中 %d 个视频链接" % n)
                    break
            time.sleep(3)

        ctx.close()

    print()
    if ok:
        print("✅ 抖音登录完成且搜索页可用,登录态已留在 profile:")
        print("   %s" % PROFILE_DIR)
        print("   下一步: python batch_collect.py --platform douyin --per-kw 5 --since 2026-09-08")
        return 0

    if captcha_seen:
        print("⚠️  登录成功,但搜索页仍被验证码中间页拦截(滑块未过)。")
        print("   请在浏览器窗口内完成滑块后重跑本脚本验证。")
    else:
        print("⚠️  登录成功,但搜索页未渲染出结果(可能是登录墙或页面结构变化)。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
