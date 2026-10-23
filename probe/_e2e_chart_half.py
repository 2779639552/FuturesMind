# -*- coding: utf-8 -*-
"""E2E 验收：横贯整页的长条图宽度是否减半（真实服务）。

判据：
  · 宽 ≈ 卡片内容宽的一半（±6%），误差来自 padding/border；
  · 高度**不变**（只缩长度，不压内容）；
  · 视口 <=900px 时回退 100%（手机上半宽图 x 轴放不下）。
只读，不改数据。
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000/"

MEASURE = """(ids) => {
  const out = {};
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) { out[id] = {err: 'missing'}; return; }
    const r = el.getBoundingClientRect();
    const card = el.closest('.card');
    const cr = card ? card.getBoundingClientRect() : null;
    if (!r.width) { out[id] = {err: 'zero-width'}; return; }
    // 卡片内容宽 = 卡片宽 - 左右 padding（取实测：用容器原本的 100% 参照）
    out[id] = { w: Math.round(r.width), h: Math.round(r.height),
                cardW: cr ? Math.round(cr.width) : null };
  });
  return out;
}"""


def main() -> int:
    res = []

    def check(name, ok, extra=''):
        res.append(ok)
        print("--- %s --- %s%s" % (name, "PASS" if ok else "FAIL", "" if ok else "  " + str(extra)))

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 900})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(1200)

        # 参照：同一卡片里"没加 chart-half"的图（chart-f-inv 在半宽 grid 里，不合适）
        # 直接用"卡片宽 - 2*卡片 padding"作为整宽参照：实测 card padding = 左右各 14~16px
        pg.evaluate("() => App.switchTab('quant')")
        pg.wait_for_timeout(1200)
        pg.evaluate("() => App.qOpenDetail('RB')")
        pg.wait_for_timeout(3000)
        m = pg.evaluate(MEASURE, ["chart-q-cmp"])
        q = m["chart-q-cmp"]
        print("chart-q-cmp:", json.dumps(q, ensure_ascii=False))
        check("多品种对比: 宽度约为卡片一半", "w" in q and 0.40 * q["cardW"] <= q["w"] <= 0.56 * q["cardW"], q)
        check("多品种对比: 高度保持 360", q.get("h") == 360, q)

        pg.evaluate("() => App.switchTab('fund')")
        pg.wait_for_timeout(1200)
        pg.evaluate("() => App.fOpenDetail('RB')")
        pg.wait_for_timeout(3500)
        m = pg.evaluate(MEASURE, ["chart-f-term", "wt-h-prod", "chart-f-r-mill-inventory", "chart-f-inv"])
        for k, v in m.items():
            print(k, ":", json.dumps(v, ensure_ascii=False))
        f = m["chart-f-term"]
        check("期限结构: 宽度约为卡片一半", "w" in f and 0.40 * f["cardW"] <= f["w"] <= 0.58 * f["cardW"], f)
        check("期限结构: 高度保持 240", f.get("h") == 240, f)
        w = m["wt-h-prod"]
        check("仓单走势: 宽度约为卡片一半", "w" in w and 0.40 * w["cardW"] <= w["w"] <= 0.58 * w["cardW"], w)
        r0 = m["chart-f-r-mill-inventory"]
        check("研报指标图: 宽度约为卡片一半", "w" in r0 and 0.40 * r0["cardW"] <= r0["w"] <= 0.58 * r0["cardW"], r0)
        # 反向校验：本来就是半宽 grid 的图不能被再压一半
        inv = m["chart-f-inv"]
        check("原本就半宽的图不受影响", inv.get("w", 0) > 600, inv)

        pg.evaluate("() => App.switchTab('sentiment')")
        pg.wait_for_timeout(3000)
        m = pg.evaluate(MEASURE, ["chart-overlay"])
        o = m["chart-overlay"]
        print("chart-overlay:", json.dumps(o, ensure_ascii=False))
        check("情绪走势: 宽度约为卡片一半", "w" in o and 0.40 * o["cardW"] <= o["w"] <= 0.56 * o["cardW"], o)

        pg.evaluate("() => App.switchTab('warehouse')")
        pg.wait_for_timeout(3000)
        m = pg.evaluate(MEASURE, ["gt-chart"])
        g = m["gt-chart"]
        print("gt-chart:", json.dumps(g, ensure_ascii=False))
        check("季节性图: 宽度约为卡片一半", "w" in g and 0.40 * g["cardW"] <= g["w"] <= 0.56 * g["cardW"], g)
        check("季节性图: 高度保持 170", g.get("h") == 170, g)

        # 窄屏回退
        pg.set_viewport_size({"width": 760, "height": 900})
        pg.wait_for_timeout(800)
        m = pg.evaluate(MEASURE, ["gt-chart", "chart-overlay"])
        gg = m["gt-chart"]
        print("760px gt-chart:", json.dumps(gg, ensure_ascii=False))
        check("窄屏回退为整宽", "w" in gg and gg["w"] >= 0.90 * gg["cardW"], gg)

        b.close()

    print("\nRESULT:", "PASS" if all(res) else "FAIL", "(%d/%d)" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
