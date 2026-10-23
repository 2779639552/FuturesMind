# -*- coding: utf-8 -*-
"""① 把桌面的重登 bat 改为带 `--force`；② 单请求核对当前登录态（只打不受验证码影响的 shelf/sync）。

【为什么 bat 要带 --force】
修好脚本后，非 --force 路径会把旧 cookie **注入浏览器上下文**；若目标站因此认为"你还登录着"，
登录入口不出现 ⇒ **二维码弹不出来**。bat 的用途就是"重登"，故一律 `--force`（干净上下文）。
保留 `%*` ⇒ `重登公众号读书通道.bat --check-only` 仍可用（此时 --force 无害：CHECK_ONLY 会跳过整段）。

【为什么单请求核对】
今天已多次打 /web/mp/articles，其中一层是风控（-2041）⇒ 遵守"探测自我污染纪律"：
只打 **1 次** `/web/shelf/sync`（外部资料称该接口不受那层验证码影响），用它判断登录态。
"""
from pathlib import Path

# ── ① 改 bat ────────────────────────────────────────────────────
BAT = Path.home() / "Desktop" / "重登公众号读书通道.bat"
raw = BAT.read_bytes()
t = raw.decode("gbk")
OLD = '"%NODE%" "%SCRIPT%" %*\r\n'
assert t.count(OLD) == 1, ("bat anchor", t.count(OLD))
NEW = ('"%NODE%" "%SCRIPT%" --force %*\r\n'
       'rem  ↑ --force: 用干净上下文强制走扫码（否则旧 cookie 会被注入、登录入口可能不出现）\r\n')
t = t.replace(OLD, NEW, 1)
BAT.write_bytes(t.encode("gbk"))
print("  ① bat 已改为带 --force（GBK，%d 字节）" % BAT.stat().st_size)

# ── ② 单请求登录态核对 ──────────────────────────────────────────
import json
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
CK = json.loads((Path.home() / ".tradingagents/weread/cookies.json").read_text(encoding="utf-8"))
hdr = "; ".join("%s=%s" % (c["name"], c["value"]) for c in CK["cookies"])

MEAN = {
    "-2010": "未登录 / 用户不存在（会话没建立）",
    "-2012": "登录超时（cookie 过期）",
    "-2041": "认证 / 风控错误（可能弹验证码；换新鲜 cookie 往往就通）",
    "-2042": "未收录",
}

print()
print("  ② 登录态核对（**只发 1 个请求**：GET /web/shelf/sync）")
r = requests.get("https://weread.qq.com/web/shelf/sync", timeout=40,
                 headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                          "Referer": "https://weread.qq.com/", "Cookie": hdr})
print("     HTTP %s   %d 字节" % (r.status_code, len(r.content)))
try:
    d = r.json() or {}
except Exception:
    d = {}
code = d.get("errCode", d.get("errcode"))
books = d.get("books") or []
print("     errCode = %s   books = %d" % (code if code is not None else "(无)", len(books)))
if code is None:
    print("     ✔ 登录态**有效**（shelf/sync 正常返回）")
    print("     ⇒ 那么 -2012/-2041 只出现在 mp 接口上 ⇒ 属**接口层**问题（风控或已废弃）")
elif str(code) in MEAN:
    print("     ✘ %s" % MEAN[str(code)])
    print("     ⇒ 登录态**已失效**，必须重新扫码")
else:
    print("     ? 未知码：%s" % str(d)[:200])
