"""
微博扫码登录 (Playwright, 持久化 profile + 真实登录态判定)
==========================================================
用法:
  python weibo_login.py                  # 打开浏览器并**自动点出二维码**(默认最多等 15 分钟)
  python weibo_login.py --harvest-only   # 不等待: 只从已登录的 profile 导出 Cookie
                                         # (适合"码扫完了、但脚本已超时退出/窗口被关"的补救)
  python weibo_login.py --headed         # 补救模式也显示窗口(默认无头)
环境变量:
  WEIBO_LOGIN_TIMEOUT  等待秒数,默认 900

为什么不能用"检测到 SUB 就判定成功":
  微博**游客态也会下发 SUB / SUBP**。旧版脚本只判断 "SUB" in cookies，
  于是在游客状态下会立刻"秒进成功"，把一份**没有登录的游客 Cookie**写进
  credentials/weibo_cookie.txt。
  后果不是报错，而是搜索**第 2 页起静默返回 ok=-100 + passport 跳转**，
  表现为"每个关键词只有几条"，极易被误判成"平台没内容"。

本版四点改造（与 xhs / douyin 登录脚本同源思路）:
  1. 用 launch_persistent_context + credentials/weibo_profile —— 会话与浏览器
     指纹同源，避免"跨上下文搬 Cookie"导致的登录态不被认可 / 风控。
  2. 判据改为调 m.weibo.cn/api/config 看 data.login == True（真登录态）。
  3. 保存前**实测第 2 页能否翻动**，能翻才落盘，杜绝"假成功"。
  4. 【2026-09-18 新增】把"二维码真的出现在屏幕上"当作必须达成的中间状态。
     旧版只 goto m.weibo.cn —— 那是**手机版信息流首页**，既没有二维码也没有登录框，
     用户打开窗口后"没看到扫码"，只能关窗走人(实测被关于 82 秒)。
     现在是: goto 登录页 → **自动点击底部「扫码登录」** → 等到二维码 img 出现为止。
     另修两处相关缺陷:
       · 窗口被关掉时脚本直接抛 TargetClosedError 崩溃 —— 改为捕获后自动
         从 profile 补救收割(Cookie 在关闭瞬间已落盘,不救就白扫了)。
       · 同名 Cookie 跨域重复(.weibo.com 与 .weibo.cn 各有一份 SUB)会让请求头里
         出现两个 SUB，服务端取哪份不确定 —— 改为生成候选并用真实请求挑出可用那份。
"""

import json
import os
import sys
import time
from pathlib import Path

CREDENTIALS_DIR = Path(__file__).parent / "credentials"
CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_FILE = CREDENTIALS_DIR / "weibo_cookie.txt"
PROFILE_DIR = CREDENTIALS_DIR / "weibo_profile"

# 最长等待(秒)。可用 WEIBO_LOGIN_TIMEOUT 覆盖。
# 【为什么默认是 900 而不是 300】扫码要掏手机、开 App 再对准镜头,300 秒实测经常不够:
#   2026-09-18 一次 5 分钟到点仍处游客态,窗口被关掉,**profile 里哪怕之后登录了也没人收割**。
#   给足 15 分钟,配合心跳输出,后台跑的时候能一眼看出"还在等"还是"卡死了"。
LOGIN_TIMEOUT = int(os.environ.get("WEIBO_LOGIN_TIMEOUT", "900"))
HEARTBEAT_SECONDS = 30  # 每 N 秒打一行心跳(含已等待时长与当前登录态判读)
POLL_SECONDS = 3  # 轮询 Cookie 变化的间隔(本地读取,不发网络请求)
DEEP_RECHECK_SECONDS = 60  # 即使 Cookie 没变化,也至少每分钟做一次带网络的深验证

