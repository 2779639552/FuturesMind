# -*- coding: utf-8 -*-
"""最终交付截图：关键页签双主题 + 移动端。"""
import pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(r"D:\AgentSense\AgentSense\probe\_final")
OUT.mkdir(exist_ok=True)
TABS = ["run", "research", "quant", "fund", "warrant", "foreign", "warehouse", "sentiment", "gtja"]

with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.evaluate("localStorage.setItem('agentsense-theme', '%s')" % theme)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(2400)
        for t in TABS:
            pg.evaluate(f"document.querySelector('nav button[data-tab=\"{t}\"]').click()")
            pg.wait_for_timeout(3000)
            pg.screenshot(path=str(OUT / f"{theme}_{t}.png"))
        pg.close()
    # 移动端
    for tab in ("run", "research"):
        pg = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                        is_mobile=True, has_touch=True)
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.wait_for_timeout(2200)
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{tab}\"]').click()")
        pg.wait_for_timeout(3000)
        pg.screenshot(path=str(OUT / f"mobile_{tab}.png"))
        pg.close()
    b.close()
print("done ->", OUT)
