"""对照实验：总览后台计算是否阻塞 K 线接口（2026-10-14 用户报障根因取证）

【待验假设】
  天勤网络不可用(SSLError) + tq_source **无失败熔断** + `_connect_lock` 全局串行
  ⇒ 总览的「21 品种 × 4 周期 = 84 个组合」每个都要抢锁并等 ~6s 重试失败
  ⇒ 84 × 6s ≈ 504s 的串行占用 ⇒ 同一把锁后面的 K 线请求被排队
  ⇒ 前端 30s 一轮的刷新请求「还没返回就过期」⇒ 用户看到「K 线不更新」

【判据】
  对照组 A（总览空闲）：kline 耗时应 ≈ 1~7s
  对照组 B（总览 refresh 触发全池后台计算后立即请求）：若耗时飙到 30s+
  ⇒ 假设成立，根因是「84 次无熔断的天勤重试占满了串行锁」

用法：python probe/_diag_overview_blocks_kline.py
"""
from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:5000"


def get(path: str, timeout: int = 300):
    t = time.time()
    try:
        req = urllib.request.Request(BASE + path, headers={"User-Agent": "probe"})
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")
        return json.loads(raw), time.time() - t, None
    except Exception as e:  # noqa: BLE001
        return None, time.time() - t, str(e)[:120]


def kline(tag: str) -> float:
    d, el, err = get("/api/quant/kline?variety=BR&period=1&days=1&bars=600", timeout=300)
    if err:
        print("  %-12s ✘ %.1fs  %s" % (tag, el, err))
        return el
    ks = (d or {}).get("klines") or (d or {}).get("bars") or []
    last = (ks[-1].get("date") if ks else "?")
    print("  %-12s %.1fs  bars=%-4d source=%-8s last=%s" % (
        tag, el, len(ks), (d or {}).get("source", "?"), last))
    return el


print("=== A. 总览空闲时的 K 线耗时（基线）===")
a = [kline("A%d" % (i + 1)) for i in range(2)]

print()
print("=== B. 触发总览全池 refresh，随即测 K 线耗时 ===")
# refresh=1 ⇒ 后端丢弃缓存、重新对全池 21 品种 × 4 周期开算（后台线程）
d, el, err = get("/api/quant/overview?refresh=1", timeout=60)
print("  触发 overview?refresh=1 -> %.2fs  ok=%s ready=%s/%s done=%s  err=%s" % (
    el, (d or {}).get("ok"), (d or {}).get("ready"), (d or {}).get("total"),
    (d or {}).get("done"), err))
b = [kline("B%d" % (i + 1)) for i in range(2)]

print()
print("=== 对照结论 ===")
avg_a = sum(a) / len(a)
avg_b = sum(b) / len(b)
print("  总览空闲均值 = %.1fs  |  总览计算中均值 = %.1fs  |  劣化倍数 = %.1fx"
      % (avg_a, avg_b, (avg_b / avg_a) if avg_a > 0 else 0))
print("  ⇒ " + ("★假设成立：总览后台计算显著阻塞 K 线接口（根因确认）"
                 if avg_b > max(10.0, avg_a * 3) else
                 "假设不成立：K 线变慢另有原因（需继续查）"))