# 【关键常量】登录页地址。必须带 r= 回跳参数,登录成功后会跳回 m.weibo.cn
#   —— 采集端打的就是 m.weibo.cn,只有它被访问过才会下发 `.weibo.cn` 域的会话 Cookie。
LOGIN_URL = (
    "https://passport.weibo.cn/signin/login"
    "?entry=mweibo&r=https%3A%2F%2Fm.weibo.cn%2F"
)
# 二维码图片的稳定特征: 由 v2.qr.weibo.cn 动态生成
QR_IMG_SELECTOR = 'img[src*="qr.weibo.cn"]'
# 点出二维码的候选选择器(页面文案是「扫码登录」)
QR_CLICK_SELECTORS = ("text=扫码登录", "text=扫码", "a:has-text('扫码登录')")
# 【为什么盯着"加载完成"而不是"元素存在"】实测元素先出现、图还是白的
# (naturalWidth=0, 截图就是一个空白框) —— 只等元素存在会把"没码的白框"当成就绪,
# 用户打开窗口又一次"没看到扫码"。必须等到 complete 且 naturalWidth>0。
QR_LOADED_JS = """() => {
  const e = document.querySelector('img[src*="qr.weibo.cn"]');
  return !!(e && e.complete && e.naturalWidth > 0);
}"""
# 源图比显示尺寸大就按 1:1 显示(更大更好扫)。不做插值放大 —— 插值会把二维码
# 的方块糊掉反而更难扫; 只在"显示尺寸 < 源图尺寸"时改, 所以永远不失真。
# 实测: 源图 180px、页面只用 140px 显示, 白白浪费了 40px 的可扫面积。
QR_ENLARGE_JS = """() => {
  const e = document.querySelector('img[src*="qr.weibo.cn"]');
  if (!e) return null;
  const nw = e.naturalWidth;
  const box = Math.round(e.getBoundingClientRect().width);
  if (nw > box + 10) {
    e.style.width = nw + 'px';
    e.style.height = nw + 'px';
  }
  return {natural: nw, box: Math.round(e.getBoundingClientRect().width)};
}"""


def _wait_qr_loaded(page, timeout: int = 12000) -> bool:
    """等二维码图片**真的画出来**(不只是 DOM 里挂了节点)。

    【功能】轮询 naturalWidth>0，确认用户屏幕上能看到码。
    【参数】page: Playwright Page；timeout: 最长等待毫秒数
    【返回】True=已渲染；False=超时
    """
    try:
        page.wait_for_function(QR_LOADED_JS, timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def _prepare_login_page(page) -> str:
    """把登录窗口推进到"二维码已显示、正等你扫"的状态。

    【功能】goto 登录页 → 自动点击底部「扫码登录」→ 等到二维码**图片加载完成**。
    【参数】page: Playwright Page
    【返回】状态说明；成功时以 "QR_READY" 开头
    【为什么必须有这一步】登录页默认只展示"账号密码"表单，二维码要**再点一次**底部
        「扫码登录」才出现；而旧版连登录页都没进(进的是信息流首页)。
        实测: m.weibo.cn 首页 0 个二维码元素、无"扫码"字样 —— 用户"没看到扫码"是必然的。
    """
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    if _wait_qr_loaded(page):
        return f"QR_READY 二维码已显示（页面默认即为扫码登录）{_qr_box_note(page)}"

    for sel in QR_CLICK_SELECTORS:
        try:
            page.click(sel, timeout=4000)
        except Exception:  # noqa: BLE001
            continue
        if _wait_qr_loaded(page):
            return f"QR_READY 二维码已显示（已自动点击「扫码登录」）{_qr_box_note(page)}"
    return "未能自动点出二维码，请手动点击页面底部的『扫码登录』"


def _qr_box_note(page) -> str:
    """尝试把二维码按 1:1 放大,并返回尺寸说明(诊断用)。"""
    try:
        info = page.evaluate(QR_ENLARGE_JS)
    except Exception:  # noqa: BLE001
        return ""
    if not info:
        return ""
    return f" | 源图 {info['natural']}px 显示 {info['box']}px"

CHROME_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)


