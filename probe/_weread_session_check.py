# -*- coding: utf-8 -*-
"""微信读书「网页登录态」体检 / 寿命测量（2026-10-16 新增）。

═══ 为什么需要它 ═══
2026-10-14 落地的「微信读书通道」用了 3 天就一直是 `-2012 登录超时`，
而**失败原因被误读成"cookie 失效 → 重跑登录脚本"**（告警文案原文如此）。
实际要分清三件不同的事，修法完全不同：

  · `-2012 登录超时`  → cookie **格式没错、身份也认得**，是**服务端那条会话记录超时了**
                        ⇒ 必须**重新扫码登录**（续期/换 key 都救不了已超时的会话）
  · `-2010 用户不存在` → cookie **本身无效**（缺 wr_skey / 被清 / 写错）
  · HTTP 200 + 有数据 → 会话活着

═══ 关于「为什么这么快就失效」（2026-10-16 查证）═══
`/web/mp/articles` 这条私域接口认两个 cookie：
  · `wr_vid`   —— **长期身份**（文件里 expires≈30 天）
  · `wr_skey`  —— **服务端会话密钥**（会被服务端**重新下发**；权威来源对其寿命说法不一：
                 "约 30 天" / "约数天"，且有实现明确写"客户端启动时自动续期"）
⇒ **`wr_skey` 不是可长期持有的凭据，而是需要不断续期的会话。**

★ 所以本通道的失败模式是**结构性**的，不是"运气不好"：
  我们是"**一次性借来的网页会话**"——登录时复制一份静态 cookie，之后**从不续期**
  （旧采集器用裸 requests，既不读 `Set-Cookie`，也没有心跳；
   **2026-10-19 已修**：改用 Session + `POST /web/login/renewal` 自动续期）；
  而这条链路只在 **08:30 / 18:30** 各跑十几秒，中间静默 **12 小时**
  ⇒ 必然在某两次运行之间被服务端的会话超时清掉。
  实测唯一一次成功（10-14 10:42）发生在**登录后 14 分钟**内，正落在会话有效窗口里。

═══ 用法 ═══
  python probe/_weread_session_check.py                # 体检（1 次请求，安全）
  python probe/_weread_session_check.py --watch 15     # 每 15 分钟探一次，测出真实寿命
                                                       # ⚠ 见下方"探测自我污染"警告

⚠ **探测自我污染纪律**：本接口虽不限制单次频率，但**频繁轮询会被判异常**。
  ⚠ **【2026-10-16 更正】判"会话有没有事"看 `/web/shelf/sync`，别用 mp 接口的错误码**：
    实测同一份 cookie，shelf/sync 正常（411 本书）而 mp/articles 返 -2041
    ⇒ 用后者推前者会把**接口层**问题误判成**会话层**问题（本脚本第一版就这么错的）。
  `--watch` 的最小间隔被强制为 **10 分钟**，且只探 1 个号，宁慢勿猛。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

STORE = Path.home() / ".tradingagents" / "weread"
F_COOKIES = STORE / "cookies.json"
F_STATE = STORE / "mp_seen.json"
BASE = "https://weread.qq.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
# 体检只挑一个号（少打扰）
PROBE_BOOK = ("混沌天成研究", "MP_WXS_3869147758")

# 已知错误码 → 处置（照抄服务端 errMsg，不推测码值含义）
CODE_HELP = {
    -2012: ("会话密钥过期（wr_skey 的 Max-Age 只有 5400 秒 = 90 分钟）",
            "→ **先自动续期**：POST /web/login/renewal（采集器已内置，无需扫码）；"
            "续期也失败才需要重扫码"),
    -2010: ("cookie 无效 / 身份不被识别（缺 wr_skey 或写错）",
            "→ 检查 cookies.json 是否含 wr_skey；必要时重跑登录脚本"),
    -2041: ("认证 / 风控错误（可能弹验证码；也可能是该接口对新版已废弃）",
            "→ 先用 /web/shelf/sync 判「会话有没有事」（本脚本步骤 1b）；"
            "若会话正常，则这是**接口层**问题 ⇒ 改用 /web/mp/cover + /web/mp/content"),
    -2042: ("该文章未收录", "→ 跳过该篇即可"),
}


def _load_cookies() -> list[dict]:
    if not F_COOKIES.exists():
        raise SystemExit("✘ 未找到 %s\n  先跑：node probe/_weread_login.mjs" % F_COOKIES)
    return (json.loads(F_COOKIES.read_text(encoding="utf-8")) or {}).get("cookies") or []


def _header(cks: list[dict]) -> str:
    return "; ".join("%s=%s" % (c["name"], c["value"]) for c in cks)


def step1_inventory() -> dict:
    """① 凭据清点：区分「长期身份」与「会话密钥」，并读各自的 expires。"""
    print("=" * 74)
    print("① 凭据清点")
    print("=" * 74)
    raw = json.loads(F_COOKIES.read_text(encoding="utf-8"))
    cks = raw.get("cookies") or []
    print("  文件：%s" % F_COOKIES)
    print("  保存于（login 时刻）：%s" % raw.get("savedAt"))
    print()
    print("  %-10s %-16s %s" % ("cookie", "角色", "expires"))
    roles = {"wr_vid": "长期身份", "wr_skey": "★会话密钥", "wr_rt": "刷新令牌",
             "wr_gid": "会话(浏览器级)", "wr_ql": "辅助", "wr_fp": "辅助"}
    now = time.time()
    for c in cks:
        exp = c.get("expires") or c.get("expirationDate")
        if isinstance(exp, (int, float)) and exp > 0:
            left = (exp - now) / 86400.0
            etxt = "%s（还剩 %.0f 天）" % (
                datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d"), left)
        else:
            etxt = "%s（会话级，关闭即弃）" % exp
        print("  %-10s %-16s %s" % (c.get("name"), roles.get(c.get("name"), "-"), etxt))
    print()
    print("  ⚠ `wr_skey` 的 expires 只是**浏览器侧**的 cookie 时限；")
    print("    真正管用的是**服务端那条会话记录** —— 实测它比 expires 早得多就失效了。")
    print("    判「现在通不通」只能实探（见 ②），看文件会得出错误结论。")
    return {"cookies": cks, "savedAt": raw.get("savedAt")}


def step1b_session_verdict(cks: list[dict]) -> dict:
    """★ 登录态判据：用 **/web/shelf/sync**（不受 mp 那层风控验证码影响）。

    【为什么单列】原脚本只看 /web/mp/articles 的错误码 → 把 **接口层** 的问题
      （-2041 风控/废弃）误判成 **会话层** 的问题（"会话超时，去重新扫码"）。
      实测同一份 cookie：shelf/sync 正常（411 本书），mp/articles 却是 -2041。
    ⇒ 判"会话有没有事"必须用一个**干净的、不受目标接口风控影响**的接口。
    """
    import requests
    hdr = "; ".join("%s=%s" % (c["name"], c["value"]) for c in cks)
    r = requests.get(BASE + "/web/shelf/sync", timeout=45,
                     headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                              "Referer": BASE + "/", "Cookie": hdr})
    body = {}
    try:
        body = r.json() or {}
    except Exception:
        pass
    code = body.get("errCode", body.get("errcode"))
    books = body.get("books") or []
    ok = (code is None or code == 0) and bool(books)
    return {"ok": ok, "code": code, "books": len(books), "status": r.status_code}


def step2_probe(cks: list[dict]) -> dict:
    """② 实探：1 次请求，按 errCode 三分解（会话超时 / 凭据无效 / 活着）。"""
    import requests
    print()
    print("=" * 74)
    print("② 实探（单号 1 次请求）")
    print("=" * 74)
    name, book = PROBE_BOOK
    r = requests.get(BASE + "/web/mp/articles", params={"bookId": book, "offset": 0},
                     headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                              "Referer": BASE + "/", "Cookie": _header(cks)}, timeout=40)
    # ★ 顺带记录：这条接口到底会不会回写 cookie（用来排除"轮换被我们丢了"这种误判）
    sc = r.headers.get("Set-Cookie")
    body = {}
    try:
        body = r.json() or {}
    except Exception:
        pass
    code = body.get("errCode")
    print("  请求：GET /web/mp/articles?bookId=%s&offset=0" % book)
    print("  HTTP %s   响应 %d 字节   Set-Cookie: %s"
          % (r.status_code, len(r.content), "有" if sc else "无"))
    print("  errCode=%s  errMsg=%r" % (code, body.get("errMsg")))
    print()
    if code is None and body:
        verdict = "ok"
        print("  ✔ 会话活着（接口返回了数据）")
    elif code in CODE_HELP:
        verdict = "dead" if code == -2012 else "bad"
        why, how = CODE_HELP[code]
        print("  ✘ %s" % why)
        print("    %s" % how)
    else:
        verdict = "unknown"
        print("  ? 未知返回：%s" % str(r.text)[:200])
    print()
    # 【2026-10-19 更正】上一版这里写着「本接口不会回写 Set-Cookie ⇒ 别往续期方向改」，
    #   那是**只看 articles/content 得出的片面结论**。真正的续期入口是
    #   POST /web/login/renewal —— 它明确下发新的 wr_skey（Max-Age=5400）。
    if sc:
        print("  ★ 本接口本次回写了 Set-Cookie（以续期接口的返回为准）。")
    else:
        print("  · 本接口不回写 Set-Cookie —— 这不代表「无法续期」；")
        print("    续期请用 POST /web/login/renewal（采集器已内置，实测下发新 wr_skey）。")
    return {"verdict": verdict, "errCode": code, "setCookie": bool(sc), "body": body}


def step3_lifetime(cks: list[dict], saved_at: str, watch_min: int) -> None:
    """③ 选测：按温和间隔轮询，测出「登录 → 首次失败」的真实寿命。"""
    import requests
    print()
    print("=" * 74)
    print("③ 寿命测量（每 %d 分钟探一次；Ctrl-C 停止）" % watch_min)
    print("=" * 74)
    print("  ⚠ 探测自我污染：本接口频繁轮询会被判异常 ⇒ 间隔被强制 ≥10 分钟、只探 1 个号。")
    t0 = time.time()
    n = 0
    while True:
        n += 1
        r = requests.get(BASE + "/web/mp/articles",
                         params={"bookId": PROBE_BOOK[1], "offset": 0},
                         headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                                  "Referer": BASE + "/", "Cookie": _header(cks)}, timeout=40)
        try:
            code = (r.json() or {}).get("errCode")
        except Exception:
            code = "非JSON"
        el = (time.time() - t0) / 60.0
        alive = code is None
        print("  第 %2d 次  距今 %6.1f 分钟  errCode=%-6s %s"
              % (n, el, code, "✔ 活着" if alive else "✘ 已失效"))
        if not alive:
            print()
            print("  ⇒ 实测寿命 ≈ %.0f 分钟（自本次探测起点）；登录时刻 %s" % (el, saved_at))
            print("    这就是「两次运行之间一定会超时」的量化依据。")
            return
        time.sleep(watch_min * 60)


def main() -> int:
    ap = argparse.ArgumentParser(description="微信读书网页登录态体检")
    ap.add_argument("--watch", type=int, default=0, metavar="MIN",
                    help="启用寿命测量，每 MIN 分钟探一次（最小 10）")
    a = ap.parse_args()

    print()
    print("微信读书「网页登录态」体检   %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    # 【2026-10-19 更正】先尝试续期：-2012 只说明 90 分钟的会话密钥过期，
    #   不等于"登录失效"。续期成功则无需扫码（旧版脚本在这里直接判死，导致误诊）。
    try:
        import wechat_weread_collector as _wc
        renewed = _wc.renew_session()
        print("  续期 /web/login/renewal：%s"
              % ("✔ 成功（新密钥已写回 cookies.json）" if renewed
                 else "✘ 失败 ⇒ 长期凭据可能已失效，需扫码"))
    except Exception as e:  # noqa: BLE001
        print("  ⚠ 续期调用异常：%s" % str(e)[:140])
    inv = step1_inventory()
    sess = step1b_session_verdict(inv["cookies"])
    print()
    print("=" * 74)
    print("①b ★ 登录态判据（/web/shelf/sync —— 不受 mp 那层风控影响）")
    print("=" * 74)
    print("  HTTP %s   errCode=%s   books=%d" % (sess["status"], sess["code"], sess["books"]))
    if sess["ok"]:
        print("  ✔ **会话有效** ⇒ 若下面 mp 接口报错，那是**接口层**问题，不是会话问题！")
    else:
        print("  ✘ 续期后会话仍无效 ⇒ 长期凭据也失效，需重新扫码（node probe/_weread_login.mjs）")
    res = step2_probe(inv["cookies"])
    if a.watch:
        m = max(10, a.watch)
        if m != a.watch:
            print("\n  （--watch 被抬到最小值 %d 分钟）" % m)
        if res["verdict"] != "ok":
            print("\n  ★ 会话当前已失效，寿命测量无意义 —— 先重登：node probe/_weread_login.mjs")
            return 1
        try:
            step3_lifetime(inv["cookies"], inv["savedAt"], m)
        except KeyboardInterrupt:
            print("\n  （已停止）")
    print()
    print("=" * 74)
    if not sess["ok"]:
        print("结论：**会话无效** → 需重新扫码（node probe/_weread_login.mjs）")
    elif res["verdict"] != "ok":
        print("结论：**会话有效，但 mp 接口不可用**（errCode=%s）" % res["errCode"])
        print("  ⇒ 先看是不是会话密钥过期：采集器已内置 /web/login/renewal 自动续期。")
        print("     旧判据「/web/mp/articles 已废弃（-2041）」**已被推翻**：")
        print("     2026-10-19 实测有效密钥下该接口正常（offset=0→101 篇、offset=20→109 篇）。")
    else:
        print("结论：会话正常、接口可用")
    print("=" * 74)
    return 0 if (sess["ok"] and res["verdict"] == "ok") else 1


if __name__ == "__main__":
    sys.exit(main())
