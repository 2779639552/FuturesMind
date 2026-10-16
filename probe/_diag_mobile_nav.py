# -*- coding: utf-8 -*-
"""诊断移动端导航回归：底栏定位为何失效。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    r = pg.evaluate("""() => {
      const nav = document.querySelector('nav#nav');
      const cs = getComputedStyle(nav);
      const rc = nav.getBoundingClientRect();
      const btn = nav.querySelector('button');
      const bcs = getComputedStyle(btn);
      return {
        nav: { position: cs.position, bottom: cs.bottom, left: cs.left, top: cs.top,
               display: cs.display, flexWrap: cs.flexWrap, w: Math.round(rc.width),
               h: Math.round(rc.height), y: Math.round(rc.y), zIndex: cs.zIndex },
        btn: { display: bcs.display, flex: bcs.flex, minHeight: bcs.minHeight,
               padding: bcs.padding, fontSize: bcs.fontSize, whiteSpace: bcs.whiteSpace,
               w: Math.round(btn.getBoundingClientRect().width),
               h: Math.round(btn.getBoundingClientRect().height) },
        header: (() => { const h = document.querySelector('header'); const c = getComputedStyle(h);
                         const r2 = h.getBoundingClientRect();
                         return { height: c.height, flexWrap: c.flexWrap, h: Math.round(r2.height) }; })(),
        // 匹配到几组 nav#nav 相关规则
        mq768: window.matchMedia('(max-width: 768px)').matches,
      };
    }""")
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))
    b.close()
