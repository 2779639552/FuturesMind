"""确认「总览后台任务占锁」是否就是把 K 线拖慢的原因（2026-10-14 取证）

【已有事实】
  · web_app._kline_minute('BR','1') 直接调用 = 5.3s（数据新鲜，最新 14:02）
  · 同一接口经 HTTP = 30~35s（总览空闲时也一样慢）
  · tq_source 无失败熔断 ⇒ 每次取数都要先抢 `_connect_lock` 并等 ~6s 的 SSLError 重试
  · 总览要对 **21 品种 × 4 周期 = 84 个组合**各取一次 ⇒ 串行抢锁 84×6s ≈ 8 分钟

【本脚本判定】
  若 overview 的 done=false（后台仍在算）且此时 kline 慢，
  而 done=true（算完）后 kline 恢复 5~6s
  ⇒ 根因 = 「总览后台任务长时间占锁/占源」，与前端定时器无关。

用法：python probe/_diag_confirm_lock_contention.py [--wait]
    加 --wait 会轮询等 overview 算完后再测一次（最长等 12 分钟）
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5000"
# ★ 必须显式禁用代理：环境里有 http_proxy=127.0.0.1:xxxxx，回环请求经它会失真
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path: str, timeout: int = 300):
    t = time.time()
    try:
        raw = _OPENER.open(BASE + path, timeout=timeout).read().decode("utf-8")
        return json.loads(raw), time.time() - t, None
    except Exception as e:  # noqa: BLE001
        return None, time.time() - t, str(e)[:100]


def ov_state():
    d, el, err = get("/api/quant/overview", timeout=60)
    if err:
        return None, err
    return d, None


def kline_once(tag: str) -> tuple[float, str, str]:
    d, el, err = get("/api/quant/kline?variety=BR&period=1&bars=600", timeout=300)
    if err:
        print("  %-14s ✘ %.1fs  %s" % (tag, el, err))
        return el, "?", "?"
    ks = (d or {}).get("klines") or (d or {}).get("bars") or []
    last = ks[-1].get("date") if ks else "?"
    src = (d or {}).get("source", "?")
    print("  %-14s %.1fs  bars=%-4d source=%-8s last=%s" % (tag, el, len(ks), src, last))
    return el, src, last


print("=== ① 当前总览状态 ===")
d, err = ov_state()
if err:
    print("  查询失败:", err)
    sys.exit(1)
print("  ready=%s/%s  done=%s  缓存=%s" % (d.get("ready"), d.get("total"),
                                        d.get("done"), d.get("cached")))
still_running = not d.get("done")

print()
print("=== ② 此刻测 K 线耗时（3 次）===")
t1 = [kline_once("now-%d" % (i + 1))[0] for i in range(3)]
avg1 = sum(t1) / len(t1)
print("  均值 %.1fs" % avg1)

if still_running and "--wait" in sys.argv:
    print()
    print("=== ③ 轮询等总览后台算完（最长 12 分钟）===")
    deadline = time.time() + 720
    while time.time() < deadline:
        time.sleep(20)
        d2, err2 = ov_state()
        if err2:
            continue
        print("  ready=%s/%s done=%s  (+%ds)" % (
            d2.get("ready"), d2.get("total"), d2.get("done"),
            int(720 - (deadline - time.time()))))
        if d2.get("done"):
            break
    print()
    print("=== ④ 算完后再测 K 线耗时（3 次）===")
    t2 = [kline_once("after-%d" % (i + 1))[0] for i in range(3)]
    avg2 = sum(t2) / len(t2)
    print("  均值 %.1fs" % avg2)
    print()
    print("=== 结论 ===")
    print("  总览计算中 %.1fs  →  算完后 %.1fs  （%.1fx）" % (avg1, avg2, (avg1 / avg2) if avg2 else 0))
    print("  " + ("★根因确认：总览后台任务把 K 线接口拖慢（占锁/占源）"
                   if avg1 > avg2 * 2.5 else
                   "总览不是唯一原因：算完后仍慢 ⇒ 另有瓶颈，需继续查"))
else:
    print()
    print("（总览 %s —— 未加 --wait，跳过等算完的对照）" % ("仍在计算" if still_running else "已算完"))
