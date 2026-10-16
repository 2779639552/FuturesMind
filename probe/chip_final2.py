# -*- coding: utf-8 -*-
"""多选筛选标签 · 最终验收(交互分工: 左键展开 / Ctrl+左键筛选)。

覆盖: ①左键仍能展开(不改变原有习惯) ②Ctrl+左键多选筛选 ③计数跳动 ④筛选只留选中
⑤清空恢复 ⑥FLIP 让位位移合理 ⑦全页签无残留 ⑧控制台无错。
"""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
TABS = ["run", "research", "sentiment", "dashboard", "quant", "warrant", "foreign", "warehouse"]
rep = []


def state(pg, tab):
    return pg.evaluate("""(tab) => {
        const t = document.getElementById('tab-' + tab);
        const idx = t.querySelector('.sec-index');
        const chips = [...t.querySelectorAll('.sec-index-item')];
        const kids = [...t.children].filter(e => !e.classList.contains('sec-hidden'));
        return {
            chips: chips.length,
            on: chips.filter(c => c.classList.contains('is-on')).length,
            count: idx.querySelector('.sec-count-num').textContent.trim(),
            vis: kids.length, total: t.children.length,
            flip: App._lastFlip || null,
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
    pg.evaluate("() => App.switchTab('sentiment')")
    pg.wait_for_timeout(2500)

    # ① 左键 = 展开(不筛选)
    before = state(pg, "sentiment")
    pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-index-item')[5].click()")
    pg.wait_for_timeout(600)
    after_left = state(pg, "sentiment")
    rep.append(f"[左键] 计数 {before['count']} → {after_left['count']} (应不变) · "
               f"选中数 {before['on']} → {after_left['on']} (应仍 0)  "
               f"{'OK' if after_left['count'] == before['count'] and after_left['on'] == 0 else 'FAIL'}")
    opened = pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-index-item')[5].classList.contains('is-open')")
    rep.append(f"       第6个标签 is-open={opened} (左键后应为 True, 说明左键确实在展开)")

    # ② Ctrl + 左键 = 筛选
    pg.evaluate("""() => {
        const el = document.querySelectorAll('#tab-sentiment .sec-index-item')[5];
        el.dispatchEvent(new MouseEvent('click', {bubbles:true, ctrlKey:true}));
    }""")
    pg.wait_for_timeout(650)
    s1 = state(pg, "sentiment")
    rep.append(f"[Ctrl+左键] 计数={s1['count']} 选中={s1['on']} 可见 {s1['vis']}/{s1['total']}  "
               f"{'OK' if s1['count'] == '1/15' and s1['on'] == 1 else 'FAIL'}")
    rep.append(f"       FLIP 位移 dx={s1['flip']['dx'][:6] if s1['flip'] else None} "
               f"dy={s1['flip']['dy'][:6] if s1['flip'] else None} (dy 应全 0)")

    # ③ 多选
    pg.evaluate("""() => {
        const el = document.querySelectorAll('#tab-sentiment .sec-index-item')[8];
        el.dispatchEvent(new MouseEvent('click', {bubbles:true, ctrlKey:true}));
    }""")
    pg.wait_for_timeout(650)
    s2 = state(pg, "sentiment")
    rep.append(f"[多选] 计数={s2['count']} 选中={s2['on']} 可见 {s2['vis']}/{s2['total']}  "
               f"{'OK' if s2['count'] == '2/15' and s2['on'] == 2 else 'FAIL'}")
    # 计数跳动动画
    anim = pg.evaluate("() => getComputedStyle(document.querySelector('#tab-sentiment .sec-count-num')).animationName")
    rep.append(f"       计数元素 animationName={anim} (应为 secCountPop)")

    # ④ 清空
    pg.evaluate("() => document.querySelector('#tab-sentiment .sec-pill').click()")
    pg.wait_for_timeout(700)
    s3 = state(pg, "sentiment")
    rep.append(f"[清空] 计数={s3['count']} 可见 {s3['vis']}/{s3['total']}  "
               f"{'OK' if s3['vis'] == s3['total'] and s3['count'] == '15' else 'FAIL'}")

    # ⑤ 全页签
    rep.append("")
    for tab in TABS:
        pg.evaluate(f"() => App.switchTab('{tab}')")
        pg.wait_for_timeout(2000)
        st = state(pg, tab)
        if not st["chips"]:
            continue
        n = st["chips"]
        picks = [n - 1] if n < 3 else [n - 2, n - 1]
        for i in picks:
            pg.evaluate(f"""() => document.querySelectorAll('#tab-{tab} .sec-index-item')[{i}]
                .dispatchEvent(new MouseEvent('click', {{bubbles:true, ctrlKey:true}}))""")
            pg.wait_for_timeout(200)
        pg.wait_for_timeout(600)
        on = state(pg, tab)
        pg.evaluate(f"() => document.querySelector('#tab-{tab} .sec-pill').click()")
        pg.wait_for_timeout(650)
        cl = state(pg, tab)
        ok = on["vis"] <= len(picks) + 2 and cl["vis"] == cl["total"]
        rep.append(f"[{tab}] {'OK ' if ok else 'CHK'} 标签{n} 选{len(picks)} → 计数={on['count']} "
                   f"可见 {on['vis']}/{on['total']} → 清空 {cl['vis']}/{cl['total']}")

    rep.append("")
    rep.append(f"[控制台错误] {errs[:5] if errs else '无'}")

    # 成品截图
    pg.evaluate("() => App.switchTab('sentiment')")
    pg.wait_for_timeout(1600)
    for i in (5, 8):
        pg.evaluate(f"""() => document.querySelectorAll('#tab-sentiment .sec-index-item')[{i}]
            .dispatchEvent(new MouseEvent('click', {{bubbles:true, ctrlKey:true}}))""")
        pg.wait_for_timeout(350)
    pg.wait_for_timeout(500)
    pg.screenshot(path="probe/_ux/chip_done.png")
    b.close()

out = "\n".join(rep)
open("probe/_chip_final2.txt", "w", encoding="utf-8").write(out)
print(out.encode("gbk", errors="replace").decode("gbk"))
