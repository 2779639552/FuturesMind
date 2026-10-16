# -*- coding: utf-8 -*-
"""v4 复验：① 用**可滚动的长页面**验证控制条不再冻结（上轮用 run 页测，
而 run 页高度 == 视口高度、根本无法滚动，属测试缺陷）；⑤ 控件横排。"""
import json, pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(r"D:\AgentSense\AgentSense\probe\_v4")
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.evaluate("localStorage.setItem('agentsense-theme','light')")
    pg.reload(wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)

    # ① 在多个页签上验证：控制条随页面滚动
    print("═══ ① 控制条是否随页面滚走（多页签实测）═══")
    for tab in ("research", "warrant", "quant", "fund"):
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{tab}\"]').click()")
        pg.wait_for_timeout(4200)
        r = pg.evaluate("""() => {
          const pt = document.querySelector('#tab-' + document.querySelector('nav button.active').dataset.tab + ' .page-tools')
                  || document.querySelector('.tab.active .page-tools');
          const scrollH = document.documentElement.scrollHeight;
          window.scrollTo(0, 0);
          const top0 = pt ? Math.round(pt.getBoundingClientRect().top) : null;
          window.scrollTo(0, 1200);
          const top1 = pt ? Math.round(pt.getBoundingClientRect().top) : null;
          window.scrollTo(0, 0);
          return { pos: pt ? getComputedStyle(pt).position : null, top0, top1, scrollH,
                   canScroll: scrollH > window.innerHeight + 50 };
        }""")
        if not r["canScroll"]:
            print(f"  {tab:<10} 页面不可滚动（高 {r['scrollH']}），跳过")
            continue
        moved = r["top1"] is not None and r["top0"] is not None and r["top1"] < r["top0"] - 100
        print(f"  {tab:<10} position={r['pos']:<8} 滚动前 top={r['top0']:<6} 滚动 1200px 后 top={r['top1']:<6} "
              f"{'OK 已随页面滚走' if moved else '✗ 仍固定'}")

    # ⑤ 研报页控件横排
    print("\n═══ ⑤ 研报每日总结右侧控件 ═══")
    pg.evaluate("document.querySelector('nav button[data-tab=\"research\"]').click()")
    pg.wait_for_timeout(5000)
    ctl = pg.evaluate("""() => {
      const box = document.getElementById('res-daily-type')?.closest('div');
      if (!box) return 'not found';
      const rc = box.getBoundingClientRect();
      const kids = [...box.children].filter(k => k.offsetParent);
      const ys = [...new Set(kids.map(k => Math.round(k.getBoundingClientRect().y)))].sort((a,z)=>a-z);
      return { w: Math.round(rc.width), h: Math.round(rc.height), rows: ys.length,
               kids: kids.map(k => ({ tag: k.tagName.toLowerCase(), id: k.id || '',
                                      w: Math.round(k.getBoundingClientRect().width) })) };
    }""")
    print(f"  控件组 {ctl['w']}x{ctl['h']}px，占 {ctl['rows']} 行")
    for k in ctl["kids"]:
        print(f"    <{k['tag']}> {k['id'] or '-':<20} 宽={k['w']}")
    print(f"  {'OK 已横排（1 行）' if ctl['rows'] == 1 else '✗ 仍占 ' + str(ctl['rows']) + ' 行'}")

    # 放大截图每日总结卡
    try:
        el = pg.query_selector("#res-daily-type")
        card = el.evaluate_handle("e => e.closest('.card').getBoundingClientRect()")
        card_el = pg.query_selector("xpath=//*[@id='res-daily-type']/ancestor::div[contains(@class,'card')][1]")
        if card_el:
            card_el.scroll_into_view_if_needed()
            pg.wait_for_timeout(500)
            card_el.screenshot(path=str(OUT / "zoom_daily_card.png"))
            print("  已截图每日总结卡")
    except Exception as e:
        print("  截图失败:", e)

    pg.screenshot(path=str(OUT / "light_research.png"))
    print(f"\n  控制台错误: {len(errs)}")
    pg.close()
    b.close()
print("->", OUT)
