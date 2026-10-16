# -*- coding: utf-8 -*-
"""修 tqsdk「一个进程内只有第一次取数成功」的静默失败（2026-10-14 实测）。

================================ 症状 ================================
```python
tq_source.fetch_daily_bars('RB',120)   # 第 1 次: 120 根  source=tq  7.0s  ✓
tq_source.fetch_daily_bars('RB',120)   # 第 2 次:   0 根  source=tq  0.0s  ✗
# last_error(): TypeError: Logger._log() got an unexpected keyword argument 'product'
```
⚠️ 最阴的是**第 2 次的 `last_source()` 仍报 `tq`**（0 根却标"天勤"）⇒ 调用方
无法从 source 判断这次到底有没有真数据，属**静默失败**。
对 `_kline_minute`（tq 优先）意味着**分钟K 从第 2 次起全空**，
「多周期共振」这类跨多个周期的功能会直接失效。

================================ 根因 ================================
`tq_source._run_isolated_from_logging_hijack` 在导入 tqsdk 前后做快照/还原，
把 `logging.setLoggerClass` 还原成 stdlib `logging.Logger`（这是**必要**的，
否则 shinny_structlog 会重建 `Logger.manager` 让宿主所有 logger 孤儿化，见该函数注释）。

**副作用**：tqsdk 内部随后**新建**的 logger 变回 stdlib `Logger`（首次连接时它已
持有 ShinnyLogger 实例，所以第 1 次没事）。而 tqsdk 记录日志时会带
**structlog 方言字段**（`logger.info(..., product=xxx)`）——
stdlib 的 `Logger._log()` 签名里没有 `product` ⇒ 抛 TypeError ⇒ 该次取数整体失败。

================================ 修法 ================================
给 `logging.Logger._log` 包一层，**丢弃**未知关键字参数。只影响"多余的日志字段"，
不触碰任何业务逻辑；对 structlog 风格与标准调用都兼容。两处都保住：
  · 宿主日志基础设施不被第三方库改写（隔离仍生效）
  · tqsdk 的日志调用不再炸（取数可重复成功）

⚠️ 必须**幂等**（可能被多次导入/重入），用哨兵属性防重复包装。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tradingagents" / "dataflows" / "tq_source.py"

ANCHOR = '''# ── 短连接会话 ─────────────────────────────────────────────────────────

class _Session:'''

NEW = '''# ── 让 stdlib Logger 容忍第三方库的"日志方言"多余字段 ─────────────────────
# 【★ 为什么必须有】上面的快照/还原保护了宿主日志不被改写，但**副作用**是：
#   tqsdk 内部随后**新建**的 logger 变回 stdlib `Logger`，而 tqsdk 记录日志时会带
#   structlog 方言字段（`logger.info(..., product=xxx)`）—— stdlib 的
#   `Logger._log()` 签名里没有 `product` ⇒ 抛 TypeError。
#   实测（2026-10-14）：一个进程里**只有第一次天勤取数成功**，第 2 次起每次都在
#   日志层炸掉；更糟的是**失败时 `last_source()` 仍报 'tq'**（返回 0 根却标"天勤"）
#   ⇒ 调用方无从判断这次到底有没有真数据（静默失败）。
#   `_kline_minute` 是天勤优先，于是**分钟K 从第 2 次起全空**，
#   依赖多个周期的功能（多周期共振）会直接失效。
# 【为什么改 _log 而不是改 getLoggerClass】把 logger 类设回 ShinnyLogger 会重新
#   引入"宿主 logger 被孤儿化"的老问题；只让 `_log` 丢弃未知 kwargs 是**最小改动**：
#   宿主 logger 与 tqsdk logger 都能正常工作，且不改变任何业务语义。
# 【为什么幂等】模块可能被重复导入/重入，用哨兵属性防重复包装（重复包装虽无害，
#   但会让调用栈叠层、排障时难以辨认）。
def _install_kwarg_tolerant_logger() -> None:
    """让 `logging.Logger._log` 丢弃未知关键字参数（幂等）。"""
    import logging as _lg

    if getattr(_lg.Logger, "_agentsense_tolerant_log", False):
        return
    _orig_log = _lg.Logger._log

    def _tolerant_log(self, level, msg, args, exc_info=None, extra=None,
                      stack_info=False, stacklevel=1, **unknown):
        # `product=` 之类的 structlog 方言字段在这里被**安静丢弃** ——
        # 它们只是日志的附加维度，丢掉不影响业务，也不该让取数失败。
        return _orig_log(self, level, msg, args, exc_info, extra, stack_info, stacklevel)

    _lg.Logger._log = _tolerant_log
    _lg.Logger._agentsense_tolerant_log = True


_install_kwarg_tolerant_logger()


# ── 短连接会话 ─────────────────────────────────────────────────────────

class _Session:'''


def main() -> int:
    raw = TARGET.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf != 0 or lf == 0:
        print(f"✘ tq_source.py 应为纯 LF (CRLF={crlf} bareLF={lf})，中止")
        return 2
    src = raw.decode("utf-8")

    print("=" * 74)
    print("修复：tqsdk 二次取数静默失败（Logger._log 拒收 product=）")
    print("=" * 74)

    n = src.count(ANCHOR)
    print(f"\n[1] 锚点预检：命中 {n} 次（期望 1）")
    if n != 1:
        print("✘ 锚点异常 —— 已中止，文件未改动")
        return 3

    src = src.replace(ANCHOR, NEW, 1)
    print("    ✔ 已插入 _install_kwarg_tolerant_logger（含幂等哨兵）")

    print("\n[2] 自检")
    checks = [
        ("容忍函数已定义", "def _install_kwarg_tolerant_logger()" in src),
        ("已调用安装", "\n_install_kwarg_tolerant_logger()\n" in src),
        ("幂等哨兵存在", "_agentsense_tolerant_log" in src),
        ("原有隔离函数仍在", "def _run_isolated_from_logging_hijack(" in src),
        ("_Session 类仍在", "class _Session:" in src),
    ]
    bad = [nm for nm, ok in checks if not ok]
    for nm, ok in checks:
        print(f"    {'OK' if ok else '✘'} {nm}")
    if bad:
        print(f"\n✘ 自检未过：{bad} —— 已中止，文件未改动")
        return 4

    TARGET.write_text(src, encoding="utf-8", newline="")
    chk = TARGET.read_bytes()
    l2 = chk.count(b"\n") - chk.count(b"\r\n")
    print(f"\n[3] 已写入 {len(raw)} → {len(chk)} bytes (+{len(chk) - len(raw)})")
    print(f"    行尾: CRLF={chk.count(chr(13).encode()+chr(10).encode())} bareLF={l2}"
          f" => {'✔ 纯 LF' if chk.count(b'\r\n') == 0 else '✘'}")
    print("\n✔ 修复完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
