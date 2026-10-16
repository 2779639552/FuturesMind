# -*- coding: utf-8 -*-
"""补充取证：quant / fund 页双主题 + 移动端，并做全站控制台错误巡检。"""
import pathlib, json
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(r"D:\AgentSense\AgentSense\probe\_ux2")
OUT.mkdir(exist_ok=True)
TABS = ["run", "research", "sentiment", "quant", "fund", "warrant", "foreign", "warehouse", "gtja"]

report = []
with sync_playwright() as p:
    b = p.chromium.launch()

    # ── 1) 各页签双主题：截图 + 体检 ──
    for theme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.evaluate("localStorage.setItem('agentsense-theme', '%s')" % theme)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(2200)

        for t in TABS:
            pg.evaluate(f"document.querySelector('nav button[data-tab=\"{t}\"]').click()")
            pg.wait_for_timeout(2600)
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
              return {
                docH: document.documentElement.scrollHeight,
                overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
                smallTargets: small.length, totalTargets: cands.length,
                minFont: Math.min(...Object.keys(sizes).map(parseFloat)),
                fontKinds: Object.keys(sizes).length,
                sizes,
              };
            }""")
            pg.screenshot(path=str(OUT / f"{theme}_{t}.png"))
            report.append({"theme": theme, "tab": t, **m})
        report.append({"theme": theme, "tab": "__errors__", "errs": errs[:8], "n": len(errs)})
        pg.close()

    # ── 2) 移动端 390px ──
    for tab in ("run", "research", "sentiment", "warrant"):
        pg = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                        is_mobile=True, has_touch=True)
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.wait_for_timeout(2200)
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{tab}\"]').click()")
        pg.wait_for_timeout(2600)
        m = pg.evaluate("""() => ({
          overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
          scrollW: document.documentElement.scrollWidth, winW: window.innerWidth,
          navFixed: getComputedStyle(document.querySelector('nav#nav')).position,
          navBottom: getComputedStyle(document.querySelector('nav#nav')).bottom,
        })""")
        pg.screenshot(path=str(OUT / f"mobile_{tab}.png"), full_page=True)
        report.append({"mobile": tab, **m})
        pg.close()
    b.close()

TXT = OUT / "audit.txt"
lines = []
for r in report:
    if "__errors__" in r:
        lines.append(f"[{r['theme']}] 控制台错误 {r['n']} 条" +
                     (": " + " | ".join(e[:90] for e in r["errs"]) if r["errs"] else ""))
        continue
    if "mobile" in r:
        lines.append(f"[mobile {r['mobile']}] 溢出={r['overflowX']} scrollW={r['scrollW']} winW={r['winW']} nav={r['navFixed']}/{r['navBottom']}")
        continue
    lines.append(f"[{r['theme']:5} {r['tab']:10}] docH={r['docH']:6} 溢出={str(r['overflowX']):5} "
                 f"小目标={r['smallTargets']}/{r['totalTargets']:3} 最小字={r['minFont']:5} 字号档={r['fontKinds']:2}")
TXT.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print("\n截图 ->", OUT)
