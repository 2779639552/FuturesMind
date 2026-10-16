# -*- coding: utf-8 -*-
"""把每个实际渲染字号反查到「是哪些元素 / 由哪条规则供给」，找出离群值。"""
import collections, json
from playwright.sync_api import sync_playwright

TABS = ["run", "research", "sentiment", "quant", "fund", "warrant", "foreign", "warehouse", "gtja"]

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2200)

    agg = collections.defaultdict(list)
    for t in TABS:
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{t}\"]').click()")
        pg.wait_for_timeout(2400)
        r = pg.evaluate("""() => {
          const vis = el => el.offsetParent !== null;
          const out = [];
          document.querySelectorAll('*').forEach(el => {
            if (!vis(el)) return;
            const txt = (el.textContent || '').trim();
            if (!txt) return;
            const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
            if (!own) return;
            const cs = getComputedStyle(el);
            out.push({ fs: cs.fontSize, fw: cs.fontWeight,
                       tag: el.tagName.toLowerCase(),
                       id: el.id || '',
                       cls: (el.className || '').toString().trim().slice(0, 40),
                       inline: (el.getAttribute('style') || '').slice(0, 40),
                       txt: txt.slice(0, 22) });
          });
          return out;
        }""")
        for x in r:
            agg[x["fs"]].append({**x, "tab": t})

    b.close()

print("=== 各字号实际用量（按 px 升序）===")
for fs in sorted(agg, key=lambda s: float(s.replace("px", ""))):
    items = agg[fs]
    print(f"\n{fs:>12}  共 {len(items):4} 处")
    # 按 class 归组看来源
    grp = collections.Counter(
        (i["tag"] + ("#" + i["id"] if i["id"] else "") + ("." + i["cls"].split(" ")[0] if i["cls"] else ""))
        for i in items)
    for k, v in grp.most_common(6):
        ex = next(i for i in items
                  if (i["tag"] + ("#" + i["id"] if i["id"] else "")
                      + ("." + i["cls"].split(" ")[0] if i["cls"] else "")) == k)
        print(f"    {v:4}  {k:46} 例: {ex['txt']!r} inline={ex['inline']!r} fw={ex['fw']}")
