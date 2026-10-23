# -*- coding: utf-8 -*-
"""★ 决定性问题: `/web/mp/cover` **不带 cookie** 能不能用?

【为什么要测这一步】
已知三件事:
  ① `bookId` 可由公开的 `__biz` 推导: `MP_WXS_` + base64解码(__biz)  —— 已 4/4 验证;
  ② 正文可从公开文章页取(需把 reviewId 的 token 里 `~`→`_`);
  ③ `reviewId` 来自 `/web/mp/cover`(每号最新一篇)。

⇒ 若 ③ **不需要 cookie**, 则整条链路 = 公开信息 + 公开页面 ⇒ **扫码彻底不需要**。

【对照组设计(★ 关键)】
必须同时测"不带 cookie"与"带 cookie"两条:
  · 都成功   → 免登录可行 ✔
  · 只有带 ck 成功 → 必须 cookie, 扫码不可省
  · **两条都失败** → 大概率是这个 IP 刚触发风控(上一轮抓公开文章页已撞验证码),
                     不能据此断言"接口不行" ⇒ 需隔一段时间复测
⚠ 守节制: 只发 2 个请求, 且中间隔 8 秒。

用法: python probe/_weread_cover_nologin_probe.py
"""
import json
import time
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BASE = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
        "Referer": "https://weread.qq.com/"}
BOOKID = "MP_WXS_3869147758"      # 混沌天成研究（由 Mzg2OTE0Nzc1OA== 推导）
URL = "https://weread.qq.com/web/mp/cover"


def load_cookie_header():
    p = Path.home() / ".tradingagents/weread/cookies.json"
    if not p.exists():
        return None
    j = json.loads(p.read_text(encoding="utf-8"))
    return "; ".join("%s=%s" % (c["name"], c["value"]) for c in (j.get("cookies") or []))


def call(label, cookie=None):
    h = dict(BASE)
    if cookie:
        h["Cookie"] = cookie
    t0 = time.time()
    try:
        r = requests.get(URL, params={"bookId": BOOKID}, headers=h, timeout=45)
    except Exception as e:
        print("  %-16s ★ 异常: %s" % (label, str(e)[:90]))
        return None
    el = time.time() - t0
    txt = r.text
    print("  %-16s HTTP %s  %6d B  %.2fs" % (label, r.status_code, len(txt), el))
    try:
        j = r.json()
    except Exception:
        print("        非 JSON: %s" % txt[:130].replace("\n", " "))
        return None
    code = j.get("errCode")
    if code:
        print("        ✘ errCode=%s  errMsg=%s" % (code, j.get("errMsg") or j.get("errmsg") or ""))
        return None
    # 找 title / reviewId（cover 的返回结构可能包一层）
    def dig(o, depth=0):
        found = {}
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("title", "reviewId", "originalId", "author", "createTime") and isinstance(v, str):
                    found.setdefault(k, v)
                if depth < 3:
                    found.update({k2: v2 for k2, v2 in dig(v, depth + 1).items() if k2 not in found})
        elif isinstance(o, list) and o and depth < 3:
            found.update({k2: v2 for k2, v2 in dig(o[0], depth + 1).items() if k2 not in found})
        return found
    d = dig(j)
    print("        ✔ 成功  顶层键=%s" % sorted(j.keys())[:8])
    for k in ("title", "reviewId", "originalId", "createTime"):
        if k in d:
            print("           %-11s %s" % (k, str(d[k])[:88]))
    rid = d.get("reviewId") or d.get("originalId")
    if rid:
        tok = rid.split(BOOKID + "_", 1)[1] if (BOOKID + "_") in rid else rid
        print("           ★ 公开链接  https://mp.weixin.qq.com/s/%s" % tok.replace("~", "_")[:60])
    return d


def main():
    print("=" * 80)
    print("  /web/mp/cover  是否免登录?   bookId=%s" % BOOKID)
    print("=" * 80)
    ck = load_cookie_header()
    print("  本地 cookie: %s" % ("存在（%d 字符）" % len(ck) if ck else "★ 不存在"))
    print()
    a = call("① 不带 cookie", None)
    time.sleep(8)
    b = call("② 带 cookie(对照)", ck)
    print()
    print("=" * 80)
    if a:
        print("  ★★ 结论: cover **免登录可用** ⇒ 扫码/ cookie 可彻底去掉")
        print("     (bookId 由公开 __biz 推导; 清单走 cover; 正文走公开文章页)")
    elif b:
        print("  ★ 结论: cover 必须带 cookie ⇒ 扫码不可省, 但只需一次(会话可续期)")
    else:
        print("  ⚠ 两条都失败 ⇒ **不能**断言接口不行 —— 极可能是本机 IP 刚触发风控")
        print("     (上一轮抓公开文章页已撞 wappoc 验证码) ⇒ 隔一段时间复测, 或换网络")
    print("=" * 80)


if __name__ == "__main__":
    main()
