# -*- coding: utf-8 -*-
"""验证【左键直接勾选】定稿交互 (#14)。

约定:
  · 左键点标签      = 勾选 / 取消勾选(可多选), 且选中即展开该模块
  · 左键点「共 N 项」 = 清空筛选, 恢复全部模块
  · 勾选 / 取消勾选都走**真实鼠标事件**(不派发合成事件, 避免"只有合成事件能触发")
运行: python probe/leftclick_verify.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

TABS = ["run", "research", "sentiment", "dashboard", "quant", "warrant", "foreign", "warehouse"]


def show(m):
    print(m)


def snapshot(pg, tab):
    return pg.evaluate("""(tab) => {
      const t = document.getElementById('tab-' + tab);
      const chips = [...t.querySelectorAll('.sec-index-item')];
      const vis = [...t.children].filter(e => !e.classList.contains('sec-hidden'));
      const rows = (App._secRows[tab] || []);
      return {
        n: chips.length,
        on: chips.map(c => c.classList.contains('is-on')),
        cnt: t.querySelector('.sec-count-num').textContent,
        meta: t.querySelector('.sec-index-meta').textContent,
        visN: vis.length,
        // 每个选中模块是否真的撑开(内容可见)
        pickedOpen: rows.filter((r,i) => (r.btn||{}).classList && r.btn.classList.contains('is-on'))
                       .map(r => { const b=(r.blocks||[])[0];
                                   return b ? {c:b.classList.contains('is-collapsed'), h:b.offsetHeight} : null; }),
      };
    }""", tab)


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
    pg.goto("http://127.0.0.1:5000/", wait_until="networkidle")
    pg.wait_for_timeout(2600)

    allok = True
    for tab in TABS:
        pg.evaluate(f"App.switchTab('{tab}')")
        pg.wait_for_timeout(2400)
        base = snapshot(pg, tab)
        if base["n"] < 2:
            show(f"[{tab}] 标签数 {base['n']} 过少, 跳过")
            continue

        chips = pg.locator(f"#tab-{tab} .sec-index-item")

        # 【为什么按内容高度挑样本】不能盲取"最后一个标签" —— 很多页签末尾是
        #   空内容操作栏(run 的「讨论进化」30px、需先跑分析才有内容)。拿它验证
        #   "选中即展开"会得到 0 高度 ⇒ 报假失败。必须先找出**有实质内容**的模块。
        sizes = pg.evaluate("""(tab)=>{
          const rows = App._secRows[tab] || [];
          return rows.map((r,i)=>{
            const b=(r.blocks||[])[0];
            return {i, h: b ? b.scrollHeight : 0};
          });
        }""", tab)
        rich = sorted(sizes, key=lambda s: -s["h"])[0]["i"]
        rich_h = sizes[rich]["h"]

        # ① 普通左键点「内容最丰富」的标签 —— 应直接勾选且该模块被撑开
        chips.nth(rich).click()
        pg.wait_for_timeout(750)
        s1 = snapshot(pg, tab)
        ok_sel = s1["on"][rich] and s1["cnt"] == f"1/{base['n']}"
        blk = pg.evaluate("""([tab,i])=>{
          const b=((App._secRows[tab]||[])[i].blocks||[])[0];
          return b ? {c:b.classList.contains('is-collapsed'), h:b.offsetHeight,
                      sh:b.scrollHeight, disp:getComputedStyle(b).display} : null;
        }""", [tab, rich])
        ok_open = bool(blk) and (not blk["c"]) and blk["h"] > 30 and blk["disp"] != "none"

        # ② 再普通左键点另一个标签 —— 应变成 2 选中(多选)
        other = 0 if rich != 0 else (base["n"] - 1)
        chips.nth(other).click()
        pg.wait_for_timeout(800)
        s2 = snapshot(pg, tab)
        ok_multi = s2["on"][rich] and s2["on"][other] and s2["cnt"] == f"2/{base['n']}"
        ok_vis = s2["visN"] < base["visN"]

        # ③ 再点一次已选标签 —— 应取消勾选
        chips.nth(other).click()
        pg.wait_for_timeout(700)
        s3 = snapshot(pg, tab)
        ok_unsel = (not s3["on"][other]) and s3["on"][rich] and s3["cnt"] == f"1/{base['n']}"

        # ④ 点「共 N 项」—— 应清空并恢复
        pg.locator(f"#tab-{tab} .sec-pill").click()
        pg.wait_for_timeout(800)
        s4 = snapshot(pg, tab)
        ok_clear = (not any(s4["on"])) and s4["cnt"] == str(base["n"]) \
            and s4["visN"] == base["visN"]
        resid = pg.evaluate("""(tab)=>{
          const t=document.getElementById('tab-'+tab);
          return {hid:t.querySelectorAll('.sec-hidden').length, filt:t.querySelectorAll('.sec-filtered').length};
        }""", tab)
        ok_resid = resid["hid"] == 0 and resid["filt"] == 0

        good = ok_sel and ok_open and ok_multi and ok_vis and ok_unsel and ok_clear and ok_resid
        allok = allok and good
        show(f"[{tab}] {'OK ' if good else 'CHK'} 标签{base['n']} 选中#{rich}(内容{rich_h}px) "
             f"左键勾选={'✓' if ok_sel else '✗'} 选中即展开={'✓' if ok_open else '✗'}(h={blk['h'] if blk else '?'}) "
             f"多选={'✓' if ok_multi else '✗'} 可见{'→'.join([str(base['visN']),str(s2['visN'])])} "
             f"取消={'✓' if ok_unsel else '✗'} 看全部={'✓' if ok_clear else '✗'} 残留={resid}")

    # 展示 warrant 的提示文案演进
    pg.evaluate("App.switchTab('warrant')")
    pg.wait_for_timeout(2400)
    pg.evaluate("App._resetSecFilter('warrant')")
    pg.wait_for_timeout(400)
    m0 = snapshot(pg, "warrant")["meta"]
    pg.locator("#tab-warrant .sec-index-item").nth(2).click()
    pg.wait_for_timeout(700)
    m1 = snapshot(pg, "warrant")["meta"]
    show(f"\n提示文案·未筛选: {m0}")
    show(f"提示文案·筛选态: {m1}")
    has_ctrl = "Ctrl" in m0 or "⌘" in m0
    show(f"文案中是否残留修饰键说法: {'!!是' if has_ctrl else '★否(已移除门槛)'}")

    pg.screenshot(path="probe/_ux/L1_leftclick.png", clip={"x": 0, "y": 90, "width": 1440, "height": 130})
    show(f"\n控制台错误: {errs[:3] if errs else '无'}")
    show("\n" + ("═══ 全部通过 ═══" if allok and not errs and not has_ctrl else "═══ 有失败项 ═══"))
    b.close()
