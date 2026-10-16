# -*- coding: utf-8 -*-
"""定位小点击目标 + 残余小字号的具体来源。"""
import pathlib, json, collections
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2000)

    for tab in ("foreign", "warehouse", "research"):
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{tab}\"]').click()")
        pg.wait_for_timeout(2600)
        r = pg.evaluate("""() => {
          const vis = el => el.offsetParent !== null;
          const cands = [...document.querySelectorAll('button, a, select, input, .btn, .sec-index-item, [onclick]')].filter(vis);
          const small = [];
          cands.forEach(e => {
            const rc = e.getBoundingClientRect();
            if (rc.width>0 && rc.height>0 && (rc.height < 32 || rc.width < 24)) {
              small.push({
                tag: e.tagName.toLowerCase(),
                cls: (e.className||'').toString().slice(0,52),
                w: Math.round(rc.width), h: Math.round(rc.height),
                txt: (e.textContent||'').trim().slice(0,20),
                inline: (e.getAttribute('style')||'').slice(0,60),
              });
            }
          });
          // 按 class 归组
          const grp = {};
          small.forEach(s => { const k = s.tag + '.' + s.cls.split(' ')[0]; grp[k] = (grp[k]||0)+1; });
          // 小字号来源
          const smallFonts = [];
          document.querySelectorAll('*').forEach(el => {
            if (!vis(el) || el.children.length) return;
            const t = (el.textContent||'').trim(); if (!t) return;
            const fs = parseFloat(getComputedStyle(el).fontSize);
            if (fs < 12) smallFonts.push({ fs, cls: (el.className||'').toString().slice(0,44), txt: t.slice(0,18) });
          });
          return { total: cands.length, smallN: small.length,
                   groups: Object.entries(grp).sort((a,b)=>b[1]-a[1]).slice(0,10),
                   sample: small.slice(0,8), smallFonts: smallFonts.slice(0,10),
                   smallFontN: smallFonts.length };
        }""")
        print(f"═══ {tab} ═══")
        print(f"  交互元素 {r['total']} 个, <32px 的 {r['smallN']} 个")
        print("  小目标分类:")
        for k, v in r["groups"]:
            print(f"    {v:4}  {k}")
        print("  样例:")
        for s in r["sample"]:
            print(f"    {s['w']}x{s['h']} <{s['tag']} class={s['cls'][:34]!r}> {s['txt']!r} style={s['inline'][:40]!r}")
        print(f"  <12px 文本 {r['smallFontN']} 处，样例:")
        for s in r["smallFonts"]:
            print(f"    {s['fs']}px class={s['cls'][:36]!r} {s['txt']!r}")
        print()
    b.close()
