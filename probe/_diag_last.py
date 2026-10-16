# -*- coding: utf-8 -*-
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2200)
    for tab in ("quant", "run", "sentiment"):
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{tab}\"]').click()")
        pg.wait_for_timeout(3200)
        r = pg.evaluate("""() => {
          const vis = el => el.offsetParent !== null;
          const out = [];
          [...document.querySelectorAll('button, a, select, input, [onclick], [href]')].filter(vis).forEach(e => {
            const rc = e.getBoundingClientRect();
            if (rc.width>0 && rc.height>0 && (rc.height < 32 || rc.width < 24)) {
              out.push({ tag: e.tagName.toLowerCase(), w: Math.round(rc.width), h: Math.round(rc.height),
                cls: (e.className||'').toString().slice(0,32), txt: (e.textContent||'').trim().slice(0,14) });
            }
          });
          const grp = {};
          out.forEach(s => { const k = s.tag+'.'+s.cls.split(' ')[0]+' '+s.w+'x'+s.h; grp[k]=(grp[k]||0)+1; });
          return { n: out.length, groups: Object.entries(grp).sort((a,b)=>b[1]-a[1]).slice(0,8) };
        }""")
        print(f"═══ {tab}: {r['n']} 个小目标 ═══")
        for k, v in r["groups"]:
            print(f"   {v:3}  {k}")
    b.close()
