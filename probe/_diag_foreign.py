# -*- coding: utf-8 -*-
"""定位 foreign 页残留的 80 个小目标 + run 页 10.83px 小字。"""
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2200)

    # ── foreign ──
    pg.evaluate("document.querySelector('nav button[data-tab=\"foreign\"]').click()")
    pg.wait_for_timeout(3000)
    r = pg.evaluate("""() => {
      const vis = el => el.offsetParent !== null;
      const cands = [...document.querySelectorAll('button, a, select, input, [onclick], [href]')].filter(vis);
      const small = [];
      cands.forEach(e => {
        const rc = e.getBoundingClientRect();
        if (rc.width>0 && rc.height>0 && (rc.height < 32 || rc.width < 24)) {
          const par = e.closest('[class]');
          small.push({ tag: e.tagName.toLowerCase(),
            cls: (e.className||'').toString().slice(0,40),
            w: Math.round(rc.width), h: Math.round(rc.height),
            txt: (e.textContent||'').trim().slice(0,18),
            parentCls: par ? (par.className||'').toString().slice(0,40) : '',
            parentId: (e.closest('[id]')||{}).id || '',
          });
        }
      });
      const grp = {};
      small.forEach(s => { const k = s.tag + '|' + s.parentCls; grp[k] = (grp[k]||0)+1; });
      return { total: cands.length, smallN: small.length,
               groups: Object.entries(grp).sort((a,b)=>b[1]-a[1]).slice(0,12),
               sample: small.slice(0,12) };
    }""")
    print("═══ foreign ═══")
    print(f"  交互元素 {r['total']}, <32px 的 {r['smallN']}")
    print("  按「标签|父容器class」分组:")
    for k, v in r["groups"]:
        print(f"    {v:4}  {k}")
    print("  样例:")
    for s in r["sample"]:
        print(f"    {s['w']}x{s['h']} <{s['tag']} cls={s['cls'][:26]!r}> txt={s['txt']!r} parent={s['parentCls'][:26]!r} id={s['parentId']!r}")

    # ── run ──
    pg.evaluate("document.querySelector('nav button[data-tab=\"run\"]').click()")
    pg.wait_for_timeout(2600)
    r2 = pg.evaluate("""() => {
      const vis = el => el.offsetParent !== null;
      const out = [];
      document.querySelectorAll('*').forEach(el => {
        if (!vis(el) || el.children.length) return;
        const t = (el.textContent||'').trim(); if (!t) return;
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs < 12) out.push({ fs, tag: el.tagName.toLowerCase(),
          cls: (el.className||'').toString().slice(0,40), txt: t.slice(0,24),
          inline: (el.getAttribute('style')||'').slice(0,70) });
      });
      return out;
    }""")
    print()
    print(f"═══ run 页 <12px 文本 {len(r2)} 处 ═══")
    for s in r2:
        print(f"    {s['fs']}px <{s['tag']} cls={s['cls'][:30]!r}> {s['txt']!r}")
        print(f"        style={s['inline']!r}")
    b.close()
