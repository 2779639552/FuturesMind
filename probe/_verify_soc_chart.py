# -*- coding: utf-8 -*-
"""只读验收：④ 卡「仓单库存 · 社会库存双口径」图（2026-10-16 用户报"只有仓单的图"）。

【验什么】用户看到的是"图标题写着双口径、图上只有仓单"。本脚本如实核对四件事：
  ① 后端**给了**社会库存序列（含字段与窗口合理性）；
  ② 前端**传了**（根因就在这一行）；
  ③ 前端**画了**（三形态 + 两口径各一轴 + 阶梯）；
  ④ 口径纪律的说明在位（量纲不可比 / 不插值）。
【只读】不改任何文件。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def ck(ok, name, extra=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %-52s %s" % ("OK " if ok else "★  ", name, extra))


def main() -> int:
    import quant_module as qm

    tpl = (ROOT / "web_template.html").read_bytes().decode("utf-8")
    tpl_lf = tpl.replace("\r\n", "\n")
    src = (ROOT / "quant_module.py").read_text(encoding="utf-8")

    print("=== A. 后端：社会库存块给了**序列**吗 ===")
    import tradingagents.dataflows.gtja_api as gapi
    rows = gapi.fetch_dataset(gapi.EP_GTJA_INVENTORY, gapi._GTJA_INV_CACHE_DIR)
    codes = sorted({(r.get("dataType") or "").split("_")[0].strip().lower() for r in (rows or [])})
    n_ok = 0
    for c in [x for x in ("CF", "TA", "BU", "SC", "EG", "PS") if x.lower() in codes]:
        b = qm._inv_social_block(c)
        r = b.get("rows") or []
        ok = b.get("ok") and r and all(k in r[-1] for k in ("date", "qty", "change"))
        n_ok += 1 if ok else 0
        # change 自校验（末点 = 相邻差）
        self_ok = (len(r) < 2) or (r[-1]["change"] == r[-1]["qty"] - r[-2]["qty"])
        ck(ok and self_ok, "%-3s 社会库存序列 %d 点（change 自洽）" % (c, len(r)),
           "%s ~ %s" % (r[0]["date"], r[-1]["date"]) if r else "空")
    ck(n_ok >= 3, "至少 3 个品种拿到序列", "%d 个" % n_ok)
    ck('"rows": rows}' in src, "返回体含 rows（不是只给汇总值）")
    ck("keep = _social_keep(dates)" in src, "窗口按日期切（不硬编码条数）")
    ck(2 <= qm._SOC_ROWS_YEARS <= 5, "窗口年数合理", str(qm._SOC_ROWS_YEARS))

    print("=== B. 前端：**传了**吗（根因所在）===")
    ck("_renderFundInventory(d.inventory || {}, d.social || {})" in tpl, "loadFundInventory 传了 d.social")
    ck("_renderFundInventory(s, so) {" in tpl, "渲染函数签名接收两个口径")

    print("=== C. 前端：**画了**吗 ===")
    ck("name: '社会库存'" in tpl, "有社会库存序列")
    ck("name: '社会库存变化'" in tpl, "有社会库存变化序列")
    ck("grid: [{ left: 64, right: 56, top: 16, height: '40%' }," in tpl, "双栏 grid（上下两栏）")
    ck("type: 'line', xAxisIndex: 0, yAxisIndex: 1, data: _step('qty')," in tpl,
       "★ 社会库存走**另一条轴**（量纲不可比）")
    ck(re.search(r"name: '社会库存'.{0,120}step: 'end'", tpl_lf, re.S) is not None, "阶梯线（step=end）")
    ck("const socChg = new Array(dates.length).fill(null);" in tpl, "社会库存变化柱（稀疏，不摊成每天）")
    ck("if (ckRows.length && !socRows.length)" in tpl, "「只有仓单」分支（BZ/CJ 保持原形态）")
    ck("if (!ckRows.length)" in tpl, "「只有社会库存」分支（降级也画）")
    m = re.search(r'id="chart-f-inv" style="height:(\d+)px', tpl)
    ck(bool(m) and int(m.group(1)) >= 340, "容器高度够放两栏", (m.group(1) + "px") if m else "?")

    print("=== D. 口径纪律的说明在位 ===")
    ck("只看走势与背离" in tpl, "tooltip 提示两口径量纲不同")
    ck("绝不插值" in tpl, "注释写明不插值（防后人顺手改 smooth）")
    ck("阶梯对齐" in tpl, "注释写明阶梯对齐语义")

    print("=== E. 结构未破 ===")
    d = 0
    for mm in re.finditer(r"<div\b|</div>", tpl):
        d += 1 if mm.group(0) == "<div" else -1
    ck(d == 7, "div 深度仍为 +7（与既有基线一致）", "%+d" % d)
    ck(tpl.count(b"\r\n".decode()) > 0 and tpl_lf.count("\n") == tpl.count("\r\n"),
       "模板 EOL 仍为纯 CRLF")
    ck(tpl.count("},,") == 0, "没有 `},,` 残渣（方法段替换的尾逗号问题）")

    print("\nPASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "; ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
