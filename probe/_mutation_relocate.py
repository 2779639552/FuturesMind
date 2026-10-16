# -*- coding: utf-8 -*-
"""`tests/test_multi_logic_relocation.py` 的变异自检（逐条打坏，护栏须变红）。

【防污染】脚本可能被 SIGTERM/超时杀死（finally 不执行）⇒ 源文件残留坏变异。
  故：① 每轮结束**立即**校验 md5；② 装信号处理器尽力还原；③ 落恢复锚到磁盘。
  ⚠ 锚点一律取**单行**（多行锚点在 CRLF 文件上会因换行不匹配而静默跳过 —— 本轮踩过）。
"""
from __future__ import annotations

import hashlib
import json
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
GUARD = "tests/test_multi_logic_relocation.py"

MUTATIONS = [
    # (标签, 锚点(单行), 替换, 期望命中)
    # M1 的锚点须**限定在 loadQuantSignals 域内** —— `const sym = this._fundSym();`
    # 在其它 fund loader 里也出现（实测 10 处），全局替换会打错地方。
    ("M1 品种源退回 #q-variety", "__IN_LOADQUANTSIGNALS__", "", 1),
    ("M2 技术面不排除多空逻辑系",
     "const votes = _allVotes.filter(v => !String(v.book || '').startsWith('多空逻辑'));",
     "const votes = _allVotes;", 1),
    ("M3 基本面不取多空逻辑系",
     "   .filter(v => String(v.book || '').startsWith('多空逻辑'));",
     "   .filter(v => true);", 1),
    ("M4 无内容时留空白（去掉提示）",
     "   return '<div style=\"color:var(--text-muted)\">该品种暂无多空逻辑口径的方向票与扩展读数",
     "   return ''; // 变异", 1),
    ("M5 loadQuant 又调 signals",
     " // 【2026-10-14 迁出】量化信号研判(库存+基差/反转/期限/利润)已迁至**基本面量化页**,",
     " this.loadQuantSignals(); // 变异", 1),
    ("M6 loadQuant 又调 ranking",
     " // 【2026-10-14 迁出】截面排行已移至**总览页**的折叠模块(默认收起, 展开时才拉),",
     " this.loadQuantRanking(0); // 变异", 1),
    ("M7 折叠改成默认展开",
     'id="q-rank-body" data-open="0"', 'id="q-rank-body" data-open="1"', 1),
    ("M8 折叠不用 max-height",
     "max-height:0;overflow:hidden;transition:max-height .28s ease",
     "display:none", 1),
    ("M9 去掉惰性加载",
     "   if (!this._qovRankLoaded) { this._qovRankLoaded = true; this.loadQuantRanking(0); }",
     "   // 变异：去掉惰性加载", 1),
    ("M10 去掉详情分数口径标注",
     "   + ` <span style=\"color:var(--text-muted)\">（技术面口径，与总览「截面排行」同源）</span>`;",
     "   + ''; // 变异", 1),
    ("M11 去掉交互绑定",
     ' onclick="App.toggleQovRank()"', "", 1),
    ("M12 版本号退回 25",
     "_SEC_IDX_VER: '27',", "_SEC_IDX_VER: '25',", 1),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run() -> bool:
    try:
        p = subprocess.run([str(ROOT / "venv/Scripts/python.exe"), "-m", "pytest", GUARD,
                            "-q", "--no-header", "-p", "no:warnings"],
                           capture_output=True, text=True, timeout=240, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return True
    out = p.stdout + p.stderr
    return ("failed" in out) or ("error" in out.lower())


def main() -> int:
    raw = TPL.read_bytes()
    h0 = md5(raw)
    (ROOT / "probe" / "_mutation_anchor_reloc.json").write_text(
        json.dumps({"web_template.html": h0}), encoding="utf-8")
    print("[防污染] 恢复锚已写 (md5=%s)" % h0[:12])

    def _restore(*_a):
        TPL.write_bytes(raw)
        print("\n[中断] 已尽力还原: %s" % ("✔" if md5(TPL.read_bytes()) == h0 else "✘"),
              flush=True)
        raise SystemExit(2)

    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _restore)
        except Exception:
            pass

    print()
    print("=== 基线（应全绿）===")
    base = run()
    print("    基线变红: %s %s" % (base, "★异常，先修基线" if base else "✔"))
    print()
    print("=== 逐条变异 ===")
    killed = total = 0
    for tag, old, new, n in MUTATIONS:
        t = raw.decode("utf-8")
        if old != "__IN_LOADQUANTSIGNALS__":        # 特例不走全局计数
            got = t.count(old)
            if got != n:
                print("    %-28s 锚点命中 %d（期望 %d）—— 跳过" % (tag, got, n))
                continue
        total += 1
        try:
            if old == "__IN_LOADQUANTSIGNALS__":
                # 只改 loadQuantSignals 方法体内的那一处
                a = t.index("async loadQuantSignals() {")
                b_end = t.index(" } catch (e) {", a)
                seg = t[a:b_end]
                seg2 = seg.replace("const sym = this._fundSym();",
                                   "const sym = this._quantSym();", 1)
                assert seg2 != seg, "M1 目标串未命中"
                TPL.write_bytes((t[:a] + seg2 + t[b_end:]).encode("utf-8"))
            else:
                TPL.write_bytes(t.replace(old, new, 1).encode("utf-8"))
            b = run()
        finally:
            TPL.write_bytes(raw)
        if md5(TPL.read_bytes()) != h0:
            print("    [FATAL] 还原失败 —— 立即中止")
            _restore()
        killed += 1 if b else 0
        print("    %-28s %s" % (tag, "击杀 OK" if b else "★存活（护栏有漏洞）"))
    print()
    print("击杀 %d/%d" % (killed, total))
    print("源文件 md5 复原:", md5(TPL.read_bytes()) == h0)
    return 0 if killed == total and total == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
