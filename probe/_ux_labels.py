# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright
TABS = ["run","research","sentiment","dashboard","quant","warrant","foreign","warehouse"]
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width":1600,"height":950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded"); pg.wait_for_timeout(2500)
    out = []
    for t in TABS:
        pg.evaluate(f"() => App.switchTab('{t}')"); pg.wait_for_timeout(1500)
        labels = pg.evaluate("""() => [...document.querySelectorAll('#tab-'+'""" + t + """'+' .sec-index-item span:last-child')].map(e=>e.textContent.trim())""")
        out.append(f"[{t}] " + " | ".join(labels))
    # 找出所有以 # 开头的泄露
    leaks = [l for l in out if "#" in l]
    print("\n".join(out))
    print("\n=== LEAKS ===")
    print("\n".join(leaks) if leaks else "none")
    b.close()
