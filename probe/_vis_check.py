# -*- coding: utf-8 -*-
"""筛选后"选中模块真的可见且有高度"专项验证(含嵌套模块)。"""
from playwright.sync_api import sync_playwright
rep=[]
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1600,"height":950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded"); pg.wait_for_timeout(2800)
    for tab, picks in (("sentiment",[5,8]), ("quant",[2,3]), ("run",[0,2])):
        pg.evaluate(f"() => App.switchTab('{tab}')"); pg.wait_for_timeout(2300)
        # 确保所有选中项展开
        for i in picks:
            pg.evaluate(f"""() => {{
                const el = document.querySelectorAll('#tab-{tab} .sec-index-item')[{i}];
                if (!el.classList.contains('is-open')) el.click();
                el.dispatchEvent(new MouseEvent('click', {{bubbles:true, ctrlKey:true}}));
            }}""")
            pg.wait_for_timeout(300)
        pg.wait_for_timeout(900)
        info = pg.evaluate(f"""() => {{
            const t = document.getElementById('tab-{tab}');
            const rows = App._secRows['{tab}'] || [];
            return rows.map((r,i) => ({{
                label: (r.label||'').slice(0,18),
                on: r.btn && r.btn.classList.contains('is-on'),
                hidden: (r.blocks||[]).some(b => b.classList.contains('sec-hidden')),
                h: Math.max(0, ...(r.blocks||[]).map(b => b.getBoundingClientRect().height)),
            }}));
        }}""")
        rep.append(f"[{tab}] 点选 {picks}")
        for x in info:
            mark = "★显示" if x['on'] else "  隐藏"
            rep.append(f"    {mark} on={str(x['on']):<5} hidden={str(x['hidden']):<5} h={x['h']:.0f}px  {x['label']}")
        pg.screenshot(path=f"probe/_ux/vis_{tab}.png")
        pg.evaluate(f"() => document.querySelector('#tab-{tab} .sec-pill').click()"); pg.wait_for_timeout(700)
    b.close()
out="\n".join(rep); open("probe/_vis_check.txt","w",encoding="utf-8").write(out)
print(out.encode("gbk",errors="replace").decode("gbk"))
