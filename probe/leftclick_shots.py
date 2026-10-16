# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import sync_playwright
OUT = "probe/_ux"
def show(m): print(m.encode("gbk","replace").decode("gbk"))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1440,"height":900})
    pg.goto("http://127.0.0.1:5000/", wait_until="networkidle"); pg.wait_for_timeout(2600)
    pg.evaluate("App.switchTab('warrant')"); pg.wait_for_timeout(3000)
    pg.evaluate("App._resetSecFilter('warrant')"); pg.wait_for_timeout(500)

    pg.screenshot(path=f"{OUT}/N1_before.png")
    show("[N1] 未勾选 · 暗色 (全屏)")

    chips = pg.locator("#tab-warrant .sec-index-item")
    chips.nth(2).click(); pg.wait_for_timeout(600)
    chips.nth(3).click(); pg.wait_for_timeout(900)
    st = pg.evaluate("""()=>{
      const t=document.getElementById('tab-warrant');
      return {cnt:t.querySelector('.sec-count-num').textContent,
              meta:t.querySelector('.sec-index-meta').textContent,
              vis:[...t.children].filter(e=>!e.classList.contains('sec-hidden')).length};
    }""")
    show(f"[N2] 左键勾选 2 个 · 暗色  计数={st['cnt']} 可见顶层={st['vis']}")
    show(f"     提示: {st['meta']}")
    pg.screenshot(path=f"{OUT}/N2_picked_dark.png")

    pg.evaluate("document.documentElement.setAttribute('data-theme','light')")
    pg.wait_for_timeout(900)
    pg.screenshot(path=f"{OUT}/N3_picked_light.png")
    show("[N3] 左键勾选 2 个 · 亮色")
    pg.evaluate("document.documentElement.setAttribute('data-theme','dark')")
    pg.wait_for_timeout(500)

    # 单勾选 + 局部
    pg.evaluate("App._resetSecFilter('warrant')"); pg.wait_for_timeout(600)
    chips.nth(2).click(); pg.wait_for_timeout(900)
    pg.locator("#tab-warrant .sec-index").screenshot(path=f"{OUT}/N4_single.png")
    show("[N4] 单勾选 (仅索引条)")
    b.close()
show("输出: " + OUT)
