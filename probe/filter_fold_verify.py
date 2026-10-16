# -*- coding: utf-8 -*-
"""验证 #12 收尾三件事:
  ① 筛选态下选中的模块真的撑开(内容可见, 非 0 高)
  ② 筛选态下左键仍能收起 / 展开该模块(折叠功能未被 !important 废掉)
  ③ 清空筛选后精确还原::原本收起的收回去, 原本展开的保持展开
运行: python probe/filter_fold_verify.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"


def show(m):
    print(m)


def snap(pg, tab):
    return pg.evaluate("""(tab) => {
      const t = document.getElementById('tab-' + tab);
      const rows = App._secRows[tab] || [];
      return rows.map(r => {
        const b = (r.blocks||[])[0];
        return {
          label: r.label.slice(0, 16),
          on: r.btn ? r.btn.classList.contains('is-on') : false,
          collapsed: b ? b.classList.contains('is-collapsed') : null,
          filtered: b ? b.classList.contains('sec-filtered') : null,
          h: b ? b.offsetHeight : 0,
        };
      });
    }""", tab)


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 960})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(2500)
    pg.evaluate("App.switchTab('sentiment')")
    pg.wait_for_timeout(2600)

    before = snap(pg, "sentiment")
    show("── ① 筛选前各模块开合状态 ──")
    for i, r in enumerate(before):
        show(f"   [{i:2d}] {r['label']:18s} collapsed={r['collapsed']} h={r['h']}")

    # 选一个"原本收起"的模块(避开默认展开项)
    open_idx = next((i for i, r in enumerate(before) if not r["collapsed"]), 0)
    pick = next((i for i, r in enumerate(before) if r["collapsed"] and r["h"] == 0), None)
    show(f"\n   默认展开项 idx={open_idx}, 选中一个原本收起的 idx={pick}")

    pg.evaluate("""(i) => {
      const rows = App._secRows['sentiment']||[];
      rows[i].btn.dispatchEvent(new MouseEvent('click',{bubbles:true}));
    }""", pick)
    pg.wait_for_timeout(800)
    after = snap(pg, "sentiment")
    r = after[pick]
    show(f"\n── ② 筛选后 [{pick}] ──")
    show(f"   on={r['on']} filtered={r['filtered']} collapsed={r['collapsed']} h={r['h']}px")
    ok1 = r["on"] and r["filtered"] and not r["collapsed"] and r["h"] > 50
    show(f"   {'★撑开 OK' if ok1 else '!!未撑开'}")

    # ②-b 【2026-09-29 交互已变】左键不再是"折叠", 而是"取消勾选"。
    #   原用例"筛选态下左键收起"已失效: 它之所以还显示 OK, 是因为取消勾选后
    #   模块恢复了**原本的收起状态**, 并非"左键能折叠" —— 这是典型的**误导性护栏**,
    #   留着会让人误以为折叠入口还在。改成验证真实的取消勾选语义。
    before_on = after[pick]["on"]
    pg.evaluate("""(i) => {
      const rows = App._secRows['sentiment']||[];
      rows[i].btn.dispatchEvent(new MouseEvent('click',{bubbles:true}));
    }""", pick)
    pg.wait_for_timeout(700)
    unsel = snap(pg, "sentiment")[pick]
    show(f"\n── ②-b 左键点已选标签(= 取消勾选) ──")
    show(f"   on: {before_on} -> {unsel['on']}   filtered={unsel['filtered']}")
    ok2 = (not unsel["on"]) and (not unsel["filtered"])
    show(f"   {'★取消勾选 OK' if ok2 else '!!未取消勾选'}")

    # 再勾回来
    pg.evaluate("""(i) => {
      const rows = App._secRows['sentiment']||[];
      rows[i].btn.dispatchEvent(new MouseEvent('click',{bubbles:true}));
    }""", pick)
    pg.wait_for_timeout(700)
    reopened = snap(pg, "sentiment")[pick]
    show(f"   再勾回来: on={reopened['on']} collapsed={reopened['collapsed']} h={reopened['h']}px")

    # ③ 清空筛选 -> 应还原为 before (pick 原本收起 -> 收起)
    pg.evaluate("App._resetSecFilter('sentiment')")
    pg.wait_for_timeout(900)
    restored = snap(pg, "sentiment")
    show("\n── ③ 清空筛选后还原 ──")
    bad = []
    for i, (a, c) in enumerate(zip(before, restored)):
        same = (a["collapsed"] == c["collapsed"]) and (a["on"] == c["on"])
        if not same:
            bad.append(i)
            show(f"   !! [{i}] {a['label']:18s} 原 collapsed={a['collapsed']} → 现 {c['collapsed']} (on {a['on']}→{c['on']})")
    show(f"   逐项比对: {'★完全还原 OK' if not bad else f'{len(bad)} 项不一致'}")
    hid = pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-hidden').length")
    filt = pg.evaluate("() => document.querySelectorAll('#tab-sentiment .sec-filtered').length")
    show(f"   残留 sec-hidden={hid} sec-filtered={filt} (均应为 0)")

    show(f"\n控制台错误: {errs[:3] if errs else '无'}")
    allok = ok1 and ok2 and not bad and hid == 0 and filt == 0 and not errs
    show("\n" + ("═══ 全部通过 ═══" if allok else "═══ 有失败项 ═══"))
    b.close()
