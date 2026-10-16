# -*- coding: utf-8 -*-
"""抓筛选态成品截图 (用内容饱满的仓单页, 便于目视验收)。
运行: python probe/chip_shots.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ux")
os.makedirs(OUT, exist_ok=True)
TAB = "warrant"


def show(m):
    print(m.encode("gbk", errors="replace").decode("gbk"))


def info(pg):
    return pg.evaluate("""(tab)=>{
      const t=document.getElementById('tab-'+tab);
      const vis=[...t.children].filter(e=>!e.classList.contains('sec-hidden'));
      return {n:vis.length, h:vis.map(e=>e.offsetHeight),
              cnt:(t.querySelector('.sec-count-num')||{}).textContent};
    }""", TAB)


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 1080})
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(2500)
    pg.evaluate(f"App.switchTab('{TAB}')")
    pg.wait_for_timeout(3000)

    # 用"选中后高度最大"的两个模块, 保证截图里有实在内容
    cand = pg.evaluate("""(tab)=>{
      const rows = App._secRows[tab]||[];
      return rows.map((r,i)=>({i, label:r.label, h:(r.blocks||[]).reduce((s,b)=>s+ (b.querySelector('.card')?b.querySelector('.card').offsetHeight:0),0)}));
    }""", TAB)
    show("── 候选模块(展开后内容高) ──")
    for c in cand:
        show(f"   [{c['i']}] {c['label'][:26]:28s} ~{c['h']}px")
    top = sorted(cand, key=lambda c: -c["h"])[:2]
    picks = sorted(c["i"] for c in top)
    show(f"\n选中内容最饱满的两个: {picks}")

    # 基准记录
    pg.evaluate("""(tab)=>{
      const list=document.querySelector('#tab-'+tab+' .sec-index-list');
      window._before=[...list.querySelectorAll('.sec-index-item')].map(e=>{
        const r=e.getBoundingClientRect(), lr=list.getBoundingClientRect();
        return {t:(e.textContent||'').trim().slice(1,12), x:r.left-lr.left, y:r.top-lr.top, w:r.width};
      });
    }""", TAB)

    pg.screenshot(path=os.path.join(OUT, "W1_dark_plain.png"), full_page=False)
    show("\n[W1] 暗色 · 筛选前")

    for i in picks:
        pg.evaluate("""([tab,i])=>document.querySelectorAll('#tab-'+tab+' .sec-index-item')[i]
          .dispatchEvent(new MouseEvent('click',{ctrlKey:true,bubbles:true}))""", [TAB, i])
        pg.wait_for_timeout(420)
    pg.wait_for_timeout(800)
    i1 = info(pg)
    show(f"[W2] 暗色 · 选中 {picks} → 可见顶层 {i1['n']} 计数={i1['cnt']} 高度={i1['h']}")
    pg.screenshot(path=os.path.join(OUT, "W2_dark_filtered.png"))

    d = pg.evaluate("""(tab)=>{
      const list=document.querySelector('#tab-'+tab+' .sec-index-list');
      return [...list.querySelectorAll('.sec-index-item')].map((e,i)=>{
        const r=e.getBoundingClientRect(), lr=list.getBoundingClientRect();
        const o=window._before[i]||{};
        return {t:(e.textContent||'').trim().slice(1,12), w:Math.round(r.width),
                dw:Math.round(r.width-(o.w||0)), dx:Math.round((r.left-lr.left)-(o.x||0)),
                dy:Math.round((r.top-lr.top)-(o.y||0)),
                on:e.classList.contains('is-on')};
      });
    }""", TAB)
    show("\n── 标签让位(FLIP) ──")
    for x in d:
        show(f"   {'★选中' if x['on'] else '     '} {x['t']:12s} w={x['w']:4d} Δw={x['dw']:+4d} Δx={x['dx']:+4d} Δy={x['dy']:+4d}")

    pg.evaluate("document.documentElement.setAttribute('data-theme','light')")
    pg.wait_for_timeout(900)
    pg.screenshot(path=os.path.join(OUT, "W3_light_filtered.png"))
    show("\n[W3] 亮色 · 筛选态")
    pg.evaluate("document.documentElement.setAttribute('data-theme','dark')")
    pg.wait_for_timeout(500)

    pg.evaluate("tab=>App._resetSecFilter(tab)", TAB)
    pg.wait_for_timeout(900)
    i2 = info(pg)
    show(f"[W4] 清空后 → 可见顶层 {i2['n']} 计数={i2['cnt']}")
    pg.screenshot(path=os.path.join(OUT, "W4_dark_cleared.png"))

    b.close()
show("\n输出: " + OUT)
