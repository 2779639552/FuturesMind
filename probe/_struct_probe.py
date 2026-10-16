# -*- coding: utf-8 -*-
"""实测：每个 .card 的父容器链 + 当前间距，找出承载卡片的容器类型全集。
设计间距系统必须基于这套事实，而不是猜。
"""
import json, collections
from playwright.sync_api import sync_playwright

TABS = ["run", "research", "sentiment", "quant", "fund", "warrant", "foreign", "warehouse", "gtja"]

parents = collections.Counter()
detail = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2200)

    for t in TABS:
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{t}\"]').click()")
        pg.wait_for_timeout(2400)
        r = pg.evaluate("""() => {
          const out = [];
          document.querySelectorAll('.card').forEach(c => {
            if (!c.offsetParent) return;
            const par = c.parentElement;
            const chain = [];
            let e = par, n = 0;
            while (e && e.tagName !== 'MAIN' && n < 4) {
              chain.push(e.tagName.toLowerCase() + (e.id ? '#'+e.id : '') +
                         (e.className && typeof e.className === 'string'
                           ? '.' + e.className.trim().split(/\\s+/).slice(0,2).join('.') : ''));
              e = e.parentElement; n++;
            }
            const rc = c.getBoundingClientRect();
            const prc = par.getBoundingClientRect();
            const pcs = getComputedStyle(par);
            out.push({
              tree: chain.join(' < '),
              w: Math.round(rc.width), x: Math.round(rc.x),
              parentW: Math.round(prc.width),
              parentDisplay: pcs.display, parentGap: pcs.gap, parentCols: pcs.gridTemplateColumns,
              cardMB: getComputedStyle(c).marginBottom,
              inlineStyle: (c.getAttribute('style') || '').slice(0, 46),
            });
          });
          return { cards: out, mainW: Math.round(document.querySelector('main').getBoundingClientRect().width),
                   mainMaxW: getComputedStyle(document.querySelector('main')).maxWidth,
                   mainPad: getComputedStyle(document.querySelector('main')).padding };
        }""")
        for c in r["cards"]:
            parents[c["tree"]] += 1
            if len(detail) < 14:
                detail.append({"tab": t, **c})
        if t == "run":
            print(f"main: 宽={r['mainW']} maxWidth={r['mainMaxW']} padding={r['mainPad']}")

    b.close()

print()
print("=== 卡片父容器容器链 全集（去掉 id/class 尾号归一）===")
import re
norm = collections.Counter()
for k, v in parents.items():
    key = re.sub(r'#[^ .]+|\.\w+', lambda m: m.group(0), k)
    norm[key] += v
for k, v in norm.most_common(30):
    print(f"  {v:4}  {k}")
print()
print("=== 样例详情 ===")
for d in detail[:14]:
    print(f"[{d['tab']:9}] w={d['w']:5} x={d['x']:4} parentW={d['parentW']:5} disp={d['parentDisplay']:12} "
          f"gap={d['parentGap']:8} cols={str(d['parentCols'])[:34]:34} cardMB={d['cardMB']:8} inline={d['inlineStyle']!r}")
    print(f"           {d['tree']}")
