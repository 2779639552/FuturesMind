# -*- coding: utf-8 -*-
"""专测 FLIP 让位动画: 点击使某标签变宽后, 同一行右侧标签应在过渡期间处于
"旧位置 → 新位置"的中间状态(computed transform 为单位矩阵以外的值)。"""
from playwright.sync_api import sync_playwright

rep = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 950})
    pg.goto("http://127.0.0.1:5000", wait_until="domcontentloaded")
    pg.wait_for_timeout(2800)
    pg.evaluate("() => App.switchTab('sentiment')")   # 情绪页标签多(15 个), 必然换行, 最能看出让位
    pg.wait_for_timeout(2500)

    # 量全部标签的几何: 行号(y) + 左偏移
    def geom():
        return pg.evaluate("""() => [...document.querySelectorAll('#tab-sentiment .sec-index-item')].map(e => {
            const r = e.getBoundingClientRect();
            return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width),
                     tf: getComputedStyle(e).transform };
        })""")

    g0 = geom()
    rows = {}
    for i, c in enumerate(g0):
        rows.setdefault(c['y'], []).append(i)
    rep.append(f"[初始] {len(g0)} 个标签, 分 {len(rows)} 行")
    for y, idxs in rows.items():
        rep.append(f"       行 y={y}: 索引 {idxs}  x={[g0[i]['x'] for i in idxs]}")

    # 选第 1 个: 它变宽会把**同一行其余标签**推走, 同时换行导致后续行整体下移
    target = 0
    pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-index-item')[0].click()")

    # 连续采样 5 帧, 看 transform 是否为"非单位矩阵"(说明处于补位过渡中)
    frames = []
    for k in range(5):
        pg.wait_for_timeout(35)
        frames.append(pg.evaluate("""() => [...document.querySelectorAll('#tab-sentiment .sec-index-item')]
            .map(e => getComputedStyle(e).transform)"""))
    rep.append(f"[点选后前 175ms] 各帧 transform:")
    for k, f in enumerate(frames):
        animated = [i for i, v in enumerate(f) if v not in ('none', 'matrix(1, 0, 0, 1, 0, 0)')]
        rep.append(f"       帧{k+1} ({(k+1)*35}ms): 正在补位的标签索引={animated}")

    pg.wait_for_timeout(700)
    g1 = geom()
    rep.append(f"[稳定后] 选中项宽度 {g0[target]['w']} → {g1[target]['w']}  (+{g1[target]['w']-g0[target]['w']}px)")
    rep.append(f"       同位置标签 transform 应已归零: {[c['tf'] for c in g1[:4]]}")
    rows2 = {}
    for i, c in enumerate(g1):
        rows2.setdefault(c['y'], []).append(i)
    rep.append(f"       重排后分 {len(rows2)} 行 (选中项变宽可能挤掉同行的标签→换行)")

    # 截图: 选中态 + 未选中态对比
    pg.screenshot(path="probe/_ux/chip_flip_on.png")
    pg.evaluate("() => document.querySelector('#tab-sentiment .sec-pill').click()")
    pg.wait_for_timeout(700)
    pg.screenshot(path="probe/_ux/chip_flip_off.png")
    b.close()

out = "\n".join(rep)
open("probe/_chip_flip.txt", "w", encoding="utf-8").write(out)
print(out.encode("gbk", errors="replace").decode("gbk"))
