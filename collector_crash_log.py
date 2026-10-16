"""采集器子进程的崩溃转储 + 全量输出落盘(2026-09-17)。

【为什么单独成模块】华泰/国君/东证三个采集器都是"一次性子进程",原先**没有任何落盘
日志**:输出只经管道回到 scheduler,而 scheduler 把 `output[-500:]` 写进告警 —— 恰好把
最关键的证据切在两头。2026-09-15~17 三天研报卡在 processing 时,拿到的告警只剩
`_result_or_cancel / result_iterator / ingest_today:509` 这几帧中间调用栈,三个不同
采集源看起来"堆栈一模一样",真凶(崩溃线程当时在哪个原生调用里)完全丢失。

本模块给采集器补三件事,都是进程级、幂等、失败不抛错:
  1) `faulthandler.enable(file=...)` —— 致命错误(access violation / abort / 栈溢出)时
     把**全部线程**的栈写进日志文件。这是唯一能保存原生崩溃现场的手段:普通
     `try/except` 接不住它(进程被直接终止,已 insert 的研报行会永久停在 processing)。
  2) stdout/stderr **同时**写控制台与日志文件(tee)—— 进程被强杀时不留白。
  3) 行缓冲(reconfigure line_buffering)+ 每次 write 后 flush —— 崩溃瞬间已产生的输出
     必须已经落盘。注意:块缓冲(默认走管道时)会让缓冲区内容随进程一起消失,这也是
     09-17 告警里只剩 faulthandler 转储、没有 Python 侧 print 的原因。

【调用约定】各采集器在 `if __name__ == "__main__":` 里、调用 main() **之前**一句:
    from collector_crash_log import enable_crash_log
    enable_crash_log("htfc")
"""

from __future__ import annotations

import faulthandler
import os
import sys
import time
from pathlib import Path

# 【日志保留引用】faulthandler 与 tee 持有的文件对象必须活到进程结束 —— 一旦被 GC,
# 转储会写进已关闭的 fd(表现为"崩溃了但日志文件是空的")。
_KEEP_ALIVE: list = []


class _Tee:
    """把一次 write 同时投给原始流与日志文件,并立即 flush。

    【为什么需要 flush】采集器崩溃多为原生崩溃(无异常可捕获),Python 的缓冲内容会
    整段丢失。逐写 flush 才能保证"崩溃前已经打印的东西"一定在日志里。
    """

    def __init__(self, stream, fh):
        self._stream = stream
        self._fh = fh

    def write(self, data):
        try:
            self._stream.write(data)
        except Exception:  # noqa: BLE001 - 控制台/管道可能已断,不能因此影响采集
            pass
        try:
            self._fh.write(data)
            self._fh.flush()
        except Exception:  # noqa: BLE001
            pass
        return len(data) if isinstance(data, str) else 0

    def flush(self):
        try:
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._fh.flush()
        except Exception:  # noqa: BLE001
            pass

    # 供 print/logging 探测的透传属性
    def __getattr__(self, name):
        return getattr(self._stream, name)

    def isatty(self):
        return False

    def writable(self):
        return True


def enable_crash_log(name: str, *, keep_console: bool = True) -> str:
    """开启致命错误转储 + 全量输出落盘,返回日志文件绝对路径(失败返回 "")。

    【参数】name: 源名(htfc/gtja/dz),决定文件名;
            keep_console: 是否同时保留控制台输出(默认 True;scheduler 仍从管道收输出)。
    【返回】日志文件路径;任何环节失败都返回 "" 且不抛错 —— 取证不能挡住采集。
    """
    try:
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"research_collect_{name}_{time.strftime('%Y%m%d')}.log"
        fh = open(path, "a", encoding="utf-8", buffering=1)  # 行缓冲:崩溃前输出不丢
        _KEEP_ALIVE.append(fh)
        try:
            interp = f"{sys.executable} (pid={os.getpid()})"
        except Exception:  # noqa: BLE001
            interp = "?"
        fh.write(
            f"\n{'=' * 78}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {name} 采集器启动 {interp}\n{'=' * 78}\n"
        )
        # 【1】致命错误转储:唯一的原生崩溃现场保存手段
        try:
            faulthandler.enable(file=fh, all_threads=True)
        except Exception:  # noqa: BLE001
            pass
        # 【2】行缓冲 + tee:崩溃前已打印的内容必须已落盘
        for attr in ("stdout", "stderr"):
            stream = getattr(sys, attr, None)
            if stream is None:
                continue
            try:
                stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except Exception:  # noqa: BLE001
                pass
            if keep_console:
                setattr(sys, attr, _Tee(stream, fh))
            else:
                setattr(sys, attr, fh)
        return str(path)
    except Exception:  # noqa: BLE001 - 取证失败绝不影响采集
        return ""
