"""小红书扫码登录 —— 在真实浏览器里完成登录，登录态与设备指纹同源。

【为什么不用 auto_login.py（纯 API 版）】
API 版在"换取会话"那一步 `/api/sns/web/v1/login/qrcode/status` 会被 WAF 拦成
**HTTP 471**，而响应体**伪装成功**（`{"code":0,"success":true,"data":{}}`）——
只解析 JSON 不看状态码的实现会把"被拦"读成"成功"，静默存下一份**没有 web_session**
的空 cookie。2026-09-15 实测：扫码被正确识别（`/api/qrcode/userinfo` 返回
codeStatus=2），但换取会话连试 3 次全 471，cookie 里还多了 `acw_tc`（阿里云 WAF 介入）。

【⚠️ 最坑的一点：web_session 在**游客态也存在**】
`/api/sns/web/v2/user/me` 对游客返回 `{"data":{"user_id":"...","guest":true},
"success":true}` —— `success:true` 但 `guest:true`。
所以「cookie 里有 web_session」**不等于**登录成功：页面一加载就有一个游客
web_session，用它当判据会**秒判成功、根本不等人扫码**（本脚本第一版就这么错过一次）。
正确判据（同 MediaCrawler `check_login_state` 的做法）：
  ① 先记下**登录前**的 web_session；
  ② 等到它**发生变化**；
  ③ 再用 user/me 确认 `guest` 不为 true。

⚠️ UA 必须与签名引擎写死的 `Chrome/122.0.0.0` 对齐：`x-s-common` 里带平台指纹，
   浏览器身份与签名身份不一致同样会被拦。

用法：
    python xhs_login.py            # 弹出浏览器窗口 + 二维码图片，扫码后自动导出 cookie
"""
import os
import sys
import time
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = ENGINE_DIR.parent / "validate" / "credentials" / "xhs_profile"
QR_PNG = ENGINE_DIR.parent / "validate" / "output" / "xhs_qrcode.png"
ENV_PATH = ENGINE_DIR / ".env"

# 与 xhs_utils/xhs_util.py 的 generate_headers 保持一致（勿随意改动）
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
]
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
"""


def open_qr_png(data: bytes) -> None:
    QR_PNG.parent.mkdir(parents=True, exist_ok=True)
    QR_PNG.write_bytes(data)
    print(f"     二维码图片: {QR_PNG}")
    try:
        os.startfile(str(QR_PNG))  # noqa: S606  Windows 专用：默认看图器打开
    except Exception as e:
        print(f"     (自动打开图片失败: {e})")


def show_qr(page) -> bool:
    """在当前页找到登录二维码并展示；找不到就点登录按钮后再找。"""
    try:
        if not page.query_selector("img.qrcode-img"):
            for sel in (
                "xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",
                "text=登录",
                ".login-btn",
            ):
                try:
                    el = page.query_selector(sel)
                    if el:
                        el.click()
                        time.sleep(2)
                        break
                except Exception:
                    continue
        el = page.query_selector("img.qrcode-img")
        if el:
            open_qr_png(el.screenshot())
            return True
    except Exception as e:
        print(f"     取二维码异常: {e}")
    return False


def cookie_dict(ctx) -> dict:
    return {c["name"]: c["value"] for c in ctx.cookies()}


def whoami(cookies: dict):
    """用签名引擎问一次 user/me；返回 (是否真实登录, 描述)。"""
    try:
        sys.path.insert(0, str(ENGINE_DIR))
        os.chdir(ENGINE_DIR)
        from apis.xhs_pc_login_apis import XHSLoginApi

        ok, info, _ = XHSLoginApi().get_user_info(dict(cookies))
        if not ok:
            return False, f"user/me success=False: {str(info)[:120]}"
        if info.get("guest") is True:
            return False, "仍是游客态 (guest=true)"
        return True, f"{info.get('nickname', '?')} (RedID: {info.get('red_id', '?')})"
    except Exception as e:
        return False, f"校验异常 {type(e).__name__}: {e}"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要 playwright：pip install playwright && playwright install chromium")
        return 1

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 62)
    print("小红书扫码登录（浏览器内完成，登录态与指纹同源）")
    print(f"  profile : {PROFILE_DIR}")
    print("=" * 62)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            user_agent=UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
            args=LAUNCH_ARGS,
        )
        ctx.add_init_script(_STEALTH_JS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("[1/3] 打开小红书 ...")
        try:
            page.goto("https://www.xiaohongshu.com/explore",
                      wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"     导航异常: {e}")
        time.sleep(4)

        # ① 记录登录前的游客 session（这一步是整个脚本的关键）
        base = cookie_dict(ctx)
        guest_session = base.get("web_session", "")
        real, desc = whoami(base)
        print(f"     登录前状态: guest_session={'有' if guest_session else '无'} "
              f"({len(guest_session)} 字符) | {desc}")
        if real:
            print("     ✅ profile 里已有真实登录态，无需扫码")

        if not real:
            print("[2/3] 获取二维码 ...")
            if not show_qr(page):
                print("     未找到二维码元素，尝试刷新页面")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=45000)
                    time.sleep(4)
                    show_qr(page)
                except Exception as e:
                    print(f"     刷新异常: {e}")
            print("\n     👉 请用小红书 App 扫码（浏览器窗口内或弹出的二维码图片）\n")

            deadline = time.time() + 300
            last_refresh = time.time()
            while time.time() < deadline:
                cur = cookie_dict(ctx)
                ws = cur.get("web_session", "")
                # ② web_session 必须与游客态不同；③ 再用 user/me 确认不是游客
                if ws and ws != guest_session:
                    real, desc = whoami(cur)
                    if real:
                        print(f"     ✅ 登录成功: {desc}")
                        break
                    print(f"     … session 已变化但校验未过: {desc}")
                if time.time() - last_refresh > 100:
                    print("     二维码可能已过期，重新获取 ...")
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=45000)
                        time.sleep(4)
                        show_qr(page)
                    except Exception:
                        pass
                    last_refresh = time.time()
                time.sleep(2)

            if not real:
                print("❌ 超时：仍未取得真实登录态，未写入 .env")
                ctx.close()
                return 2

        final = cookie_dict(ctx)
        cookies_str = "; ".join(f"{k}={v}" for k, v in final.items())
        ENV_PATH.write_text(f"COOKIES={cookies_str}\n", encoding="utf-8")
        print(f"\n[3/3] 已写入 {ENV_PATH}（{len(cookies_str)} 字符）")
        print(f"     key: {sorted(final)}")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
