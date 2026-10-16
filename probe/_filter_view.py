# -*- coding: utf-8 -*-
"""筛选后残留检查: 统计 tab 顶层可见元素, 确认只剩索引条 + 选中模块 + 其段头。"""
import sys
from playwright.sync_api import sync_playwright
TAB = sys.argv[1] if len(sys.argv) > 1 else "sentiment"
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 2
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1600,"height":950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded"); pg.wait_for_timeout(2800)
    pg.evaluate(f"() => App.switchTab('{TAB}')"); pg.wait_for_timeout(2500)
    pg.evaluate(f"() => document.querySelectorAll('#tab-{TAB} .sec-index-item')[{IDX}].click()")
    pg.wait_for_timeout(800)
    res = pg.evaluate(f"""() => {{
        const tab = document.getElementById('tab-{TAB}');
        const kids = [...tab.children];
        const vis = kids.filter(e => !e.classList.contains('sec-hidden')).map(e => ({{
            cls: (e.className||'').toString().slice(0,40),
            txt: (e.textContent||'').replace(/\s+/g,' ').trim().slice(0,30),
        }}));
        return {{ total: kids.length, visible: vis.length, list: vis,
                  count: tab.querySelector('.sec-count-num').textContent }};
    }}""")
    lines = [f"[{TAB}] 点第 {IDX+1} 个标签 → 计数={res['count']}"]
    lines.append(f"  顶层 {res['total']} 个元素, 筛选后可见 {res['visible']} 个:")
    for v in res['list']:
        lines.append(f"    cls={v['cls']:<42} | {v['txt']}")
    # 再清空, 确认全部恢复
    pg.evaluate(f"() => document.querySelector('#tab-{TAB} .sec-pill').click()"); pg.wait_for_timeout(700)
    after = pg.evaluate(f"() => [...document.getElementById('tab-{TAB}').children].filter(e=>!e.classList.contains('sec-hidden')).length")
    lines.append(f"  清空筛选后可见 {after}/{res['total']}  (应等于总数)")
    pg.screenshot(path=f"probe/_ux/filter_{TAB}.png")
    b.close()
out="\n".join(lines); open("probe/_filter_view.txt","w",encoding="utf-8").write(out)
print(out.encode("gbk",errors="replace").decode("gbk"))
