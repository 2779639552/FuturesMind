# -*- coding: utf-8 -*-
"""探针：确认章节折叠（sec-block）行为是否被 Apple 化改动破坏。"""
import pathlib, json
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(r"D:\AgentSense\AgentSense\probe")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)

    pg.evaluate("document.querySelector('nav button[data-tab=\"research\"]').click()")
    pg.wait_for_timeout(4000)

    m = pg.evaluate("""() => {
      const blocks = [...document.querySelectorAll('.sec-block')];
      const idx = document.querySelector('.sec-index');
      return {
        docH: document.documentElement.scrollHeight,
        secBlocks: blocks.length,
        collapsed: blocks.filter(b => b.classList.contains('is-collapsed')).length,
        secOpen: blocks.filter(b => b.classList.contains('sec-open')).length,
        secInit: blocks.filter(b => b.classList.contains('sec-init')).length,
        hasIndex: !!idx,
        indexItems: document.querySelectorAll('.sec-index-item').length,
        cards: document.querySelectorAll('.card').length,
        visibleCards: [...document.querySelectorAll('.card')].filter(c => c.offsetParent).length,
        styleBlocks: document.styleSheets.length,
        cssRules: (() => { let n=0; for (const s of document.styleSheets) { try { n += s.cssRules.length; } catch(e){} } return n; })(),
      };
    }""")
    print(json.dumps(m, ensure_ascii=False, indent=2))
    print("\n控制台错误:", len(errs))
    for e in errs[:6]:
        print("  ", e[:150])

    pg.screenshot(path=str(OUT / "_probe_research_full.png"), full_page=True)
    pg.screenshot(path=str(OUT / "_probe_research_fold.png"))

    # 也测运行分析页
    pg.evaluate("document.querySelector('nav button[data-tab=\"run\"]').click()")
    pg.wait_for_timeout(2500)
    m2 = pg.evaluate("""() => {
      const blocks = [...document.querySelectorAll('.sec-block')];
      return { docH: document.documentElement.scrollHeight,
               secBlocks: blocks.length,
               collapsed: blocks.filter(b => b.classList.contains('is-collapsed')).length,
               visibleCards: [...document.querySelectorAll('.card')].filter(c=>c.offsetParent).length };
    }""")
    print("\n=== run 页 ===")
    print(json.dumps(m2, ensure_ascii=False))
    print("总控制台错误:", len(errs))
    b.close()
