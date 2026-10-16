# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1600,"height":950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded"); pg.wait_for_timeout(2800)
    pg.evaluate("() => App.switchTab('run')"); pg.wait_for_timeout(2200)
    rows = pg.evaluate("""() => [...document.getElementById('tab-run').children].map((e,i) => ({
        i, tag: e.tagName.toLowerCase(), cls: (e.className||'').toString().slice(0,40),
        id: e.id||'', txt: (e.textContent||'').replace(/\s+/g,' ').trim().slice(0,28),
        isBlock: e.classList.contains('sec-block'),
    }))""")
    out="\n".join(f"{r['i']:>3} {r['tag']:<6} block={str(r['isBlock']):<5} cls={r['cls']:<40} id={r['id']:<18} | {r['txt']}" for r in rows)
    open("probe/_struct2.txt","w",encoding="utf-8").write(out)
    b.close()
print("OK")
