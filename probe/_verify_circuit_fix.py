"""验证熔断修复效果：K 线是否恢复、总览是否不再长时间占锁（2026-10-14）

对照基线（修复前，实测）：
  · 总览空闲时 K 线仍 34s；总览计算中 44~65s
  · 总览 refresh 后 16 分钟 done 仍为 False（84 次取数串行抢锁）
  · 天勤状态：TqAuth SSLError，每次尝试 ~6s

预期（修复后）：
  · K 线：前 2 次各 ~6s（探测 + 触发熔断），第 3 次起 ~0.3~1s
  · 总览：几十秒内 done=True（熔断后 84 次里 82 次立即回落 AKShare）
  · 总览计算中，K 线仍保持秒级（不再被占锁拖慢）

用法：python probe/_verify_circuit_fix.py
"""
from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:5000"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 回环必须绕过环境代理


def get(path: str, timeout: int = 300):
    t = time.time()
    try:
        raw = _OPENER.open(BASE + path, timeout=timeout).read().decode("utf-8")
        return json.loads(raw), time.time() - t, None
    except Exception as e:  # noqa: BLE001
        return None, time.time() - t, str(e)[:100]


def kline(tag: str) -> float:
    d, el, err = get("/api/quant/kline?variety=BR&period=1&bars=600")
    if err:
        print("  %-16s ✘ %.1fs  %s" % (tag, el, err))
        return el
    ks = (d or {}).get("klines") or (d or {}).get("bars") or []
    print("  %-16s %6.2fs  bars=%-4d source=%-8s last=%s" % (
        tag, el, len(ks), (d or {}).get("source", "?"), ks[-1].get("date") if ks else "?"))
    return el


print("=== ① K 线接口：逐次耗时（前 2 次付探测成本，之后应立刻回落）===")
t = [kline("kline-%d" % (i + 1)) for i in range(4)]
print("  最后一次 %.2fs  （修复前基线 34~50s）" % t[-1])

print()
print("=== ② 触发总览全池重算，观察多久算完 ===")
t0 = time.time()
d, el, err = get("/api/quant/overview?refresh=1", timeout=60)
print("  触发返回 %.2fs  ready=%s/%s done=%s" % (el, (d or {}).get("ready"),
                                            (d or {}).get("total"), (d or {}).get("done")))
last = None
for i in range(40):  # 最多等 8 分钟
    time.sleep(12)
    d2, _, e2 = get("/api/quant/overview", timeout=60)
    if e2:
        continue
    print("    +%3ds  ready=%s/%s done=%s" % ((i + 1) * 12, d2.get("ready"),
                                             d2.get("total"), d2.get("done")))
    if d2.get("done"):
        last = time.time() - t0
        break
if last:
    print("  ⇒ 总览全池算完耗时 %.0fs（修复前 16 分钟仍未完成）" % last)
else:
    print("  ⇒ 8 分钟内仍未 done（需继续查）")

print()
print("=== ③ 总览计算中，K 线是否仍保持秒级 ===")
during = [kline("during-%d" % (i + 1)) for i in range(2)]
print("  均值 %.2fs（修复前同场景 44~65s）" % (sum(during) / len(during)))

print()
print("=== 结论 ===")
verdict = t[-1] < 3.0 and (sum(during) / len(during)) < 5.0
print("  K 线修复后 %.2fs ；总览计算中 %.2fs" % (t[-1], sum(during) / len(during)))
print("  " + ("✔ 修复生效：K 线恢复秒级，总览不再把 K 线拖到几十秒" if verdict
              else "✘ 仍偏慢，需继续排查"))
