# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1440,"height":1000})
    pg.goto("http://127.0.0.1:5000/", wait_until="networkidle"); pg.wait_for_timeout(2500)
    pg.evaluate("App.switchTab('warrant')"); pg.wait_for_timeout(3000)

    def st(tag):
        r = pg.evaluate("""()=>{
          const t=document.getElementById('tab-warrant');
          return {on:[...t.querySelectorAll('.sec-index-item')].map(c=>c.classList.contains('is-on')),
                  open:[...t.querySelectorAll('.sec-index-item')].map(c=>c.classList.contains('is-open')),
                  cnt:t.querySelector('.sec-count-num').textContent,
                  pillTitle:t.querySelector('.sec-pill').title,
                  pillText:t.querySelector('.sec-pill').textContent.trim()};
        }""")
        print(f"[{tag}] cnt={r['cnt']} on={r['on']} open={r['open']}")
        return r

    a = st("初始")
    print(f"   pill 文案='{a['pillText']}'  tooltip='{a['pillTitle']}'")
    print("\n-- 未筛选状态下, 点击「筛选」pill --")
    pg.locator("#tab-warrant .sec-pill").click()
    pg.wait_for_timeout(800)
    bb = st("点 pill 后")
    print(f"   折叠态是否被改变: {'是 ← 误导! 点它本来不该有任何变化' if bb['open']!=a['open'] else '否'}")

    print("\n-- 提示文字的字号/颜色 (是否够醒目) --")
    m = pg.evaluate("""()=>{
      const el=document.querySelector('#tab-warrant .sec-index-meta');
      const cs=getComputedStyle(el);
      return {fontSize:cs.fontSize, color:cs.color, text:el.textContent};
    }""")
    print(f"   {m['fontSize']}  color={m['color']}")
    print(f"   内容: {m['text']}")

    print("\n-- 标签数量与是否有「可点击筛选」的视觉提示 --")
    v = pg.evaluate("""()=>{
      const chips=[...document.querySelectorAll('#tab-warrant .sec-index-item')];
      const c0=chips[0];
      const cs=getComputedStyle(c0);
      return {n:chips.length, cursor:cs.cursor, title:c0.title,
              hasCheckbox: !!c0.querySelector('input[type=checkbox]'),
              ckMaxW:getComputedStyle(c0.querySelector('.sec-ck')).maxWidth};
    }""")
    print(f"   标签数={v['n']} cursor={v['cursor']} 有复选框={v['hasCheckbox']}")
    print(f"   标签tooltip='{v['title']}'  ✓徽标未选中宽度={v['ckMaxW']}")
    b.close()
