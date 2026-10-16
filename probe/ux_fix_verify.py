# -*- coding: utf-8 -*-
"""P0 修复回归验证: ①索引无 '#' 泄露 ②裸链接不再是默认蓝 ③移动端最小字号 ≥11px。"""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
TABS = ["run", "research", "sentiment", "dashboard", "quant", "warrant", "foreign", "warehouse"]
report = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 950})
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)

    # ① 索引标签
    for t in TABS:
        pg.evaluate(f"() => App.switchTab('{t}')")
        pg.wait_for_timeout(1400)
        labels = pg.evaluate(
            "() => [...document.querySelectorAll('#tab-'+'" + t + "'+' .sec-index-item span:last-child')]"
            ".map(e=>e.textContent.trim())")
        bad = [x for x in labels if x.startswith('#') or x.endswith('…')]
        report.append(f"[{t}] {'FAIL ' + str(bad) if bad else 'OK  ' + ' | '.join(labels[:4]) + (' …' if len(labels) > 4 else '')}")

    # ② 裸链接颜色(外文研报页历史任务)
    pg.evaluate("() => App.switchTab('foreign')")
    pg.wait_for_timeout(1400)
    link_colors = pg.evaluate("""() => {
        const seen = new Set();
        document.querySelectorAll('#tab-foreign a').forEach(a => {
            if (!a.offsetParent) return;
            if (a.getAttribute('style')) return;   // 只查"裸"链接
            seen.add(getComputedStyle(a).color);
        });
        return [...seen];
    }""")
    report.append(f"[links] 裸链接颜色: {link_colors or '(页内当前无可见裸链接)'}  期望 rgb(96,165,250)")

    # ③ 桌面回归截图
    pg.evaluate("() => App.switchTab('run')")
    pg.wait_for_timeout(1200)
    pg.screenshot(path="probe/_ux/fix_dark_run.png")

    # ④ 移动端字号
    m = b.new_page(viewport={"width": 390, "height": 844})
    m.goto(BASE, wait_until="domcontentloaded")
    m.wait_for_timeout(2500)
    m.evaluate("() => App.switchTab('run')")
    m.wait_for_timeout(1800)
    sizes = m.evaluate("""() => {
        const s = {};
        document.querySelectorAll('body *').forEach(el => {
            if (!el.offsetParent || el.children.length || !el.textContent.trim()) return;
            const v = Math.round(parseFloat(getComputedStyle(el).fontSize) * 10) / 10;
            s[v] = (s[v] || 0) + 1;
        });
        return Object.entries(s).sort((a, b) => a[0] - b[0]).slice(0, 6);
    }""")
    root_fs = m.evaluate("() => getComputedStyle(document.documentElement).fontSize")
    report.append(f"[mobile] 根字号 {root_fs}  最小字号分布: {sizes}")
    m.screenshot(path="probe/_ux/fix_mobile_run.png")
    b.close()

out = "\n".join(report)
open("probe/_ux_fix_report.txt", "w", encoding="utf-8").write(out)
print(out.encode("gbk", errors="replace").decode("gbk"))
