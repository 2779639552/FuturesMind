# -*- coding: utf-8 -*-
"""本次两条护栏（`tests/test_quant_overview_refresh.py`）的变异自检。

【防污染】脚本可能被 SIGTERM/超时杀死（finally 不执行）⇒ 源文件残留坏变异。
  故：① 每轮结束**立即**校验 md5；② 装信号处理器尽力还原；③ 落恢复锚到磁盘。
  ⚠ 锚点**一律取单行** —— 多行锚点在 CRLF 文件上会因换行不匹配而静默跳过
    （本次写补丁时刚踩过：模板是 CRLF，脚本源码是 LF）。
"""
from __future__ import annotations

import hashlib
import json
import re
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QM = ROOT / "quant_module.py"          # LF
TPL = ROOT / "web_template.html"       # CRLF
GUARD = "tests/test_quant_overview_refresh.py"

# (标签, 文件, 锚点(单行), 替换, 期望命中)
MUTATIONS = [
    ("M1 后端 TTL 退回 300", QM,
     "_TTL_OVERVIEW = 900     # 【变量】总览整体缓存 15 min",
     "_TTL_OVERVIEW = 300", 1),
    ("M2 新鲜度判断失效(每次都重算)", QM,
     '        fresh = bool(st and st.get("done") and time.time() - st.get("ts", 0) < _TTL_OVERVIEW)',
     "        fresh = False", 1),
    ("M3 去掉在跑中不重复起线程", QM,
     '        if st is not None and not st.get("done") and not force:',
     "        if False:", 1),
    ("M4 前端周期改成 3s", TPL,
     "_QOV_REFRESH_MS: 15 * 60 * 1000,", "_QOV_REFRESH_MS: 3 * 1000,", 1),
    ("M5 算完不排期(只留 3s 轮询)", TPL,
     "   this._qovNextAt = Date.now() + this._QOV_REFRESH_MS;",
     "   this._qovNextAt = 0;", 1),
    ("M6 渐进轮询也改成 15min", TPL,
     "   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), 3000);",
     "   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), this._QOV_REFRESH_MS);", 1),
    ("M7 离开总览不清下次时刻", TPL,
     "  this._qovNextAt = 0;   // 离开总览", "  // 变异", 1),
    ("M8 flex 容器被重复插入", TPL,
     ' <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-top:12px">',
     ' <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-top:12px">'
     '\r\n <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-top:12px">', 1),
    ("M9 align-items 改 stretch", TPL,
     "align-items:flex-start;flex-wrap:wrap", "align-items:stretch;flex-wrap:wrap", 1),
    ("M10 去掉 flex-wrap", TPL,
     "align-items:flex-start;flex-wrap:wrap;margin-top:12px",
     "align-items:flex-start;margin-top:12px", 1),
    ("M11 K线高度被改小", TPL,
     'id="chart-q-kline" style="height:520px"', 'id="chart-q-kline" style="height:300px"', 1),
    ("M12 RSI/MACD 高度回退 150", TPL,
     'id="chart-q-macd" style="height:130px;margin-top:6px"',
     'id="chart-q-macd" style="height:150px;margin-top:6px"', 1),
    ("M13 两卡不再各占一半宽", TPL,
     "flex:1 1 calc(50% - 6px);min-width:420px", "flex:0 0 auto;min-width:420px", 1),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run_guard() -> bool:
    """True = 护栏变红（= 变异被击杀）。"""
    try:
        p = subprocess.run(
            [str(ROOT / "venv/Scripts/python.exe"), "-m", "pytest", GUARD,
             "-q", "--no-header", "-p", "no:warnings"],
            capture_output=True, text=True, timeout=240, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return True
    out = p.stdout + p.stderr
    return ("failed" in out) or ("error" in out.lower())


def main() -> int:
    originals = {f: f.read_bytes() for f in (QM, TPL)}
    h0 = {f: md5(b) for f, b in originals.items()}
    anchor = ROOT / "probe" / "_mutation_anchor_refresh.json"
    anchor.write_text(json.dumps({str(k): v for k, v in h0.items()}), encoding="utf-8")
    print("[防污染] 恢复锚已写: probe/_mutation_anchor_refresh.json")
    for f, h in h0.items():
        print("    %-24s md5=%s" % (f.name, h[:12]))

    def _restore(*_a):
        for f, b in originals.items():
            try:
                f.write_bytes(b)
            except Exception:
                pass
        ok = all(md5(f.read_bytes()) == h0[f] for f in originals)
        print("\n[中断] 已尽力还原: %s" % ("✔" if ok else "✘ 失败！"), flush=True)
        raise SystemExit(2)

    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _restore)
        except Exception:
            pass

    print()
    print("=== 基线（应全绿）===")
    base = run_guard()
    print("    基线变红: %s %s" % (base, "★异常，先修基线" if base else "✔"))
    print()
    print("=== 逐条变异 ===")
    killed = 0
    total = 0
    for tag, f, old, new, n in MUTATIONS:
        raw = originals[f]
        # 按目标文件的 EOL 对齐锚点（源码里写的是 \n）
        eol = "\r\n" if raw.count(b"\r\n") else "\n"
        old_e = old.replace("\n", eol)
        new_e = new.replace("\n", eol)
        txt = raw.decode("utf-8")
        got = txt.count(old_e)
        if got != n:
            print("    %-30s 锚点命中 %d（期望 %d）—— 跳过" % (tag, got, n))
            continue
        total += 1
        try:
            f.write_bytes(txt.replace(old_e, new_e, 1).encode("utf-8"))
            b = run_guard()
        finally:
            f.write_bytes(raw)
        if md5(f.read_bytes()) != h0[f]:
            print("    [FATAL] 还原失败 —— 立即中止")
            _restore()
        killed += 1 if b else 0
        print("    %-30s %s" % (tag, "击杀 OK" if b else "★存活（护栏有漏洞）"))
    print()
    print("击杀 %d/%d" % (killed, total))
    print("源文件 md5 全部复原:", all(md5(f.read_bytes()) == h0[f] for f in originals))
    return 0 if killed == total == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
