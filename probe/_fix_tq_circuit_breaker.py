# -*- coding: utf-8 -*-
"""给 tq_source 加「失败熔断」，修掉「K 线自动刷新失效」的根因（2026-10-14）

═══ 为什么必须熔断（实测取证链）═══
1. 环境：天勤 auth 不可达 —— `SSLError: HTTPSConnectionPool(host='auth.shinnytech.com')`。
   （已验证与本仓库代码无关：带代理 / 不带代理、独立进程都失败。）
2. `tq_source` **没有失败熔断**：每次取数都要 `with _connect_lock:` 抢**全局串行锁**
   并等 ~6s 的 SSLError 重试（`Max retries exceeded`）。
3. 技术面量化的**总览**要对 `21 品种 × 4 周期 = 84 个组合`各取一次数
   ⇒ 串行 84 × 6s ≈ **8 分钟以上持续占锁**。实测总览 16 分钟后 `done` 仍为 False。
4. K 线接口（`_kline_minute` 天勤优先）排在同一把锁后面
   ⇒ 实测经 HTTP **35~50s** 才返回，而**直接调同一函数只要 5.3s**。
5. 前端分钟 K 自动刷新周期是 **30s** ⇒ 请求还没返回就过期 ⇒
   用户看到的就是「**分钟级 K 线的自动更新没有了，还显示十分钟前的数据**」。

═══ 修法 ═══
连接类失败连续达阈值 ⇒ 进入冷却期；冷却期内直接返回 None（**不抢锁、不重试**），
让调用方立刻走 AKShare 回落；冷却结束放行一次"试探"（半开）。
只把**建连失败**计入熔断（当前故障正是建连阶段），避免"某品种无数据"误伤全站。

预期效果：
  · 总览 84 次取数：仅前 2 次各等 ~6s，其余立即回落 AKShare（0.3s）⇒ 总览从 8 分钟降到 ~1 分钟
  · K 线：熔断后每次 ~1s 返回 ⇒ 30s 的自动刷新恢复正常
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tradingagents" / "dataflows" / "tq_source.py"

ANCHOR_STATE = """# 【变量】最近一次错误(供诊断/前端"数据源边界"卡显示原因,不抛给调用方)
_last_error: str | None = None
"""

NEW_STATE = """# 【变量】最近一次错误(供诊断/前端"数据源边界"卡显示原因,不抛给调用方)
_last_error: str | None = None

# ── 失败熔断(2026-10-14) ────────────────────────────────────────────────
# 【为什么】天勤不可用时,每次取数都要抢下面的 `_connect_lock`(全局串行)并等
#   ~6s 的 SSLError 重试;而调用方常按「品种 × 周期」批量取数(实测技术面总览
#   为 21 × 4 = 84 次) ⇒ 串行 84 × 6s ≈ 8 分钟**持续占锁**,把同一把锁后面的
#   交互式请求(分钟 K 线自动刷新)排到 35~50s 才返回 —— 而前端刷新周期仅 30s,
#   于是「请求还没返回就过期」,用户看到的是「K 线不更新、还是十分钟前的数据」。
# 【做法】连接类失败连续达阈值 ⇒ 冷却期内**直接返回 None,不抢锁不重试**,
#   让调用方立刻走 AKShare 回落;冷却结束放行一次"试探"(半开),失败则再冷却。
# 【为什么只记建连失败】当前故障发生在建连(auth token)阶段;把"某品种无数据"
#   之类的业务级空结果也计入,会让单个品种的问题误伤全站。
_FAIL_STREAK = 0            # 【变量】连续建连失败次数
_COOLDOWN_UNTIL = 0.0       # 【变量】冷却截止时间戳(<=now 表示可放行试探)
_FAIL_LOCK = threading.Lock()  # 【变量】熔断状态锁
# 阈值/冷却期可用环境变量覆盖(测试用极小值,避免真的等 2 分钟)
_FAIL_THRESHOLD = int(os.getenv("TQ_FAIL_THRESHOLD") or 2)
_COOLDOWN_SECONDS = float(os.getenv("TQ_COOLDOWN") or 120)


def _circuit_open() -> bool:
    \"\"\"熔断是否生效(冷却期内)。生效时调用方应**立刻**回落其它数据源。\"\"\"
    with _FAIL_LOCK:
        return time.time() < _COOLDOWN_UNTIL


def _record_fail(exc: BaseException | str) -> None:
    \"\"\"记一次建连失败;连续达阈值即开启冷却。\"\"\"
    global _FAIL_STREAK, _COOLDOWN_UNTIL
    with _FAIL_LOCK:
        _FAIL_STREAK += 1
        if _FAIL_STREAK >= _FAIL_THRESHOLD:
            _COOLDOWN_UNTIL = time.time() + _COOLDOWN_SECONDS


def _record_ok() -> None:
    \"\"\"记一次建连成功 ⇒ 清零计数、解除熔断。\"\"\"
    global _FAIL_STREAK, _COOLDOWN_UNTIL
    with _FAIL_LOCK:
        _FAIL_STREAK = 0
        _COOLDOWN_UNTIL = 0.0


