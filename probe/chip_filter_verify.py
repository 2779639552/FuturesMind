# -*- coding: utf-8 -*-
"""多选筛选标签验证: ①点击后高亮/撑开 ②相邻标签真有 FLIP 位移过渡 ③计数跳动
④模块按选中筛选 ⑤再次点击取消恢复 ⑥点序号只折叠不筛选。"""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
rep = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 950})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(2800)
    pg.evaluate("() => App.switchTab('run')")
    pg.wait_for_timeout(1800)

    def chips():
        return pg.evaluate("""() => [...document.querySelectorAll('#tab-run .sec-index-item')].map(e => ({
            label: e.querySelector('span:last-child').textContent.trim(),
            on: e.classList.contains('is-on'),
            w: Math.round(e.getBoundingClientRect().width),
            ckMax: getComputedStyle(e.querySelector('.sec-ck')).maxWidth,
            tf: e.style.transform || '',
            hasTfTransition: getComputedStyle(e).transitionProperty.includes('transform') || getComputedStyle(e).transition.includes('transform'),
        }))""")

    def count():
        return pg.evaluate("() => document.querySelector('#tab-run .sec-count-num').textContent.trim()")

    def visible_blocks():
        return pg.evaluate("""() => {
            const rows = App._secRows['run'] || [];
            return rows.length;
        }""")

    before = chips()
    rep.append(f"[初始] 计数={count()}  标签数={len(before)}")
    rep.append(f"       选中态={[c['on'] for c in before]}")
    rep.append(f"       宽度={[c['w'] for c in before]}")
    rep.append(f"       check 塌陷宽度(maxWidth)={before[0]['ckMax']}")
    rep.append(f"       标签带 transform 过渡={before[0]['hasTfTransition']}")

    # ① 选中第 2 个标签
    pg.evaluate("() => document.querySelectorAll('#tab-run .sec-index-item')[1].click()")
    pg.wait_for_timeout(70)   # 动画进行中, 抓"正在让位"的中间态
    mid = pg.evaluate("""() => [...document.querySelectorAll('#tab-run .sec-index-item')].map(e => e.style.transform || 'none')""")
    rep.append(f"[点选后 70ms] 各标签 transform(应为非 none, 正在让位)={mid}")
    pg.wait_for_timeout(500)
    after = chips()
    rep.append(f"[点选后 600ms] 计数={count()}  选中态={[c['on'] for c in after]}")
    rep.append(f"       宽度={[c['w'] for c in after]}  (选中项应比初始宽)")
    rep.append(f"       选中项 check 宽度={after[1]['ckMax']} (应为 1.05em 非 0)")
    vis = pg.evaluate("""() => {
        const rows = App._secRows['run'] || [];
        return rows.map(r => (r.blocks||[]).some(b => !b.classList.contains('sec-hidden')));
    }""")
    rep.append(f"       模块可见性={vis}  (应只有第2个为 true)")
    pg.screenshot(path="probe/_ux/chip_one_selected.png")

    # ② 再选第 4 个 → 多选
    pg.evaluate("() => document.querySelectorAll('#tab-run .sec-index-item')[3].click()")
    pg.wait_for_timeout(600)
    vis2 = pg.evaluate("""() => {
        const rows = App._secRows['run'] || [];
        return rows.map(r => (r.blocks||[]).some(b => !b.classList.contains('sec-hidden')));
    }""")
    rep.append(f"[多选] 计数={count()}  可见性={vis2}  (应第2、4为 true)")
    pg.screenshot(path="probe/_ux/chip_multi_selected.png")

    # ③ 计数跳动动画是否真的被触发
    popped = pg.evaluate("""() => {
        const el = document.querySelector('#tab-run .sec-count-num');
        return {cls: el.className, anim: getComputedStyle(el).animationName};
    }""")
    rep.append(f"[跳动] 计数元素 class/animation={popped}")

    # ④ 点序号 → 只折叠, 不改筛选
    cnt_before = count()
    pg.evaluate("() => document.querySelectorAll('#tab-run .sec-index-item .sec-index-num')[1].click()")
    pg.wait_for_timeout(500)
    rep.append(f"[点序号] 计数 {cnt_before} → {count()} (应不变)  该模块选中态={chips()[1]['on']} (应仍为 true)")

    # ⑤ 点筛选按钮 → 清空
    pg.evaluate("() => document.querySelector('#tab-run .sec-pill').click()")
    pg.wait_for_timeout(600)
    vis3 = pg.evaluate("""() => {
        const rows = App._secRows['run'] || [];
        return rows.map(r => (r.blocks||[]).some(b => !b.classList.contains('sec-hidden')));
    }""")
    rep.append(f"[清空] 计数={count()}  选中态={[c['on'] for c in chips()]}  可见性={vis3} (应全 true)")
    pg.screenshot(path="probe/_ux/chip_cleared.png")

    rep.append(f"[控制台错误] {errs[:5] if errs else '无'}")

    # ⑥ 换页签也生效
    pg.evaluate("() => App.switchTab('sentiment')")
    pg.wait_for_timeout(2200)
    n = pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-index-item').length")
    pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-index-item')[2].click()")
    pg.wait_for_timeout(600)
    cnt = pg.evaluate("() => document.querySelector('#tab-sentiment .sec-count-num').textContent.trim()")
    rep.append(f"[情绪页] 标签数={n}  选中后计数={cnt}")
    pg.screenshot(path="probe/_ux/chip_sentiment.png")
    b.close()

out = "\n".join(rep)
open("probe/_chip_report.txt", "w", encoding="utf-8").write(out)
print(out.encode("gbk", errors="replace").decode("gbk"))