def _probe(cookie: str, page: int) -> dict:
    """用给定 cookie 直接打微博接口，返回解析后的 JSON。

    【功能】在浏览器之外用 requests 复核"这份 Cookie 到底能不能翻页"。
    【参数】cookie: 待验证的 Cookie 串；page: 页码（1=匿名可取，2=必须登录）
    【返回】接口 JSON（成功 or ok=-100 等错误信封）
    """
    import requests

    headers = {
        "User-Agent": CHROME_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://m.weibo.cn/",
        "Cookie": cookie,
    }
    resp = requests.get(
        "https://m.weibo.cn/api/container/getIndex",
        params={
            "containerid": "100103type=1&q=螺纹钢",
            "page_type": "searchall",
            "page": page,
        },
        headers=headers,
        timeout=15,
    )
    try:
        return resp.json()
    except ValueError:
        return {"ok": None, "raw": resp.text[:120]}


def _is_logged_in(cookie: str) -> bool:
    """判断 Cookie 是否代表真实登录态（而非游客态）。

    【功能】调 /api/config 读 data.login。
    【参数】cookie: 待验证 Cookie 串
    【返回】True=已登录；False=游客/失效
    【关键逻辑】⚠️ **有 SUB ≠ 已登录**。必须看 login 字段。
    """
    import requests

    headers = {
        "User-Agent": CHROME_UA,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://m.weibo.cn/",
        "Cookie": cookie,
    }
    try:
        resp = requests.get(
            "https://m.weibo.cn/api/config", headers=headers, timeout=15
        )
        return bool(resp.json().get("data", {}).get("login"))
    except Exception:  # noqa: BLE001
        return False


def _cookie_variants(ctx) -> list:
    """给出若干候选 Cookie 串，供验证环节挑出"服务端真的认"的那一份。

    【功能】同一份浏览器 Cookie 按"域优先级"派生出候选请求头串。
    【参数】ctx: Playwright 持久化上下文
    【返回】[(标签, Cookie串), ...]，按推荐程度排序
    【为什么不能直接全部拼起来】扫码登录走 passport.weibo.com，会给 **.weibo.com**
        域下发一份 SUB；而采集端打的是 **m.weibo.cn**，用的是 **.weibo.cn** 域的
        SUB（游客态也有一份）。两个域同名 SUB 一起拼进请求头就会出现
        `SUB=..; SUB=..` 这种重复键，服务端取哪份不确定 ——
        最坏情况是"登录明明成功、采回来还是 8 条"。这里不做猜测：
        生成候选，让 _validate_context 用真实请求去挑。
    """
    cookies = ctx.cookies()
    allv = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    def _merge(want: str) -> str:
        """同名 Cookie 冲突时,优先保留域以 want 结尾的那一份(保证请求头里 SUB 只有一个)。"""
        picked = {}
        for c in cookies:
            name = c["name"]
            dom = (c.get("domain") or "").lower()
            prev = picked.get(name)
            if prev is None or (dom.endswith(want) and not prev[1].endswith(want)):
                picked[name] = (c["value"], dom)
        return "; ".join(f"{k}={v[0]}" for k, v in picked.items())

    # 两个方向都试: 登录态到底落在 .weibo.cn 还是 .weibo.com 上,由真实请求判定,
    # 不靠猜 —— 猜错的表现就是"登录成功但依然只采 8 条"。
    out = [("weibo.cn优先", _merge("weibo.cn")), ("weibo.com优先", _merge("weibo.com"))]
    if allv not in {ck for _, ck in out}:
        out.append(("原样全量", allv))
    return out


