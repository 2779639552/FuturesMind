# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright
rep=[]
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1600,"height":950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded"); pg.wait_for_timeout(2800)
    pg.evaluate("() => App.switchTab('sentiment')"); pg.wait_for_timeout(2500)
    def snap(tag):
        return pg.evaluate("() => App._lastFlip || null")
    pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-index-item')[0].click()")
    pg.wait_for_timeout(500)
    rep.append(f"[第1次点击] _lastFlip={snap('a')}")
    pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-index-item')[5].click()")
    pg.wait_for_timeout(500)
    rep.append(f"[第2次点击] _lastFlip={snap('b')}")
    pg.evaluate("() => document.querySelector('#tab-sentiment .sec-pill').click()")   # 清空(隐藏的模块重新出现→大幅让位)
    pg.wait_for_timeout(600)
    rep.append(f"[清空筛选] _lastFlip={snap('c')}  ← 这一步位移最大, 应可见明显 dy")
    # 也采样中间帧 transform
    pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-index-item')[2].click()")
    fr=[]
    for k in range(4):
        pg.wait_for_timeout(40)
        fr.append(pg.evaluate("() => [...document.querySelectorAll('#tab-sentiment .sec-index-item')].map(e=>getComputedStyle(e).transform)"))
    rep.append("[中间帧 transform 采样]")
    for k,f in enumerate(fr):
        non=[i for i,v in enumerate(f) if v not in ('none','matrix(1, 0, 0, 1, 0, 0)')]
        vals=[f[i] for i in non[:3]]
        rep.append(f"  帧{k+1}: 补位中={non[:8]}... 前几个矩阵={vals}")
    b.close()
out="\n".join(rep); open("probe/_flip_dbg.txt","w",encoding="utf-8").write(out)
print(out.encode("gbk",errors="replace").decode("gbk"))
