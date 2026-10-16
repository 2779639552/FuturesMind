# -*- coding: utf-8 -*-
"""诊断：哪些卡片没有铺满可用宽度，以及为什么。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2300)

    for t in ("run", "research", "sentiment", "quant", "foreign", "gtja"):
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{t}\"]').click()")
        pg.wait_for_timeout(2600)
        r = pg.evaluate("""() => {
          const main = document.querySelector('main');
          const avail = main.getBoundingClientRect().width
                      - parseFloat(getComputedStyle(main).paddingLeft)
                      - parseFloat(getComputedStyle(main).paddingRight);
          const out = [];
          document.querySelectorAll('.card').forEach(c => {
            if (!c.offsetParent) return;
            const rc = c.getBoundingClientRect();
            const par = c.parentElement;
            const pcs = getComputedStyle(par);
            const ratio = rc.width / avail;
            if (ratio < 0.9) {
              out.push({
                h3: (c.querySelector('h3')?.textContent || '').trim().slice(0, 22),
                w: Math.round(rc.width), ratio: Math.round(ratio * 100) / 100,
                parent: par.tagName.toLowerCase() + (par.id ? '#'+par.id : '')
                        + '.' + (par.className || '').toString().trim().split(/\\s+/).slice(0,2).join('.'),
                parentDisplay: pcs.display, parentCols: pcs.gridTemplateColumns.slice(0, 46),
                siblings: par.children.length,
                flexBasis: getComputedStyle(c).flexBasis, flexGrow: getComputedStyle(c).flexGrow,
                inline: (c.getAttribute('style') || '').slice(0, 56),
              });
            }
          });
          return { avail: Math.round(avail), items: out.slice(0, 14) };
        }""")
        print(f"=== {t} === 可用宽 {r['avail']}px，未铺满的卡片 {len(r['items'])} 张")
        for i in r["items"]:
            print(f"   {i['ratio']*100:5.0f}% w={i['w']:5} {i['h3']!r}")
            print(f"          父={i['parent']} display={i['parentDisplay']} 兄弟数={i['siblings']} cols={i['parentCols']}")
            print(f"          flexBasis={i['flexBasis']} flexGrow={i['flexGrow']} inline={i['inline']!r}")
        print()
    b.close()
