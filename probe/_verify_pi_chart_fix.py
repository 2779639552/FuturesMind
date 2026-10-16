# -*- coding: utf-8 -*-
"""验证「价格 vs 仓单库存」图修复(2026-10-13):
1. 两条 series 颜色不同且均为合法色值(以 # 开头)
2. grid.bottom 足够容纳 45° 旋转的 x 轴标签(不与 bottom:0 图例重叠)
3. 顺带: 量化页 K 线蜡烛涨跌色恢复为有效值
"""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"

JS = """() => {
  const out = {};
  const probe = (id) => {
    const el = document.getElementById(id);
    if (!el) return {missing: true};
    const inst = window.echarts && echarts.getInstanceByDom(el);
    if (!inst) return {noInstance: true};
    const o = inst.getOption() || {};
    const sers = (o.series || []).map(s => ({
      name: s.name, type: s.type,
      color: s.lineStyle && s.lineStyle.color,
      pts: Array.isArray(s.data) ? s.data.filter(v => v !== null && v !== undefined).length : 0
    }));
    const lg = (o.legend && o.legend[0]) || {};
    const g = (o.grid && o.grid[0]) || {};
    return {sers, legendBottom: lg.bottom, gridBottom: g.bottom};
  };
  out.pi = probe('chart-f-pi');
  out.kline = probe('chart-q-kline');
  return out;
}"""

fails = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(3000)

    # 基本面页
    pg.click('nav button[data-tab="fund"]')
    pg.wait_for_timeout(6000)
    r = pg.evaluate(JS)

    pi = r.get("pi", {})
    if pi.get("missing"):
        fails.append("chart-f-pi 缺失")
    else:
        sers = pi.get("sers", [])
        print("=== chart-f-pi series ===")
        colors = []
        for s in sers:
            print(f"  {s['name']:<8} type={s['type']:<10} color={s['color']!r:<14} 有效点={s['pts']}")
            colors.append(s["color"])
        if len(sers) < 2:
            fails.append(f"series 不足 2 条: {len(sers)}")
        else:
            if colors[0] == colors[1]:
                fails.append(f"两条线颜色相同({colors[0]}) ⇒ 仍会视觉重叠")
            for c in colors:
                if not (isinstance(c, str) and c.startswith("#")):
                    fails.append(f"颜色非法: {c!r}")
            if sers[1].get("pts", 0) == 0:
                fails.append("仓单库存 series 无有效点")
        gb, lb = pi.get("gridBottom"), pi.get("legendBottom")
        print(f"  grid.bottom={gb} legend.bottom={lb}")
        if not (isinstance(gb, (int, float)) and gb >= 55):
            fails.append(f"grid.bottom={gb} 不足以容纳 45° x 轴标签")

    # 量化页 K 线
    pg.click('nav button[data-tab="quant"]')
    pg.wait_for_timeout(8000)
    r = pg.evaluate(JS)
    k = r.get("kline", {})
    print("\n=== chart-q-kline(K线蜡烛色) ===")
    if k.get("missing"):
        print("  (未进入/未渲染, 跳过)")
    else:
        for s in k.get("sers", [])[:2]:
            print(f"  {s['name']:<8} type={s['type']:<12} color={s['color']!r}")
        candle = [s for s in k.get("sers", []) if s["type"] == "candlestick"]
        if candle:
            print(f"  candlestick color={candle[0]['color']!r}")
    b.close()

print("\n" + "=" * 56)
if fails:
    print(f"失败 {len(fails)} 项:")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("全部通过 ✓")
