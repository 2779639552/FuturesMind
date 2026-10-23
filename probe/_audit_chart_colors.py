# -*- coding: utf-8 -*-
"""全站图表配色审计：找出「颜色太浅」与「图例↔曲线不对应」。

判定规则
--------
1. **非法色值**：series 的 lineStyle/itemStyle 颜色含 `var(`（canvas 解析不了，
   回落成 ECharts 默认色板，表象就是"浅灰"）。
2. **太浅**：颜色亮度 > 0.72 且饱和度 < 0.45（在白底上几乎看不见），或 alpha < 0.5。
3. **图例不对应**：
   a) legend.data 里有名字在 series 里不存在（图例点了没反应 / 颜色来自默认数组）；
   b) 同名 series 出现多次（图例只能对应其中一条）；
   c) 页面自绘 HTML 图例（legend 关闭时）颜色与 series 不一致 —— 只能人工核对，
      这里列出 series 真实颜色供比对。

用法：python probe/_audit_chart_colors.py [--shots]
"""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
THEME = "dark"   # 由 main() 按 --light 覆盖，audit() 里要用

GRAB = r"""
() => {
  const norm = c => {
    if (c == null) return null;
    if (typeof c === 'string') return c;
    if (typeof c === 'object') return c.color || null;   // {type:'linear', colorStops:[]}
    return String(c);
  };
  const parse = c => {
    if (!c || typeof c !== 'string') return null;
    let m = /^#([0-9a-f]{6})$/i.exec(c.trim());
    if (m) {
      const v = parseInt(m[1], 16);
      return { r: (v >> 16) & 255, g: (v >> 8) & 255, b: v & 255, a: 1 };
    }
    m = /^#([0-9a-f]{3})$/i.exec(c.trim());
    if (m) {
      const h = m[1];
      return { r: parseInt(h[0] + h[0], 16), g: parseInt(h[1] + h[1], 16), b: parseInt(h[2] + h[2], 16), a: 1 };
    }
    m = /^rgba?\(([^)]+)\)$/i.exec(c.trim());
    if (m) {
      const p = m[1].split(',').map(x => parseFloat(x));
      return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
    }
    return null;
  };
  const lumSat = c => {
    const o = parse(c);
    if (!o) return null;
    const r = o.r / 255, g = o.g / 255, b = o.b / 255;
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    const sat = mx === 0 ? 0 : (mx - mn) / mx;
    // WCAG 相对亮度：必须先把 sRGB 线性化，否则纯红/纯紫会被严重低估
    const lin = v => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
    const rel = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    return { lum: +lum.toFixed(3), sat: +sat.toFixed(3), a: o.a, rel: +rel.toFixed(4) };
  };

  const out = [];
  // ⚠ App 是 const，不在 window 上（本项目反复踩的同一个坑）⇒ 用 typeof 判全局词法绑定
  const insts = (typeof App !== 'undefined' && App._chartInstances) || {};
  Object.keys(insts).forEach(id => {
    const inst = insts[id];
    if (!inst || inst.isDisposed && inst.isDisposed()) return;
    let opt;
    try { opt = inst.getOption(); } catch (e) { return; }
    const el = document.getElementById(id);
    const vis = el && el.getBoundingClientRect().width > 0;
    const legend = (opt.legend && opt.legend[0]) || null;
    const ldata = legend && legend.data ? legend.data.map(d => (typeof d === 'string' ? d : d.name)) : null;
    const series = (opt.series || []).map(s => {
      const c1 = norm(s.lineStyle && s.lineStyle.color);
      const c2 = norm(s.itemStyle && s.itemStyle.color);
      let c3 = norm(s.areaStyle && s.areaStyle.color);
      const as = s.areaStyle && s.areaStyle.color;
      if (!c3 && as && as.colorStops && as.colorStops[0]) c3 = as.colorStops[0].color;
      if (c3 && /rgba?\([^)]*,\s*0(\.0+)?\)$/.test(c3)) c3 = null;  // 全透明末端忽略
      return {
        name: s.name, type: s.type,
        line: c1, item: c2, area: c3,
        effective: c1 || c2 || null,
        stats: lumSat(c1 || c2),
      };
    });
    const names = series.map(s => s.name);
    const dupNames = names.filter((n, i) => n && names.indexOf(n) !== i);
    out.push({
      id, vis, title: el ? (el.closest('.card') || {}).id || '' : '',
      legendShow: !!(legend && legend.show !== false),
      legendData: ldata,
      legendOrphans: ldata ? ldata.filter(n => !names.includes(n)) : [],
      series,
      dupNames,
      // 图例颜色：ECharts 默认取 series 颜色；只有显式写了 legend.itemStyle 才会错位
      legendItemColor: norm(legend && legend.itemStyle && legend.itemStyle.color),
    });
  });
  return out;
}
"""