def _cookie_signature(ctx) -> str:
    """给当前 Cookie 集合取一个本地指纹(不发网络请求),用于判断"状态是否变了"。

    【功能】等待期间靠它触发深验证 —— 用户扫码后 Cookie 会立刻变，
        不必每 3 秒就去打微博接口(15 分钟下来会打几千次,容易被风控)。
    【参数】ctx: Playwright 持久化上下文
    【返回】指纹字符串
    """
    import hashlib

    raw = "|".join(
        sorted(f"{c['name']}@{c.get('domain')}={c['value']}" for c in ctx.cookies())
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _validate_context(ctx) -> tuple:
    """判定当前浏览器上下文是否已具备**可用**的登录态。

    【功能】把"到底算不算登录成功"收成一个函数，供等待循环与 --harvest-only 共用。
    【参数】ctx: Playwright 持久化上下文
    【返回】(是否通过, 诊断说明, 应落盘的 Cookie 串)
    【关键逻辑】必须**同时**满足两条，缺一不可:
        1) /api/config 的 data.login == True（证明不是游客态）
        2) 搜索第 2 页 ok == 1（证明这个登录态真的能翻页 —— 只判 login 会漏掉
           "已登录但被风控限制"的场景，那样落盘的 Cookie 依然只能采第 1 页）
    """
    last = "尚未登录"
    for label, ck in _cookie_variants(ctx):
        if not ck:
            continue
        if "SUB" not in ck:
            last = "浏览器内还没有 SUB（尚未登录）"
            continue
        if not _is_logged_in(ck):
            # 游客态: 微博会给游客也发 SUB,这正是旧版会误判成成功的场景
            last = f"[{label}] 检测到 SUB 但 login=False（游客态）"
            continue
        probe = _probe(ck, 2)
        if probe.get("ok") == 1:
            return True, f"[{label}] login=True 且搜索第 2 页可翻", ck
        last = f"[{label}] 已登录但第 2 页仍被拒: {str(probe)[:60]}"
    return False, last, ""


def _open_context(p, headless: bool):
    """按统一参数打开持久化 profile 上下文（登录与补救收割共用，保证指纹一致）。"""
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 430, "height": 932},
        user_agent=CHROME_UA,
        locale="zh-CN",
        args=["--no-first-run", "--no-default-browser-check"],
    )


def _harvest_once(p) -> tuple:
    """补救收割: 用新的无头上下文读一次 profile，判断是否已有可用登录态。

    【功能】窗口被关掉后的兜底 —— Chromium 关闭时会把 Cookie flush 到 profile，
        所以重开一个上下文就能把登录态读出来，避免"扫完码却白扫"。
    【参数】p: sync_playwright() 上下文管理器
    【返回】(是否通过, 诊断说明, Cookie串)
    """
    ctx = _open_context(p, headless=True)
    try:
        return _validate_context(ctx)
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass


def _save_cookie(cookie_str: str) -> None:
    """把已验证可用的 Cookie 串落盘为采集侧使用的格式。"""
    COOKIE_FILE.write_text(cookie_str, encoding="utf-8")


