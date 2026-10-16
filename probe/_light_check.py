# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1440,"height":1000})
    pg.goto("http://127.0.0.1:5000", wait_until="networkidle"); pg.wait_for_timeout(2500)
    pg.evaluate("App.switchTab('run')"); pg.wait_for_timeout(2200)
    for th in ("dark","light"):
        pg.evaluate("t=>document.documentElement.setAttribute('data-theme',t)", th)
        pg.evaluate("App._resetSecFilter('run')"); pg.wait_for_timeout(400)
        for i in (3,4):
            pg.evaluate("i=>document.querySelectorAll('#tab-run .sec-index-item')[i].dispatchEvent(new MouseEvent('click',{ctrlKey:true,bubbles:true}))", i)
            pg.wait_for_timeout(350)
        pg.wait_for_timeout(600)
        r = pg.evaluate("""(t)=>{
          const tab=document.getElementById('tab-run');
          const vis=[...tab.children].filter(e=>!e.classList.contains('sec-hidden'));
          const idx=tab.querySelector('.sec-index');
          return {
            theme:t,
            totalH:document.body.scrollHeight,
            visN:vis.length,
            visH:vis.map(e=>({tag:e.tagName,id:e.id,cls:e.className.slice(0,30),h:e.offsetHeight,
                              txt:(e.textContent||'').replace(/\s+/g,' ').trim().slice(0,22)})),
            chipBg:getComputedStyle(idx.querySelector('.sec-index-item.is-on')).backgroundImage.slice(0,60),
            chipColor:getComputedStyle(idx.querySelector('.sec-index-item.is-on')).color,
            cnt:idx.querySelector('.sec-count-num').textContent,
          };
        }""", th)
        print(f"\n[{r['theme']}] 总高={r['totalH']} 可见顶层={r['visN']} 计数={r['cnt']}")
        print(f"   选中标签底色={r['chipBg']}  文字色={r['chipColor']}")
        for v in r['visH']:
            print(f"     {v['cls']:32s} h={v['h']:5d}  {v['txt']}")
    b.close()
