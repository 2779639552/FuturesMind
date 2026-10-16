# -*- coding: utf-8 -*-
"""折叠/平铺 前后对比截图（供人工审核）。

用法: python probe/_sec_shot.py <tab> <out_prefix>
输出: probe/_shot_<prefix>_<theme>.png

【纪律】切页签用真实点击 nav；主题切换点 .theme-toggle 按钮（App 未挂到 window）。
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

tab = sys.argv[1] if len(sys.argv) > 1 else "run"
prefix = sys.argv[2] if len(sys.argv) > 2 else tab
OUT = Path(__file__).parent

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded", timeout=120000)
    pg.wait_for_timeout(3000)
    pg.click(f'nav button[data-tab="{tab}"]')
    pg.wait_for_timeout(3200)

    for theme in ("light", "dark"):
        # 主题存在 <html data-theme="dark|light"> 属性上（不是 class）；缺省 dark
        cur = pg.evaluate("() => document.documentElement.getAttribute('data-theme') || 'dark'")
        if cur != theme:
            pg.click(".theme-toggle")
            pg.wait_for_timeout(900)
            cur = pg.evaluate("() => document.documentElement.getAttribute('data-theme') || 'dark'")
        if cur != theme:
            print(f"  [warn] 期望 {theme} 实际 {cur}，主题切换可能未生效")
        p = OUT / f"_shot_{prefix}_{theme}.png"
        pg.screenshot(path=str(p), full_page=True)
        info = pg.evaluate("""(t) => { const tab = document.getElementById('tab-'+t);
            return {idx: tab.querySelectorAll('.sec-index').length,
                    blocks: tab.querySelectorAll('.sec-block').length}; }""", tab)
        print(f"  {theme:<6} -> {p.name}  (sec-index={info['idx']} sec-block={info['blocks']})")
    b.close()
