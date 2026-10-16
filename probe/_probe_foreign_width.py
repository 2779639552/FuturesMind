# -*- coding: utf-8 -*-
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2200)
    pg.evaluate("document.querySelector('nav button[data-tab=\"foreign\"]').click()")
    pg.wait_for_timeout(3000)
    r = pg.evaluate("""() => {
      const tab = document.getElementById('tab-foreign');
      const out = [];
      // tab 顶层子元素
      [...tab.children].forEach(ch => {
        const rc = ch.getBoundingClientRect();
        out.push({ role: 'tab-child', cls: (ch.className||'').toString().slice(0,40),
                   w: Math.round(rc.width), x: Math.round(rc.x), scrollW: ch.scrollWidth });
      });
      // 所有 .card
      [...tab.querySelectorAll('.card')].forEach(c => {
        const rc = c.getBoundingClientRect();
        if (rc.width > 0) out.push({ role: 'card', h3: (c.querySelector('h3')?.textContent||'').trim().slice(0,26),
                                     w: Math.round(rc.width), x: Math.round(rc.x), scrollW: c.scrollWidth,
                                     parentCls: (c.parentElement.className||'').toString().slice(0,34) });
      });
      return { mainW: Math.round(document.querySelector('main').getBoundingClientRect().width),
               mainPad: getComputedStyle(document.querySelector('main')).padding,
               items: out };
    }""")
    print("main 宽:", r["mainW"], " padding:", r["mainPad"])
    for i in r["items"]:
        print(f"  {i['role']:10} x={i['x']:5} w={i['w']:5} scrollW={i.get('scrollW','-'):>6} "
              f"{i.get('h3', i.get('cls',''))!r} parent={i.get('parentCls','')!r}")
    b.close()
