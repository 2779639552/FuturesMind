# -*- coding: utf-8 -*-
"""主题检查: 仓单页「品种范围」控制条在深浅两套主题下的可读性。

【为什么单独跑】本页新增了一个 select 控制条, 且交易所汇总卡的品种数改成了
  动态重算 —— 浅色主题下若沿用深色硬编码色会「白底白字」, 必须两套都截图确认。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:5000"
SHOTS = ROOT / "probe" / "_ui"

with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        pg = b.new_page(viewport={"width": 1560, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.route("**/api/auth/status", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"ok":true,"authenticated":true}'))
        pg.goto(BASE, wait_until="domcontentloaded")
        pg.wait_for_timeout(2000)
        if theme == "light":
            pg.evaluate("""() => { const t=document.querySelector('.theme-toggle'); if(t) t.click(); }""")
            pg.wait_for_timeout(900)
        pg.click('nav button[data-tab="warrant"]')
        pg.wait_for_timeout(6000)
        info = pg.evaluate("""() => {
          const s = document.getElementById('wt-scope');
          const cs = s ? getComputedStyle(s) : null;
          const badge = document.getElementById('wt-ov-badge');
          const card = document.querySelector('#wt-excards .wt-excard');
          const cnt = card ? (card.querySelectorAll('.s')[0]||{}).textContent : '';
          return {
            theme: document.documentElement.getAttribute('data-theme') || document.body.className,
            scopeVal: s ? s.value : null,
            scopeColor: cs ? cs.color : null,
            scopeBg: cs ? cs.backgroundColor : null,
            badge: badge ? badge.textContent.trim() : null,
            cardCount: cnt ? cnt.trim() : null,
            cardColor: card ? getComputedStyle(card).color : null
          };
        }""")
        print(theme, "->", info, "errs=", errs[:2])
        pg.screenshot(path=str(SHOTS / f"wt_pool_ctrl_{theme}.png"))
        pg.close()
    b.close()
print("shots saved to", SHOTS)
