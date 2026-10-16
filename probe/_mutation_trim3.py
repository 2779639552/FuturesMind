# -*- coding: utf-8 -*-
"""test_trim3_content_decisions 护栏的变异自检（逐条打坏，护栏必须变红）。

【防污染】脚本可能被 SIGTERM/超时杀死（finally 不执行）⇒ 源文件残留坏变异。
  故：① 每轮结束**立即**校验 md5；② 装信号处理器尽力还原；③ 落恢复锚到磁盘。
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
GUARD = "tests/test_page_tools_variety_state.py::test_trim3_content_decisions"

# (标签, 锚点, 替换, 期望命中次数)
MUTATIONS = [
    ("M1 四源卡加回来",
     '<!-- ② 指标总览 + 走势图 + 明细数据',
     '<div class="card" style="margin-top:16px">\r\n <h3>② 四源取数状态</h3>\r\n'
     ' <div id="gt-sources">x</div>\r\n </div>\r\n\r\n <!-- ② 指标总览 + 走势图 + 明细数据', 1),
    ("M2 价格vs仓单图加回来",
     ' <!-- 【2026-10-14 用户要求删除】此处原为「价格 vs 仓单库存」双轴图',
     ' <div style="font-size:0.8125rem">\r\n 价格 vs 仓单库存 <span class="badge badge-info" '
     'id="f-pi-badge"></span>\r\n </div>\r\n <div class="chart-container" id="chart-f-pi"></div>\r\n'
     ' <!-- 【2026-10-14 用户要求删除】此处原为「价格 vs 仓单库存」双轴图', 1),
    ("M3 toggleResearchSection 删掉",
     " toggleResearchSection(pfx) {", " toggleResearchSectionXX(pfx) {", 1),
    ("M4 折叠改用 display:none",
     "style=\"max-height:${wasOpen ? 'none' : '0'};overflow:hidden;transition:max-height .28s ease\"",
     "style=\"display:${wasOpen ? '' : 'none'}\"", 1),
    ("M5 折叠块塞第 2 个 h3",
     'class="badge badge-info">${keys.length} 类 · ${totalPts} 点</span></h3>',
     'class="badge badge-info">${keys.length} 类 · ${totalPts} 点</span></h3>'
     '<h3>多塞的第二个标题</h3>', 1),
    ("M6 去掉 gt-sources 守卫",
     "_renderGtjaSources(d) {\r\n const el = document.getElementById('gt-sources');\r\n if (!el) return;",
     "_renderGtjaSources(d) {\r\n const el = document.getElementById('gt-sources');", 1),
    ("M7 版本号回退 25",
     "_SEC_IDX_VER: '26',", "_SEC_IDX_VER: '25',", 1),
    ("M8 去掉跨渲染状态保持",
     "this._rsOpen = this._rsOpen || {};", "// 变异：状态不保持", 1),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run_guard() -> bool:
    try:
        p = subprocess.run(
            [str(ROOT / "venv/Scripts/python.exe"), "-m", "pytest", GUARD,
             "-q", "--no-header", "-p", "no:warnings"],
            capture_output=True, text=True, timeout=240, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return True
    out = p.stdout + p.stderr
    return ("failed" in out) or ("1 error" in out.lower())


def main() -> int:
    raw = TPL.read_bytes()
    h0 = md5(raw)
    (ROOT / "probe" / "_mutation_anchor_trim3.json").write_text(
        json.dumps({"web_template.html": h0}), encoding="utf-8")
    print("[防污染] 恢复锚已写 (md5=%s)" % h0[:12])

    def _restore(*_a):
        TPL.write_bytes(raw)
        print("\n[中断] 已尽力还原: %s" % ("✔" if md5(TPL.read_bytes()) == h0 else "✘"),
              flush=True)
        raise SystemExit(2)

    for _s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_s, _restore)
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
    for tag, old, new, n in MUTATIONS:
        t = raw.decode("utf-8")
        got = t.count(old)
        if got != n:
            print("    %-26s 锚点命中 %d（期望 %d）—— 跳过" % (tag, got, n))
            continue
        total += 1
        try:
            TPL.write_bytes(t.replace(old, new).encode("utf-8"))
            b = run_guard()
        finally:
            TPL.write_bytes(raw)
        if md5(TPL.read_bytes()) != h0:
            print("    [FATAL] 还原失败 —— 立即中止")
            _restore()
        killed += 1 if b else 0
        print("    %-26s %s" % (tag, "击杀 OK" if b else "★存活（护栏有漏洞）"))
    print()
    print("击杀 %d/%d" % (killed, total))
    print("源文件 md5 复原:", md5(TPL.read_bytes()) == h0)
    return 0 if killed == total and total == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