def circuit_state() -> dict:
    \"\"\"熔断状态快照(供诊断接口 / 日志 / 测试断言,不改变状态)。\"\"\"
    with _FAIL_LOCK:
        return {
            "open": time.time() < _COOLDOWN_UNTIL,
            "streak": _FAIL_STREAK,
            "threshold": _FAIL_THRESHOLD,
            "cooldown_seconds": _COOLDOWN_SECONDS,
            "cooldown_remaining": max(0.0, _COOLDOWN_UNTIL - time.time()),
        }


def reset_circuit() -> None:
    \"\"\"手动复位熔断(测试用;也让运维能在修好网络后立即恢复,不必等冷却)。\"\"\"
    global _FAIL_STREAK, _COOLDOWN_UNTIL
    with _FAIL_LOCK:
        _FAIL_STREAK = 0
        _COOLDOWN_UNTIL = 0.0
"""

ANCHOR_ENTER = """        self.api = _run_isolated_from_logging_hijack(_connect)
        return self.api
"""

NEW_ENTER = """        try:
            self.api = _run_isolated_from_logging_hijack(_connect)
        except Exception as exc:  # 【异常】建连失败 = 数据源级故障 ⇒ 计入熔断后原样上抛
            _record_fail(exc)
            raise
        _record_ok()  # 建连成功 ⇒ 数据源健康,清零失败计数
        return self.api
"""

# 三处 fetch 入口的 `if not configured(): return None` 后各插一段熔断检查
ANCHOR_FETCH = """    if not configured():
        return None
"""

NEW_FETCH = """    if not configured():
        return None
    # 【失败熔断 2026-10-14】冷却期内**直接返回**,不抢 _connect_lock 也不重试 ——
    # 否则批量取数(如总览 21×4=84 次)会串行占锁数分钟,把交互式 K 线请求拖到几十秒。
    if _circuit_open():
        _set_error("天勤连接熔断中(近期连续失败) → 本轮直接回落其它数据源")
        return None
"""


def main() -> int:
    raw = TARGET.read_bytes()
    assert raw.count(b"\r\n") == 0, "tq_source.py 应为 LF"
    src = raw.decode("utf-8")
    h0 = hashlib.md5(raw).hexdigest()

    # ── 重跑保护：已打过补丁就不重复打 ────────────────────────────
    if "_circuit_open" in src or "TUNING" in src:
        print("⚠️  已包含熔断逻辑（_circuit_open 已存在）—— 本次不重复打补丁")
        print("    如需确认，可跑：python -c \"import tq_source\" 或直接看 circuit_state()")
        return 0

    checks = [
        ("① 熔断状态区", ANCHOR_STATE, 1),
        ("② 建连处记失败/成功", ANCHOR_ENTER, 1),
        ("③ 三处 fetch 入口熔断检查", ANCHOR_FETCH, 3),
    ]
    print("[1] 锚点校验")
    ok = True
    for tag, anchor, want in checks:
        got = src.count(anchor)
        flag = "✔" if got == want else "✘"
        print("    %s %-26s 命中 %d 次（期望 %d）" % (flag, tag, got, want))
        if got != want:
            ok = False
    if not ok:
        print("\n✘ 锚点不匹配 —— **未写入任何内容**（避免半截补丁）")
        return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    bak = ROOT / "backup" / ("tq_source_pre_circuit_%s.py" % ts)
    shutil.copy2(TARGET, bak)
    print("\n[2] 已备份 -> backup/%s" % bak.name)

    out = src
    out = out.replace(ANCHOR_STATE, NEW_STATE, 1)
    out = out.replace(ANCHOR_ENTER, NEW_ENTER, 1)
    out = out.replace(ANCHOR_FETCH, NEW_FETCH, 3)
    TARGET.write_text(out, encoding="utf-8", newline="")

    # ── 自检 ────────────────────────────────────────────────────
    after = TARGET.read_text(encoding="utf-8")
    print("[3] 自检")
    for tag, needle, want in [
        ("_circuit_open 定义", "def _circuit_open()", 1),
        ("_record_fail 定义", "def _record_fail(", 1),
        ("_record_ok 定义", "def _record_ok()", 1),
        ("circuit_state 定义", "def circuit_state()", 1),
        ("建连处调 _record_fail", "_record_fail(exc)", 1),
        ("建连处调 _record_ok", "_record_ok()  # 建连成功", 1),
        ("fetch 熔断检查", "if _circuit_open():", 3),
        ("fetch 熔断提示", "天勤连接熔断中", 3),
    ]:
        got = after.count(needle)
        print("    %s %-24s %d" % ("✔" if got == want else "✘", tag, got))
        assert got == want, "%s 期望 %d 实际 %d" % (tag, want, got)

    nb = TARGET.read_bytes()
    assert nb.count(b"\r\n") == 0, "EOL 被破坏（应保持 LF）"
    print("    ✔ EOL 仍为 LF；原 md5=%s 新 md5=%s" % (h0[:8], hashlib.md5(nb).hexdigest()[:8]))
    print("\n✔ 熔断补丁已落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