def main() -> None:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要安装 Playwright:")
        print("  pip install playwright")
        print("  playwright install chromium")
        sys.exit(1)

    harvest_only = "--harvest-only" in sys.argv  # 【变量】补救模式: 只从现有 profile 收割

    # 【为什么关掉缓冲】后台/重定向运行时 Python 默认块缓冲，心跳会全憋在缓冲区里
    # 直到进程退出才吐出来，表现为"脚本明明在等、日志却一片空白"(2026-09-18 实测踩过,
    # 只能靠 profile lockfile 判断窗口是否还开着)。改成行缓冲后心跳可实时看到。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    print("=" * 62)
    print("  微博登录（持久化 profile + 真实登录态判定）")
    print("=" * 62)
    print()
    if harvest_only:
        print("模式: --harvest-only（不打开等待窗口，直接检查 profile 里的登录态）")
    print("⚠️ 判定成功的标准是『第 2 页能翻动』，只看到首页有内容是**不算**的。")
    print(f"   最长等待 {LOGIN_TIMEOUT} 秒（可用 WEIBO_LOGIN_TIMEOUT 覆盖）。")
    print()

    with sync_playwright() as p:
        # 补救模式默认无头: 只是把 profile 里已有的 Cookie 读出来,不需要人看窗口。
        headless = harvest_only and "--headed" not in sys.argv
        ctx = _open_context(p, headless=headless)

        ok = False
        last_note = ""
        won_cookie = ""
        window_closed = False
        try:
            if harvest_only:
                ok, last_note, won_cookie = _validate_context(ctx)
            else:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                print(f"   {_prepare_login_page(page)}")
                print()
                print("   👉 手机打开微博 App → 底部「我」→ 右上角「扫一扫」")
                print("      对准窗口里的二维码扫码，并在手机上点确认。")
                print("      ⚠️ 请**不要关闭**这个窗口，扫码成功后脚本会自动收尾。")
                print()

                start = time.time()
                next_beat = start + HEARTBEAT_SECONDS
                last_sig = _cookie_signature(ctx)
                last_deep = 0.0
                while True:
                    now = time.time()
                    sig = _cookie_signature(ctx)
                    # 深验证会打微博接口,只在"Cookie 变了(多半是扫码成功)"或
                    # "距上次满 1 分钟"时才做;否则纯本地轮询,避免把接口打烦。
                    if sig != last_sig or (now - last_deep) >= DEEP_RECHECK_SECONDS:
                        last_sig = sig
                        last_deep = now
                        ok, last_note, won_cookie = _validate_context(ctx)
                        if ok:
                            break
                    elapsed = now - start
                    if elapsed >= LOGIN_TIMEOUT:
                        break
                    if now >= next_beat:
                        print(f"   [等待中 {int(elapsed):>4}s/{LOGIN_TIMEOUT}s] {last_note}")
                        next_beat = now + HEARTBEAT_SECONDS
                    time.sleep(POLL_SECONDS)
        except PlaywrightError as exc:
            # 窗口被关掉(用户以为完事了 / 误关)。此时 profile 里可能**已经有**有效登录态:
            # Chromium 关闭时会把 Cookie 落盘,所以不要直接放弃,换上下文重新读一次。
            window_closed = True
            last_note = f"浏览器窗口已被关闭（{type(exc).__name__}）"
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

        if not ok and window_closed and not harvest_only:
            print("\n⚠️ 浏览器窗口被关闭。正在从 profile 补救读取登录态"
                  "（Chromium 关闭时已把 Cookie 落盘）…")
            try:
                ok, last_note, won_cookie = _harvest_once(p)
            except Exception as exc:  # noqa: BLE001
                last_note = f"补救读取失败: {type(exc).__name__}: {str(exc)[:100]}"

        if not ok:
            print("\n❌ 未取得可用登录态，**未写入** Cookie 文件（旧 Cookie 保持原样）。")
            if last_note:
                print(f"   最后状态: {last_note}")
            print("\n排查顺序:")
            print("   1. 手机微博 App 是否扫了『窗口里』的码并在手机上确认；")
            print("   2. 窗口是否被提前关闭（关闭会把等待中断）；")
            print("   3. 若浏览器里已显示登录、仍被拒 → 触发了风控，稍后再试。")
            print("   提示: 只要 profile 里登录成功过，可以再跑一次")
            print("         `python weibo_login.py --harvest-only` 把 Cookie 补导出。")
            sys.exit(2)

        _save_cookie(won_cookie)
        print("\n✅ 登录态有效并已保存")
        print(f"   文件: {COOKIE_FILE}")
        print(f"   长度: {len(won_cookie)} 字符 | 校验: {last_note}")

    print()
    print("下一步:")
    print("  python batch_collect.py --platform weibo --per-kw 30 --no-detail")


if __name__ == "__main__":
    main()
