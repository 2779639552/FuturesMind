# -*- coding: utf-8 -*-
"""变异自检: 明细数据折叠护栏是否真能抓到回退(2026-10-19)。

做法: 逐条改坏 → 跑 pytest → 必须恰好变红 → 复原 → 核 md5。
"""
import hashlib
import io
import subprocess
import sys

P = "web_template.html"
PY = "./venv/Scripts/python.exe"


def md5(path):
    return hashlib.md5(io.open(path, "rb").read()).hexdigest()


orig = io.open(P, encoding="utf-8", newline="").read()
h0 = md5(P)
print("原始 md5", h0)

MUTS = [
    ("默认带 open 类", 'class="expander" id="gt-detail-expander"',
     'class="expander open" id="gt-detail-expander"',
     "test_expander_defaults_collapsed"),
    ("查看按钮不回传 expandDetail", "App.showGtjaIndicator(${i}, true)",
     "App.showGtjaIndicator(${i})", "test_only_explicit_view_click_expands"),
    ("自动渲染也展开", "if ((d.indicators || []).length) this.showGtjaIndicator(0);",
     "if ((d.indicators || []).length) this.showGtjaIndicator(0, true);",
     "test_only_explicit_view_click_expands"),
    ("表头不绑开关", 'onclick="App.toggleGtjaDetail()"', 'onclick="void 0"',
     "test_detail_header_is_clickable"),
    ("#gt-table 写死 display:none", '<div id="gt-table" style="max-height:420px',
     '<div id="gt-table" style="display:none;max-height:420px',
     "test_gt_table_stays_in_dom_inside_expander_body"),
    ("warehouse tab 多一个 </div>", '<div id="tab-warehouse" class="tab">',
     '<div id="tab-warehouse" class="tab"></div>', "test_warehouse_tab_div_balance"),
    ("条件展开被去掉", "if (expandDetail) this.toggleGtjaDetail(true);",
     "if (false) this.toggleGtjaDetail(true);", "test_show_gtja_indicator_guards_expand"),
]

killed = 0
for name, old, new, target in MUTS:
    assert orig.count(old) == 1, (name, orig.count(old))
    try:
        io.open(P, "w", encoding="utf-8", newline="").write(orig.replace(old, new, 1))
        r = subprocess.run([PY, "-m", "pytest", "tests/test_gtja_detail_collapse.py",
                            "-q", "-k", target], capture_output=True, text=True)
        ok = r.returncode != 0
        print(("KILLED " if ok else "SURVIVED ") + name)
        killed += 1 if ok else 0
    finally:
        io.open(P, "w", encoding="utf-8", newline="").write(orig)

assert md5(P) == h0, "复原失败! md5 不一致"
print("复原 md5 校验通过;  击杀 %d/%d" % (killed, len(MUTS)))
sys.exit(0 if killed == len(MUTS) else 1)
