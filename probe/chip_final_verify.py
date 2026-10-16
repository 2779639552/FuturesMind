# -*- coding: utf-8 -*-
"""多选筛选标签 · 全页签综合验收。

对 8 个页签逐一: ①筛选前可见项数 ②点 2 个标签后可见项数(应只剩索引条+那些模块+段头)
③计数文案 ④清空后完全恢复 ⑤控制台无错误。
另抓 run 页"单个选中 / 多个选中"两张成品截图。
"""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
TABS = ["run", "research", "sentiment", "dashboard", "quant", "warrant", "foreign", "warehouse"]
rep = []


def probe(pg, tab):
    return pg.evaluate("""(tab) => {
        const t = document.getElementById('tab-' + tab);
        const kids = [...t.children];
        const idx = t.querySelector('.sec-index');
        const chips = [...t.querySelectorAll('.sec-index-item')];
        const vis = kids.filter(e => !e.classList.contains('sec-hidden'));
        return {
            totalKids: kids.length,
            visibleKids: vis.length,
            chips: chips.length,
            onCount: chips.filter(c => c.classList.contains('is-on')).length,
            count: idx ? idx.querySelector('.sec-count-num').textContent.trim() : null,
            visible: vis.map(e => (e.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 24)),
        };
    }""", tab)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 950})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(2800)

    for tab in TABS:
        pg.evaluate(f"() => App.switchTab('{tab}')")
        pg.wait_for_timeout(2200)
        base = probe(pg, tab)
        if not base["chips"]:
            rep.append(f"[{tab}] 无索引标签, 跳过 (顶层 {base['totalKids']})")
            continue
        # 点最后两个标签(避开默认展开项, 更能看出筛选效果)
        # 【2026-09-29 交互定稿】左键**直接勾选**, 不再需要修饰键。
        #   历史教训: 曾用不带 ctrlKey 的 .click() 测"必须 Ctrl+左键"的旧交互,
        #   只触发折叠不触发筛选, 统计出"选 2 个仍可见 10/10"的假象 ——
        #   护栏的按键方式必须与当前交互语义一致, 否则测的是不存在的行为。
        n = base["chips"]
        picks = [n - 1] if n < 3 else [n - 2, n - 1]
        for i in picks:
            pg.evaluate(
                "i => document.querySelectorAll('#tab-%s .sec-index-item')[i]"
                ".dispatchEvent(new MouseEvent('click',{bubbles:true}))" % tab, i)
            pg.wait_for_timeout(220)
        pg.wait_for_timeout(600)
        on = probe(pg, tab)
        pg.evaluate(f"() => document.querySelector('#tab-{tab} .sec-pill').click()")
        pg.wait_for_timeout(650)
        clr = probe(pg, tab)
        ok = (on["visibleKids"] <= len(picks) + 2 and on["count"] == f"{len(picks)}/{n}"
              and clr["visibleKids"] == base["totalKids"])
        rep.append(
            f"[{tab}] {'OK ' if ok else 'CHK'} 标签{n} 选{len(picks)}个 → "
            f"计数={on['count']} 可见 {base['visibleKids']}/{base['totalKids']} → "
            f"{on['visibleKids']}  清空后 {clr['visibleKids']}/{base['totalKids']}")
        rep.append(f"        可见内容: {on['visible']}")

    rep.append(f"[控制台错误] {errs[:5] if errs else '无'}")

    # 抓成品截图
    pg.evaluate("() => App.switchTab('run')")
    pg.wait_for_timeout(1600)
    pg.evaluate("() => document.querySelectorAll('#tab-run .sec-index-item')[0].click()")
    pg.wait_for_timeout(700)
    pg.screenshot(path="probe/_ux/chip_final_one.png")
    pg.evaluate("() => document.querySelectorAll('#tab-run .sec-index-item')[3].click()")
    pg.wait_for_timeout(700)
    pg.screenshot(path="probe/_ux/chip_final_multi.png")
    b.close()

out = "\n".join(rep)
open("probe/_chip_final.txt", "w", encoding="utf-8").write(out)
print(out.encode("gbk", errors="replace").decode("gbk"))
