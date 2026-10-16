# -*- coding: utf-8 -*-
"""纯度量巡检（不截图，快速）：全页签双主题 + 移动端。"""
import pathlib, json
from playwright.sync_api import sync_playwright

TABS = ["run", "research", "sentiment", "quant", "fund", "warrant", "foreign", "warehouse", "gtja"]
rows, errrows = [], []

with sync_playwright() as p:
    b = p.chromium.launch()

    for theme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.evaluate("localStorage.setItem('agentsense-theme', '%s')" % theme)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(2000)

        for t in TABS:
            pg.evaluate(f"document.querySelector('nav button[data-tab=\"{t}\"]').click()")
            pg.wait_for_timeout(2400)
            m = pg.evaluate("""() => {
              const vis = el => el.offsetParent !== null;
              const cands = [...document.querySelectorAll('button, a, select, input, .btn, .sec-index-item')].filter(vis);
              const small = cands.filter(e => { const r = e.getBoundingClientRect();
                                               return r.width>0 && r.height>0 && (r.height < 32 || r.width < 24); });
              const sizes = {};
              document.querySelectorAll('*').forEach(el => {
                if (!vis(el) || el.children.length) return;
                const txt = (el.textContent||'').trim(); if (!txt) return;
                const fs = getComputedStyle(el).fontSize; sizes[fs] = (sizes[fs]||0)+1;
              });
              const px = Object.keys(sizes).map(parseFloat);
              return { docH: document.documentElement.scrollHeight,
                       overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
                       small: small.length, total: cands.length,
                       minFont: Math.min(...px), fontKinds: px.length };
            }""")
            rows.append((theme, t, m))
        errrows.append((theme, errs))
        pg.close()

    mobs = []
    for tab in ("run", "research", "sentiment", "warrant"):
        pg = b.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.wait_for_timeout(2000)
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{tab}\"]').click()")
        pg.wait_for_timeout(2400)
        m = pg.evaluate("""() => {
          const nav = document.querySelector('nav#nav'); const cs = getComputedStyle(nav);
          return { overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
                   scrollW: document.documentElement.scrollWidth, winW: window.innerWidth,
                   navPosition: cs.position, navBottom: cs.bottom };
        }""")
        mobs.append((tab, m))
        pg.close()
    b.close()

out = []
out.append("═══ 桌面 1440px · 双主题 9 页签 ═══")
out.append(f"{'主题':<6}{'页签':<11}{'页高':>7}{'横向溢出':>9}{'小目标/总':>11}{'最小字':>8}{'字号档':>7}")
for theme, t, m in rows:
    out.append(f"{theme:<6}{t:<11}{m['docH']:>7}{str(m['overflowX']):>9}"
               f"{str(m['small'])+'/'+str(m['total']):>11}{m['minFont']:>8}{m['fontKinds']:>7}")
out.append("")
out.append("═══ 控制台错误 ═══")
for theme, errs in errrows:
    out.append(f"  [{theme}] {len(errs)} 条" + ((": " + " | ".join(e[:80] for e in errs[:5])) if errs else ""))
out.append("")
out.append("═══ 移动端 390px ═══")
for tab, m in mobs:
    out.append(f"  {tab:<11} 溢出={str(m['overflowX']):<6} scrollW={m['scrollW']:<6} winW={m['winW']:<5} "
               f"nav={m['navPosition']} bottom={m['navBottom']}")

txt = "\n".join(out)
pathlib.Path(r"D:\AgentSense\AgentSense\probe\_ux2\audit.txt").write_text(txt, encoding="utf-8")
print(txt)
