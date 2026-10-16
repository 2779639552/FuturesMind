# -*- coding: utf-8 -*-
"""抓筛选态成品截图 + 标签 FLIP 让位位移量, 供人工目视验收。
运行: python probe/chip_shot_final.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ux")
os.makedirs(OUT, exist_ok=True)


def show(m):
    print(m.encode("gbk", errors="replace").decode("gbk"))


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 1000})

    # 暗色主题
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(2500)
    pg.evaluate("App.switchTab('run')")
    pg.wait_for_timeout(2200)

    # 筛选前
    pg.screenshot(path=os.path.join(OUT, "S1_plain_dark.png"))
    show("[S1] 筛选前 · 暗色")

    # 记录位置 -> 选中 2 个 -> 量位移
    pg.evaluate("""() => {
      const list = document.querySelector('#tab-run .sec-index-list');
      window._before = [...list.querySelectorAll('.sec-index-item')].map(e=>{
        const r=e.getBoundingClientRect(), lr=list.getBoundingClientRect();
        return {t:(e.textContent||'').trim().slice(0,10), x:r.left-lr.left, y:r.top-lr.top, w:r.width};
      });
    }""")
    for i in (3, 4):
        pg.evaluate(
            "i => document.querySelectorAll('#tab-run .sec-index-item')[i]"
            ".dispatchEvent(new MouseEvent('click',{ctrlKey:true,bubbles:true}))", i)
        pg.wait_for_timeout(400)
    pg.wait_for_timeout(600)
    pg.screenshot(path=os.path.join(OUT, "S2_filtered_dark.png"))
    show("[S2] 选中 2 个模块 · 暗色")

    delta = pg.evaluate("""() => {
      const list = document.querySelector('#tab-run .sec-index-list');
      const now = [...list.querySelectorAll('.sec-index-item')].map(e=>{
        const r=e.getBoundingClientRect(), lr=list.getBoundingClientRect();
        return {t:(e.textContent||'').trim().slice(0,10), x:r.left-lr.left, y:r.top-lr.top, w:r.width};
      });
      return now.map((n,i)=>{
        const o = window._before[i]||{};
        return {t:n.t, w:Math.round(n.w), dw:Math.round(n.w-(o.w||0)),
                dx:Math.round(n.x-(o.x||0)), dy:Math.round(n.y-(o.y||0))};
      });
    }""")
    show("\n── 标签位移(含选中态) ──")
    for d in delta:
        mark = "★选中" if abs(d["dw"]) > 4 else "     "
        show(f"   {mark} {d['t']:14s} w={d['w']:4d} Δw={d['dw']:+4d} Δx={d['dx']:+4d} Δy={d['dy']:+4d}")

    # 亮色主题
    pg.evaluate("document.documentElement.setAttribute('data-theme','light')")
    pg.wait_for_timeout(700)
    pg.screenshot(path=os.path.join(OUT, "S3_filtered_light.png"))
    show("\n[S3] 选中 2 个模块 · 亮色")

    # 清空后
    pg.evaluate("App._resetSecFilter('run')")
    pg.wait_for_timeout(700)
    pg.screenshot(path=os.path.join(OUT, "S4_cleared_light.png"))
    show("[S4] 清空筛选后 · 亮色")

    b.close()
show("\n截图输出目录: " + OUT)
