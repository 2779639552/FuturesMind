# -*- coding: utf-8 -*-
"""定位 warehouse 页的横向溢出元素。"""
from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    b = p.chromium.launch()
    for w in (1920, 1440, 1280):
        pg = b.new_page(viewport={"width": w, "height": 1000})
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.wait_for_timeout(2200)
        pg.evaluate("document.querySelector('nav button[data-tab=\"warehouse\"]').click()")
        pg.wait_for_timeout(3000)
        r = pg.evaluate("""() => {
          const vw = window.innerWidth;
          const out = [];
          document.querySelectorAll('*').forEach(el => {
            const rc = el.getBoundingClientRect();
            if (rc.width === 0 || rc.height === 0) return;
            if (rc.right > vw + 1 || rc.left < -1) {
              const cs = getComputedStyle(el);
              out.push({ tag: el.tagName.toLowerCase(),
                id: el.id || '', cls: (el.className||'').toString().slice(0,44),
                left: Math.round(rc.left), right: Math.round(rc.right), w: Math.round(rc.width),
                overflowX: cs.overflowX, minWidth: cs.minWidth, whiteSpace: cs.whiteSpace,
                parentOverflow: el.parentElement ? getComputedStyle(el.parentElement).overflowX : '',
                txt: (el.textContent||'').trim().slice(0,26) });
            }
          });
          return { vw, scrollW: document.documentElement.scrollWidth,
                   bodyScrollW: document.body.scrollWidth, items: out.slice(0, 22) };
        }""")
        print(f"=== 视口 {w}px === scrollW={r['scrollW']} bodyScrollW={r['bodyScrollW']} 溢出={r['scrollW'] > w+1}")
        for i in r["items"]:
            print(f"   <{i['tag']} id={i['id']!r} class={i['cls']!r}>")
            print(f"      left={i['left']} right={i['right']} w={i['w']} overflowX={i['overflowX']} "
                  f"minWidth={i['minWidth']} ws={i['whiteSpace']} 父overflowX={i['parentOverflow']}")
            print(f"      txt={i['txt']!r}")
        print()
        pg.close()
    b.close()
