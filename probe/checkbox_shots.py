# -*- coding: utf-8 -*-
"""抓「常驻复选框」改版前后对比: 未选中态(应看到空心方框) + 选中态。
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright
OUT = "probe/_ux"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1440,"height":620})
    errs=[]
    pg.on("console", lambda m: errs.append(m.text) if m.type=="error" else None)
    pg.goto("http://127.0.0.1:5000/", wait_until="networkidle"); pg.wait_for_timeout(2500)
    pg.evaluate("App.switchTab('warrant')"); pg.wait_for_timeout(3000)
    pg.evaluate("App._resetSecFilter('warrant')"); pg.wait_for_timeout(500)

    # 量方框几何: 未选中态必须是可见的方块(宽高 > 6px 且非透明)
    r = pg.evaluate("""()=>{
      const t=document.getElementById('tab-warrant');
      const ck=t.querySelector('.sec-index-item .sec-ck');
      const cs=getComputedStyle(ck);
      const rc=ck.getBoundingClientRect();
      return {w:Math.round(rc.width), h:Math.round(rc.height),
              border:cs.borderTopWidth+' '+cs.borderTopColor, bg:cs.backgroundColor,
              color:cs.color, metaFont:getComputedStyle(t.querySelector('.sec-index-meta')).fontSize,
              metaColor:getComputedStyle(t.querySelector('.sec-index-meta')).color,
              pill:t.querySelector('.sec-pill').textContent.trim(),
              kbd:!!t.querySelector('.sec-index-meta .sec-kbd')};
    }""")
    print("── 未选中态几何 ──")
    print(f"  复选框方框 {r['w']}x{r['h']}px  描边={r['border']}")
    print(f"  边框可见: {'★是' if r['w']>=8 and r['h']>=8 else '!!太小'}")
    print(f"  提示文字 {r['metaFont']} {r['metaColor']}  含键帽标记={r['kbd']}")
    print(f"  按钮文案='{r['pill']}'")
    pg.locator("#tab-warrant .sec-index").screenshot(path=f"{OUT}/C1_boxes_off_dark.png")
    print("[C1] 暗色 · 未选中(空方框)")

    pg.evaluate("document.documentElement.setAttribute('data-theme','light')")
    pg.wait_for_timeout(600)
    pg.locator("#tab-warrant .sec-index").screenshot(path=f"{OUT}/C2_boxes_off_light.png")
    print("[C2] 亮色 · 未选中(空方框)")
    pg.evaluate("document.documentElement.setAttribute('data-theme','dark')")
    pg.wait_for_timeout(400)

    # 选中 2 个
    chips = pg.locator("#tab-warrant .sec-index-item")
    chips.nth(2).click(modifiers=["Control"]); pg.wait_for_timeout(400)
    chips.nth(3).click(modifiers=["Control"]); pg.wait_for_timeout(700)
    pg.locator("#tab-warrant .sec-index").screenshot(path=f"{OUT}/C3_boxes_on_dark.png")
    print("[C3] 暗色 · 选中(实心✓)")
    r2 = pg.evaluate("""()=>{
      const t=document.getElementById('tab-warrant');
      const on=t.querySelector('.sec-index-item.is-on .sec-ck');
      const cs=getComputedStyle(on);
      return {bg:cs.backgroundColor, color:cs.color, cnt:t.querySelector('.sec-count-num').textContent,
              meta:t.querySelector('.sec-index-meta').textContent};
    }""")
    print(f"  选中方框 bg={r2['bg']} 勾色={r2['color']} 计数={r2['cnt']}")
    print(f"  筛选态提示: {r2['meta']}")
    pg.evaluate("document.documentElement.setAttribute('data-theme','light')")
    pg.wait_for_timeout(600)
    pg.locator("#tab-warrant .sec-index").screenshot(path=f"{OUT}/C4_boxes_on_light.png")
    print("[C4] 亮色 · 选中(实心✓)")
    print(f"\n控制台错误: {errs[:3] if errs else '无'}")
    b.close()