def _hue(c):
    """十六进制颜色 → 色相角（0~360）；解析失败返回 None。"""
    import re as _re
    m = _re.match(r"^#([0-9a-f]{6})$", (c or "").strip(), _re.I)
    if not m:
        return None
    v = int(m.group(1), 16)
    r, g, b = ((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return None
    d = mx - mn
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return int(h * 60)


def audit(rows, label):
    issues = []
    for r in rows:
        if not r["vis"]:
            continue
        for s in r["series"]:
            for k in ("line", "item", "area"):
                c = s.get(k)
                if c and "var(" in c:
                    issues.append((label, r["id"], s["name"], "%s 用 CSS 变量(%s)" % (k, c)))
                    break
            st = s.get("stats")
            # 【判据】WCAG 对比度（图形元素要求 ≥3:1）。用简单亮度会把纯红/纯紫误判成"太暗"
            #   （#FF0000 简单亮度 0.21 但对比度 5.2，暗底上非常醒目）。
            if st and st.get("rel") is not None:
                bg_rel = 1.0 if THEME == "light" else 0.0
                hi, lo = max(st["rel"], bg_rel), min(st["rel"], bg_rel)
                contrast = (hi + 0.05) / (lo + 0.05)
                s["contrast"] = round(contrast, 2)
                if contrast < 2.9:
                    issues.append((label, r["id"], s["name"],
                                   "对比度不足 %.2f (%s, 底色 %s)" % (contrast, s.get("effective"),
                                                                  "白" if THEME == "light" else "黑")))
            if st and st["a"] is not None and st["a"] < 0.5:
                issues.append((label, r["id"], s["name"],
                               "透明度过低 alpha=%.2f (%s)" % (st["a"], s.get("effective"))))
        if r["legendOrphans"]:
            issues.append((label, r["id"], "-", "图例项在 series 里不存在: %s" % r["legendOrphans"]))
        # ★ 同图内系列撞色：图例会出现两个一模一样的色块，读者无法对应到曲线
        cols = {}
        for s_ in r["series"]:
            c = s_.get("effective")
            if not c or "var(" in c or c.startswith("function"):
                continue
            cols.setdefault(c, []).append(s_["name"])
        for c, ns in cols.items():
            if len(ns) > 1:
                issues.append((label, r["id"], "-", "同图系列撞色 %s: %s" % (c, ns)))
        # ★ 面积填充与线条色相不一致（线蓝、面积橙 —— 读者以为填充属于另一条序列）
        for s_ in r["series"]:
            lc, ac = s_.get("line"), s_.get("area")
            if lc and ac and not lc.startswith("rgba"):
                h1, h2 = _hue(lc), _hue(ac)
                if h1 is not None and h2 is not None:
                    d = abs(h1 - h2)
                    d = min(d, 360 - d)
                    if d > 40:
                        issues.append((label, r["id"], s_["name"],
                                       "面积色与线色相差 %d° (%s vs %s)" % (d, lc, ac)))
        if r["dupNames"]:
            issues.append((label, r["id"], "-", "同名 series 重复: %s" % sorted(set(r["dupNames"]))))
        if r["legendItemColor"] and r["legendItemColor"] not in ("inherit", ""):
            issues.append((label, r["id"], "-",
                           "legend 整体写死颜色 %s（会盖掉各 series 颜色）" % r["legendItemColor"]))
    return issues


def main():
    shots = "--shots" in sys.argv
    global THEME
    theme = "light" if "--light" in sys.argv else "dark"
    THEME = theme
    allrows, issues = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1680, "height": 1050})
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(1500)
        # 指定主题时先切过去（默认 dark），再开始扫
        if theme:
            cur = pg.evaluate("() => document.documentElement.getAttribute('data-theme')")
            if cur != theme:
                pg.evaluate("() => App.toggleTheme()")
                pg.wait_for_timeout(800)

        def sweep(label, wait=3500):
            pg.wait_for_timeout(wait)
            rows = pg.evaluate(GRAB)
            for r in rows:
                r["_tab"] = label
            allrows.extend(rows)
            issues.extend(audit(rows, label))
            print("[%s] 图表实例 %d 个（可见 %d）" % (label, len(rows), sum(1 for r in rows if r["vis"])))

        for tab in ("run", "research", "quant", "fund", "sentiment", "foreign", "warehouse"):
            pg.evaluate("(t) => App.switchTab(t)", tab)
            sweep(tab, 4000)

        # 两个量化明细页：图表只有进明细才渲染
        for tab, fn in (("quant", "App.qOpenDetail('RB')"), ("fund", "App.fOpenDetail('M')")):
            pg.evaluate("(t) => App.switchTab(t)", tab)
            pg.wait_for_timeout(1500)
            pg.evaluate("() => " + fn)
            sweep(tab + ":detail", 9000)

        if shots:
            import pathlib
            out = pathlib.Path(r"D:\AgentSense\AgentSense\docs\shots")
            for tab in ("fund", "quant", "sentiment"):
                pg.evaluate("(t) => App.switchTab(t)", tab)
                pg.wait_for_timeout(3000)
                pg.screenshot(path=str(out / ("audit_" + tab + ".png")))
        b.close()

    print("\n" + "=" * 78)
    print("问题清单（%d 条）" % len(issues))
    for it in issues:
        print("  [%s] %-28s %-16s %s" % it)

    print("\n" + "=" * 78)
    print("全部 series 实际颜色（按图表）")
    seen = set()
    for r in allrows:
        if not r["vis"] or not r["series"]:
            continue
        key = (r["_tab"], r["id"])
        if key in seen:
            continue
        seen.add(key)
        print("\n%-16s %s   legend=%s" % (r["_tab"], r["id"],
                                          r["legendData"] if r["legendData"] else (r["legendShow"] and "默认" or "无")))
        for s in r["series"][:8]:
            print("   %-18s %-9s %s" % ((s["name"] or "-")[:18], s["type"], s["effective"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
