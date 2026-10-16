# -*- coding: utf-8 -*-
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path("probe/_ux"); OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width":1600,"height":950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    pg.evaluate("() => localStorage.setItem('agentsense-theme','dark')")
    pg.reload(wait_until="domcontentloaded"); pg.wait_for_timeout(2200)
    pg.evaluate("() => App.switchTab('quant')"); pg.wait_for_timeout(3500)
    pg.screenshot(path=str(OUT/"dark_quant.png"))
    # 展开模块说明, 看展开态
    pg.evaluate("() => App.switchTab('research')"); pg.wait_for_timeout(800)
    exp = pg.locator("#tab-research details summary, #tab-research .expander, #tab-research [onclick*='模块']")
    pg.evaluate("""() => { document.querySelectorAll('#tab-research details').forEach(d=>d.open=true); }""")
    pg.wait_for_timeout(600)
    pg.screenshot(path=str(OUT/"dark_research_expanded.png"))
    b.close()
print("OK")
