# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1600,"height":950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded"); pg.wait_for_timeout(2800)
    pg.evaluate("() => App.switchTab('sentiment')"); pg.wait_for_timeout(2500)
    rows = pg.evaluate("""() => [...document.getElementById('tab-sentiment').children].map((e,i) => ({
        i, tag: e.tagName.toLowerCase(), cls: (e.className||'').toString().slice(0,46),
        id: e.id || '', txt: (e.textContent||'').replace(/\s+/g,' ').trim().slice(0,26),
    }))""")
    lines=[f"{r['i']:>3} {r['tag']:<6} cls={r['cls']:<46} id={r['id']:<22} | {r['txt']}" for r in rows]
    out="\n".join(lines)
    open("probe/_struct.txt","w",encoding="utf-8").write(out)
    print(f"共 {len(rows)} 个顶层子元素")
    b.close()
