# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width":1600,"height":950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded"); pg.wait_for_timeout(2500)
    pg.evaluate("() => App.switchTab('foreign')"); pg.wait_for_timeout(1600)
    info = pg.evaluate("""() => {
      return [...document.querySelectorAll('#tab-foreign a')].filter(a=>a.offsetParent && !a.getAttribute('style'))
        .map(a => ({
          text: (a.textContent||'').trim().slice(0,10),
          color: getComputedStyle(a).color,
          html: a.outerHTML.slice(0,110),
          parentCls: (a.parentElement && a.parentElement.className || '').toString().slice(0,40),
          varLink: getComputedStyle(document.documentElement).getPropertyValue('--link').trim(),
        }));
    }""")
    import json
    out = json.dumps(info, ensure_ascii=False, indent=1)
    open("probe/_ux_linkdbg.txt","w",encoding="utf-8").write(out)
    b.close()
print("OK")
